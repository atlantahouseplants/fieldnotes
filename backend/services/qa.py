"""
FieldNotes — "Ask FieldNotes" Q&A Service (P1)

Workers ask questions in the same chat they log notes to.
Answers come ONLY from the tenant's own accounts + service history + action queue.
Tenant isolation is sacred: every query is scoped by business_id.
"""
import json
import os
import re
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from ..models import Account, ServiceLog, Action, QaEvent, Worker

# ── LLM provider chain (same pattern as parser.py, but free-text answers) ──
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ── Intent heuristic ──────────────────────────────────────────────
QUESTION_STARTERS = (
    "what", "what's", "whats", "when", "when's", "where", "where's", "who",
    "how", "which", "why", "is there", "are there", "any ", "do we", "did we",
    "does ", "can you", "could you", "tell me", "show me", "remind me",
    "got any", "have we",
)
LOOKUP_PHRASES = (
    "gate code", "gate codes", "code for", "access code", "alarm code",
    "door code", "lockbox", "lock box", "key code", "wifi password",
    "wi-fi password", "wifi code", "entry code", "parking",
)


def looks_like_question(text: str) -> bool:
    """Cheap deterministic intent check — no LLM call for obvious cases."""
    t = text.strip().lower()
    if not t:
        return False
    if "?" in t:
        return True
    if any(t.startswith(w) for w in QUESTION_STARTERS):
        return True
    if any(p in t for p in LOOKUP_PHRASES):
        return True
    return False


# ── Account matching (word-boundary, NEVER loose substring) ──────
def _match_accounts(question: str, accounts: list) -> list:
    """Return accounts whose name or shorthand appears as a whole word/phrase."""
    q = question.lower()
    hits = []
    for a in accounts:
        names = {a.name.lower()}
        if a.shorthand:
            names.add(a.shorthand.lower())
        # Also try name without common suffixes ("Riverside Office Park" → "riverside")
        first_word = a.name.lower().split()[0]
        if len(first_word) >= 4:
            names.add(first_word)
        for n in names:
            if re.search(r"\b" + re.escape(n) + r"\b", q):
                hits.append(a)
                break
    # dedupe by id, preserve order
    seen, out = set(), []
    for a in hits:
        if a.id not in seen:
            seen.add(a.id)
            out.append(a)
    return out


_STOPWORDS = {
    "what", "whats", "when", "where", "who", "how", "which", "why", "the",
    "for", "and", "are", "was", "did", "does", "have", "has", "any", "there",
    "this", "that", "with", "from", "they", "them", "code", "gate", "last",
    "week", "month", "today", "tomorrow", "tell", "show", "remind", "about",
    "we", "do", "is", "it", "at", "on", "of", "to", "a", "an", "me", "my",
}


def _keywords(question: str) -> list:
    words = re.findall(r"[a-z0-9]+", question.lower())
    return [w for w in words if len(w) >= 3 and w not in _STOPWORDS][:6]


# ── Retrieval (all tenant-scoped) ─────────────────────────────────
def _gather_context(db: Session, business_id: int, question: str) -> tuple[dict, list]:
    """
    Pull relevant records for the question. Returns (context_dict, matched_accounts).
    Every query below filters business_id — no exceptions.
    """
    ctx: dict = {"accounts": [], "logs": [], "open_actions": []}

    accounts = db.query(Account).filter(
        Account.business_id == business_id, Account.is_active == True
    ).all()

    matched = _match_accounts(question, accounts)

    if len(matched) == 1:
        a = matched[0]
        ctx["accounts"].append({
            "name": a.name, "address": a.address,
            "contact_name": a.contact_name, "contact_phone": a.contact_phone,
            "notes": a.notes,
        })
        logs = db.query(ServiceLog).filter(
            ServiceLog.business_id == business_id,
            ServiceLog.account_id == a.id,
        ).order_by(ServiceLog.timestamp.desc()).limit(10).all()
        ctx["logs"] = [_log_dict(l) for l in logs]
        actions = db.query(Action).filter(
            Action.business_id == business_id,
            Action.account_id == a.id,
            Action.status == "pending",
        ).limit(10).all()
        ctx["open_actions"] = [a2.description for a2 in actions]

    elif not matched:
        # No specific account — keyword search across the tenant's history
        kws = _keywords(question)
        q = db.query(ServiceLog).filter(ServiceLog.business_id == business_id)
        logs = q.order_by(ServiceLog.timestamp.desc()).limit(200).all()
        hits = [l for l in logs if any(k in (l.raw_note or "").lower() for k in kws)][:10]
        ctx["logs"] = [_log_dict(l) for l in hits]

        # "open issues / action items" style questions → the queue
        if any(w in question.lower() for w in ("issue", "action", "open", "todo", "to-do", "outstanding", "problem")):
            actions = db.query(Action).filter(
                Action.business_id == business_id, Action.status == "pending"
            ).order_by(Action.created_at.desc()).limit(15).all()
            ctx["open_actions"] = [a.description for a in actions]

        # Give the model the account roster for orientation (names only)
        ctx["accounts"] = [{"name": a.name, "notes": a.notes} for a in accounts[:25]]

    return ctx, matched


