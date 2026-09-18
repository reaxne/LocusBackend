"""Russian planning guidance. Suggested preparation periods are never admission deadlines."""
import hashlib
import json
from recommendation.models import RoadmapTask
from recommendation.roadmap import get_next_action

GUIDES = {
    'verify_requirements': ('Уточнить требования поступления', 'Правила могут меняться для каждого набора.', ['Откройте официальный сайт программы и выберите свой год поступления.', 'Запишите доступные маршруты поступления и необходимые экзамены.', 'Если сведения неполные, отправьте приёмной комиссии вопрос и сохраните ответ.']),
    'verify_deadlines': ('Проверить сроки подачи', 'Подтверждённого срока для вашего набора пока нет.', ['Найдите календарь приёма на официальном сайте.', 'Проверьте, к какому году и маршруту относится дата.', 'Запишите ссылку и подтверждённый срок; при отсутствии даты уточните её у приёмной комиссии.']),
    'choose_direction': ('Изучить направление обучения', 'Учебная программа должна соответствовать вашим интересам.', ['Откройте учебный план и выпишите три интересных предмета.', 'Посмотрите, какие задачи решают выпускники.', 'Попробуйте небольшое учебное задание по выбранному направлению и запишите впечатления.']),
    'prepare_documents': ('Подготовить документы', 'Точный список документов необходимо получить у университета.', ['Найдите официальный список для своей категории абитуриента.', 'Составьте таблицу: документ, где получить, что уже готово.', 'Подготовьте требуемые копии и проверьте формат файлов и написание имени.']),
    'apply_university': ('Подать заявление в университет', 'Заявление подаётся по правилам выбранного набора.', ['Убедитесь, что требования и срок подтверждены, а документы готовы.', 'Откройте официальный кабинет поступающего и проверьте заполненные поля.', 'Отправьте заявление и сохраните подтверждение; проверьте статус в кабинете.']),
    'apply_state_grant': ('Проверить возможность участия в конкурсе грантов', 'Участие в конкурсе не гарантирует получение гранта.', ['Уточните правила и ограничения для своей категории.', 'Проверьте официальный список документов и сроки конкурса.', 'Подайте заявку, если соответствуете условиям, и сохраните подтверждение.']),
    'apply_scholarship': ('Подготовить заявку на стипендию', 'Условия финансирования проверяются отдельно от поступления.', ['Найдите официальные условия стипендии.', 'Уточните покрытие расходов, критерии и документы.', 'Подготовьте заявку и проверьте срок по официальному источнику.']),
    'portfolio_activity': ('Выполнить небольшой проект по интересам', 'Это идея для развития навыков, а не подтверждённое требование поступления.', ['Выберите небольшую проблему и опишите ожидаемый результат.', 'Разделите работу на три части и запланируйте время на первую.', 'Создайте рабочую версию, соберите обратную связь и опишите свой вклад и выводы.']),
}


