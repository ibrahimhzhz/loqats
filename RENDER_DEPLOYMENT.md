# LoqATS — Deploying on Render + Neon + Upstash (Free)

This is the fast path: everything here is free, no card required anywhere,
and you can be live within the hour. The tradeoff, stated once clearly so
it's not a surprise later: Render's free web service **sleeps after 15
minutes of no traffic** and takes 30–60 seconds to wake on the next request,
and the Celery worker runs *inside* that same sleeping service (a deliberate
trick to fit both processes into one free slot). This is fine for demos and
challenge deliveries you're present for. It is **not** something to hand a
paying client to use unattended — the moment you land one, follow the
"upgrade path" at the bottom (10 minutes, no rebuild).

---

## Phase 1 — Neon (Postgres)

1. Go to **neon.tech** → sign up (GitHub/Google login is fine, no card).
2. Create a project — name it `loqats`, pick a region close to where Render
   will run (US East / Oregon is Render's common default; picking a nearby
   Neon region reduces latency between them, but it isn't critical to get
   perfect).
3. On the project dashboard, find **Connection string** (usually shown right
   after creation, or under Dashboard → Connection Details). Copy the one
   that looks like:
   ```
   postgresql://<user>:<password>@<endpoint>.neon.tech/<dbname>?sslmode=require
   ```
   Keep this tab open — you'll paste it into Render as `DATABASE_URL` shortly.

That's it for Neon. Your app's `psycopg2-binary` driver (already in
`requirements.txt`) and `sslmode=require` handle the rest automatically.

---

## Phase 2 — Upstash (Redis)

1. Go to **upstash.com** → sign up (no card).
2. Create a Redis database — name it `loqats`, choose the **Global** or a
   region near Render's.
3. On the database details page, find the **Redis connection** section and
   copy the **TLS** URL (starts with `rediss://`, not `redis://` — the extra
   `s` means encrypted). It looks like:
   ```
   rediss://default:<password>@<endpoint>.upstash.io:6379
   ```
4. **Append a query parameter** to that URL before using it — this avoids a
   common SSL-verification error between Celery and Upstash:
   ```
   rediss://default:<password>@<endpoint>.upstash.io:6379/0?ssl_cert_reqs=CERT_NONE
   ```
   That full string is what you'll paste into Render as `REDIS_URL`.

---

## Phase 3 — Push your code to GitHub

**On your Windows machine (PowerShell), from the project folder:**
```powershell
git init
git add .
git commit -m "Initial commit"
```
Create a **private** repo on GitHub, then:
```powershell
git remote add origin https://github.com/<you>/loqats.git
git branch -M main
git push -u origin main
```
Before pushing, double-check nothing sensitive is included:
```powershell
git ls-files | Select-String -Pattern "vertex-key.json|\.env$|ats\.db"
```
This must return nothing. If it returns something, stop and fix it first —
see IMPROVEMENTS.md's secrets section.

---

## Phase 4 — Create the Render service

1. Go to **render.com** → sign up (GitHub login recommended — it also grants
   repo access for the next step, no card needed for this tier).
2. **New +** → **Web Service** → connect your GitHub account → select your
   `loqats` repo.
3. Configure:
   - **Name:** `loqats` (becomes part of your default URL:
     `loqats.onrender.com`, until you attach a custom domain).
   - **Region:** whichever is closest/default (US East is typical).
   - **Branch:** `main`.
   - **Runtime:** **Python 3**.
   - **Build Command:**
     ```
     pip install -r requirements.txt
     ```
   - **Start Command:**
     ```
     sh start-render.sh
     ```
     (This is the script already in your repo — it runs the Celery worker in
     the background and the API in the foreground, in the same process.)
   - **Instance Type:** **Free**.
4. Don't click "Create Web Service" yet — first add the environment
   variables in the next phase (Render lets you add them on this same
   creation screen, under "Environment Variables" — do that now before
   deploying, so the first build already has everything it needs).

---

## Phase 5 — Environment variables

On the same creation screen (or Environment tab if you already created the
service), add these:

```
APP_ENV=production
SECRET_KEY=<generate below>
CORS_ORIGINS=https://loqats.onrender.com
APP_BASE_URL=https://loqats.onrender.com

DATABASE_URL=<your Neon connection string from Phase 1>
REDIS_URL=<your Upstash rediss:// URL with ?ssl_cert_reqs=CERT_NONE from Phase 2>

GEMINI_API_KEY=<your key from aistudio.google.com/apikey>
GEMINI_MODEL=gemini-2.5-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
GEMINI_EMBEDDING_DIM=768
GEMINI_RPM=10

STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=./_file_store
```

