#!/usr/bin/env python3
"""
P9 — FieldNotes Parser Eval Harness ("measure, don't vibe")

Runs the golden note dataset against a single LLM provider or the full
production fallback chain, scores results deterministically, and can
diff against a saved baseline (regression gate for model/prompt swaps).

Usage:
    python3 scripts/parser_evals.py --provider deepseek
    python3 scripts/parser_evals.py --provider chain --save-baseline
    python3 scripts/parser_evals.py --provider openai --baseline scripts/evals/results/baseline_deepseek.json

Exit code 1 if scores regress vs baseline beyond threshold (5 pts on
account or status accuracy) — wire into pre-model-swap checks.
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = Path(__file__).resolve().parent / "evals"
RESULTS_DIR = EVALS_DIR / "results"

# --- env: load BEFORE importing parser (it reads keys at module level) ---
from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")
load_dotenv(Path.home() / ".hermes" / ".env")  # XAI/DeepSeek/OpenAI keys live here

sys.path.insert(0, str(REPO_ROOT))
from backend.services import parser  # noqa: E402

# Rough per-call cost estimates (USD) — update when pricing changes.
COST_PER_CALL = {
    "moonshot": 0.002,   # kimi-k3 estimate — verify against Moonshot billing
    "xai": 0.005,        # grok-4.5 estimate from strategy session
    "deepseek": 0.0004,
    "openai": 0.0003,    # gpt-4o-mini
    "basic": 0.0,
    "chain": None,       # measured per-note provider unknown; reported as n/a
}

STATUSES = {"all_good", "issues_found", "needs_supplies", "follow_up_needed", "urgent"}
STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "at", "for", "is", "it",
    "its", "be", "by", "we", "i", "x2", "next", "with", "from", "needs", "need",
}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(s).lower())).strip()


def tokens(s: str) -> set:
    return {t for t in norm(s).split() if t and t not in STOPWORDS}


def account_match(expected: str, got: str) -> bool:
    e, g = norm(expected), norm(got or "")
    if not e or not g:
        return False
    return e == g or e in g or g in e


def array_f1(expected_phrases: list, produced_items) -> float:
    """Token-overlap F1 between expected key phrases and produced array items."""
    if isinstance(produced_items, str):
        produced_items = [produced_items]
    if not isinstance(produced_items, list):
        produced_items = []
    exp_tokens = set()
    for p in (expected_phrases or []):
        exp_tokens |= tokens(p)
    prod_tokens = set()
    for p in (produced_items or []):
        if isinstance(p, str):
            prod_tokens |= tokens(p)
    if not exp_tokens and not prod_tokens:
        return 1.0
    if not exp_tokens or not prod_tokens:
        return 0.0
    tp = len(exp_tokens & prod_tokens)
    precision = tp / len(prod_tokens)
    recall = tp / len(exp_tokens)
    return 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)


async def run_one(provider: str, note: str, accounts: list) -> dict:
    prompt = parser.build_prompt(note, accounts)
    t0 = time.time()
    if provider == "chain":
        result = await parser.parse_note(note, accounts)
        return result
    if provider == "basic":
        return parser._basic_parse(note)
    fn = {"xai": parser._call_xai, "deepseek": parser._call_deepseek,
          "openai": parser._call_openai, "moonshot": parser._call_moonshot}[provider]
    result = await fn(prompt)
    result["processing_time_ms"] = int((time.time() - t0) * 1000)
    return result


async def run_eval(provider: str, data: dict, concurrency: int = 5) -> dict:
    accounts = data["known_accounts"]
    notes = data["notes"]
    sem = asyncio.Semaphore(concurrency)
    failures = []

    async def one(entry):
        async with sem:
            try:
                result = await run_one(provider, entry["note"], accounts)
            except Exception as e:
                result = {"_error": f"{type(e).__name__}: {e}"}
                failures.append({"id": entry["id"], "error": result["_error"]})
            return entry, result

    pairs = await asyncio.gather(*[one(e) for e in notes])

    per_note, acct_hits, status_hits = [], 0, 0
    f1_fields = {"issues": [], "supplies": [], "follow_ups": []}
    latencies = []

    for entry, result in pairs:
        exp = entry["expected"]
        if "_error" in result:
            per_note.append({"id": entry["id"], "error": result["_error"]})
            continue
        am = account_match(exp["account_hint"], result.get("account_hint", ""))
        sm = result.get("status") == exp["status"]
        acct_hits += am
        status_hits += sm
        row = {
            "id": entry["id"],
            "account_ok": am,
            "status_ok": sm,
            "got_account": result.get("account_hint"),
            "got_status": result.get("status"),
            "latency_ms": result.get("processing_time_ms", 0),
        }
        for f in f1_fields:
            f1 = array_f1(exp.get(f), result.get(f))
            f1_fields[f].append(f1)
            row[f"{f}_f1"] = round(f1, 3)
        latencies.append(row["latency_ms"])
        per_note.append(row)

    n = len(notes)
    ok = n - len(failures)
    avg = lambda xs: round(sum(xs) / len(xs), 4) if xs else 0.0
    report = {
        "provider": provider,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_notes": n,
        "n_failed_calls": len(failures),
        "scores": {
            "account_accuracy": round(acct_hits / n, 4),
            "status_accuracy": round(status_hits / n, 4),
            "issues_f1": avg(f1_fields["issues"]),
            "supplies_f1": avg(f1_fields["supplies"]),
            "follow_ups_f1": avg(f1_fields["follow_ups"]),
        },
        "latency_ms": {
            "avg": int(sum(latencies) / len(latencies)) if latencies else 0,
            "max": max(latencies) if latencies else 0,
        },
        "est_cost_usd": (round(COST_PER_CALL[provider] * ok, 4)
                         if COST_PER_CALL.get(provider) is not None else "n/a (chain — mixed providers)"),
        "failures": failures,
        "per_note": per_note,
    }
    s = report["scores"]
    s["macro_f1"] = round((s["issues_f1"] + s["supplies_f1"] + s["follow_ups_f1"]) / 3, 4)
    return report


def print_report(r: dict):
    s, lat = r["scores"], r["latency_ms"]
    print(f"\n=== Parser Eval — provider: {r['provider']} — {r['n_notes']} notes ===")
    print(f"  account accuracy : {s['account_accuracy']*100:.1f}%")
    print(f"  status accuracy  : {s['status_accuracy']*100:.1f}%")
    print(f"  issues F1        : {s['issues_f1']*100:.1f}%")
    print(f"  supplies F1      : {s['supplies_f1']*100:.1f}%")
    print(f"  follow_ups F1    : {s['follow_ups_f1']*100:.1f}%")
    print(f"  macro F1         : {s['macro_f1']*100:.1f}%")
    print(f"  latency avg/max  : {lat['avg']}ms / {lat['max']}ms")
    print(f"  failed calls     : {r['n_failed_calls']}")
    print(f"  est. cost        : {r['est_cost_usd']}")
    misses = [p for p in r["per_note"] if not p.get("account_ok") or not p.get("status_ok")]
    if misses:
        print(f"\n  Misses ({len(misses)}):")
        for m in misses[:15]:
            print(f"    #{m['id']}: acct={m.get('got_account')!r} status={m.get('got_status')!r}"
                  f"  (acct_ok={m.get('account_ok')}, status_ok={m.get('status_ok')})")


def diff_baseline(current: dict, baseline_path: Path, threshold: float = 0.05) -> bool:
    base = json.loads(baseline_path.read_text())
    print(f"\n=== Diff vs baseline ({baseline_path.name}, provider: {base['provider']}, {base['run_at']}) ===")
    regressed = False
    for k in ("account_accuracy", "status_accuracy", "issues_f1", "supplies_f1", "follow_ups_f1", "macro_f1"):
        b, c = base["scores"][k], current["scores"][k]
        delta = c - b
        flag = ""
        if k in ("account_accuracy", "status_accuracy") and delta < -threshold:
            flag = "  <-- REGRESSION"
            regressed = True
        print(f"  {k:18s} {b*100:5.1f}% -> {c*100:5.1f}%  ({delta*100:+.1f} pts){flag}")
    return regressed


def main():
    ap = argparse.ArgumentParser(description="FieldNotes parser eval harness (P9)")
    ap.add_argument("--provider", required=True, choices=["chain", "moonshot", "xai", "deepseek", "openai", "basic"])
    ap.add_argument("--dataset", default=str(EVALS_DIR / "golden_notes.json"))
    ap.add_argument("--baseline", help="path to baseline report JSON to diff against")
    ap.add_argument("--save-baseline", action="store_true",
                    help=f"save report as results/baseline_<provider>.json")
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()

    data = json.loads(Path(args.dataset).read_text())
    report = asyncio.run(run_eval(args.provider, data, args.concurrency))
    print_report(report)

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"{stamp}_{args.provider}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nReport written: {out}")

    if args.save_baseline:
        bpath = RESULTS_DIR / f"baseline_{args.provider}.json"
        bpath.write_text(json.dumps(report, indent=2))
        print(f"Baseline saved: {bpath}")

    regressed = False
    if args.baseline:
        regressed = diff_baseline(report, Path(args.baseline))

    sys.exit(1 if regressed else 0)


if __name__ == "__main__":
    main()
