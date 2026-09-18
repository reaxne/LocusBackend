"""Reuse validated advice by stable task ID, requesting only changed tasks."""
from copy import deepcopy


def delta_context(context, previous):
    request = deepcopy(context)
    existing = {}
    previous_tasks = {(program['programId'], task['id']): task
                      for program in (previous or {}).get('recommendations', []) for task in program['roadmap']}
    current_tasks = {(program['programId'], task['id']): task
                     for program in context['results']['recommendations'] for task in program['roadmap']}
    content = lambda task: {key: task.get(key) for key in ('title', 'description', 'reason', 'how', 'timing', 'target')}
    for program in (previous or {}).get('coaching', {}).get('programs', []):
        for step in program['steps']:
            key = (program['program_id'], step['task_id'])
            if key in previous_tasks and key in current_tasks and content(previous_tasks[key]) == content(current_tasks[key]):
                existing[key] = step
    for program in request['results']['recommendations']:
        program['roadmap'] = [task for task in program['roadmap']
                              if (program['programId'], task['id']) not in existing]
        program['nextAction'] = None
    return request, existing


def merge_advice(context, generated, existing):
    count = 0
    for program in generated['programs']:
        new_steps = {step['task_id']: step for step in program['steps']}
        tasks = next(p['roadmap'] for p in context['results']['recommendations'] if p['programId'] == program['program_id'])
        steps = []
        for task in tasks:
            key = (program['program_id'], task['id'])
            if key in existing:
                steps.append(existing[key])
                count += 1
            else:
                steps.append(new_steps[task['id']])
        program['steps'] = steps
    return generated, count
