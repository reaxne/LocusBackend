import json
from datetime import date
from pathlib import Path

import pytest

from recommendation.models import StudentProfile
from recommendation.recommender import Recommender
from recommendation.repository import JSONProgramRepository


def record(program_id="test-program", **updates):
    return {
        "id": program_id, "universityId": "test-university",
        "universityName": "Test University", "name": "Test Program",
        "interests": ["programming"], **updates,
    }


def write_catalog(tmp_path, data):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_selects_exact_year_then_generic_and_sorts(tmp_path):
    repository = JSONProgramRepository(write_catalog(tmp_path, [
        record("z", name="Общая программа"),
        record("z", admissionYear=2026, tuitionPerYear=123),
        record("a"), record("future-only", admissionYear=2028),
    ]))
    assert [p.id for p in repository.get_programs_for_entry_year(2026)] == ["a", "z"]
    assert repository.get_program_by_id("z", 2026).tuition_per_year == 123
    assert repository.get_program_by_id("z", 2027).name == "Общая программа"
    assert repository.get_program_by_id("z", 2027).tuition_per_year is None
    assert repository.get_program_by_id("future-only", 2026) is None
    assert repository.get_program_by_id("missing", 2026) is None


@pytest.mark.parametrize("flag", [{"active": False}, {"isDemo": True}])
def test_excluded_cycle_does_not_fall_back(tmp_path, flag):
    repository = JSONProgramRepository(write_catalog(tmp_path, [
        record(), record(admissionYear=2026, **flag), record("hidden", **flag),
    ]))
    assert repository.get_programs_for_entry_year(2026) == []
    assert repository.get_program_by_id("test-program", 2026) is None
    assert repository.get_program_by_id("test-program", 2027) is not None


def test_nested_format_metadata_and_overrides(tmp_path):
    repository = JSONProgramRepository(write_catalog(tmp_path, {
        "schemaVersion": "1.0.0", "programEvidence": {},
        "universities": [{"id": "test-university", "cityAliases": [], "programs": [record()]}],
        "programCycleOverrides": [record(admissionYear=2026, name="Cycle version")],
    }))
    assert repository.get_program_by_id("test-program", 2026).name == "Cycle version"
    assert repository.get_program_by_id("test-program", 2027).name == "Test Program"


@pytest.mark.parametrize("data", [
    [record(), record()],
    [record(admissionYear=2026), record(admissionYear=2026)],
    [record(tuitionPerYear=-1)],
    [record(unexpectedField=True)],
    {}, None, "not a catalog",
    {"universities": [{"id": "wrong", "programs": [record()]}]},
    {"universities": [], "programCycleOverrides": [record(admissionYear=2026)]},
])
def test_rejects_invalid_catalog_with_file_context(tmp_path, data):
    with pytest.raises(ValueError, match="Invalid program catalog.*catalog.json"):
        JSONProgramRepository(write_catalog(tmp_path, data))


def test_malformed_json_and_missing_file(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("[", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid program catalog"):
        JSONProgramRepository(path)
    with pytest.raises(FileNotFoundError):
        JSONProgramRepository(tmp_path / "missing.json")


def test_snapshot_and_returned_models_are_isolated(tmp_path):
    path = write_catalog(tmp_path, [record()])
    repository = JSONProgramRepository(path)
    repository.get_program_by_id("test-program", 2026).interests.append("changed")
    repository.get_programs_for_entry_year(2026)[0].active = False
    path.write_text("[]", encoding="utf-8")
    assert repository.get_program_by_id("test-program", 2026).interests == ["programming"]
    assert len(repository.get_programs_for_entry_year(2026)) == 1
    assert JSONProgramRepository(path).get_programs_for_entry_year(2026) == []


def test_empty_catalog_and_utf8_bom(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("[]", encoding="utf-8-sig")
    assert JSONProgramRepository(str(path)).get_programs_for_entry_year(2026) == []


def test_real_formats_match_and_work_with_recommender(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    flat = JSONProgramRepository()
    nested = JSONProgramRepository(root / "catalogs/kazakhstan_universities.json")
    for year in (2026, 2027):
        assert flat.get_programs_for_entry_year(year) == nested.get_programs_for_entry_year(year)
        assert len(flat.get_programs_for_entry_year(year)) == 82
    assert flat.get_program_by_id("kz-iitu-cs", 2026).tuition_per_year == 1479000
    assert flat.get_program_by_id("kz-iitu-cs", 2027).tuition_per_year is None
    result = Recommender(flat).recommend(
        StudentProfile(grade=11, entryYear=2027, interest=["programming"]),
        as_of=date(2026, 9, 18),
    )
    assert result.recommendations
    assert all(r.eligibility.status == "unknown" for r in result.recommendations)
