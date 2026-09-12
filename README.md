# Student Workload & Wellbeing — Phases 1–4

This is the existing backend, reorganized as a FastAPI modular monolith. The frontend remains in the sibling `stressed` folder. PostgreSQL is the authoritative history store.

## Implemented

- Google OIDC authorization-code sign-in with PKCE/state/nonce validation; opaque, hashed, expiring database sessions; CSRF and Origin checks.
- Optional loopback-only development account, disabled by default and forbidden in production.
- Explicit daily check-in creation, authenticated reads, immutable application-level revisions, and optimistic edit conflicts.
- Per-user mutation keys, payload fingerprints, database constraints, and transactions protect retries/concurrent writes.
- A canonical three-component, 27-rule product-sum fuzzy heuristic, versioned specification and SHA-256 fingerprint, actual memberships/rule strengths/component centroids and weighted fusion.
- Pydantic request/response contracts and PostgreSQL constraints; new requests enforce the current ranges; legacy observations remain readable without invented new values.
- Initial migration, real PostgreSQL tests, API health endpoints and request metadata logging without sensitive payloads.

Phase 2 adds Timeline/revisions, safe deletion, private data export, timezone preferences, deterministic personal analytics, and read-only hypothetical inference. The sibling frontend provides Today, Timeline, Patterns, Explore, and Settings.

Phases 3–4 add opt-in grounded LLM evidence selection, validated reflections, durable quotas, an as-of personal ML evaluation pipeline, security headers/request bounds, structured logs, same-origin release packaging, and local backup/restore verification. Prediction serving remains disabled. The subsequent focused check-in redesign adds deadline pressure and recovery, with a new questionnaire/model version. See [the current model design](docs/FUZZY-3.md). See [AI design, parameter review, evaluation gates and operations](docs/PHASES-3-4.md) for the complete implementation and limitations.

## Run locally

Use Python 3.12 and Node 20.19+ or Node 22+. Create a virtual environment in this folder and install `requirements-dev.txt` (or only `requirements.txt` for application dependencies). Run commands from this backend folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

If Python is supplied by the Codex bundled runtime, its Python executable can replace `py -3.12` in the first command. Virtual environments, local credentials, and database files are ignored by Git.

For this Windows machine, PostgreSQL 18 binaries already exist. The helper creates an isolated password-protected development cluster on **127.0.0.1:55432** using `.local/postgres`; it does not modify the installed Windows service. It generates `.env` only if absent, creates `wellbeing` and `wellbeing_test`, and enables local development login for this loopback setup:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local-db.ps1
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Otherwise provision PostgreSQL yourself and copy `.env.example` to `.env`, replacing database credentials and the session secret. Never point TEST_DATABASE_URL at real user data. The local helper intentionally refuses mismatched existing configuration.

In a second terminal, start the sibling frontend with `npm ci` then `npm run dev`. Open **http://localhost:5173**. Vite proxies `/api` to the backend. Use the displayed local development account or configure Google sign-in. The app must be reached as localhost when using the documented Origin configuration.

Stop the isolated database with `scripts/stop-local-db.ps1`. Its data remains in `.local/postgres` for the next start. This helper is development infrastructure, not a production backup strategy.

## Google sign-in

Configure OIDC_CLIENT_ID and OIDC_CLIENT_SECRET for a Google **web application**. Register `http://localhost:5173/api/auth/callback` when signing in through Vite; register the production same-origin HTTPS callback for production. Only the `openid` scope is requested; no calendar/email access or provider token storage is required.

Production uses Netlify (`stressdetect.netlify.app`) proxying `/api/*` to Render (`studentstressdetector-backend.onrender.com`), preserving one browser origin. Set `APP_ENV=production`, `COOKIE_SECURE=true`, `ENABLE_DEV_AUTH=false`, `FRONTEND_ORIGIN=https://stressdetect.netlify.app`, and `BACKEND_HOST=studentstressdetector-backend.onrender.com`. Google credentials and an independent random SESSION_SECRET stay on Render. Leave FRONTEND_DIST empty. The callback is `https://stressdetect.netlify.app/api/auth/callback`. Follow [the production authentication setup](docs/PRODUCTION-AUTH.md) for exact Google/Render/Netlify settings, tests and limitations; this supersedes the earlier bundled-static hosting recipe for these sites.

