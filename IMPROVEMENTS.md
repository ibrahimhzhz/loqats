# LoqATS — Logic & Reasoning Improvements (July 2026)

This document records what was changed in this revision, why each change matters, and what still stands between the current codebase and a product you can confidently sell.

## 1. One scoring engine instead of two

The codebase contained two independent scoring engines. The Celery pipeline (bulk screening, public applications, reprocessing) used `scoring.py` with 50/30/20 weights, semantic matching, signals, and buckets at a 65 threshold. The single-resume endpoint `/applicants/apply/{job_id}` used a legacy engine inside `ai_engine.py` with 40/40/20 weights, its own knockout rules (experience floor of `min − 0.5`, a 50% skill-coverage floor), and status thresholds at 80/60. The same resume could be "shortlisted" through one door and "rejected" through the other, which is indefensible in front of a customer and a genuine fairness problem. The single-resume path now runs the exact pipeline the bulk path runs: normalize JD requirements, extract facts, generate embeddings, score with `scoring.calculate_deterministic_score`, evaluate the shared knockout filters, generate signals, bucket, and persist the full enriched record. The legacy functions remain in `ai_engine.py` for reference but nothing imports them for scoring anymore.

## 2. Transient AI failures no longer reject candidates

`extract_candidate_facts` previously swallowed every Gemini exception and returned an empty fallback whose `extractable_text` was `False`. Downstream, that flag triggers the "unreadable resume" hard knockout — so a thirty-second Vertex AI outage would auto-reject perfectly readable resumes with a misleading reason, permanently. Extraction now raises `AIServiceUnavailableError` on API failure, and both Celery tasks retry with exponential backoff (30s → 60s → 120s → 240s, capped at 10 minutes, four attempts) without advancing job progress. A candidate is only ever scored from facts that were actually extracted.

## 3. Unreadable resumes are surfaced, not silently dropped

In the bulk pipeline, a PDF that yielded fewer than 50 characters of text simply vanished: progress advanced, no applicant row was written, and the recruiter saw "50 uploaded, 50 processed" with 47 candidates and no explanation. These files are now persisted as a "Manual Review — Unreadable Resume" applicant with the original PDF attached, a clear knockout reason, and a unique placeholder email so multiple unreadable files in one batch don't collide on the uniqueness constraint. Nothing a candidate submits disappears without a trace — this matters for auditability and, in many jurisdictions, for compliance.

## 4. Progress counting fixed

`process_resume` advanced `processed_resumes` inside the task body on early-exit paths *and* again in the `finally` block, so duplicates and unreadable files were double-counted and jobs could be marked "completed" before all resumes were actually handled. Progress now advances exactly once per genuinely-handled resume, and never on a retry.

## 5. One definition of "shortlisted"

The per-candidate status called anyone scoring ≥ 65 "shortlisted", while the job results payload defined the shortlist as "top 10% by score". A candidate could carry a shortlisted badge yet be absent from the shortlist panel, or appear in the panel while labelled "review". The results payload now derives the shortlist from candidate status, so every surface agrees.

## 6. Scoring fairness and separation

Three quiet score-inflation and fairness issues were fixed. First, jobs never pass a required domain, so the 10-point "domain experience" sub-score was awarded to everyone for free, compressing the entire experience component into a 15-point spread; the domain sub-score now mirrors the candidate's total-experience performance when no domain is evaluated, so a 0-years candidate scores 0/25 on experience rather than 10/25. Second, when a job specified no education requirement, the code invented a "bachelor" default and gave degree-less candidates with under five years of experience 0/20 for a requirement the employer never set; "Not specified" is now the honest default and awards full marks, since there is nothing to fail. Third, `CURRENT_YEAR` was hardcoded to 2026 and would silently corrupt every recency and tenure calculation from January 2027 onward; it now resolves at import time.

## 7. Skill matching: a deterministic fuzzy tier

