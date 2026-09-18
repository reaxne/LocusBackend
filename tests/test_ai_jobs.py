import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from database import Database
from main import create_app
from recommendation.ai import Coaching, GemmaClient, build_context, compact_context
from recommendation.telemetry import Job, Cancelled, current_job
from test_ai import advice
from test_recommendations import MemoryRepository, program, student, recommend


def account(client, db, name='jobtest'):
    credentials = {'username':name,'password':'test-password-123'}
    client.post('/auth/register', json=credentials)
    token = client.post('/auth/login', json=credentials).json()['access_token']
    user = db.get_user(name)['id']
    db.save_survey(user, student().model_dump(mode='json', by_alias=True))
    return user, {'Authorization':'Bearer '+token}


def test_stream_stages_cache_and_safe_logs(db_url, monkeypatch, caplog):
    monkeypatch.setattr(GemmaClient, 'generate', lambda self, context: Coaching.model_validate(advice(context)))
    with TestClient(create_app(db_url, program_repository=MemoryRepository([program()]))) as client:
        db = Database(db_url)
        _, headers = account(client, db)
        request_id = str(uuid4())
        response = client.post('/ai/roadmap', json={}, headers={**headers,'Accept':'application/x-ndjson','X-Request-ID':request_id})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[0]['stage'] == 'preparing'
        assert any(e['type']=='baseline' for e in events)
        assert events[-1]['type']=='result'
        assert events[-1]['data']['ai']['status']=='generated'
        assert response.headers['x-request-id'] == request_id
        assert 'contextBytes' in caplog.text
        assert 'jobtest' not in caplog.text and 'test-password' not in caplog.text
        again = client.post('/ai/roadmap', json={}, headers=headers)
        assert again.json()['ai']['cached'] is True
        assert 'cache_hit' in caplog.text


def test_cancel_stops_worker_and_never_caches_late_result(db_url, monkeypatch):
    entered = threading.Event()
    def slow(self, context):
        entered.set()
        for _ in range(100):
            current_job.get().check()
            time.sleep(.02)
        raise AssertionError('Cancellation failed')
    monkeypatch.setattr(GemmaClient, 'generate', slow)
    with TestClient(create_app(db_url, program_repository=MemoryRepository([program()]))) as client:
        db = Database(db_url)
        user, headers = account(client, db)
        request_id = str(uuid4())
        with ThreadPoolExecutor() as pool:
            future = pool.submit(client.post, '/ai/roadmap', json={}, headers={**headers,'X-Request-ID':request_id})
            assert entered.wait(5)
            assert client.post(f'/ai/requests/{request_id}/cancel', headers=headers).status_code == 204
            assert future.result(timeout=5).status_code == 409
        with db._connect() as connection:
            assert not connection.execute('SELECT 1 FROM ai_results WHERE user_id=%s',(user,)).fetchone()
            assert connection.execute('SELECT stage FROM ai_requests WHERE id=%s',(request_id,)).fetchone()['stage']=='cancelled'
        monkeypatch.setattr(GemmaClient, 'generate', lambda self, context: Coaching.model_validate(advice(context)))
        assert client.post('/ai/roadmap', json={}, headers=headers).json()['ai']['status']=='generated'