def prepare_plan(response, profile, state):
    saved = (state or {}).get('profile') or {}
    # A change of exam score/goal invalidates corresponding manual completion IDs.
    exam_data = {name: {'score': getattr(profile, name),
                       'goal': saved.get('examGoals', {}).get(name),
                       'status': saved.get('exams', {}).get(name),
                       'sections': saved.get('ieltsSectionScores') if name == 'IELTS' else None}
                 for name in ('SAT', 'IELTS', 'NUET', 'UNT', 'AET')}
    for match in response.recommendations:
        for task in match.roadmap:
            if task.source is None:
                task.source = next(iter(match.sources.values()), None)
            if task.id in GUIDES:
                task.title, task.reason, task.how = GUIDES[task.id]
                task.description = task.how[0]
                task.completion_criteria = 'Все действия шага выполнены, результат и ссылки сохранены.'
            elif task.id.startswith(('prepare_', 'take_')):
                exam = task.id.split('_', 1)[1].upper()
                if exam in exam_data:
                    task.title = f'Подготовиться к {exam}' if task.id.startswith('prepare_') else f'Сдать {exam} и записать результат'
                    task.reason = f'По выбранному маршруту подтверждён необходимый результат: {task.target}.'
                    task.how = (['Пройдите пробный вариант с таймером.', 'Запишите общий балл и результаты по разделам.', 'Выберите самый слабый раздел и запланируйте три занятия.', 'После занятий повторите пробный вариант и сравните результаты.']
                                if task.id.startswith('prepare_') else ['Проверьте официальный формат экзамена и доступные даты.', 'Зарегистрируйтесь, когда готовы, и сохраните подтверждение.', 'После экзамена внесите результат в раздел «Экзамены».'])
                    task.description = task.how[0]
                    task.completion_criteria = f'Результат подготовки записан; ориентир по маршруту: {task.target}.'
        # Personal exam goals are preparation advice, not universal university requirements.
        personal = []
        for exam, goal in saved.get('examGoals', {}).items():
            target = goal.get('targetScore')
            planned = saved.get('exams', {}).get(exam, {}).get('status') == 'planned'
            score = getattr(profile, exam, None)
            if (target is None and not planned) or (target is not None and score is not None and score >= target):
                continue
            for suffix, title, how in [
                ('diagnostic', 'Пройти пробный тест', ['Найдите пробный тест у организатора экзамена.', 'Выполните его с таймером без подсказок.', 'Сохраните результат и список заданий, которые вызвали трудности.']),
                ('scores', 'Записать результаты по разделам', ['Разберите ошибки в пробном варианте.', 'Запишите баллы каждого раздела; для IELTS используйте поля в разделе «Экзамены».', 'Выделите темы с наибольшим количеством ошибок.']),
                ('weak', 'Выбрать слабый раздел', ['Сравните результаты разделов со своей целью.', 'Выберите один раздел для ближайшей недели.', 'Выпишите два типа заданий для практики.']),
                ('practice', 'Запланировать практику', ['Выделите три занятия по 30–45 минут.', 'На каждом занятии решайте задания выбранного типа и разбирайте ошибки.', 'В конце недели повторите пробный раздел и скорректируйте план.']),
            ]:
                personal.append(RoadmapTask(id=f'goal_{exam}_{suffix}', title=f'{exam}: {title}', description=how[0], how=how,
                    reason=f'Ваша личная цель: {target if target is not None else "подготовиться к экзамену"}. Это не требование всех вузов.',
                    timing=f'Личная целевая дата: {goal["targetDate"]}' if goal.get('targetDate') else 'В ближайшую неделю · рекомендация',
                    completion_criteria='Действия выполнены, результат записан.'))
        match.roadmap = personal + match.roadmap
        mapping = {}
        for task in match.roadmap:
            relevant = next((exam for exam in exam_data if exam.lower() in task.id.lower()), None)
            signature = task_dependencies(task, profile, saved, exam_data.get(relevant), match)
            version = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:10]
            mapping[task.id] = f'{task.id}:{version}'
        for task in match.roadmap:
            task.id = mapping.get(task.id, task.id)
            task.depends_on = [mapping.get(item, item) for item in task.depends_on]
        match.next_action = get_next_action(match.roadmap)
    return response


def task_dependencies(task, profile, saved, exam, match):
    """Only changes that alter a task's expected result invalidate its completion."""
    base = {'target': task.target, 'deadline': str(task.deadline),
            'source': str(task.source.url) if task.source else None}
    if task.id.startswith('goal_'):
        # Changing a target date doesn't undo an already-taken diagnostic test.
        goal = (exam or {}).get('goal') or {}
        base = {'examScore': (exam or {}).get('score')}
        if task.id.endswith(('_weak', '_practice')):
            base.update(target=goal.get('targetScore'), sections=(exam or {}).get('sections'))
        if task.id.endswith('_practice'):
            base['targetDate'] = goal.get('targetDate')
        return base
    if exam is not None:
        base.update(score=exam.get('score'), status=exam.get('status'))
        if task.id.startswith('prepare_'):
            base.update(goal=exam.get('goal'), sections=exam.get('sections'))
        return base
    if task.id in ('choose_direction', 'portfolio_activity'):
        base.update(interest=profile.interest)
        if task.id == 'portfolio_activity':
            base.update(categories=profile.extracurricular_interests,
                        strengths=profile.academic_strengths, grade=profile.grade)
        return base
    base.update(entryYear=profile.entry_year, category=profile.category)
    if task.id in ('verify_requirements', 'prepare_documents', 'apply_university'):
        base['route'] = match.eligibility.best_admission_route
        base['requirements'] = [{ 'exam': r.exam, 'required': r.required_value,
                                 'source': str(r.source.url) if r.source else None }
                               for route in match.eligibility.available_routes for r in route.requirements]
    if task.id in ('apply_state_grant', 'apply_scholarship'):
        base.update(funding=profile.funding, budget=profile.budget)
    return base