Live Google account sign-in requires credentials configured by the owner; automated tests verify actual signed ID-token validation against a mocked provider. Do not enable the development account on a shared/public machine.

`--no-access-log` prevents default server access logs from recording OAuth authorization-code query strings. The application logs route templates, status, duration, and request IDs without raw inputs or tokens. Provider failures return sanitized messages. Dependencies currently emit non-failing upstream HTTPX deprecation warnings; the current integration is tested without introducing a second HTTP client.

## API contract

Public: GET `/api/auth/config`, `/api/auth/login`, `/api/auth/callback`, `/api/fuzzy-model`, `/api/health/live`, `/api/health/ready`, `/docs`, `/openapi.json`.

Authenticated: GET `/api/me`, GET `/api/check-ins`, GET `/api/check-ins/{id}`, POST `/api/check-ins`, PATCH `/api/check-ins/{id}`, POST `/api/auth/logout`.

POST `/api/auth/dev-login` is available only with explicit enablement, development environment, loopback request host/client, and the exact permitted Origin. A client cannot choose this development user's identity.

All authenticated mutations require the `X-CSRF-Token` returned by `/api/me` and the exact configured `Origin`. Create/edit additionally require an `Idempotency-Key` UUID.

Create body: `observation_date` (YYYY-MM-DD), `timezone` (IANA), `sleep_hours`, `academic_load`, `deadline_pressure`, `screen_hours`, `extracurricular_load`, `recovery`, `reported_strain`.

Hours are JSON numbers from 0–24 in quarter-hour increments. Load/strain ratings are integers 0–10. `reported_strain` must be present but may explicitly be null. Unknown fields, nonfinite values, booleans-as-numbers, invalid timezones, and future dates are rejected. Dates are observation dates, not timestamps. The observation timezone/date remains unchanged on revisions.

Edit body contains the same seven input fields plus `expected_revision`; date and timezone are immutable. It returns 409 if another edit already advanced the revision. Each revision records the server receipt time in UTC, questionnaire version, model version/hash, and assessment. Backfilled observations are marked retrospective.

GET list supports `start_date`, `end_date`, `limit` (1–100), and `cursor` (exclusive observation date). The default is the current 30-day window; a requested window cannot exceed 366 days.

Create returns 201 only after commit. Matching retry returns 200 plus `Idempotency-Replayed: true` and the same original revision/history-version snapshot, even after a subsequent edit. Current GET returns the latest revision. Reusing a key with different contents returns 409. A different key for an existing day also returns 409.

Mutation receipts have a 24-hour accepted retry window. Expired keys are rejected rather than silently replayed as new. Minimal receipts (IDs, payload hash, operation, version and timestamps, no raw input contents) survive history deletion to reject delayed creates/edits. They are retained after the accepted retry window; no automatic purge runs because forgetting a key would allow an arbitrarily late request to recreate a deleted observation. Account deletion removes receipts too. A future bounded receipt-retention policy needs an enforceable request-age contract before cleanup can safely be added.

Mutations acquire a PostgreSQL row lock on the authenticated user, serializing that user's short writes. Other users proceed independently. A shared read lock provides a consistent history-version/record snapshot. Database transactions commit observations, revisions, receipts, and history-version changes together. The deferred current-revision foreign key permits inserting both sides atomically. Application endpoints never update old revisions.

Errors use `{ "error": { "code", "message", "request_id", "fields"? } }`. Expect 401 for missing/expired sessions, 403 for Origin/CSRF rejection, 404 for inaccessible records, 409 for conflicts, 422 for invalid input, and 503 for database/model availability. A timeout/503 can leave save status unconfirmed: retry the identical request key and contents. The client must not append unsaved data to history.

## Fuzzy model

`app/fuzzy.py` implements `fuzzy-3.0.0`: three two-input components, nine complete rules per component, product conjunction, product-scaled fuzzy output sets, sum aggregation, centroid defuzzification, then a 45%/40%/15% weighted blend. This is Larsen-style product-sum inference, **not the previous min/max Mamdani algorithm**. Self-reported strain is excluded. The model is an authored heuristic, not a validated predictor or clinical measurement.

