# LoqATS — Sales & Pitch Playbook

An honest, practical plan for turning LoqATS into revenue. One thing up front: there is no fool-proof plan for selling software, and anyone who promises one is selling you something. What follows is the highest-probability path given what LoqATS actually is today — a solid screening engine with a careers portal — and who you actually are today: a solo founder in Karachi with an existing services business (Vlabs) and no enterprise compliance credentials yet.

## Part 1 — Who to sell to (and why "MNCs first" will stall you)

Multinationals buy ATS software through procurement. Before a single demo, they will ask for SOC 2 Type II or ISO 27001, GDPR data-processing agreements, penetration test reports, references from comparable customers, an SLA, and increasingly an AI-hiring compliance story (the EU AI Act classifies hiring AI as high-risk; NYC Local Law 144 mandates bias audits). Sales cycles run 6–18 months against incumbents like Workday, SuccessFactors, Greenhouse, and Lever. A solo founder cannot survive that pipeline as the first motion — not because the product is bad, but because the buyer's checklist is designed to filter out exactly your current profile.

The winning sequence is: land where procurement is one person saying yes, build proof, then move upmarket with case studies in hand.

**Beachhead 1 — Recruiting agencies and staffing firms (Pakistan + GCC).** They screen hundreds of resumes per role daily, feel the pain hardest, decide fast, and one agency equals many "jobs" of volume. Your bulk-screen ZIP upload is built precisely for their workflow. This is your best first market.

**Beachhead 2 — SMEs hiring 5–50 people per year (Karachi/Lahore/Dubai tech, BPO, retail chains).** You already know how to find newly opened SMBs from your Vlabs prospecting work — the same channels (LinkedIn, local Facebook groups, direct outreach) apply. Bundle LoqATS with your existing Vlabs services: a client buying GBP + landing page work is a warm lead for "and here's how you handle the applications that come in."

**Beachhead 3 — HR consultants.** Sell them white-label or reseller terms; they become your distribution.

MNC subsidiaries come later, and the realistic entry point is not global procurement — it's a country HR manager in Karachi or Dubai with local budget authority, adopting LoqATS for local hiring as "shadow IT" that later gets legitimized. That path opens only after beachheads 1–2 give you logos and uptime history.

## Part 2 — Positioning and message

Do not position against Workday; you lose on features and compliance. Position against the actual alternative your buyers use today: Excel, email inboxes, and a recruiter spending three days reading 400 PDFs.

Core message: **"LoqATS reads every resume in minutes, shows you exactly why each candidate was ranked where they were, and never makes a hiring decision for you."**

Three pillars, in the order buyers care:

1. **Speed with receipts.** 400 resumes screened in the time it takes to make tea — and every score comes with an inspectable breakdown (experience 22/30, skills 41/50, education 20/20), matched/missing skills, and a plain-language rationale. The deterministic engine means the same resume always gets the same score — you can demo that live, which "black box AI" competitors cannot.
2. **Human in control.** AI extracts facts; Python math scores; the recruiter decides. Hard knockouts are limited to objective, employer-set gates (visa, deadline, experience floor), every filtered-out candidate shows the exact gate that fired, and unreadable resumes are flagged for manual review rather than silently rejected. This is your compliance story in embryo — lead with it, because it is genuinely rare.
3. **Complete flow, not just screening.** Public careers page → application form with custom questions → AI screening → pipeline stages → audit trail. One tool from posting to offer.

Anti-claims (things to never say): "eliminates bias" (you cannot prove it and it invites liability), "replaces recruiters" (it alienates your buyer), "AI decides" (regulatory poison).

## Part 3 — Pricing

Anchor on the cost of the problem, not your cost of goods. A recruiter at even $800/month spending 40% of time screening = $320/month of screening labor per recruiter, before slow-hire costs.

Suggested tiers (adjust for market; quote PK clients in PKR, GCC in AED/USD):

- **Starter — $49/mo:** 2 active jobs, 200 screenings/mo, 1 user, careers portal. For single SMEs.
- **Growth — $149/mo:** 10 active jobs, 1,000 screenings/mo, 5 users, custom questions, pipeline. Your volume tier.
- **Agency — $399/mo:** unlimited jobs, 5,000 screenings/mo, 15 users, priority support, white-label careers pages. For staffing firms.
- **Annual = 2 months free.** Push annual hard — it funds your runway and locks retention.

