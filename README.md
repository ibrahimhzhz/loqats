# LoqATS Backend

Multi-tenant, AI-powered Applicant Tracking System (ATS) built with FastAPI, Celery, PostgreSQL/SQLite, Redis, and Google Vertex AI.

LoqATS automates resume screening with:
- structured AI extraction from resumes and job descriptions,
- deterministic scoring logic in Python,
- semantic skill matching with embeddings,
- a recruiter pipeline with stage history and auditability,
- a public careers portal for candidate applications.

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Features](#features)
4. [Tech Stack](#tech-stack)
5. [Project Structure](#project-structure)
6. [Quick Start (Local)](#quick-start-local)
7. [Quick Start (Docker Compose)](#quick-start-docker-compose)
8. [Environment Variables](#environment-variables)
9. [How Scoring Works](#how-scoring-works)
10. [API Map](#api-map)
11. [Data Model](#data-model)
12. [Migrations](#migrations)
13. [Deployment on Railway](#deployment-on-railway)
14. [Security Notes](#security-notes)
15. [Troubleshooting](#troubleshooting)

## Overview

LoqATS backend serves both:
- recruiter-facing workflows (`/`, `/login`, authenticated APIs), and
- public candidate workflows (`/apply/{job_id}`, `/api/public/*`).

Resume processing is asynchronous:
- API receives submission,
- Celery worker parses PDF and extracts facts,
- deterministic scorer computes results,
- DB stores applicants and job aggregates,
- UI polls status and renders pipeline data.

## Architecture

```text
Frontend (HTML/JS)
        |
        v
FastAPI (main.py, routers/*)
        |
        +--> PostgreSQL/SQLite (SQLAlchemy models)
        |
        +--> Redis (Celery broker/backend)
                |
                v
            Celery Worker (services/tasks.py)
                |
                v
         Vertex AI Gemini + Embeddings
```

## Features

- Multi-tenant isolation using `company_id` on all protected data paths.
- JWT auth with issuer/audience validation and role checks.
- Public careers portal with anti-bot and rate-limit protections.
- Async resume processing with Celery (`process_resume`, `process_public_resume`).
- Structured extraction pipeline from resume text and JD text.
- Deterministic scoring engine (`scoring.py`) with explainable breakdown.
- Semantic skill matching via Vertex embeddings (`text-embedding-004`) plus alias matching.
- Job-level embedding reuse for efficiency.
- Pipeline stage transitions with immutable history logs.
- Startup-safe, idempotent schema migration helpers.

## Tech Stack

- Python, FastAPI, Uvicorn
- SQLAlchemy ORM
- Celery + Redis
- PostgreSQL (production), SQLite (default local)
- Google Vertex AI (Gemini and text embeddings)
- PyMuPDF for PDF text extraction
- Alembic for migration versioning

## Project Structure

```text
ats_backend/
  main.py
  database.py
  models.py
  schemas.py
  scoring.py
  routers/
    auth.py
    jobs.py
    applicants.py
    public.py
    pipeline.py
  services/
    ai_engine.py
    auth.py
    pdf_parser.py
    tasks.py
    job_tracker.py
  core/
    celery_app.py
  middleware/
    security.py
  frontend/
    index.html
    login.html
    apply.html
    static/js/auth.js
  alembic/versions/
  Dockerfile
  docker-compose.yml
  Procfile
  requirements.txt
```

## Quick Start (Local)

### 1. Prerequisites

- Python 3.11+
- Redis (required for Celery tasks)
- Optional: PostgreSQL (if you do not want SQLite)
- Google Cloud credentials (required for AI extraction/matching)

### 2. Setup virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Create `.env`

```env
# Core
DATABASE_URL=sqlite:///./ats.db
REDIS_URL=redis://localhost:6379/0

# Auth
SECRET_KEY=replace_with_at_least_32_characters
ACCESS_TOKEN_EXPIRE_MINUTES=480
JWT_ISSUER=ats-backend
JWT_AUDIENCE=ats-client
APP_ENV=development
ALLOW_INSECURE_DEV_SECRET=false

# AI
GCP_PROJECT_ID=your-gcp-project-id
GCP_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json

# Optional runtime tuning
JOB_TRACKER_CLEANUP_INTERVAL_SECONDS=1800
JOB_TRACKER_MAX_AGE_HOURS=24
ATS_RESUME_TMP_DIR=
```

Credential alternatives for AI are also supported:
- `GOOGLE_APPLICATION_CREDENTIALS_JSON`
- `GOOGLE_CREDENTIALS_BASE64`
- `GOOGLE_CREDENTIALS_JSON`

### 4. Start Redis

If you do not have Redis running locally:

```powershell
docker run --name ats-redis -p 6379:6379 redis:alpine
```

### 5. Start API and worker

Terminal 1:

```powershell
uvicorn main:app --reload --port 8001
```

Terminal 2:

```powershell
celery -A core.celery_app worker --loglevel=info
```

### 6. Open app

- Dashboard: `http://localhost:8001/`
- Login: `http://localhost:8001/login`
- Swagger docs: `http://localhost:8001/docs`
- Health: `http://localhost:8001/api/health`

## Quick Start (Docker Compose)

Use this for full-stack local run (DB + Redis + API + worker):

```bash
docker-compose up --build
```

Services:
- `db` (PostgreSQL 15)
- `redis`
- `web` (`uvicorn main:app --host 0.0.0.0 --port 8000`)
- `worker` (`celery -A core.celery_app worker --loglevel=info`)

Compose reads environment from `.env.docker`.

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | No | `sqlite:///./ats.db` | SQLAlchemy connection string |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Celery broker and backend |
| `SECRET_KEY` | Yes (prod) | none | JWT signing key (>=32 chars) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `480` | JWT expiry minutes |
| `JWT_ISSUER` | No | `ats-backend` | JWT issuer claim |
| `JWT_AUDIENCE` | No | `ats-client` | JWT audience claim |
| `APP_ENV` | No | `development` | Environment mode |
| `ALLOW_INSECURE_DEV_SECRET` | No | `false` | Allow weak dev secret only for local dev |
| `GCP_PROJECT_ID` | Yes (AI) | none | Vertex AI project |
| `GCP_LOCATION` | No | `us-central1` | Vertex AI region |
| `GOOGLE_APPLICATION_CREDENTIALS` | Yes (AI) | none | Path to service account JSON |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Alt (AI) | none | Inline JSON credentials |
| `GOOGLE_CREDENTIALS_BASE64` | Alt (AI) | none | Base64 JSON credentials |
| `GOOGLE_CREDENTIALS_JSON` | Alt (AI) | none | Inline JSON credentials |
| `JOB_TRACKER_CLEANUP_INTERVAL_SECONDS` | No | `1800` | Cleanup loop interval |
| `JOB_TRACKER_MAX_AGE_HOURS` | No | `24` | Job tracker retention |
| `ATS_RESUME_TMP_DIR` | No | system temp | Temporary ZIP extraction path |
| `PORT` | No | `8000` | Runtime port (used in container/Procfile) |

## How Scoring Works

There are two scoring paths:

- Primary async pipeline (`services/tasks.py`) uses `scoring.py`.
- Legacy direct endpoint (`POST /applicants/apply/{job_id}`) uses compatibility scoring in `services/ai_engine.py`.

### Primary scorer (`scoring.py`)

Contributing weights:
- Skills: 50
- Experience: 30
- Education: 20

Informational-only components (not added to final numeric score):
- Role level fit
- Application quality

Skill matching order:
1. Canonical alias match (`SKILL_ALIASES`)
2. Semantic fallback using embeddings if available
   - model: `text-embedding-004`
   - similarity threshold: `0.82`

Embedding storage:
- Applicant embeddings: `applicants.skill_embeddings`
- Job required embeddings: `jobs.required_skill_embeddings`

Buckets:
- Hard knockout -> `Filtered Out`
- Score >= 65 -> `Strong Match`
- Otherwise -> `Potential`

Mapped applicant statuses used in APIs/UI:
- `shortlisted`, `review`, `knockout` (and `rejected` can still appear in legacy/manual flows)

## API Map

### Auth (`/auth`)

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`

### Jobs (`/jobs`)

- `POST /jobs/`
- `GET /jobs/`
- `GET /jobs/{job_id}`
- `PUT /jobs/{job_id}`
- `DELETE /jobs/{job_id}` (admin)
- `PATCH /jobs/{job_id}/status` (admin)
- `PUT /jobs/{job_id}/form-config`
- `GET /jobs/dashboard/stats`
- `GET /jobs/{job_id}/stats`

### Applicants (`/applicants`)

- `POST /applicants/apply/{job_id}` (legacy direct apply)
- `GET /applicants/`
- `GET /applicants/{applicant_id}`
- `PUT /applicants/{applicant_id}/status`
- `PUT /applicants/bulk/status`
- `DELETE /applicants/{applicant_id}` (admin)
- `GET /applicants/{applicant_id}/download-resume`

### Public careers (`/api/public`)

- `GET /api/public/job/{job_id}`
- `POST /api/public/apply/{job_id}`

### Pipeline (`/api/jobs`)

- `PATCH /api/jobs/{job_id}/applicants/{applicant_id}/stage`
- `GET /api/jobs/{job_id}/applicants/{applicant_id}/history`
- `GET /api/jobs/{job_id}/pipeline`

### Bulk processing and app routes

- `GET /` (dashboard)
- `GET /login`
- `GET /apply/{job_id}`
- `GET /api/health`
- `POST /api/bulk-screen`
- `GET /api/job/{job_id}`
- `GET /api/jobs`

## Data Model

Primary tables:
- `companies`
- `users`
- `jobs`
- `applicants`
- `applicant_stage_log`

Important fields:
- Multi-tenancy: `jobs.company_id`, `applicants.company_id`
- Job tracking: `jobs.tracking_id`, `jobs.status`, `jobs.results`
- Pipeline: `applicants.pipeline_stage`, `applicants.stage_updated_at`
- Embeddings: `jobs.required_skill_embeddings`, `applicants.skill_embeddings`

Notable constraints/indexes:
- Unique applicant per job/email: `uq_applicants_job_email`
- Tenant query indexes on jobs/applicants
- Stage log lookup index

## Migrations

Migration strategy is hybrid:

- `Base.metadata.create_all(bind=engine)` creates missing tables.
- `run_migrations()` in `database.py` applies idempotent column/index updates at startup.
- Alembic versions are tracked in `alembic/versions/`.

Latest embedding migration:
- `alembic/versions/20260306_01_add_skill_embedding_columns.py`

## Deployment on Railway

### Services

Run both process types from `Procfile`:

- `web`: `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`
- `worker`: `celery -A core.celery_app worker --loglevel=info`

### Required add-ons

- PostgreSQL
- Redis

### Required env vars (minimum)

- `DATABASE_URL`
- `REDIS_URL`
- `SECRET_KEY`
- `JWT_ISSUER`
- `JWT_AUDIENCE`
- `APP_ENV=production`
- AI vars if using extraction in production:
  - `GCP_PROJECT_ID`
  - `GCP_LOCATION`
  - One credentials method (`GOOGLE_APPLICATION_CREDENTIALS` or JSON variants)

### Verify after deploy

- Health endpoint: `/api/health`
- Web process is up and serving `/`
- Worker process connected to Redis and consuming Celery tasks

## Security Notes

- Tenant isolation is enforced by server-side `company_id` scoping.
- JWT validation checks signature, expiration, issuer, and audience.
- Passwords are stored as bcrypt hashes.
- Security headers middleware is enabled globally.
- Public apply endpoint includes:
  - honeypot trap field,
  - per-IP and per-email rate limits,
  - duplicate-submission suppression behavior.

Important:
- `GET /admin/nuke-jobs-table` exists in `main.py`. Treat this as a development-only endpoint and remove/protect it before broad production exposure.

## Troubleshooting

- `503 AI extraction unavailable`:
  - Check `GCP_PROJECT_ID`, region, and credentials env vars.
- Celery tasks not running:
  - Verify `REDIS_URL` and ensure worker process is running.
- Auth failures (`401`):
  - Check `SECRET_KEY`, `JWT_ISSUER`, `JWT_AUDIENCE`, token expiry.
- Public apply blocked:
  - Validate job visibility/active status/deadline and required fields.
- DB schema mismatch:
  - Restart app so `run_migrations()` can apply idempotent updates.
