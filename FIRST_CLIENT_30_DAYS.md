# LoqATS — The 30-Day First International Client Plan

Written for Ibrahim, Karachi, starting the week this document is opened. One
honest sentence before anything else: nobody can guarantee a client in 30 days,
and any plan that promises one is lying to you. What this plan does is maximize
the probability by defining the target correctly, front-loading the work that
compounds, and putting a specific number of at-bats on the board every single
day. Done fully, the funnel math at the bottom says one to two paying customers
is a realistic outcome, zero is possible, and three would be a great month.

## 1. Define the win (this decides everything else)

"First international client" for this plan means: **a company outside Pakistan
that pays you real money for LoqATS to screen their real candidates.** That's
it. It does not mean an annual contract, an MNC, or four figures. Three shapes
count as a win, in ascending order of quality:

**Win A — Paid screening job ($75–$300 one-time).** They send you the résumés
from one open or recently-closed role; you run them through LoqATS and deliver
the ranked shortlist with score receipts. This is a service wrapping your
product, and it's the fastest possible international revenue because it asks
for almost no trust.

**Win B — Paid pilot ($99–$199 for 30 days).** They get an account, run one or
two roles through it themselves, you support them closely. Converts to Win C.

**Win C — Subscription ($49–$399/mo).** A Starter/Growth/Agency plan, ideally
paid quarterly up front with a founding-customer discount.

Chase A and B aggressively; treat C as the conversion of B, not the opening
ask. Asking a stranger for a subscription in week one is why most first-client
attempts die.

**Who the target is.** One person must be able to say yes and pay from a card
or PayPal-equivalent without procurement. That means: recruiting and staffing
agencies with 2–25 staff (they feel résumé volume pain daily and buy fast),
and SMEs actively hiring right now (a company with 3+ live job posts has the
pain this week). Geography, in priority order: **UAE and Saudi** (overlapping
time zone, huge recruiting-agency density, culturally close, they hire from
Pakistan constantly so a Karachi vendor is normal, not exotic), **UK** (large
agency market, reachable time zone), then **US** (biggest market, hardest
timing, best via Upwork rather than cold outreach). Skip enterprise entirely
this month.

**Who it is not.** HR directors at large companies, anyone requiring vendor
onboarding, anyone who says "send a proposal to procurement." Politely park
them for later.

## 2. The offer you're selling

You are not selling "an ATS." Agencies have heard a thousand ATS pitches. You
are selling this sentence:

> "Send me the résumés from any role you're working right now. Within 24
> hours you'll have every candidate scored and ranked, with the exact math
> behind each score — which skills matched, which are missing, years verified.
> If the shortlist isn't at least as good as what your team produced by hand,
> you don't pay."

That's Win A packaged as a no-risk challenge. The "exact math" part is your
genuine differentiator — every competitor says "AI-powered"; almost none can
show a deterministic, inspectable score receipt. Lead with the receipt, always.

Founding-customer terms for anyone who converts to a subscription this month:
50% off for 6 months, direct WhatsApp line to the founder, and their feature
requests get priority. Scarcity framing that's actually true: "I'm taking five
founding customers before the price goes up."

## 3. Week Zero — the 5 working days of setup (Days 1–5)

Nothing sells while the product isn't live and provable. This week is
non-negotiable and everything in it has already been built or documented — it's
execution, not invention.

**Day 1 — Deploy.** Follow DEPLOYMENT.md end to end: rotate the GCP key /
set the Gemini API key, generate SECRET_KEY, Railway Postgres + Redis + web +
worker, R2 storage, Resend email. Finish only when `/api/health` shows
`ai_ready: true` on a public URL and a test résumé ZIP processes correctly.
Budget the whole day; deployment always eats more time than planned.

**Day 2 — Custom domain + payment rails.** Buy a domain (loqats.com or
similar, ~$10) and point it at Railway. Then solve the Pakistan payment
problem before it kills your first close: Stripe isn't available to Pakistani
businesses, so set up a **merchant of record** — Lemon Squeezy or Paddle —
which handles international cards, invoices, and sales tax for you and pays
out to Payoneer/Wise. Also set up plain Payoneer/Wise invoicing as the backup
for the one-time Win A jobs. Test a $1 transaction. A client who says yes and
then can't pay you is a self-inflicted wound.

