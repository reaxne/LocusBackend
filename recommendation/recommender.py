from datetime import date

from recommendation.config import DEFAULT_CONFIG, ScoringConfig
from recommendation.eligibility import check_eligibility
from recommendation.embeddings import InterestMatcher
from recommendation.models import (
    ExcludedProgram, Recommendation, RecommendationResponse, RequirementsSummary, StudentProfile, StudentSummary,
)
from recommendation.normalization import concepts
from recommendation.repository import ProgramRepository
from recommendation.roadmap import generate_roadmap, get_next_action
from recommendation.localization import localize_response, text, values
from recommendation.scoring import (
    academic_match, city_match, extracurricular_match, financial_match,
    hard_constraint_reasons, language_match, weighted_average,
)

MAX_RECOMMENDATIONS = 4


class Recommender:
    def __init__(self, repository: ProgramRepository, matcher: InterestMatcher | None = None,
                 config: ScoringConfig = DEFAULT_CONFIG):
        self.repository = repository
        self.matcher = matcher or InterestMatcher()
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
        interest_matches = self.matcher.matches(student, [item for item, _ in candidates])
        recommendations = []
        for program, eligibility in candidates:
            financial = financial_match(student, program, as_of, self.config, eligibility.best_admission_route)
            best = next((route for route in eligibility.available_routes
                         if route.route == eligibility.best_admission_route), None)
            scores = {
                "interest": interest_matches[program.id].score,
                "academic": academic_match(student, program),
                "financial": financial.score, "city": city_match(student, program, self.config),
                "language": language_match(student, program),
                "exam": best.exam_score if best else None,
                "extracurricular": extracurricular_match(student, program),
            }
            match_score, weights = weighted_average(student, scores, self.config)
            reasons = []
            warnings = [*eligibility.warnings, *financial.warnings]
            if interest_matches[program.id].matched_interests:
                reasons.append("Программа соответствует интересам: " + ", ".join(values(interest_matches[program.id].matched_interests)) + ".")
            if scores["academic"]:
                matches = [item for item in program.recommended_academic_strengths
                           if concepts(item) & {concept for value in student.academic_strengths for concept in concepts(value)}]
                reasons.append("Соответствующие рекомендуемые академические навыки: " + ", ".join(values(matches)) + ".")
            if scores["city"] == 1:
                reasons.append("Программа находится в одном из предпочитаемых вами городов.")
            elif scores["city"] is not None:
                warnings.append("Программа находится вне предпочитаемых городов; в анкете допускается переезд.")
            if scores["language"] == 1:
                reasons.append("Программа предлагает предпочитаемый вами язык обучения.")
            elif student.study_language and scores["language"] is None:
                warnings.append("Язык обучения программы не указан, поэтому языковое предпочтение не учтено в оценке.")
            if eligibility.eligible and best:
                reasons.append("Ваши результаты соответствуют подтверждённому маршруту поступления.")
            if financial.within_budget:
                reasons.append("Подтверждённая годовая стоимость обучения укладывается в ваш бюджет.")
            if financial.funding_options:
                reasons.append("Подтверждённые варианты финансирования соответствуют вашему выбору.")
            if student.category:
                warnings.append("Категория сохранена, но не участвует в оценке: подтверждённые правила для категорий не внесены в каталог.")
            if student.academic_performance and student.academic_performance != "unknown":
                warnings.append("Академическая успеваемость сохранена, но не участвует в оценке: в каталоге нет сопоставимых критериев.")
            if student.constraints:
                warnings.append("Дополнительные условия сохранены, но не участвуют в оценке: в каталоге нет структурированных полей для них.")
            if student.country and student.country.casefold() != "kazakhstan":
                warnings.append("Страна сохранена, но не участвует в оценке: в каталоге нет поля страны.")
            if program.is_demo:
                warnings.append("Демонстрационная программа: вымышленные данные предназначены только для тестирования.")
            if not weights:
                warnings.append("Недостаточно данных анкеты или программы для расчёта соответствия; оценка равна нулю.")
            unavailable = [key for key, value in scores.items() if value is None]
            if unavailable:
                warnings.append("Компоненты без пригодных данных не учтены в ранжировании.")
            requirements = best.requirements if best else []
            summary = RequirementsSummary(
                completed=[item for item in requirements if item.status == "passed"],
                missing=[item for item in requirements if item.status == "missing"],
                failed=[item for item in requirements if item.status == "failed"],
                unknown=[item for item in requirements if item.status == "unknown"],
            )
            roadmap = generate_roadmap(student, program, eligibility, financial, as_of)
            catalog_source = program.sources.get('program_catalog')
            field_sources = {
                key: source for key, source in {
                    'name': catalog_source,
                    'description': catalog_source,
                    'admissions': catalog_source,
                    'language': program.sources.get('languages'),
                    'tuition': program.sources.get('tuition_per_year'),
                    'grant': program.sources.get('state_grant_available') or
                    next((item.source for item in program.scholarships or [] if item.source), None),
                    'duration': program.sources.get('duration'),
                    'documents': program.sources.get('documents'),
                }.items() if source is not None
            }
            sources = {**program.sources}
            for name, source in field_sources.items():
                sources.setdefault(name, source)
            recommendations.append(Recommendation(
                university_id=program.university_id, university=program.university_name,
                university_short_name=program.university_short_name or program.university_name,
                university_url=program.university_url,
                program_id=program.id, program=program.name, description=program.description,
                duration=program.duration, documents=program.documents,
                admissions_url=program.admissions_url or (str(catalog_source.url) if catalog_source else None),
                is_demo=program.is_demo,
                match_score=min(1.0, max(0.0, match_score)), eligibility=eligibility,
                scores=scores,
                score_breakdown={
                    "interest": scores["interest"], "academicStrengths": scores["academic"],
                    "location": scores["city"], "language": scores["language"],
                    "financial": scores["financial"], "eligibility": (
                        1.0 if eligibility.status == "eligible_now" else
                        0.0 if eligibility.status == "ineligible" else None
                    ), "exam": scores["exam"], "extracurricular": scores["extracurricular"],
                },
                weights=weights, financial=financial, why_recommended=reasons,
                warnings=warnings, requirements=summary, missing_requirements=summary.missing,
                roadmap=roadmap, next_action=get_next_action(roadmap, as_of), sources=sources,
                field_sources=field_sources,
                city=program.city, interests=program.interests, languages=program.languages,
                program_group=program.program_group,
            ))
        recommendations.sort(key=lambda item: (-item.match_score, item.program_id))
        constraints = []
        if student.budget is not None:
            constraints.append(f"Предпочтительный годовой бюджет на обучение: {student.budget} тенге")
        if student.city:
            constraints.append(("Необходимо остаться в городе: " if student.must_stay else "Предпочитаемые города: ") + ", ".join(student.city))
        if student.constraints:
            constraints.append("Дополнительные условия сохранены в анкете.")
        return localize_response(RecommendationResponse(
            student_summary=StudentSummary(
                target_entry_year=student.entry_year, main_interests=student.interest,
                academic_strengths=student.academic_strengths, constraints=constraints, category=student.category,
                study_language=student.study_language, academic_performance=student.academic_performance,
                country=student.country, level=student.level,
            ),
            recommendations=recommendations[:limit], evaluated_at=as_of,
            matching_method=self.matcher.name,
            excluded_programs=excluded,
            warnings=[] if recommendations else ["Нет подходящих программ. Каталог может быть пустым, относиться к другому году набора или не соответствовать условиям анкеты."],
        ))
