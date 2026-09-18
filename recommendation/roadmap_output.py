"""Small AI wire format; task identity and all planning facts remain server-owned."""
from typing import Literal

from pydantic import ConfigDict, Field

from recommendation.ai import StrictModel
from recommendation.roadmap_plan import _base, _duration


class RoadmapOutputStep(StrictModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    title: str = Field(min_length=1, max_length=300)
    action: str = Field(min_length=1, max_length=1600)
    why: str = Field(min_length=1, max_length=1200)
    how: list[str] = Field(min_length=3, max_length=7)
    priority: Literal['high', 'medium', 'low']
    duration: int = Field(ge=10, le=1200, description='Server-estimated minutes')
    deadline: str | None
    source: str | None = Field(description='Exact server-provided source URL, or null')
    dependsOn: list[str]


class RoadmapOutput(StrictModel):
    steps: list[RoadmapOutputStep] = Field(min_length=1, max_length=6)


def wire_step(task):
    return {
        'title': task['title'], 'action': task['description'],
        'why': task['reason'], 'how': task.get('how', []),
        'priority': task['priority'], 'duration': _duration(_base(task['id'])),
        'deadline': task.get('deadline'),
        'source': (task.get('source') or {}).get('url'),
        'dependsOn': task.get('dependsOn', []),
    }


def validate_roadmap(result, context):
    from recommendation.free_ai import grounded_text, russian

    expected = context['steps']
    if len(result.steps) != len(expected):
        raise ValueError('Unexpected step count')
    for actual, original in zip(result.steps, expected):
        values = actual.model_dump()
        if any(values[key] != original[key] for key in original if key not in ('why', 'how')):
            raise ValueError('Server-owned roadmap fields changed')
        for text in [actual.why, *actual.how]:
            russian(text)
            grounded_text(text, context)
