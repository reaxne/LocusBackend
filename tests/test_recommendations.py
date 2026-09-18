"""All admission values below are fictional DEMO fixtures, not university facts."""

from datetime import date
import json
from pathlib import Path
import psycopg
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from database import Database
from main import create_app
from recommendation.config import DEFAULT_CONFIG
from recommendation.eligibility import check_eligibility, exam_fit
from recommendation.embeddings import InterestMatcher, KeywordEmbeddingProvider
from recommendation.models import (
    AdmissionRoute, Deadline, ExamRequirement, Program, RoadmapTask, Source, StudentProfile,
)
from recommendation.recommender import Recommender
from recommendation.repository import SQLiteProgramRepository
from recommendation.roadmap import get_next_action
from recommendation.scoring import academic_match, calculate_weights, financial_match


AS_OF = date(2029, 1, 1)
YEAR = 2030
SOURCE = Source(url="https://example.invalid/demo-only", verified_at=date(2025, 1, 1), academic_year=YEAR)


def student(**updates):
    return StudentProfile.model_validate({
        "grade": 11, "entryYear": YEAR, "interest": ["Robotics", "Programming"],
        "city": ["Astana"], "mustStay": False, "budget": 2500000,
        "funding": ["state_grant"], "academicStrengths": ["Mathematics", "Physics", "Computer Science"],
        "SAT": 1350, "IELTS": 6.5, "UNT": None,
        "extracurricularInterests": ["Robotics"], **updates,
    })


def route(route_id="sat", **minimums):
    return AdmissionRoute(
        id=route_id, requirements_complete=True, source=SOURCE,
        requirements={exam: ExamRequirement(minimum=value, source=SOURCE) for exam, value in minimums.items()},
    )


def program(**updates):
    return Program.model_validate({
        "id": "demo-robotics", "university_id": "demo-university",
        "university_name": "Fictional Demo University", "name": "Robotics and Programming",
        "city": "Astana", "admission_year": YEAR, "is_demo": True,
        "interests": ["robotics", "programming"], "important_courses": ["Robotics", "Programming"],
        "recommended_academic_strengths": ["Mathematics", "Computer Science"],
        "tuition_per_year": 4000000, "state_grant_available": True,
        "sources": {"tuition_per_year": SOURCE, "state_grant_available": SOURCE},
        "admission_routes": [route(SAT=1250, IELTS=6.0)], **updates,
    })


class MemoryRepository:
    def __init__(self, programs):
        self.programs = programs

    def get_programs_for_entry_year(self, year):
        return self.programs

    def get_program_by_id(self, program_id, year):
        return next((item for item in self.programs if item.id == program_id), None)


def recommend(profile=None, programs=None):
    engine = Recommender(MemoryRepository(programs if programs is not None else [program()]))
    return engine.recommend(profile or student(), as_of=AS_OF)


def test_hard_city_excludes_outside_and_unknown_cities():
    items = [program(), program(id="outside", city="Almaty"), program(id="unknown", city=None)]
    result = recommend(student(mustStay=True), items)
    assert [item.program_id for item in result.recommendations] == ["demo-robotics"]
    assert {item.program_id for item in result.excluded_programs} == {"outside", "unknown"}
    assert all(item.reasons for item in result.excluded_programs)


def test_soft_city_keeps_outside_with_lower_score():
    result = recommend(programs=[program(), program(id="outside", city="Almaty")]).recommendations
    assert len(result) == 2
    assert result[0].scores["city"] == 1
    assert result[0].match_score > result[1].match_score
    assert result[1].scores["city"] == DEFAULT_CONFIG.outside_city_score


def test_unt_route_passes():
    result = check_eligibility(student(UNT=115), program(admission_routes=[route("unt", UNT=100)]), AS_OF)
    assert result.eligible
    assert result.best_admission_route == "unt"


def test_sat_and_ielts_route_passes():
    result = check_eligibility(student(), program(), AS_OF)
    assert result.status == "eligible_now"
    assert all(item.status == "passed" for item in result.available_routes[0].requirements)


def test_sat_pass_ielts_fail_does_not_pass_route():
    result = check_eligibility(student(IELTS=5), program(), AS_OF)
    assert not result.eligible
    assert result.status == "ineligible"
    assert [item.exam for item in result.failed_requirements] == ["IELTS"]


