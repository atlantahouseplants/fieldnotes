# AHP Dogfood — Atlanta Houseplants as FieldNotes Client #1

**Goal:** Sign up Atlanta Houseplants through the REAL customer flow (cold signup, no backdoor),
run daily operations on FieldNotes for 2+ weeks, fix everything that breaks, and only then
take the product to market. Proof in the pudding: we sell what we run.

**Started:** 2026-08-08 · **Owner:** Geoff + Hermes · **Rule:** every step happens exactly as a
stranger would experience it. Any time we have to "cheat" (manual DB edit, founder-only fix),
that gets logged as a finding — a real customer would have been stuck there.

## Pre-flight verification (DONE 2026-08-08)

| Check | Result |
|---|---|
| Prod health `fieldnotesapp.io/health` | healthy, db ok |
| Landing / try.html / pricing / start.html | all live (307→200 chain, 200s) |
| Telegram webhook | url set, pending 0, no errors |
| Demo log tap (`/api/demo`) | real LLM parse — matched account, issues extracted |
| Demo Q&A tap ("gate code?") | answered with seeded data — demo magic works |
| Content engine + uptime/parse watchdogs | running daily |
| Existing AHP tenant (biz 3) | 20 accounts, 5 logs (Jul 13–19, stale), seeded NOT signed-up — no Stripe, no real onboarding |

**Why a fresh signup instead of reusing biz 3:** biz 3 was created by direct DB seeding.
It never went through checkout → signup → invite → import. Geoff wants to feel what a paying
customer feels. The new tenant becomes the REAL AHP tenant; biz 3 gets retired (kept for history).

## Status board

| # | Step | Owner | Status |
|---|------|-------|--------|
| 0 | Pre-flight verification | Hermes | ✅ DONE (2026-08-08) |
| 1 | Cold signup as AHP (landing → demo → pricing → checkout → start.html) | Geoff (manual) + Hermes (verify) | ⬜ |
| 2 | Link owner + worker via Telegram deep-links | Geoff (taps) + Hermes (verify DB) | ⬜ |
| 3 | CSV import: 20 accounts with schedules, gate codes, contacts | Hermes (build CSV) → Geoff or Hermes (import.html) | ⬜ |
| 4 | Retire biz 3 (clear owner_telegram, deactivate) | Hermes | ⬜ |
| 5 | Recaps enabled on 1 pilot account (sarah@ as stand-in client) | Hermes | ⬜ |
| 6 | Week 1: live route ops on FieldNotes (notes, Q&A, tasks, route push, nightly summaries) | Geoff (uses it) + Hermes (monitors) | ⬜ |
| 7 | Week 2: stress the edges (voice notes, ambiguous accounts, missed stops, edits) | Geoff + Hermes | ⬜ |
| 8 | Fix-forward: every finding logged below gets fixed + regression-checked | Hermes | rolling |
| 9 | Gold-check review → GO/NO-GO for outbound sales | Geoff | ⬜ |

## The journey — step by step

### Step 1 — Cold signup (Geoff, ~5 min, exactly like a stranger)

1. Open **https://fieldnotesapp.io** fresh (incognito optional — we WANT the stranger path).
2. Run the 60-sec demo (try.html) — log a note, ask the gate code. (Already verified working.)
3. Go to pricing → pick **Team** → checkout. Use coupon **BETA49** (= $49/mo locked).
   - ✅ DECIDED (Geoff, 2026-08-08): Team, not Crew. Hitting the route-push gate is our first
     product finding — "morning route push shouldn't be Crew-only." Feeling the gate IS the dogfood.
   - Use the SAME email at checkout and at signup (pending-subscription matches by email).
     Confirmed: **sarah@atlantahouseplant.com** (biz 3's geoff@atlhouseplants.com is stale).
4. After checkout → start.html: business name **Atlanta Houseplants**, owner name Geoff Wall,
   owner email (same as checkout), paste the plain account list (one per line).
5. Watch for: the founder signup alarm fires to Geoff's Telegram (biz #N) — first real
   "customer" alarm. Save the dashboard URL + invite link the success screen shows.

**Hermes verifies:** new Business row with stripe_customer_id/subscription_id/tier from
checkout metadata, subscription_status=trialing, accounts created, no errors in Railway logs.

### Step 2 — Telegram linking (Geoff, 1 min)

1. Tap the **owner link** (`?start=owner_...`) → expect "👑 You're linked as owner".
2. Tap the **worker invite link** (`?start=invite_...`) → expect "🎉 You're connected".
   - This RE-LINKS Geoff's existing worker row from biz 3 to the new tenant (code-confirmed:
     `handle_start` updates `worker.business_id`). No dual-tenant conflict by design.

