"""Small authentication API. Run with: python -m uvicorn main:app --reload."""

import hashlib
import hmac
import secrets
import time
import os
import json
import asyncio
import queue
from uuid import UUID, uuid4
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from database import Database, UsernameAlreadyRegistered, SurveyConflict
from settings import load_environment
from profile_schema import FrontendState, to_survey
from recommendation.embeddings import EmbeddingProvider, InterestMatcher
from recommendation.models import RecommendationResponse, StudentProfile
from recommendation.recommender import Recommender
from recommendation.ai import GemmaClient, AIUnavailable, MODEL, build_context, cache_key, compact_context
from recommendation.planning import prepare_plan
from recommendation.prompts import VERSION as AI_PROMPT_VERSION
from recommendation.ai_reuse import delta_context, merge_advice
from recommendation.telemetry import Job, Cancelled, current_job, emit, check
from psycopg.types.json import Jsonb
from recommendation.repository import ProgramRepository, PostgreSQLProgramRepository


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
    state: FrontendState | None = None


def create_app(
    database_url: str | None = None,
    *,
    program_repository: ProgramRepository | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> FastAPI:
    load_environment()
    database = Database(database_url)
    recommender = Recommender(
        program_repository if program_repository is not None else PostgreSQLProgramRepository(database),
        InterestMatcher(embedding_provider),
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.initialize()
        yield

    application = FastAPI(title="Locus Auth API", version="1.0.0", lifespan=lifespan)
    origins = json.loads(os.getenv('ALLOWED_ORIGINS', '["http://127.0.0.1:5173","http://localhost:5173","http://127.0.0.1:3000","http://localhost:3000"]'))
    application.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
        allow_methods=['GET','POST'], allow_headers=['Content-Type','Authorization','X-Locus-Request','X-Locus-User','X-Request-ID'])

    @application.middleware('http')
    async def browser_security(request: Request, call_next):
        if request.method == 'POST':
            origin = request.headers.get('origin')
            browser_request = request.headers.get('x-locus-request') == '1'
            cookie_request = request.cookies.get('locus_session') and not request.headers.get('authorization')
            if (origin and origin not in origins) or ((browser_request or cookie_request) and origin not in origins):
                return JSONResponse({'detail':'Untrusted origin'}, status_code=403)
            if cookie_request and not browser_request:
                return JSONResponse({'detail':'Missing CSRF header'}, status_code=403)
            if len(await request.body()) > 32768:
                return JSONResponse({'detail':'Payload too large'}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response
    auth_router = APIRouter(prefix="/auth", tags=["auth"])

    def unauthorized() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def current_session(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> dict:
        token = credentials.credentials if credentials else request.cookies.get('locus_session')
        if not token:
            raise unauthorized()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
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
    def login(payload: Credentials, request: Request, response: Response):
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
        if request.headers.get('x-locus-request') == '1':
            old = request.cookies.get('locus_session')
            if old:
                database.delete_session(hashlib.sha256(old.encode()).hexdigest())
            response.set_cookie('locus_session', token, httponly=True, samesite='lax',
                secure=os.getenv('COOKIE_SECURE', 'true').lower() == 'true', max_age=SESSION_SECONDS, path='/')
        return TokenResponse(access_token=token)

    @auth_router.get("/me", response_model=UserResponse)
    def me(session: Annotated[dict, Depends(current_session)]):
        return session

    @auth_router.post("/logout", status_code=204)
    def logout(response: Response, session: Annotated[dict, Depends(current_session)]):
        database.delete_session(session["token_hash"])
        response.delete_cookie('locus_session', path='/')

    application.include_router(auth_router)

    @application.post("/survey", tags=["survey"])
    def save_survey(
        payload: SurveyPayload,
        request: Request,
        session: Annotated[dict, Depends(current_session)],
    ):
        if payload.state is not None:
            if request.headers.get('x-locus-user') != str(session['id']):
                raise HTTPException(409, 'Account changed. Reload before saving.')
            if payload.survey.model_dump() != to_survey(payload.state.profile or payload.state.draft):
                raise HTTPException(422, 'Survey and frontend state disagree')
        try:
            state = database.save_survey(session['id'], payload.survey.model_dump(mode='json'),
                payload.state.model_dump(mode='json') if payload.state else None,
                payload.state.revision if payload.state else None)
        except SurveyConflict:
            raise HTTPException(409, 'Survey changed in another tab') from None
        return {'survey': payload.survey, **({'state': state} if state is not None else {})}

    @application.get("/survey", tags=["survey"])
    def get_survey(request: Request, session: Annotated[dict, Depends(current_session)]):
        record = database.get_survey_record(session['id'])
        if record is None:
            raise HTTPException(status_code=404, detail="Survey not found")
        result = {'survey': record['answers_json'], **({'state': record['state_json']} if record['state_json'] is not None else {})}
        if request.headers.get('x-locus-request') == '1':
            result.update(revision=record['revision'], userId=str(session['id']), updatedAt=record['updated_at'].isoformat())
        return result

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
        record = database.get_survey_record(session['id'])
        if record and record['state_json'] is not None and record['state_json']['profile'] is None:
            raise HTTPException(409, 'Complete the questionnaire first')
        survey = record['answers_json'] if record else None
        if survey is None:
            raise HTTPException(status_code=404, detail="Survey not found")
        try:
            profile = StudentProfile.model_validate(survey)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_context=False)) from exc
        return recommender.recommend(profile, limit=limit)

    class AIRequest(BaseModel):
        model_config = ConfigDict(extra='forbid')
        programIds: list[str] = Field(default_factory=list, max_length=6)
        limit: int = Field(default=5, ge=1, le=6)
        generateAI: bool = True

    def build_ai_plan(payload, request, session):
        check()
        purpose = request.url.path.rsplit('/', 1)[-1]
        if request.headers.get('x-locus-user') not in (None, str(session['id'])):
            raise HTTPException(409, 'Account changed. Reload before generating.')
        record = database.get_survey_record(session['id'])
        if record is None:
            raise HTTPException(404, 'Save your questionnaire first')
        if record['state_json'] is not None and record['state_json'].get('profile') is None:
            raise HTTPException(409, 'Complete the questionnaire first')
        try:
            profile = StudentProfile.model_validate(record['answers_json'])
        except ValidationError:
            raise HTTPException(422, 'Saved questionnaire needs updating') from None
        baseline = recommender.recommend(profile, limit=6 if payload.programIds else payload.limit,
                                        program_ids=payload.programIds or None)
        emit('matched')
        if payload.programIds:
            requested = set(payload.programIds)
            available = {p.program_id for p in baseline.recommendations}
            if len(requested) != len(payload.programIds) or not requested <= available:
                raise HTTPException(422, 'Select unique program IDs from your current matching results')
            baseline.recommendations = [p for p in baseline.recommendations if p.program_id in requested]
        baseline = prepare_plan(baseline, profile, record['state_json'])
        emit('prepared')
        check()
        result = baseline.model_dump(mode='json', by_alias=True)
        result['ai'] = {'status': 'unavailable', 'model': None, 'cached': False,
                        'purpose': purpose, 'promptVersion': AI_PROMPT_VERSION,
                        'notice': 'AI coaching is planning advice; verify admissions facts with official sources.'}
        if purpose == 'profile':
            result['analysis'] = None
        job = current_job.get()
        if job and purpose == 'roadmap':
            job.events.put({'type': 'baseline', 'data': json.loads(json.dumps(result))})
        if not baseline.recommendations and purpose != 'profile':
            result['ai']['reason'] = 'no_matching_programs'
            return result
        if not payload.generateAI:
            result['ai']['reason'] = 'not_requested'
            return result
        context = build_context(profile, baseline, record['state_json'])
        context['_purpose'] = purpose
        try:
            fingerprint = cache_key(context)
        except AIUnavailable as exc:
            result['ai']['reason'] = str(exc)
            return result
        with database.ai_request(session['id']) as db:
            if db is None:
                raise HTTPException(429, 'Generation already in progress. Try again shortly.', headers={'Retry-After': '20'})
            previous = db.execute("SELECT *, updated_at > now() - interval '20 seconds' AS recent FROM ai_results WHERE user_id=%s AND purpose=%s",
                                  (session['id'], purpose)).fetchone()
            if previous and previous['cache_key'] == fingerprint and previous['result'].get('ai', {}).get('status') == 'generated':
                cached = previous['result']
                cached['ai']['cached'] = True
                emit('cache_hit')
                return cached
            if previous and previous['recent']:
                raise HTTPException(429, 'Please wait before generating again.', headers={'Retry-After': '20'})
            try:
                client = GemmaClient()
                generation_context, reused = context, {}
                if purpose == 'roadmap' and previous and previous['result'].get('ai', {}).get('promptVersion') == AI_PROMPT_VERSION:
                    generation_context, reused = delta_context(context, previous['result'])
                before_bytes = len(json.dumps(generation_context).encode())
                generation_context = compact_context(generation_context, purpose)
                emit('context_ready', originalBytes=before_bytes, contextBytes=len(json.dumps(generation_context).encode()))
                coaching = client.generate(generation_context)
                if purpose == 'profile':
                    result['analysis'] = coaching.model_dump()
                elif purpose == 'roadmap':
                    result['coaching'], result['ai']['reusedSteps'] = merge_advice(context, coaching.model_dump(), reused)
                else:
                    result['coaching'] = coaching.model_dump()
                result['ai']['status'] = 'generated'
                result['ai']['model'] = client.model
            except AIUnavailable as exc:
                result['ai']['reason'] = str(exc)
                emit('unavailable', reason=str(exc))
            result['ai']['attempts'] = client.attempts
            check()
            db.execute('''INSERT INTO ai_results(user_id,purpose,cache_key,result) VALUES (%s,%s,%s,%s)
                ON CONFLICT(user_id,purpose) DO UPDATE SET cache_key=excluded.cache_key,
                result=excluded.result,updated_at=now()''', (session['id'],purpose,fingerprint,Jsonb(result)))
        return result

    @application.post('/ai/requests/{request_id}/cancel', status_code=204)
    def cancel_ai(request_id: UUID, session: Annotated[dict, Depends(current_session)]):
        with database._connect() as db:
            # A tombstone also handles cancellation arriving before registration.
            db.execute('''INSERT INTO ai_requests(id,user_id,purpose,cancelled,ended_at)
                VALUES (%s,%s,'cancelled',TRUE,now()) ON CONFLICT(id) DO UPDATE
                SET cancelled=TRUE WHERE ai_requests.user_id=excluded.user_id''', (str(request_id),session['id']))

    @application.post('/ai/recommendations', tags=['AI'])
    @application.post('/ai/roadmap', tags=['AI'])
    @application.post('/ai/profile', tags=['AI'])
    async def ai_plan(payload: AIRequest, request: Request, session: Annotated[dict, Depends(current_session)]):
        try:
            request_id = str(UUID(request.headers.get('x-request-id', str(uuid4()))))
        except ValueError:
            raise HTTPException(422, 'Invalid request ID') from None
        job = Job(database, request_id, session['id'], request.url.path.rsplit('/',1)[-1])
        def work():
            token = current_job.set(job)
            try:
                job.register()
                result = build_ai_plan(payload, request, session)
                job.check()
                job.finish('completed')
                return result
            except Cancelled:
                job.finish('cancelled')
                raise HTTPException(409, 'Request cancelled') from None
            except Exception as exc:
                job.emit('error', reason=type(exc).__name__, status=getattr(exc, 'status_code', 500))
                job.finish('failed')
                raise
            finally:
                current_job.reset(token)
        if 'application/x-ndjson' not in request.headers.get('accept',''):
            result = await asyncio.to_thread(work)
            return JSONResponse(result, headers={'X-Request-ID':request_id})
        async def stream():
            task = asyncio.create_task(asyncio.to_thread(work))
            try:
                while not task.done() or not job.events.empty():
                    try:
                        event = job.events.get_nowait()
                        yield json.dumps(event, ensure_ascii=False) + '\n'
                    except queue.Empty:
                        await asyncio.sleep(0.05)
                result = await task
                yield json.dumps({'type':'result','data':result}, ensure_ascii=False) + '\n'
            except HTTPException as exc:
                yield json.dumps({'type':'error','status':exc.status_code,'requestId':request_id}) + '\n'
            except asyncio.CancelledError:
                raise
            except Exception:
                yield json.dumps({'type':'error','status':500,'requestId':request_id}) + '\n'
            finally:
                job.stop.set()
                # Retrieve worker exceptions even after a disconnected consumer.
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return StreamingResponse(stream(), media_type='application/x-ndjson', headers={'X-Request-ID':request_id,'X-Accel-Buffering':'no'})

    return application


app = create_app()
