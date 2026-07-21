# P1 — Q&A Assistant ("Ask FieldNotes")

**Status: 🔲 NOT STARTED**

## Goal

Worker sends a QUESTION to the same Telegram chat → FieldNotes answers from that tenant's accounts + service history. Logging behavior unchanged for non-questions.

## User stories

- Worker: "gate code for Matsuda?" → "Gate: #8124. Door: 08870. Alarm: 2580 (disarm top-left). Suite is at the end on the left." (sourced from Account fields + past log notes)
- Worker: "when did we last service Luna?" → last ServiceLog date + summary
- Worker: "what did we replace at Iris last month?" → replacements extracted from logs
- Owner: "any open issues this week?" → action queue digest
- Unknown/no data → honest "I don't have that yet — log it after your visit and I'll know next time." NEVER hallucinate client facts. Provenance: answers cite which record they came from.

## Design

### 1. Intent routing (in `backend/routes/webhook.py`, before the log pipeline)
- One fast classify call (reuse parser provider chain: Grok→DeepSeek→OpenAI): `{"intent": "question" | "log", ...}`.
- Cheap heuristic FIRST to save tokens: starts with question word / ends with "?" / contains lookup verbs ("what's the code", "when did", "where is", "any issues") → question. LLM classify only if heuristic is ambiguous.
- `log` → existing pipeline, untouched. `question` → new Q&A service.

### 2. Retrieval (`backend/services/qa.py` — NEW)
Tenant-scoped only. Order:
1. **Account match** — fuzzy-match account names mentioned (reuse `account_hint` matching rules: exact-first; if ambiguous, ASK which account rather than guess).
2. **Structured fields** — Account columns: name, address, access notes, contacts (P2 adds gate_code etc.; P1 works with what exists today: `notes`/`details` fields — CHECK models.py and use what exists).
3. **Log history** — keyword search (SQL LIKE) over `service_logs.raw_notes` + parsed issues/replacements for the matched account(s), most recent first, cap ~10 entries.
4. **Action queue** — for "open issues" style questions, `action_queue.get_action_queue(business_id)`.

### 3. Answer synthesis
- LLM call with: question + retrieved records (JSON) → 2-4 sentence answer, plain text, Telegram-friendly. Prompt MUST instruct: answer ONLY from provided records; if insufficient, say what's missing; cite source record (account name / log date).
- Send via existing Telegram integration. Log the Q&A exchange as a lightweight record (new table `qa_events`: business_id, worker_id, question, answer, sources JSON, created_at) — becomes usage data for P5 tiering decisions.

### 4. Demo readiness
- Seed demo tenant (business_id=2) with 3-4 rich fake accounts incl. gate codes, a dog warning, a replacement history — so "ask it the gate code" wows in sales demos.

## Tasks (implementable units)

1. `qa_events` table + model (SQLAlchemy; follow models.py patterns; remember is_active/SQLite default pitfall — set defaults explicitly)
2. Intent heuristic + LLM classify fallback in webhook (with tests: question → qa path, log → existing path)
3. `services/qa.py`: account matching + retrieval assembly (tenant-scoped!)
4. Answer synthesis prompt + provider call (reuse parser's provider chain pattern)
5. Telegram send + `qa_events` write
6. Demo tenant seed script (`scripts/seed_demo_tenant.py`)
7. Tenant-isolation test: plant a secret in tenant A, ask from tenant B, assert absent
8. E2E through webhook locally: 5 test questions incl. ambiguous account, unknown fact, "open issues"

## Acceptance criteria

- [ ] Questions get sourced answers in <5s; logs still log (no regression on existing pipeline)
- [ ] Zero cross-tenant leakage (test 7 passes)
- [ ] Unknown facts get honest "don't have that" — no invented client data
- [ ] Ambiguous account → clarification question, not a guess
- [ ] Demo tenant answers "what's the gate code" live
- [ ] `qa_events` populated for every exchange

## Pitfalls

- **Prompt `{note}` replacement** — use `str.replace`, never `.format()` (curly-brace conflict, known pitfall).
- **Fuzzy patches on webhook.py** — after editing, run the AST function-list smoke test (skill pitfall #24). webhook.py got mangled once this way.
- **Account matching** — NO substring matching (pitfall #12). Exact/alias match or ask.
- Sync endpoint = no event loop — fire-and-forget side effects use sync httpx or BackgroundTasks (pitfall #25).
- Export XAI_API_KEY before launching server or parser silently falls to basic mode.
