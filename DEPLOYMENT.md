# LoqATS — Deployment Runbook

A start-to-finish guide, in order, from a clean machine to a live production
deployment. Local commands are written for **Windows PowerShell** (your setup).
Production instructions target **Railway** (which you've used before), with notes
for anywhere else at the end.

Read the whole "Phase 0" section before doing anything. It is not optional.

---

## Phase 0 — Secure the secrets (do this first, before any git push)

The repository has been carrying live secrets. Until these are rotated and
removed from git history, treat them as compromised. If you have already pushed
this repo anywhere, assume the keys are burned and rotate regardless.

**0.1 — Rotate the Google Cloud service-account key.**
Go to Google Cloud Console → IAM & Admin → Service Accounts → your ATS account →
Keys. Delete the old `vertex-key.json` key and "Add key → Create new key (JSON)".
Download the new one. Keep it off git (see 0.4).

**0.2 — Rotate the app secret.** Generate a fresh 32-byte secret and keep it
somewhere safe (a password manager). In PowerShell:
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```
This becomes `SECRET_KEY` in production. Rotating it logs everyone out (expected).

**0.3 — Confirm nothing sensitive is tracked.** From the repo root:
```powershell
git ls-files | Select-String -Pattern "vertex-key.json|\.env$|ats\.db"
```
If any of those appear, they are tracked and must be removed from history.

**0.4 — Untrack secrets and local data.** `.gitignore` already lists them, but
gitignore does not untrack files already committed:
```powershell
git rm --cached vertex-key.json .env ats.db 2>$null
git commit -m "Stop tracking secrets and local database"
```

**0.5 — Scrub them from history** (they still live in old commits until you do).
Install git-filter-repo (`pip install git-filter-repo`), then:
```powershell
python -m git_filter_repo --invert-paths --path vertex-key.json --path .env --path ats.db --force
```
This rewrites history. Afterward you must force-push (`git push origin --force --all`)
and every collaborator must re-clone. If the repo is solo and unpushed, this is painless.

> If any of 0.1–0.5 is unfamiliar, stop and do just 0.1 and 0.2 (rotation) at
> minimum — a rotated key that leaks is harmless. History scrubbing can follow.

---

## Phase 1 — Run it locally (prove it works before deploying)

**1.1 — Install the tools you need (once):**
- Python 3.11 (matches the Dockerfile). Check: `python --version`.
- Redis. On Windows the simplest path is Docker Desktop, then run Redis in a container (1.4). Alternatively use a free Upstash Redis URL and skip local Redis entirely.
- Git.

**1.2 — Create and activate a virtual environment:**
```powershell
cd path\to\ats_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
If activation is blocked by execution policy:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**1.3 — Install dependencies:**
```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

**1.4 — Start Redis** (Docker Desktop running):
```powershell
docker run -d --name loqats-redis -p 6379:6379 redis:7
```
(Or set `REDIS_URL` to an Upstash URL in 1.5 and skip this.)

**1.5 — Create your local `.env`** in the repo root. Copy from `.env.example`
and fill in. Minimum to boot locally:
```
APP_ENV=development
ALLOW_INSECURE_DEV_SECRET=true
DATABASE_URL=sqlite:///./ats.db
REDIS_URL=redis://localhost:6379/0
GOOGLE_APPLICATION_CREDENTIALS=./vertex-key.json
STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=./_file_store
```
Put your **new** `vertex-key.json` (from 0.1) in the repo root. Email can stay
unset locally — notifications will log instead of send, which is fine.

**1.6 — Run the two processes** (two PowerShell tabs, both with the venv active):

Tab 1 — the web app (this also creates tables + runs migrations on boot):
```powershell
uvicorn main:app --reload --port 8000
```
Tab 2 — the Celery worker (this does the résumé screening):
```powershell
celery -A core.celery_app worker --loglevel=info --pool=solo
```
> `--pool=solo` matters on Windows; the default prefork pool misbehaves there.

**1.7 — Smoke-test it.** Open http://localhost:8000/login, create an account,
and you land on the console at http://localhost:8000/console. Post a job, upload
a résumé ZIP through the classic dashboard (http://localhost:8000/classic), and
confirm candidates appear and the worker logs show processing. If the worker
isn't running, uploads will sit unprocessed — that's the #1 "why is nothing
happening" cause.

---

## Phase 2 — Provision production infrastructure (Railway)

You need four things running: a **Postgres** database, a **Redis** instance, a
**web** service, and a **worker** service. Plus object storage for résumés and
an email provider. Do them in this order.

**2.1 — Create the project and database.**
In Railway: New Project → Deploy PostgreSQL. Railway exposes a
`DATABASE_URL`. One caveat: SQLAlchemy needs the `postgresql://` scheme, and
some providers hand out `postgres://`. If yours does, you'll set
`DATABASE_URL` manually in 3.2 with the corrected scheme.

**2.2 — Add Redis.** In the same project: New → Database → Add Redis. It exposes
`REDIS_URL`.

**2.3 — Pick résumé storage.** Local disk will NOT work in production — Railway's
filesystem is ephemeral and wiped on every deploy, so stored résumés would
vanish. Use object storage. Easiest and cheapest is **Cloudflare R2** (S3-compatible,
generous free tier):
- Create an R2 bucket (e.g. `loqats-resumes`).
- Create an R2 API token (Access Key ID + Secret).
- You'll set `STORAGE_BACKEND=s3`, `S3_BUCKET=loqats-resumes`,
  `S3_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com`, and the AWS
  key/secret env vars in 3.2.
(Plain AWS S3 or Google Cloud Storage work too — see `.env.example`.)

**2.4 — Set up email (Resend is the least friction).**
- Sign up at resend.com, add and verify your sending domain (DNS records).
- Create an API key.
- You'll set `RESEND_API_KEY` and `EMAIL_FROM="Your Co Careers <careers@yourdomain.com>"`.
- Until the domain verifies, Resend only sends to your own address; that's fine for testing.

---

## Phase 3 — Deploy the web service

**3.1 — Connect the repo.** Push your (now secret-free) repo to GitHub, then in
Railway: New → Deploy from GitHub repo → pick it. Railway detects the Dockerfile
and builds. The Dockerfile's default command runs the **web** process, which is
what this service should be.

**3.2 — Set environment variables** on the web service (Variables tab). Paste
these, filled in with your real values:
```
APP_ENV=production
SECRET_KEY=<the 32-byte hex from step 0.2>
DATABASE_URL=<from Railway Postgres, ensure postgresql:// scheme>
REDIS_URL=<from Railway Redis>
CORS_ORIGINS=https://<your-web-service>.up.railway.app
APP_BASE_URL=https://<your-web-service>.up.railway.app

# Vertex AI (see 3.3 for the credentials file)
GOOGLE_CLOUD_PROJECT=<your gcp project id>
GOOGLE_APPLICATION_CREDENTIALS=/app/vertex-key.json

# Résumé storage (R2 example)
STORAGE_BACKEND=s3
S3_BUCKET=loqats-resumes
S3_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=<r2 access key>
AWS_SECRET_ACCESS_KEY=<r2 secret key>
AWS_DEFAULT_REGION=auto

# Email
RESEND_API_KEY=<resend key>
EMAIL_FROM=Your Co Careers <careers@yourdomain.com>
```
Do **not** set `ALLOW_INSECURE_DEV_SECRET` in production — with a real
`SECRET_KEY` set, the app boots securely on its own.

**3.3 — Get the Vertex AI credentials into the container.** The service account
JSON can't be committed. Two clean options:

- **Simplest (base64 env + write on boot).** Base64-encode your new key locally:
  ```powershell
  [Convert]::ToBase64String([IO.File]::ReadAllBytes("vertex-key.json")) | Set-Clipboard
  ```
  Set it as `GCP_SA_KEY_B64` in Railway, then add `boto3`-style bootstrapping:
  create a tiny `start-web.sh` that decodes it and launches uvicorn:
  ```sh
  #!/bin/sh
  if [ -n "$GCP_SA_KEY_B64" ]; then
    echo "$GCP_SA_KEY_B64" | base64 -d > /app/vertex-key.json
  fi
  uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
  ```
  and point the web service's start command at `sh start-web.sh`.
- **Alternative.** Use Railway's file-mount / secret-file feature if available on
  your plan, mounting the JSON at `/app/vertex-key.json`.

**3.4 — Add the storage dependency.** For S3/R2, `boto3` must be installed.
Uncomment `boto3` in `requirements.txt` (for GCS uncomment `google-cloud-storage`)
and commit, so the build includes it.

**3.5 — Deploy.** Railway builds and starts the web service. On first boot it
creates all tables and runs migrations automatically (this happens in
`main.py` at import). Watch the deploy logs for "Application startup complete".

**3.6 — Verify.** Hit `https://<your-web-service>.up.railway.app/api/health`.
You want `{"status":"ok","ai_ready":true}`. If `ai_ready` is false, the
`ai_reason` field tells you what's wrong (usually the credentials path or
project id). Then open `/login` and create your first real account.

