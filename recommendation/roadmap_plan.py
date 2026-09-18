"""Validated, deduplicated roadmap assembled from verified facts and AI coaching."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date as Date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from recommendation.models import Model, Source, StudentProfile


StepStatus = Literal['todo', 'in_progress', 'completed', 'blocked', 'needs_verification']


class RoadmapPreferences(Model):
    timezone: str = 'Asia/Almaty'
    available_hours_per_week: float | None = Field(default=None, ge=.5, le=40)
    achievements: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError('Use a valid IANA timezone') from None
        return value

    @field_validator('achievements')
    @classmethod
    def clean_achievements(cls, values):
        result = []
        for item in values:
            clean = ' '.join(item.split())
            if not clean or len(clean) > 200:
                raise ValueError('Achievements must contain 1-200 characters')
            if clean not in result:
                result.append(clean)
        return result


class PlanDeadline(Model):
    date: Date | None = None
    kind: Literal['official', 'suggested', 'unknown', 'past'] = 'unknown'
    source: Source | None = None
    needs_verification: bool = True


class PlanStep(Model):
    id: str = Field(min_length=1, max_length=300)
    logical_key: str = Field(min_length=1, max_length=200)
    scope: Literal['common', 'university']
    university_ids: list[str] = Field(default_factory=list, max_length=20)
    program_ids: list[str] = Field(default_factory=list, max_length=20)
    title: str = Field(min_length=1, max_length=300)
    action: str = Field(min_length=1, max_length=1600)
    why: str = Field(min_length=1, max_length=1600)
    priority: Literal['high', 'medium', 'low']
    estimated_minutes: int = Field(ge=10, le=1200)
    deadline: PlanDeadline
    status: StepStatus = 'todo'
    depends_on: list[str] = Field(default_factory=list, max_length=40)
    details: list[str] = Field(default_factory=list, min_length=1, max_length=7)
    completion_criteria: str = Field(min_length=1, max_length=800)
    detail_level: Literal['detailed', 'standard', 'overview']
    basis: Literal['verified_requirement', 'personal_goal', 'planning_suggestion', 'unknown']
    sources: list[Source] = Field(default_factory=list, max_length=20)
    order: int = Field(ge=0)
    completed_at: datetime | None = None

    @model_validator(mode='after')
    def consistent_completion(self):
        if self.status == 'completed' and self.completed_at is None:
            self.completed_at = datetime.now(timezone.utc)
        if self.status != 'completed':
            self.completed_at = None
        return self


class RoadmapPlan(Model):
    schema_version: int = 1
    request_id: str
    input_hash: str
    generated_at: datetime
    as_of: Date
    timezone: str
    entry_year: int
    selected_program_ids: list[str]
    selected_university_ids: list[str]
    available_hours_per_week: float | None = None
    achievements_used: int = Field(ge=0)
    ai_model: str | None = None
    steps: list[PlanStep] = Field(max_length=240)
    next_action_id: str | None = None

    @model_validator(mode='after')
    def valid_graph_and_dates(self):
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError('Roadmap step IDs must be unique')
        known = set(ids)
        positions = {step.id: step.order for step in self.steps}
        if sorted(positions.values()) != list(range(len(self.steps))):
            raise ValueError('Roadmap order must be contiguous')
        for step in self.steps:
            if step.id in step.depends_on or not set(step.depends_on) <= known:
                raise ValueError('Roadmap dependency is missing or self-referential')
            if any(positions[item] >= step.order for item in step.depends_on):
                raise ValueError('Roadmap dependencies must precede dependent steps')
            deadline = step.deadline
            if deadline.kind == 'official':
                if deadline.date is None or deadline.source is None or deadline.needs_verification:
                    raise ValueError('Official deadlines require a verified source')
                if deadline.date < self.as_of:
                    raise ValueError('Past deadlines cannot be presented as future official deadlines')
            elif deadline.kind == 'past':
                if deadline.date is None or deadline.date >= self.as_of:
                    raise ValueError('Past deadline status is inconsistent')
                if step.status not in ('blocked', 'completed'):
                    raise ValueError('A missed deadline must block an unfinished step')
            if deadline.date is not None and deadline.kind != 'past':
                for dependency in step.depends_on:
                    parent = self.steps[positions[dependency]]
                    if parent.deadline.date is not None and parent.deadline.date > deadline.date:
                        raise ValueError('Dependency is scheduled after its dependent step')
        if self.next_action_id is not None and self.next_action_id not in known:
            raise ValueError('Next action is missing')
        return self


COMMON_BASES = {'choose_direction', 'prepare_documents', 'apply_state_grant', 'portfolio_activity'}
DURATION = {
    'select_programs': 60, 'verify_requirements': 45, 'verify_deadlines': 30,
    'choose_direction': 90, 'prepare_documents': 180, 'apply_university': 120,
    'apply_state_grant': 120, 'apply_scholarship': 120, 'portfolio_activity': 360,
}


def local_today(preferences: RoadmapPreferences) -> Date:
    return datetime.now(ZoneInfo(preferences.timezone)).date()


def _base(task_id: str) -> str:
    base, separator, suffix = task_id.rpartition(':')
    return base if separator and re.fullmatch('[0-9a-f]{10}', suffix) else task_id


def _common(base: str) -> bool:
    return (base in COMMON_BASES or base.startswith('goal_') or
            base.startswith('prepare_') or base.startswith('take_'))


def _unique(values):
    return list(dict.fromkeys(value for value in values if value is not None))


def _source_key(source):
    return (source.get('url'), source.get('verifiedAt'), source.get('academicYear'))


def _duration(base: str) -> int:
    if base.startswith('goal_'):
        return 75 if base.endswith(('_diagnostic', '_practice')) else 30
    if base.startswith('prepare_'):
        return 300
    if base.startswith('take_'):
        return 240
    return DURATION.get(base, 60)


def _priority(values):
    order = {'high': 0, 'medium': 1, 'low': 2}
    return min(values, key=lambda item: order[item])


def _suggested_date(base, today, entry_year, state, duration, weekly_hours, index):
    goals = ((state or {}).get('profile') or {}).get('examGoals', {})
    exam = next((name for name in ('SAT', 'IELTS', 'NUET', 'UNT') if name.lower() in base.lower()), None)
    target = goals.get(exam, {}).get('targetDate') if exam else None
    if target:
        try:
            target_date = Date.fromisoformat(target)
        except (TypeError, ValueError):
            target_date = None
        if target_date:
            lead = 0 if base.startswith('take_') else 30 if base.endswith('_practice') else 60
            return max(today, target_date - timedelta(days=lead))
    fixed = {
        'select_programs': 7, 'verify_requirements': 7, 'verify_deadlines': 7,
        'choose_direction': 14, 'prepare_documents': 45, 'portfolio_activity': 90,
        'apply_state_grant': 60, 'apply_scholarship': 60,
    }
    if base.startswith('goal_'):
        days = 7 + 7 * ['diagnostic', 'scores', 'weak', 'practice'].index(base.rsplit('_', 1)[-1])
    elif base.startswith('prepare_'):
        days = 90
    elif base.startswith('take_'):
        days = 180
    else:
        days = fixed.get(base, 21 + index * 7)
    if weekly_hours:
        days = max(days, math.ceil(duration / (weekly_hours * 60) * 7))
    horizon = Date(entry_year, 1, 1) - timedelta(days=30)
    return min(today + timedelta(days=days), max(today, horizon))


def _topological(groups):
    pending = {key: set(value['depends']) for key, value in groups.items()}
    ordered = []
    while pending:
        ready = sorted((key for key, deps in pending.items() if not deps),
                       key=lambda key: groups[key]['first_order'])
        if not ready:
            raise ValueError('AI roadmap contains a dependency cycle')
        for key in ready:
            ordered.append(key)
            pending.pop(key)
        for deps in pending.values():
            deps.difference_update(ready)
    return ordered


def build_roadmap_plan(result: dict, profile: StudentProfile, state: dict | None,
                       preferences: RoadmapPreferences, progress: dict[str, dict],
                       selected_program_ids: list[str], request_id: str, input_hash: str,
                       ai_model: str | None, as_of: Date | None = None) -> RoadmapPlan:
    today = as_of or local_today(preferences)
    selected = set(selected_program_ids)
    recommendations = [item for item in result.get('recommendations', [])
                       if item.get('programId') in selected]
    coaching = {(program['program_id'], step['task_id']): step
                for program in result.get('coaching', {}).get('programs', [])
                for step in program.get('steps', [])}
    groups = {}
    task_group = {}
    sequence = 0
    for recommendation in recommendations:
        program_id = recommendation['programId']
        university_id = recommendation['universityId']
        for task in recommendation.get('roadmap', []):
            base = _base(task['id'])
            key = ('common', base) if _common(base) else ('university', program_id, base)
            group = groups.setdefault(key, {'tasks': [], 'program_ids': [], 'university_ids': [],
                                            'depends_raw': [], 'depends': set(), 'first_order': sequence})
            group['tasks'].append((program_id, task))
            group['program_ids'].append(program_id)
            group['university_ids'].append(university_id)
            group['depends_raw'].extend((program_id, item) for item in task.get('dependsOn', []))
            task_group[(program_id, task['id'])] = key
            sequence += 1
    if not recommendations:
        groups[('common', 'select_programs')] = {
            'tasks': [(None, {'id': 'select_programs', 'title': 'Выбрать программы для roadmap',
                      'description': 'Сравните программы и добавьте подходящие варианты в свой список.',
                      'reason': 'Требования и сроки можно включить в персональный план только после выбора программ.',
                      'priority': 'high', 'dependsOn': [], 'deadline': None,
                      'deadlineStatus': 'unknown', 'source': None,
                      'how': ['Откройте подбор университетов.', 'Сравните требования и официальные источники.',
                              'Сохраните до шести программ, которые хотите включить в план.'],
                      'completionCriteria': 'В список добавлена хотя бы одна программа.'})],
            'program_ids': [], 'university_ids': [], 'depends_raw': [], 'depends': set(), 'first_order': 0}
    for group_key, group in groups.items():
        for raw in group['depends_raw']:
            dependency = task_group.get(raw)
            if dependency is not None and dependency != group_key:
                group['depends'].add(dependency)
    ordered_keys = _topological(groups)
    key_to_id = {}
    for key in ordered_keys:
        group = groups[key]
        signature = sorted({task['id'] for _, task in group['tasks']})
        suffix = hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:12]
        key_to_id[key] = f'{key[0]}:{key[-1]}:{suffix}'
    steps = []
    for index, key in enumerate(ordered_keys):
        group = groups[key]
        values = [task for _, task in group['tasks']]
        base = key[-1]
        sources_by_key = {}
        for task in values:
            if task.get('source'):
                sources_by_key[_source_key(task['source'])] = task['source']
        sources = list(sources_by_key.values())
        ai_steps = [coaching.get((program_id, task['id'])) for program_id, task in group['tasks']]
        ai_steps = [item for item in ai_steps if item]
        details = _unique(action for item in ai_steps for action in item.get('how', []))
        if not details:
            details = _unique(action for task in values for action in task.get('how', []))
        if not details:
            details = [values[0]['description']]
        official = next((task for task in values if task.get('deadlineStatus') == 'verified'
                         and task.get('deadline') and task.get('source')), None)
        deadline_date = Date.fromisoformat(official['deadline']) if official else None
        deadline_kind = 'official' if official else 'suggested'
        deadline_source = official.get('source') if official else None
        completed = any(task.get('status') == 'completed' for task in values)
        persisted = progress.get(key_to_id[key])
        status = persisted['status'] if persisted else ('completed' if completed else 'todo')
        completed_at = persisted.get('completed_at') if persisted else None
        if deadline_date and deadline_date < today:
            deadline_kind = 'past'
            if status != 'completed':
                status = 'blocked'
        if deadline_date is None:
            deadline_date = _suggested_date(base, today, profile.entry_year, state,
                                            _duration(base), preferences.available_hours_per_week, index)
        needs_verification = deadline_kind not in ('official', 'past')
        if base in ('verify_requirements', 'verify_deadlines') and status == 'todo':
            status = 'needs_verification'
        why = ai_steps[0]['why'] if ai_steps else values[0]['reason']
        requirement_source = bool(sources) and (base.startswith(('prepare_', 'take_')) or
                                                 base in ('apply_university', 'prepare_documents'))
        basis = ('personal_goal' if base.startswith(('goal_', 'portfolio_activity', 'choose_direction'))
                 else 'verified_requirement' if requirement_source else
                 'unknown' if base.startswith('verify_') else 'planning_suggestion')
        step = PlanStep(
            id=key_to_id[key], logical_key=base, scope=key[0],
            university_ids=sorted(set(group['university_ids'])),
            program_ids=sorted(set(group['program_ids'])),
            title=values[0]['title'], action=values[0]['description'], why=why,
            priority=_priority([task.get('priority', 'medium') for task in values]),
            estimated_minutes=_duration(base),
            deadline=PlanDeadline(date=deadline_date, kind=deadline_kind,
                                  source=deadline_source, needs_verification=needs_verification),
            status=status, depends_on=[key_to_id[item] for item in sorted(
                group['depends'], key=ordered_keys.index)],
            details=details[:7], completion_criteria=values[0].get('completionCriteria') or
                    'Действие выполнено, а результат сохранён.', detail_level='standard',
            basis=basis, sources=sources, order=index, completed_at=completed_at)
        steps.append(step)
    # Move suggested prerequisites before fixed official deadlines, then enforce forward order.
    by_id = {step.id: step for step in steps}
    for step in reversed(steps):
        if step.deadline.kind == 'official' and step.deadline.date:
            for dependency in step.depends_on:
                parent = by_id[dependency]
                if parent.deadline.kind == 'suggested' and parent.deadline.date > step.deadline.date:
                    parent.deadline.date = max(today, step.deadline.date - timedelta(days=7))
    for step in steps:
        parents = [by_id[item] for item in step.depends_on]
        if parents and step.deadline.kind == 'suggested':
            latest = max((item.deadline.date for item in parents if item.deadline.date), default=today)
            step.deadline.date = max(step.deadline.date or today, latest)
        days = (step.deadline.date - today).days if step.deadline.date else 10_000
        step.detail_level = 'detailed' if days <= 90 else 'standard' if days <= 365 else 'overview'
        if step.detail_level == 'overview':
            step.details = step.details[:2]
        elif step.detail_level == 'standard':
            step.details = step.details[:4]
    completed_ids = {step.id for step in steps if step.status == 'completed'}
    next_step = next((step for step in steps if step.status != 'completed'
                      and set(step.depends_on) <= completed_ids and step.status != 'blocked'), None)
    return RoadmapPlan(
        request_id=request_id, input_hash=input_hash, generated_at=datetime.now(timezone.utc),
        as_of=today, timezone=preferences.timezone, entry_year=profile.entry_year,
        selected_program_ids=sorted(selected),
        selected_university_ids=sorted({item['universityId'] for item in recommendations}),
        available_hours_per_week=preferences.available_hours_per_week,
        achievements_used=len(preferences.achievements), ai_model=ai_model,
        steps=steps, next_action_id=next_step.id if next_step else None)


def change_step_status(plan_value: dict, step_id: str, status: Literal['todo', 'in_progress', 'completed']):
    plan = RoadmapPlan.model_validate(plan_value)
    by_id = {step.id: step for step in plan.steps}
    if step_id not in by_id:
        raise KeyError(step_id)
    step = by_id[step_id]
    if status == 'completed' and any(by_id[item].status != 'completed' for item in step.depends_on):
        raise ValueError('Complete prerequisite steps first')
    now = datetime.now(timezone.utc)
    step.status = status
    step.completed_at = now if status == 'completed' else None
    changed = {step.id: {'status': step.status, 'completed_at': step.completed_at}}
    if status != 'completed':
        reset = {step_id}
        for candidate in plan.steps:
            if set(candidate.depends_on) & reset and candidate.status == 'completed':
                candidate.status = 'todo'
                candidate.completed_at = None
                reset.add(candidate.id)
                changed[candidate.id] = {'status': 'todo', 'completed_at': None}
    completed = {item.id for item in plan.steps if item.status == 'completed'}
    next_step = next((item for item in plan.steps if item.status != 'completed'
                      and set(item.depends_on) <= completed and item.status != 'blocked'), None)
    plan.next_action_id = next_step.id if next_step else None
    return plan, changed
