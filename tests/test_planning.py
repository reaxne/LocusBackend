from recommendation.planning import prepare_plan
from test_recommendations import recommend, student


def state(target=7, date='2030-05-01'):
    return {'profile': {'examGoals': {'IELTS': {'targetScore': target, 'targetDate': date}}}}


def test_russian_steps_and_unknown_deadlines():
    response = prepare_plan(recommend(), student(), state())
    tasks = response.recommendations[0].roadmap
    assert len([t for t in tasks if t.id.startswith('goal_IELTS')]) == 4
    assert all(len(t.how) >= 3 for t in tasks)
    assert all(t.deadline is None for t in tasks if t.id.startswith('goal_'))
    assert all('Личная целевая дата' in t.timing for t in tasks if t.id.startswith('goal_'))


def test_exam_changes_invalidate_only_related_tasks():
    before = prepare_plan(recommend(), student(), state()).recommendations[0].roadmap
    after = prepare_plan(recommend(), student(), state(8)).recommendations[0].roadmap
    before_ids = {t.id.split(':')[0]: t.id for t in before}
    after_ids = {t.id.split(':')[0]: t.id for t in after}
    assert before_ids['goal_IELTS_diagnostic'] == after_ids['goal_IELTS_diagnostic']
    assert before_ids['goal_IELTS_scores'] == after_ids['goal_IELTS_scores']
    assert before_ids['goal_IELTS_weak'] != after_ids['goal_IELTS_weak']
    assert before_ids['goal_IELTS_practice'] != after_ids['goal_IELTS_practice']
    assert {t.id for t in before if 'ielts' not in t.id.lower()} == {t.id for t in after if 'ielts' not in t.id.lower()}
    done = prepare_plan(recommend(student(IELTS=8)), student(IELTS=8), state(8))
    assert not any(t.id.startswith('goal_IELTS') for t in done.recommendations[0].roadmap)


def test_target_date_only_changes_practice_and_related_exam_preparation():
    before = {t.id.split(':')[0]: t.id for t in prepare_plan(recommend(), student(), state()).recommendations[0].roadmap}
    after = {t.id.split(':')[0]: t.id for t in prepare_plan(recommend(), student(), state(date='2030-06-01')).recommendations[0].roadmap}
    assert {key for key in before if before[key] != after[key]} == {'goal_IELTS_practice', 'prepare_ielts'}


def test_budget_does_not_reset_diagnostics_documents_or_ielts():
    before = prepare_plan(recommend(), student(), state()).recommendations[0].roadmap
    after = prepare_plan(recommend(student(budget=3500000)), student(budget=3500000), state()).recommendations[0].roadmap
    unchanged = lambda tasks: {t.id for t in tasks if t.id.startswith(('goal_', 'prepare_', 'verify_'))}
    assert unchanged(before) == unchanged(after)
