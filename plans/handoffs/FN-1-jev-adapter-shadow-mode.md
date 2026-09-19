# HANDOFF — FN-1: Jev parse adapter + shadow mode (FieldNotes)

**From:** Hermes (default) · **To:** builder · **Date:** Sep 16, 2026
**Geoff directive:** "I want this implemented thoroughly where we need it. I think this is going to be big for us." Builder owns implementation; Hermes owns product decisions. PR-only — NEVER deploy to prod.

## Mission
Implement Phase 1 of the Jev parse-chain integration in the FieldNotes repo (`~/fieldnotes`): a TypeSafe (Jev) adapter behind the existing `parse_note()` interface, plus shadow-mode logging that runs Jev alongside the current parser on live notes with zero user-facing change.

## Read first (in order)
1. `fieldnotes/plans/jev-parse-chain-spec.md` — the full spec. Phase 0 RESULTS (§7) are DONE and PASS. Your work = Phase 1 + Phase 2 prep only.
2. `fieldnotes/CLAUDE.md` — repo conventions (v2/v3 programs, status board).
3. Skill `typesafe-ai` (installed in your profile) — the TypeSafe playbook. Live docs: https://docs.typesafe.ai/llms.txt (append .md to paths).

## Environment
- `TYPESAFE_API_KEY` is in your profile `.env`. Python SDK: `pip install typesafe-sdk` (or raw HTTP POST https://api.typesafe.ai/v1/systemone, Bearer auth, model `jev-latest`).
- Phase 0 spike proof (run by Hermes Sep 16): 3/3 account Choice matches (p .92–1.00), 3/3 status, correct follow-up Nouls, 375–530ms, ~700 tok/note ≈ $0.00003.

## Build (acceptance criteria)
1. `backend/services/parser/jev_client.py` implementing the SAME interface as the current parser so it slots behind `parse_note()` via config flag (e.g. `PARSER_BACKEND=current|jev|shadow`, default `current`). Adapter pattern — no big-bang, current parser untouched.
2. Question design per the skill: account = Choice over tenant's account names (+ `nomatch` option); status = Choice all_good/issues_found; supplies/follow-ups/customer-requests = Noul per type; issue/supply TEXT via the "select instead of generate" pattern (code splits candidate clauses/spans from the note, Jev picks; code assembles).
3. **Shadow mode:** when `PARSER_BACKEND=shadow`, every note runs BOTH parsers; Jev result + latency + token usage logged to a new `parse_shadow_logs` table (Alembic migration included) alongside the current parser's output. Zero change to user-facing behavior.
4. Cost telemetry: log input tokens per call; a daily-cost rollup helper (Geoff's COGS guardrail — cap config `JEV_DAILY_TOKEN_CAP` with fail-closed behavior to `current` parser).
5. Tests: unit tests for the adapter (mocked API) + a replay script `scripts/jev_shadow_replay.py` that re-runs the 37 historical service_logs through Jev and prints agreement metrics vs stored parses.
6. PR against `atlantahouseplants/fieldnotes` main with: summary, design notes, test output, replay metrics. DO NOT merge, DO NOT deploy, DO NOT touch Railway.

## Done when
PR link posted as the task artifact with all 6 acceptance items checked off. If anything blocks (API limits, SDK gaps), comment on the card with specifics and `kanban_block` — don't improvise scope changes.

## Out of scope (Phase 2+, later cards)
Confidence-gated primary cutover, missed-stop scoring, AHP lead-engine port, killing the moonshot dependency.
