# Student Stress Detector API

The backend for [Student Stress Detector](https://stressdetect.netlify.app/), a student project for understanding routine-based stress and tracking self-reported strain.

[Frontend repository](https://github.com/raihanahmadkhan/studentStressDetector)

## Product

Google accounts, explicit daily check-ins, explainable estimates, practical guidance, revisions, descriptive trends, isolated what-if scenarios, exports, and account deletion.

Saving again for a date updates its current values while retaining earlier revisions. Retries do not duplicate revisions or revert newer saves. Guidance is deterministic and grounded in recorded rule activations.

## Calculation

Six routine inputs form academic pressure, recovery deficit, and contextual pressure. Each component uses nine authored fuzzy rules, product inference, summed output sets, and centroid calculation. Their weighted scores form the final estimate. Reported strain stays independent.

The current engine is a product-sum fuzzy system, not a trained classifier. It is an authored heuristic, not a clinical diagnosis or validated stress prediction. There is no active LLM or predictive ML feature.

## Architecture

One FastAPI service with PostgreSQL, SQLAlchemy, Alembic, Pydantic, and Google authentication through Authlib. Frontend and backend deploy separately. Credentials are supplied through the backend host's environment.

## Development

Python 3.12 and PostgreSQL are required. Create a virtual environment, install requirements-dev.txt, and copy .env.example to a private .env. Configure a dedicated local database and a separate disposable database ending in _test. Never point tests at application data.

```sh
python -m pip install -r requirements-dev.txt
alembic upgrade head
uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Windows helpers in scripts/ start an isolated local database and API. Development sign-in is opt-in and unavailable in production.

```sh
python -m pytest
python scripts/verify-migrations.py
python scripts/verify-backup.py
python -m pip check
```

Migration and restore rehearsals modify only the configured disposable test database. Retired schema fields remain for compatibility and historical exports; they do not enable retired features.

See [calculation design](docs/FUZZY-3.md), [guidance](docs/GUIDANCE.md), and [verification](docs/VERIFICATION.md).
