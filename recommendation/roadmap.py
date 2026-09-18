from datetime import date

from recommendation.models import (
    EligibilityResult, FinancialResult, Program, RoadmapTask, StudentProfile,
)


def generate_roadmap(student: StudentProfile, program: Program, eligibility: EligibilityResult,
                     financial: FinancialResult, as_of: date | None = None) -> list[RoadmapTask]:
    as_of = as_of or date.today()
    tasks = []
    route = next((item for item in eligibility.available_routes
                  if item.route == eligibility.best_admission_route), None)

    def add(task_id, title, description, reason, **kwargs):
        explicit_source = kwargs.pop('source', None)
        deadlines = [item for item in program.deadlines
                     if program.admission_year == student.entry_year and item.task_type == task_id
                     and item.route_id in (None, eligibility.best_admission_route)
                     and item.verified(student.entry_year, as_of)]
        deadline = min(deadlines, key=lambda item: item.date) if deadlines else None
        tasks.append(RoadmapTask(
            id=task_id, title=title, description=description, reason=reason,
            deadline=deadline.date if deadline else None,
            deadline_status="verified" if deadline else "unknown",
            source=deadline.source if deadline else explicit_source, **kwargs,
        ))

    unverified_tasks = []
    if eligibility.status == "unknown":
        add("verify_requirements", "Confirm admission requirements",
            "Ask the university to confirm the complete admission route and deadlines for your entry year.",
            "Admission information is missing or unverified.", blocking=True, priority="high")
        unverified_tasks.append("verify_requirements")
    if not any(item.task_type == "apply_university" and item.verified(student.entry_year, as_of)
               and program.admission_year == student.entry_year
               and item.route_id in (None, eligibility.best_admission_route) for item in program.deadlines):
        add("verify_deadlines", "Confirm the application deadline",
            "Check the official admissions schedule before planning exam dates or submitting an application.",
            "There is no verified application deadline for the selected route.", priority="high")
        unverified_tasks.append("verify_deadlines")
    if student.grade <= 10 or not student.interest:
        add("choose_direction", "Explore your study direction",
            "Compare the curriculum and careers with your interests before choosing a program.",
            f"You are planning from grade {student.grade} for entry in {student.entry_year}.")
    exam_task_ids = []
    if route:
        for requirement in route.requirements:
            if requirement.status == "unknown":
                continue
            exam = requirement.exam
            done = requirement.status == "passed"
            reason = f"The {route.route} route requires {exam} >= {requirement.required_value:g}."
            prep_id, exam_id = f"prepare_{exam.lower()}", f"take_{exam.lower()}"
            status = "completed" if done else "todo"
            add(prep_id, f"Prepare for {exam}",
                "Review the exam format and plan preparation for the required score.", reason,
                status=status, priority="high", target=requirement.required_value, blocking=not done,
                source=requirement.source)
            add(exam_id, f"Take {exam}" if requirement.status != "failed" else f"Retake {exam}",
                "Confirm test availability and submit a qualifying result to the university.", reason,
                status=status, priority="high", target=requirement.required_value, blocking=not done,
                depends_on=[prep_id], source=requirement.source)
            exam_task_ids.append(exam_id)
    add("prepare_documents", "Prepare application documents",
        "Obtain the official document checklist and prepare the requested materials; the checklist is not stored here.",
        "Application preparation is needed before submission.")
    add("apply_university", "Apply to the university",
        "Confirm the current application process and submit through the official admissions channel.",
        f"Submit an application for {program.name} for entry in {student.entry_year}.",
        depends_on=["prepare_documents", *exam_task_ids, *unverified_tasks])
    for option in financial.funding_options:
        task_id = "apply_state_grant" if option == "state_grant" else "apply_scholarship"
        add(task_id, "Apply for state grant funding" if option == "state_grant" else "Apply for a scholarship",
            "Verify the funding rules, coverage, competition, and application process; an award is not guaranteed.",
            "This funding path matches your preference" + (" and tuition exceeds your budget." if financial.funding_dependent else "."),
            priority="high" if financial.funding_dependent else "medium")
    if student.extracurricular_interests:
        add("portfolio_activity", "Develop a project in your area of interest",
            "Choose a small project or activity related to: " + ", ".join(student.extracurricular_interests) + ".",
            "Optional exploration of your interests; this is not a stated admission requirement.", priority="low")
    return tasks


def get_next_action(roadmap: list[RoadmapTask], as_of: date | None = None) -> RoadmapTask | None:
    as_of = as_of or date.today()
    completed = {task.id for task in roadmap if task.status == "completed"}
    pending = [task for task in roadmap if task.status == "todo"
               and set(task.depends_on) <= completed
               and not (task.deadline_status == "verified" and task.deadline is not None and task.deadline < as_of)]

    def priority(task):
        upcoming = task.deadline is not None and task.deadline >= as_of and task.deadline_status == "verified"
        kind = (0 if task.id.startswith(("prepare_unt", "take_unt", "prepare_sat", "take_sat",
                                         "prepare_ielts", "take_ielts", "prepare_nuet", "take_nuet",
                                         "prepare_aet", "take_aet")) else
                1 if task.id in ("apply_state_grant", "apply_scholarship") else
                2 if task.id in ("prepare_documents", "apply_university") else 3)
        return (not task.blocking, not upcoming, task.deadline if upcoming else date.max, kind)

    return min(pending, key=priority) if pending else None
