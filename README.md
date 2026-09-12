# Student Wellbeing — Backend API

FastAPI backend for a daily student check-in app that turns self-reported routine data into a transparent, explainable wellbeing index.

**Live Demo:** https://stressdetect.netlify.app/

## Overview

Students log a short daily check-in — sleep, academic workload, deadline pressure, screen time, extracurricular load, and recovery time. The backend scores each check-in with a rule-based fuzzy inference model and returns a routine index the student can inspect, not a black-box number. The model is an authored heuristic for reflection, not a clinical or diagnostic tool.

The [frontend](https://github.com/raihanahmadkhan/studentStressDetector) is a separate React/TypeScript app that consumes this API.

## Features

- Google sign-in with secure, server-side sessions
- Daily check-ins with a full edit history, so past entries are never silently overwritten
- Personal trends and pattern detection over time
- "What-if" scenario exploration against the current model, without affecting saved history
- Self-service data export and account deletion
- A transparent, versioned fuzzy-logic scoring model with published rules — no hidden weights
- Optional, opt-in AI-assisted reflections on a student's own check-in history

## Architecture

A single FastAPI service backed by PostgreSQL, with the frontend and backend deployed and versioned independently. Schema changes are managed through Alembic migrations. The scoring model is implemented as a standalone, versioned module so past assessments stay reproducible even as the model evolves.

## Tech Stack

- **API:** FastAPI, Pydantic
- **Database:** PostgreSQL, SQLAlchemy, Alembic
- **Inference:** NumPy, SciPy, scikit-fuzzy
- **Auth:** Google sign-in (Authlib)
- **AI (optional):** OpenAI, used only for opt-in reflection summaries

## Local Development

Requires Python 3.12 and a local PostgreSQL instance.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp .env.example .env   # fill in your local database and session settings
alembic upgrade head
uvicorn main:app --reload
```

Run the [frontend](https://github.com/raihanahmadkhan/studentStressDetector) separately and point it at this API.

## Limitations

- The scoring model is a hand-authored heuristic, not a clinically validated predictor of stress
- AI-assisted reflections require a personally configured API key and are disabled by default
- Built as a personal/portfolio project; not intended for production-scale traffic