[Current design, full rule matrices, numerical example, provenance and limitations](docs/FUZZY-3.md). `/api/fuzzy-model` exposes its specification/hash. `app/fuzzy_v2.py` preserves the original 13-rule model and its numerical regression tests. Stored legacy revisions keep their original assessments; reading them never reruns inference. Missing new fields remain null. Explicitly editing an old entry requires the new routine inputs and creates a new versioned revision. A current-model scenario cannot compare against an incomplete legacy reference.

## Core product contracts

- DELETE `/api/check-ins/{id}`: `expected_revision` body and UUID `Idempotency-Key`. Deletes every revision atomically; stale revisions return 409. Same-key retries return the original deletion result, never recreate data.
- GET `/api/check-ins/{id}/revisions`: newest first, `before` revision cursor, `limit` 1–30. Both revision and date lists accept `expected_history_version`; a changed snapshot returns 409 instead of mixing pages.
- GET `/api/patterns?end_date=YYYY-MM-DD`: latest owned revisions only, at most 34 calendar days queried, no derived data stored. See `app/analytics.py` for policy `personal-patterns-2.0.0`.
- POST `/api/scenarios`: `{inputs: {the seven input fields}, reference_id?: UUID}`. Frontend passes null strain; it never predicts strain. Reference must belong to the authenticated user. Both reference and hypothetical inputs run through the current fuzzy engine, and difference is returned only if both results are available. No tables, including version registry or receipts, are written.
- PATCH `/api/settings`: `{timezone: IANA string}`; existing observation dates/timezones stay unchanged.
- GET `/api/data/export`: streamed JSON of all owned revisions and referenced model specifications, with a consistent shared-lock snapshot. No session credentials, identity-provider identifiers, or mutation keys. Streaming bounds server memory; large exports hold this user's shared read lock until the stream finishes and can delay that user's writes. Browser download buffers a Blob and uses a 60-second request timeout.
- DELETE `/api/data/history`: `{expected_history_version, confirmation: "DELETE"}` with UUID idempotency key. Clears observations/revisions, increments history version, invalidates derived explanation records, preserves retry receipts.
- DELETE `/api/data/account`: same confirmation/version body; requires sign-in within 15 minutes, cascades all owned data and sessions. A retry after confirmed deletion is unauthenticated (401), never a new account.

All product mutations require the existing CSRF token and exact Origin. Ownership always comes from the session. All own-user writes share the same row lock, including edits/deletes and preference changes; another user's data remains independent.

Analytics uses recent D−6…D and baseline D−27…D−7. Per measure, comparison needs 4/7 recent and 10/21 baseline reports. It returns medians and their difference in original units. Seven-day rolling means require four reports; missing dates and skipped strain stay null. An unusual observation at D uses `abs(value − baseline median) >= max(minimum change, 2.5 × 1.4826 × MAD)`, with minimum changes sleep 1h, screen 2h, other ratings 2 points. Three consecutive calendar days in one direction indicate persistence; a missing date breaks it. Low sleep/high academic demand co-occurrence is descriptive only. Fuzzy scores are excluded from personal trends to avoid conflating model versions.

## Verify the complete implementation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m alembic check
.\.venv\Scripts\python.exe scripts/verify-migrations.py
```

`verify-migrations.py` intentionally resets only the configured database ending in `_test`: downgrade to base, upgrade to head, compare model/schema. The test fixtures also reset only that dedicated database. Never put valuable data there. Pure fuzzy tests can run separately without PostgreSQL with `python -m pytest tests/test_fuzzy.py`.

Coverage includes independent centroid references, boundaries, complete rule coverage, concurrency isolation, signed OIDC claims, session rotation/expiry/CSRF, user ownership, validation, rollback, retry deduplication, stale edit/delete races, deletion cascades, export privacy/provenance, scenario no-write invariants, and analytic windows/missingness/count thresholds. Synthetic fixtures exist only in the disposable test database, not application history or training data.

Local backup/restore has a reproducible test-only rehearsal. Live deployment, managed-host disaster recovery, clinical validation and real-data predictive accuracy are not claimed. LLM selection is tested with mocked provider responses; live quality requires configured credentials and evaluation.