def _log_dict(l: ServiceLog) -> dict:
    return {
        "date": l.timestamp.strftime("%Y-%m-%d") if l.timestamp is not None else None,
        "account": l.account_name() if hasattr(l, "account_name") else None,
        "note": l.raw_note,
        "status": l.parsed_status,
        "issues": l.get_issues() if hasattr(l, "get_issues") else [],
        "supplies": l.get_supplies() if hasattr(l, "get_supplies") else [],
    }


# ── Answer synthesis ──────────────────────────────────────────────
ANSWER_PROMPT = """You are FieldNotes, the assistant for a field service company. A worker in the field asked a question. Answer it using ONLY the company records below — nothing else.

Question: {question}

Company records (JSON):
{context}

Rules:
- Answer ONLY from the records above. Never invent client facts (codes, names, dates).
- If the records don't contain the answer, say honestly that you don't have it yet and suggest the worker log it after their next visit so you'll know next time.
- Keep it to 2-4 short sentences, plain text, no markdown — this is read on a phone in the field.
- Mention where the answer came from (account name or log date) so the worker can trust it.

Answer:"""


async def _llm_text(prompt: str) -> str:
    """Free-text LLM call: Grok → DeepSeek → OpenAI."""
    providers = [
        ("https://api.x.ai/v1", XAI_API_KEY, "grok-4.5"),
        ("https://api.deepseek.com/v1", DEEPSEEK_API_KEY, "deepseek-chat"),
        ("https://api.openai.com/v1", OPENAI_API_KEY, "gpt-4o-mini"),
    ]
    last_err = None
    for base, key, model in providers:
        if not key:
            continue
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                        "max_tokens": 300,
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"all LLM providers failed: {last_err}")


def _deterministic_answer(ctx: dict, question: str) -> str:
    """No-LLM fallback: surface raw records honestly."""
    parts = []
    for a in ctx["accounts"]:
        if a.get("notes"):
            parts.append(f"{a['name']} notes: {a['notes']}")
        if a.get("address"):
            parts.append(f"{a['name']} address: {a['address']}")
        if a.get("contact_name"):
            parts.append(f"{a['name']} contact: {a['contact_name']} {a.get('contact_phone') or ''}".strip())
    for l in ctx["logs"][:3]:
        parts.append(f"Log {l['date']} ({l.get('account') or 'unassigned'}): {l['note']}")
    for act in ctx["open_actions"][:5]:
        parts.append(f"Open action: {act}")
    if parts:
        return "Here's what I have on file:\n" + "\n".join(parts[:8])
    return ("I don't have that on file yet. Log it after your next visit "
            "and I'll know next time.")


async def answer_question(db: Session, business_id: int, worker: Optional[Worker], question: str) -> dict:
    """
    Answer a worker's question from tenant data. Writes a QaEvent.
    Returns {"answer": str, "sources": list, "clarification": bool}.
    """
    ctx, matched = _gather_context(db, business_id, question)

    # Ambiguous account mention → ask, don't guess
    if len(matched) > 1:
        names = " or ".join(a.name for a in matched[:4])
        answer = f"Which one do you mean — {names}?"
        _record_event(db, business_id, worker, question, answer, {"clarification": [a.name for a in matched]})
        return {"answer": answer, "sources": [a.name for a in matched], "clarification": True}

    has_data = bool(ctx["logs"] or ctx["open_actions"] or any(
        v for a in ctx["accounts"] for v in (a.get("notes"), a.get("address"), a.get("contact_name"))
    ))

    sources = ([a["name"] for a in ctx["accounts"][:3]]
               + [f"log {l['date']}" for l in ctx["logs"][:3]])

    if has_data:
        prompt = (ANSWER_PROMPT
                  .replace("{question}", question)
                  .replace("{context}", json.dumps(ctx, default=str)[:6000]))
        try:
            answer = await _llm_text(prompt)
        except Exception:
            answer = _deterministic_answer(ctx, question)
    else:
        answer = ("I don't have that on file yet for your company. "
                  "Log it after your next visit and I'll know next time.")

    _record_event(db, business_id, worker, question, answer, {"sources": sources, "used_llm": has_data})
    return {"answer": answer, "sources": sources, "clarification": False}


def _record_event(db: Session, business_id: int, worker: Optional[Worker],
                  question: str, answer: str, meta: dict) -> None:
    try:
        ev = QaEvent(
            business_id=business_id,
            worker_id=worker.id if worker else None,
            question=question,
            answer=answer,
            sources=json.dumps(meta, default=str),
            created_at=datetime.utcnow(),
        )
        db.add(ev)
        db.commit()
    except Exception:
        db.rollback()  # never let telemetry break the answer path