Your marginal cost is roughly a Gemini Flash call + embeddings per resume (fractions of a cent), Railway hosting, and Redis — gross margins are excellent, so compete on generosity of limits, not on price cuts.

Offer a **14-day pilot on the buyer's own resumes**: they upload a ZIP from a real recent role, you screen it live, and you compare LoqATS's shortlist against who they actually interviewed. When the tool surfaces a good candidate they'd overlooked — and across 400 resumes it usually will — the deal closes itself.

## Part 4 — The pitch (20-minute structure)

**Minutes 0–3, the pain, in their numbers.** Ask two questions and shut up: "How many applications did your last opening get?" and "How long before every one of those was actually read?" Write their answers down; the rest of the pitch reuses them.

**Minutes 3–12, live demo on THEIR resumes.** Never demo on canned data. Ask beforehand for 30–50 anonymized resumes from a closed role plus the JD. Live: paste the JD (show the extracted requirements — must-have skills, min years, education), upload the ZIP, and while it processes, talk through the scoring philosophy: facts extracted by AI, scored by transparent math, never judged by a model. Then open results: shortlist, buckets, and — the money moment — click one candidate and walk the breakdown: matched skills, missing skills, tenure signal, employment-gap flag, the override suggestion. Then click a Filtered Out candidate and show the exact knockout reason. Transparency is the demo.

**Minutes 12–16, objection handling.** The four you will always get:

- *"Will the AI reject good people?"* — The AI rejects nobody. Only your own hard gates filter anyone out, every filtered candidate shows the reason, and one click overrides it. Unreadable PDFs go to manual review, never to the bin.
- *"What about our data?"* — Tenant-isolated database, resumes stored on your instance, processed via Google Cloud's enterprise Vertex AI (not a consumer chatbot), deletable on request. (Do the security roadmap in Part 6 before you say this to anyone regulated.)
- *"We already have a process."* — "Keep it. LoqATS replaces the reading, not the deciding. Your process starts from a ranked list instead of a folder of PDFs."
- *"How accurate is it?"* — "Let's not argue about it — that's why the pilot runs on your own past role, and you compare my shortlist to who you actually hired."

**Minutes 16–20, close on the pilot.** "Send me the resumes from your last closed role today; you'll have the ranked results tomorrow morning. If the shortlist doesn't match or beat your own, we shake hands and part friends." Low risk, fast time-to-value, and it converts.

## Part 5 — Channels and the first 10 customers

Founder-led sales only for the first 20 customers — no ads, no SDRs. Concretely: (1) LinkedIn outreach to agency owners and HR managers in Karachi, Lahore, and Dubai — 20 personalized messages a day referencing a live job posting of theirs ("I ran your open Sales Executive role's JD through my screening engine — happy to show you what it does with real applicants"); (2) upsell existing and future Vlabs clients — every landing-page client who's hiring is a lead; (3) HR communities and Facebook groups you already prospect in; (4) two case studies from your first pilots, written as "X Agency screened 1,200 resumes in a week and cut time-to-shortlist from 4 days to 20 minutes," which become your entire marketing site; (5) a referral kickback — one free month per referred customer — since agencies all know each other.

Track exactly three numbers weekly: demos booked, pilots started, pilots converted. Everything else is vanity at this stage.

## Part 6 — What must be true before you pitch anyone regulated or large

In priority order: rotate the leaked GCP key and SECRET_KEY and scrub them from git history (today — see IMPROVEMENTS.md §10); move resume PDFs to object storage; add rate limiting and CAPTCHA to the public apply endpoint; write a real privacy policy and data-processing terms with candidate data deletion; add automated tests around the scoring engine (buyers' technical diligence will ask); stand up basic uptime monitoring and backups; then, when revenue justifies it (~$3–5k MRR), begin SOC 2 Type I via a platform like Vanta or Drata — that certificate is the key that unlocks the MNC conversations you ultimately want.

## Part 7 — Realistic milestones

Month 1–2: security fixes, 3 pilot agencies from your network, first paying customer. Month 3–6: 10 customers, two written case studies, pricing validated, ~$1–2k MRR. Month 6–12: 30–50 customers, first GCC customers, SOC 2 process started, first MNC-subsidiary conversation via a warm intro from an agency client. That is the honest shape of the curve — a screening engine this transparent is genuinely differentiated, but distribution is earned one pilot at a time.
