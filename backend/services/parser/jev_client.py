"""
FieldNotes — Jev (TypeSafe System One) parser adapter.

Implements the SAME shape parse_note() returns, calling the TypeSafe
System One API (https://api.typesafe.ai/v1/systemone) instead of a chat
LLM. Jev is the *decision layer*: Choice for account/status, Noul for
presence of supplies/follow-ups/customer-requests, and Choice-per-clause
for the "select instead of generate" text-extraction pattern (code
splits candidate clauses out of the raw note; Jev picks which clauses
belong to which category; code assembles the final arrays verbatim from
the worker's own words — Jev never generates prose).

This module is a plain HTTP client (no SDK dependency — typesafe-sdk
isn't installable in this box's constrained pip environment; the raw
HTTP contract is simple and documented at docs.typesafe.ai/api).

Cost telemetry: every call's usage.input_tokens/output_tokens is
returned on the result dict so callers (parser.py dispatcher) can log
it to parse_shadow_logs for the daily cost rollup.
"""
import json
import logging
import os
import re
import time
from typing import Optional

import httpx
from sqlalchemy.orm import Session

_logger = logging.getLogger(__name__)

TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")
TYPESAFE_BASE = os.getenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.getenv("JEV_MODEL", "jev-latest")
JEV_TIMEOUT_S = float(os.getenv("JEV_TIMEOUT_S", "10"))

if JEV_MODEL == "jev-latest":
    _logger.warning(
        "JEV_MODEL is unpinned (default 'jev-latest') — parses may silently change "
        "as TypeSafe rolls the alias forward. Ops should pin an explicit version via "
        "JEV_MODEL for reproducibility; the resolved model version is still logged "
        "per call (see parse_shadow_logs.jev_model_version)."
    )

MAX_CLAUSES = 6  # cap Choice questions per call — cost + latency guardrail

_CLAUSE_SPLIT_RE = re.compile(r"[.;\n]+|(?:,\s+(?=(?:and|but)\b))")

# Statuses Jev's "status" Choice can pick — mirrors the current parser's
# `status` field values (parser.py PARSE_PROMPT), minus the rarely-used
# "urgent"/"needs_supplies" which fold into issues_found/status text.
STATUS_OPTIONS = {
    "all_good": "Worker reports everything is fine, nothing needed",
    "issues_found": "Worker reports a problem, damage, or something wrong",
    "needs_supplies": "Worker needs parts/supplies for a future visit",
    "follow_up_needed": "Worker needs to check back or do something next time",
    "urgent": "Emergency — broken/flooding/fire/safety issue needing immediate attention",
}

CLAUSE_ROLE_OPTIONS = {
    "issue": "Describes a problem, damage, or something that went wrong",
    "supply": "Names a part or supply needed for a future visit",
    "followup": "Describes something to check or do on the next visit",
    "customer_request": "Something the client/customer explicitly asked for",
    "none": "Doesn't fit any of the above — small talk, filler, or already covered",
}


def split_candidate_clauses(note: str) -> list[str]:
    """Split a worker note into candidate clauses for Jev to classify.

    Deterministic, no LLM — code owns this. Splits on sentence-ish
    boundaries (. ; newline, or ", and"/", but") so each clause is a
    single self-contained claim Jev can tag with one Choice question.
    Caps at MAX_CLAUSES (cost/latency guardrail — a single question per
    clause, so this also bounds the number of Choice questions we ask).
    """
    note = (note or "").strip()
    if not note:
        return []
    parts = [p.strip() for p in _CLAUSE_SPLIT_RE.split(note) if p.strip()]
    if not parts:
        parts = [note]
    return parts[:MAX_CLAUSES]


def _build_questions(known_accounts: Optional[list[str]], clauses: list[str]) -> dict:
    account_criteria = {}
    if known_accounts:
        for acct in known_accounts:
            key = re.sub(r"[^a-z0-9_]", "_", acct.lower()).strip("_") or "account"
            account_criteria[key] = acct
    account_criteria["nomatch"] = "No known account is mentioned in the note"

    questions: dict = {
        "status": {
            "type": "choice",
            "instructions": "What is the overall status of this service stop, based on the worker's note?",
            "criteria": STATUS_OPTIONS,
        },
        "supplies_present": {
            "type": "noul",
            "instructions": "Does the note mention any supplies or parts needed for a future visit?",
        },
        "followups_present": {
            "type": "noul",
            "instructions": "Does the note mention anything to check or do on a future visit?",
        },
        "customer_requests_present": {
            "type": "noul",
            "instructions": "Does the note mention something the customer/client explicitly asked for?",
        },
    }
    if account_criteria:
        questions["account"] = {
            "type": "choice",
            "instructions": "Which known account/location is this note about?",
            "criteria": account_criteria,
        }
    for i, clause in enumerate(clauses):
        questions[f"clause_{i}"] = {
            "type": "choice",
            "instructions": {
                "question": "What role does this clause from the worker's note play?",
                "clause": clause,
            },
            "criteria": CLAUSE_ROLE_OPTIONS,
        }
    return questions, account_criteria