Matching previously jumped from the exact alias table straight to embeddings. Embeddings are only generated when Vertex AI is reachable and cost a network round-trip, so common near-misses like "Java" vs "java 17", "react" vs "react-18", or "python" vs "python3" could fall through when embeddings were absent. A deterministic fuzzy tier (whole-term match with version-suffix awareness, careful not to match "java" inside "javascript") now sits between the alias table and the semantic tier. Match order is: alias → fuzzy → embedding.

## 8. Honest employment-gap detection

Extraction produces years only, yet the old code claimed to detect "6-month gaps" via `(next_start − current_end) × 12 ≥ 6` — with integer years this fires only on a full-year difference, and a Dec→Jan job switch (a two-week gap) counts as a one-year difference. Candidates were being badged with employment gaps they did not have. Gaps are now flagged only when the year difference is ≥ 2 (guaranteeing at least one fully uncovered calendar year), and the documented-but-missing trailing-gap check (last job ended, nothing since) is now actually implemented. Precision over recall: no false gap badges from clean job switches.

## 9. Knockout reasoning

An unreadable resume now short-circuits knockout evaluation and returns only the unreadable flag. Previously it also accumulated "insufficient experience: 0.0 years" — technically true of the empty extraction, but a misleading rejection reason for a resume nobody could read.

## 10. Security

The unauthenticated `GET /admin/nuke-jobs-table` endpoint — which dropped the jobs table for every tenant, no login required — has been removed. Destructive schema operations belong in Alembic migrations run from a shell.

**Act on this immediately:** the repository ships `vertex-key.json` (a live GCP service-account private key), `.env` (containing `SECRET_KEY`), and `ats.db` (a 10 MB database of real applicant data, including PII). `.gitignore` has been updated, but gitignore does not remove already-tracked files or rewrite history. You must: (1) revoke and re-issue the GCP service-account key in the Google Cloud console, (2) rotate `SECRET_KEY` (this invalidates existing JWTs), (3) `git rm --cached` the three files and scrub history with `git filter-repo` before this repo touches any shared remote, and (4) treat the applicant data in `ats.db` as breached if the repo was ever shared.

## What still stands between this and "sellable to serious companies"

The following are not fixed in this revision and should be treated as the roadmap. Resume PDFs are stored as BLOBs in Postgres and shipped through Redis as base64 — at real volume this bloats the database and the broker; move binaries to object storage (GCS/S3) and pass references. There is no test suite; the scoring engine is pure Python and eminently testable, and a customer due-diligence process will ask for it. There is no bias/adverse-impact monitoring, which regulations like NYC Local Law 144 and the EU AI Act (which classifies hiring AI as high-risk) effectively require — at minimum, log score distributions and let customers export audit reports; never extract or score protected attributes. CORS is pinned to localhost; parameterize it. There is no rate limiting on the public apply endpoint, no email notifications to candidates, no interview scheduling, and no integrations (job boards, calendar, e-signature) — the latter being what buyers actually shortlist ATSes on. Deprecated `@app.on_event` handlers should migrate to lifespan handlers before a FastAPI upgrade breaks them.

---

# Product Expansion (July 2026) — Features & Frontend

This revision turns the screening engine into a product a team can run end to
end: notifications, collaboration, scheduling, offers, a cross-job talent pool,
bulk actions, exports, a full audit trail, off-database resume storage, and a
revamped, professional UI.

## New capabilities

**Candidate email notifications.** `services/email.py` sends transactional email
through Resend, SendGrid, or SMTP — whichever is configured — and is a safe,
logged no-op when nothing is set up (so dev and CI never break). Events wired:
application received, pipeline stage changed, rejection (softer template),
interview scheduled, and offer sent. Every send is recorded in `email_log`.
Templates ship with sensible defaults and are overridable per company via
`PUT /api/notifications/templates/{key}`; authors use `{candidate_name}`,
`{job_title}`, `{company_name}`, `{new_stage}` merge fields.

