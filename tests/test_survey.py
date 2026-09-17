import sqlite3

import pytest
from fastapi.testclient import TestClient

from main import create_app


ANSWERS = {
    "grade": 11,
    "entryYear": "2027",
    "interest": ["engineering", "science"],
    "city": "Алматы",
    "mustStay": False,
    "budget": {"amount": 2000000, "currency": "KZT"},
    "funding": ["scholarship"],
    "category": "university",
    "academicStrengths": ["math", "physics"],
    "SAT": 1400,
    "IELTS": 7.5,
    "NUET": None,
    "UNT": 0,
    "AET": None,
    "extracurricularInterests": [],
}


def sign_in(client, username="alice"):
    credentials = {"username": username, "password": "strong-password-123"}
    assert client.post("/auth/register", json=credentials).status_code == 201
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api(tmp_path):
    with TestClient(create_app(tmp_path / "auth.db")) as client:
        yield client, sign_in(client)


def test_save_replace_and_restart(tmp_path):
    path = tmp_path / "auth.db"
    with TestClient(create_app(path)) as client:
        headers = sign_in(client)
        assert client.get("/survey", headers=headers).status_code == 404
        payload = {"survey": ANSWERS}
        response = client.post("/survey", headers=headers, json=payload)
        assert response.status_code == 200
        assert response.json() == payload
        assert client.get("/survey", headers=headers).json() == payload
        updated = {"survey": {**ANSWERS, "city": "Astana", "SAT": None}}
        assert client.post("/survey", headers=headers, json=updated).json() == updated
    with TestClient(create_app(path)) as client:
        assert client.get("/survey", headers=headers).json() == updated
        assert client.get("/auth/me", headers=headers).status_code == 200
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM surveys").fetchone()[0] == 1


def test_surveys_are_private(api):
    client, alice = api
    bob = sign_in(client, "bob")
    assert client.post("/survey", headers=alice, json={"survey": ANSWERS}).status_code == 200
    assert client.get("/survey", headers=bob).status_code == 404
    bob_payload = {"survey": {**ANSWERS, "city": "Astana"}}
    assert client.post("/survey", headers=bob, json=bob_payload).status_code == 200
    assert client.get("/survey", headers=alice).json() == {"survey": ANSWERS}
    assert client.get("/survey", headers=bob).json() == bob_payload


def test_authentication_required(api):
    client, headers = api
    for invalid in ({}, {"Authorization": "Bearer invalid"}):
        assert client.get("/survey", headers=invalid).status_code == 401
        assert client.post("/survey", headers=invalid, json={"survey": ANSWERS}).status_code == 401
    client.post("/auth/logout", headers=headers)
    assert client.get("/survey", headers=headers).status_code == 401
    assert client.post("/survey", headers=headers, json={"survey": ANSWERS}).status_code == 401


@pytest.mark.parametrize("payload", [
    {},
    ANSWERS,
    {"survey": None},
    {"survey": []},
    {"survey": {}},
    {"survey": {key: value for key, value in ANSWERS.items() if key != "SAT"}},
    {"survey": {**ANSWERS, "sat": 1200}},
    {"survey": ANSWERS, "user_id": 2},
])
def test_invalid_payload_does_not_overwrite_survey(api, payload):
    client, headers = api
    client.post("/survey", headers=headers, json={"survey": ANSWERS})
    assert client.post("/survey", headers=headers, json=payload).status_code == 422
    assert client.get("/survey", headers=headers).json() == {"survey": ANSWERS}
