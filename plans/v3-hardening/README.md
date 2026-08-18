# FieldNotes v3 — Hardening Track ("Safe at Volume")

**Status board (update after EVERY work session + commit):**

| Phase | Name | Status | Owner agent | Last touched |
|-------|------|--------|-------------|--------------|
| H1 | Kill the WSL dependency | 🔨 CODE DONE (Aug 18, commit d78034e) — in-process scheduler (route push 6:45a + nightly 7p ET, ET/DST-aware, grace windows, founder alerts), env-gated FIELDNOTES_SCHEDULER_ENABLED=1, 24-check suite + boot smoke green. ⏳ CUTOVER IN FLIGHT: cron ba90deca4220 flips the Railway var ~9:15pm ET + pauses WSL crons (2a920ed016a0, b72aed0861be); verify cron 80bee721f9bd checks first live fire 7:05am Aug 19 | hermes-tg-session | 2026-08-18 |
| H2 | Disaster recovery (backups + drilled restore) | 🔲 not started | — | — |
| H3 | Dashboard access control v1 (revocable per-person links, key rotation) | 🔲 not started | — | — |
| H4 | Audit attribution (who did what) | 🔲 not started | — | — |
| H5 | Per-tenant cost/usage guardrails | 🔲 not started | — | — |
| H6 | Uptime & deploy safety | 🔲 not started | — | — |
| H7 | Tenant data export | 🔲 not started | — | — |
| H8 | Secrets-rotation runbook (leaked link response) | 🔲 not started | — | — |

Legend: 🔲 not started · 🔨 in progress · ✅ done · ⏳ blocked

---

## Why this track exists (strategic context — read first)

Origin: Aug 18 2026 discussion — "is FieldNotes enterprise-ready, can it scale, or are we capped?"

**The honest verdict, ratified with Geoff:**

1. **FieldNotes is NOT enterprise-ready and is not trying to be.** Enterprise = SSO/SAML, RBAC, audit compliance, SOC2, SLAs, procurement billing. That's Jobber/FieldRoutes territory. We do not chase it now.
2. **Nothing in the architecture caps scale.** FastAPI + managed Postgres handles hundreds of SMB tenants with incremental spend, not rewrites. The real bottleneck at volume is LLM parse latency + provider rate limits — solved with a queue/workers, not a rebuild.
3. **The actual cap is GTM + Geoff's time**, not the stack. Founder-led onboarding = 1–2 signups/wk. That stays the primary focus.
4. **BUT the conversation exposed real gaps that hurt us at 5 techs, not just 50** — a home machine in the prod path, no tested restore, a shared dashboard key that can't be revoked per person. A fired tech walking out with a link to every customer's gate code is a problem for a 3-truck plumber too. **That's what this track fixes.**

**Scope rule (binding):** this track makes FieldNotes safe for the ICP at volume — dozens to hundreds of 1–15 tech tenants. It does NOT add enterprise features. If a task smells like SSO, SAML, SOC2, org hierarchies, or procurement invoicing, it belongs to a future v4 decision gated on revenue, not here.

**Guardrail (same as P10):** hardening runs in parallel with founder-led sales. It must NEVER become a build cycle that delays selling. Selling is the priority; hardening fills gaps between sales work.

## Market math (estimates — labeled as such, not documented facts)

- US field service businesses skew overwhelmingly small: landscaping alone ~600k businesses, the large majority under 10 employees. Pest control, pool, cleaning, HVAC maintenance similar shape.
- 500 tenants at ~$70/mo average ≈ $420k ARR. No enterprise contract required.
- Upmarket move (20–50 tech companies) becomes viable only after H3 + H4 land AND SMB traction proves the wedge. That's the honest answer to "can we take a 50-employee client's money": not today, yes later, and we don't need to.

## Phases

