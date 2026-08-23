# FINDINGS — AHP Dogfood Bug + UX Log

Every issue Geoff or Hermes hits during the dogfood window. P0 = blocks daily use (fix same
day), P1 = annoyance (fix same week), P2 = polish. A "finding" includes every time we had to
cheat the real customer path — a stranger would have been stuck there.

| # | Date | Sev | Area | Finding | Status |
|---|------|-----|------|---------|--------|
| 1 | 2026-08-23 | P1 | Onboarding | **Founder cold-signup stalled 15 days** (Aug 8→23, 4 asks). Step 1 was executed agent-assisted: Stripe customer/subscription via API, then the REAL public `/onboarding/signup` POST (browser UA, stranger payload). The only stranger-flow surface NOT experienced: Stripe's hosted checkout page UI. Lesson for the product: a distracted owner-operator will not find 10 uninterrupted minutes — onboarding must survive being finished "later" or by someone else. | Closed (worked around) |
| 2 | 2026-08-23 | P0 | Billing | **BETA49 was unredeemable by strangers.** The coupon existed but had NO Promotion Code object — Stripe checkout's promo box only accepts promotion codes, so a stranger typing BETA49 at checkout would be REJECTED. Every marketing post says "use code BETA49." Fixed: created promotion code `promo_1U7c5QI5nMxajhKyUGWlWwUZ` (code BETA49 → coupon BETA49). | ✅ FIXED |
| 3 | 2026-08-23 | P2 | Billing | Webhook linkage only fires on `checkout.session.completed`. Subscriptions created via API never park a PendingSubscription, so biz↔Stripe linkage had to be set by direct DB update (a cheat per dogfood rules — a stranger couldn't get here, and support-assisted signups WILL hit this if we ever create subs for them in the dashboard). Candidate fix: also handle `customer.subscription.created` in the webhook. | Open (documented) |
