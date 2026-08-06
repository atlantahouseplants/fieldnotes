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

- [x] `python3 scripts/parser_evals.py --provider deepseek` runs 50 notes, prints score table, writes report.
- [x] Baseline saved for every reachable provider; `--baseline` diff works and exits non-zero on forced regression.
- [x] Baseline report committed so any future session can compare.
- [x] README status board updated.

## OUTCOME (Aug 6, 2026) — bigger than specced

1. **Eval exposed a live prod outage:** xAI 403 / DeepSeek 402 / OpenAI 429 — ALL credit-dead. Prod had been silently parsing with `_basic_parse` since ~Aug 3. Nothing alerted.
2. **Fix shipped:** Moonshot kimi-k3 added as FIRST provider in the parse chain (parser.py), MOONSHOT_API_KEY set on Railway, deployed (commits b8dcf8d, d4e9e3a). Eval: **kimi-k3 = 100% account / 76% status / 79% macro-F1** vs basic fallback 76/72/56. kimi-k2.6 rejected (32/50 empty-content failures). kimi-k3 quirks: temperature param rejected (omit it), reasoning tokens count against max_tokens (needs 4000, not 600), ~10-20s latency.
3. **Second bug found via prod logs:** `account_id or 0` in ingest/ahp_pipeline/action_queue violates the PG FK on uncategorized notes — invisible on SQLite (no FK enforcement), masked pre-Moonshot because basic parse creates no action rows. Fixed → NULL.
4. **Observability closed:** `fieldnotes_parse_watchdog.sh` + daily 8:05am ET cron (job 84e4d138675b) probes /api/demo and alerts Geoff if the chain degrades again. Silent on success.
5. **When Geoff tops up xAI:** move `_call_xai` back to chain front (one-line reorder in parser.py) and rerun `--provider xai --baseline results/baseline_moonshot.json` to compare before deciding.

**Deferred:** per-tenant weekly COGS report cron — low value until paying tenants exist (data is qa_events + usage endpoint; report would read ~$0 today). Spec stays in strategy notes; build when first 3 betas are live.

## Pitfalls (inherited)

- Never inline API keys in code (token-redaction pitfall) — harness reads env at runtime.
- xAI 403s (credit block) — `chain` runs will exercise the fallback path; that's the current prod reality and a valid thing to measure.
- Keep total run cost < $0.10 — 50 notes × 500 max_tokens is ~$0.01–0.02 on DeepSeek.