def test_or_between_routes_and_best_valid_route_only():
    result = check_eligibility(student(IELTS=5, UNT=110), program(
        admission_routes=[route(SAT=1250, IELTS=6), route("unt", UNT=100)]), AS_OF)
    assert result.eligible
    assert result.best_admission_route == "unt"
    assert result.failed_requirements == []
    recommendation = recommend(student(IELTS=5, UNT=110), [program(
        admission_routes=[route(SAT=1250, IELTS=6), route("unt", UNT=100)])]).recommendations[0]
    assert recommendation.scores["exam"] == pytest.approx(exam_fit(110, 100))


def test_missing_exam_is_not_failed():
    missing = check_eligibility(student(IELTS=None), program(), AS_OF)
    failed = check_eligibility(student(IELTS=0), program(), AS_OF)
    assert [item.exam for item in missing.missing_requirements] == ["IELTS"]
    assert missing.failed_requirements == []
    assert failed.missing_requirements == []
    assert [item.exam for item in failed.failed_requirements] == ["IELTS"]


def test_grade_10_future_missing_exam_is_potential():
    result = check_eligibility(student(grade=10, UNT=None), program(
        admission_routes=[route("unt", UNT=100)]), AS_OF)
    assert result.status == "potentially_eligible"
    assert not result.eligible


def test_verified_deadline_limits_preparation_window():
    item = program(deadlines=[Deadline(task_type="apply_university", date=date(2029, 1, 10), source=SOURCE)])
    assert check_eligibility(student(IELTS=None), item, AS_OF).status == "ineligible"
    expired = program(deadlines=[Deadline(task_type="apply_university", date=date(2028, 12, 31), source=SOURCE)])
    assert check_eligibility(student(), expired, AS_OF).status == "ineligible"


def test_unverified_deadlines_are_not_used():
    item = program(deadlines=[Deadline(task_type="apply_university", date=date(2020, 1, 1))])
    assert check_eligibility(student(), item, AS_OF).eligible
    result = recommend(programs=[item]).recommendations[0]
    assert all(task.deadline is None for task in result.roadmap)


def test_failed_exam_can_only_be_planned_when_retakes_explicitly_allowed():
    sat = route(SAT=1250, IELTS=6).model_copy(update={"allows_retakes": True})
    item = program(admission_routes=[sat])
    result = recommend(student(IELTS=5), [item]).recommendations[0]
    assert result.eligibility.status == "potentially_eligible"
    assert any(task.title == "Retake IELTS" for task in result.roadmap)


def test_financial_within_budget():
    result = financial_match(student(budget=5000000), program(), AS_OF)
    assert result.score == 1
    assert result.within_budget
    assert result.difference == -1000000


def test_above_budget_with_grant_retained_and_not_guaranteed():
    result = recommend().recommendations[0]
    assert result.financial.funding_dependent
    assert result.financial.funding_options == ["state_grant"]
    assert result.financial.difference == 1500000
    assert any("not guaranteed" in item for item in result.warnings)


def test_financial_unknown_and_no_selected_funding():
    unknown = financial_match(student(), program(sources={}), AS_OF)
    assert unknown.score is None and unknown.tuition_per_year is None
    assert unknown.funding_options == []
    no_grant = financial_match(student(funding=["self_funded"]), program(), AS_OF)
    assert not no_grant.funding_dependent
    assert no_grant.score < DEFAULT_CONFIG.funding_dependent_score


def test_academic_strengths_aliases_and_whitespace():
    assert academic_match(student(academicStrengths=[" MATH ", "  computer   science "]), program()) == 1
    assert academic_match(student(academicStrengths=["History"]), program()) == 0
    assert academic_match(student(), program(recommended_academic_strengths=[])) is None


def test_interest_ordering_changes_with_direction():
    programs = [program(), program(id="economics", name="Economics Finance Markets",
                                  interests=["Economics"], important_courses=["Finance"])]
    robotics = recommend(student(), programs).recommendations
    economics = recommend(student(interest=["Economics", "Finance"], extracurricularInterests=[]), programs).recommendations
    assert robotics[0].program_id == "demo-robotics"
    assert economics[0].program_id == "economics"


@pytest.mark.parametrize("must_stay", [True, False])
def test_dynamic_weights_sum_to_one(must_stay):
    weights = calculate_weights(student(mustStay=must_stay), set(DEFAULT_CONFIG.weights))
    assert sum(weights.values()) == pytest.approx(1)
    assert ("city" not in weights) == must_stay
    assert weights["financial"] > DEFAULT_CONFIG.weights["financial"]


