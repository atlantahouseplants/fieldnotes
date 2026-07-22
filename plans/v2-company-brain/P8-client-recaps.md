# P8 — Client Recaps (proof-of-service, client-facing)

**Status: 🔲 NOT STARTED** · Depends on: P7 (✅ done) · Origin: Geoff night-drive brainstorm 2026-07-21; pre-seeded as "client receipts" concept in USE-CASE-MAP. Geoff approved spec'ing on 2026-07-21.

## The scenario this solves (real, not hypothetical)

AHP, June 2026: One Street's Claudia emails photos of healthy plants complaining *"we don't know when anyone was last out."* Geoff had to pull four service dates from the vault and write a defensive email. **A client recap kills that dispute before it starts:**

```
Rep logs a visit note → internal log + action queue (existing pipeline)
→ AI rewrites it CLIENT-SAFE → owner one-tap approves
→ branded email lands in the client's inbox before the rep leaves the parking lot
```

Timestamped, automatic, professional. Churn prevention + invoice justification + dispute armor in one feature. Every ICP buyer has had their own Claudia moment — this is the demo story for owners (Q&A is the demo for workers).

## Hard rules (the feature dies if these break)

1. **NEVER send the raw worker note.** "Milking the snake plants as long as possible" cannot reach a client. The client-safe rewrite is mandatory: if the LLM pass fails for any reason, the recap is HELD for the owner — it never falls back to sending the raw note.
2. **Approve-first by default.** Owner gets a chat ping with the recap preview → ✓ send / ✗ skip / edit. Per-account auto-send toggle earns its way in later, only after the owner trusts the rewrites. No silent sends at launch.
3. **Email first, SMS later.** Clients are office/property managers — email is free and carries no consent burden. SMS recaps wait for P3 (10DLC) + per-client opt-in + SMS budget caps (Crew tier).
4. **Per-account opt-in.** Recaps only fire for accounts explicitly enabled with a client email on file. Default = off.

## Data model changes

**`Account` additions:**
| Column | Notes |
|---|---|
| recap_enabled | Boolean, default False — explicit opt-in per account |
| recap_email | String, nullable — client's service-contact email (P2 import already carries contact fields; this is the designated recap recipient) |
| recap_auto_send | Boolean, default False — Phase 2; skip owner approval for trusted accounts |

**`recap_log` (NEW table):** id, business_id, account_id, service_log_id, client_text, status (pending_approval / sent / skipped / held), channel (email), approved_by_worker_id (null = owner), created_at, sent_at. Index (business_id, account_id, status).

## Flows

### 1. Trigger
Service log persists for an account with `recap_enabled=True` and `recap_email` set → enqueue recap draft. **Batching:** multiple notes for the same account within a visit window (~2h) merge into ONE recap — never spam a client 3× for one visit.

### 2. Client-safe rewrite (the core LLM pass)
New service `services/recap_writer.py`: parser output + log context → client-appropriate recap. Rules baked into prompt:
- Professional third person ("Your service was completed today…")
- Strip: internal jargon, costs, supplies, crew names, negativity about plant/property condition, anything hedged ("milking," "nursing," "barely")
- Keep: date, work performed, plants/areas serviced, honest condition notes phrased constructively, next-visit items the CLIENT should know ("two plants recommended for replacement next visit")
- Uncertain about a phrase → drop it. When in doubt, leave it out.
- Fallback chain same as parser (Grok→DeepSeek→OpenAI); all fail → status=held, owner pinged to write manually. NEVER raw-send.

### 3. Approval (reuse P7's ✓/✗ confirm machinery)
Owner chat ping: *"Recap for Smith Office ready: 'Serviced today — all plants watered and inspected, two flagged for replacement next visit.' Send? ✓ / ✗ / edit"*. Timeout (e.g. 4h) with no reply → stays pending, listed in nightly summary. Edit path: owner replies with replacement text → that sends.

### 4. Send
Existing `integrations/email.py::send_email()` (AgentMail primary → Resend fallback). Branded template: business name prominent, FieldNotes footer, one-line opt-out notice, reply-to = nothing client-visible (no list addresses). Record in `recap_log`, link on dashboard account page ("Recap sent Jul 21 ✓").

### 5. Setup paths (chat + dashboard, per the two-user model)
- **Owner chat:** "Recaps on for Smith: jane@smithoffice.com" → intent → confirm in plain words
- **Dashboard:** account page recap toggle + email field (fits P6 account-page patterns)
- Q&A: "which clients get recaps?" → list enabled accounts

## Tiering & cost

- **Email recaps = Team tier** · **SMS recaps = Crew** (post-P3). Beta tenants grandfathered (beta_all_access) — recaps follow the same gate path as other features; log `[GATED:recap]` attempts like P5 did for Q&A.
- Marginal cost per recap: 1 rewrite LLM call (fractions of a cent) + email send (free at scale). Margin-safe per the Jul 21 unit-economics analysis. SMS recaps inherit the per-tenant SMS budget rule when P3 lands.

## Tasks (implementable units)

1. Model changes + migration (`Account` cols + `recap_log` table; explicit defaults per pitfalls #5/#30)
2. `services/recap_writer.py` — client-safe rewrite + hold-on-failure
3. Trigger hook in `ingest.persist_parsed_note` (batching window logic)
4. Owner approval flow (reuse P7 YES-NO confirm + edit path + 4h timeout)
5. Send path via email.py + branded template + `recap_log` recording
6. Setup intents (chat) + dashboard account-page controls
7. Tier gate wiring (Team) + `[GATED:recap]` telemetry
8. Tests: full loop (log → draft → approve → sent + recorded); **jargon-leak test** ("milking the snake plants" must NOT appear in client text); hold-on-LLM-failure; batching (3 notes → 1 recap); tenant isolation; recap-off account = nothing fires
9. AHP dogfood: Geoff enables recaps for 1-2 friendly accounts (e.g. a property manager who'd appreciate it) and runs a real route week

## Acceptance criteria

- [ ] The Claudia scenario is structurally impossible: every enabled client's inbox has a timestamped recap after each visit
- [ ] No internal jargon/costs/supplies ever appear in client text (test proves it)
- [ ] Approve-first default; zero silent sends; LLM failure = held, never raw
- [ ] 3 notes on one visit = 1 recap
- [ ] Tenant isolation test passes
- [ ] Setup works identically via owner chat and dashboard
- [ ] Demo tenant (biz 2) has a recap-enabled fake account for sales demos ("watch the client's email")

## Pitfalls

- **The raw-note fallback is the product-killer.** Any code path that could send unprocessed worker text to a client is a P0 bug. Review criterion #1 for this phase (alongside tenant isolation).
- Batching window: anchor on the visit, not the clock — notes 90 min apart on the same account are one visit. Don't fire a second recap for a "forgot to mention" follow-up note; append to pending draft if still unapproved.
- Client email quality: P2 imports carry contact names/emails of varying quality — require explicit recap_email confirmation by owner at enable-time; never auto-pick an imported contact.
- Opt-out: every recap email carries a one-line unsubscribe note; an opt-out request = disable recaps for that account immediately and tell the owner. No marketing content in these emails — they're service records.
- Demo safety: the demo tenant's recaps must send to a Geoff-controlled address only — a stranger triggering a recap to a real external inbox during a self-serve demo is a spam incident.
- Owner chat vs rep chat for approvals: owner-only (P7 already established the role check — reuse it).
