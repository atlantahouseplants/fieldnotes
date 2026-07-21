# FieldNotes — Master Use-Case Map

**Purpose:** every way an owner, manager, or field rep would use this product, mapped by workflow moment. Reference ICP: pool company (100 accounts, 5 techs) — applies to AHP, pest control, landscaping, HVAC, cleaning. Source: Geoff direction 2026-07-21 + how Geoff actually uses Hermes for AHP routes.

Status legend: ✅ live · 🔨 planned/spec'd (phase #) · 💡 concept (needs Geoff decision)

## Actors

- **OWNER** — pays, wants oversight without interruptions. Phone, between stops.
- **MANAGER/DISPATCHER** — bigger companies only; same dashboard as owner. (v2 = owner covers this role)
- **REP/TECH** — the user. Chat only. Zero tech tolerance. Often Spanish-first.
- **END CLIENT** — the pool owner. No access in v2 (💡 receipts, v3).

## 1. Before the day / before the stop

| Use case | Status |
|---|---|
| "What's my route today?" — morning push per rep | 🔨 P4 |
| Stop briefing: gate codes, access notes, dog warnings, contacts — ask, get answer | ✅ P1 |
| **Special requests for today's stops** ("at Smith ALSO fix the cover") | 🔨 **P7** |
| Supplies check — what parts/chemicals do I need on the truck today | 🔨 P7 (task supplies field) → surfaced in morning push |
| Schedule change / client canceled — owner updates, reps see it | 💡 (owner chat command: "cancel Smith today") |
| Bilingual: rep texts in Spanish, everything just works | 💡 parser handles free; VERIFY + market this — huge for ICP |

## 2. During the visit

| Use case | Status |
|---|---|
| Log the work (voice/text, zero training) | ✅ core |
| Ask anything (history, "what filter does this pool use?", gate code) | ✅ P1 |
| **Open tasks surface automatically when logging that account** ("heads up: 1 open task at Smith") | 🔨 P7 |
| Mark a task done ("fixed the cover") → loop closes, owner notified | 🔨 P7 |
| Exception: can't service (locked gate, no access) → logged, owner alerted | ✅ partially (issues flag) — formalize 💡 |
| Discovered work: "pump leaking, ~$400" → owner gets it, quotes client, approved work becomes a task | 💡 P7 extension (approval flow) |
| **Photo logging** (before/after — proof of service; pool/pest cos LOVE this) | 💡 Telegram photos → attach to ServiceLog. Verify webhook handles images |

## 3. After the visit

| Use case | Status |
|---|---|
| Completion confirmation to owner ("✅ Mike did the cover at Smith") | 🔨 P7 |
| Follow-up scheduling ("needs filter in 2 weeks") → becomes dated task | 🔨 P7 |
| Nightly owner summary (who did what, issues, supplies) | ✅ |
| Supplies restock rollup (across all reps → one order list) | ✅ partially in summary — P7 strengthens |
| **Client receipt**: "Your pool was serviced today — [summary]" email/text to end client | 💡 v3 — killer retention feature; SMS-to-clients = separate 10DLC use case, email version first |

## 4. Owner operations (dashboard home base — P6)

| Use case | Status |
|---|---|
| See today / action queue / activity | ✅ |
| Add account (button OR chat command) | 🔨 P6a/b |
| Log a note manually (button OR chat) | 🔨 P6a/b |
| **Delegate: "Task for Smith: repair cover, Mike, Thursday"** — chat or dashboard | 🔨 **P7** |
| **Per-account page: open tasks, special requests, notes for next service** | 🔨 P7 |
| Re-import client list (button on dashboard) | 🔨 P6a |
| Invite/deactivate reps | 🔨 P6a/c |
| "Are my guys using it?" usage strip | 🔨 P6c |
| Time-on-site per rep (implicit from log timestamps) | 💡 |
| Billing / plan management (Stripe portal) | 🔨 P6c |

## 5. Recurring & seasonal

| Use case | Status |
|---|---|
| Recurring schedules + "did we skip anyone?" | 🔨 P4 |
| Recurring maintenance ("filter every 3 months") → auto-task | 💡 P7 extension |
| Seasonal patterns (pool open/close, plant rotations) | 💡 |
| Weather holds (freeze → skip watering; rain → chemical notes) | 💡 OWM key already in env |

## 6. Exception & escalation flows

| Use case | Status |
|---|---|
| Urgent flag ("green pool — priority") → owner pinged immediately | ✅ partially (issues) — formalize 💡 |
| Client complaint → task → resolution tracking | 🔨 P7 (task from complaint) |
| Rep no-show / missed stop detection | 🔨 P4 |

## Priority read (my recommendation)

**P7 (account tasks + delegation loop) is the highest-value next build after P6a.** It's what Geoff described as "the liaison" — client request → owner → delegated → rep aware → supplies known → completed → confirmed. It converts FieldNotes from a log into an operations system, and it demos incredibly well. Everything 💡 stays parked until beta usage validates.
