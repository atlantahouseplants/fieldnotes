# Jev (TypeSafe System One) — FieldNotes Parse-Chain Integration Spec

**Status:** DRAFT v1 — Sep 16, 2026 · **Driver:** Geoff · **Prereq:** TypeSafe early access (waitlist submitted Sep 16, use-case bump sent)
**Why now:** Parse chain DEGRADED (moonshot credit/auth, watchdog silent 11+ days). Re-point decision was already open; Jev is a candidate, not a foregone conclusion.

---

## 1. The fit

FN's core loop is System-One-shaped: unstructured note in → structured decisions out, with a confidence-gated route.

| FN task | Jev-shaped? | Notes |
|---------|-------------|-------|
| Account classification (which of tenant's accounts) | ✅ perfect | high-cardinality choice; Jev supports 255, 2-stage for more |
| Issue / supply / follow-up extraction into schema | ✅ perfect | typed output, no type errors |
| Confidence-gated routing (auto-log vs ask worker) | ✅ THE unlock | calibrated probability replaces our heuristic |
| Missed-stop / alert decisions | ✅ | score + threshold |
| Recap emails, owner summaries, recaps | ❌ stays LLM | text generation — Jev can't |
| Worker Q&A ("company brain") | ❌ stays LLM | text generation |

**Architecture takeaway:** Jev becomes the *decision layer*; LLM stays for the *language layer*. Cheaper + faster + honest confidence on the high-volume path; LLM spend drops to the minority of calls that truly need strings.

## 2. Cost math (order of magnitude)

- Current: chat LLM per note — seconds latency, ~5-10k tokens in + generation out.
- Jev: $0.042/MTok in, output free, 70–500ms. Same note ≈ 100x cheaper, ~10-50x faster.
- Geoff's COGS-per-note anxiety is directly addressed on the highest-volume call path.

## 3. Integration plan (phases)

**Phase 0 — Access & adapter (days)**
- Get early access; read docs.typesafe.ai; spike one query against their playground with a real FN parse example.
- Build `backend/services/parser/jev_client.py` implementing the SAME interface as the current parser (adapter pattern — Jev is one backend behind `parse_note()`, swappable, no big-bang).

**Phase 1 — Shadow mode (1-2 weeks)**
- Route 100% of production notes through BOTH parsers; log Jev output alongside current; zero user-facing change.
- Compare: account-match agreement, extraction F1 on a hand-labeled 50-note sample, confidence calibration (does 0.9 confidence = 90% right?).
- Report disagreements to TypeSafe (we told them we would — goodwill + it's in our interest).

**Phase 2 — Confidence-gated primary (if shadow holds)**
- Jev primary: confidence ≥ threshold → auto-log; below → ask worker (existing flow).
- LLM fallback on Jev outage/low-cardinality overflow (>255 accounts — not our size, but note it).
- Kill moonshot dependency for the parse path; keep LLM for summaries/recaps.

**Phase 3 — Extend**
- Missed-stop detection + alert scoring onto Jev.
- AHP lead-engine scoring is the SECOND tenant of the same pattern (separate small project).

## 4. Risks / honest unknowns
- **Early access unknowns:** rate limits, SLA, real-world latency from our region, pricing sustainability (they admit it's unproven).
- **Vendor evals are self-reported** — shadow mode on OUR data is the only eval that matters. Go/no-go on our numbers, not theirs.
- **Calibration on OUR distribution** is the specific thing to verify — their party trick means nothing if it's miscalibrated on worker voice-note gibberish.
- **Migration cost is low** (adapter + shadow), so downside is capped at a wasted spike.

## 5. Go / No-Go criteria (decide after Phase 1)
- GO: ≥ current parser agreement on account match AND extraction, calibrated confidence (reliability curve ~diagonal), p95 latency < 1s, cost < 10% of current.
- NO-GO: stay on current/replacement LLM; document why; revisit in 6 months.

*Owner: Geoff decision · Hermes executes phases 0-2 · Report to Geoff at each gate.*

---

## 6. Access packet (everything needed on day one)

**Links:**
- Announcement: https://typesafe.ai/blog/introducing-system-one-models-and-jev
- Docs: https://docs.typesafe.ai/
- LLM adapter (for shadow-mode LLM side): https://github.com/typesafe-ai/system-one-adapter-python
- Their workflow evals: https://evals.typesafe.ai/
- Pricing: $0.042/MTok input, output free · Latency claim: 70–500ms · Cardinality max 255 (2-stage beyond)

**Waitlist status:** Geoff signed up + use-case bump sent Sep 16, 2026 (answer below). Access arrives via his email — when Geoff says "Jev access landed," start Phase 0.

**Use-case answer submitted to TypeSafe (verbatim, for reference):**
> I run two production systems where Jev's exact shape — structured decisions with calibrated confidence — is the missing component.
> 1. FieldNotes (live B2B SaaS, Railway + Postgres, real paying usage): field service workers send voice/text notes via Telegram; we parse them into structured service logs — account classification, issue/supply/follow-up extraction — then route low-confidence results back to the worker for confirmation. Today that parsing runs through a chat LLM: slow (multi-second), expensive at volume, and overconfident in exactly the places we need honesty. Confidence-gated routing ("parse autonomously above threshold, ask below") is core to our UX, and calibrated probabilities would make it principled instead of heuristic. Cost matters — we're a startup watching COGS per note.
> 2. Lead qualification for my service business (live): inbound website leads get scored and routed (hot → immediate draft, cold → nurture). Classification + probability is the whole job; latency and cost per call directly shape the product.
> 3. Prediction-market signal pipeline: we grade model-generated trade signals against market prices — a calibration-native workload where an honest probability IS the product.
> We'd integrate via your Python adapter in shadow mode first, compare against our current LLM parses on real production data, and report disagreements back. Happy to share eval results and be a reference if it works.

**Day-one checklist (Phase 0 expanded):**
1. Read docs.typesafe.ai (query schema, probability semantics, limits)
2. Playground spike: 3 real FN notes → hand-compare outputs vs current parser
3. Build `backend/services/parser/jev_client.py` behind the existing `parse_note()` interface
4. Wire shadow logging (both parsers, zero user-facing change)
5. Go from there per spec phases 1–3