**Team collaboration.** Roles expanded to admin / recruiter / hiring_manager /
interviewer / viewer. Admins invite teammates (`/api/team/invite`) and change
roles. Candidates carry threaded comments with @mentions
(`/api/applicants/{id}/comments`) and structured interview scorecards with a
recommendation (strong_yes…strong_no), 1–4 ratings, and per-criterion notes
(`/api/applicants/{id}/scorecards`), plus an aggregate recommendation tally.

**Interview scheduling.** `/api/applicants/{id}/interviews` schedules an
interview, optionally emails the candidate, and exposes a downloadable
iCalendar invite at `/api/interviews/{id}/ics` that drops straight into Google
Calendar / Outlook. `/api/interviews` lists everything upcoming.

**Offer stage.** Draft → send → respond. Creating an offer auto-generates a
letter body; sending it emails the candidate and advances them to the Offer
stage; accepting moves them to Hired, declining to Rejected — all audit-logged.

**Talent pool.** `POST /api/talent-pool/search` searches every candidate the
company has ever screened, across all jobs, with combinable filters (free text,
required skills with alias/fuzzy matching, min score, min experience, stage,
status). Results are de-duplicated by person (one row per email, best record
surfaced) because recruiters think in people, not applications.

**Bulk actions.** `/api/bulk/stage`, `/api/bulk/reject` (with optional
per-candidate rejection emails), and `/api/bulk/status` — each tenant-scoped,
stage-logged, and audit-logged.

**CSV export.** `/api/jobs/{id}/export.csv` for a whole job and
`/api/export/candidates.csv` for a hand-picked set; both audit-logged, since
candidate data leaving the system is exactly what a compliance review asks about.

**Audit trail everywhere.** `services/audit.py` records every consequential
action to an append-only `audit_log`; `GET /api/audit` exposes a filterable
feed. This is the backbone of the "defend a hiring decision" story and of NYC
Local Law 144 / EU AI Act readiness.

**Resumes off the database.** `services/storage.py` stores PDFs in S3, GCS, or
local disk (dev default) and keeps only a `resume_file_key` on the row. All
three ingestion paths now write to storage; downloads prefer the key and fall
back to the legacy in-DB BLOB for old records, so nothing breaks mid-migration.

## Frontend

A new design system (`frontend/static/css/loqats.css`) establishes a distinct,
professional identity — an editorial "receipts" look built around the product's
real differentiator (transparent, inspectable scoring): Fraunces display type,
Inter for UI, IBM Plex Mono for the numbers that must be trusted, a calm teal
trust-accent, and warm coral for human moments. The signature element is the
**score receipt** — the match score as a ring beside a monospace ledger of the
exact sub-scores.

- `/login` — revamped sign-in / register with a live score-receipt hero.
- `/console` — a working recruiter console driving the new APIs: overview
  tiles, talent-pool search, interview scheduling, offer management, team
  management, and the audit feed, with modals for scheduling and offers and
  CSV export of selected candidates.
- The original dashboard is preserved at `/classic` (and `/login/classic`)
  during the migration; the 3,800-line single-file `index.html` is best
  migrated onto the new design system incrementally rather than rewritten in
  one pass.

## New environment variables

Email (all optional; unset = notifications are logged, not sent):
`EMAIL_FROM`, `APP_BASE_URL`, and one provider — `RESEND_API_KEY`, or
`SENDGRID_API_KEY`, or `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_USE_TLS`.

Storage: `STORAGE_BACKEND` (local | s3 | gcs), `LOCAL_STORAGE_DIR`, `S3_BUCKET`
(+ standard AWS creds, optional `S3_ENDPOINT_URL`), or `GCS_BUCKET`.

## Migration & compatibility notes

New tables (`comments`, `scorecards`, `interviews`, `offers`, `audit_log`,
`email_log`, `email_templates`) are created by `Base.metadata.create_all`; the
new `applicants.resume_file_key` column is added by `run_migrations()`. Both run
at startup, so no manual migration step is required for SQLite/Postgres dev. For
production Postgres, fold these into a proper Alembic migration. All new
endpoints are tenant-scoped through `get_current_user` and were verified by an
end-to-end integration test covering every feature above.
