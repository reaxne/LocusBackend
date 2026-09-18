import json

import httpx
import pytest
from fastapi.testclient import TestClient

from main import create_app
from recommendation.ai import AIUnavailable, GemmaClient, MODEL, build_context, cache_key
from test_recommendations import MemoryRepository, program, student, recommend


def advice(context):
    return {'programs': [{'program_id': p['programId'], 'explanation': 'Изучите программу с учётом ваших интересов.',
                         'steps': [{'task_id': t['id'], 'why': 'Этот шаг помогает подготовиться к поступлению.',
                                    'how': ['Откройте официальный источник.', 'Проверьте условия своего набора.', 'Запишите результат проверки.'], 'suggested_timing': 'This week'}
                                   for t in p['roadmap']]}
                        for p in context['results']['recommendations']]}


def test_client_uses_exact_free_model_and_validates_response(monkeypatch):
    monkeypatch.setenv('API_GEMMA', 'test-key')
    context = build_context(student(), recommend(), None)
    def handler(request):
        body = json.loads(request.content)
        assert body['model'] == MODEL
        assert request.headers['authorization'] == 'Bearer test-key'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(advice(context))}}]})
    result = GemmaClient(httpx.MockTransport(handler)).generate(context)
    assert result.programs[0].steps


@pytest.mark.parametrize('mode', ['unknown_id', 'malformed', 'truncated', 'rate_limit', 'timeout'])
def test_provider_failures_are_safe(monkeypatch, mode):
    monkeypatch.setenv('API_GEMMA', 'secret-must-not-leak')
    context = build_context(student(), recommend(), None)
    def handler(request):
        if mode == 'timeout':
            raise httpx.ReadTimeout('secret-must-not-leak', request=request)
        if mode == 'rate_limit':
            return httpx.Response(429, text='secret-must-not-leak')
        output = advice(context)
        output['programs'][0]['program_id'] = 'invented'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if mode == 'truncated' else 'stop',
            'message': {'content': 'broken' if mode == 'malformed' else json.dumps(output)}}]})
    with pytest.raises(AIUnavailable) as exc:
        GemmaClient(httpx.MockTransport(handler)).generate(context)
    assert 'secret' not in str(exc.value)


def test_context_allowlist_and_cache_changes():
    baseline = recommend()
    state = {'profile': {'password': 'private', 'examGoals': {'IELTS': {'targetScore': 7}}}}
    context = build_context(student(), baseline, state)
    assert 'private' not in json.dumps(context)
    assert cache_key(context) != cache_key(build_context(student(grade=10), baseline, state))
    assert cache_key(context) != cache_key(build_context(student(), baseline, None))


def test_ai_auth_saved_profile_cache_and_regeneration(db_url, monkeypatch):
    from recommendation.ai import Coaching
    from database import Database
    calls = []
    def fake(self, context):
        calls.append(context)
        return Coaching.model_validate(advice(context))
    monkeypatch.setattr(GemmaClient, 'generate', fake)
    app = create_app(db_url, program_repository=MemoryRepository([program()]))
    with TestClient(app) as client:
        assert client.post('/ai/roadmap', json={}).status_code == 401
        credentials = {'username': 'aitest', 'password': 'long-password'}
        client.post('/auth/register', json=credentials)
        token = client.post('/auth/login', json=credentials).json()['access_token']
        headers = {'Authorization': 'Bearer ' + token}
        assert client.post('/ai/roadmap', json={}, headers=headers).status_code == 404
        db = Database(db_url)
        user_id = db.get_user('aitest')['id']
        db.save_survey(user_id, student().model_dump(mode='json', by_alias=True))
        first = client.post('/ai/roadmap', json={}, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()['ai']['status'] == 'generated'
        again = client.post('/ai/roadmap', json={}, headers=headers).json()
        assert again['ai']['cached'] is True
        assert len(calls) == 1
        assert client.post('/ai/roadmap', json={'programIds': ['invented']}, headers=headers).status_code == 422
        db.save_survey(user_id, student(grade=10).model_dump(mode='json', by_alias=True))
        assert client.post('/ai/roadmap', json={}, headers=headers).status_code == 429
        with db._connect() as connection:
            connection.execute("UPDATE ai_results SET updated_at=now()-interval '30 seconds'")
        refreshed = client.post('/ai/roadmap', json={}, headers=headers).json()
        assert refreshed['ai']['cached'] is False
        assert calls[-1]['student']['grade'] == 10
        assert len(calls) == 2
        assert refreshed['ai']['reusedSteps'] > 0
        assert len(calls[-1]['results']['recommendations'][0]['roadmap']) < len(refreshed['recommendations'][0]['roadmap'])
        other = {'username': 'otherstudent', 'password': 'long-password'}
        client.post('/auth/register', json=other)
        other_token = client.post('/auth/login', json=other).json()['access_token']
        assert client.post('/ai/roadmap', json={}, headers={'Authorization': 'Bearer ' + other_token}).status_code == 404
        def unavailable(self, context):
            raise AIUnavailable('rate_limited')
        monkeypatch.setattr(GemmaClient, 'generate', unavailable)
        db.save_survey(user_id, student(grade=9).model_dump(mode='json', by_alias=True))
        with db._connect() as connection:
            connection.execute("UPDATE ai_results SET updated_at=now()-interval '30 seconds'")
        fallback = client.post('/ai/roadmap', json={}, headers=headers)
        assert fallback.status_code == 503
        assert fallback.json()['detail']['code'] == 'rate_limited'
        assert fallback.json()['detail']['retryable'] is True
        assert fallback.json()['detail']['fallbackAvailable'] is True


def test_missing_key(monkeypatch):
    monkeypatch.delenv('API_GEMMA', raising=False)
    with pytest.raises(AIUnavailable, match='not_configured'):
        GemmaClient().generate({})


def test_purpose_caches_and_profile_endpoint_are_isolated(db_url, monkeypatch):
    from recommendation.ai import Coaching
    from recommendation.free_ai import ProfileAnalysis
    from database import Database
    purposes = []
    def fake(self, context):
        purpose = context['_purpose']
        purposes.append(purpose)
        if purpose == 'profile':
            return ProfileAnalysis(strengths=[], weaknesses=[], unknowns=['Нужно уточнить текущие навыки.'])
        data = advice(context)
        if purpose == 'recommendations':
            for program in data['programs']: program['steps'] = []
        return Coaching.model_validate(data)
    monkeypatch.setattr(GemmaClient, 'generate', fake)
    with TestClient(create_app(db_url, program_repository=MemoryRepository([program()]))) as client:
        credentials = {'username': 'analysis_test', 'password': 'long-password'}
        client.post('/auth/register', json=credentials)
        token = client.post('/auth/login', json=credentials).json()['access_token']
        headers = {'Authorization': 'Bearer ' + token}
        db = Database(db_url)
        db.save_survey(db.get_user('analysis_test')['id'], student().model_dump(mode='json', by_alias=True))
        for purpose in ('roadmap', 'recommendations', 'profile'):
            result = client.post('/ai/' + purpose, json={}, headers=headers)
            assert result.status_code == 200
            assert result.json()['ai']['purpose'] == purpose
            assert client.post('/ai/' + purpose, json={}, headers=headers).json()['ai']['cached']
        assert purposes == ['roadmap', 'recommendations', 'profile']
        assert result.json()['analysis']['unknowns']
        assert client.post('/ai/profile', json={}, headers={**headers, 'X-Locus-User': '999999'}).status_code == 409