**Day 3 — The demo asset.** Record ONE 3-minute screen video (Loom is fine):
paste a real job description → extracted requirements appear → upload a ZIP of
20 sample résumés → ranked results → click one candidate → walk the score
receipt → click a filtered-out candidate → show the exact knockout reason.
Rehearse it three times, record once, done. This video does more selling than
any deck. Use realistic but fabricated résumés (generate 20 varied ones; never
use real people's CVs in marketing).

**Day 4 — The landing page.** One page on your domain: the headline ("Screen
400 résumés before lunch — with the math to prove every ranking"), the video,
three receipt screenshots, the challenge offer from §2, founding-customer
pricing, and a Calendly link + WhatsApp button. You build landing pages for
Vlabs clients; give yourself the same treatment. Half a day, not a week — done
beats perfect.

**Day 5 — The proof kit + channel setup.** Run your 20 sample résumés against
2–3 job types (developer, sales, accountant) and screenshot the results — this
is your "case study" until a real one exists, labeled honestly as a
demonstration. Then: polish your LinkedIn profile headline ("Founder @ LoqATS —
AI résumé screening with inspectable scoring | I screen 400 CVs in 20 min"),
create the Upwork profile and one productized Fiverr/Upwork gig ("I will
AI-screen and rank all applicants for your job opening — 24h delivery"), and
build your first prospect list of 100 names (see §5).

By Friday of Week Zero you have: a live product, a way to get paid, a 3-minute
proof video, a landing page, a marketplace gig, and 100 targets. Now you sell.

## 4. Weeks 1–4 — the daily operating rhythm (Days 6–30)

From Day 6, every working day has the same skeleton. Two hours minimum,
ideally three. Consistency beats intensity — 20 quality touches a day for 20
days beats 200 in a burst.

**The daily 20:** 10 personalized LinkedIn touches + 7 cold emails + 3 Upwork
proposals (on live job posts asking for recruitment help, résumé screening,
sourcing support, or "VA to review applicants" — those last ones are your gig
in disguise). Every LinkedIn touch references something real: a live job THEY
posted. That's the trick that lifts reply rates from 2% to 15% — you're not
pitching software, you're commenting on their week.

**The daily 10 follow-ups:** anyone contacted 3+ days ago without reply gets
one short bump. Most deals close on follow-up 2–4; most founders quit after
follow-up 1.

**Evenings (UAE/UK time overlap):** demos. You're in Karachi — UAE is only
one hour behind and the UK four; your afternoon-evening covers both markets'
working hours perfectly. Take every demo call the same day or next day; speed
is a startup's only unfair advantage.

**Weekly checkpoint (every Friday):** count four numbers only — touches sent,
replies, demos done, pilots started. If touches are below 80/week, the problem
is effort. If touches are fine but replies are under 8%, rewrite the message
(see §6). If demos happen but pilots don't start, the offer is too heavy —
drop the price of Win A or make the challenge fully free for the next five
prospects.

**Week-by-week emphasis:**

*Week 1 (Days 6–12):* Volume + calibration. Expect awkwardness and low
replies; that's data, not failure. Send the challenge offer to your 5 warmest
possible contacts first — Vlabs clients who hire, the Mpesu client's network,
anyone from your internship or Fiverr history who's abroad. A warm intro is
worth 50 cold messages. Goal: 100 touches, 3+ conversations, 1 demo.

*Week 2 (Days 13–19):* First conversions. Push every conversation toward Win
A ("just send me the résumés from one role — free if it doesn't impress you").
Deliver any screening job within 24h with an over-the-top quality bar: the
ranked CSV, the receipts, plus a one-page summary of your three most
interesting findings ("candidate #14 scored low overall but is your only
applicant with X — worth a look"). That summary paragraph is what gets
forwarded to their boss. Goal: 100 more touches, 3 demos, 1–2 free/paid
challenges delivered.

*Week 3 (Days 20–26):* Convert and multiply. Every delivered challenge gets
the conversion ask within 48 hours: "That was one role. On the Growth plan you
can run every role like this yourself — founding price is $75/mo for you,
locked for 6 months." Ask every happy contact — even non-buyers — for one
introduction: "Who's one agency owner you know who's drowning in CVs?"
Referrals in the agency world close at 5–10x cold rates. Goal: first paid win
(A or B) lands this week if week 1–2 volume was real.

*Week 4 (Days 27–30):* Close what's open. Every warm-but-undecided prospect
gets the honest deadline: "I'm closing my five founding-customer slots this
Friday — after that it's full price. Want one?" Deadlines are how maybes
become answers, in either direction — and a fast no is a gift. Write up
whatever real result exists (even a free challenge) as your first true case
study for month two.

## 5. Building the list (where the 400 names come from)

You need ~400 qualified prospects for the month; build 100 in Week Zero and
~75 each following week. Sources, in order of yield:

LinkedIn search: "recruitment" OR "staffing" + Dubai/Abu Dhabi/Riyadh/London,
company size 2–50, role = founder/director/managing partner. The person who
owns the P&L answers DMs; internal recruiters don't. Then: companies with 3+
live openings on LinkedIn/Indeed in UAE/UK (filter by "posted this week" —
fresh pain). Then: Upwork/Fiverr live postings mentioning résumé screening,
candidate sourcing, or recruitment VA work — these are people with budget
already out, this week. Then: GCC recruiter communities and Facebook/WhatsApp
groups you already know how to work from your Vlabs prospecting. Log everything
in one sheet: name, company, country, live role you referenced, date touched,
follow-up date, status. Your CRM is a spreadsheet this month; don't
procrastinate by shopping for tools.

## 6. Scripts (starting points — rewrite in your own voice by Week 2)

**LinkedIn DM (agency owner, references their live role):**
"Hi [Name] — saw you're hiring a [role] for a client. Quick question: how many
CVs did that post pull so far? I built a tool that reads all of them and ranks
every candidate in ~20 minutes, with the exact scoring math shown (not a black
box). Happy to run your actual applicants through it free — if the shortlist
isn't at least as good as your team's, we never speak again. Worth a look?"

**Cold email (subject: "your [role] opening — 400 CVs in 20 minutes"):**
Three sentences max. The pain in their numbers, the challenge offer, one link
(the 3-minute video). Sign as founder. No brochure, no feature list.

**Follow-up bump (3–4 days later):**
"Hi [Name] — following up once. The offer's simple: send me the CVs from one
live role, get a ranked shortlist with full scoring receipts in 24h, pay
nothing if it doesn't beat your manual process. If it's a no, tell me and I'll
stop — no hard feelings."

**Upwork proposal (on a screening/sourcing post):**
Open with their job's specifics, quote a fixed price ($100–$250 depending on
volume), promise 24h delivery + the receipts + the findings summary, link the
video. Fixed-price beats hourly for this — it's an outcome, not labor.

**The conversion ask (after a delivered challenge):**
"Glad the shortlist landed. That took me 25 minutes; on the platform it takes
you the same with zero of me in the loop. Founding customer terms: [plan] at
50% off for 6 months, cancel anytime, and you get my WhatsApp. Shall I set up
your account today?"

**Objections you'll hear, and honest answers:** "Is our data safe?" — résumés
stored on encrypted cloud storage, tenant-isolated, deletable on request, and
for the challenge you can anonymize CVs before sending. "How do we pay from
here?" — card checkout via [Lemon Squeezy/Paddle] like any SaaS, or an invoice.
"We already have a process." — keep it; this replaces the reading, not the
deciding. "Will AI reject good people?" — the AI rejects nobody; only your own
hard rules filter anyone, every filtered candidate shows the exact reason, one
click overrides.

## 7. The funnel math (why the daily 20 is the whole plan)

20 touches × 20 working days = 400 touches. At an 8–15% reply rate (achievable
when every message references a live role) that's 32–60 conversations. Of
those, roughly a third agree to a demo or the free challenge: 10–20. Of
challenges delivered with 24h turnaround and the findings summary, 20–40%
convert to paid: **2–6 paying wins**, of which realistically 1–3 are outside
Pakistan. Upwork runs in parallel with its own math: 60 proposals at typical
3–8% win rates is 2–5 paid international jobs — which is why the marketplace
gig may quietly deliver your "first international client" faster than cold
outreach does, and that counts.

The two failure modes that break the math: touches quietly dropping below 15/day
(fix: do outreach first thing, before building anything), and messages pitching
features instead of referencing their live role (fix: no message goes out
without a specific role named in line one).

## 8. Rules for the month

Build nothing new. The product is ready; every hour of coding this month is an
hour of hiding from sales. Bug fixes for an active pilot are the only
exception. Answer every reply within 2 hours during UAE/UK working hours —
speed is the one dimension where you beat every incumbent. Deliver every
challenge in under 24 hours even if it costs you a night. Ask for the referral
every single time. And keep the definition of victory honest: one real company,
outside Pakistan, paying real money, using LoqATS on real candidates. Get that,
write the case study, and month two starts from proof instead of promises.
