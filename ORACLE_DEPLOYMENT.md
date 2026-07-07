# LoqATS — Deploying on Oracle Cloud's Always Free VM

This replaces the Railway-specific phases of DEPLOYMENT.md. Everything runs on
one always-on virtual machine you fully control: Postgres, Redis, the web app,
the Celery worker, and Caddy (which gets you free, auto-renewing HTTPS). No
service sleeps, no database expires, no separate paid worker needed.

The tradeoff for "genuinely free forever" is that you're now the sysadmin —
you're running the update commands a PaaS would normally run for you. This
guide gives you the exact commands; you don't need prior server experience,
just patience the first time through.

Local commands are PowerShell (your machine). VM commands are Linux/bash
(after you SSH in) — the guide marks which is which.

---

## Phase 1 — Create the Oracle Cloud account and the VM

**1.1 — Sign up.** Go to oracle.com/cloud/free and create an account. Oracle
asks for a card for identity verification only — Always Free resources are not
billed as long as you stay within the free limits this guide uses.

**1.2 — Pick your home region carefully.** During signup you choose a "home
region." **You cannot change this later without creating a new account.** Pick
one geographically close to you or your first customers (e.g. a Middle East
region if UAE/Saudi is your primary market, or an EU/Asia region — whichever
Oracle lists as available for your account). This mostly affects latency, not
functionality.

**1.3 — Launch the VM.** Console → Compute → Instances → **Create Instance**.
- **Name:** `loqats-prod` (anything).
- **Image and shape → Edit:** choose **Ubuntu 22.04**, then under shape click
  **Change Shape** → Ampere → **VM.Standard.A1.Flex**. Set **4 OCPUs / 24 GB
  memory** (the full Always Free allowance — use all of it; running Postgres,
  Redis, the API, and the worker together needs the headroom).
- **Networking:** leave defaults (creates a new VCN with a public IP) unless
  you already have one.
- **Add SSH keys:** select "Generate a key pair for me" and **download both
  the private and public key files immediately** — this is your only chance.
  Save them somewhere like `C:\Users\<you>\.oci\loqats-key.key`. Alternatively,
  generate your own key first in PowerShell:
  ```powershell
  ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\loqats-oracle" -C "loqats"
  ```
  then upload the resulting `.pub` file's contents on this screen.
- Click **Create**.

