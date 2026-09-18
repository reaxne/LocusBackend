from datetime import date

from recommendation.config import DEFAULT_CONFIG, ScoringConfig
from recommendation.eligibility import check_eligibility
from recommendation.embeddings import InterestMatcher
from recommendation.models import (
    ExcludedProgram, Recommendation, RecommendationResponse, RequirementsSummary, StudentProfile, StudentSummary,
)
from recommendation.normalization import normalize
from recommendation.repository import ProgramRepository
from recommendation.roadmap import generate_roadmap, get_next_action
from recommendation.scoring import (
    academic_match, calculate_weights, city_match, extracurricular_match,
    financial_match, hard_constraint_reasons,
)

MAX_RECOMMENDATIONS = 4


class Recommender:
    def __init__(self, repository: ProgramRepository, matcher: InterestMatcher | None = None,
                 config: ScoringConfig = DEFAULT_CONFIG):
        self.repository = repository
        self.matcher = matcher or InterestMatcher(cache_size=config.embedding_cache_size)
        self.config = config

    def recommend(self, student: StudentProfile, limit: int = MAX_RECOMMENDATIONS,
                  as_of: date | None = None, program_ids: list[str] | None = None) -> RecommendationResponse:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        # Bound automatic suggestions even for older clients requesting more.
        # Explicit roadmap selections retain all requested programs.
        if program_ids is None:
            limit = min(limit, MAX_RECOMMENDATIONS)
        as_of = as_of or date.today()
        candidates = []
        excluded = []
        for program in self.repository.get_programs_for_entry_year(student.entry_year):
            if program_ids is not None and program.id not in program_ids:
                continue
            reasons = hard_constraint_reasons(student, program, as_of)
            if reasons and program_ids is None:
                excluded.append(ExcludedProgram(program_id=program.id, reasons=reasons))
                continue
            eligibility = check_eligibility(student, program, as_of, self.config)
            if eligibility.status == "ineligible" and program_ids is None:
                excluded.append(ExcludedProgram(program_id=program.id, reasons=eligibility.warnings, eligibility=eligibility))
            else:
                candidates.append((program, eligibility))
        interests = self.matcher.scores(student, [item for item, _ in candidates])
        recommendations = []
        for program, eligibility in candidates:
            financial = financial_match(student, program, as_of, self.config, eligibility.best_admission_route)
            best = next((route for route in eligibility.available_routes
                         if route.route == eligibility.best_admission_route), None)
            scores = {
                "interest": interests[program.id], "academic": academic_match(student, program),
                "financial": financial.score, "city": city_match(student, program, self.config),
                "exam": best.exam_score if best else None,
                "extracurricular": extracurricular_match(student, program),
            }
            weights = calculate_weights(student, {key for key, value in scores.items() if value is not None}, self.config)
            match_score = sum(scores[key] * weight for key, weight in weights.items())
            reasons = []
            warnings = [*eligibility.warnings, *financial.warnings]
            if scores["interest"] is not None and scores["interest"] > 0:
                reasons.append("Your interests and activities overlap with the program text according to " + self.matcher.provider.name + ".")
            if scores["academic"]:
                matches = [item for item in program.recommended_academic_strengths
                           if normalize(item) in {normalize(value) for value in student.academic_strengths}]
                reasons.append("Matching recommended academic strengths: " + ", ".join(matches) + ".")
            if scores["city"] == 1:
                reasons.append("The program is in one of your preferred cities.")
            elif scores["city"] is not None:
                warnings.append("The program is outside your preferred cities; relocation is optional in your profile.")
            if eligibility.eligible and best:
                reasons.append(f"Your results satisfy the verified {best.route} admission route.")
            if financial.within_budget:
                reasons.append("Verified annual tuition is within your preferred budget.")
            if financial.funding_options:
                reasons.append("Verified funding paths match your selection: " + ", ".join(financial.funding_options) + ".")
            if student.category:
                warnings.append("Your category is preserved but not used: verified category-specific rules are not modeled.")
            if program.is_demo:
                warnings.append("DEMO program: fictional data for testing or examples only.")
            if not weights:
                warnings.append("Insufficient profile or program information to calculate compatibility; score is zero.")
            unavailable = [key for key, value in scores.items() if value is None]
            if unavailable:
                warnings.append("Components without usable data are omitted from ranking: " + ", ".join(unavailable) + ".")
            requirements = best.requirements if best else []
            summary = RequirementsSummary(
                completed=[item for item in requirements if item.status == "passed"],
                missing=[item for item in requirements if item.status == "missing"],
                failed=[item for item in requirements if item.status == "failed"],
                unknown=[item for item in requirements if item.status == "unknown"],
            )
            roadmap = generate_roadmap(student, program, eligibility, financial, as_of)
            recommendations.append(Recommendation(
                university_id=program.university_id, university=program.university_name,
                program_id=program.id, program=program.name, is_demo=program.is_demo,
                match_score=min(1.0, max(0.0, match_score)), eligibility=eligibility,
                scores=scores, weights=weights, financial=financial, why_recommended=reasons,
                warnings=warnings, requirements=summary, missing_requirements=summary.missing,
                roadmap=roadmap, next_action=get_next_action(roadmap, as_of), sources=program.sources,
                city=program.city, interests=program.interests, languages=program.languages,
                program_group=program.program_group,
            ))
        recommendations.sort(key=lambda item: (-item.match_score, item.program_id))
        constraints = []
        if student.budget is not None:
            constraints.append(f"Preferred annual tuition budget: {student.budget} KZT")
        if student.city:
            constraints.append(("Must stay in: " if student.must_stay else "Preferred cities: ") + ", ".join(student.city))
        return RecommendationResponse(
            student_summary=StudentSummary(
                target_entry_year=student.entry_year, main_interests=student.interest,
                academic_strengths=student.academic_strengths, constraints=constraints, category=student.category,
            ),
            recommendations=recommendations[:limit], evaluated_at=as_of,
            matching_method=self.matcher.provider.name,
            excluded_programs=excluded,
            warnings=[] if recommendations else ["No matching programs are available. The catalog may be empty, outside this cycle, or excluded by your constraints and admission requirements."],
        )