def test_identical_generation_cannot_run_concurrently(db_url, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    def controlled(self, context):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(5)
        return Coaching.model_validate(advice(context))
    monkeypatch.setattr(GemmaClient, 'generate', controlled)
    with TestClient(create_app(db_url, program_repository=MemoryRepository([program()]))) as client:
        db = Database(db_url)
        _, headers = account(client, db, 'concurrentjob')
        with ThreadPoolExecutor() as pool:
            first = pool.submit(client.post, '/ai/roadmap', json={'programIds':['demo-robotics']},
                                headers={**headers,'X-Request-ID':str(uuid4())})
            assert entered.wait(5)
            duplicate = client.post('/ai/roadmap', json={'programIds':['demo-robotics']},
                                    headers={**headers,'X-Request-ID':str(uuid4())})
            assert duplicate.status_code == 429
            release.set()
            assert first.result(timeout=5).status_code == 200
        assert calls == 1


def test_cancel_before_start_and_owner_isolation(db_url):
    with TestClient(create_app(db_url, program_repository=MemoryRepository([program()]))) as client:
        db = Database(db_url)
        user, headers = account(client, db)
        _, other = account(client, db, 'anotherjob')
        job = Job(db, str(uuid4()), user, 'profile')
        job.register()
        client.post(f'/ai/requests/{job.id}/cancel', headers=other)
        job.check()  # Another account cannot cancel this operation.
        request_id = str(uuid4())
        client.post(f'/ai/requests/{request_id}/cancel', headers=headers)
        response = client.post('/ai/profile', json={}, headers={**headers,'X-Request-ID':request_id})
        assert response.status_code == 409


def test_debug_payloads_are_opt_in_and_redact_credentials(db_url, monkeypatch):
    db = Database(db_url)
    db.initialize()
    user = db.create_user('debuguser','not-a-real-password-hash')['id']
    job = Job(db,str(uuid4()),user,'profile')
    job.register()
    job.debug('prompt', {'password':'secret','safe':'private student answer'})
    with db._connect() as connection:
        assert connection.execute('SELECT count(*) AS n FROM ai_debug_payloads').fetchone()['n']==0
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.setenv('AI_DEBUG_PAYLOADS','true')
    job.debug('prompt', {'password':'secret','safe':'private student answer'})
    with db._connect() as connection:
        payload = connection.execute('SELECT payload FROM ai_debug_payloads').fetchone()['payload']
        assert payload['password']=='[REDACTED]'
        assert payload['safe']=='private student answer'


def test_compact_context_reduces_size_without_losing_requirements():
    context = build_context(student(),recommend(),None)
    for purpose in ('roadmap','recommendations','profile'):
        reduced = compact_context(context,purpose)
        assert len(json.dumps(reduced)) < len(json.dumps(context))
        assert reduced['student'] == context['student']
        assert reduced['results']['recommendations'][0]['requirements'] == context['results']['recommendations'][0]['requirements']
    assert context['results']['recommendations'][0]['roadmap']


def test_cancellation_closes_provider_request(monkeypatch):
    import asyncio
    import httpx
    from recommendation.free_ai import FreeAIClient
    monkeypatch.setenv('API_GEMMA','synthetic-test-key')
    stopped = threading.Event()
    entered = threading.Event()
    class LocalJob:
        def check(self):
            if entered.is_set(): raise Cancelled()
        def emit(self, *args, **kwargs): pass
        def debug(self, *args, **kwargs): pass
    async def handler(request):
        entered.set()
        try: await asyncio.sleep(30)
        finally: stopped.set()
    token = current_job.set(LocalJob())
    started = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            FreeAIClient(httpx.MockTransport(handler)).generate(build_context(student(),recommend(),None))
        assert stopped.is_set()
        assert time.monotonic()-started < 3
    finally:
        current_job.reset(token)


def test_persisted_roadmap_status_progress_and_owner_isolation(db_url, monkeypatch):
    monkeypatch.setattr(GemmaClient, 'generate', lambda self, context: Coaching.model_validate(advice(context)))
    with TestClient(create_app(db_url, program_repository=MemoryRepository([program()]))) as client:
        db = Database(db_url)
        user, headers = account(client, db, 'roadmapowner')
        _, other_headers = account(client, db, 'roadmapother')
        preferences = client.post('/roadmap/preferences', headers=headers, json={
            'timezone':'Asia/Almaty','availableHoursPerWeek':4,
            'achievements':['Robotics club finalist']})
        assert preferences.status_code == 200
        generated = client.post('/ai/roadmap', headers=headers,
                                json={'programIds':['demo-robotics']}).json()
        assert generated['ai']['status'] == 'generated'
        assert generated['roadmapPlan']['achievementsUsed'] == 1
        saved = client.get('/roadmap', headers=headers)
        assert saved.status_code == 200
        assert client.get('/roadmap', headers=other_headers).status_code == 404
        revision = saved.json()['revision']
        plan = saved.json()['plan']
        step_id = plan['nextActionId']
        updated = client.post(f'/roadmap/steps/{step_id}', headers=headers,
                              json={'revision':revision,'status':'completed'})
        assert updated.status_code == 200
        assert next(item for item in updated.json()['plan']['steps'] if item['id'] == step_id)['status'] == 'completed'
        assert client.post(f'/roadmap/steps/{step_id}', headers=other_headers,
                           json={'revision':revision,'status':'completed'}).status_code == 404
        with db._connect() as connection:
            assert connection.execute('SELECT user_id FROM roadmap_plans').fetchone()['user_id'] == user


def test_request_status_and_failed_regeneration_preserves_plan(db_url, monkeypatch):
    monkeypatch.setattr(GemmaClient, 'generate', lambda self, context: Coaching.model_validate(advice(context)))
    with TestClient(create_app(db_url, program_repository=MemoryRepository([program()]))) as client:
        db = Database(db_url)
        _, headers = account(client, db, 'preserveplan')
        request_id = str(uuid4())
        first = client.post('/ai/roadmap', headers={**headers,'X-Request-ID':request_id},
                            json={'programIds':['demo-robotics']})
        assert first.status_code == 200
        assert client.get(f'/ai/requests/{request_id}', headers=headers).json()['stage'] == 'completed'
        original = client.get('/roadmap', headers=headers).json()
        from recommendation.ai import AIUnavailable
        import httpx
        from recommendation.free_ai import FreeAIClient
        monkeypatch.setenv('API_GEMMA', 'test')
        monkeypatch.setenv('AI_FREE_MODELS', 'test:free')
        monkeypatch.delenv('AI_MODEL', raising=False)
        invalid = httpx.MockTransport(lambda request: httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"steps": []}'}}]}))
        monkeypatch.setattr(GemmaClient, 'generate',
                            lambda self, context: FreeAIClient(invalid).generate(context))
        changed = student(interest=['Cybersecurity']).model_dump(mode='json', by_alias=True)
        db.save_survey(db.get_user('preserveplan')['id'], changed)
        with db._connect() as connection:
            connection.execute("UPDATE ai_results SET updated_at=now()-interval '21 seconds'")
        failed = client.post('/ai/roadmap', headers=headers,
                             json={'programIds':['demo-robotics']})
        assert failed.status_code == 503
        assert failed.json()['detail']['code'] == 'invalid_response'
        assert failed.json()['detail']['retryable'] is True
        assert failed.json()['detail']['previousPlanPreserved'] is True
        failed_request_id = failed.json()['detail']['requestId']
        assert client.get(f'/ai/requests/{failed_request_id}', headers=headers).json()['stage'] == 'failed'
        after = client.get('/roadmap', headers=headers).json()
        assert after['revision'] == original['revision']
        assert after['plan'] == original['plan']
        monkeypatch.setattr(GemmaClient, 'generate', lambda self, context: Coaching.model_validate(advice(context)))
        retried = client.post('/ai/roadmap', headers=headers,
                              json={'programIds':['demo-robotics']})
        assert retried.status_code == 200
        assert retried.json()['ai']['status'] == 'generated'


def test_streamed_ai_failure_ends_with_error_and_failed_job(db_url, monkeypatch):
    from recommendation.ai import AIUnavailable
    monkeypatch.setattr(GemmaClient, 'generate',
                        lambda self, context: (_ for _ in ()).throw(AIUnavailable('all_free_models_failed')))
    with TestClient(create_app(db_url, program_repository=MemoryRepository([program()]))) as client:
        db = Database(db_url)
        _, headers = account(client, db, 'streamfailure')
        request_id = str(uuid4())
        response = client.post('/ai/roadmap', json={'programIds':['demo-robotics']},
            headers={**headers,'Accept':'application/x-ndjson','X-Request-ID':request_id})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert any(item['type'] == 'baseline' for item in events)
        assert events[-1] == {
            'type':'error','status':503,'requestId':request_id,
            'code':'all_free_models_failed',
            'message':'AI models did not return a valid response. Retry the request.',
            'retryable':True,'previousPlanPreserved':False,'fallbackAvailable':True,
            'attempts':[],
        }
        assert not any(item.get('stage') == 'completed' for item in events)
        state = client.get(f'/ai/requests/{request_id}', headers=headers).json()
        assert state['stage'] == 'failed'
