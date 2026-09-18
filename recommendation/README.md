# Program recommendations

This package extends the FastAPI backend. The default API reads the bundled
`catalogs/kazakhstan_programs.json` directly through `JSONProgramRepository`.
PostgreSQL stores accounts, surveys, and saved plans; no catalog import is required.

## API and existing surveys

Both endpoints require the existing `Authorization: Bearer <access_token>` header:

- `POST /recommendations?limit=4` accepts a **direct StudentProfile object**.
- `GET /recommendations?limit=4` uses the authenticated user's saved `/survey`.

`limit` defaults to 4. Values 1–50 remain accepted for compatibility, but automatic recommendations return at most the four highest-ranked programs. POST evaluates without changing the saved survey.
GET returns 404 if no survey exists and 422 if its answers cannot be normalized.
The existing `/survey` storage contract remains unchanged. This matters because
old surveys allowed arbitrary JSON values, including objects where a recommendation
now requires a number. Correct those answers before requesting recommendations.

Example JavaScript, using the same survey answer object as the existing frontend:

```javascript
const response = await fetch(`${apiBaseUrl}/recommendations?limit=4`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${accessToken}`,
  },
  body: JSON.stringify(survey),
});
if (!response.ok) throw new Error(`Recommendations failed: ${response.status}`);
const result = await response.json();
```

Required profile fields: integer `grade` (1–12) and `entryYear` (2000–2200).
Interest, city, funding, strengths and extracurricular fields default to empty
lists; a single string becomes a one-item list. Whitespace and duplicate entries
are normalized. `mustStay` defaults to false and requires a nonempty city list
when true. Empty exam/budget values become null. Numbers must be finite and
nonnegative; missing scores are never zero. `category` is preserved and has no
ranking effect. Funding accepts `self_funded`, `state_grant`,
`university_scholarship`, and `other`. Budget means annual tuition in KZT.

Responses include `studentSummary`, `recommendations`, `excludedPrograms`,
`evaluatedAt`, `matchingMethod`, and catalog-level `warnings`. Each recommendation
contains `matchScore`, component `scores`, effective `weights`, detailed eligibility
for every route, financial evidence, reasons, warnings, completed/missing/failed/
unknown requirements, an ordered roadmap, and one next action.
`excludedPrograms` explains hard filters and ineligible routes among the records
returned by the catalog; inactive or other-year catalog records are not queried.
Ties are resolved by program ID. OpenAPI schemas are available at `/docs`.

## Architecture

1. `models.py` validates a normalized view of the existing questionnaire and
   defines program, provenance, eligibility, roadmap, and response schemas.
2. `repository.py` loads and validates the JSON catalog once at application creation.
   Restart the backend after catalog edits. A PostgreSQL adapter remains available
   for explicitly injected repositories; all SQL stays in `database.py`.
3. `scoring.py` applies hard city/cycle constraints and computes independent fits.
4. `eligibility.py` evaluates admission routes deterministically.
5. `embeddings.py` matches interests against program text and caches program vectors.
6. `recommender.py` calculates dynamic weights, ranks, and builds evidence-based reasons.
7. `roadmap.py` creates tasks from the selected route and verified deadlines, then
   chooses a useful unfinished task whose prerequisites are complete.

No LLM is used. A `Recommender` and its matcher are created once per application,
not once per request. Routes are synchronous, allowing FastAPI to run them in its
worker thread pool.

## Eligibility and planning policy

Routes use **OR**; requirements within a route use **AND**. Exams in unrelated
routes do not lower a passing route's exam score or appear in its roadmap.

- `eligible_now`: at least one complete, verified route is satisfied. This means
  the recorded requirements are met, not that the university has made an offer.
- `potentially_eligible`: a complete route has missing exams and enough planning
  time. Failed exams qualify for this only if its verified route explicitly sets
  `allowsRetakes: true`.
- `ineligible`: the recorded requirements cannot currently be satisfied under the
  planning policy, or the verified application deadline/cycle has passed. These
  programs are excluded from ranked results, with reasons.
- `unknown`: route data, source verification, or completeness is missing. These
  options may be ranked, with explicit warnings and a requirement-verification task.

The planning heuristic requires at least **90 days** before the earliest verified
application deadline. If it is unavailable, January 1 of the future entry year is
used only as a conservative internal horizon, never presented as a real deadline.
This is a configurable planning heuristic, not knowledge of exam booking dates or
a guarantee that a student can improve a score. Grade 10 students with future
missing exams can remain potential candidates; their roadmap also suggests
exploring a direction. Same-year missing exams need a verified deadline far enough
away. The clock can be injected into `recommend(..., as_of=...)` for deterministic tests.

Best route selection: passing before potential before unknown before ineligible;
among passing routes, highest exam fit wins; otherwise fewer unmet requirements,
then route ID. Roadmaps use only that route. Completed exams are marked completed;
missing exams get preparation and exam tasks. Deadlines remain null unless backed
by a cycle-matched source. Next action prioritizes admission blockers, then upcoming
verified deadlines, exams, funding, documents, and optional activities. Completed,
expired-deadline, and prerequisite-blocked tasks are not selected. This release
does not persist user completion of document, funding, or activity tasks.

## Match Score and dynamic weights

`matchScore` is compatibility on **0..1**, NOT an admission probability. It is not
calibrated against offers, competition, quota places, or grant outcomes. A potential
or unknown-eligibility option can have high compatibility; always display eligibility
and warnings next to the score. Scores should not be displayed as acceptance chances.

Base weights in `config.py`:

| Component | Weight | Calculation |
| --- | ---: | --- |
| interest | 0.35 | Clamped cosine similarity of student/program vectors |
| academic | 0.20 | Fraction of recommended strengths matched after normalization |
| financial | 0.20 | Verified tuition within budget = 1; funding-dependent = 0.70; otherwise budget/tuition |
| city | 0.10 | Preferred city = 1, another city = 0.35 |
| exam | 0.10 | Mean bounded exam fit across the best passing route |
| extracurricular | 0.05 | Fraction of requested activities found in known campus activity data |

If a budget is given, raw financial weight becomes 0.30. If `mustStay` is true,
city is a hard filter and its weight is removed. Unavailable components are null
and have no weight; all remaining weights are renormalized to sum to 1. If none
are usable, weights are empty and the score is 0 with an insufficient-data warning.
No fabricated neutral matches are added. Scores are returned without rounding that
could change ordering; clients may round for display.

For an exam meeting its minimum `m`, the fit is
`0.70 + 0.30 * min(1, (value - m) / (0.25 * m))`. Below minimum it is 0.
A zero minimum gives 0.70 because relative headroom is undefined. Potential and
unknown routes have no exam component. Baseline, headroom, city score, funding
score, planning horizon, and weights are centralized in `ScoringConfig`.

Over-budget programs are retained. Verified selected grant/scholarship paths
produce a funding-dependency warning; awards and full cost coverage are never
guaranteed. Missing fees or budget omit financial scoring. A verified expired
funding deadline for the program or selected route removes that funding option. Living costs and
scholarship award amounts are not modeled, and grant places are not invented.

## Deterministic interest matching

Interest matching uses no embeddings or hashes. `InterestMatcher` extracts exact,
normalized keywords and conservative domain concepts from the student interests and
from a program's name, interests, tags, career paths, courses, and description.
The catalog fields have fixed weights in that order, so an explicit program name
match is stronger than a descriptive-text match. The shared English/Russian/Kazakh
vocabulary is in `recommendation.normalization`; it maps close equivalents such as
`Programming`, `Computer Science`, and `Software engineering` to one computing
concept, but does not infer similarity between unrelated words.

Each response includes `scoreBreakdown`. A `null` component did not participate
because the student answer or comparable catalog field was unavailable. The score is
the weighted average of only non-null components, with their configured weights
renormalized to one. Unknown eligibility is informational rather than a score
penalty; verified ineligibility remains filtered for ordinary recommendations.

## Catalog and provenance

The default repository loads `catalogs/kazakhstan_programs.json`, independently
of the process working directory. It prefers the requested admission year over a
generic record and excludes inactive/demo records. Missing or invalid files fail
application creation instead of silently producing an empty catalog. Returned
records are copies of the validated snapshot. See `../catalogs/README.md` for data
coverage and limitations; restart the backend after editing the file.

For custom integrations only, `PostgreSQLProgramRepository` can be supplied to
`create_app(program_repository=...)`. The optional catalog importer targets
`DATABASE_URL` or `--database <PostgreSQL URL>`; it does not update the default
JSON-backed API. Demo examples remain available in `../examples/programs.demo.json`.

Sources use `url`, `verifiedAt` (ISO date), and `academicYear`. Each exam requirement
has its own source; routes also need a source and `requirementsComplete: true`
before they can establish eligibility. Do not mark a route complete if subject
combinations, interviews, certificates, or other official conditions are unmodeled.
Use `minimum: null` for an unknown minimum.

Fee/grant source keys in the `sources` map are **`tuition_per_year`** and
**`state_grant_available`**. Each scholarship has a name and source. Each deadline
has `taskType` (a roadmap task ID), `date`, optional `routeId`, and source.
Route-specific application deadlines are evaluated only for that route.
Other descriptive fields may use additional entries in `sources`.

A mutable fact is used only when its source academic year matches the profile,
the program's cycle matches, and verification is not in the future. Stale,
missing-source, or unknown facts do not become thresholds, fees, grants, or dates
in decision evidence. Source URLs and dates are metadata supplied by a trusted
curator, not automatic verification of webpage contents; no crawler is included.

## Testing and limitations

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use fictional fixtures, fixed clocks, the existing pytest framework, and
FastAPI TestClient. They cover route AND/OR behavior, source validity, constraints,
funding, score ordering, reweighting, roadmaps, persistence, API validation, and
embedding caching, alongside the original authentication/survey tests.

The bundled catalog has partial admission data. Category/quota policies, subject
combinations, language certificates beyond the modeled exams, competition for
grants, tuition inflation, test scheduling, and individual scholarship conditions
require verified data and further rule modeling. Financial options indicate paths
to investigate, not personal funding eligibility. There is no LLM layer, external
embedding dependency, rate limiter, or persisted roadmap progress in this change.
