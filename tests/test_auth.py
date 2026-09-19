import hashlib
import psycopg

import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def api(db_url):
    path = db_url
    with TestClient(create_app(path)) as client:
        yield client, path


USER = {"username": "Alice", "password": "strong-password-123"}


def test_registration_login_and_logout(api):
    client, path = api
    response = client.post("/auth/register", json=USER)
    assert response.status_code == 201
    assert response.json() == {"id": 1, "username": "alice"}
    assert client.post("/auth/register", json={**USER, "username": "alice"}).status_code == 409
    login = client.post("/auth/login", json=USER)
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).json() == response.json()
    with psycopg.connect(path) as db:
        stored_password = db.execute("SELECT password_hash FROM users").fetchone()[0]
        stored_token = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert USER["password"] not in stored_password
    assert stored_token == hashlib.sha256(token.encode()).hexdigest()
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_invalid_auth_and_expiration(api):
    client, path = api
    client.post("/auth/register", json=USER)
    assert client.post("/auth/login", json={**USER, "password": "wrong-password"}).status_code == 401
    assert client.post("/auth/login", json={**USER, "username": "nobody"}).status_code == 401
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
    token = client.post("/auth/login", json=USER).json()["access_token"]
    with psycopg.connect(path) as db:
        db.execute("UPDATE sessions SET expires_at = 0")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("payload", [
    {"username": "ab", "password": "long-password"},
    {"username": "alice", "password": "short"},
    {"username": "bad name", "password": "long-password"},
    {"username": "alice", "password": "x" * 129},
])
def test_validation(api, payload):
    client, _ = api
    assert client.post("/auth/register", json=payload).status_code == 422


def test_data_survives_restart(db_url):
    path = db_url
    with TestClient(create_app(path)) as client:
        client.post("/auth/register", json=USER)
        token = client.post("/auth/login", json=USER).json()["access_token"]
    with TestClient(create_app(path)) as client:
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_user_state_is_owned_versioned_and_persists(api):
    client, _ = api
    client.post('/auth/register', json=USER)
    token = client.post('/auth/login', json=USER).json()['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    empty = client.get('/user-state', headers=headers)
    assert empty.status_code == 200
    assert empty.json() == {
        'savedOptions': [], 'comparison': [], 'focus': None, 'activities': [],
        'completed': [], 'inProgress': [], 'theme': 'dark', 'revision': 0,
    }
    payload = {
        'savedOptions': [{'programId': 'kz-program', 'label': 'Priority'}],
        'comparison': ['kz-program'], 'focus': 'kz-program',
        'activities': [{'id': 'activity-1', 'templateId': None, 'category': 'Personal Projects',
                        'title': 'Проект', 'targetPeriod': 'Осень', 'status': 'planned'}],
        'completed': ['task-1'], 'inProgress': ['task-2'], 'theme': 'system', 'revision': 0,
    }
    saved = client.put('/user-state', headers=headers, json=payload)
    assert saved.status_code == 200
    assert saved.json() == {**payload, 'revision': 1}
    assert client.get('/user-state', headers=headers).json() == {**payload, 'revision': 1}
    assert client.put('/user-state', headers=headers, json=payload).status_code == 409
    assert client.get('/user-state').status_code == 401


@pytest.mark.parametrize('patch', [
    {'comparison': ['same', 'same']},
    {'completed': ['same'], 'inProgress': ['same']},
    {'theme': 'blue'},
    {'activities': [{'id': 'same', 'templateId': None, 'category': 'project',
                    'title': 'Один', 'targetPeriod': 'Сейчас', 'status': 'invalid'}]},
])
def test_user_state_rejects_invalid_payload(api, patch):
    client, _ = api
    client.post('/auth/register', json=USER)
    token = client.post('/auth/login', json=USER).json()['access_token']
    payload = {'savedOptions': [], 'comparison': [], 'focus': None, 'activities': [],
               'completed': [], 'inProgress': [], 'theme': 'dark', 'revision': 0, **patch}
    assert client.put('/user-state', headers={'Authorization': f'Bearer {token}'}, json=payload).status_code == 422
