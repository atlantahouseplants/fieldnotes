"""P3 — channel seam: one pipeline, two doorways (Telegram + SMS).

Design contract — keeps Telegram behavior byte-identical:

- ``Channel.send(dest, text)`` and ``Channel.send_confirmation(dest, account,
  status, extra)`` keep the EXACT signatures of integrations/telegram's
  ``send_message`` / ``send_confirmation``. Inside ``process_worker_note`` the
  imported telegram functions are shadowed with channel-bound versions, so
  every existing call site runs unchanged — Telegram in, Telegram out.

- Routing is by DESTINATION, not by source: a reply to the channel's own
  sender goes over that channel; anything addressed elsewhere (owner pings →
  ``owner_telegram_id``, recap approvals) stays on Telegram. An SMS rep whose
  task-close pings the owner still pings the owner's Telegram.

- ``find_worker(db)`` resolves identity per channel:
  Telegram = chat_id with owner + demo-business fallbacks (unchanged);
  SMS = E.164 ``Worker.phone`` — NO demo fallback (unknown numbers get the
  invite prompt from the /webhook/sms route, never the demo tenant).

- SMS formatting: HTML is stripped (Telegram messages are HTML-authored),
  bodies are truncated at SMS_LIMIT with a "Reply MORE" continuation stash
  (in-memory, 30-min TTL — single-process Railway deploy).
"""
import html
import re
import time
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from ..models import Business, Worker
from . import telegram as tg
from .agentphone import send_sms, normalize_e164

SMS_LIMIT = 900
_MORE_TTL = 1800  # 30 min

# phone → (remaining_text, expires_at). In-memory: survives neither restart nor
# multi-instance — acceptable for beta (worst case: a "MORE" replies nothing).
_MORE_STASH: dict = {}

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
START_WORDS = {"START", "UNSTOP"}

_TAG_RE = re.compile(r"<[^>]+>")


def plain(text: str) -> str:
    """Strip Telegram HTML authoring (<b>, <i>) → SMS-safe plain text."""
    return html.unescape(_TAG_RE.sub("", text or ""))


def _chunk_for_send(phone: str, text: str) -> str:
    """First SMS-sized chunk; stashes the remainder for 'reply MORE'."""
    if len(text) <= SMS_LIMIT:
        return text
    marker = "\n\nReply MORE for the rest."
    head = text[: SMS_LIMIT - len(marker)]
    _MORE_STASH[phone] = (text[len(head):], time.time() + _MORE_TTL)
    return head + marker


def pop_more(phone: str) -> Optional[str]:
    """Next continuation chunk for 'MORE', or None if nothing stashed."""
    entry = _MORE_STASH.pop(phone, None)
    if not entry:
        return None
    remaining, expires = entry
    if time.time() > expires:
        return None
    return _chunk_for_send(phone, remaining)  # re-stashes if STILL long


class Channel:
    """Base — identity + destination-routed sends."""
    name = "base"

    def __init__(self, sender_id: str):
        self.sender_id = str(sender_id)

    def find_worker(self, db: Session) -> Optional[Tuple[Worker, bool]]:
        raise NotImplementedError

    async def send(self, dest: str, text: str) -> None:
        raise NotImplementedError

    async def send_confirmation(self, dest: str, account_name: str,
                                status: str, extra: str = "") -> None:
        raise NotImplementedError


class TelegramChannel(Channel):
    """Byte-identical pass-through to the existing telegram integration."""
    name = "telegram"

    def find_worker(self, db: Session) -> Optional[Tuple[Worker, bool]]:
        # 1. Registered field worker
        worker = db.query(Worker).filter(
            Worker.telegram_id == self.sender_id,
            Worker.is_active == True  # noqa: E712
        ).first()
        if worker:
            return worker, False
        # 2. Owner → synthetic "Owner" worker in their own tenant
        owner_biz = db.query(Business).filter(
            Business.owner_telegram_id == self.sender_id,
            Business.is_active == True  # noqa: E712
        ).first()
        if owner_biz:
            from ..services import accounts as accounts_mod
            return accounts_mod.get_or_create_owner_worker(db, int(owner_biz.id)), False
        # 3. Demo fallback (business_id == 2 — seed order matters, pitfall #38b)
        demo_worker = db.query(Worker).filter(
            Worker.business_id == 2,
            Worker.is_active == True  # noqa: E712
        ).first()
        if demo_worker:
            return demo_worker, True
        return None

    async def send(self, dest: str, text: str) -> None:
        await tg.send_message(str(dest), text)

    async def send_confirmation(self, dest: str, account_name: str,
                                status: str, extra: str = "") -> None:
        await tg.send_confirmation(str(dest), account_name, status, extra=extra)


class SmsChannel(Channel):
    """SMS via AgentPhone. sender_id = worker's E.164 phone."""
    name = "sms"

    def find_worker(self, db: Session) -> Optional[Tuple[Worker, bool]]:
        worker = db.query(Worker).filter(
            Worker.phone == normalize_e164(self.sender_id),
            Worker.is_active == True  # noqa: E712
        ).first()
        if worker:
            return worker, False
        return None  # no owner-sms, no demo — invite prompt handled by the route

    async def send(self, dest: str, text: str) -> None:
        dest = str(dest)
        if normalize_e164(dest) == normalize_e164(self.sender_id):
            await send_sms(dest, _chunk_for_send(dest, plain(text)))
        else:
            # Owner pings / anything not addressed to the SMS sender → Telegram
            await tg.send_message(dest, text)

    async def send_confirmation(self, dest: str, account_name: str,
                                status: str, extra: str = "") -> None:
        dest = str(dest)
        if normalize_e164(dest) == normalize_e164(self.sender_id):
            emoji = "✅" if status == "all_good" else "⚠️"
            msg = f"{emoji} {account_name} logged — {status.replace('_', ' ').title()}"
            if extra:
                msg += f"\n{plain(extra)}"
            await send_sms(dest, msg)
        else:
            await tg.send_confirmation(dest, account_name, status, extra=extra)
