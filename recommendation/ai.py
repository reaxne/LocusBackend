"""OpenRouter coaching over authoritative, deterministic admission results."""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from recommendation.prompts import VERSION

MODEL = "google/gemma-4-26b-a4b-it:free"
PROMPT_VERSION = VERSION


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoachingStep(StrictModel):
    task_id: str = Field(min_length=1, max_length=200)
    why: str = Field(min_length=1, max_length=1200)
    how: list[str] = Field(min_length=3, max_length=7)
    suggested_timing: Literal["Now", "This week", "After prerequisites"]


class ProgramAdvice(StrictModel):
    program_id: str
    explanation: str = Field(min_length=1, max_length=1600)
    steps: list[CoachingStep] = Field(max_length=60)


class Coaching(StrictModel):
    programs: list[ProgramAdvice] = Field(max_length=6)


class AIUnavailable(Exception):
    """Safe reason code; provider bodies and credentials must never be exposed."""


class GemmaClient:
    def __init__(self, transport=None):
        self.transport = transport
        self.model = None
        self.attempts = []

    def generate(self, context: dict):
        # Compatibility entry point for existing integrations/tests.
        from recommendation.free_ai import FreeAIClient
        client = FreeAIClient(self.transport)
        try:
            return client.generate(context, context.get('_purpose', 'roadmap'))
        finally:
            self.model, self.attempts = client.model, client.attempts


def build_context(profile, baseline, state):
    # Deliberate allowlist: no account identity, password, token, or unrelated state.
    saved = (state or {}).get("profile") or {}
    return {"student": profile.model_dump(mode="json", by_alias=True),
            "planning": {key: saved[key] for key in ("examGoals", "ieltsSectionScores", "exams", "academicPerformance", "studyLanguage") if key in saved},
            "results": baseline.model_dump(mode="json", by_alias=True)}


def cache_key(context):
    from recommendation.free_ai import model_chain
    return hashlib.sha256(json.dumps([model_chain(), PROMPT_VERSION, context], sort_keys=True).encode()).hexdigest()


def compact_context(context, purpose):
    """Remove duplicate summaries and irrelevant tasks, preserving verified facts."""
    import copy
    result = copy.deepcopy(context)
    results = result.get('results', {})
    results.pop('excludedPrograms', None)
    results.pop('studentSummary', None)
    for program in results.get('recommendations', []):
        for key in ('nextAction', 'missingRequirements', 'weights', 'scores'):
            program.pop(key, None)
        if purpose != 'roadmap':
            program['roadmap'] = []
        if purpose == 'profile':
            for key in list(program):
                if key not in ('programId','program','university','requirements','warnings'):
                    del program[key]
            program['roadmap'] = []
    return result
