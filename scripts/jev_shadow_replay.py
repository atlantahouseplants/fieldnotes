#!/usr/bin/env python3
"""
FN-1 — Jev shadow replay script.

Re-runs the historical service_logs through the Jev adapter and prints
agreement + calibration metrics vs the stored (current-parser) results.
Read-only — never writes to service_logs; only reads from the WSL sqlite
dev DB (fieldnotes.db, frozen/stale per repo convention, but perfectly fine
as a fixed historical-note corpus for this comparison).

Calibration (HFT/Jev manual rule — measure against OUR outcomes, not the
vendor's): Brier score + 10-bin reliability table over the three Noul
questions (supplies / follow-ups / customer-requests), ground truth = 1 if
the stored parse's array was non-empty, and a Brier over the status Choice's
confidence vs stored-status agreement.

Usage:
    /home/wallg/ahp-digital/lead-engine/.venv/bin/python scripts/jev_shadow_replay.py

Requires TYPESAFE_API_KEY in ~/.hermes/.env (loaded automatically) and
a working sqlalchemy env (this repo's requirements aren't installed
system-wide on this box — the lead-engine venv mirrors them closely
enough for read-only model access + jev_client's httpx-only deps).
"""
import asyncio
import json
import os
import sys
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")
load_dotenv(Path.home() / ".hermes" / ".env")  # TYPESAFE_API_KEY lives here

from backend.services.parser import jev_client
from backend.services.parser import jev_calibration

# NOTE: the WSL dev sqlite DB (fieldnotes.db) is FROZEN/stale per repo
# convention and predates several PG-only schema migrations (e.g. P8's
# accounts.recap_enabled) — the SQLAlchemy ORM models can't SELECT * on
# a schema-drifted sqlite file. This script only needs a few columns from
# 2 tables, so it reads them with raw sqlite3 (no ORM), which tolerates
# the drift fine — a purely read-only, additive-columns-only situation.
DB_PATH = REPO_ROOT / "fieldnotes.db"


def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _known_accounts_for(conn, business_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT name, shorthand FROM accounts WHERE business_id = ? AND is_active = 1",
        (business_id,),
    ).fetchall()
    return [r["shorthand"] or r["name"] for r in rows]


def _account_name_map(conn, business_id: int) -> dict:
    rows = conn.execute(
        "SELECT name, shorthand FROM accounts WHERE business_id = ?", (business_id,)
    ).fetchall()
    m = {}
    for r in rows:
        m[r["name"].lower()] = r["name"].lower()
        if r["shorthand"]:
            m[r["shorthand"].lower()] = r["name"].lower()
    return m


def _json_array_nonempty(text) -> bool:
    """True if `text` is a JSON array with at least one element (the stored
    parser serializes issues/supplies/follow-ups/customer-requests as JSON
    arrays). Non-JSON / empty / non-list values count as 'empty'."""
    if not text:
        return False
    try:
        arr = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(arr, list) and len(arr) > 0


