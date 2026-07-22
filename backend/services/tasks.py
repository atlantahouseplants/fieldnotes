"""P7 — Account Tasks: the delegation loop.

Owner (or a rep) creates a task attached to an account. Open tasks surface at
log time, in the morning push, in Q&A, and on the dashboard. Completion is
confirmed, never silent:
  - explicit close ("done with the cover at Smith") matches account first,
    then title similarity — 2+ candidates → ask, don't guess;
  - implicit completion (a normal log note that overlaps a task title) creates
    a PendingTaskClose and asks YES/NO — nothing closes without the rep's word.

Tenant isolation: every query filters business_id. All parsing here is
deterministic (no LLM) — cheap, testable, and safe against prompt weirdness.
"""
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..models import AccountTask, PendingTaskClose, Account, Worker

# ── Intent detection ──────────────────────────────────────────────
_TASK_CREATE = re.compile(r"^\s*task\s+for\s+(.+?)\s*:\s*(.+)$", re.I | re.S)
_COMPLETION = re.compile(
    r"\b(done with|finished|completed|wrapped up|took care of|task done|fixed the)\b", re.I)
_SUPPLIES = re.compile(r"\b(?:needs?|bring|with)\s+([^,]+)", re.I)
_DAY_WORDS = ("monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday", "today", "tomorrow")
_YES = {"yes", "y", "yeah", "yep", "si", "sí", "✓"}
_NO = {"no", "n", "nope", "✗", "cancel"}

_PENDING_TTL = timedelta(hours=24)


def task_create_intent(text: str) -> Optional[tuple]:
    """'Task for <account>: <body>' → (account_query, body), else None."""
    m = _TASK_CREATE.match(text)
    if not m:
        return None
    account_query, body = m.group(1).strip(), m.group(2).strip()
    if not account_query or not body:
        return None
    return account_query, body


def task_close_language(text: str) -> bool:
    """Completion phrasing — caller must ALSO require a matched account with
    open tasks before treating this as a close intent (otherwise 'Smith: all
    done, filters changed' is a normal log note)."""
    return bool(_COMPLETION.search(text))


def is_yes(text: str) -> bool:
    return text.strip().lower() in _YES


def is_no(text: str) -> bool:
    return text.strip().lower() in _NO


def is_yes_or_no(text: str) -> bool:
    return is_yes(text) or is_no(text)


# ── Task-body parsing (deterministic) ─────────────────────────────
def parse_task_body(body: str, workers: list) -> dict:
    """Extract supplies / assigned worker / due day from free text.

    Example: 'repair cover, needs patch kit, Mike, Thursday' →
    title='repair cover', supplies='patch kit', assigned=Mike's row,
    due='Thursday'. The matched worker name and day word are stripped from
    the title. Only active workers of the SAME business are passed in.
    """
    text = body.strip().rstrip(".")

    # Order matters: strip the due day and worker name FIRST so a supplies
    # clause ("needs patch kit, Mike, Thursday") can't swallow them.
    due = None
    for d in _DAY_WORDS:
        if re.search(r"\b" + d + r"\b", text.lower()):
            due = d.capitalize() if d not in ("today", "tomorrow") else d
            text = re.sub(r"\b" + d + r"\b", "", text, flags=re.I).strip(" ,")
            break

    assigned = None
    for w in workers:
        if not w.name:
            continue
        first = w.name.split()[0].lower()
        if len(first) >= 3 and re.search(r"\b" + re.escape(first) + r"\b", text.lower()):
            assigned = w
            text = re.sub(r"\b" + re.escape(first) + r"\b", "", text, flags=re.I).strip(" ,")
            break

    supplies = None
    m = _SUPPLIES.search(text)
    if m:
        supplies = m.group(1).strip().rstrip(",").strip()
        text = (text[:m.start()] + text[m.end():]).strip(" ,")

    # Tidy leftover comma runs from stripping
    title = re.sub(r"\s*,\s*,\s*", ", ", text).strip(" ,")
    return {"title": title, "supplies": supplies, "assigned": assigned, "due": due}


