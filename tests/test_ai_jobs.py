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
