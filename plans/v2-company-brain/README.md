# FieldNotes v2 — "The Company Brain"

**Status board (update after EVERY work session + commit):**

| Phase | Name | Status | Owner agent | Last touched |
|-------|------|--------|-------------|--------------|
| P1 | Q&A Assistant ("Ask FieldNotes") | ✅ DONE (Jul 20) — E2E verified, live in prod | hermes-tui-session | 2026-07-20 |
| P2 | Rich Client Import (CSV) | ✅ DONE (Jul 21) — committed b828e20, live in prod | hermes-tui-session | 2026-07-21 |
| P3 | SMS Channel (AgentPhone) | 🟢 READY — 10DLC APPROVED 2026-07-23 | — | — |
| P4 | Route Awareness (schedules) | ✅ DONE (Jul 21) — committed b828e20, live in prod | hermes-tui-session | 2026-07-21 |
| P5 | Tiering & Feature Gates | ✅ DONE (Jul 21) — committed 11d9e95, live in prod; beta tenants grandfathered (all-access) | hermes-tui-session | 2026-07-21 |
| P6 | Dashboard Home Base (owner UX) | ✅ DONE (Jul 22) — P6a quick-action bar + P6b owner chat commands (New account / Note for / invite), live in prod | hermes-tui-session | 2026-07-22 |
| P7 | Account Tasks (delegation loop) | ✅ DONE (Jul 22) — chat intents (create/close/YES-NO confirm), log-time + route-push surfacing, owner pings, dashboard Tasks section, 46-check suite; live in prod | hermes-tui-session | 2026-07-22 |
| P8 | Client Recaps (proof-of-service) | ✅ DONE (Jul 22) — LLM rewrite + safety filter (held, never raw-send), owner approve-first (✓/✗/edit), 2h visit-window merge, tier gate, nightly pending line, dashboard Recap column, suite green; live in prod | hermes-tui-session | 2026-07-22 |
| INFRA | Paying-Client Infrastructure | ✅ DONE (Jul 21) — Railway + managed PG live, data migrated, fieldnotesapp.io custom domain cut over (TXT-verified, SSL issued), Telegram webhook + Stripe endpoint on pretty domain, tenant isolation verified. Only remaining: UptimeRobot (task 7) + retire Cloudflare Tunnel after 24h clean (fallback meanwhile) | hermes-tui-session (Jul 21 INFRA redo) | 2026-07-21 |

Also read: `USE-CASE-MAP.md` (every workflow moment, tagged live/planned/concept).

Legend: 🔲 not started · 🔨 in progress · ✅ done · ⏳ blocked

---

## What we're building

FieldNotes v1 = voice/text → structured service logs + daily summaries (DONE, live, sellable).

**v2 = the company brain that rides along in the truck.** Workers don't just *log* — they *ask*. "What's the gate code for the Johnson house?" → instant answer from the company's own accounts + service history. "Never call the office again."

### The flywheel (why this wins)
Every note a worker sends already feeds the knowledge base. Logs → knowledge → answers. The product gets smarter the more a crew uses it. Competitors can't copy accumulated company knowledge with a feature checklist.

### Target use case (ICP for v2)
Pool cleaning service, ~3 field employees, fair route volume, metro Atlanta. Owner pays; workers use. Same shape as AHP, landscapers, pest control, mobile detailers, HVAC.

## Hard requirements (all phases)

1. **TENANT ISOLATION IS SACRED.** Every read/answer/retrieval filters `business_id`. A Q&A answer must NEVER leak across tenants. This is the #1 review criterion.
2. **Deliverable to paying clients.** Architecture choices must survive real money: managed hosting + Postgres (see INFRA-scale.md), health monitoring, no WSL-laptop single point of failure.
3. **Demo magic.** "Ask it the gate code" must work live in a sales demo. Keep a seeded demo tenant (business_id=2) with rich fake accounts for this.
4. **Beta users get everything.** No paywalls during beta — watch usage, then gate (P5).

## Build order

**P1 (Q&A) → P2 (CSV import) → P3 (SMS) → P4 (routes) → P5 (tiering). INFRA runs in parallel with P1/P2** — it blocks charging money, not building.

Q&A first because it transforms the pitch. CSV second because Q&A is only as good as the imported client data (cold-start). SMS third — gated on 10DLC approval anyway.

## Multi-agent coordination rules

1. **Read this file + the phase doc fully before touching code.** Each phase doc is self-contained: scope, tasks, acceptance criteria, verification steps, pitfalls.
2. **One agent, one phase at a time** unless phases touch disjoint files (P3 and P4 can parallel; P1 and P2 CANNOT — both touch onboarding/models).
3. **Update the status board + commit** at the end of every session. Commit format: `v2(Px): <what changed>`. Push to origin so parallel sessions stay synced — **always `git pull` before starting work** (parallel Hermes sessions share this repo).
4. **Claim work:** flip your phase to 🔨 with your agent/session name in the status board, commit, THEN start. Prevents double-work.
5. **Run the app locally to verify** (see CLAUDE.md / SKILL.md "Running Locally"). Never mark ✅ on code you haven't executed.
6. **Two-stage review per task** (subagent-driven-development): spec compliance first, code quality second. Tenant isolation is part of spec.
7. Geoff approves phase completions before the next phase launches. Report to him on Telegram, short.

## Key existing assets (do NOT rebuild)

- Tenant model + key-lock middleware: `backend/models.py`, `backend/deps.py` (13/13 endpoints verified)
- Message pipeline: Telegram webhook → AI parser (Grok→DeepSeek→OpenAI→basic) → `ahp_pipeline.py` → logs/actions
- Onboarding with paste-import: `backend/routes/onboarding.py` (accounts bulk-create from pasted list)
- Email: AgentMail primary (`backend/integrations/email.py`)
- Stripe: linked subs, signature-verified webhook (`backend/routes/billing.py`), tiers on Business
- AgentPhone account provisioned, keys in `~/.hermes/.env`, API bank in `agent-account-provisioning` skill → `references/agentphone-api.md`
- 10DLC/A2P registration: filed 2026-07-20, in review. Compliance pages live: ahp-pages.vercel.app (privacy/terms/opt-in). Status: `GET https://api.agentphone.ai/v1/register/status`. Watchdog cron: `10dlc-status-watchdog` (every 12h).
- Demo tenant: business_id=2 (public demo), AHP = business_id=3

## Pitch after v2

"Never call the office again. Your crew texts or asks FieldNotes anything — it logs the work AND answers from your own client data. $X/crew/mo."
