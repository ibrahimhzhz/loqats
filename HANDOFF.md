# LoqATS — Project Handoff Brief

Paste this whole document to your assistant, and **upload the project zip
(`loq_ats_v2.zip`) too** — this brief is orientation, but for any concrete code
change the assistant needs to see the actual file. When you ask for an edit,
either upload the zip or paste the specific file(s) involved.

---

## 1. What this is

LoqATS is an AI-powered Applicant Tracking System (ATS) sold as a SaaS to
hiring teams and agencies. A recruiter posts a job, bulk-uploads résumés (a ZIP
of PDFs) or receives applications through a public careers page, and the system
extracts facts from each résumé, scores every candidate against the job with
**transparent, deterministic math**, and ranks them. The product's core selling
point is that every score comes with an inspectable breakdown — the AI extracts
facts, but Python does the scoring, and a human makes the decision.

It also handles the full hiring flow after screening: a Kanban pipeline,
interview scheduling, offers, team collaboration, and an audit trail.

## 2. Tech stack

- **Backend:** Python 3.11, FastAPI, SQLAlchemy ORM.
- **Async work:** Celery worker with Redis as broker/backend. Résumé screening
  runs in the worker, not in the web request.
- **Database:** PostgreSQL in production, SQLite in local dev (controlled by
  `DATABASE_URL`).
- **AI:** Google Vertex AI (Gemini) for résumé fact-extraction and for skill
  embeddings (semantic skill matching). Accessed via a service-account JSON.
- **Auth:** JWT (python-jose) + bcrypt. Multi-tenant.
- **Frontend:** server-rendered HTML + vanilla JS, served by FastAPI itself
  (no separate SPA build). A shared CSS design system.
- **Deploy target:** Railway (Docker). Procfile defines two processes.

## 3. How it runs (architecture)

There are **two long-running processes**, both from the same codebase:

- **web** — `uvicorn main:app`. Serves the JSON API and the HTML pages. On
  startup it creates tables and runs migrations automatically (in `main.py`).
- **worker** — `celery -A core.celery_app worker`. Consumes screening jobs from
  Redis and does the heavy lifting (PDF parse → AI extraction → embeddings →
  deterministic scoring → persist). **If the worker isn't running, uploads
  queue forever and nothing gets processed.** This is the #1 gotcha.

Request flow for bulk screening: web receives a ZIP → enqueues one Celery task
per résumé → worker processes each → results aggregate onto the job. Public
applications and single-résumé uploads go through the same scoring path.

## 4. Repository map (where things live)

Top level:
- `main.py` — FastAPI app, route registration, startup, `/api/health`,
  `/api/bulk-screen`, page routes (`/`, `/console`, `/login`, `/apply/{id}`).
- `models.py` — all SQLAlchemy models (Company, User, Job, Applicant,
  ApplicantStageLog, and the newer Comment, Scorecard, Interview, Offer,
  AuditLog, EmailLog, EmailTemplate).
- `schemas.py` — Pydantic request/response models.
- `database.py` — engine/session, `get_db`, and `run_migrations()` (idempotent
  ALTER TABLEs + indexes; SQLite- and Postgres-safe).
- `scoring.py` — **the deterministic scoring engine.** Skill matching
  (alias → fuzzy → semantic), experience/education/role scoring, knockout
  filters, buckets, and status mapping. Pure Python, no network.
- `requirements.txt`, `Dockerfile`, `Procfile`, `.env.example`.
- `start-web.sh` / `start-worker.sh` — entrypoints that decode the base64 GCP
  key into a file before launching (for production, see DEPLOYMENT.md §3.3).

`core/`
- `celery_app.py` — Celery app; includes `services.tasks`.

`services/`
- `ai_engine.py` — Vertex AI calls: résumé fact extraction, JD requirement
  extraction, embeddings, candidate summaries. Raises
  `AIServiceUnavailableError` on API failure (so tasks retry rather than
  mis-scoring). Contains legacy scoring functions that are **no longer used**.
