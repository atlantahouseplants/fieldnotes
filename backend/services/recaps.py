"""P8 — Client Recaps (proof-of-service).

Rep logs a visit → internal pipeline (existing) → CLIENT-SAFE rewrite →
owner one-tap approves → branded email lands in the client's inbox.

HARD RULES (spec — the feature dies if these break):
1. NEVER send the raw worker note. If the LLM rewrite fails for any reason
   the recap is HELD for the owner — there is no raw-text fallback.
2. Approve-first: every recap waits for the owner's ✓. recap_auto_send is
   a Phase-2 column only — nothing reads it at launch.
3. Email only (SMS recaps wait for P3 + opt-in + budget caps).
4. Per-account opt-in: recap_enabled AND recap_email must both be set.

Structure (sync/async split — ingest is sync, the webhook is async):
  plan_for_log()       sync, called from ingest.persist_parsed_note — gate
                       check + batching + creates/merges a `drafting` row.
  draft_and_notify()   async, called by the route handler right after —
                       LLM rewrite → pending_approval + owner ping (or held).
  handle_owner_reply() async, owner ✓ / ✗ / "edit: …" → send / skip.

Tenant isolation: every query filters business_id. The owner ping goes only
to Business.owner_telegram_id — never to reps, never across tenants.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from ..models import Account, Business, RecapLog, ServiceLog
from ..deps import has_feature

XAI_API_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

VISIT_WINDOW = timedelta(hours=2)   # notes within this merge into ONE recap
PENDING_NUDGE_AFTER = timedelta(hours=4)  # spec: timeout → stays pending, listed in summary

# Test hooks (never set in prod): stub the LLM rewrite / the email send so the
# suite can drive the full loop deterministically with zero network.
_STUB_REWRITE = os.getenv("FIELDNOTES_RECAP_STUB", "").strip()
_STUB_EMAIL = os.getenv("FIELDNOTES_EMAIL_STUB", "") == "1"


# ── Deterministic safety filter (the backstop) ───────────────────
# The LLM prompt is the primary defense; this filter is the guarantee. If a
# rewrite trips ANY of these it is held, not sent — a bad LLM pass cannot
# leak jargon, money talk, or the raw note to a client.
_BANNED_WORDS = (
    # hedged/negative phrasing named in the spec
    "milking", "nursing", "barely", "limping", "neglected",
    # internal jargon
    "action queue", "service log", "raw note", "tenant", "pipeline",
    # money talk
    "invoice", "billing", "charge", "cost us", "markup",
)
_MONEY_RE = re.compile(r"(\$\s?\d|\b\d+\s?(?:dollars|bucks)\b)", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def passes_safety_filter(client_text: str, source_text: str) -> tuple:
    """(ok, reason). Reason is a short machine tag for the held note."""
    if not client_text or not client_text.strip():
        return False, "empty"
    low = _norm(client_text)
    for w in _BANNED_WORDS:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            return False, f"banned:{w}"
    if _MONEY_RE.search(client_text):
        return False, "money"
    # verbatim raw-note leak: any 6+ word contiguous run from the source
    src_words = _norm(source_text).split()
    if len(src_words) >= 6:
        src_runs = {" ".join(src_words[i:i + 6]) for i in range(len(src_words) - 5)}
        if any(run in low for run in src_runs):
            return False, "verbatim"
    return True, "ok"


# ── Client-safe rewrite (LLM chain, mirrors parser.py) ───────────
_REWRITE_PROMPT = """You are rewriting a field service note into a short CLIENT-FACING service recap email for "{account}" on behalf of {business}.

Rules (all mandatory):
- Professional third person, warm but factual. Start like "Your service was completed today…" or "During today's visit…".
- KEEP: the date, work performed, areas/items serviced, honest condition notes phrased constructively, next-visit items the client should know (e.g. "two plants recommended for replacement next visit").
- STRIP: internal jargon, costs or prices, supply lists, crew names, any negativity or hedging ("milking", "nursing", "barely"), anything about internal processes.
- If you are unsure whether a phrase is appropriate, DROP it. When in doubt, leave it out.
- 2-4 short sentences. Plain text only — no greeting, no sign-off, no subject line, no bullet points, no emojis.

Service note(s) from today's visit:
{notes}

