# P9 — Parser Eval Harness ("measure, don't vibe")

**Origin:** Aug 5, 2026 — Geoff's X-link brief discussion (graph-architect article, its Step 20: evals). Our 13 regression suites test plumbing; NOTHING measures parse *quality*. Every model/prompt change to date has been eyeballed. xAI credit-block (Aug 3) made this urgent: the chain now falls to DeepSeek/OpenAI and we had no objective way to say whether that's worse.

## Scope

1. **Golden dataset** — `scripts/evals/golden_notes.json`: ~50 realistic field notes (shorthand, typos, voice-transcription artifacts) across the ICP verticals (plants, pool, lawn, pest, HVAC, cleaning), each with expected `account_hint`, `status`, and key phrases for issues/supplies/follow_ups.
2. **Harness** — `scripts/parser_evals.py`:
   - `--provider chain|xai|deepseek|openai|basic` — run the golden set against one provider in isolation or the full fallback chain.
   - Scoring (deterministic, explainable): account-match accuracy, status accuracy, token-overlap F1 on issues/supplies/follow_ups, avg latency, est. cost.
   - Writes JSON report to `scripts/evals/results/<timestamp>.json`.
   - `--baseline <file>` / `--save-baseline`: diff vs baseline, exit 1 on regression beyond threshold (account −5pts or status −5pts) → usable as a pre-commit / pre-model-swap gate.
3. **Small parser refactor** — extract `build_prompt()` from `parse_note` so the harness builds prompts identically to prod (no drift).

## Non-goals

- No LangGraph / graph framework. No changes to the live parse chain order.
- Not a Q&A eval (separate future task if this proves useful).
- No LLM-as-judge scoring — deterministic only (reproducible, free, honest).

## Acceptance criteria

- [ ] `python3 scripts/parser_evals.py --provider deepseek` runs 50 notes, prints score table, writes report.
- [ ] Baseline saved for every reachable provider; `--baseline` diff works and exits non-zero on forced regression.
- [ ] Baseline report committed so any future session can compare.
- [ ] README status board updated.

## Pitfalls (inherited)

- Never inline API keys in code (token-redaction pitfall) — harness reads env at runtime.
- xAI 403s (credit block) — `chain` runs will exercise the fallback path; that's the current prod reality and a valid thing to measure.
- Keep total run cost < $0.10 — 50 notes × 500 max_tokens is ~$0.01–0.02 on DeepSeek.