def test_unavailable_component_reweighted():
    result = recommend().recommendations[0]
    assert result.scores["extracurricular"] is None
    assert "extracurricular" not in result.weights
    assert sum(result.weights.values()) == pytest.approx(1)
    assert result.match_score == pytest.approx(sum(result.scores[key] * value for key, value in result.weights.items()))


def test_sorted_descending_default_top_four_and_stable_ties():
    items = [program(id=str(index), tuition_per_year=1000000 * (index + 1)) for index in range(8)]
    result = recommend(programs=items).recommendations
    assert len(result) == 4
    engine = Recommender(MemoryRepository(items))
    all_ranked = engine.recommend(student(), limit=50, as_of=AS_OF,
                                  program_ids=[item.id for item in items]).recommendations
    assert len(all_ranked) == 8
    assert result == all_ranked[:4]
    assert engine.recommend(student(), limit=50, as_of=AS_OF).recommendations == result
    assert [item.match_score for item in result] == sorted([item.match_score for item in result], reverse=True)
    tied = recommend(programs=[program(id="b"), program(id="a")]).recommendations
    assert [item.program_id for item in tied] == ["a", "b"]


def test_budget_changes_financial_score_and_ranking():
    items = [program(id="near", tuition_per_year=5000000),
             program(id="far", city="Almaty", tuition_per_year=1000000)]
    low = recommend(student(budget=1000000, funding=["self_funded"]), items).recommendations
    high = recommend(student(budget=5000000, funding=["self_funded"]), items).recommendations
    assert low[0].program_id == "far"
    assert high[0].program_id == "near"


def test_roadmap_completed_and_missing_exams_and_next_action():
    result = recommend(student(IELTS=None)).recommendations[0]
    tasks = {task.id: task for task in result.roadmap}
    assert tasks["take_sat"].status == "completed"
    assert tasks["take_ielts"].status == "todo"
    assert tasks["take_ielts"].target == 6
    assert result.next_action.id == "prepare_ielts"
    assert result.next_action.blocking
    assert all(task.deadline is None for task in result.roadmap)


def test_next_action_prioritizes_blocker_over_deadline_and_completed_tasks():
    tasks = [
        RoadmapTask(id="apply_scholarship", title="Funding", description="", reason="", deadline=date(2029, 1, 2), deadline_status="verified", source=SOURCE),
        RoadmapTask(id="prepare_unt", title="Prepare", description="", reason="", blocking=True),
        RoadmapTask(id="take_sat", title="SAT", description="", reason="", status="completed", blocking=True),
    ]
    assert get_next_action(tasks, AS_OF).id == "prepare_unt"
    assert get_next_action(tasks[:1], AS_OF).id == "apply_scholarship"
    assert get_next_action(tasks[2:], AS_OF) is None


def test_verified_roadmap_deadline_keeps_source():
    item = program(deadlines=[Deadline(task_type="apply_university", date=date(2029, 12, 1), source=SOURCE)])
    result = recommend(programs=[item]).recommendations[0]
    task = next(item for item in result.roadmap if item.id == "apply_university")
    assert task.deadline == date(2029, 12, 1)
    assert task.source == SOURCE
    assert task.deadline_status == "verified"


def test_response_uses_match_score_and_no_probability_language():
    output = recommend().model_dump_json(by_alias=True).lower()
    assert "matchscore" in output
    for forbidden in ("admissionprobability", "acceptancechance", "chanceofadmission", "admission probability"):
        assert forbidden not in output


def test_missing_program_information_is_explicit():
    item = Program(id="unknown", university_id="demo", university_name="Demo", name="Unknown", is_demo=True)
    result = recommend(programs=[item]).recommendations[0]
    assert result.eligibility.status == "unknown"
    assert result.financial.score is None
    assert result.next_action.id == "verify_requirements"
    assert recommend(programs=[]).recommendations == []
    assert recommend(programs=[]).warnings


def test_empty_route_cannot_imply_eligibility_without_verified_completeness():
    result = check_eligibility(student(), program(admission_routes=[AdmissionRoute(id="unknown")]), AS_OF)
    assert result.status == "unknown" and not result.eligible


