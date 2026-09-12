# AI layer and production readiness

Deployment update: the selected production topology is now Netlify's frontend with an `/api/*` proxy to Render. Follow [PRODUCTION-AUTH.md](PRODUCTION-AUTH.md). Earlier bundled-static release instructions below describe the prior supported packaging option, not the deployment recipe for these two production sites.

## Historical Phase 3 parameter review (superseded for the check-in redesign)

At the Phase 3 review, no parameters were added. The subsequent explicit check-in redesign supersedes that decision: deadline pressure and recovery are now required for new submissions under check-in-2.0.0. See [current design](FUZZY-3.md). The table below records the earlier decision, not the current contract.

| Candidate | Potential value | Decision |
| --- | --- | --- |
| Deadline pressure | Could distinguish a near deadline from total workload | Defer: no real observations, validated questionnaire wording or defensible membership/rule policy demonstrate incremental value over academic workload. Arbitrary new rules would double-count demand. |
| Focus/energy | Could provide subjective context | Defer: focus and energy are different constructs; one combined rating is ambiguous and may overlap the independent strain target. No evidence supports their fuzzy treatment or predictive benefit. |
| Social/personal commitment load | Captures nonacademic demand | Already covered by Other commitments (nonacademic demand 0–10). Adding a duplicate parameter would increase burden and double-count it. |

Future adoption requires a separately defined, optional nullable field; stable wording/range; missingness analysis; a questionnaire version and immutable revision migration; a separately justified fuzzy version with no imputation or clipping; its own analytic counts/units; and as-of ML features tested for incremental benefit. Lack of data is not evidence of benefit.

## Grounded LLM design

The provider is OpenAI's Responses API, called through the existing HTTPX dependency. An explicit configured model supporting structured outputs is required; no model is silently selected or upgraded. `LLM_ENABLED=false` by default. An API key and model are absent in the current installation, so live-provider quality, latency, access and cost have **not** been evaluated.

The LLM performs constrained evidence selection and reflection-question selection. It does not write arbitrary factual prose. This is deliberately narrower than free-form generation: evidence IDs and compatible question IDs are validated, then the backend renders its own exact factual sentences and authored reflection questions. This prevents accepted model output from changing scores, inventing diagnoses, attaching unrelated questions or fabricating history. It does not prove that the model selected the most useful three facts; usefulness still needs human evaluation.

Reference implementation: `app/reflection_facts.py`, `app/llm_provider.py`, `app/reflections.py`. Protocol documentation checked during implementation:

- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Responses request reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- [Provider data controls](https://developers.openai.com/api/docs/guides/your-data)

### Request and lifecycle

1. User explicitly requests a check-in reflection or a recent-period reflection. Client supplies only source identifiers, end date, and expected history version; extra fields (including client facts or prose) are rejected.
2. Backend locks this user, verifies the expected version and ownership, and builds a small fact bundle from saved revisions or deterministic calendar analytics. No account/provider identity, token, arbitrary notes, or complete raw history is sent.
3. Without configuration or explicit consent, return a clearly labeled deterministic template fallback. No provider call or quota reservation occurs.
4. When enabled and consented, reuse a matching validated result by source hash + prompt version + model. An idempotency ledger prevents repeat provider calls for the same attempt. A PostgreSQL user lock serializes reservations across workers: five calls per rolling hour, twenty per rolling day by default, one recent in-flight reservation per user. Failures count. Edits, history deletion and consent changes cannot reset the ledger. Account deletion removes it.
5. Commit the reservation and release the database connection before the external call. One request, no automatic retries, no tools, no external retrieval. Hard deadline defaults to eight seconds; input <=16 KiB, provider output <=32 KiB, generation <=600 tokens. The provider response must be completed, contain exactly one output-text item, and pass schema plus evidence validation.
6. Reacquire the user lock. Verify the session still exists, consent remains valid, the reservation was not revoked, and history version is unchanged. Discard any stale result. Revoking and re-enabling consent while a request runs does not revive it.
7. Store validated output, source snapshot/hash, prompt/model version and allowlisted token counts. Never store raw rejected output. Return selected highlights with evidence IDs and all supporting facts. Edits/deletions invalidate stored reflections; user exports include remaining provenance.

`store=false` requests no Responses application-state persistence. It does **not** guarantee zero provider retention. Settings explains what leaves the app and links the provider policy. Revocation cannot retract an already transmitted request.

### APIs

- GET `/api/ai/status`: authenticated configuration, consent and quotas; no secrets.
- PATCH `/api/ai/consent`: strict boolean `enabled`, existing CSRF/Origin checks. Revocation deletes reflections and marks pending requests revoked.
- POST `/api/ai/reflections`: `kind=checkin` + `checkin_id`, or `kind=weekly` + explicit `end_date`; `expected_history_version` and UUID `Idempotency-Key` required. The weekly view describes seven recent days against the preceding twenty-one, not a predictive forecast.
- GET `/api/ml/status`: always reports serving disabled; does not expose another user's counts.

Fallbacks cover no provider, no consent, quota/in-flight limit, uncertain replay, timeouts, HTTP failures, refusals, incomplete responses, malformed output and unsupported evidence. Dependency or ownership failures still fail with the existing API error contract. They are never disguised as successful AI.

## Predictive ML evaluation path

Target: next calendar day's **independently self-reported strain**, 0–10. The heuristic index is never a label or feature. No synthetic observations are added to application history.

The pipeline is local/read-only and personal: it refuses pooled user data. `scripts/evaluate-ml.py --user-id <UUID> --output <local-json>` loads at most two years/10,000 revisions for that one account. Omit the user argument only if the installation has at most one account. It produces an aggregate report, no serving artifact or prediction endpoint.

Feature cutoff is the end of observation day in the timezone saved with the observation. Use the latest revision actually received before that cutoff. Exclude retrospective reports and later corrections. Target is the first nonmissing next-day report received on that day, after the feature cutoff; missing calendar days do not form a pair. A timezone shift cannot turn an already known target into future evidence. Current strain must be reported, allowing an honest persistence baseline. This complete-case restriction introduces selection bias, explicitly limiting generalization.

Features are sleep, academic load, screen hours, other commitments, deadline pressure, recovery and current reported strain (feature policy next-day-strain-2.0.0). All must be recorded; legacy missing values are excluded, never imputed. Source questionnaire/engine provenance accompanies prepared pairs and is not a predictive feature. Candidate: train-fold-standardized ridge regression, fixed alpha=10, unpenalized intercept, outputs bounded to 0–10. Existing NumPy supplies the small linear solve. Baselines: last reported strain and training-set median. No hyperparameter search or shared/global model is fitted.

Minimum evidence: 120 eligible pairs over 150 calendar days. These are conservative engineering gates, **not scientifically validated sample-size guarantees**. Evaluation uses three expanding-window folds with the last 14 eligible pairs held out in each, a calendar-day embargo, and checks that all training labels were available before the block. Each training fold needs at least 60 pairs. Scaling uses training data only. Report MAE and RMSE for every model/fold, pooled MAE, and a seeded moving-block bootstrap interval (seven consecutive eligible pairs, not necessarily seven consecutive calendar days) for improvement over the strongest baseline.

Candidate gate: at least 10% lower MAE than the strongest baseline, improvement in every fold against both baselines, positive lower bootstrap bound. Passing still returns `candidate_passed_review_required`; serving stays disabled. The bootstrap and three folds are modest evidence, not a promise of future accuracy. Model governance, prospective drift evaluation and a clinical claim are not inferred from this result.

Current real-data report: zero eligible pairs, `insufficient_data`, no training or accuracy score. Synthetic unit fixtures verify leakage handling, missingness, temporal splits, constant-feature stability and refusal to promote a model that cannot beat persistence; they are not predictive evaluation evidence.

## Security and operations

The architecture remains React/TypeScript → FastAPI → PostgreSQL. No queues, Redis, agents, retrieval systems, vector stores or new hosting service is required.

- Exact Origin + CSRF on authenticated mutations; opaque expiring sessions, server-side ownership, secure production cookies, disabled development login in production.
- Trusted Host validation; production CSP, HSTS, anti-framing, nosniff and restrictive browser-feature policy; no API response caching. Swagger/OpenAPI disabled in production.
- ASGI request-body limit 32 KiB including chunked requests, ten-second body deadline. Documented single-process server limit: 64 concurrent requests. Database pool and statement/lock/connect timeouts remain bounded.
- Structured JSON request logs include only request ID, method, route template, status and duration-to-response-headers. No query strings, tokens, SQL, prompts, raw inputs or provider responses. Disable Uvicorn access logs and keep HTTPX/HTTP core debug logging off. Export streaming duration is not represented by the header timing.
- Readiness verifies PostgreSQL and current Alembic revision. Optional provider failure does not make the core application unready. Configured AI does not imply provider reachability; no health probe transmits user data or spends tokens.
- Application AI quotas are durable and account-scoped, not a comprehensive anti-abuse system. Set provider-project spend limits and use the hosting platform's normal request protections before public exposure; no distributed limiter is claimed.

### Same-origin release

Run `scripts/package-release.ps1` from the backend. It builds the sibling frontend and stages only application code, migrations, runtime dependencies, deployment descriptor and compiled `static/` under `.local/releases/`. No `.env`, database, test history, or provider key is copied. The two source repositories remain separate; release packaging combines their artifacts.

Configure production secrets outside the package: PostgreSQL URL, independent session secret, Google OIDC credentials, exact public HTTPS FRONTEND_ORIGIN, COOKIE_SECURE=true, ENABLE_DEV_AUTH=false, APP_ENV=production, FRONTEND_DIST=static. Register the same origin's `/api/auth/callback`. Install pinned requirements, run `alembic upgrade head` once before serving, then run:

```text
uvicorn main:app --host 0.0.0.0 --port <host-port> --workers 1 --limit-concurrency 64 --timeout-keep-alive 5 --no-access-log
```

The host must terminate HTTPS and preserve/forward the configured host correctly. Forwarded-header trust must be limited to the actual trusted proxy, not arbitrary clients. `/api/*` routes and the built frontend are served from this one FastAPI application. The supplied Render descriptor is for a staged release and intentionally fails if the frontend artifact is absent; its pre-deploy migration feature and chosen service plan must be checked with the host before deployment. No live deployment or cloud account was created or modified.

### Backup and restore

`scripts/verify-backup.py` refuses any database not explicitly named `*_test`. It creates a disposable fixture containing two revisions and explanation provenance, performs `pg_dump --format=custom`, restores with `pg_restore --clean --if-exists --single-transaction`, and compares every public table's row count and canonical content hash. It then removes the fixture. Credentials are passed through the subprocess environment, not command arguments or output. The dump/report live under ignored `.local/verification/` and must be treated as sensitive if the test database contains sensitive fixtures. Do not run tests concurrently with restore rehearsal.

This verifies local logical restore correctness, not host-managed retention, encrypted offsite storage, disaster recovery, RPO or RTO. For deployment, take scheduled logical/managed backups through the chosen host, restrict and encrypt their storage, rehearse restoration into a separate database, validate application readiness and record recovery timings. Never restore over the live database as a test. Old backups can retain deleted data until their retention expires; restoring an old backup requires replaying deletions before reopening access.

### Verification

Run the backend full pytest suite against the dedicated test database, migration downgrade/upgrade/schema comparison, backup rehearsal, frontend tests/lint/typecheck/build, and dependency checks. Performance tests record local p50/p95/max for canonical inference and 28-day history/pattern/scenario APIs. They use disposable synthetic fixtures and generous regression budgets; no cloud capacity or LLM-latency SLA is claimed.

Remaining validation: real Google sign-in with configured credentials, live LLM provider quality/usefulness/cost evaluation, managed-host deployment/TLS, external backups, and real-data predictive evaluation. None is represented as completed by mocked or local tests.
