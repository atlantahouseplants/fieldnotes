"""Public self-serve demo endpoint (M1 — plans/marketing/README.md).

Lets the try.html phone-mockup drive the REAL pipeline against the demo
tenant (business_id=2, "Precision HVAC") only. HARD RULES (spec):

1. business_id is HARDCODED to 2 server-side — no parameter, no path, no
   way to touch another tenant.
2. Rate-limited per IP (public unauthenticated LLM-calling endpoint —
   unit economics). In-memory sliding windows: ~10/hr + ~30/day per IP,
   tunable via FIELDNOTES_DEMO_RATE_HOUR / FIELDNOTES_DEMO_RATE_DAY.
   LLM chain stays Grok-primary (same as production); cost is bounded by
   the rate limit (30 calls/day/IP ≈ $0.15 worst case). If abuse shows
   up, flipping to DeepSeek-primary is a one-line env change on Railway.
3. NEVER sends real email/Telegram. The recap step runs the P8 draft
   logic (rewrite_client_safe + passes_safety_filter) and returns the
   drafted client_text for DISPLAY ONLY — the RecapLog row is marked
   "skipped" so nothing ever enters the owner approval queue, the
   nightly pending list, or a send path from a demo visit.
4. Plain-words errors — never raw 500s.
"""
import os
import re
import time
from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..models import SessionLocal, Account, Business, Worker, ServiceLog, RecapLog
from ..services.parser import parse_note
from ..services.ingest import persist_parsed_note
from ..services.qa import looks_like_question, answer_question
from ..services import tasks as tasks_mod
from ..services import recaps as recaps_mod

router = APIRouter()

DEMO_BUSINESS_ID = 2  # Precision HVAC — hardcoded, never from the request
MAX_TEXT_LEN = 500

RATE_HOUR = int(os.getenv("FIELDNOTES_DEMO_RATE_HOUR", "10"))
RATE_DAY = int(os.getenv("FIELDNOTES_DEMO_RATE_DAY", "30"))

# ── Per-IP sliding-window rate limiter (in-memory; single Railway
# instance, resets on deploy — acceptable for a demo guard) ──
_hits: dict = defaultdict(list)


def _client_ip(request: Request) -> str:
    # Prod sits behind Cloudflare → Railway edge. The Railway edge rewrites
    # X-Forwarded-For to the *Cloudflare egress* IP, which varies request to
    # request — taking XFF[0] silently defeats a per-IP limiter (verified in
    # prod: 12 rapid requests, 12 distinct keys). CF-Connecting-IP is set by
    # Cloudflare to the true client IP and passes through untouched, so it
    # wins when present. Direct-to-origin callers could spoof it, but the
    # demo page is only served via the CF-fronted domain — best-effort is
    # fine for a demo guard.
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd
    return request.client.host if request.client else "unknown"


def _rate_ok(ip: str) -> bool:
    now = time.time()
    day_ago = now - 86400
    hits = [t for t in _hits[ip] if t > day_ago]
    _hits[ip] = hits
    if len(hits) >= RATE_DAY:
        return False
    if sum(1 for t in hits if t > now - 3600) >= RATE_HOUR:
        return False
    hits.append(now)
    return True


def _plain(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"ok": False, "error": message, "message": message})


class DemoIn(BaseModel):
    text: str


# "show the recap" / "prove it" — renders the client recap preview again.
_RECAP_INTENT_RE = re.compile(
    r"^\s*(?:(?:show|see|prove|preview|send)\b[\s\S]*\b(?:recap|reciept|receipt|email|proof|client)\b|prove\s+it)\s*[?.!]*\s*$",
    re.I)


def _recap_payload(account_name: str, client_text: str) -> dict:
    """Display-only recap preview. Deliberately NO recipient address — a
    public endpoint must not leak the demo's Geoff-controlled email."""
    return {
        "account": account_name,
        "to": "your customer's inbox",
        "subject": f"Service recap — {account_name}",
        "client_text": client_text,
    }


async def _latest_recap_preview(db) -> dict | None:
    """Newest demo recap that already has drafted text, or draft one fresh
    from the latest log at a recap-enabled demo account."""
    row = db.query(RecapLog).filter(
        RecapLog.business_id == DEMO_BUSINESS_ID,
        RecapLog.client_text.isnot(None),
    ).order_by(RecapLog.created_at.desc()).first()
    if row:
        acct = db.query(Account).filter(Account.id == row.account_id).first()
        return _recap_payload(acct.name if acct else "your customer", row.client_text)

    # Nothing drafted yet — draft from the most recent log at a
    # recap-enabled account (display only; no row created, nothing sent).
    biz = db.query(Business).filter(Business.id == DEMO_BUSINESS_ID).first()
    enabled = recaps_mod.enabled_accounts(db, DEMO_BUSINESS_ID)
    if not (biz and enabled):
        return None
    acct_ids = [int(a.id) for a in enabled]
    log = db.query(ServiceLog).filter(
        ServiceLog.business_id == DEMO_BUSINESS_ID,
        ServiceLog.account_id.in_(acct_ids),
    ).order_by(ServiceLog.timestamp.desc()).first()
    if not log:
        return None
    acct = next((a for a in enabled if int(a.id) == log.account_id), enabled[0])
    text = await recaps_mod.rewrite_client_safe(
        biz.name or "your service team", acct.name, log.raw_note or "")
    if not text:
        return None
    ok, _ = recaps_mod.passes_safety_filter(text, log.raw_note or "")
    if not ok:
        return None
    return _recap_payload(acct.name, text)