def test_stale_or_future_sources_not_used():
    for source in (SOURCE.model_copy(update={"academic_year": YEAR - 1}),
                   SOURCE.model_copy(update={"verified_at": date(2031, 1, 1)})):
        item = program(admission_routes=[AdmissionRoute(id="unt", requirements_complete=True, source=source,
                       requirements={"UNT": ExamRequirement(minimum=100, source=source)})])
        result = check_eligibility(student(UNT=120), item, AS_OF)
        assert result.status == "unknown"
        assert result.available_routes[0].requirements[0].required_value is None


def test_category_has_no_ranking_effect():
    normal = recommend().recommendations[0]
    special = recommend(student(category="example-category")).recommendations[0]
    assert special.match_score == normal.match_score
    assert any("category" in warning for warning in special.warnings)


def test_exam_fit_is_bounded_and_monotonic():
    assert exam_fit(1100, 1200) == 0
    assert exam_fit(1200, 1200) == 0.7
    assert exam_fit(1370, 1200) > exam_fit(1200, 1200)
    assert exam_fit(999999, 1200) == 1
    assert exam_fit(0, 0) == 0.7


def test_normalization_and_validation():
    profile = student(city=" Астана ", interest=["Robotics", " robotics ", ""], SAT="", IELTS="  ", budget="")
    assert profile.city == ["Астана"] and profile.interest == ["Robotics"]
    assert profile.SAT is None and profile.IELTS is None and profile.budget is None
    for update in ({"mustStay": True, "city": []}, {"SAT": True}, {"budget": -1},
                   {"IELTS": float("nan")}, {"funding": ["invented"]}, {"unknown": "x"}):
        with pytest.raises(ValidationError):
            student(**update)


def test_cached_program_embeddings_are_batched_and_invalidated_by_content():
    class CountingProvider(KeywordEmbeddingProvider):
        def __init__(self):
            super().__init__()
            self.calls = []

        def encode(self, texts):
            self.calls.append(list(texts))
            return super().encode(texts)

    provider = CountingProvider()
    matcher = InterestMatcher(provider, cache_size=2)
    programs = [program(), program(id="second", name="Economics")]
    first = matcher.scores(student(), programs)
    assert len(provider.calls[0]) == 2
    assert matcher.scores(student(), programs) == first
    assert len(provider.calls) == 3  # one program batch and two student encodes
    matcher.scores(student(), [program(name="Different curriculum")])
    assert len(provider.calls) == 5
    assert len(matcher._cache) == 2


def test_repository_persists_cycles_and_excludes_demo_inactive(db_url):
    database = Database(db_url)
    database.initialize()
    repository = SQLiteProgramRepository(database)
    # Fictional rows marked non-demo here ONLY to exercise the production query filter.
    repository.save_programs([
        program(), program(id="active", is_demo=False), program(id="inactive", is_demo=False, active=False),
        program(id="old", is_demo=False, admission_year=YEAR - 1),
        program(id="active", is_demo=False, admission_year=None),
    ])
    reopened = SQLiteProgramRepository(Database(database.url))
    assert [item.id for item in reopened.get_programs_for_entry_year(YEAR)] == ["active"]
    assert reopened.get_program_by_id("active", YEAR).admission_year == YEAR
    assert reopened.get_program_by_id("demo-robotics", YEAR) is None
    reopened.save_programs([program(id="active", is_demo=False, name="Updated")])
    assert reopened.get_program_by_id("active", YEAR).name == "Updated"


@pytest.fixture
def api(db_url):
    app = create_app(db_url, program_repository=MemoryRepository([program()]))
    with TestClient(app) as client:
        credentials = {"username": "alice", "password": "strong-password-123"}
        client.post("/auth/register", json=credentials)
        token = client.post("/auth/login", json=credentials).json()["access_token"]
        yield client, {"Authorization": f"Bearer {token}"}


def test_api_post_get_saved_survey_and_authentication(api):
    client, headers = api
    profile = student().model_dump(mode="json", by_alias=True)
    assert client.post("/recommendations", json=profile).status_code == 401
    assert client.get("/recommendations").status_code == 401
    assert client.get("/recommendations", headers=headers).status_code == 404
    response = client.post("/recommendations?limit=1", headers=headers, json=profile)
    assert response.status_code == 200
    result = response.json()
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["eligibility"]["bestAdmissionRoute"] == "sat"
    assert client.post("/survey", headers=headers, json={"survey": profile}).status_code == 200
    assert client.get("/recommendations", headers=headers).json() == result


