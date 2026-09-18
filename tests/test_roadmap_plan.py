from datetime import date

import pytest

from recommendation.models import Deadline
from recommendation.planning import prepare_plan
from recommendation.roadmap_plan import (
    RoadmapPreferences, build_roadmap_plan, change_step_status,
)
from test_recommendations import AS_OF, MemoryRepository, SOURCE, program, student
from recommendation.recommender import Recommender


def make_result(profile, programs):
    response = Recommender(MemoryRepository(programs)).recommend(
        profile, limit=6, program_ids=[item.id for item in programs], as_of=AS_OF)
    return prepare_plan(response, profile, None, AS_OF).model_dump(mode='json', by_alias=True)


def make_plan(result, profile, selected, progress=None):
    return build_roadmap_plan(
        result, profile, None, RoadmapPreferences(availableHoursPerWeek=4), progress or {},
        selected, '00000000-0000-0000-0000-000000000001', 'input-hash', 'test-model', AS_OF)


def test_profile_without_selected_universities_gets_one_selection_action():
    profile = student()
    result = make_result(profile, [program()])
    plan = make_plan(result, profile, [])
    assert [step.logical_key for step in plan.steps] == ['select_programs']
    assert plan.steps[0].scope == 'common'
    assert plan.next_action_id == plan.steps[0].id


def test_multiple_universities_deduplicate_common_work_and_keep_official_deadlines():
    profile = student(SAT=None, IELTS=None)
    first = program(deadlines=[Deadline(taskType='apply_university', date=date(2029, 8, 1), source=SOURCE)])
    second = program(id='demo-cs', university_id='demo-cs-university',
                     university_name='Second Demo University', name='Computer Science',
                     deadlines=[Deadline(taskType='apply_university', date=date(2029, 9, 1), source=SOURCE)])
    result = make_result(profile, [first, second])
    plan = make_plan(result, profile, [first.id, second.id])
    keys = [step.logical_key for step in plan.steps]
    assert keys.count('prepare_sat') == 1
    assert keys.count('prepare_documents') == 1
    applications = [step for step in plan.steps if step.logical_key == 'apply_university']
    assert len(applications) == 2
    assert {step.deadline.date for step in applications} == {date(2029, 8, 1), date(2029, 9, 1)}
    assert all(step.deadline.kind == 'official' and step.deadline.source for step in applications)
    positions = {step.id: step.order for step in plan.steps}
    assert all(positions[parent] < step.order for step in plan.steps for parent in step.depends_on)


def test_regeneration_preserves_matching_completion_and_drops_irrelevant_progress():
    profile = student(SAT=None, IELTS=None)
    item = program()
    initial = make_plan(make_result(profile, [item]), profile, [item.id])
    first = initial.steps[0]
    regenerated = make_plan(make_result(profile, [item]), profile, [item.id], {
        first.id: {'status': 'completed', 'completed_at': '2029-01-02T00:00:00Z'},
        'removed-step': {'status': 'completed', 'completed_at': '2029-01-02T00:00:00Z'},
    })
    assert next(step for step in regenerated.steps if step.id == first.id).status == 'completed'
    assert all(step.id != 'removed-step' for step in regenerated.steps)


def test_completion_requires_dependencies_and_reopening_resets_completed_dependents():
    profile = student(SAT=None, IELTS=None)
    plan = make_plan(make_result(profile, [program()]), profile, ['demo-robotics'])
    dependent = next(step for step in plan.steps if step.depends_on)
    with pytest.raises(ValueError, match='prerequisite'):
        change_step_status(plan.model_dump(mode='json', by_alias=True), dependent.id, 'completed')
    parent_id = dependent.depends_on[0]
    changed, _ = change_step_status(plan.model_dump(mode='json', by_alias=True), parent_id, 'completed')
    # Complete every dependency before the target.
    for dependency in dependent.depends_on[1:]:
        changed, _ = change_step_status(changed.model_dump(mode='json', by_alias=True), dependency, 'completed')
    changed, _ = change_step_status(changed.model_dump(mode='json', by_alias=True), dependent.id, 'completed')
    changed, updates = change_step_status(changed.model_dump(mode='json', by_alias=True), parent_id, 'todo')
    assert next(step for step in changed.steps if step.id == dependent.id).status == 'todo'
    assert dependent.id in updates


def test_past_official_deadline_is_blocked_and_never_future():
    profile = student(SAT=None, IELTS=None)
    item = program(deadlines=[Deadline(taskType='apply_university', date=date(2028, 12, 1), source=SOURCE)])
    # Directly build from a past-deadline result because matching correctly excludes an expired route.
    result = make_result(profile, [program()])
    application = next(task for task in result['recommendations'][0]['roadmap']
                       if task['id'].startswith('apply_university'))
    application.update(deadline='2028-12-01', deadlineStatus='verified',
                       source=SOURCE.model_dump(mode='json', by_alias=True))
    plan = make_plan(result, profile, [item.id])
    step = next(step for step in plan.steps if step.logical_key == 'apply_university')
    assert step.deadline.kind == 'past'
    assert step.status == 'blocked'
    assert plan.next_action_id != step.id
