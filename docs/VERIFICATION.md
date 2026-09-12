# Check-in redesign verification — 2026-09-12

Current implementation: [fuzzy-3.0.0 design](FUZZY-3.md).

| Check | Result |
| --- | --- |
| Complete backend suite | 172 passed; three upstream deprecation warnings. Command: `python -m pytest -q -o cache_dir=.local/pytest-cache` (avoids an existing unwritable default cache) |
| Complete frontend suite | 29 passed |
| ESLint / TypeScript / production build | Passed; main JS 404.79 kB, 138.02 kB gzip |
| Migration | Dedicated test downgrade/fresh upgrade/schema comparison passed; application upgraded to 0003_checkin_inputs; Alembic check clean |
| Backup/restore | Test-only roundtrip preserved table counts and content hashes, including new questionnaire fields and revisions |
| Browser keyboard | Native Home then ArrowRight produced 0.25 hours; no Save action performed |
| Responsive check | At 390 px viewport all seven sliders were 256 px wide; document width 375 px, no horizontal overflow |
| Browser limitation | Navigation from the unsaved QA draft opened the existing discard confirmation; browser automation stalled while handling that dialog. A complete browser scenario flow was not verified in this run. Scenario isolation and rendering passed automated API/frontend tests. Viewport override was reset. |
| Existing application data | One saved fuzzy-2.0.0/check-in-1.0.0 revision remains under its original provenance. No synthetic observation was saved to the application database |
| Real-data ML gate | Zero eligible pairs; insufficient_data; serving disabled |

Current backend additions verify all input boundaries, 64 corners, positive partition coverage, all 27 activated rules, direction consistency, deterministic scores, actual component evidence facts, legacy revisions/exports and missing feature handling. Fuzzy inference p95: 6.633 ms (200 local sequential samples); scenario p95: 42.596 ms (20 samples). This is a local regression check, not a production SLA.

The LLM/provider architecture is unchanged. No new live-provider quality or predictive accuracy claim is made.

---

## Historical Phase 3–4 verification

# Verification record â€” 2026-09-12

Implementation and policy: [Phases 3â€“4](PHASES-3-4.md).

| Check | Result |
| --- | --- |
| Full backend suite | 143 tests passed against dedicated PostgreSQL; three non-failing upstream deprecation warnings |
| Frontend suite | 26 tests passed |
| TypeScript + production build | Passed; approximately 402 kB main JS, 137 kB gzip |
| ESLint | Passed, no warnings |
| npm production dependency audit | Zero reported vulnerabilities |
| Python dependency consistency | `pip check` passed (not a vulnerability audit) |
| Alembic | Downgrade, fresh upgrade to 0002_ai_requests, schema comparison passed in dedicated test database |
| Local pg_dump/pg_restore | All nine public tables' row counts and content hashes matched; fixture included two revisions and reflection provenance |
| Staged release | Production middleware, built HTML/JS/CSS, same-origin API readiness, unauthorized AI protection, disabled public API docs verified |
| Browser | Live application startup, missing-data reflection fallback/evidence, disabled unconfigured AI consent, keyboard navigation and 390 px layout verified |
| Application history | Zero observations; test fixtures were never inserted into the application database |

## AI evaluation scope

27 backend reflection tests passed: no consent/configuration, source ownership, strict client contract, successful structured-response protocol, cache/provenance/export, seven malformed/hallucinated-output cases, durable quota, duplicate in-flight reservation, edit/delete/logout/revocation races (including revoke/re-enable), provider refusal/incomplete/invalid JSON, deadline, oversized response, and HTTP 429/500/redirect failures without retries. Six frontend AI tests cover explicit action, labeled evidence fallback, retry keys, late responses, explicit consent and source conflict.

All seven injected malformed/hallucinated selections fell back to backend-verified wording. This is a test-case result, not a measured population-wide hallucination rate. The renderer never accepts arbitrary provider prose. It still requires human evaluation of whether selected evidence and authored questions are useful. No live model was called, because neither API credentials nor a model are configured. No live quality, access, cost, provider latency, or retention behavior was empirically evaluated.

## ML evaluation scope

The real local account has **zero eligible next-day pairs**. Result: `insufficient_data`, no fitted model, no accuracy claim, serving disabled. Six synthetic pipeline tests verify as-of revision handling, independent/missing/zero labels, user isolation, refusal to train without evidence, temporal embargo/baseline gating, and training-only scaling. They are mechanics tests, not evidence that predictions work for students.

The evaluation policy compares ridge against persistence and training median, uses expanding chronological folds, and requires a conservative improvement gate plus review. It cannot produce a user-facing prediction merely because a script exists.

## Targeted local performance

Recorded during the full suite, using sequential FastAPI TestClient requests and real local PostgreSQL with 28 disposable synthetic observations:

| Operation | Samples | p50 ms | p95 ms |
| --- | ---: | ---: | ---: |
| Canonical fuzzy inference | 200 | 2.127 | 4.084 |
| History, 28 days | 20 | 71.603 | 97.725 |
| Patterns, 28 days | 20 | 77.752 | 100.559 |
| Scenario | 20 | 20.836 | 25.221 |

These are local regression measurements; neither concurrency capacity, external network latency nor a cloud SLA is established. Fresh runs overwrite ignored `.local/verification/performance-report.json`; normal machine load changes results.

## Unverified external requirements

- Live Google sign-in and LLM usefulness/cost require configured owner credentials.
- The release is staged and locally verified, not deployed. Host TLS/proxy policy, service-plan migration support and public traffic protections need host-specific verification.
- Backup verification is a logical local roundtrip, not encrypted offsite retention or a managed-host disaster-recovery drill. RPO/RTO are not claimed.
- The fuzzy index remains an unvalidated heuristic. No added parameters, clinical validation, synthetic training labels or enabled predictive model are claimed.