async def main():
    conn = _connect()
    logs = conn.execute(
        "SELECT sl.id, sl.business_id, sl.account_id, sl.raw_note, sl.parsed_status, "
        "sl.parsed_supplies, sl.parsed_followups, sl.parsed_customer_requests, "
        "a.name AS account_name "
        "FROM service_logs sl LEFT JOIN accounts a ON a.id = sl.account_id "
        "ORDER BY sl.id"
    ).fetchall()
    print(f"Replaying {len(logs)} historical service_logs through Jev...\n")

    n = 0
    account_agree = 0
    status_agree = 0
    account_skipped = 0  # no account name stored to compare against (uncategorized)
    total_latency = 0
    total_in_tokens = 0
    total_out_tokens = 0
    errors = 0
    results = []

    for log in logs:
        n += 1
        biz_id = log["business_id"]
        known_accounts = _known_accounts_for(conn, biz_id)
        name_map = _account_name_map(conn, biz_id)

        try:
            jev_result = await jev_client.parse_note_jev(log["raw_note"], known_accounts)
        except Exception as e:
            errors += 1
            print(f"  [{log['id']}] ERROR calling Jev: {e}")
            continue

        stored_account = (log["account_name"].lower() if log["account_name"] else None)
        jev_account_key = (jev_result.get("account_hint") or "").lower()
        jev_account_norm = name_map.get(jev_account_key, jev_account_key)

        acct_match = None
        if stored_account is not None:
            acct_match = (jev_account_norm == stored_account) or (jev_account_norm in stored_account) or (stored_account in jev_account_norm)
            if acct_match:
                account_agree += 1
        else:
            account_skipped += 1

        stored_status = log["parsed_status"]
        stat_match = (jev_result.get("status") == stored_status)
        if stat_match:
            status_agree += 1

        # Noul probabilities (Jev) + stored-parse ground truth for calibration.
        raw_answers = jev_result.get("jev_raw_answers") or {}
        supplies_prob = (raw_answers.get("supplies_present") or {}).get("noul")
        followups_prob = (raw_answers.get("followups_present") or {}).get("noul")
        customer_requests_prob = (raw_answers.get("customer_requests_present") or {}).get("noul")
        status_conf = (jev_result.get("jev_confidence") or {}).get("status")

        total_latency += jev_result.get("processing_time_ms", 0)
        total_in_tokens += jev_result.get("jev_input_tokens", 0) or 0
        total_out_tokens += jev_result.get("jev_output_tokens", 0) or 0

        results.append({
            "id": log["id"],
            "note": log["raw_note"][:60],
            "stored_account": stored_account,
            "jev_account": jev_account_norm,
            "account_agree": acct_match,
            "stored_status": stored_status,
            "jev_status": jev_result.get("status"),
            "status_agree": stat_match,
            "latency_ms": jev_result.get("processing_time_ms"),
            "model_version": jev_result.get("model_version"),
            "model_requested": jev_result.get("model_requested"),
            "supplies_present_prob": supplies_prob,
            "followups_present_prob": followups_prob,
            "customer_requests_present_prob": customer_requests_prob,
            "stored_supplies_nonempty": _json_array_nonempty(log["parsed_supplies"]),
            "stored_followups_nonempty": _json_array_nonempty(log["parsed_followups"]),
            "stored_customer_requests_nonempty": _json_array_nonempty(log["parsed_customer_requests"]),
            "status_confidence": status_conf,
        })
        tick = "✅" if (acct_match in (True, None)) and stat_match else "⚠️"
        print(f"  {tick} [{log['id']}] acct: {stored_account!r} vs jev {jev_account_norm!r} "
              f"({'agree' if acct_match else 'DISAGREE' if acct_match is False else 'n/a'}) | "
              f"status: {stored_status!r} vs jev {jev_result.get('status')!r} "
              f"({'agree' if stat_match else 'DISAGREE'}) | "
              f"model {jev_result.get('model_version') or jev_result.get('model_requested')}")

    compared_accounts = n - account_skipped - errors
    attempted = n - errors

    model_versions_seen = {}
    for r in results:
        v = r.get("model_version")
        if v is None:
            req = r.get("model_requested")
            v = f"requested:{req}" if req else "unknown"
        model_versions_seen[v] = model_versions_seen.get(v, 0) + 1

    cal = jev_calibration.compute_calibration(results)

    print("\n" + "=" * 70)
    print("AGREEMENT METRICS")
    print("=" * 70)
    print(f"Notes replayed:            {n}")
    print(f"Jev call errors:           {errors}")
    if attempted:
        print(f"Status agreement:          {status_agree}/{attempted} ({100*status_agree/attempted:.0f}%)")
    if compared_accounts:
        print(f"Account agreement:         {account_agree}/{compared_accounts} ({100*account_agree/compared_accounts:.0f}%) "
              f"(skipped {account_skipped} uncategorized stored logs)")
    if attempted:
        print(f"Avg latency:               {total_latency/attempted:.0f}ms")
        print(f"Total tokens (in/out):     {total_in_tokens}/{total_out_tokens}")
        cost = total_in_tokens * 0.042 / 1_000_000
        print(f"Est. input-token cost:     ${cost:.6f} for {attempted} notes (${cost/attempted:.8f}/note)")

    print("\n" + "=" * 70)
    print("CALIBRATION (vs OUR stored parse)")
    print("=" * 70)
    print(f"Model versions seen:       {model_versions_seen}")
    print(f"Brier (Noul, 3 qs):        {cal['brier_noul']}  (n={cal['noul_sample_size']})")
    print(f"Brier (status Choice):      {cal['brier_status']}  (n={cal['status_sample_size']})")
    if cal["note"]:
        print(f"NOTE: {cal['note']}")
    print("Reliability table (Noul):")
    for row in cal["reliability_table"]:
        print(f"  bin {row['bin']} [{row['range'][0]:.2f},{row['range'][1]:.2f}) "
              f"n={row['count']:>3}  mean_pred={row['mean_predicted']}  "
              f"observed={row['observed_frequency']}")

    out_path = REPO_ROOT / "scripts" / "evals" / "results" / "jev_shadow_replay.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "n": n, "errors": errors, "status_agree": status_agree,
        "account_agree": account_agree, "account_skipped": account_skipped,
        "avg_latency_ms": total_latency / attempted if attempted else None,
        "total_in_tokens": total_in_tokens, "total_out_tokens": total_out_tokens,
        "model_versions_seen": model_versions_seen,
        "brier_noul": cal["brier_noul"],
        "brier_status": cal["brier_status"],
        "reliability_table": cal["reliability_table"],
        "noul_sample_size": cal["noul_sample_size"],
        "status_sample_size": cal["status_sample_size"],
        "note": cal["note"],
        "results": results,
    }, indent=2))
    print(f"\nFull results written to {out_path}")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
