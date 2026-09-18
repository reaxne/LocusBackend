# LocusBackend

The existing Python/FastAPI backend now uses PostgreSQL for users, sessions, surveys and the program catalog. The existing `/auth/*`, `/survey` and `/recommendations` routes are preserved. No application data is written to JSON files or SQLite by the API.

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

`DATABASE_URL` must use `postgresql://USER:PASSWORD@HOST:PORT/DATABASE`; URL-encode special characters in credentials. There is no file-storage fallback. `.env` contains configuration only. The startup initializer applies `migrations/001_core.sql` once, tracking the version in `schema_migrations` under a PostgreSQL advisory lock. Add reviewed numbered migrations and corresponding initializer steps for future schema changes; do not change an already-applied migration.

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

`ApplicantProfile` is validated in `profile_schema.py`: exams/statuses/scores, language, academic performance, funding, budget, constraints, strengths, interests, exam goals and IELTS section scores. The top-level survey must equal `to_survey(state.profile or state.draft)`; conflicts between the two representations return 422. Funding becomes the recommendation engine's existing funding list; exams become numeric top-level scores or null. The original exam statuses and extra fields remain intact in `state`.

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

`main.py` keeps the established routes; `database.py` owns transactions; `profile_schema.py` owns the frontend contract. The recommendation engine is unchanged. `PostgreSQLProgramRepository` is the catalog adapter; `SQLiteProgramRepository` remains only as an import alias for older code. The catalog importer uses `DATABASE_URL` or `--database <PostgreSQL URL>`. JSON catalog files are optional import inputs, not runtime persistence. No real catalog is fabricated by this update.
# Gemma recommendations and roadmap coaching

The backend reads the server-only `API_GEMMA` OpenRouter key from this folder's `.env`.
Install `requirements.txt` and restart the backend; startup applies migration
`002_ai.sql` automatically. The model is fixed to
`google/gemma-4-26b-a4b-it:free`, with no paid-model fallback.

After registering/signing in and saving a completed `/survey`, call either:

* `POST /ai/recommendations` with `{"limit":5}` for the best current matches.
* `POST /ai/roadmap` with `{"programIds":["actual-program-id","another-program-id"]}`
  for up to six unique program IDs from current matching results.

Both routes return the same planning bundle. Use the existing bearer authentication,
or session cookies with the existing origin and `X-Locus-Request: 1` protections.
Neither route accepts another user's ID or arbitrary profile content. Requests read
the authenticated user's latest saved questionnaire. Unknown/nonmatching program
IDs return 422; an incomplete questionnaire returns 409; a missing survey returns 404.

The response preserves the existing `recommendations`, `roadmap`, `nextAction`,
verified deadlines, requirements, and sources. On success, `ai.status` is `generated`
and `coaching.programs` contains `program_id`, a personalized `explanation`, and
`steps` with `task_id`, `why`, `how` (small actionable instructions), and
`suggested_timing`. Join coaching by program/task IDs to the original result.
Source links and deadlines come from the original deterministic tasks/requirements,
not from generated text. Suggested timing is relative planning advice, not an
application deadline. Each program retains its own roadmap and next action; this
version does not merge tasks across programs or persist completion controls.

`ai.status: unavailable` includes a safe `reason` such as `rate_limited`, `timeout`,
`not_configured`, or `invalid_response`, alongside usable deterministic results.
Free-provider availability is not guaranteed. Allow at least 60 seconds in the
calling client's request timeout. Generation has a 55-second HTTP timeout.
Only matching program and task IDs with validated JSON are accepted. AI prose is
still advice, not independently verified admissions information.

The latest response per account is cached in PostgreSQL `ai_results`. Profile,
exam-goal, catalog, date, selection, model, and prompt-version changes invalidate the
cache. A per-account database lock prevents concurrent generations, and a 20-second
cooldown limits repeated changed/failed requests (429 with `Retry-After`). No generated
responses are saved in files. Passwords, tokens, usernames, and unrelated profile
fields are excluded from prompts. Educational answers and relevant exam goals are
sent to OpenRouter and its model provider. There is no uploaded-portfolio analysis;
portfolio advice uses saved interests and academic strengths.

The existing `/recommendations` routes remain deterministic and compatible. The
frontend now calls `/ai/roadmap` with `generateAI: false` after saving profile changes.
This returns Russian preparation instructions without a provider call or AI cooldown.
The AI button requests Gemma explanations in Russian with 3–7 actionable instructions
per task. Personal exam goals add diagnostic, score-recording, weak-section and practice
steps. Their IDs change when the relevant score, status, goal or section scores change.
Selected programs are evaluated directly, including saved choices whose eligibility
has changed; clients must inspect eligibility before applying. Catalog records must already
be imported into PostgreSQL; missing facts remain unknown. See `catalogs/README.md`
and `recommendation/README.md` for catalog maintenance.

References: [requested model](https://openrouter.ai/google/gemma-4-26b-a4b-it:free),
[OpenRouter API documentation](https://openrouter.ai/docs/api/reference/overview).
