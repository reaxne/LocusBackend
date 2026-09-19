from datetime import date

from recommendation.config import DEFAULT_CONFIG, ScoringConfig
from recommendation.models import FinancialResult, Program, StudentProfile
from recommendation.normalization import concepts, language, level, normalize


def academic_match(student: StudentProfile, program: Program) -> float | None:
    required = {concept for item in program.recommended_academic_strengths for concept in concepts(item)}
    strengths = {concept for item in student.academic_strengths for concept in concepts(item)}
    if not required or not strengths:
        return None
    return len(required & strengths) / len(required)


def city_match(student: StudentProfile, program: Program,
               config: ScoringConfig = DEFAULT_CONFIG) -> float | None:
    if not student.city or not program.city:
        return None
    return 1.0 if normalize(program.city) in {normalize(item) for item in student.city} else config.outside_city_score


def hard_constraint_reasons(student: StudentProfile, program: Program, as_of: date) -> list[str]:
    reasons = []
    if not program.active:
        reasons.append("Программа неактивна.")
    if student.entry_year < as_of.year:
        reasons.append("Запрошенный год поступления уже прошёл.")
    if program.admission_year is not None and program.admission_year != student.entry_year:
        reasons.append("Год набора программы не совпадает с запрошенным годом поступления.")
    if student.must_stay and city_match(student, program) != 1.0:
        reasons.append("Город программы не входит в обязательные города или не указан.")
    if student.level and program.degree and level(student.level) != level(program.degree):
        reasons.append("Уровень программы не совпадает с запрошенным уровнем.")
    return reasons


def financial_match(student: StudentProfile, program: Program, as_of: date | None = None,
                    config: ScoringConfig = DEFAULT_CONFIG,
                    route_id: str | None = None) -> FinancialResult:
    as_of = as_of or date.today()

    def verified(field):
        source = program.sources.get(field)
        return (program.admission_year == student.entry_year and source is not None
                and source.applies(student.entry_year, as_of))

    options = []
    if "state_grant" in student.funding and program.state_grant_available is True and verified("state_grant_available"):
        options.append("state_grant")
    if "university_scholarship" in student.funding and program.admission_year == student.entry_year:
        if any(item.source is not None and item.source.applies(student.entry_year, as_of)
               for item in program.scholarships or []):
            options.append("university_scholarship")
    warnings = []
    for option in list(options):
        task = "apply_state_grant" if option == "state_grant" else "apply_scholarship"
        if any(item.task_type == task and item.route_id in (None, route_id)
               and item.verified(student.entry_year, as_of) and item.date < as_of
               for item in program.deadlines):
            options.remove(option)
            warnings.append("Подтверждённый срок подачи заявки на финансирование уже прошёл.")
    tuition = program.tuition_per_year if verified("tuition_per_year") else None
    difference = tuition - student.budget if tuition is not None and student.budget is not None else None
    within = difference <= 0 if difference is not None else None
    dependent = within is False and bool(options)
    score = None
    if within is True:
        score = 1.0
    elif dependent:
        score = config.funding_dependent_score
        warnings.append(f"Годовая стоимость обучения превышает ваш бюджет на {difference} тенге; вариант зависит от конкурсного финансирования, которое не гарантировано и может не покрыть всю сумму.")
    elif within is False:
        score = student.budget / tuition
        warnings.append(f"Годовая стоимость обучения превышает ваш бюджет на {difference} тенге; выбранные варианты финансирования не подтверждены.")
    if tuition is None:
        warnings.append("Годовая стоимость обучения для этого года набора не указана или не подтверждена.")
    if options and not dependent:
        warnings.append("Выбранные варианты финансирования существуют, но получение выплаты и покрытие расходов не гарантированы.")
    if any(item in student.funding for item in ("state_grant", "university_scholarship")) and not options:
        warnings.append("Доступность выбранного гранта или стипендии не подтверждена.")
    return FinancialResult(
        score=score, within_budget=within, difference=difference, tuition_per_year=tuition,
        funding_options=options, funding_dependent=dependent, warnings=warnings,
    )


def extracurricular_match(student: StudentProfile, program: Program) -> float | None:
    if not student.extracurricular_interests or not program.extracurricular:
        return None
    wanted = {concept for item in student.extracurricular_interests for concept in concepts(item)}
    offered = {concept for item in program.extracurricular for concept in concepts(item)}
    return len(wanted & offered) / len(wanted)


def language_match(student: StudentProfile, program: Program) -> float | None:
    if not student.study_language or normalize(student.study_language) == "any" or not program.languages:
        return None
    requested = language(student.study_language)
    offered = {language(item) for item in program.languages}
    return 1.0 if requested in offered else 0.0


def calculate_weights(student: StudentProfile, available_components: set[str],
                      config: ScoringConfig = DEFAULT_CONFIG) -> dict[str, float]:
    weights = {key: value for key, value in config.weights.items() if key in available_components}
    if student.must_stay:
        weights.pop("city", None)
    if "financial" in weights and student.budget is not None:
        weights["financial"] = config.constrained_financial_weight
    total = sum(weights.values())
    return {key: value / total for key, value in weights.items()} if total else {}


def weighted_average(student: StudentProfile, components: dict[str, float | None],
                     config: ScoringConfig = DEFAULT_CONFIG) -> tuple[float, dict[str, float]]:
    """Average only comparable components and return their normalized weights."""
    weights = calculate_weights(student, {key for key, value in components.items() if value is not None}, config)
    return sum(components[key] * weight for key, weight in weights.items()), weights
