# LocusBackend

The Python/FastAPI backend uses PostgreSQL for users, sessions, surveys and saved roadmaps. The program catalog is read directly from `catalogs/kazakhstan_programs.json`. The existing `/auth/*`, `/survey` and `/recommendations` routes are preserved. No application data is written to JSON files or SQLite by the API.

## Setup

For local development with PostgreSQL installed, run `.venv/Scripts/python run_local.py`.
The launcher initializes a separate password-protected PostgreSQL cluster on loopback port 54328
only when no configuration exists, creates a restricted application role, saves generated connection
settings to the ignored `.env`, and starts the API on port 8000. Subsequent runs restart that cluster
if necessary without replacing data. Set `PG_BIN` if PostgreSQL binaries are not detected.
The existing system PostgreSQL service is not reconfigured. Keep `data/postgres-local` to retain accounts.

Configuration is automatically loaded from the `.env` beside `settings.py`, even when the IDE's
working directory is different. Real environment variables take precedence. Plain
`python -m uvicorn main:app --reload` works after setup while PostgreSQL is running;
use `run_local.py` after reboot to start both services. No `--env-file` flag is required.
`setup_local.py` configures/starts the database without launching the API.

Python 3.12+ and PostgreSQL 16+ (tested with 18):

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
# Configure DATABASE_URL with a dedicated PostgreSQL role/database.
.venv/Scripts/python -m uvicorn main:app --env-file .env --host 127.0.0.1 --port 8000
```

`DATABASE_URL` must use `postgresql://USER:PASSWORD@HOST:PORT/DATABASE`; URL-encode special characters in credentials. There is no file-storage fallback. `.env` contains configuration only. The startup initializer applies numbered migrations once, tracking versions in `schema_migrations` under a PostgreSQL advisory lock. Add reviewed numbered migrations and corresponding initializer steps for future schema changes; do not change an already-applied migration.

Set `ALLOWED_ORIGINS` to a JSON array of exact frontend origins. `COOKIE_SECURE=false` is for local HTTP only; it defaults to true for HTTPS. Production must provide HTTPS. On Railway, attach a PostgreSQL service and supply `DATABASE_URL`; the old `/data` SQLite volume is no longer the active data store. `PORT` is still respected by the Docker command.

## Existing authentication contract

| Method | Route | Request/result |
| --- | --- | --- |
| POST | `/auth/register` | `{username,password}` → `{id,username}`, 201 |
| POST | `/auth/login` | `{username,password}` → `{access_token,token_type,expires_in}` |
| GET | `/auth/me` | Authenticated `{id,username}` |
| POST | `/auth/logout` | Revoke current session, 204 |
| GET/POST | `/survey` | Read/save the user's survey |
| GET/POST | `/recommendations` | Existing recommendation engine and payload |
| GET | `/health` | API liveness |

Usernames are 3–50 Latin letters, digits or underscores, case-insensitive; passwords are 8–128 characters. Registration does not itself log in. Password hashing remains PBKDF2-HMAC-SHA256 with 600,000 iterations and random salts. Tokens remain random, expire after 24 hours, and are stored only as SHA-256 digests in PostgreSQL. Existing API clients continue using `Authorization: Bearer <access_token>`.

For browsers, login with `X-Locus-Request: 1` from an allowed Origin also sets an HttpOnly, SameSite=Lax session cookie. The frontend uses `credentials: 'include'` without storing tokens in localStorage. Cookie-based mutations require this custom header and an allowed Origin. Bearer clients without an Origin keep working. Logout revokes the session and clears the cookie. Public deployments still need gateway rate limiting; email verification, password reset and OAuth are outside this update.

## Questionnaire and profile editing through `/survey`

The original payload remains supported:

```json
{"survey":{"grade":11,"entryYear":2027,"interest":"Software engineering","city":"Astana","mustStay":false,"budget":null,"funding":["self_funded"],"category":"domestic","academicStrengths":[],"SAT":null,"IELTS":null,"NUET":null,"UNT":null,"AET":null,"extracurricularInterests":[]}}
```

Legacy writes replace the fifteen fields and return `{survey}`; legacy reads retain that shape. They retain their original JSON-value flexibility. Before the first save, GET returns 404. All ownership comes from the authenticated session.

The frontend sends an additional optional `state` object:

