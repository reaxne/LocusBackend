from datetime import date

from recommendation.config import DEFAULT_CONFIG, ScoringConfig
from recommendation.models import (
    EligibilityResult, Program, RequirementResult, RouteResult, StudentProfile,
)


def exam_fit(value: float, minimum: float, config: ScoringConfig = DEFAULT_CONFIG) -> float:
    if value < minimum:
        return 0.0
    if minimum == 0:
        return config.exam_baseline
    margin = min(1.0, (value - minimum) / (minimum * config.exam_full_score_margin))
    return config.exam_baseline + (1 - config.exam_baseline) * margin


def check_eligibility(
    student: StudentProfile, program: Program, as_of: date | None = None,
    config: ScoringConfig = DEFAULT_CONFIG,
) -> EligibilityResult:
    as_of = as_of or date.today()
    results = []
    cycle_known = program.admission_year == student.entry_year
    for route in program.admission_routes:
        warnings = []
        complete = (cycle_known and route.requirements_complete and route.source is not None
                    and route.source.applies(student.entry_year, as_of))
        requirements = []
        for exam, requirement in sorted(route.requirements.items()):
            value = getattr(student, exam)
            verified = (cycle_known and requirement.source is not None
                        and requirement.source.applies(student.entry_year, as_of))
            minimum = requirement.minimum if verified else None
            state = ("unknown" if minimum is None else "missing" if value is None
                     else "passed" if value >= minimum else "failed")
            requirements.append(RequirementResult(
                exam=exam, student_value=value, required_value=minimum, status=state,
                source=requirement.source if verified else None,
            ))
        deadlines = [item.date for item in program.deadlines
                     if cycle_known and item.task_type == "apply_university" and item.route_id in (None, route.id)
                     and item.verified(student.entry_year, as_of)]
        cutoff = min(deadlines) if deadlines else None
        # Jan 1 is a conservative planning horizon, never a claimed admission deadline.
        horizon = cutoff or date(student.entry_year, 1, 1)
        enough_time = (horizon - as_of).days >= config.minimum_preparation_days
        states = {item.status for item in requirements}
        if (cutoff is not None and cutoff < as_of) or student.entry_year < as_of.year:
            state = "ineligible"
            warnings.append("The admission cycle or verified application deadline has passed.")
        elif not complete or "unknown" in states:
            state = "unknown"
            warnings.append("The complete admission requirements are not verified for this entry year.")
        elif not states or states == {"passed"}:
            state = "eligible_now"
        elif enough_time and ("failed" not in states or route.allows_retakes):
            state = "potentially_eligible"
            warnings.append(
                "Eligibility is provisional: complete the outstanding exams and verify test availability."
            )
            if cutoff is None:
                warnings.append("The application deadline is unknown; planning uses the future entry year.")
        else:
            state = "ineligible"
            warnings.append("Exam requirements are unmet and the configured preparation window or retake policy is not satisfied.")
        score = None
        if state == "eligible_now" and requirements:
            score = sum(exam_fit(item.student_value, item.required_value, config)
                        for item in requirements) / len(requirements)
        results.append(RouteResult(
            route=route.id, eligible=state == "eligible_now", status=state,
            requirements=requirements, exam_score=score, warnings=warnings,
        ))
    preference = {"eligible_now": 0, "potentially_eligible": 1, "unknown": 2, "ineligible": 3}
    ordered = sorted(results, key=lambda item: (
        preference[item.status], -(item.exam_score or 0),
        sum(req.status != "passed" for req in item.requirements), item.route,
    ))
    best = ordered[0] if ordered else None
    state = best.status if best else "unknown"
    warnings = list(best.warnings) if best else ["No admission routes are available; confirm requirements with the university."]
    if student.entry_year < as_of.year or (program.admission_year is not None and not cycle_known):
        state = "ineligible"
        warnings.append("This program does not match the requested active admission cycle.")
    return EligibilityResult(
        eligible=state == "eligible_now", status=state, available_routes=results,
        best_admission_route=best.route if best else None,
        missing_requirements=[item for item in best.requirements if item.status == "missing"] if best else [],
        failed_requirements=[item for item in best.requirements if item.status == "failed"] if best else [],
        warnings=warnings,
    )
