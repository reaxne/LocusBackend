import json
from copy import deepcopy

import httpx
import pytest

from recommendation.ai import AIUnavailable, build_context
from recommendation.free_ai import FreeAIClient, model_chain
from test_ai import wire_advice
from test_free_ai import response
from test_recommendations import student, recommend


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv('API_GEMMA', 'test')
    monkeypatch.setenv('AI_FREE_MODELS', 'test:free')
    monkeypatch.delenv('AI_MODEL', raising=False)


def test_batches_preserve_identity_and_strict_schema():
    context = build_context(student(), recommend(), None)
    program = context['results']['recommendations'][0]
    template = program['roadmap'][0]
    program['roadmap'] = [{**deepcopy(template), 'id': f'task-{i}'} for i in range(14)]
    sizes = []
    def handler(request):
        body = json.loads(request.content)
        schema = body['response_format']['json_schema']['schema']
        assert set(schema['properties']) == {'steps'}
        step_schema = schema['$defs']['RoadmapOutputStep']
        assert set(step_schema['required']) == set(step_schema['properties']) == {
            'title', 'action', 'why', 'how', 'priority', 'duration', 'deadline', 'source', 'dependsOn'}
        assert step_schema['additionalProperties'] is False
        output = wire_advice(body)
        sizes.append(len(output['steps']))
        return response(output)
    result = FreeAIClient(httpx.MockTransport(handler)).generate(context)
    assert sizes == [6, 6, 2]
    assert [step.task_id for step in result.programs[0].steps] == [f'task-{i}' for i in range(14)]


@pytest.mark.parametrize('mode', ['markdown', 'repair', 'bad_repair', 'changed_fact', 'extra', 'coercion'])
def test_validation_and_single_repair(mode):
    context = build_context(student(), recommend(), None)
    context['results']['recommendations'][0]['roadmap'] = context['results']['recommendations'][0]['roadmap'][:1]
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        output = wire_advice(body)
        if mode == 'markdown':
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                'message': {'content': 'Here is the JSON:\n```JSON\n' + json.dumps(output) + '\n```'}}]})
        if len(calls) == 2:
            assert 'Do not add new facts' in body['messages'][-1]['content']
            if mode == 'repair':
                return response(output)
        if mode == 'changed_fact':
            output['steps'][0]['deadline'] = '2099-01-01'
        elif mode == 'extra':
            output['steps'][0]['invented'] = True
        elif mode == 'coercion':
            output['steps'][0]['duration'] = '60'
        else:
            output = {'steps': []}
        return response(output)
    client = FreeAIClient(httpx.MockTransport(handler))
    if mode in ('markdown', 'repair'):
        assert client.generate(context).programs[0].steps
    else:
        with pytest.raises(AIUnavailable, match='invalid_response'):
            client.generate(context)
    assert len(calls) == (1 if mode == 'markdown' else 2)


def test_paid_primary_and_free_fallback(monkeypatch):
    monkeypatch.setenv('AI_MODEL', 'vendor/paid')
    assert model_chain() == ['vendor/paid', 'test:free']
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body['model'])
        assert body['provider']['require_parameters'] is True
        if body['model'] == 'vendor/paid':
            assert 'max_price' not in body['provider']
            return httpx.Response(503)
        assert body['provider']['max_price'] == {'prompt': 0, 'completion': 0}
        return response(wire_advice(body))
    assert FreeAIClient(httpx.MockTransport(handler)).generate(build_context(student(), recommend(), None))
    assert calls[:2] == ['vendor/paid', 'test:free']