```text
{
  survey: <the existing fifteen fields>,
  state: {
    profile: <complete ApplicantProfile, or null during diagnosis>,
    draft: <ApplicantProfile>,
    draftStep: 0..17,
    answeredQuestions: <known question IDs>,
    revision: <last server revision; 0 for a new survey>
  }
}
```

`ApplicantProfile` is validated in `profile_schema.py`: exams/statuses/scores, language, academic performance, funding, budget, constraints, strengths, free-form interests (one string or a list), exam goals and IELTS section scores. State is converted to the recommendation survey through `to_survey(state.profile or state.draft)`. During a multi-step save, this derived survey is canonical because the parallel top-level `survey` may be stale. Funding becomes the recommendation engine's existing funding list; legacy `grant` and `scholarship` values are normalized, and a legacy KZT budget object becomes its numeric amount. Exams become numeric top-level scores or null. The original exam statuses and extra fields remain intact in `state`.

Send `X-Locus-User` equal to the authenticated user's ID for state writes. It only guards against a different tab changing the session; it cannot select another user's data. Draft and committed profile are updated atomically in one PostgreSQL transaction. Each save increments `revision`; stale writes return 409. Submitted profiles require all questions to be answered or skipped. Profile edits use the same POST route. GET recommendations returns 409 for an unfinished frontend questionnaire.

Browser GET requests with `X-Locus-Request: 1` also receive `userId`, `revision`, and `updatedAt`. Older surveys without `state` remain readable; the frontend imports supported values and reports incompatible legacy formats instead of silently overwriting them. A legacy write intentionally replaces the survey and removes the optional frontend state, matching replacement semantics.

## Existing SQLite data

The runtime no longer reads `DATABASE_PATH`. To import previous records, back up the old database, stop writers, set `DATABASE_URL` to an **empty** PostgreSQL database, then run:

```powershell
.venv/Scripts/python migrate_sqlite.py C:/path/to/old/auth.db
```

The utility opens SQLite read-only, preserves user IDs, password/token hashes and survey/catalog records, and imports in one transaction. It refuses to overwrite a nonempty target and leaves the source intact. PostgreSQL's own storage files/volumes are database storage, not file-based API persistence.

## Tests and extension points

Set `TEST_DATABASE_URL` to a dedicated PostgreSQL test database and run `python -m pytest -q`. Each integration test creates and drops its own uniquely named schema. The frontend repository also provides `scripts/test_locus_backend.py --backend <this folder> --browser` to launch an isolated PostgreSQL cluster and run the backend suite plus browser integration tests. Do not point tests at production.

`main.py` keeps the established routes; `database.py` owns transactions; `profile_schema.py` owns the frontend contract. The recommendation engine is unchanged. `JSONProgramRepository` is the default catalog adapter for all recommendation and AI routes. It loads and validates `catalogs/kazakhstan_programs.json` once when the application is created, using a path relative to the project rather than the working directory. Restart the backend after editing the catalog; no database import is needed. The Docker image includes the catalog. `PostgreSQLProgramRepository` and the administrative importer remain available for explicitly injected database repositories; they do not supply the default API catalog.
# OpenRouter recommendations, profile analysis, and roadmap coaching

The backend reads the server-only `API_GEMMA` OpenRouter key from this folder's `.env`.
Install `requirements.txt` and restart the backend; startup applies migrations
automatically. `AI_FREE_MODELS` contains an ordered, free-only fallback chain:
NEX N2.5 Mini, DeepSeek V4 Flash, NVIDIA Nemotron 3 Super, then OpenRouter's free router.
Set `AI_MODEL=vendor/model-id` to use a reliable structured-output-capable paid
model first in production. Leave it blank for free-only development. The backend
rejects non-free IDs in `AI_FREE_MODELS` and caps prices at zero only for free
requests. Paid primary requests have no zero-price cap; free models remain fallbacks.

Roadmap steps are built deterministically from verified requirements and explicit
personal goals; unknown facts produce verification tasks. OpenRouter receives at
most six steps per request, using `response_format.type=json_schema`, `strict=true`,
and `provider.require_parameters=true`. The strict wire schema contains only
`steps[]` with `title`, `action`, `why`, `how`, `priority`, `duration` (estimated
minutes), `deadline`, `source` (URL or null), and `dependsOn`. Only `why` and `how`
may change. All other fields, step count, and order must match server input.
Pydantic validates every response. Invalid output is checked for fenced Markdown
JSON, then gets one repair request with the same schema and a prohibition on new
facts. Failed repair aborts the entire generation with retryable HTTP 503
`invalid_response`, without saving partial batches or replacing the previous plan.
The existing public response and stored plan formats are unchanged.