- `tasks.py` — Celery tasks: `process_resume`, `process_public_resume`,
  `aggregate_job_results`, `extract_jd_requirements_task`. Retries on AI
  outages with backoff; stores résumés in object storage.
- `auth.py` — password hashing, JWT create/decode, `get_current_user`
  dependency, `SECRET_KEY` validation.
- `storage.py` — résumé file storage abstraction (local / S3 / GCS).
- `email.py` — transactional email (Resend / SendGrid / SMTP) with default +
  per-company templates; safe logged no-op when unconfigured.
- `audit.py` — append-only audit-log helper.
- `pdf_parser.py` — PDF → text (PyMuPDF).

`routers/`
- `auth.py` (`/auth`), `jobs.py`, `applicants.py`, `pipeline.py`,
  `public.py` (public careers portal, no auth).
- Newer: `collaboration.py` (team, comments, scorecards), `interviews.py`
  (scheduling + `.ics`), `offers.py`, `talent_pool.py` (cross-job search),
  `bulk.py` (bulk actions), `exports.py` (CSV), `admin_tools.py` (audit +
  email templates).

`frontend/`
- `index.html` — the original single-file dashboard (~3,800 lines), served at
  `/` and `/classic`.
- `login.html` — original login (served at `/login/classic`).
- `apply.html` — public application page.
- `login_v2.html` — revamped login (served at `/login`).
- `console.html` — new recruiter console (served at `/console`), drives the
  newer APIs.
- `static/css/loqats.css` — the design system.
- `static/js/auth.js` — token helpers (`localStorage` key `access_token`).

## 5. Invariants the assistant must not break

These are load-bearing. Preserve them in any change:

1. **Multi-tenancy.** Every table has `company_id`. Every authenticated query
   filters by `current_user.company_id`. Never return or mutate rows across
   tenants. New endpoints take `current_user = Depends(get_current_user)` and
   scope by company.
2. **One scoring path.** All three résumé intake routes (bulk, public, single
   apply) must produce identical scores via `scoring.calculate_pipeline_score` /
   `calculate_deterministic_score`, the shared `evaluate_knockout_filters`, and
   the shared buckets (shortlist threshold = score ≥ 65). Do **not** reintroduce
   the old per-endpoint scoring logic in `ai_engine.py`.
3. **AI failures must never reject candidates.** Extraction raises
   `AIServiceUnavailableError`; tasks retry with backoff. Don't swallow it into
   an empty "unreadable" fallback.
4. **Résumés live in object storage, not the DB.** New records set
   `resume_file_key`; downloads prefer the key and fall back to the legacy BLOB.
   Don't go back to storing PDFs as DB BLOBs.
5. **Email is a safe no-op when unconfigured.** Never let a missing email
   provider raise into a request. Log to `email_log` instead.
6. **Audit consequential actions** via `services/audit.record(...)`.
7. **Never commit secrets.** No API keys, service-account JSON, `.env`, or the
   SQLite DB in code or git. Read them from environment variables.

## 6. Environment variables

Core:
- `SECRET_KEY` — 32+ char random hex. Required in production (app refuses to
  boot with a weak one unless `APP_ENV` is dev and `ALLOW_INSECURE_DEV_SECRET=true`).
- `DATABASE_URL` — `postgresql://…` in prod (fix `postgres://` → `postgresql://`
  if your provider gives the short scheme); defaults to local SQLite.
- `REDIS_URL` — Celery broker/backend.
- `APP_ENV` — `production` | `development`.
- `ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_ISSUER`, `JWT_AUDIENCE` — optional auth tuning.
- `CORS_ORIGINS` — comma-separated; only needed if the frontend is on a
  different domain than the API (bundled UI is same-origin).
- `APP_BASE_URL` — used in email links.

Vertex AI:
- `GOOGLE_APPLICATION_CREDENTIALS` — path to the service-account JSON (e.g.
  `/app/vertex-key.json`).
