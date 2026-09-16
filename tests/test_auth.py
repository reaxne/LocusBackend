import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def api(tmp_path):
    path = tmp_path / "auth.db"
    with TestClient(create_app(path)) as client:
        yield client, path


USER = {"username": "Alice", "password": "strong-password-123"}


def test_registration_login_and_logout(api):
    client, path = api
    response = client.post("/register", json=USER)
    assert response.status_code == 201
    assert response.json() == {"id": 1, "username": "alice"}
    assert client.post("/register", json={**USER, "username": "alice"}).status_code == 409
    login = client.post("/login", json=USER)
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/me", headers=headers).json() == response.json()
    with sqlite3.connect(path) as db:
        stored_password = db.execute("SELECT password_hash FROM users").fetchone()[0]
        stored_token = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert USER["password"] not in stored_password
    assert stored_token == hashlib.sha256(token.encode()).hexdigest()
    assert client.post("/logout", headers=headers).status_code == 204
    assert client.get("/me", headers=headers).status_code == 401


def test_invalid_auth_and_expiration(api):
    client, path = api
    client.post("/register", json=USER)
    assert client.post("/login", json={**USER, "password": "wrong-password"}).status_code == 401
    assert client.post("/login", json={**USER, "username": "nobody"}).status_code == 401
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
    token = client.post("/login", json=USER).json()["access_token"]
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sessions SET expires_at = 0")
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("payload", [
    {"username": "ab", "password": "long-password"},
    {"username": "alice", "password": "short"},
    {"username": "bad name", "password": "long-password"},
    {"username": "alice", "password": "x" * 129},
])
def test_validation(api, payload):
    client, _ = api
    assert client.post("/register", json=payload).status_code == 422


def test_data_survives_restart(tmp_path):
    path = tmp_path / "persist.db"
    with TestClient(create_app(path)) as client:
        client.post("/register", json=USER)
        token = client.post("/login", json=USER).json()["access_token"]
    with TestClient(create_app(path)) as client:
        assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
