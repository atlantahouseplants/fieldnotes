# P5 — Tiering & Feature Gates

**Status: 🔲 NOT STARTED** · Depends on: P1–P4 shipped + beta usage data (qa_events, log volume)

## Goal

Turn the v2 feature set into revenue packaging WITHOUT guessing. Beta users get everything; `qa_events` + usage data tells us what to gate.

## Proposed packaging (hypothesis to validate)

| Tier | Price | Includes |
|------|-------|----------|
| Solo | $39/mo | Logging + daily summaries + dashboard (v1 product) |
| Team | $79/mo | + **Ask FieldNotes (Q&A)** + CSV import + up to 5 workers |
| Crew | $149/mo | + SMS channel + route awareness + morning push + unlimited workers |

Rationale: Q&A is the wow — gate it at Team to create the upgrade moment. SMS is the premium channel — gate at Crew (also has real per-message costs). Stripe prices + BETA49 coupon already exist; gating is code, not billing rebuild.

## Scope

1. **Feature-gate middleware** (`backend/deps.py` extension): `require_feature(business, "qa" | "sms" | "routes")` — reads `Business.tier` (already set by Stripe linkage), returns 402-with-upgrade-message when gated. Beta override flag (`Business.beta_all_access=True`) so current beta keeps everything while we observe.
2. **Upgrade path in-product:** gated attempt → "Ask FieldNotes is on Team — upgrade: <checkout link>". Stripe checkout already carries plan metadata; webhook linkage already works.
3. **Usage metering:** qa_events count, SMS sent count, worker count per business — surface on owner dashboard ("you've asked 47 questions this month").
4. **Grandfathering:** beta users keep beta pricing forever (BETA49 promise) — gates apply to features only, not price.

## Decision inputs needed before gating (from beta data)

- Do Solo users who try Q&A upgrade-ask? (gate = conversion lever vs frustration)
- SMS cost per tenant per month at real volume (AgentPhone overage pricing) → does Crew price cover it?
- Worker-count distribution of signups → is 5 the right Team cap?

## Tasks

1. `require_feature` middleware + tier→feature map (single config dict)
2. Beta override flag + migration
3. Gated-attempt UX (upgrade prompt + checkout link)
4. Usage counters + dashboard display
5. Tests: Solo blocked from Q&A with clean message; Team passes; beta flag overrides
6. Pricing page update (v2 feature matrix) — ALSO fix the P2-leftover: pricing.html still old pre-rebrand green theme

## Acceptance criteria

- [ ] Gates enforced server-side (never trust frontend)
- [ ] Upgrade flow: gated attempt → checkout → webhook → feature unlocks without support touch
- [ ] Existing beta tenants unaffected (all-access flag)
- [ ] Usage numbers visible to owner

## Pitfalls

- Don't gate until ≥2-3 paying tenants exist — gating a product nobody pays for yet is theater.
- Stripe webhook already links subs → businesses by email; keep that as the single source of tier truth (no manual tier edits without an audit note).