### H1 — Kill the WSL dependency (FIRST — protects current tenants today)
The morning route push, nightly owner summaries, and watchdogs fire from cron scripts on Geoff's WSL box hitting the Railway API. Geoff's home machine is in the production path: WSL reboot, Windows update, power, travel = summaries silently die (watchdog alerts, but that's a page, not resilience).
- Move route push + nightly summary scheduling server-side: in-process scheduler (APScheduler/asyncio task in the FastAPI app), Railway cron service, or GitHub Actions on a schedule hitting the CRON_SECRET endpoints.
- Watchdogs can stay on WSL (they're monitoring, not serving) — but each script must be classified: prod-path (must move) vs observer (can stay).
- Acceptance: with Geoff's laptop OFF for 48h, route pushes and nightly summaries still fire for all tenants. Verified by test + one live weekday observation.
- Pitfall: in-process schedulers duplicate work if Railway ever runs 2 replicas — gate with a DB lock row or keep it single-replica + document.

### H2 — Disaster recovery (SECOND — protects current tenants today)
Railway managed PG has backups, but no one has ever restored one. "Probably recoverable" is not a plan.
- Document the restore path (Railway PG backup → restore → verify counts per tenant).
- DRILL IT once into a throwaway Railway PG instance: restore, run the tenant-count verification queries, record timings.
- Add a monthly cron that dumps `pg_dump` to off-Railway storage (S3/R2/Drive) so a Railway account problem isn't existential.
- Acceptance: a written runbook at `plans/v3-hardening/DR-RUNBOOK.md` + one completed drill with evidence + automated monthly off-site dump confirmed by file existence check.

### H3 — Dashboard access control v1 (unlocks 10–20 tech tenants)
Today: one `dashboard_key` per business, in the URL, shared by everyone. A leaked or departed-employee link = full access to every customer's gate codes, no per-person revocation.
- Per-person invite links (name-scoped tokens) for the dashboard, revocable individually.
- One-click key rotation in the dashboard ("burn all links" button for the owner).
- Read-only vs owner-level distinction if cheap (view-only link for a manager who shouldn't edit).
- Acceptance: owner can issue 3 links, revoke 1, and the revoked link 403s within a minute; suite covers cross-tenant + revoked-token cases.
- Pitfall: key-in-URL is still the model (no logins — Geoff's UI mandate) — this phase makes the keys per-person and burnable, it does not add auth.

### H4 — Audit attribution
At 5 people "who logged this" is trivia; at 15 it's a dispute. Worker attribution exists on ServiceLog — extend the pattern.
- Attribute every mutation (add-account, close-task, recap approve/skip/edit, CSV import) to a worker/owner identity.
- Simple owner-visible "recent changes" view in the dashboard.
- Acceptance: every write path in the app stores an actor; owner can answer "who closed this task" from the UI.

### H5 — Per-tenant cost/usage guardrails (protects margin)
Already specced in the pricing strategy, build when usage grows:
- Per-tier fair-use caps via existing `/accounts/usage` + qa_events metering (soft cap = warn, hard cap = upgrade prompt).
- SMS monthly budget cap per tenant (Crew feature, 1–2¢/segment adds up).
- Weekly COGS report cron flagging any tenant costing >20% of plan price.
- Acceptance: a synthetic heavy tenant trips soft then hard cap in the suite; COGS cron fires and reports.

### H6 — Uptime & deploy safety
- Verify Railway restart policy + healthcheck-driven zero-downtime deploys (currently every push = brief blip).
- Confirm UptimeRobot coverage on the critical API paths, not just `/` and `/health`.
- Acceptance: deploy during active demo traffic with no failed requests (verify with a probe loop during a real deploy).

### H7 — Tenant data export
Any tenant can leave with their data: full CSV/JSON export (accounts, logs, tasks, action history) from the dashboard.
- This is a SALES ASSET, not just hygiene — "your data is never locked in" kills a common objection for $79 buyers.
- Acceptance: export downloads, re-imports cleanly into a fresh tenant via the P2 CSV path.

### H8 — Secrets-rotation runbook
Leaked-link response is currently "rotate dashboard_key" — undocumented and manual.
- One-click rotation (lands with H3), plus a written runbook: what to rotate, what breaks, who to notify.
- Acceptance: runbook at `plans/v3-hardening/KEY-ROTATION.md`, drill once on a test tenant.

## Build order

**H1 → H2 first** (protect paying/dogfood tenants TODAY) → **H3 → H4** (unlock bigger tenants) → **H5** (margin) → **H6/H7/H8** as capacity allows. Sales work preempts any phase at any time.

## Multi-agent coordination rules

Same rules as v2 (`plans/v2-company-brain/README.md`): git pull first, claim on the board before starting, spec + acceptance criteria per phase, tenant isolation is the #1 review criterion, update board + commit after every session, Geoff approves completions. Commit format: `v3(Hx): <what changed>`.
