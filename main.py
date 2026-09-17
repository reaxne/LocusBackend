"""Small authentication API. Run with: python -m uvicorn main:app --reload."""

import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from database import Database, UsernameAlreadyRegistered
from recommendation.embeddings import EmbeddingProvider, InterestMatcher
from recommendation.models import RecommendationResponse, StudentProfile
from recommendation.recommender import Recommender
from recommendation.repository import ProgramRepository, SQLiteProgramRepository


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


def create_app(
    database_path: str | Path | None = None,
    *,
    program_repository: ProgramRepository | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> FastAPI:
    database = Database(database_path)
    recommender = Recommender(
        program_repository if program_repository is not None else SQLiteProgramRepository(database),
        InterestMatcher(embedding_provider),
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.initialize()
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
        session = database.get_session(token_hash, int(time.time()))
        if session is None:
            raise unauthorized()
        return session

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @auth_router.post("/register", response_model=UserResponse, status_code=201)
    def register(payload: Credentials):
        password_hash = hash_password(payload.password)
        try:
            return database.create_user(payload.username, password_hash)
        except UsernameAlreadyRegistered:
            raise HTTPException(status_code=409, detail="Username already registered") from None

    @auth_router.post("/login", response_model=TokenResponse)
    def login(payload: Credentials):
        user = database.get_user(payload.username)
        valid = verify_password(
            payload.password, user["password_hash"] if user else DUMMY_PASSWORD_HASH
        )
        if not valid or user is None:
            raise unauthorized()
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        database.create_session(
            hashlib.sha256(token.encode()).hexdigest(), user["id"], now + SESSION_SECONDS, now
        )
        return TokenResponse(access_token=token)

    @auth_router.get("/me", response_model=UserResponse)
    def me(session: Annotated[dict, Depends(current_session)]):
        return session

    @auth_router.post("/logout", status_code=204)
    def logout(session: Annotated[dict, Depends(current_session)]):
        database.delete_session(session["token_hash"])

    application.include_router(auth_router)

    @application.post("/survey", response_model=SurveyPayload, tags=["survey"])
    def save_survey(
        payload: SurveyPayload,
        session: Annotated[dict, Depends(current_session)],
    ):
        database.save_survey(session["id"], payload.survey.model_dump(mode="json"))
        return payload

    @application.get("/survey", response_model=SurveyPayload, tags=["survey"])
    def get_survey(session: Annotated[dict, Depends(current_session)]):
        survey = database.get_survey(session["id"])
        if survey is None:
            raise HTTPException(status_code=404, detail="Survey not found")
        return {"survey": survey}

    @application.post("/recommendations", response_model=RecommendationResponse, tags=["recommendations"])
    def recommend(
        payload: StudentProfile,
        session: Annotated[dict, Depends(current_session)],
        limit: Annotated[int, Query(ge=1, le=50)] = 5,
    ):
        return recommender.recommend(payload, limit=limit)

    @application.get("/recommendations", response_model=RecommendationResponse, tags=["recommendations"])
    def recommend_saved_survey(
        session: Annotated[dict, Depends(current_session)],
        limit: Annotated[int, Query(ge=1, le=50)] = 5,
    ):
        survey = database.get_survey(session["id"])
        if survey is None:
            raise HTTPException(status_code=404, detail="Survey not found")
        try:
            profile = StudentProfile.model_validate(survey)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_context=False)) from exc
        return recommender.recommend(profile, limit=limit)

    return application


app = create_app()