**Hermes verifies:** businesses.owner_telegram_id set on new biz; worker row moved;
biz 3 no longer has an active worker.

### Step 3 — Real data in (CSV import)

Hermes builds the CSV from the vault masters (Cheat Sheet + accounts/_overview.md): name,
schedule, gate/access notes, contact. Import through **import.html** (the real customer tool —
NOT direct DB calls). Verify: all 20 accounts, schedules parsed into RouteEntries
(Week A/B patterns), no dupes, gate codes visible on dashboard.

### Step 4 — Retire biz 3

Clear `owner_telegram_id` on biz 3 (else Geoff gets double nightly summaries), set
is_active=False. History stays. Do NOT delete.

### Step 5 — Recaps pilot

Enable recaps on ONE account with sarah@atlantahouseplant.com as the recipient (client
stand-in — pitfall: never send test recaps to real clients unannounced). Log a note →
owner approval ping → ✓ → recap email received. Later, Geoff decides if/when real clients
(Floyd's Stacey? Concord's Debbie?) get looped in for real — that's a sales asset, not a test.

### Steps 6–7 — Two weeks of real operations (the actual dogfood)

Daily expectations:
- 6:45am route push arrives with the day's stops (new tenant, Week A/B correct)
- Geoff logs notes from the route via Telegram (text AND voice) — stop using Hermes chat
  for route capture during the dogfood window
- Ask it things mid-route ("gate code for Luna", "what's open at Michelin")
- Create + close tasks ("Task for Floyd: check lobby ficus")
- 7pm nightly summary (email + Telegram) matches the day's reality
- Dashboard: check it daily on the phone — is it actually useful in sunlight at 44px?

**The dogfood rule:** if FieldNotes can do it, do it in FieldNotes. Hermes chat is the
fallback, not the primary. Every time Geoff reaches for Hermes instead, that's a finding.

### Step 8 — Fix-forward

Every finding → logged in `FINDINGS.md` (this directory) → fixed → regression suite green →
deployed. P0 = blocks daily use (fix same day). P1 = annoyance (fix same week). P2 = polish.

### Step 9 — Gold-check criteria (what "ready to sell" means)

- [ ] 10+ consecutive days of daily use with zero P0s open
- [ ] Morning push + nightly summary correct every weekday of week 2
- [ ] Parse quality: Geoff stops re-explaining notes (account match rate feels automatic)
- [ ] Dashboard used unprompted (Geoff actually opens it)
- [ ] Recap pilot: at least 3 approved recaps sent, zero held-for-safety on legit notes
- [ ] Billing: trial → paid transition works (or conscious decision to stay trialing during beta)
- [ ] Geoff's verdict: "I'd be mad if you took this away"

## Known watch-items going in (from skill pitfalls, not new findings)

- kimi-k3 parse latency is 10–20s/note (vs ~1s Grok) — noticeable on voice notes. Acceptable
  for webhook notes; if it feels broken in daily use, that's a finding, not a surprise.
- Tenant TZ: logs after 8pm EDT count as next UTC day (deliberate v2.x deferral) — will show
  up in nightly summaries if Geoff logs late.
- SMS channel outbound still vendor-blocked (AgentPhone 10DLC) — out of scope for dogfood.
- BETA49 as written = $30 off ANY plan (Solo would be $9/mo). Geoff's open pricing decision.
