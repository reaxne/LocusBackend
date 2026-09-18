import json
import asyncio
import httpx
import pytest
from recommendation.free_ai import FreeAIClient, ProfileAnalysis, attempt_budget, model_chain
from recommendation.ai import AIUnavailable, build_context
from recommendation.prompts import PROMPTS
from test_ai import advice, wire_advice
from test_recommendations import recommend, student


def response(data):
    return httpx.Response(200, json={'model': 'working:free', 'choices': [
        {'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}]})


@pytest.mark.parametrize('failure', ['429', '503', '403', 'timeout', 'bad_json', 'english',
                                      'invented_date', 'guarantee'])
def test_falls_back_after_unusable_response(monkeypatch, failure):
    monkeypatch.setenv('API_GEMMA', 'secret')
    monkeypatch.setenv('AI_FREE_MODELS', 'first:free,second:free')
    context = build_context(student(), recommend(), None)
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body['model'])
        assert body['provider']['max_price'] == {'prompt': 0, 'completion': 0}
        assert body['provider']['require_parameters'] is True
        assert body['reasoning'] == {'enabled': False}
        assert body['response_format']['type'] == 'json_schema'
        assert body['response_format']['json_schema']['strict'] is True
        assert set(body['messages'][1]) == {'role', 'content'}
        assert set(json.loads(body['messages'][1]['content'])) == {'context'}
        if len(calls) == 1:
            if failure.isdigit(): return httpx.Response(int(failure))
            if failure == 'timeout': raise httpx.ReadTimeout('private', request=request)
            if failure == 'bad_json': return httpx.Response(200, json={'unexpected': True})
            invalid = wire_advice(body)
            if failure == 'english':
                invalid['steps'][0]['why'] = 'English only'
            elif failure == 'invented_date':
                invalid['steps'][0]['how'][0] = 'Подайте документы до 2099-12-31.'
            else:
                invalid['steps'][0]['why'] = 'Это гарантирует поступление.'
            return response(invalid)
        return response(wire_advice(json.loads(request.content)))
    client = FreeAIClient(httpx.MockTransport(handler))
    assert client.generate(context).programs
    assert calls[:2] == ['first:free', 'second:free' if failure in ('429', '503', '403', 'timeout') else 'first:free']
    assert client.model == 'working:free'
    assert client.attempts[-1]['status'] == 'generated'


def test_rejects_paid_chain_before_request(monkeypatch):
    monkeypatch.setenv('AI_FREE_MODELS', 'google/paid-model')
    with pytest.raises(AIUnavailable, match='invalid_free_model_configuration'):
        model_chain()


def test_invalid_key_stops_without_leaking_secrets(monkeypatch):
    monkeypatch.setenv('API_GEMMA', 'secret')
    client = FreeAIClient(httpx.MockTransport(lambda request: httpx.Response(401, text='secret')))
    with pytest.raises(AIUnavailable, match='authentication_error'):
        client.generate({}, 'profile')
    assert len(client.attempts) == 1


def test_profile_analysis_validates_evidence_and_separate_prompt(monkeypatch):
    monkeypatch.setenv('API_GEMMA', 'secret')
    monkeypatch.setenv('AI_FREE_MODELS', 'first:free,second:free')
    context = build_context(student(), recommend(), None)
    insight = {'title': 'Интерес к программированию', 'evidence': 'В анкете указано программирование.',
               'evidence_fields': ['student.interest'], 'why': 'Это основа для выбора учебного проекта.',
               'actions': ['Выберите учебную задачу.', 'Создайте прототип.', 'Запишите выводы.']}
    calls = []
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body['messages'][0]['content'] == PROMPTS['profile']
        assert body['max_tokens'] == 2200
        assert body['response_format']['json_schema']['name'] == 'profile_analysis'
        item = {**insight, 'evidence_fields': ['student.invented']} if len(calls) == 1 else insight
        return response({'strengths': [item], 'weaknesses': [], 'unknowns': []})
    result = FreeAIClient(httpx.MockTransport(handler)).generate(context, 'profile')
    assert isinstance(result, ProfileAnalysis)
    assert len(calls) == 2


def test_recommendations_do_not_request_roadmap_steps(monkeypatch):
    monkeypatch.setenv('API_GEMMA', 'secret')
    context = build_context(student(), recommend(), None)
    output = advice(context)
    for p in output['programs']: p['steps'] = []
    client = FreeAIClient(httpx.MockTransport(lambda request: response(output)))
    assert client.generate(context, 'recommendations').programs[0].steps == []


def test_wall_clock_timeout_cancels_slow_model_and_tries_next(monkeypatch):
    import recommendation.free_ai as module
    monkeypatch.setenv('API_GEMMA', 'secret')
    monkeypatch.setenv('AI_FREE_MODELS', 'slow:free,fast:free')
    monkeypatch.setattr(module, 'TOTAL_TIMEOUT', 0.12)
    monkeypatch.setattr(module, 'ATTEMPT_TIMEOUT', 0.05)
    monkeypatch.setattr(module, 'MIN_ATTEMPT_TIMEOUT', 0.02)
    monkeypatch.setattr(module, 'TRANSITION_RESERVE', 0.001)
    context = build_context(student(), recommend(), None)
    # Exercise one batch: later batches share the same overall time budget.
    context['results']['recommendations'][0]['roadmap'] = context['results']['recommendations'][0]['roadmap'][:1]
    async def handler(request):
        if json.loads(request.content)['model'] == 'slow:free':
            await asyncio.sleep(1)
        return response(wire_advice(json.loads(request.content)))
    client = FreeAIClient(httpx.MockTransport(handler))
    assert client.generate(context).programs
    assert client.attempts[0]['status'] == 'attempt_timeout'
    assert client.attempts[1]['status'] == 'generated'


def test_default_budget_does_not_starve_fallback_models():
    allocations = []
    remaining = 60.0
    for attempts_left in (4, 3, 2, 1):
        budget = attempt_budget(remaining, attempts_left)
        assert budget is not None
        allocations.append(budget)
        remaining -= budget + (0.5 if attempts_left > 1 else 0)
    assert min(allocations) >= 10
    assert max(allocations) <= 18
    assert allocations[0] < 20