---

## Phase 4 — Deploy the worker service

The web service answers requests; the **worker** does the actual résumé
screening. Without it, uploads queue forever.

**4.1 — Add a second service** in the same Railway project pointing at the
**same GitHub repo** (New → GitHub repo → same repo, or "Empty Service" then
attach the repo).

**4.2 — Override its start command** to run the worker instead of the web
process. In that service's Settings → Deploy → Custom Start Command:
```
celery -A core.celery_app worker --loglevel=info --concurrency=2
```
(If you used the base64 credentials trick in 3.3, make a `start-worker.sh` that
decodes the key first, then runs the celery line, and point the start command at it.)

**4.3 — Give the worker the same environment variables** as the web service —
it needs `DATABASE_URL`, `REDIS_URL`, the Vertex credentials, storage vars, and
email vars. The fastest way is Railway's shared-variables feature; otherwise
copy them across. The worker does **not** need `CORS_ORIGINS` or `APP_BASE_URL`
(harmless if present).

**4.4 — Deploy and verify.** In the worker logs you should see Celery connect to
Redis and print "celery@… ready". Now upload a résumé ZIP in the app and watch
the worker log process each one and the candidates appear in the UI.

---

## Phase 5 — Post-deploy checklist

Run through this once, live:

- **Auth:** register, log out, log back in. JWT works across the two services.
- **Screening:** bulk-upload a small ZIP; candidates appear with scores and the
  score-receipt breakdown.
