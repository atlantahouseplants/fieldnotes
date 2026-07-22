"""
FieldNotes — Telegram Webhook Route
Receives worker messages, parses them, logs service, creates actions, sends confirmation.
"""
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import json
import os
import secrets as _secrets

TELEGRAM_SECRET = os.getenv("TELEGRAM_SECRET", "")

from ..models import SessionLocal, Worker, Account, ServiceLog, Business
from ..services.parser import parse_note
from ..services.actions import create_action, bulk_create_actions
from ..services.action_queue import add_action as queue_add
from ..services.ahp_pipeline import run_pipeline
from ..services.ingest import persist_parsed_note
from ..services.qa import looks_like_question, answer_question, route_intent, _match_accounts
from ..services import tasks as tasks_mod
from ..services import accounts as accounts_mod
from .onboarding import BOT_USERNAME
from ..integrations.telegram import send_confirmation, send_message
from ..deps import has_feature, upgrade_message

router = APIRouter(prefix="/webhook", tags=["webhook"])


def _record_gated_attempt(db: Session, business_id: int, worker, question: str, feature: str) -> None:
    """P5 telemetry: record gated feature attempts as upgrade-intent signal.
    Never let telemetry break the reply path."""
    from ..models import QaEvent
    try:
        ev = QaEvent(
            business_id=business_id,
            worker_id=worker.id if worker else None,
            question=question,
            answer=f"[GATED:{feature}] upgrade prompt shown",
            sources=json.dumps({"gated": True, "feature": feature}),
            created_at=datetime.utcnow(),
        )
        db.add(ev)
        db.commit()
    except Exception:
        db.rollback()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/telegram/status")
async def webhook_status():
    """Quick check that webhook endpoint is alive."""
    return {"status": "ready", "service": "FieldNotes Telegram Webhook"}


@router.post("/telegram")
async def telegram_webhook(request: Request):
    """
    Receive Telegram messages, process worker notes.
    
    Flow:
    1. Receive message from Telegram
    2. Identify worker by Telegram ID
    3. Parse note with AI
    4. Match account name
    5. Create service log
    6. Generate action items
    7. Send confirmation to worker
    """
    # Verify the update actually came from Telegram (secret_token set at setWebhook)
    if TELEGRAM_SECRET:
        sent = request.headers.get("x-telegram-bot-api-secret-token", "")
        if not _secrets.compare_digest(sent, TELEGRAM_SECRET):
            raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()
    
    # Extract message from Telegram update
    message = body.get("message", {})
    if not message:
        return {"ok": True, "detail": "no message in update"}
    
    chat = message.get("chat", {})
    text = message.get("text", "")
    telegram_id = str(chat.get("id", ""))
    
    if not text or not telegram_id:
        return {"ok": True, "detail": "no text or chat id"}

    # Handle /start commands (deep-link worker registration + welcome)
    if text.startswith("/start"):
        db = SessionLocal()
        try:
            return await handle_start(db, telegram_id, text, chat)
        finally:
            db.close()

    # Process in a fresh DB session
    db = SessionLocal()
    try:
        result = await process_worker_note(db, telegram_id, text)
        return {"ok": True, **result}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def handle_start(db: Session, telegram_id: str, text: str, chat: dict) -> dict:
    """Handle /start — deep-link worker registration or plain welcome."""
    parts = text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    first_name = chat.get("first_name") or chat.get("username") or "there"

    if payload.startswith("owner_"):
        token = payload[len("owner_"):]
        biz = db.query(Business).filter(Business.invite_token == token).first()
        if not biz:
            await send_message(telegram_id, "⚠️ That owner link isn't valid. Generate a fresh one from your dashboard.")
            return {"ok": True, "detail": "invalid_owner_link"}

        biz.owner_telegram_id = telegram_id
        db.commit()

        await send_message(
            telegram_id,
            f"👑 You're linked as the owner of <b>{biz.name}</b>!\n\n"
            f"Every evening I'll send you a daily summary here: stops completed, "
            f"missed stops, issues flagged, and open actions.\n\n"
            f"Your dashboard: https://fieldnotesapp.io/app/dashboard.html?biz={biz.id}&key={biz.dashboard_key}"
        )
        return {"ok": True, "detail": "owner_linked", "business": biz.name}

    if payload.startswith("invite_"):
        token = payload[len("invite_"):]
        biz = db.query(Business).filter(Business.invite_token == token).first()
        if not biz:
            await send_message(telegram_id, "⚠️ That invite link isn't valid. Ask your boss for a fresh one.")
            return {"ok": True, "detail": "invalid_invite"}

        worker = db.query(Worker).filter(Worker.telegram_id == telegram_id).first()
        if worker:
            worker.business_id = biz.id
            worker.is_active = True
        else:
            worker = Worker(business_id=biz.id, name=first_name, telegram_id=telegram_id)
            db.add(worker)
        db.commit()

        await send_message(
            telegram_id,
            f"🎉 You're connected to <b>{biz.name}</b>!\n\n"
            f"After each stop, just send me a quick message like:\n"
            f"<i>\"Acme Office: all good, replaced filter, need more filters next time.\"</i>\n\n"
            f"Voice notes work too. 10 seconds, done. 🎙️"
        )
        return {"ok": True, "detail": "worker_registered", "business": biz.name}

    # Plain /start — no invite
    await send_message(
        telegram_id,
        f"👋 Hey {first_name}! I'm the <b>FieldNotes</b> bot.\n\n"
        f"Field crews send me 10-second voice notes between stops — "
        f"I turn them into service logs, action queues, and daily summaries for the boss.\n\n"
        f"<b>Trying the demo?</b> Just send me a note like:\n"
        f"<i>\"Acme Office: serviced, all good. River Towers: pump leaking, need parts.\"</i>\n\n"
        f"<b>Have an invite link from your boss?</b> Tap it to connect to your company.\n"
        f"<b>Want FieldNotes for your crew?</b> https://fieldnotesapp.io"
    )
    return {"ok": True, "detail": "welcome_sent"}