Client-safe recap:"""


async def rewrite_client_safe(business_name: str, account_name: str,
                              source_text: str) -> Optional[str]:
    """→ client-safe text, or None when every provider failed (caller holds)."""
    if _STUB_REWRITE:
        return _STUB_REWRITE
    prompt = (_REWRITE_PROMPT
              .replace("{account}", account_name)
              .replace("{business}", business_name)
              .replace("{notes}", source_text))
    for caller in (_call_xai, _call_deepseek, _call_openai):
        try:
            text = await caller(prompt)
            if text and text.strip():
                return text.strip()
        except Exception:
            continue
    return None


async def _call_xai(prompt: str) -> str:
    if not XAI_API_KEY:
        raise ValueError("XAI_API_KEY not set")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{XAI_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {XAI_API_KEY}"},
            json={"model": "grok-4.5", "temperature": 0.3, "max_tokens": 220,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _call_deepseek(prompt: str) -> str:
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY not set")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={"model": "deepseek-chat", "temperature": 0.3, "max_tokens": 220,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _call_openai(prompt: str) -> str:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{OPENAI_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": "gpt-4o-mini", "temperature": 0.3, "max_tokens": 220,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ── Setup intents (owner chat; deterministic) ────────────────────
_RECAPS_ON_RE = re.compile(
    r"^\s*recaps?\s+on\s+for\s+(.+?)\s*:\s*(\S+@\S+)\s*\.?\s*$", re.I | re.S)
_RECAPS_OFF_RE = re.compile(
    r"^\s*recaps?\s+off\s+for\s+(.+?)\s*\.?\s*$", re.I)
_RECAPS_LIST_RE = re.compile(
    r"^\s*(?:which\s+(?:clients?|customers?|accounts?)\s+get\s+recaps?|"
    r"recaps?\s+(?:list|status))\s*\??\s*$", re.I)
_EDIT_RE = re.compile(r"^\s*edit\s*:\s*(.+)$", re.I | re.S)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_recaps_on(text: str):
    """'Recaps on for Smith Office: jane@smith.com' → (account_query, email) | None."""
    m = _RECAPS_ON_RE.match(text)
    if not m:
        return None
    account_q, email = m.group(1).strip(), m.group(2).strip().rstrip(".")
    if not account_q or not _EMAIL_RE.match(email):
        return None
    return account_q, email


def parse_recaps_off(text: str):
    """'Recaps off for Smith Office' → account_query | None."""
    m = _RECAPS_OFF_RE.match(text)
    return m.group(1).strip() if m else None


_RECAPS_ON_PREFIX_RE = re.compile(r"^\s*recaps?\s+on\s+for\s+", re.I)

def malformed_recaps_on(text: str) -> bool:
    """Starts like 'recaps on for …' but didn't parse — almost always a bad or
    missing email. Without this catch the owner's setup command falls through
    and gets logged as a service NOTE, polluting the account history (and a
    client recap's source text)."""
    return bool(_RECAPS_ON_PREFIX_RE.match(text)) and parse_recaps_on(text) is None


def recaps_list_intent(text: str) -> bool:
    return bool(_RECAPS_LIST_RE.match(text))


def parse_edit(text: str) -> Optional[str]:
    """'edit: <replacement recap text>' → text | None."""
    m = _EDIT_RE.match(text)
    if not m:
        return None
    body = m.group(1).strip()
    return body or None


def resolve_account(db: Session, business_id: int, query: str):
    """Exact (name/shorthand, case-insensitive) FIRST, then fuzzy substring.
    → (Account, None) | (None, 'not_found') | (None, 'ambiguous')."""
    q = (query or "").strip().lower()
    if not q:
        return None, "not_found"
    accounts = db.query(Account).filter(
        Account.business_id == business_id, Account.is_active == True).all()
    exact = [a for a in accounts
             if (a.name or "").lower() == q or (a.shorthand or "").lower() == q]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, "ambiguous"
    fuzzy = [a for a in accounts
             if q in (a.name or "").lower() or (a.shorthand or "").lower().startswith(q)]
    if len(fuzzy) == 1:
        return fuzzy[0], None
    if len(fuzzy) > 1:
        return None, "ambiguous"
    return None, "not_found"


def enable_recaps(db: Session, business_id: int, account_query: str, email: str):
    """→ (Account, None) | (None, plain-words error)."""
    account, err = resolve_account(db, business_id, account_query)
    if err == "ambiguous":
        return None, (f"⚠️ More than one customer matches \"{account_query}\""
                      " — give me the full name.")
    if err:
        return None, f"⚠️ Couldn't find a customer called \"{account_query}\" — nothing changed."
    account.recap_enabled = True
    account.recap_email = email.strip().lower()
    db.commit()
    return account, None


def disable_recaps(db: Session, business_id: int, account_query: str):
    account, err = resolve_account(db, business_id, account_query)
    if err == "ambiguous":
        return None, (f"⚠️ More than one customer matches \"{account_query}\""
                      " — give me the full name.")
    if err:
        return None, f"⚠️ Couldn't find a customer called \"{account_query}\" — nothing changed."
    account.recap_enabled = False
    db.commit()
    return account, None


def enabled_accounts(db: Session, business_id: int) -> list:
    return db.query(Account).filter(
        Account.business_id == business_id,
        Account.is_active == True,
        Account.recap_enabled == True,
    ).order_by(Account.name).all()


# ── Trigger: batching + draft planning (sync; called from ingest) ─
def plan_for_log(db: Session, biz: Business, log: ServiceLog,
                 account: Optional[Account]) -> Optional[RecapLog]:
    """Decide whether this log starts/merges a recap draft. → RecapLog | None.

    Creates a `drafting` row (or merges into an existing unapproved draft
    inside the visit window). The caller (route handler) then runs the async
    draft_and_notify(). Tier-gated tenants get a `held` telemetry row tagged
    [GATED:recap] — no draft, no ping (spec: log gated attempts like P5).
    """
    if not account or not account.recap_enabled or not (account.recap_email or "").strip():
        return None
    if not has_feature(biz, "recaps"):
        gated = RecapLog(
            business_id=int(biz.id), account_id=int(account.id),
            service_log_id=int(log.id), source_log_ids=json.dumps([int(log.id)]),
            source_text=log.raw_note, status="held", channel="email",
            client_text="[GATED:recap]",
        )
        db.add(gated)
        db.commit()
        return None

    window_start = datetime.utcnow() - VISIT_WINDOW
    pending = db.query(RecapLog).filter(
        RecapLog.business_id == int(biz.id),
        RecapLog.account_id == int(account.id),
        RecapLog.status.in_(("drafting", "pending_approval")),
        RecapLog.created_at >= window_start,
    ).order_by(RecapLog.created_at.desc()).first()

    if pending:
        # Same visit — fold this note into the existing draft (never a 2nd email).
        ids = json.loads(pending.source_log_ids or "[]")
        ids.append(int(log.id))
        pending.source_log_ids = json.dumps(ids)
        pending.source_text = (pending.source_text or "") + "\n" + (log.raw_note or "")
        pending.status = "drafting"   # re-draft from the combined notes
        db.commit()
        db.refresh(pending)
        return pending

    recap = RecapLog(
        business_id=int(biz.id), account_id=int(account.id),
        service_log_id=int(log.id), source_log_ids=json.dumps([int(log.id)]),
        source_text=log.raw_note, status="drafting", channel="email",
    )
    db.add(recap)
    db.commit()
    db.refresh(recap)
    return recap


# ── Draft + owner ping (async; called by route handlers) ─────────
async def draft_and_notify(db: Session, biz: Business, recap: RecapLog,
                           send_message) -> None:
    """Rewrite the combined notes client-safe → pending_approval + owner ping.

    Any failure (LLM chain exhausted, safety filter trip) → status=held and
    the owner is asked to write it manually. NEVER sends raw text anywhere.
    `send_message` is integrations.telegram.send_message (injected for tests).
    """
    account = db.query(Account).filter(Account.id == recap.account_id).first()
    acct_name = account.name if account else "?"

    client_text = await rewrite_client_safe(biz.name or "your service team",
                                            acct_name, recap.source_text or "")
    ok, reason = (False, "llm-failed") if not client_text \
        else passes_safety_filter(client_text, recap.source_text or "")

    if not ok:
        recap.status = "held"
        recap.client_text = None   # nothing questionable sits on the row
        db.commit()
        await _ping_owner(db, biz, send_message,
                          f"⚠️ Recap for <b>{acct_name}</b> needs your words — "
                          f"the auto-rewrite didn't pass the safety check ({reason}). "
                          f"Reply <code>edit: your recap text</code> and I'll send that, "
                          f"or skip it with ✗.")
        return

    recap.client_text = client_text
    recap.status = "pending_approval"
    db.commit()
    await _ping_owner(db, biz, send_message,
                      f"📬 Recap for <b>{acct_name}</b> ready:\n\n"
                      f"<i>{client_text}</i>\n\n"
                      f"Reply ✓ to send it to {account.recap_email}, ✗ to skip, "
                      f"or <code>edit: your version</code>.")


async def _ping_owner(db: Session, biz: Business, send_message, text: str) -> None:
    """Owner-only ping (spec pitfall — never to reps). Failures never break flow."""
    try:
        if biz and biz.owner_telegram_id:
            await send_message(str(biz.owner_telegram_id), text)
    except Exception:
        pass


# ── Approval flow (owner chat) ────────────────────────────────────
def latest_actionable(db: Session, business_id: int) -> Optional[RecapLog]:
    """Newest recap awaiting the owner's word (pending_approval or held)."""
    return db.query(RecapLog).filter(
        RecapLog.business_id == business_id,
        RecapLog.status.in_(("pending_approval", "held")),
    ).order_by(RecapLog.created_at.desc()).first()


def recap_email_html(business_name: str, account_name: str,
                     visit_date: str, client_text: str) -> str:
    """Branded template: business name prominent, FieldNotes footer, opt-out line."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:560px;margin:0 auto;padding:20px;background:#f5f7f5">
  <div style="background:#fff;border-radius:12px;padding:28px;box-shadow:0 2px 8px rgba(0,0,0,0.06)">
    <p style="color:#1b5e20;font-weight:bold;font-size:18px;margin:0 0 4px">{business_name}</p>
    <p style="color:#666;font-size:13px;margin:0 0 20px">Service recap — {account_name} · {visit_date}</p>
    <p style="color:#222;font-size:15px;line-height:1.55;margin:0">{client_text}</p>
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0 12px">
    <p style="color:#999;font-size:11px;margin:0">This is a service record from {business_name}.
    Don't want these? Just reply "stop" and we'll turn them off.
    Sent via FieldNotes.</p>
  </div>
</body></html>"""


async def send_recap(db: Session, biz: Business, recap: RecapLog,
                     account: Account, client_text: str) -> dict:
    """Fire the email + record the outcome. → {"ok": bool, ...}"""
    from ..integrations.email import send_email  # deferred: circular-safe
    visit_date = (recap.created_at or datetime.utcnow()).strftime("%b %-d, %Y") \
        if os.name != "nt" else (recap.created_at or datetime.utcnow()).strftime("%b %d, %Y")
    subject = f"Service recap — {account.name} · {visit_date}"
    html = recap_email_html(biz.name or "Your service team", account.name,
                            visit_date, client_text)
    if _STUB_EMAIL:
        return {"ok": True, "provider": "stub"}
    return await send_email(to_email=account.recap_email, subject=subject, html_body=html)


async def handle_owner_reply(db: Session, biz: Business, text: str,
                             send_message, owner_chat_id: str) -> Optional[dict]:
    """Consume an owner ✓ / ✗ / 'edit: …' reply against the newest actionable
    recap. → result dict when consumed, None when no recap is waiting (caller
    falls through to normal intent handling)."""
    recap = latest_actionable(db, int(biz.id))
    if not recap:
        return None

    from . import tasks as tasks_mod  # is_yes / is_no — the same words reps use
    edit_text = parse_edit(text)
    if not (edit_text or tasks_mod.is_yes(text) or tasks_mod.is_no(text)):
        return None

    account = db.query(Account).filter(Account.id == recap.account_id).first()
    acct_name = account.name if account else "?"

    if tasks_mod.is_no(text):
        recap.status = "skipped"
        db.commit()
        await send_message(owner_chat_id,
                           f"👍 Skipped the recap for <b>{acct_name}</b> — nothing sent.")
        return {"intent": "recap_skipped", "recap_id": int(recap.id)}

    # ✓ or edit — the text to send is the draft or the owner's replacement.
    final_text = edit_text or recap.client_text
    if not final_text:
        recap.status = "held"
        db.commit()
        await send_message(owner_chat_id,
                           f"⚠️ There's no recap text for <b>{acct_name}</b> yet — "
                           f"reply <code>edit: your recap text</code> or skip with ✗.")
        return {"intent": "recap_held", "recap_id": int(recap.id)}

    # An owner-supplied edit still goes through the safety filter — the rule
    # is "no raw worker note reaches a client", and an edit could paste one.
    ok, reason = passes_safety_filter(final_text, recap.source_text or "")
    if not ok:
        recap.status = "held"
        db.commit()
        await send_message(owner_chat_id,
                           f"⚠️ That text didn't pass the client-safety check ({reason}) "
                           f"— nothing sent. Try a different wording, or ✗ to skip.")
        return {"intent": "recap_held", "recap_id": int(recap.id), "reason": reason}

    result = await send_recap(db, biz, recap, account, final_text)
    if result.get("ok"):
        recap.client_text = final_text
        recap.status = "sent"
        recap.sent_at = datetime.utcnow()
        db.commit()
        await send_message(owner_chat_id,
                           f"✅ Recap for <b>{acct_name}</b> sent to {account.recap_email}.")
        return {"intent": "recap_sent", "recap_id": int(recap.id)}

    recap.status = "held"
    db.commit()
    await send_message(owner_chat_id,
                       f"⚠️ The email to {account.recap_email} didn't go through — "
                       f"the recap for <b>{acct_name}</b> is held. Reply ✓ to try again "
                       f"or ✗ to skip.")
    return {"intent": "recap_held", "recap_id": int(recap.id),
            "error": result.get("error")}


# ── Nightly-summary surfacing ─────────────────────────────────────
def pending_stale(db: Session, business_id: int) -> list:
    """Pending/held recaps older than the nudge window — listed in the nightly
    summary (spec: timeout → stays pending, listed there)."""
    cutoff = datetime.utcnow() - PENDING_NUDGE_AFTER
    return db.query(RecapLog).filter(
        RecapLog.business_id == business_id,
        RecapLog.status.in_(("pending_approval", "held")),
        RecapLog.created_at <= cutoff,
    ).order_by(RecapLog.created_at).all()