- **Email:** move a candidate a stage; check Resend's dashboard shows the send,
  and `/api/notifications/log` in the app shows `sent` (not `skipped`).
- **Storage:** download a résumé from a candidate card; it streams from R2, not
  the database.
- **Interviews/offers:** schedule an interview (candidate gets an email + you can
  download the `.ics`); draft and send an offer.
- **Audit:** open the Audit tab — every action above is recorded.
- **Backups:** in Railway, enable automated Postgres backups (Settings on the DB).
  Do this before you have real customer data, not after.

---

## Phase 6 — Custom domain & hardening (when you have a first customer)

- **Domain:** add a custom domain to the web service in Railway, update DNS,
  then set `CORS_ORIGINS` and `APP_BASE_URL` to the new domain and redeploy.
- **HTTPS:** Railway terminates TLS automatically on custom domains.
- **Rate limiting** on the public apply endpoint (add slowapi or a proxy rule) —
  it's currently unthrottled and is the obvious abuse target.
- **Uptime monitoring:** point a free monitor (e.g. UptimeRobot) at `/api/health`.
- **Log retention & error tracking:** add Sentry (free tier) for exceptions.
- **Move migrations to Alembic** before you have production data you can't lose;
  the current auto-create-on-boot is fine for launch but you'll want versioned,
  reversible migrations for schema changes later.
- **SOC 2 groundwork** (Vanta/Drata) once you're at a few thousand in MRR and
  chasing larger customers — see SALES_PLAYBOOK.md.

---

## Common failure modes (and the fix)

- **Uploads never finish / candidates never appear** → the worker service isn't
  running, or it can't reach Redis. Check the worker logs and that `REDIS_URL`
  matches on both services.
- **`ai_ready: false` on /api/health** → wrong `GOOGLE_APPLICATION_CREDENTIALS`
  path, missing `GOOGLE_CLOUD_PROJECT`, or the key wasn't written to
  `/app/vertex-key.json`. Read the `ai_reason` field.
- **App refuses to boot with a SECRET_KEY error** → `SECRET_KEY` is unset or
  under 32 chars in production. Set a real one (step 0.2).
- **Downloaded résumés 404 in production** → `STORAGE_BACKEND` is still `local`;
  the file was written to an ephemeral disk that got wiped. Switch to `s3`/`gcs`.
- **`postgres://` connection error** → change the scheme to `postgresql://` in
  `DATABASE_URL`.
- **Emails show `skipped` in the log** → no provider configured; set
  `RESEND_API_KEY` (or SMTP) and `EMAIL_FROM`.
- **CORS errors in the browser** → only relevant if you host the frontend on a
  different domain than the API; add that domain to `CORS_ORIGINS`. The bundled
  console/login are same-origin and need nothing.

---

## The absolute minimum, if you just want it live today

1. Rotate the GCP key and generate a `SECRET_KEY` (Phase 0.1–0.2).
2. Railway: add Postgres + Redis.
3. Deploy the repo as the web service; set the env vars in 3.2; get the GCP key
   in via base64 (3.3); switch storage to R2 (2.3).
4. Add a second service with the worker start command and the same env (Phase 4).
5. Check `/api/health`, then `/login`.

Everything else in Phases 5–6 is hardening you can layer on after the first
customer is using it.