After registering/signing in and saving a completed `/survey`, call either:

* `POST /ai/recommendations` with `{"limit":4}` for the four best current matches. Automatic recommendations
  are capped at four even if an older client requests more.
* `POST /ai/roadmap` with `{"programIds":["actual-program-id","another-program-id"]}`
  for up to six unique program IDs from current matching results.

Both routes return the same planning bundle. Use the existing bearer authentication,
or session cookies with the existing origin and `X-Locus-Request: 1` protections.
Neither route accepts another user's ID or arbitrary profile content. Requests read
the authenticated user's latest saved questionnaire. Unknown/nonmatching program
IDs return 422; an incomplete questionnaire returns 409; a missing survey returns 404.

The response preserves the existing `recommendations`, `roadmap`, `nextAction`,
verified deadlines, requirements, and sources. On success, `ai.status` is `generated`
and `coaching.programs` contains `program_id`, an `explanation`, and
`steps` with `task_id`, `why`, `how` (small actionable instructions), and
`suggested_timing`. Join coaching by program/task IDs to the original result.
Source links and deadlines come from the original deterministic tasks/requirements,
not from generated text. Suggested timing is relative planning advice, not an
application deadline. A successful `/ai/roadmap` response also contains
`roadmapPlan`: one validated plan that deduplicates shared exams/documents while
keeping university-specific applications and official deadlines separate. It stores
dependencies, effort, priority, status, detailed instructions, completion criteria,
fact basis and sources. The plan is persisted only after local validation succeeds.

When every provider attempt fails, the API returns retryable HTTP 503 (or an NDJSON
`error` event) with a safe code such as `all_models_failed` or
`overall_timeout_exhausted`. The request row ends in `failed`, never `completed`.
The streamed deterministic baseline remains usable, and an earlier validated plan
is preserved in PostgreSQL. Free-provider availability is not guaranteed. Clients
allow 95 seconds and the production proxy allows 90 seconds; generation itself has
a 60-second total budget shared by all batches, fallbacks, and repairs. The backend divides that budget across the remaining
models (normally about 14–15 seconds each) and will not start an attempt with less
than 10 seconds available. `attempt_timeout` describes only that attempt and does
not mark a model permanently unavailable.
Only matching program and task IDs with validated JSON are accepted. AI prose is
still advice, not independently verified admissions information.

The latest response per account is cached in PostgreSQL `ai_results`. The validated
plan, revision and completion state are stored in `roadmap_plans` and
`roadmap_step_progress`; preferences are stored in `roadmap_preferences`. Profile,
exam-goal, catalog, date, selection, model, and prompt-version changes invalidate the
cache. A per-account database lock prevents concurrent generations, and a 20-second
cooldown limits repeated changed/failed requests (429 with `Retry-After`). No generated
responses are saved in files. Passwords, tokens, usernames, and unrelated profile
fields are excluded from prompts. Educational answers and relevant exam goals are
sent to OpenRouter and its model provider. There is no uploaded-portfolio analysis;
portfolio advice uses saved interests and academic strengths.

Roadmap API:

* `GET /roadmap` returns the authenticated user's latest validated plan and revision.
* `GET/POST /roadmap/preferences` reads or writes IANA timezone, weekly available
  hours, and achievement summaries used for planning.
* `POST /roadmap/steps/{stepId}` with `{revision,status}` changes a step. A dependent
  step cannot be completed before prerequisites; reopening a prerequisite resets
  completed dependents. A stale revision returns 409.
* `GET /ai/requests/{requestId}` returns the owner's current stage and safe timings.
* `POST /ai/requests/{requestId}/cancel` cancels the owner's active request.

Failed, invalid or cancelled regeneration never replaces the last validated plan.
Regeneration restores completion only for stable step IDs; changed requirements,
exam goals or selected programs invalidate only affected IDs. Past official dates
are blocked rather than presented as future actions. Unknown dates are explicit
planning suggestions and have `needsVerification: true`.

