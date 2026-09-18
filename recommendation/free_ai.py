"""Bounded free-only OpenRouter failover; no credentials or provider bodies in diagnostics."""
import json
import asyncio
import os
import re
import time
from typing import Callable

import httpx
from pydantic import Field
from recommendation.ai import AIUnavailable, Coaching, StrictModel
from recommendation.prompts import PROMPTS

DEFAULT_MODELS = ('google/gemma-4-26b-a4b-it:free', 'qwen/qwen3.8-27b:free',
                  'deepseek/deepseek-v4-flash-0731:free', 'openrouter/free')
TOTAL_TIMEOUT = 60
ATTEMPT_TIMEOUT = 45


def model_chain():
    raw = os.getenv('AI_FREE_MODELS', ','.join(DEFAULT_MODELS))
    models = list(dict.fromkeys(value.strip() for value in raw.split(',') if value.strip()))
    if not models or len(models) > 4 or any(not (m.endswith(':free') or m == 'openrouter/free') for m in models):
        raise AIUnavailable('invalid_free_model_configuration')
    return models


class ProfileInsight(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    evidence: str = Field(min_length=1, max_length=1200)
    evidence_fields: list[str] = Field(min_length=1, max_length=8)
    why: str = Field(min_length=1, max_length=1200)
    actions: list[str] = Field(min_length=3, max_length=7)


class ProfileAnalysis(StrictModel):
    strengths: list[ProfileInsight] = Field(max_length=5)
    weaknesses: list[ProfileInsight] = Field(max_length=5)
    unknowns: list[str] = Field(max_length=10)


def russian(text):
    if not isinstance(text, str) or not re.search('[а-яА-ЯёЁ]', text) or len(text) > 1800 or '://' in text:
        raise ValueError('Invalid Russian text')


def validate_result(result, context, purpose):
    if purpose == 'profile':
        for insight in [*result.strengths, *result.weaknesses]:
            for field in insight.evidence_fields:
                value = context
                for part in field.split('.'):
                    if not isinstance(value, dict) or part not in value:
                        raise ValueError('Unknown evidence field')
                    value = value[part]
                if value is None or value == '' or value == [] or value == {}:
                    raise ValueError('Missing evidence')
            for text in [insight.title, insight.evidence, insight.why, *insight.actions]:
                russian(text)
        for text in result.unknowns:
            russian(text)
        if not result.strengths and not result.weaknesses and not result.unknowns:
            raise ValueError('Empty analysis')
        return
    expected = {p['programId']: {t['id'] for t in p['roadmap']} if purpose == 'roadmap' else set()
                for p in context['results']['recommendations']}
    if len(result.programs) != len(expected) or {p.program_id for p in result.programs} != set(expected):
        raise ValueError('Unexpected programs')
    for program in result.programs:
        russian(program.explanation)
        if len(program.steps) != len(expected[program.program_id]) or {s.task_id for s in program.steps} != expected[program.program_id]:
            raise ValueError('Unexpected tasks')
        for step in program.steps:
            for text in [step.why, *step.how]:
                russian(text)


class FreeAIClient:
    def __init__(self, transport=None, clock: Callable = time.monotonic):
        self.transport = transport
        self.clock = clock
        self.attempts = []
        self.model = None

    def generate(self, context, purpose='roadmap'):
        return asyncio.run(self._generate(context, purpose))

    async def _generate(self, context, purpose):
        key = os.getenv('API_GEMMA', '').strip()
        if not key:
            raise AIUnavailable('not_configured')
        models = model_chain()
        schema = ProfileAnalysis if purpose == 'profile' else Coaching
        output_schema = schema.model_json_schema()
        if purpose == 'profile':
            def fields(value, prefix=''):
                found = []
                if isinstance(value, dict):
                    for name, item in value.items():
                        path = f'{prefix}.{name}' if prefix else name
                        if isinstance(item, dict): found.extend(fields(item, path))
                        elif item is not None and item != '' and item != []: found.append(path)
                return found
            output_schema['$defs']['ProfileInsight']['properties']['evidence_fields']['items']['enum'] = fields(context)
        started = self.clock()
        for index, model in enumerate(models):
            remaining = TOTAL_TIMEOUT - (self.clock() - started)
            if remaining <= 1:
                break
            remaining_models = len(models) - index
            reserve = min(5, remaining / remaining_models) * (remaining_models - 1)
            budget = min(ATTEMPT_TIMEOUT, remaining - reserve)
            reason = 'invalid_response'
            try:
                async with asyncio.timeout(budget):
                    async with httpx.AsyncClient(timeout=httpx.Timeout(budget, connect=min(5, budget)), transport=self.transport) as client:
                        response = await client.post('https://openrouter.ai/api/v1/chat/completions',
                            headers={'Authorization': f'Bearer {key}'},
                            json={'model': model, 'temperature': 0.2, 'max_tokens': 5000 if purpose == 'profile' else 12000,
                                  'provider': {'max_price': {'prompt': 0, 'completion': 0}},
                                  'messages': [{'role': 'system', 'content': PROMPTS[purpose]},
                                               {'role': 'user', 'content': json.dumps({'schema': output_schema, 'context': context}, ensure_ascii=False)}]})
                if response.status_code == 401:
                    self.attempts.append({'model': model, 'status': 'authentication_error'})
                    raise AIUnavailable('authentication_error')
                if response.status_code != 200:
                    reason = 'rate_limited' if response.status_code == 429 else 'provider_error'
                    raise ValueError('Provider unavailable')
                payload = response.json()
                choice = payload['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    reason = 'incomplete_response'
                    raise ValueError('Incomplete output')
                content = choice['message']['content'].strip()
                if content.startswith('```json') and content.endswith('```'):
                    content = content[7:-3].strip()
                result = schema.model_validate_json(content)
                validate_result(result, context, purpose)
                self.model = payload.get('model') or model
                self.attempts.append({'model': model, 'status': 'generated'})
                return result
            except (httpx.TimeoutException, TimeoutError):
                reason = 'timeout'
            except httpx.HTTPError:
                reason = 'network_error'
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                pass
            self.attempts.append({'model': model, 'status': reason})
        raise AIUnavailable('all_free_models_unavailable')