def test_api_invalid_profiles_and_limits(api):
    client, headers = api
    profile = student().model_dump(mode="json", by_alias=True)
    for limit in (0, 51):
        assert client.post(f"/recommendations?limit={limit}", headers=headers, json=profile).status_code == 422
    assert client.post("/recommendations", headers=headers, json={**profile, "mustStay": True, "city": []}).status_code == 422
    profile["budget"] = {"amount": 1000}
    assert client.post("/survey", headers=headers, json={"survey": profile}).status_code == 200
    assert client.get("/recommendations", headers=headers).status_code == 422


def test_api_empty_production_catalog(db_url):
    with TestClient(create_app(db_url, program_repository=MemoryRepository([]))) as client:
        credentials = {"username": "alice", "password": "strong-password-123"}
        client.post("/auth/register", json=credentials)
        token = client.post("/auth/login", json=credentials).json()["access_token"]
        response = client.post("/recommendations", json=student().model_dump(by_alias=True),
                               headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["recommendations"] == []
        assert response.json()["warnings"]


def test_expired_funding_deadline_removes_option_and_task():
    item = program(deadlines=[Deadline(task_type="apply_state_grant", route_id="sat",
                                      date=date(2028, 12, 31), source=SOURCE)])
    result = recommend(programs=[item]).recommendations[0]
    assert result.financial.funding_options == []
    assert not result.financial.funding_dependent
    assert all(task.id != "apply_state_grant" for task in result.roadmap)
    assert any("deadline has passed" in warning for warning in result.warnings)


def test_expired_task_not_selected():
    task = RoadmapTask(id="apply_scholarship", title="Funding", description="", reason="",
                       deadline=date(2028, 12, 31), deadline_status="verified", source=SOURCE)
    assert get_next_action([task], AS_OF) is None


def test_cycle_deactivation_does_not_fall_back_to_unknown_year(db_url):
    database = Database(db_url)
    database.initialize()
    repository = SQLiteProgramRepository(database)
    repository.save_programs([program(is_demo=False, admission_year=None),
                              program(is_demo=False, active=False)])
    assert repository.get_programs_for_entry_year(YEAR) == []
    assert repository.get_program_by_id("demo-robotics", YEAR) is None


def test_initialization_preserves_existing_auth_and_survey_tables(db_url):
    database = Database(db_url)
    database.initialize()
    user = database.create_user("alice", "existing-hash")
    database.save_survey(user["id"], {"grade": 10})
    with psycopg.connect(database.url) as connection:
        connection.execute("DROP TABLE programs")
        connection.execute('DELETE FROM schema_migrations WHERE version=1')
    database.initialize()
    assert database.get_user("alice")["password_hash"] == "existing-hash"
    assert database.get_survey(user["id"]) == {"grade": 10}
    assert database.get_program_records(YEAR) == []


def test_catalog_import_command_and_demo_fixture(db_url):
    root = Path(__file__).resolve().parents[1]
    path = db_url
    result = subprocess.run(
        [sys.executable, "-m", "recommendation.import_catalog", str(root / "examples/programs.demo.json"),
         "--database", str(path)], cwd=root, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "1 demo records" in result.stdout
    assert Database(path).get_program_records(2027) == []
    with psycopg.connect(path) as connection:
        row = connection.execute("SELECT data_json FROM programs").fetchone()
    assert row[0]["is_demo"] is True


def test_past_entry_year_excluded_and_no_numeric_data_does_not_crash():
    assert recommend(student(entryYear=2020)).recommendations == []
    result = recommend(student(interest=[], academicStrengths=[], extracurricularInterests=[],
                               budget=None, city=[]), [Program(id="empty", university_id="demo",
                               university_name="Demo", name="Demo", is_demo=True)]).recommendations[0]
    assert result.match_score == 0
    assert result.weights == {}


def test_multiple_passing_routes_choose_strongest_exam_fit():
    item = program(admission_routes=[route("sat", SAT=1350), route("unt", UNT=100)])
    result = check_eligibility(student(UNT=125), item, AS_OF)
    assert result.best_admission_route == "unt"


def test_scholarship_path_and_campus_activity_component():
    item = program(state_grant_available=False,
                   scholarships=[{"name": "Demo award", "source": SOURCE}],
                   extracurricular=["Robotics"])
    result = recommend(student(funding=["university_scholarship"]), [item]).recommendations[0]
    assert result.financial.funding_options == ["university_scholarship"]
    assert result.scores["extracurricular"] == 1
    assert "extracurricular" in result.weights
    assert any(task.id == "apply_scholarship" for task in result.roadmap)