def _resolve_one_account(query: str, accounts: list):
    """Exact name/shorthand match first (case-insensitive); else word-boundary
    fuzzy. Returns the Account, 'ambiguous', or None."""
    q = query.strip().lower()
    exact = [a for a in accounts if (a.name and a.name.lower() == q)
             or (a.shorthand and a.shorthand.lower() == q)]
    if len(exact) == 1:
        return exact[0]
    fuzzy = _match_accounts(query, accounts)
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        return "ambiguous"
    return None


async def _ping_owner_task_done(db: Session, biz, closer_telegram_id: str,
                                worker, task, account_name: str) -> None:
    """Owner-only completion ping — NEVER to reps (spec pitfall). Silent no-op
    if the owner hasn't linked Telegram or closed it themselves."""
    try:
        if not biz or not biz.owner_telegram_id:
            return
        if str(biz.owner_telegram_id) == str(closer_telegram_id):
            return
        await send_message(
            str(biz.owner_telegram_id),
            f"✅ <b>{task.title}</b> at {account_name} — done, closed by {worker.name}.")
    except Exception:
        pass  # a failed ping must never break the reply path


async def process_worker_note(db: Session, telegram_id: str, text: str) -> dict:
    """Process a single worker note end-to-end."""

    # 1. Find worker
    worker = db.query(Worker).filter(
        Worker.telegram_id == telegram_id,
        Worker.is_active == True
    ).first()

    is_demo = False
    if not worker:
        # P6b: the owner isn't a field worker — resolve them via
        # Business.owner_telegram_id so their chat commands and notes land
        # in their OWN tenant (attributed to the synthetic "Owner" worker).
        owner_biz = db.query(Business).filter(
            Business.owner_telegram_id == telegram_id,
            Business.is_active == True
        ).first()
        if owner_biz:
            worker = accounts_mod.get_or_create_owner_worker(db, int(owner_biz.id))
        else:
            # DEMO MODE: Allow unregistered users to test with the demo business
            demo_worker = db.query(Worker).filter(
                Worker.business_id == 2,  # Precision HVAC demo
                Worker.is_active == True
            ).first()
            if demo_worker:
                worker = demo_worker
                is_demo = True
            else:
                return {"detail": "unknown_worker", "telegram_id": telegram_id}

    business_id = int(worker.business_id)

    biz = db.query(Business).filter(Business.id == business_id).first()
    is_owner_chat = bool(biz and biz.owner_telegram_id
                         and str(biz.owner_telegram_id) == str(telegram_id))

    # ── P7: YES/NO answer to a pending task-close proposal ────────
    if tasks_mod.is_yes_or_no(text):
        pend = tasks_mod.pending_close_for(db, int(worker.id))
        if pend:
            pending, task = pend
            acct = db.query(Account).filter(Account.id == task.account_id).first()
            acct_name = acct.name if acct else "?"
            if tasks_mod.is_yes(text):
                tasks_mod.close_task(db, task, closed_by_worker_id=int(worker.id))
                tasks_mod.clear_pending(db, pending)
                await send_message(telegram_id,
                                   f"✅ Marked done: <b>{task.title}</b> at {acct_name}. Nice work.")
                await _ping_owner_task_done(db, biz, telegram_id, worker, task, acct_name)
                return {"worker": worker.name, "intent": "close_task",
                        "task_id": int(task.id), "via": "confirm"}
            tasks_mod.clear_pending(db, pending)
            await send_message(telegram_id,
                               f"👍 OK — left <b>{task.title}</b> open at {acct_name}.")
            return {"worker": worker.name, "intent": "close_task_declined",
                    "task_id": int(task.id)}
        # No pending proposal — fall through; a bare "yes"/"no" is just a note.

    # ── P7: task create — "Task for <account>: <what>" ────────────
    create_intent = tasks_mod.task_create_intent(text)
    if create_intent:
        account_query, body = create_intent
        accounts_all = db.query(Account).filter(
            Account.business_id == business_id, Account.is_active == True).all()
        acct = _resolve_one_account(account_query, accounts_all)
        if acct is None:
            await send_message(
                telegram_id,
                f"⚠️ Couldn't find a customer called \"{account_query}\". "
                f"Check the name or add them on the dashboard first.")
            return {"worker": worker.name, "intent": "create_task",
                    "error": "account_not_found", "query": account_query}
        if acct == "ambiguous":
            names = [a.name for a in _match_accounts(account_query, accounts_all)][:4]
            await send_message(
                telegram_id,
                f"⚠️ Which customer? Could be: {', '.join(names)}. "
                f"Say it like \"Task for <full name>: …\"")
            return {"worker": worker.name, "intent": "create_task",
                    "error": "account_ambiguous", "query": account_query}
        crew = db.query(Worker).filter(
            Worker.business_id == business_id, Worker.is_active == True).all()
        parts = tasks_mod.parse_task_body(body, crew)
        if not parts["title"]:
            await send_message(telegram_id, "⚠️ What's the task? Say it like "
                                            "\"Task for Smith Office: repair pool cover\".")
            return {"worker": worker.name, "intent": "create_task", "error": "empty_title"}
        task = tasks_mod.create_task(
            db, business_id=business_id, account_id=int(acct.id),
            title=parts["title"], supplies=parts["supplies"], due=parts["due"],
            assigned_worker_id=int(parts["assigned"].id) if parts["assigned"] else None,
            source="chat_owner" if is_owner_chat else "chat_rep",
            created_by_worker_id=None if is_owner_chat else int(worker.id),
        )
        who = f"<b>{parts['assigned'].name}</b> will see it" if parts["assigned"] \
            else "The crew will see it"
        when = f" — {parts['due']}" if parts["due"] else ""
        sup = f"\n🧰 Supplies: {task.supplies_needed}" if task.supplies_needed else ""
        await send_message(
            telegram_id,
            f"📋 Task added to <b>{acct.name}</b>: {task.title}.{sup}\n"
            f"{who} at log time and in the morning route{when}.")
        return {"worker": worker.name, "intent": "create_task",
                "task_id": int(task.id), "account": acct.name,
                "assigned_to": parts["assigned"].name if parts["assigned"] else None,
                "due": parts["due"], "supplies": parts["supplies"]}

    # ── P7: task close — completion language + account with open tasks ──
    # Guard: requires BOTH completion phrasing AND a matched account that
    # actually has open tasks, so a normal "Smith: all done" note still logs.
    if tasks_mod.task_close_language(text):
        accounts_all = db.query(Account).filter(
            Account.business_id == business_id, Account.is_active == True).all()
        for acct in _match_accounts(text, accounts_all):
            open_t = tasks_mod.open_tasks_for_account(db, business_id, int(acct.id))
            if not open_t:
                continue
            cands = tasks_mod.match_open_tasks(open_t, text)
            if len(cands) == 1:
                task = cands[0]
                tasks_mod.close_task(db, task, closed_by_worker_id=int(worker.id))
                await send_message(
                    telegram_id,
                    f"✅ Marked done: <b>{task.title}</b> at {acct.name}. Nice work.")
                await _ping_owner_task_done(db, biz, telegram_id, worker, task, acct.name)
                return {"worker": worker.name, "intent": "close_task",
                        "task_id": int(task.id), "account": acct.name, "via": "explicit"}
            if len(cands) > 1:
                listed = "; ".join(t.title for t in cands[:3])
                await send_message(
                    telegram_id,
                    f"⚠️ Which one is done at {acct.name}? Open tasks: {listed}. "
                    f"Say \"done with <name>\".")
                return {"worker": worker.name, "intent": "close_task",
                        "error": "ambiguous", "candidates": [t.title for t in cands]}
            # No title overlap → normal log note; falls through.

    # ── P6b: owner chat command — "New account: Name, address, gate, schedule" ──
    # Owner-only by spec; a rep's identical text just becomes a normal note.
    na = accounts_mod.parse_new_account(text)
    if na and is_owner_chat:
        account, err = accounts_mod.create_account(
            db, business_id, na["name"], address=na["address"],
            gate_code=na["gate_code"], schedule=na["schedule"])
        if err:
            await send_message(telegram_id, f"⚠️ {err}")
            return {"worker": worker.name, "intent": "new_account", "error": err}
        bits = []
        if account.address:
            bits.append(str(account.address))
        if account.gate_code:
            bits.append(f"gate {account.gate_code}")
        if account.schedule:
            bits.append(f"route: {account.schedule}")
        detail = f" — {', '.join(bits)}" if bits else ""
        await send_message(
            telegram_id,
            f"🏢 Customer added ✓ <b>{account.name}</b>{detail}.\n"
            f"Reps can log at it right away; it shows on the dashboard too.")
        return {"worker": worker.name, "intent": "new_account",
                "account_id": int(account.id), "account": account.name}

    # ── P6b: "Note for <customer>: …" — owner or rep, explicit account ──
    nf = accounts_mod.parse_note_for(text)
    if nf:
        account_q, note_body = nf
        all_accounts = db.query(Account).filter(
            Account.business_id == business_id, Account.is_active == True).all()
        acct = _resolve_one_account(account_q, all_accounts)
        if acct is None:
            await send_message(
                telegram_id,
                f"⚠️ Couldn't find a customer called \"{account_q}\" — nothing saved. "
                f"Check the name, or add them first.")
            return {"worker": worker.name, "intent": "note_for",
                    "error": "account_not_found", "query": account_q}
        if acct == "ambiguous":
            names = [a.name for a in _match_accounts(account_q, all_accounts)][:4]
            await send_message(
                telegram_id,
                f"⚠️ Which customer? Could be: {', '.join(names)}. "
                f"Say it like \"Note for <full name>: …\"")
            return {"worker": worker.name, "intent": "note_for",
                    "error": "account_ambiguous", "query": account_q}
        hints = [acct.name] + ([acct.shorthand] if acct.shorthand else [])
        parsed = await parse_note(note_body, hints)
        persisted = persist_parsed_note(
            db, business_id=business_id, worker_id=int(worker.id),
            text=note_body, parsed=parsed, account_id=int(acct.id))
        status = parsed.get("status", "logged")
        await send_confirmation(telegram_id, acct.name, status)
        return {"worker": worker.name, "intent": "note_for",
                "account": acct.name, "account_id": int(acct.id),
                "status": status, "log_id": int(persisted["log"].id)}

    # ── P6b: "invite" — owner gets the link; reps get pointed to the boss ──
    if accounts_mod.invite_intent(text):
        if is_owner_chat and biz:
            if not biz.invite_token:
                biz.invite_token = _secrets.token_urlsafe(12)
                db.commit()
                db.refresh(biz)
            link = f"https://t.me/{BOT_USERNAME}?start=invite_{biz.invite_token}"
            await send_message(
                telegram_id,
                f"👷 Send this to your new guy — he taps it, presses START, "
                f"and his notes land in your account. No setup:\n{link}")
            return {"worker": worker.name, "intent": "invite"}
        await send_message(
            telegram_id,
            "Only the boss can invite workers — ask them to send the invite "
            "link (it's on their dashboard).")
        return {"worker": worker.name, "intent": "invite", "error": "not_owner"}

    # ── Ask FieldNotes: questions get answers, not service logs ──
    if looks_like_question(text):
        # P5: feature gate — route questions need Crew, other Q&A needs Team.
        # Beta tenants (beta_all_access) pass everything. Gated attempts are
        # recorded in qa_events as upgrade-intent signal (no answer given).
        biz = db.query(Business).filter(Business.id == business_id).first()
        feature = "routes" if route_intent(text) else "qa"
        if biz and not has_feature(biz, feature):
            msg = upgrade_message(feature, biz)
            await send_message(telegram_id, msg)
            _record_gated_attempt(db, business_id, worker, text, feature)
            return {
                "worker": worker.name,
                "intent": "question",
                "gated": True,
                "feature": feature,
                "answer": msg,
            }
        qa = await answer_question(db, business_id, worker, text)
        await send_message(telegram_id, qa["answer"])
        if is_demo:
            await send_message(
                telegram_id,
                "🧪 <i>Demo mode — answered from our sample business, Precision HVAC. "
                "Want this for your crew? https://fieldnotesapp.io</i>"
            )
        return {
            "worker": worker.name,
            "intent": "question",
            "answer": qa["answer"],
            "sources": qa["sources"],
            "clarification": qa["clarification"],
        }

    # 2. Get known accounts for matching
    accounts = db.query(Account).filter(
        Account.business_id == business_id,
        Account.is_active == True
    ).all()
    account_hints = [a.shorthand or a.name for a in accounts]
    
    # Build mapping: shorthand/name → account.id
    account_map = {}
    for a in accounts:
        account_map[a.name.lower()] = a.id
        if a.shorthand:
            account_map[a.shorthand.lower()] = a.id
    
    # 3. Parse note with AI
    parsed = await parse_note(text, account_hints)
    
    # 4. Match account
    account_hint = (parsed.get("account_hint") or "").lower()
    account_id = None
    account_name = account_hint or text[:40]
    
    # Try exact match first
    if account_hint in account_map:
        account_id = account_map[account_hint]
        account_name = account_hint
    
    # If still no match, leave as uncategorized — owner can re-assign
    if not account_id:
        account_name = account_hint if account_hint else "uncategorized"
    
    # 5-7. Persist through the SHARED pipeline (services/ingest.py):
    # ServiceLog + action items + deterministic execution pipeline.
    # Owner-added dashboard notes go through this exact same function.
    persisted = persist_parsed_note(
        db,
        business_id=business_id,
        worker_id=int(worker.id),
        text=text,
        parsed=parsed,
        account_id=account_id,
    )
    log = persisted["log"]
    actions_created = persisted["actions_created"]
    pipeline_result = persisted["pipeline"]

    # 8. Confirm — with open-task annotation (P7 log-time surfacing), then
    # implicit-completion proposal when the note overlaps exactly one task.
    open_tasks = tasks_mod.open_tasks_for_account(db, business_id, account_id) \
        if account_id else []
    await send_confirmation(
        chat_id=telegram_id,
        account_name=account_name,
        status=parsed.get("status", "logged"),
        extra=tasks_mod.tasks_annotation(open_tasks),
    )
    proposed_task_id = None
    if open_tasks:
        cands = tasks_mod.match_open_tasks(open_tasks, text)
        if len(cands) == 1:
            proposed = cands[0]
            tasks_mod.propose_close(db, business_id, int(worker.id), proposed)
            proposed_task_id = int(proposed.id)
            await send_message(
                telegram_id,
                f"🤔 That sounds like <b>{proposed.title}</b> — mark it done? "
                f"Reply YES or NO.")

    # Demo disclosure — unregistered users are testing the sample business
    if is_demo:
        await send_message(
            telegram_id,
            "🧪 <i>Demo mode — logged to our sample business, Precision HVAC. "
            "Want this for your crew? https://fieldnotesapp.io</i>"
        )
    
    return {
        "worker": worker.name,
        "account": account_name,
        "account_id": account_id,
        "status": parsed.get("status"),
        "summary": parsed.get("summary", text[:100]),
        "actions_created": actions_created,
        "processing_ms": parsed.get("processing_time_ms", 0),
        "pipeline": pipeline_result,
        "open_tasks": len(open_tasks),
        "proposed_task_id": proposed_task_id,
    }