@router.post("/api/demo")
async def demo(body: DemoIn, request: Request):
    text = (body.text or "").strip()
    if not text:
        return _plain(400, "Type a note or a question first.")
    if len(text) > MAX_TEXT_LEN:
        return _plain(400, "Keep it under 500 characters — field notes are short.")

    ip = _client_ip(request)
    if not _rate_ok(ip):
        return _plain(429, "The demo's busy right now — try again in a bit.")

    db = SessionLocal()
    try:
        biz = db.query(Business).filter(Business.id == DEMO_BUSINESS_ID).first()
        worker = db.query(Worker).filter(
            Worker.business_id == DEMO_BUSINESS_ID,
            Worker.is_active == True,
        ).first()
        if not (biz and worker):
            return _plain(503, "The demo isn't set up yet — check back soon.")

        # ── "show the recap" — display-only preview of the P8 draft ──
        if _RECAP_INTENT_RE.match(text):
            preview = await _latest_recap_preview(db)
            if not preview:
                return {"ok": True, "kind": "recap", "recap_preview": None,
                        "answer": ("No visit logged yet — log one first (try "
                                   "\"Riverside: replaced the belt on unit 1\") "
                                   "and I'll draft the client recap.")}
            return {"ok": True, "kind": "recap", "recap_preview": preview}

        # ── Question → real tenant-scoped Q&A (P1) ──
        if looks_like_question(text):
            qa = await answer_question(db, DEMO_BUSINESS_ID, worker, text)
            return {"ok": True, "kind": "question",
                    "answer": qa["answer"], "sources": qa["sources"],
                    "clarification": qa.get("clarification", False)}

        # ── Note → the REAL ingest path reps use (parse → log → action
        # queue → task match → recap planning) ──
        accounts = db.query(Account).filter(
            Account.business_id == DEMO_BUSINESS_ID,
            Account.is_active == True,
        ).all()
        account_hints = [a.shorthand or a.name for a in accounts]
        account_map = {}
        for a in accounts:
            account_map[a.name.lower()] = a.id
            if a.shorthand:
                account_map[a.shorthand.lower()] = a.id

        parsed = await parse_note(text, account_hints)
        account_hint = (parsed.get("account_hint") or "").lower()
        account_id = account_map.get(account_hint)
        account_name = account_hint or "uncategorized"

        persisted = persist_parsed_note(
            db, business_id=DEMO_BUSINESS_ID, worker_id=int(worker.id),
            text=text, parsed=parsed, account_id=account_id)

        # P7 payoff: a note that matches exactly one open task closes it.
        # Demo twist (spec M2 tap 1): the web visitor can't reply YES, so
        # the demo plays both sides — closes it, reports it openly, and
        # re-opens a fresh copy so the NEXT visitor sees the same magic.
        task_closed = None
        if account_id:
            open_tasks = tasks_mod.open_tasks_for_account(
                db, DEMO_BUSINESS_ID, account_id)
            cands = tasks_mod.match_open_tasks(open_tasks, text) if open_tasks else []
            if len(cands) == 1:
                t = cands[0]
                tasks_mod.close_task(db, t, closed_by_worker_id=int(worker.id))
                task_closed = {"title": t.title}
                tasks_mod.create_task(
                    db, DEMO_BUSINESS_ID, account_id, title=t.title,
                    details=t.details, supplies=t.supplies_needed,
                    due=t.due_date, source="demo-reseed")

        # P8 payoff: draft the client recap for DISPLAY ONLY. The row is
        # marked "skipped" — demo previews never wait on owner approval,
        # never appear in nightly pending lists, never send. No Telegram
        # ping, no email — rewrite + safety filter only.
        recap_preview = None
        recap = persisted.get("recap")
        if recap is not None:
            acct = db.query(Account).filter(Account.id == recap.account_id).first()
            acct_name = acct.name if acct else "your customer"
            drafted = await recaps_mod.rewrite_client_safe(
                biz.name or "your service team", acct_name,
                recap.source_text or "")
            ok = bool(drafted) and recaps_mod.passes_safety_filter(
                drafted, recap.source_text or "")[0]
            if ok:
                recap.client_text = drafted
                recap_preview = _recap_payload(acct_name, drafted)
            recap.status = "skipped"   # display-only — NEVER a send path
            db.commit()

        return {
            "ok": True,
            "kind": "note",
            "log": {
                "account": account_name,
                "matched": bool(account_id),
                "status": parsed.get("status", "logged"),
                "summary": parsed.get("summary", text[:100]),
                "actions_created": persisted["actions_created"],
            },
            "open_tasks": len(tasks_mod.open_tasks_for_account(
                db, DEMO_BUSINESS_ID, account_id)) if account_id else 0,
            "task_closed": task_closed,
            "recap_preview": recap_preview,
        }
    except Exception as e:
        print(f"/api/demo error: {e}")
        return _plain(500, "The demo hiccuped on our end — give it another try.")
    finally:
        db.close()
