import json
import asyncio
import httpx
import pytest
from recommendation.free_ai import FreeAIClient, ProfileAnalysis, model_chain
from recommendation.ai import AIUnavailable, build_context
from recommendation.prompts import PROMPTS
from test_ai import advice
from test_recommendations import recommend, student


def response(data):
    return httpx.Response(200, json={'model': 'working:free', 'choices': [
        {'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}]})


@pytest.mark.parametrize('failure', ['429', '503', '403', 'timeout', 'bad_json', 'english'])
def test_falls_back_after_unusable_response(monkeypatch, failure):
    monkeypatch.setenv('API_GEMMA', 'secret')
    monkeypatch.setenv('AI_FREE_MODELS', 'first:free,second:free')
    context = build_context(student(), recommend(), None)
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body['model'])
        assert body['provider']['max_price'] == {'prompt': 0, 'completion': 0}
        if len(calls) == 1:
            if failure.isdigit(): return httpx.Response(int(failure))
            if failure == 'timeout': raise httpx.ReadTimeout('private', request=request)
            if failure == 'bad_json': return httpx.Response(200, json={'unexpected': True})
            invalid = advice(context)
            invalid['programs'][0]['explanation'] = 'English only'
            return response(invalid)
        return response(advice(context))
    client = FreeAIClient(httpx.MockTransport(handler))
    assert client.generate(context).programs
    assert calls == ['first:free', 'second:free']
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
        client.generate({})
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
        assert json.loads(request.content)['messages'][0]['content'] == PROMPTS['profile']
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
    monkeypatch.setattr(module, 'ATTEMPT_TIMEOUT', 0.02)
    context = build_context(student(), recommend(), None)
    async def handler(request):
        if json.loads(request.content)['model'] == 'slow:free':
            await asyncio.sleep(1)
        return response(advice(context))
    client = FreeAIClient(httpx.MockTransport(handler))
    assert client.generate(context).programs
    assert client.attempts[0]['status'] == 'timeout'
    assert client.attempts[1]['status'] == 'generated'