def _account_key_to_name(key: str, account_criteria: dict, known_accounts: Optional[list[str]]) -> str:
    if key == "nomatch" or not known_accounts:
        return ""
    return account_criteria.get(key, key)


async def parse_note_jev(worker_note: str, known_accounts: Optional[list[str]] = None) -> dict:
    """Parse a worker note via the Jev (TypeSafe System One) API.

    Returns the same dict shape as backend.services.parser.parse_note,
    plus jev_input_tokens/jev_output_tokens/jev_confidence for shadow-mode
    telemetry. Raises on HTTP/transport error — callers (the shadow-mode
    dispatcher) are responsible for catching and failing closed.
    """
    if not TYPESAFE_API_KEY:
        raise ValueError("TYPESAFE_API_KEY not set")

    t0 = time.time()
    clauses = split_candidate_clauses(worker_note)
    questions, account_criteria = _build_questions(known_accounts, clauses)

    async with httpx.AsyncClient(timeout=JEV_TIMEOUT_S) as client:
        resp = await client.post(
            TYPESAFE_BASE,
            headers={
                "Authorization": f"Bearer {TYPESAFE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"state": worker_note, "model": JEV_MODEL, "questions": questions},
        )
        resp.raise_for_status()
        data = resp.json()

    answers = data.get("answers", {})
    elapsed_ms = int((time.time() - t0) * 1000)

    # The TypeSafe response exposes the RESOLVED model version at the
    # top level (`model`), e.g. "jev-1.13.0" even when `jev-latest` was
    # requested — pinning + per-row logging depend on this field.
    model_version = data.get("model")

    account_answer = answers.get("account")
    account_hint = ""
    if account_answer:
        account_hint = _account_key_to_name(account_answer["choice"], account_criteria, known_accounts)

    status_answer = answers.get("status", {})
    status = status_answer.get("choice", "issues_found")

    issues, supplies, follow_ups, customer_requests = [], [], [], []
    for i, clause in enumerate(clauses):
        clause_answer = answers.get(f"clause_{i}")
        if not clause_answer:
            continue
        role = clause_answer.get("choice")
        if role == "issue":
            issues.append(clause)
        elif role == "supply":
            supplies.append(clause)
        elif role == "followup":
            follow_ups.append(clause)
        elif role == "customer_request":
            customer_requests.append(clause)

    usage = data.get("usage", {})
    jev_confidence = {}
    if account_answer:
        jev_confidence["account"] = account_answer.get("confidence")
    if status_answer:
        jev_confidence["status"] = status_answer.get("confidence")

    return {
        "account_hint": account_hint,
        "status": status,
        "issues": issues,
        "supplies": supplies,
        "follow_ups": follow_ups,
        "followups": follow_ups,  # alias — ingest.py reads "followups"
        "customer_requests": customer_requests,
        "summary": worker_note[:200].strip(),
        "processing_time_ms": elapsed_ms,
        "model_version": model_version,
        "model_requested": JEV_MODEL if model_version is None else None,
        "jev_input_tokens": usage.get("input_tokens"),
        "jev_output_tokens": usage.get("output_tokens"),
        "jev_confidence": jev_confidence,
        "jev_raw_answers": answers,
    }


def get_todays_jev_tokens(db: Session) -> int:
    """Sum input+output Jev tokens logged today (UTC) — the daily COGS
    guardrail's usage counter. Reads parse_shadow_logs (populated by
    both shadow and jev PARSER_BACKEND modes)."""
    from datetime import datetime, timezone
    from ...models import ParseShadowLog  # lazy import, avoids any import-order surprises

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    rows = db.query(ParseShadowLog).filter(
        ParseShadowLog.created_at >= today_start
    ).all()
    total = 0
    for row in rows:
        total += (row.jev_input_tokens or 0) + (row.jev_output_tokens or 0)
    return total
