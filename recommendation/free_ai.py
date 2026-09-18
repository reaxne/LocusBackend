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
from recommendation.telemetry import current_job, emit, check

DEFAULT_MODELS = ('nex-agi/nex-n2.5-mini:free',
                  'deepseek/deepseek-v4-flash-0731:free',
                  'nvidia/nemotron-3-super-120b-a12b:free', 'openrouter/free')
TOTAL_TIMEOUT = 60
ATTEMPT_TIMEOUT = 18
MIN_ATTEMPT_TIMEOUT = 10
TRANSITION_RESERVE = 0.5


def attempt_budget(remaining: float, attempts_left: int) -> float | None:
    """Give every remaining model a useful window instead of starving fallbacks."""
    if attempts_left < 1:
        return None
    usable = remaining - TRANSITION_RESERVE * (attempts_left - 1)
    budget = min(ATTEMPT_TIMEOUT, usable / attempts_left)
    return budget if budget >= MIN_ATTEMPT_TIMEOUT else None


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


def grounded_text(text, context):
    """Reject new calendar dates and admission guarantees in generated prose."""
    source = json.dumps(context, ensure_ascii=False)
    for value in re.findall(r'\b(?:20\d{2}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.20\d{2})\b', text):
        normalized = value if '-' in value else '-'.join(reversed(value.split('.')))
        if normalized not in source:
            raise ValueError('Ungrounded calendar date')
    lowered = text.lower()
    if any(value in lowered for value in ('гарантирует поступление', 'гарантия поступления',
                                           'точно поступите', 'точно поступишь')):
        raise ValueError('Admission guarantee')


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
                grounded_text(text, context)


class FreeAIClient:
    def __init__(self, transport=None, clock: Callable = time.monotonic):
        self.transport = transport
        self.clock = clock
        self.attempts = []
        self.model = None

    def generate(self, context, purpose='roadmap'):
        return asyncio.run(self._cancellable(context, purpose))

    async def _cancellable(self, context, purpose):
        job = current_job.get()
        if not job:
            return await self._generate(context, purpose)
        async def monitor():
            while True:
                await asyncio.to_thread(job.check)
                await asyncio.sleep(0.25)
        generation = asyncio.create_task(self._generate(context, purpose))
        cancellation = asyncio.create_task(monitor())
        try:
            done, _ = await asyncio.wait([generation, cancellation], return_when=asyncio.FIRST_COMPLETED)
            if cancellation in done: await cancellation
            return await generation
        finally:
            generation.cancel()
            cancellation.cancel()
            await asyncio.gather(generation, cancellation, return_exceptions=True)

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
        output_tokens = 2200 if purpose == 'profile' else 12000
        started = self.clock()
        for index, model in enumerate(models):
            check()
            remaining = TOTAL_TIMEOUT - (self.clock() - started)
            remaining_models = len(models) - index
            budget = attempt_budget(remaining, remaining_models)
            if budget is None:
                self.attempts.append({'model': model, 'status': 'skipped_insufficient_time'})
                emit('budget_exhausted', model=model, attempt=index + 1,
                     remainingMs=max(0, round(remaining * 1000)),
                     minimumAttemptMs=round(MIN_ATTEMPT_TIMEOUT * 1000))
                break
            attempt_started = self.clock()
            allocation = round(budget * 1000)
            emit('model', model=model, attempt=index + 1, allocatedMs=allocation,
                 remainingMs=max(0, round(remaining * 1000)), totalBudgetMs=TOTAL_TIMEOUT * 1000)
            reason = 'invalid_response'
            try:
                job = current_job.get()
                if job: job.debug('prompt', {'model':model,'instructions':PROMPTS[purpose],'schema':output_schema,'context':context})
                async with asyncio.timeout(budget):
                    async with httpx.AsyncClient(timeout=httpx.Timeout(
                            budget, connect=min(5, budget), read=budget,
                            write=min(10, budget), pool=min(5, budget)), transport=self.transport) as client:
                        response = await client.post('https://openrouter.ai/api/v1/chat/completions',
                            headers={'Authorization': f'Bearer {key}'},
                            json={'model': model, 'temperature': 0.2, 'max_tokens': output_tokens,
                                  'reasoning': {'enabled': False},
                                  'response_format': {'type': 'json_schema', 'json_schema': {
                                      'name': 'profile_analysis' if purpose == 'profile' else 'admission_coaching',
                                      'strict': True, 'schema': output_schema}},
                                  'provider': {'max_price': {'prompt': 0, 'completion': 0},
                                               'require_parameters': True},
                                  'messages': [{'role': 'system', 'content': PROMPTS[purpose]},
                                               {'role': 'user', 'content': json.dumps({'context': context}, ensure_ascii=False)}]})
                if response.status_code == 401:
                    self.attempts.append({'model': model, 'status': 'authentication_error'})
                    emit('model_failed', model=model, reason='authentication_error',
                         allocatedMs=allocation,
                         remainingMs=max(0, round((TOTAL_TIMEOUT-(self.clock()-started))*1000)),
                         durationMs=round((self.clock()-attempt_started)*1000),
                         failureScope='configuration')
                    raise AIUnavailable('authentication_error')
                if response.status_code != 200:
                    reason = ('rate_limited' if response.status_code == 429 else
                              'provider_timeout' if response.status_code in (408, 504) else
                              'provider_unavailable' if response.status_code >= 500 else
                              'provider_rejected')
                    raise ValueError('Provider unavailable')
                payload = response.json()
                usage = payload.get('usage') or {}
                emit('validating', model=model, durationMs=round((self.clock()-attempt_started)*1000),
                     tokens={key:usage[key] for key in ('prompt_tokens','completion_tokens','total_tokens') if isinstance(usage.get(key),int)})
                if job: job.debug('response', payload)
                choice = payload['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    reason = 'incomplete_response'
                    raise ValueError('Incomplete output')
                content = choice['message']['content'].strip()
                if not content:
                    reason = 'empty_response'
                    raise ValueError('Empty output')
                if content.startswith('```json') and content.endswith('```'):
                    content = content[7:-3].strip()
                result = schema.model_validate_json(content)
                validate_result(result, context, purpose)
                self.model = payload.get('model') or model
                self.attempts.append({'model': model, 'status': 'generated'})
                emit('validated', model=self.model, status='generated')
                return result
            except (httpx.TimeoutException, TimeoutError):
                reason = 'attempt_timeout'
            except httpx.HTTPError:
                reason = 'network_error'
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                pass
            self.attempts.append({'model': model, 'status': reason})
            emit('model_failed', model=model, reason=reason, allocatedMs=allocation,
                 remainingMs=max(0, round((TOTAL_TIMEOUT-(self.clock()-started))*1000)),
                 durationMs=round((self.clock()-attempt_started)*1000), failureScope='attempt')
        remaining = TOTAL_TIMEOUT - (self.clock() - started)
        reason = 'overall_timeout_exhausted' if remaining < MIN_ATTEMPT_TIMEOUT else 'all_free_models_failed'
        emit('generation_failed', reason=reason, remainingMs=max(0, round(remaining*1000)),
             attempted=len([item for item in self.attempts if not item['status'].startswith('skipped_')]))
        raise AIUnavailable(reason)
