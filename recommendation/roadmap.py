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
        add("verify_requirements", "Уточнить требования поступления",
            "Запросите у университета полный маршрут поступления и сроки для вашего года набора.",
            "Информация о поступлении отсутствует или не подтверждена.", blocking=True, priority="high")
        unverified_tasks.append("verify_requirements")
    if not any(item.task_type == "apply_university" and item.verified(student.entry_year, as_of)
               and program.admission_year == student.entry_year
               and item.route_id in (None, eligibility.best_admission_route) for item in program.deadlines):
        add("verify_deadlines", "Проверить срок подачи заявления",
            "Проверьте официальный календарь приёма до планирования экзаменов и подачи заявления.",
            "Для выбранного маршрута нет подтверждённого срока подачи заявления.", priority="high")
        unverified_tasks.append("verify_deadlines")
    if student.grade <= 10 or not student.interest:
        add("choose_direction", "Изучить направление обучения",
            "Сравните учебный план и будущие профессии со своими интересами до выбора программы.",
            f"Вы планируете поступление после {student.grade} класса в {student.entry_year} году.")
    exam_task_ids = []
    if route:
        for requirement in route.requirements:
            if requirement.status == "unknown":
                continue
            exam = requirement.exam
            done = requirement.status == "passed"
            reason = f"По выбранному маршруту требуется результат {exam} не ниже {requirement.required_value:g}."
            prep_id, exam_id = f"prepare_{exam.lower()}", f"take_{exam.lower()}"
            status = "completed" if done else "todo"
            add(prep_id, f"Подготовиться к {exam}",
                "Изучите формат экзамена и составьте план подготовки к требуемому результату.", reason,
                status=status, priority="high", target=requirement.required_value, blocking=not done,
                source=requirement.source)
            add(exam_id, f"Сдать {exam}" if requirement.status != "failed" else f"Пересдать {exam}",
                "Проверьте доступность экзамена и передайте в университет подходящий результат.", reason,
                status=status, priority="high", target=requirement.required_value, blocking=not done,
                depends_on=[prep_id], source=requirement.source)
            exam_task_ids.append(exam_id)
    add("prepare_documents", "Подготовить документы для поступления",
        "Получите официальный список документов и подготовьте запрошенные материалы.",
        "Перед подачей заявления нужно подготовить документы.")
    add("apply_university", "Подать заявление в университет",
        "Уточните текущий порядок подачи и отправьте заявление через официальный канал приёма.",
        f"Подайте заявление на программу для поступления в {student.entry_year} году.",
        depends_on=["prepare_documents", *exam_task_ids, *unverified_tasks])
    for option in financial.funding_options:
        task_id = "apply_state_grant" if option == "state_grant" else "apply_scholarship"
        add(task_id, "Подать заявку на государственный грант" if option == "state_grant" else "Подать заявку на стипендию",
            "Проверьте правила финансирования, покрытие, конкурс и порядок подачи; получение выплаты не гарантировано.",
            "Этот вариант финансирования соответствует вашим предпочтениям" + (" и стоимость обучения выше вашего бюджета." if financial.funding_dependent else "."),
            priority="high" if financial.funding_dependent else "medium")
    if student.extracurricular_interests:
        add("portfolio_activity", "Сделать проект по интересующему направлению",
            "Выберите небольшой проект или занятие, связанное с вашими интересами.",
            "Это дополнительный способ развить интересы, а не подтверждённое требование поступления.", priority="low")
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
