"""Small authentication API. Run with: python -m uvicorn main:app --reload."""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 24 * 60 * 60
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, _ = stored.split(":", 1)
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), stored)


# Missing accounts still run the same expensive password verification.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.lower()


class UserResponse(BaseModel):
    id: int
    username: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = SESSION_SECONDS


class Survey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: JsonValue
    entryYear: JsonValue
    interest: JsonValue
    city: JsonValue
    mustStay: JsonValue
    budget: JsonValue
    funding: JsonValue
    category: JsonValue
    academicStrengths: JsonValue
    SAT: JsonValue
    IELTS: JsonValue
    NUET: JsonValue
    UNT: JsonValue
    AET: JsonValue
    extracurricularInterests: JsonValue


class SurveyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    survey: Survey


def create_app(database_path: str | Path | None = None) -> FastAPI:
    db_path = Path(database_path or os.getenv("DATABASE_PATH", "data/auth.db"))

    @contextmanager
    def database():
        connection = sqlite3.connect(db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS surveys (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    answers_json TEXT NOT NULL
                );
            """)
        yield

    application = FastAPI(title="Locus Auth API", version="1.0.0", lifespan=lifespan)
    auth_router = APIRouter(prefix="/auth", tags=["auth"])

    def unauthorized() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def current_session(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> dict:
        if credentials is None:
            raise unauthorized()
        token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        with database() as db:
            row = db.execute(
                """SELECT users.id, users.username, sessions.token_hash
                   FROM sessions JOIN users ON users.id = sessions.user_id
                   WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
                (token_hash, int(time.time())),
            ).fetchone()
        if row is None:
            raise unauthorized()
        return dict(row)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @auth_router.post("/register", response_model=UserResponse, status_code=201)
    def register(payload: Credentials):
        password_hash = hash_password(payload.password)
        try:
            with database() as db:
                cursor = db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (payload.username, password_hash),
                )
                return {"id": cursor.lastrowid, "username": payload.username}
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Username already registered") from None

    @auth_router.post("/login", response_model=TokenResponse)
    def login(payload: Credentials):
        with database() as db:
            user = db.execute(
                "SELECT * FROM users WHERE username = ?", (payload.username,)
            ).fetchone()
        valid = verify_password(
            payload.password, user["password_hash"] if user else DUMMY_PASSWORD_HASH
        )
        if not valid or user is None:
            raise unauthorized()
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with database() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (hashlib.sha256(token.encode()).hexdigest(), user["id"], now + SESSION_SECONDS),
            )
        return TokenResponse(access_token=token)

    @auth_router.get("/me", response_model=UserResponse)
    def me(session: Annotated[dict, Depends(current_session)]):
        return session

    @auth_router.post("/logout", status_code=204)
    def logout(session: Annotated[dict, Depends(current_session)]):
        with database() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))

    application.include_router(auth_router)

    @application.post("/survey", response_model=SurveyPayload, tags=["survey"])
    def save_survey(
        payload: SurveyPayload,
        session: Annotated[dict, Depends(current_session)],
    ):
        with database() as db:
            db.execute(
                """INSERT INTO surveys (user_id, answers_json) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET answers_json = excluded.answers_json""",
                (session["id"], payload.survey.model_dump_json()),
            )
        return payload

    @application.get("/survey", response_model=SurveyPayload, tags=["survey"])
    def get_survey(session: Annotated[dict, Depends(current_session)]):
        with database() as db:
            row = db.execute(
                "SELECT answers_json FROM surveys WHERE user_id = ?", (session["id"],)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Survey not found")
        return {"survey": json.loads(row["answers_json"])}

    return application


app = create_app()
