# Stranger Test Report — 2026-07-19

**Method:** Simulated a brand-new customer walking the full chain: landing → checkout → onboarding → Telegram linking → logging notes → dashboard → daily summary.

**Verdict: the product cannot accept paying strangers today.** 7 breaks found. 1 (the worst) fixed same-night.

---

## 🔴 P0-1 — Public data exposure (FIXED ✅)
- `/api/dashboard/logs` had **no business filter and no auth** — returned all logs from all tenants.
- Real AHP client names + service notes were publicly visible at fieldnotesapp.io.
- **Fix:** per-business `dashboard_key` column; endpoint now requires `business_id` + matching `key` (403/422 otherwise). Verified locally and through the public tunnel.
- Keys live in DB. AHP dashboard URL: `/app/dashboard.html?biz=3&key=Mqz5GZXYk0Ch-yS_`

## 🔴 P0-2 — No onboarding path (OPEN)
- `frontend/start.html` is a leftover mock: asks customers to create their own bot via @BotFather (contradicts the product), form inputs are dead (no submit), links to `pricing.html` (404 on tunnel), says "14-day trial" (we sell 30).
- `onboarding.py` = create/get business only. No accounts setup, no worker invites.

## 🔴 P0-3 — Unknown-worker notes vanish into demo tenant (OPEN)
- `webhook.py`: unregistered Telegram user → silently logs as demo business (id 2).
- A real customer's worker would see "logged ✅" while their dashboard stays empty forever.

## 🔴 P0-4 — No worker invite/linking mechanism (OPEN)
- Workers can only be registered by direct DB insert. Needs Telegram deep-link flow: `t.me/Field_notesbot_bot?start=invite_TOKEN` → webhook `/start` payload → register worker to business.

## 🟠 P1-5 — Daily email summaries non-functional (OPEN)
- `RESEND_API_KEY` commented out in .env; FROM address is `fieldnotes@fieldnotes.ai` (domain not owned; would fail Resend verification).
- No scheduler triggers `/summary/email` anyway.
- **Recommendation:** for beta, send daily summary via Telegram to owner chat (zero infra). Email later via Resend + verified fieldnotesapp.io domain (DNS is on Cloudflare — one API call).

## 🟠 P1-6 — Stripe webhook unverified + no subscription→business link (OPEN)
- `/billing/webhook` accepts unsigned payloads (spoofable).
- No `stripe_customer_id` / `subscription_status` on Business — can't enforce tiers or know who paid.

## 🟡 P2-7 — Smaller items (OPEN)
- `summary.py` returns `business_name=""`.
- `/summary/today` and `/actions` endpoints still keyless (same leak class as P0-1 — need key check).
- `start.html` trial copy mismatch (14 vs 30 days).
- Webhook demo-fallback needs a disclosure message ("Demo mode — you're testing Precision HVAC").

---

## Fix plan
1. **Session A — The Onboarding Build** (THE unlock): real start.html (post-checkout: business name + paste accounts → create business → worker invite link) + bot deep-link registration + welcome message.
2. **Session B — Daily summary via Telegram** to owner; key-check remaining read endpoints.
3. **Session C — Stripe hardening:** webhook signature, subscription→business linkage, tier enforcement.
4. **Session D — Re-run full stranger test E2E.**
