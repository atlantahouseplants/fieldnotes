"""P6b — owner chat commands: shared account-creation + intent parsing.

One pipeline, no special cases: the dashboard Add Customer form and the
owner's "New account: …" text both go through create_account(). Owner notes
(dashboard Quick Note / chat "Note for X: …") are attributed to a synthetic
"Owner" worker row via get_or_create_owner_worker().

Everything here is deterministic — no LLM. parse_note() does the AI work
on the note BODY downstream.
"""
import json
import re
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from ..models import Account, Worker
from .schedule import parse_schedule, sync_route_entries


# ── intent parsing (deterministic) ────────────────────────────────
_NEW_ACCOUNT_RE = re.compile(
    r"^\s*(?:new|add)\s+(?:account|customer|client)\s*:\s*(.+)$", re.I | re.S)
_NOTE_FOR_RE = re.compile(
    r"^\s*(?:note|log)\s+(?:for|on)\s+(.+?)\s*:\s*(.+)$", re.I | re.S)
_INVITE_RE = re.compile(
    r"^\s*(?:invite(?:\s+(?:a\s+|new\s+)?(?:worker|rep|tech|employee|guy))?"
    r"|add\s+(?:a\s+|new\s+)?(?:worker|rep|tech|employee|guy))\s*\.?\s*$", re.I)

_DAY_HINTS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun",
              "wk", "week", "daily", "monthly", "/")


def parse_new_account(text: str):
    """"New account: Smith, 121 Main St, gate 4412, Tue/Fri" → dict | None.
    Positional after the name: address, then gate/schedule in either order."""
    m = _NEW_ACCOUNT_RE.match(text)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
    if not parts:
        return None
    gate, schedule = None, None
    for p in parts[2:]:
        low = p.lower()
        if schedule is None and any(d in low for d in _DAY_HINTS):
            schedule = p
        elif gate is None:
            gate = re.sub(r"^(?:gate|access|code)\s*[:#]?\s*", "", p, flags=re.I) or None
        elif schedule is None:
            schedule = p
    return {"name": parts[0],
            "address": parts[1] if len(parts) > 1 else None,
            "gate_code": gate, "schedule": schedule}


def parse_note_for(text: str):
    """"Note for Smith Office: gate code changed" → (account_query, body) | None."""
    m = _NOTE_FOR_RE.match(text)
    if not m:
        return None
    account_q, body = m.group(1).strip(), m.group(2).strip()
    if not account_q or not body:
        return None
    return account_q, body


def invite_intent(text: str) -> bool:
    return bool(_INVITE_RE.match(text))


# ── shared create path (dashboard + chat) ─────────────────────────
def create_account(db: Session, business_id: int, name: str,
                   address: Optional[str] = None, gate_code: Optional[str] = None,
                   access_notes: Optional[str] = None, schedule: Optional[str] = None):
    """Create + schedule-sync an account. → (Account, None) | (None, plain-words error)."""
    name_clean = (name or "").strip()
    if not name_clean:
        return None, "Type the customer's name first"

    existing = db.query(Account).filter(
        Account.business_id == business_id,
        func.lower(Account.name) == name_clean.lower()
    ).first()
    if existing:
        return None, (f"You already have a customer called \"{existing.name}\""
                      " — no need to add it twice.")

    account = Account(
        business_id=business_id, name=name_clean,
        address=(address or "").strip() or None,
        gate_code=(gate_code or "").strip() or None,
        access_notes=(access_notes or "").strip() or None,
        schedule=(schedule or "").strip() or None,
        is_active=True,
    )
    db.add(account)
    db.flush()  # id materializes; commit at the end of the whole flow

    if account.schedule:
        parsed = parse_schedule(account.schedule)
        if parsed["entries"] or parsed["monthly_day"]:
            account.schedule_parsed = json.dumps(parsed)
        sync_route_entries(db, business_id)

    db.commit()
    db.refresh(account)
    return account, None


def get_or_create_owner_worker(db: Session, business_id: int) -> Worker:
    """Attribution row for owner-added notes (ServiceLog.worker_id is NOT NULL).
    telegram_id stays NULL — owner isn't a field worker; morning-push and
    summary worker loops skip NULL/placeholder ids (isdigit check). The chat
    path re-resolves owners via Business.owner_telegram_id every message."""
    owner = db.query(Worker).filter(
        Worker.business_id == business_id, Worker.name == "Owner"
    ).first()
    if not owner:
        owner = Worker(business_id=business_id, name="Owner",
                       telegram_id=None, is_active=True)
        db.add(owner)
        db.flush()  # id available now; caller owns the commit
        db.refresh(owner)
    return owner