**Generate `SECRET_KEY`** on your own machine first (PowerShell):
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```
Paste the output as the value.

**About `STORAGE_BACKEND=local` here — read this once:** Render's free disk
is **not guaranteed to persist across deploys** (a `git push` triggers a
rebuild that can reset it). For this validation month, that's an acceptable
trade — you're delivering screening results live or within 24 hours, not
storing résumés for months. Just avoid unnecessary redeploys while an active
challenge/pilot's files matter, and once you have a paying client, switch to
real object storage (see "Upgrade path" below) — it's a five-minute env
change, not a rebuild.

Email is optional for now — leave `RESEND_API_KEY` unset and notifications
will log as "skipped" rather than fail. Add it later following
DEPLOYMENT.md §2.4 whenever you're ready.

---

## Phase 6 — Deploy and verify

1. Click **Create Web Service**. Render builds and starts it — watch the
   live log stream on the dashboard.
2. Look for two things in the logs: **"Application startup complete"** (the
   web side) and **"celery@... ready"** (the worker side, running in the
   same log stream since they're one process).
3. Once deployed, open `https://loqats.onrender.com/api/health` — expect
   `{"status":"ok","ai_ready":true}`.
4. Open `https://loqats.onrender.com/login`, create your first real account,
   and confirm you land on `/console`.

---

## Phase 7 — Confirm the full flow

Same checklist as before: bulk-upload a small résumé ZIP and confirm
candidates appear with score receipts (give it a minute — the worker inside
the same process needs to pick up the job); move a candidate a stage; check
`/api/audit` shows it; download a résumé; schedule an interview and grab the
`.ics`. If the site was asleep, your very first request will just take
30–60 seconds to respond — that's the free-tier wake-up, not a bug.

---

## Phase 8 — Keep it awake during demos (optional, still free)

If you're about to do a live demo call and don't want the 30–60 second wake
delay in front of a prospect, visit the site yourself 2–3 minutes beforehand
to wake it, or set up a free uptime monitor (UptimeRobot, free tier) pinging
`/api/health` every 10 minutes — this keeps it warm. Render's own docs note
this "defeats the purpose" of free-tier resource conservation, so don't run
it 24/7 forever; just switch it on during an active week of demos and off
otherwise, or better, move to a paid plan once revenue justifies it.

---

## Phase 9 — Custom domain (optional, once you've bought one)

Render → your service → Settings → **Custom Domains** → add your domain →
Render shows you the CNAME/A record to add at your registrar. Once DNS
propagates, update `CORS_ORIGINS` and `APP_BASE_URL` env vars to the new
domain and redeploy.

---

## Common failure modes here specifically

- **`ai_ready: false`** → check `GEMINI_API_KEY` is set correctly; visit
  `/api/health` for the `ai_reason` field.
- **Worker never processes uploads** → check the Render log for Celery
  connection errors — almost always the `REDIS_URL` is missing the
  `?ssl_cert_reqs=CERT_NONE` suffix, or you copied the non-TLS `redis://`
  URL instead of `rediss://`.
- **Database connection refused** → confirm `?sslmode=require` is on the end
  of the Neon `DATABASE_URL` (Neon requires TLS).
- **Résumé downloads 404 after a while** → expected per the storage note in
  Phase 5 — a redeploy reset the disk. Not a bug; see the upgrade path below.
- **Everything works, then silently stops mid-task** → the process slept
  mid-processing. If this happens during an active client interaction,
  that's your sign to move off free tier for that engagement.

---

## Upgrade path — do this the moment you have a paying client

You don't need to rebuild anything; you're changing three things:

1. **Split the worker out.** Render → New → **Background Worker** ($7/mo),
   same repo, Start Command: `celery -A core.celery_app worker --loglevel=info
   --concurrency=2`. Then simplify the web service's Start Command back to
   `uvicorn main:app --host 0.0.0.0 --port $PORT` (no more combined script).
   This alone removes both the sleep problem and the "worker crash goes
   unnoticed" risk.
2. **Upgrade the web service off free** (Render Starter, ~$7/mo) to remove
   the sleep/wake delay entirely.
3. **Switch résumé storage to Cloudflare R2 or Backblaze B2** — set
   `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL`, and the access
   key/secret env vars (see `.env.example`), then redeploy. Now résumés
   survive every future deploy permanently.

Total cost at that point: roughly $14/month for something that no longer
sleeps, has a real isolated worker, and never loses a file — trivially
covered by a single Win-A screening job's revenue.