**1.4 — If you see "Out of host capacity."** This is Oracle's most common
Always Free complaint — Ampere A1 capacity is popular and sometimes full in a
given region/availability domain. If it happens: try again in a few minutes,
try a different Availability Domain (dropdown near the shape selector), or try
at an off-peak hour (late night in your target region's timezone). It almost
always succeeds within a few attempts; it is not a sign anything is wrong with
your account.

**1.5 — Note the public IP.** Once the instance shows "Running," copy its
**Public IP Address** from the instance details page. You'll use this constantly
below as `<VM_IP>`.

---

## Phase 2 — Open the firewall (the step everyone forgets)

Oracle blocks incoming traffic at **two separate layers**. Miss either one and
your site is unreachable even though everything on the VM looks fine. Do both.

**2.1 — Cloud-level firewall (Security List).** Console → your instance →
click the subnet link → click the Security List (usually "Default Security
List for ...") → **Add Ingress Rules**. Add two rules, both with:
- Source CIDR: `0.0.0.0/0`
- IP Protocol: TCP
- Destination Port Range: `80` (rule 1), then repeat for `443` (rule 2).

(Port 22/SSH is already open by default — that's how you'll connect next.)

**2.2 — OS-level firewall (on the VM itself).** Oracle's Ubuntu images ship
with `iptables` rules that block 80/443 by default, separately from the rule
above. You'll fix this once you're SSH'd in — covered in Phase 4.

---

## Phase 3 — Point your domain at the VM

Buy your domain if you haven't (Namecheap, ~$10/yr — see FIRST_CLIENT_30_DAYS.md
§3 Day 2). In your domain registrar's DNS settings, add:
- Type: **A**, Host: `@` (or blank, for the root domain), Value: `<VM_IP>`, TTL: default.
- Optionally also Type: **A**, Host: `www`, Value: `<VM_IP>`.

DNS can take anywhere from 5 minutes to a few hours to propagate. Check with:
```powershell
nslookup yourdomain.com
```
Don't proceed to the Caddy step (Phase 7) until this returns your VM's IP.

---

## Phase 4 — Connect and prepare the VM

**4.1 — SSH in** from PowerShell (replace the key path and IP):
```powershell
ssh -i "$env:USERPROFILE\.ssh\loqats-oracle" ubuntu@<VM_IP>
```
If you downloaded the key from the console instead, point at that file's path.
First connection asks to confirm the host fingerprint — type `yes`.

You're now on the VM. Everything below in this phase runs **on the VM.**

**4.2 — Update the system and fix the OS firewall:**
```bash
sudo apt update && sudo apt upgrade -y
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```
(If `netfilter-persistent` isn't found: `sudo apt install -y iptables-persistent`
then re-run the save command.)

**4.3 — Install Docker and the Compose plugin:**
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```
Log out and back in (exit the SSH session and reconnect with the same command
from 4.1) for the group change to apply — otherwise you'll need `sudo` before
every `docker` command below.

**4.4 — Verify:**
```bash
docker --version
docker compose version
```
Both should print version numbers.

---

## Phase 5 — Push your code to GitHub, then pull it onto the VM

**5.1 — On your Windows machine (PowerShell), from the project folder:**
```powershell
git init
git add .
git commit -m "Initial commit"
```
Create a **private** repo on GitHub (recommended for a commercial product),
then:
```powershell
git remote add origin https://github.com/<you>/loqats.git
git branch -M main
git push -u origin main
```
Double-check nothing sensitive got committed first:
```powershell
git ls-files | Select-String -Pattern "vertex-key.json|\.env$|ats\.db"
```
This should return nothing. If it returns something, stop and revisit
IMPROVEMENTS.md's secrets section before pushing.

**5.2 — Back on the VM, clone it:**
```bash
git clone https://github.com/<you>/loqats.git ~/loqats
cd ~/loqats
```
(For a private repo, Git will prompt for credentials — use a GitHub Personal
Access Token as the password, since GitHub no longer accepts account
passwords over HTTPS git operations. Create one at GitHub → Settings →
Developer settings → Personal access tokens, scope: `repo`.)

---

## Phase 6 — Configure environment variables

**6.1 — On the VM, create `.env` from the example:**
```bash
cp .env.example .env
nano .env
```

**6.2 — Fill in these values** (delete or ignore anything storage/GCP-related
that doesn't apply — you're using local storage and the Gemini API key path):

```
APP_ENV=production
SECRET_KEY=<generate below>
CORS_ORIGINS=https://yourdomain.com
APP_BASE_URL=https://yourdomain.com

GEMINI_API_KEY=<your key from aistudio.google.com/apikey>
GEMINI_MODEL=gemini-2.5-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
GEMINI_EMBEDDING_DIM=768
GEMINI_RPM=10

STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=/app/_file_store

EMAIL_FROM=LoqATS <careers@yourdomain.com>
RESEND_API_KEY=<your resend key, once you've set it up>
```
Note: `DATABASE_URL` and `REDIS_URL` are **not** set here — the Compose file
injects them automatically, pointing at the `postgres` and `redis` containers.

**6.3 — Generate the SECRET_KEY** (run this, then paste the output into the
`.env` file above, replacing the placeholder):
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

**6.4 — Set the Postgres password** — this is a separate value the Compose
file reads from the environment, not from `.env`. Add it to your shell profile
so it persists across reboots:
```bash
echo 'export POSTGRES_PASSWORD="'$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")'"' >> ~/.bashrc
source ~/.bashrc
echo $POSTGRES_PASSWORD   # confirm it's set — keep this somewhere safe too
```
Save the printed password somewhere (password manager) as your backup.

**6.5 — Set the domain in the Caddyfile:**
```bash
nano Caddyfile
```
Replace `yourdomain.com` with your real domain, save, exit (Ctrl+O, Enter,
Ctrl+X in nano).

---

## Phase 7 — Bring the stack up

**7.1 — Confirm DNS has propagated** (from Phase 3) before this step, or
Caddy's certificate request will fail.

**7.2 — Build and start everything:**
```bash
cd ~/loqats
docker compose -f docker-compose.prod.yml up -d --build
```
This builds the app image (first time takes a few minutes), then starts
Postgres, Redis, web, worker, and Caddy in the right order.

**7.3 — Watch the logs** to confirm a clean boot:
```bash
docker compose -f docker-compose.prod.yml logs -f web
```
Look for "Application startup complete." Press Ctrl+C to stop watching (the
containers keep running in the background regardless).

```bash
docker compose -f docker-compose.prod.yml logs -f worker
```
Look for "celery@... ready." Ctrl+C when confirmed.

```bash
docker compose -f docker-compose.prod.yml logs caddy
```
Look for certificate obtained messages, no repeated errors.

**7.4 — Verify from your own machine:**
```powershell
curl.exe https://yourdomain.com/api/health
```
Expect `{"status":"ok","ai_ready":true}`. Then open `https://yourdomain.com/login`
in a browser and create your first real account.

---

## Phase 8 — Confirm the full flow works

Same checklist as DEPLOYMENT.md Phase 5: register → log out → log in; bulk-
upload a small résumé ZIP and confirm the worker log shows it processing and
candidates appear with score receipts; move a candidate a stage and confirm
`/api/notifications/log` shows the email (or "skipped" if you haven't set up
Resend yet — see below); download a résumé and confirm it streams (now from
the VM's local disk); schedule an interview and download the `.ics`.

**Résumé storage note:** you're using `STORAGE_BACKEND=local` here, which is
safe on a real VM (unlike ephemeral PaaS disks) because the `resume_files`
Docker volume persists across restarts and rebuilds. It does **not** survive
you deleting the VM entirely, so back it up (Phase 10) once real candidate
data is flowing. Cloudflare R2 (as in the original DEPLOYMENT.md §2.3) remains
a drop-in upgrade later — just change `STORAGE_BACKEND` and the R2 vars in
`.env` and restart.

**Email:** if you haven't set up Resend yet, notifications log as "skipped" —
harmless. Follow DEPLOYMENT.md §2.4 (Resend signup + domain verification),
add `RESEND_API_KEY` to `.env`, then:
```bash
docker compose -f docker-compose.prod.yml restart web worker
```

---

## Phase 9 — Make it survive a reboot

Docker itself is enabled to start on boot by the install script in 4.3, and
every service in the Compose file has `restart: unless-stopped`, so a VM
reboot brings everything back automatically. Confirm this once:
```bash
sudo reboot
```
Wait a minute, reconnect via SSH, then:
```bash
docker compose -f docker-compose.prod.yml ps
```
All five services (postgres, redis, web, worker, caddy) should show "Up."

---

## Phase 10 — Backups (do this before real client data flows in)

Unlike a managed database, nothing backs up Postgres or your résumé files
automatically here — that's now your job. A simple daily cron job covers both:

```bash
mkdir -p ~/backups
crontab -e
```
Add this line (runs at 3 AM daily, keeps 7 days):
```
0 3 * * * docker exec $(docker compose -f /home/ubuntu/loqats/docker-compose.prod.yml ps -q postgres) pg_dump -U loqats loqats | gzip > /home/ubuntu/backups/db-$(date +\%Y\%m\%d).sql.gz && find /home/ubuntu/backups -mtime +7 -delete
```
For real peace of mind, periodically copy `~/backups` off the VM entirely
(e.g. `scp` it to your PowerShell machine, or sync to a free-tier cloud
storage bucket) — a backup that lives only on the machine it's protecting
against doesn't protect against that machine dying.

---

## Phase 11 — Redeploying when you push code changes

Whenever you push updates to GitHub:
```bash
cd ~/loqats
git pull
docker compose -f docker-compose.prod.yml up -d --build
```
This rebuilds only what changed and restarts the affected containers with a
few seconds of downtime. Migrations run automatically on web-container
startup, same as in the original DEPLOYMENT.md.

---

## Phase 12 — When you land your first paying client

Nothing about this deployment needs to change to serve a paying customer — the
VM is a real, always-on server, not a demo sandbox. The two things worth doing
at that point: switch résumé storage to Cloudflare R2 for off-VM durability
(Phase 8 note above), and consider whether you want a second, smaller VM as a
staging environment before you start making changes against live customer
data. Both are optional upgrades, not requirements to keep running.

---

## Common failure modes on this setup

- **Site unreachable, containers all show "Up"** → almost always the Phase 2
  firewall — check both the Security List (cloud) AND `iptables` (OS) rules.
- **Caddy stuck / certificate errors in its logs** → DNS hasn't propagated yet,
  or the Caddyfile still has the placeholder domain. Fix DNS/the Caddyfile,
  then `docker compose -f docker-compose.prod.yml restart caddy`.
- **`docker: permission denied`** → you ran a docker command before logging
  out/in after Phase 4.3; log out and back in, or prefix commands with `sudo`.
- **"Out of host capacity" when creating the VM** → see Phase 1.4; retry, try
  a different availability domain, or try off-peak hours.
- **Worker not processing uploads** → `docker compose -f docker-compose.prod.yml
  logs worker` — usually a Gemini API key issue (check `/api/health`) or Redis
  not yet healthy when the worker started (restart it: `docker compose -f
  docker-compose.prod.yml restart worker`).
- **Changes to `.env` not taking effect** → containers cache environment
  variables at start; after editing `.env`, run
  `docker compose -f docker-compose.prod.yml up -d --build` again.

---

## What to do right now

1. Do Phase 1 (create the account, launch the VM) — this is pure Oracle
   Console clicking, the one part I can't do alongside you.
2. Come back with the VM's public IP once it's "Running," and tell me if you
   hit the capacity error.
3. From there I'll walk you through Phases 2–8 command by command, checking
   output with you at each step so we catch anything unexpected immediately
   rather than at the end.