The existing `/recommendations` routes remain deterministic and compatible. The
frontend now calls `/ai/roadmap` with `generateAI: false` after saving profile changes.
This returns Russian preparation instructions without a provider call or AI cooldown.
The AI button requests explanations in Russian with 3–7 actionable instructions
per task. Personal exam goals add diagnostic, score-recording, weak-section and practice
steps. Their IDs change when the relevant score, status, goal or section scores change.
Selected programs are evaluated directly, including saved choices whose eligibility
has changed; clients must inspect eligibility before applying. Catalog records are read directly from
`catalogs/kazakhstan_programs.json`; missing facts remain unknown. See `catalogs/README.md`
and `recommendation/README.md` for catalog maintenance.

Every generation disables model reasoning, requests strict JSON Schema output, and
still performs local Pydantic and domain validation. This prevents hidden reasoning
from consuming the profile-analysis output budget and rejects incomplete responses.
The schema is sent once through `response_format`; `require_parameters` prevents
routing to a provider that cannot accept it.

References: [DeepSeek](https://openrouter.ai/deepseek/deepseek-v4-flash-0731),
[NEX N2.5 Mini](https://openrouter.ai/nex-agi/nex-n2.5-mini:free),
[NVIDIA Nemotron 3 Super](https://openrouter.ai/nvidia/nemotron-3-super-120b-a12b:free), and
[OpenRouter structured output](https://openrouter.ai/docs/guides/features/structured-outputs).
# Диагностика AI и отмена запросов

Frontend использует существующие `/ai/roadmap`, `/ai/recommendations`, `/ai/profile`.
Обычный JSON-контракт сохранён. `Accept: application/x-ndjson` включает поток событий
`stage`, `baseline` (проверенный базовый roadmap), `result` или `error`.
Клиент передаёт UUID в `X-Request-ID`; сервер возвращает его в заголовке и событиях.
Ошибки после начала потока приходят событием `error`, а не новым HTTP-статусом.

`POST /ai/requests/{uuid}/cancel` требует сессию и может отменить только запрос
этого пользователя. Миграция 004 добавляет `ai_requests` и `ai_debug_payloads` в
PostgreSQL, не меняя пользовательские профили. Отмена работает между процессами:
обработчик проверяет флаг в БД, закрывает активный HTTP-вызов OpenRouter, не сохраняет
отменённый результат в кеш. Разрыв NDJSON-соединения также останавливает обработку.
Повтор UUID запрещён; ранняя отмена создаёт запись до старта запроса. Уже завершённый
удалённый расчёт провайдера нельзя гарантированно отменить на стороне OpenRouter.

Логгер `locus.ai` пишет JSON в stderr: UTC-время, requestId, purpose, stage,
elapsedMs, модель/попытку, durationMs, безопасный код ошибки и доступные сведения
о токенах. Этапы `matched`, `prepared`, `context_ready`, `model`, `validating`,
`validated`, `saved`, `budget_exhausted`, `generation_failed`, `cache_hit`,
`completed/failed/cancelled` позволяют отделить поиск,
подготовку, вызов модели, проверку и сохранение. Имена пользователей, анкеты, cookie и ключи
в эти логи не включаются. При ошибке провайдера его сырой текст не логируется.

Полные подготовленные контексты/инструкции/схемы и ответы включаются **только**
совместными настройками `APP_ENV=development` и `AI_DEBUG_PAYLOADS=true`.
Они записываются в приватную таблицу `ai_debug_payloads`, не в файлы или HTTP-логи.
Доступ — только через защищённое подключение администратора к БД; публичного API
для чтения нет. Поля с ключами, cookie, паролями и токенами редактируются.
Не включайте этот режим в production и не предоставляйте пользователям доступ
к таблицам диагностики. Записи старше 7 дней удаляются при регистрации нового запроса.

Кеш результата остаётся персональным и зависит от анкеты, проверенных фактов,
даты оценки, набора моделей и версии промпта. Контекст сокращается по назначению:
рекомендации не получают все шаги, анализ профиля — дублирующие финансовые/рейтинговые
сводки. В roadmap сохраняется переиспользование только незатронутых шагов.
Источник требований по-прежнему каталог, а не генерация модели.

Проверка: `pytest -q` с `TEST_DATABASE_URL` (каждый тест создаёт отдельную схему).
`tests/test_ai_jobs.py` проверяет поток, кеш, отмену активного provider HTTP-запроса,
раннюю отмену, разграничение владельцев и отладочные данные.
`python check_ai.py --purpose profile` проверяет реальные бесплатные модели только
на синтетической анкете, выводя статусы и длительность без ключей/сырых ответов.