- `GOOGLE_CLOUD_PROJECT` — GCP project id.
- (In production the key is provided as base64 in `GCP_SA_KEY_B64` and written
  to a file by the start scripts.)

Résumé storage:
- `STORAGE_BACKEND` — `local` | `s3` | `gcs`. **Must be `s3` or `gcs` in
  production** (Railway's disk is ephemeral; local storage loses files).
- `LOCAL_STORAGE_DIR` (local), `S3_BUCKET` + `S3_ENDPOINT_URL` + AWS creds
  (s3/R2), or `GCS_BUCKET` (gcs).

Email (all optional; unset = notifications logged, not sent):
- `EMAIL_FROM`, and one of: `RESEND_API_KEY`, `SENDGRID_API_KEY`, or
  `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_USE_TLS`.

Full annotated list is in `.env.example`.

## 7. Run locally (Windows PowerShell)

The developer is on **Windows PowerShell** — give PowerShell commands, not bash.
Two processes, two terminals, both with the venv active:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# terminal 1 (web — also runs migrations on boot):
uvicorn main:app --reload --port 8000
# terminal 2 (worker — note --pool=solo is required on Windows):
celery -A core.celery_app worker --loglevel=info --pool=solo
```

Needs a local `.env` (copy `.env.example`), a Redis instance
(`docker run -d -p 6379:6379 redis:7` or an Upstash URL), and the Vertex key at
the path in `GOOGLE_APPLICATION_CREDENTIALS`. Then open
`http://localhost:8000/login`.

## 8. Deploy (summary — full runbook is in DEPLOYMENT.md)

Production needs **four things**: Postgres, Redis, a **web** service, and a
**worker** service, plus object storage (Cloudflare R2 is easy/cheap) and an
email provider (Resend is easiest). On Railway:

1. Add Postgres + Redis.
2. Deploy the repo as the **web** service; set env vars (§6); provide the GCP
   key via base64 (`GCP_SA_KEY_B64`) using `start-web.sh`; set `STORAGE_BACKEND=s3`.
3. Add a **second** service from the same repo with start command
   `sh start-worker.sh` (or `celery -A core.celery_app worker …`) and the **same**
   env vars.
4. Verify `/api/health` returns `ai_ready: true`, then use `/login`.

**Before deploying:** rotate the GCP service-account key and generate a fresh
`SECRET_KEY`, and make sure `vertex-key.json`, `.env`, and `ats.db` are neither
tracked in git nor in history. (See DEPLOYMENT.md Phase 0.)

## 9. Known limitations / roadmap (fair game to work on)

- No automated test suite yet (the scoring engine is pure Python and very
  testable — a good first addition).
- Migrations auto-run on boot via `create_all` + `run_migrations()`; fine for
  launch, but move to Alembic before there's production data you can't lose.
- The public apply endpoint has no rate limiting (obvious abuse target).
- No bias/adverse-impact reporting yet (relevant for NYC Local Law 144 / EU AI
  Act; never extract protected attributes — expose score-distribution reports
  instead).
- The 3,800-line `index.html` should be migrated onto the new design system
  incrementally, not rewritten in one pass.
- `@app.on_event` startup/shutdown hooks are deprecated in newer FastAPI; migrate
  to lifespan handlers eventually.

## 10. Ground rules for the assistant

- The developer is on **Windows PowerShell** — all shell commands in PowerShell syntax.
- Preserve the invariants in §5 (multi-tenancy, single scoring path, no secrets
  in code, storage/email/audit behavior).
- Before any large rewrite, explain the plan and the risk, and prefer the
  smallest change that solves the problem. Don't rewrite working modules
  wholesale without being asked.
- When editing, show the specific file and the exact change; don't guess at file
  contents you haven't been shown — ask for the file or work from the uploaded zip.
- Be honest about tradeoffs and what you're unsure of. Flag anything that could
  break tenant isolation, scoring consistency, or the deployment.
