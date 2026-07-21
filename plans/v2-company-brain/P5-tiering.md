# P5 — Tiering & Feature Gates

**Status: 🔲 NOT STARTED** · Depends on: P1–P3 live + beta usage data from `qa_events` · LAST phase by design

## Principle

**Give beta users everything. Watch what they use. Fence where value proves.** No paywall drawing before real usage data exists — the `qa_events` table (P1) + worker activity stats tell us which tier each feature belongs in.

## Proposed tiers (draft — validate against beta usage)

| | Solo $39 | Team $79 | Crew $149 |
|---|---|---|---|
| Voice/text logging | ✅ | ✅ | ✅ |
| Daily summaries | ✅ | ✅ | ✅ |
| Workers | 1 | up to 5 | up to 15 |
| **Q&A assistant** (P1) | — | ✅ | ✅ |
| **CSV client import** (P2) | 1 import | ✅ unlimited | ✅ unlimited |
| **SMS channel** (P3) | — | — | ✅ |
| **Route awareness** (P4) | — | — | ✅ |

Rationale: Q&A is the mid-tier hook (owner + small crew). SMS + routes are ops-heavy features = top tier, and SMS has real per-message cost to us (AgentPhone) so it must sit where margins cover it. Solo stays the foot-in-door.

Reference pricing sanity: Skimmer (pool software) ~$39/user/mo; Jobber ~$49-249/mo. We're priced under both with zero-app-install as the wedge.

## Implementation (when the time comes)

1. `Business.tier` already exists (Stripe-linked). Add `PLAN_FEATURES` map in one module — single source of truth.
2. Gate middleware: decorator `requires_feature("qa")` on the Q&A route path, channel send, route push. Returns an upsell message, not an error: "Q&A is on the Team plan — tap to upgrade" (Stripe link).
3. Worker-count enforcement at invite time (count active Workers vs plan cap).
4. SMS cost guard: per-tenant monthly SMS budget in config; warn owner at 80%, soft-stop at 100% with overage opt-in.
5. Grandfather rule: every beta business gets Team features free for 6 months post-gating, stated in writing — beta goodwill is worth more than $40/mo.

## Decision inputs to collect during beta (from qa_events + usage)

- % of businesses that ask ≥1 question/week (Q&A stickiness)
- Questions per worker per day (value density)
- Which question types dominate (gate codes vs history vs route) — shapes marketing
- Import usage: how many actually upload a CSV vs dribble accounts in by hand

## Acceptance criteria

- [ ] Gates enforce without breaking existing flows (all tests green with tier=solo/team/crew fixtures)
- [ ] Upsell path converts in-product (Stripe checkout link in the upsell message)
- [ ] Beta grandfathering applied + communicated
- [ ] SMS cost guard tested (simulate 100% budget)

## Pitfall

- Do NOT gate the logging pipeline itself — a tenant who hits a paywall on their core log flow churns and tells people. Gates only on the v2 value-adds.
