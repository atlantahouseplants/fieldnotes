# P3 — SMS Channel (AgentPhone)

**Status: ⏳ BLOCKED — 10DLC/A2P registration in review (filed 2026-07-20, standard_brand, Atlanta Houseplants LLC). Watchdog cron `10dlc-status-watchdog` reports status changes. Do NOT start integration until status = approved.**

## Why this might be THE unlock

Pool/landscaping/field crews in the US do not live on Telegram — they live on SMS (and WhatsApp, later via Twilio). "Text this number your notes, ask it anything" requires ZERO app install, zero training, works on any phone. That is the actual product for the ICP.

## Scope

### 1. Channel abstraction (`backend/integrations/channel.py` — NEW)
Today the pipeline is Telegram-coupled (webhook shape, chat_id identity, telegram send). Introduce a thin seam:
- `InboundMessage(channel, sender_id, text)` — normalized. Telegram webhook and SMS webhook both produce this.
- `reply(channel, sender_id, text)` — routes to Telegram or AgentPhone send.
- Worker identity: Telegram = chat_id (existing); SMS = E.164 phone number. `Worker` model gets `phone_number` column; lookup by channel+id. Link flow: owner invites worker via SMS (AgentPhone sends invite text); worker's first reply registers them (mirrors the Telegram deep-link flow, token in the invite text body: "reply YES to join").

### 2. AgentPhone integration (`backend/integrations/agentphone.py` — NEW)
- Inbound: AgentPhone webhook → our `POST /webhook/sms` (verify shared secret in a custom header, same pattern as TELEGRAM_SECRET).
- Outbound: send SMS via AgentPhone API (API bank: `agent-account-provisioning` skill → `references/agentphone-api.md`; keys in `~/.hermes/.env` as AGENTPHONE_API_KEY / AGENTPHONE_AGENT_ID / AGENTPHONE_NUMBER).
- **Cloudflare 1010 pitfall:** Python urllib default UA gets blocked — always send a browser User-Agent (learned on the 10DLC filing, 2026-07-20).
- **Sending number decision (OPEN — Geoff decides):** AHP's AgentPhone number vs a dedicated FieldNotes number. Recommendation: dedicated number per REGION later; single shared FieldNotes number for beta (per-tenant numbers = cost/compliance sprawl; revisit at scale).

### 3. Compliance (non-negotiable for SMS)
- Every first outbound to a new worker includes opt-out language; honor STOP (AgentPhone/carrier handles STOP at network level — VERIFY this in their docs; if not, implement STOP/START keyword handling ourselves: STOP → Worker.is_active=False, no more sends).
- 10DLC filing covers OUR number's brand (Atlanta Houseplants LLC). Tenants texting THEIR workers from our number ride on our campaign — sample messages in the filing were written to cover service-notification use case. If a tenant wants to text their CLIENTS, that's a different use case = out of scope for v2.

### 4. Voice note path (stretch)
MMS/voice via AgentPhone voice minutes (250/mo free tier) — transcribe → same pipeline. Only if trivial; Telegram voice already works, SMS voice is nice-to-have.

## Tasks

1. `channel.py` seam + refactor webhook.py to use it (Telegram behavior byte-identical after refactor — regression test)
2. `Worker.phone_number` migration
3. `agentphone.py` send + webhook receive + secret verification
4. SMS invite flow (owner dashboard: "add worker by phone" → invite text → YES reply registers)
5. STOP/START handling (or verification that carrier handles it)
6. Q&A + logs work identically over SMS (intent routing is channel-agnostic after P1)
7. E2E: real phone → note → log; question → answer; STOP honored

## Acceptance criteria

- [ ] Full loop on a real phone with zero app install
- [ ] Telegram path untouched (all existing tests green)
- [ ] STOP honored, opt-out language on invites
- [ ] Per-tenant isolation maintained (phone number only belongs to one business per registration)

## Pitfalls

- 10DLC pending — build against AgentPhone SANDBOX/docs, flip on after approval. Watchdog cron will tell us.
- Carrier MMS size limits for long Q&A answers — truncate at ~900 chars with "reply MORE" continuation.
- Rate limits: AgentPhone API 429s (observed 125s cooldown on register endpoint) — back off, queue outbound.
