# Shipping verification

## Repeatable checks

Use a dedicated disposable PostgreSQL database configured as TEST_DATABASE_URL, with a name ending in _test. Never use application data.

```sh
python scripts/verify-migrations.py
python scripts/verify-backup.py
python -m pytest -q -o cache_dir=.local/pytest-cache
python -m ruff check app migrations scripts tests --select E9,F63,F7,F82
python -m compileall -q app migrations
python -m pip check
```

Use pip-audit for Python dependency advisory checks. In the frontend, run npm ci, npm test, npm run lint, npm run typecheck, npm run build, and npm audit.

## Coverage

Tests exercise authentication, callback validation, ownership, CSRF, constrained inputs, transaction retries, concurrent saves, revisions, deletion, exports, scenario isolation, historical model compatibility, fuzzy coverage, deterministic guidance, request limits, and production gates. Backup rehearsal compares table counts and content hashes after restoration.

Frontend tests cover forms, result rendering, session recovery, analytics states, guidance, report generation and escaping. Performance checks use synthetic observations in the disposable database; their timings are not a cloud service-level guarantee.

## Operational limits

Request limits are bounded and in-process for the single-worker deployment. They reset on restart and do not replace upstream traffic protection. Production PostgreSQL requires TLS; certificate verification depends on the provider's connection and CA configuration.

Local checks cannot inspect host secrets, historical hosting logs, Google publishing settings, provider backup retention, or prove an authenticated production flow without Google sign-in. Readiness and public authentication configuration can be checked without account access.

Retired AI/ML runtime modules are removed. Historical schema fields and migrations remain for existing records and exports. The absence of retired API routes is tested. There are no file-upload endpoints.