# ── CRUD (all tenant-scoped) ──────────────────────────────────────
def create_task(db: Session, business_id: int, account_id: int, title: str,
                details: str = None, supplies: str = None, due: str = None,
                assigned_worker_id: int = None, source: str = "chat_owner",
                created_by_worker_id: int = None) -> AccountTask:
    task = AccountTask(
        business_id=business_id, account_id=account_id,
        title=title.strip(), details=(details or None),
        status="open", assigned_worker_id=assigned_worker_id,
        supplies_needed=(supplies or None), due_date=(due or None),
        source=source, created_by_worker_id=created_by_worker_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def open_tasks_for_account(db: Session, business_id: int, account_id: int) -> list:
    return db.query(AccountTask).filter(
        AccountTask.business_id == business_id,
        AccountTask.account_id == account_id,
        AccountTask.status == "open",
    ).order_by(AccountTask.created_at).all()


def open_tasks_for_business(db: Session, business_id: int) -> list:
    return db.query(AccountTask).filter(
        AccountTask.business_id == business_id,
        AccountTask.status == "open",
    ).order_by(AccountTask.created_at).all()


def close_task(db: Session, task: AccountTask, closed_by_worker_id: int = None) -> AccountTask:
    task.status = "done"
    task.closed_at = datetime.utcnow()
    task.closed_by_worker_id = closed_by_worker_id
    db.commit()
    db.refresh(task)
    return task


# ── Title-similarity matching (account is already fixed) ─────────
def _title_words(title: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) >= 3}


def match_open_tasks(tasks: list, text: str) -> list:
    """Rank open tasks by title-word overlap with the text.

    Returns candidates sorted best-first; caller decides: exactly one → act,
    more → ask. A task with zero overlap is never a candidate.
    """
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    scored = []
    for t in tasks:
        tw = _title_words(t.title)
        if not tw:
            continue
        overlap = len(tw & words) / len(tw)
        if overlap >= 0.5 or (tw & words and any(len(w) >= 5 for w in tw & words)):
            scored.append((overlap, t))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [t for _, t in scored]


# ── Pending YES/NO confirmations ──────────────────────────────────
def propose_close(db: Session, business_id: int, worker_id: int, task: AccountTask) -> None:
    """Record a proposal; replace any stale proposals for this worker."""
    db.query(PendingTaskClose).filter(
        PendingTaskClose.worker_id == worker_id,
    ).delete()
    db.add(PendingTaskClose(business_id=business_id, worker_id=worker_id, task_id=task.id))
    db.commit()


def pending_close_for(db: Session, worker_id: int) -> Optional[tuple]:
    """Most recent unexpired proposal → (pending, task) if task still open."""
    p = db.query(PendingTaskClose).filter(
        PendingTaskClose.worker_id == worker_id,
    ).order_by(PendingTaskClose.created_at.desc()).first()
    if not p:
        return None
    if datetime.utcnow() - p.created_at > _PENDING_TTL:
        db.delete(p)
        db.commit()
        return None
    task = db.query(AccountTask).filter(
        AccountTask.id == p.task_id, AccountTask.status == "open",
    ).first()
    if not task:
        db.delete(p)
        db.commit()
        return None
    return p, task


def clear_pending(db: Session, pending: PendingTaskClose) -> None:
    db.delete(pending)
    db.commit()


# ── Formatting ────────────────────────────────────────────────────
def task_line(task: AccountTask) -> str:
    s = task.title
    if task.supplies_needed:
        s += f" ({task.supplies_needed})"
    return s


def tasks_annotation(tasks: list) -> str:
    """'⚠️ 1 open task: repair pool cover (patch kit)' — appended to log
    confirmations and morning-push stop lines."""
    if not tasks:
        return ""
    n = len(tasks)
    listed = "; ".join(task_line(t) for t in tasks[:3])
    more = f" +{n - 3} more" if n > 3 else ""
    word = "task" if n == 1 else "tasks"
    return f"⚠️ {n} open {word}: {listed}{more}"
