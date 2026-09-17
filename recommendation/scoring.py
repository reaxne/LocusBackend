from datetime import date

from recommendation.config import DEFAULT_CONFIG, ScoringConfig
from recommendation.models import FinancialResult, Program, StudentProfile
from recommendation.normalization import normalize


def academic_match(student: StudentProfile, program: Program) -> float | None:
    required = {normalize(item) for item in program.recommended_academic_strengths if item.strip()}
    strengths = {normalize(item) for item in student.academic_strengths}
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
        reasons.append("The program is inactive.")
    if student.entry_year < as_of.year:
        reasons.append("The requested entry year has passed.")
    if program.admission_year is not None and program.admission_year != student.entry_year:
        reasons.append("The program admission cycle differs from the requested entry year.")
    if student.must_stay and city_match(student, program) != 1.0:
        reasons.append("The program city is outside the required cities or is unknown.")
    return reasons


def passes_hard_constraints(student: StudentProfile, program: Program, as_of: date) -> bool:
    return not hard_constraint_reasons(student, program, as_of)


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
            warnings.append(f"The verified {option} application deadline has passed.")
    tuition = program.tuition_per_year if verified("tuition_per_year") else None
    difference = tuition - student.budget if tuition is not None and student.budget is not None else None
    within = difference <= 0 if difference is not None else None
    dependent = within is False and bool(options)
    score = None
    if within is True:
        score = 1.0
    elif dependent:
        score = config.funding_dependent_score
        warnings.append(f"Annual tuition exceeds your budget by {difference} KZT; this option depends on competitive funding, which is not guaranteed and may not cover the full cost.")
    elif within is False:
        score = student.budget / tuition
        warnings.append(f"Annual tuition exceeds your budget by {difference} KZT; no selected funding path is verified.")
    if tuition is None:
        warnings.append("Annual tuition is unknown or unverified for this entry year.")
    if options and not dependent:
        warnings.append("Selected funding paths exist, but awards and coverage are not guaranteed.")
    if any(item in student.funding for item in ("state_grant", "university_scholarship")) and not options:
        warnings.append("Availability of the selected grant or scholarship paths is not verified.")
    return FinancialResult(
        score=score, within_budget=within, difference=difference, tuition_per_year=tuition,
        funding_options=options, funding_dependent=dependent, warnings=warnings,
    )


def extracurricular_match(student: StudentProfile, program: Program) -> float | None:
    if not student.extracurricular_interests or not program.extracurricular:
        return None
    wanted = {normalize(item) for item in student.extracurricular_interests}
    offered = {normalize(item) for item in program.extracurricular}
    return len(wanted & offered) / len(wanted)


def calculate_weights(student: StudentProfile, available_components: set[str],
                      config: ScoringConfig = DEFAULT_CONFIG) -> dict[str, float]:
    weights = {key: value for key, value in config.weights.items() if key in available_components}
    if student.must_stay:
        weights.pop("city", None)
    if "financial" in weights and student.budget is not None:
        weights["financial"] = config.constrained_financial_weight
    total = sum(weights.values())
    return {key: value / total for key, value in weights.items()} if total else {}
