# P4 — Route Awareness (schedules, NOT calendar sync)

**Status: 🔲 NOT STARTED** · Depends on: P2 (schedule field exists) · Can parallel with P3

## Goal

Worker asks "what's my route today?" / "who am I doing tomorrow?" → answer from account schedule data. Owner gets per-worker route sheet in the morning. **Deliberately NOT Google Calendar OAuth** — schedule patterns on accounts answer 90% of route questions with 10% of the build. Calendar sync revisited only if beta users demand it.

## Scope

### 1. Schedule model
- `Account.schedule` from P2 (free text like "Mon/Thu", "every other Tue", "1st of month").
- Deterministic parser (`services/schedule.py`): normalize to weekday set + frequency. LLM-assisted parse ONLY at import time (one call per account batch), stored structured — zero LLM cost at query time.
- Week A/B parity: AHP runs bi-weekly rotations (see ahp-route skill) — support "A/B" pattern since our design partner needs it.

### 2. Route queries (extends `services/qa.py` from P1)
- "route today/tomorrow/Friday" → accounts whose schedule matches that weekday (+frequency check) → ordered list with addresses.
- "did we skip anyone this week?" → scheduled accounts with no ServiceLog this week → missed-stop detection (PRODUCT_SPEC already wanted this as `alerts.py` — fold it in here).

### 3. Morning route push (extends existing summary infra)
- Per-worker 7am Telegram/SMS: today's stops + any open action items for those accounts. Reuses `POST /summary/send-daily` pattern + FIELDNOTES_CRON_SECRET cron.

## Tasks

1. `services/schedule.py` parser + tests (Mon/Thu, A/B weeks, monthly, irregular free text)
2. Import-time schedule parsing in csv_import (P2 integration point)
3. Q&A route intents (route today / skipped this week)
4. Morning route push endpoint + cron script (`~/.hermes/scripts/fieldnotes_route_push.sh`, no_agent watchdog pattern like nightly summaries)
5. Missed-stop alert into daily summary
6. AHP dogfood: encode Geoff's real Week A/B rotation (source of truth: Wiki route-schedule + sarah@ calendar) and verify "route today" matches reality for 2 weeks

## Acceptance criteria

- [ ] "What's my route today" returns correct accounts for AHP's real bi-weekly rotation
- [ ] Missed-stop detection flags a skipped scheduled account in daily summary
- [ ] Morning push arrives per worker before 7:30am ET
- [ ] Zero LLM calls at query time (parse-once at import)

## Pitfalls

- Geoff makes OFF-SCHEDULE extra visits in summer — "route today" is the plan, not the truth; logs remain the record of what actually happened. Phrase answers as "scheduled today".
- A/B week anchor date must be configurable per business (AHP's anchor is known; new tenants set theirs at setup).
