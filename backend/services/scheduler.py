"""
H1: In-process scheduler — replaces the WSL cron scripts for prod-path jobs.

Fires, America/New_York time, Monday-Friday:
  - 06:45  morning route push       (was ~/.hermes/scripts/fieldnotes_route_push.sh)
  - 19:00  nightly owner summaries  (was ~/.hermes/scripts/fieldnotes_daily_summary.sh)

Rules:
- GATED by FIELDNOTES_SCHEDULER_ENABLED=1 (Railway prod only). Default OFF so
  local dev + test servers never fire real messages.
- Grace window per job: if the server is down at the scheduled minute, the job
  fires late inside the window and is skipped beyond it (a 3pm route push is
  worse than none; a 9pm summary is still useful).
- Failure alerts the founder (FIELDNOTES_FOUNDER_CHAT_ID) via Telegram;
  success is silent except stdout (Railway logs) — mirrors the old watchdogs.
- SINGLE-REPLICA assumption (Railway Hobby runs one). If replicas ever scale
  past 1, disable this and move to an external scheduler, or jobs fire N times.
"""
import asyncio
import os
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# (job name, ET fire time, weekdays Mon=0, grace minutes)
SCHEDULE = [
    ("route_push", time(6, 45), {0, 1, 2, 3, 4}, 60),
    ("nightly_summary", time(19, 0), {0, 1, 2, 3, 4}, 120),
]

_last_fired = {}  # job name -> ET date last fired


def due_jobs(now: datetime, last_fired: Optional[dict] = None) -> list:
    """Pure: which jobs should fire at `now` (ET-aware), given last-fired dates."""
    fired = _last_fired if last_fired is None else last_fired
    due = []
    for name, at, weekdays, grace_min in SCHEDULE:
        if now.weekday() not in weekdays:
            continue
        if fired.get(name) == now.date():
            continue
        sched = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        if sched <= now <= sched + timedelta(minutes=grace_min):
            due.append(name)
    return due


async def _alert(text: str) -> None:
    chat_id = os.getenv("FIELDNOTES_FOUNDER_CHAT_ID", "")
    if not chat_id:
        print(f"[scheduler] ALERT (no FIELDNOTES_FOUNDER_CHAT_ID): {text}")
        return
    from ..integrations.telegram import send_message
    await send_message(chat_id, text)


async def fire_job(name: str) -> dict:
    """Fire one scheduled job in-process by calling the existing route handlers.

    Alerts the founder on hard failure (exception) and on partial failure
    (nightly summaries with per-business failures) — same semantics as the
    retired WSL watchdog scripts.
    """
    from ..models import SessionLocal
    from ..routes.summary import route_push, send_daily

    secret = os.getenv("FIELDNOTES_CRON_SECRET", "")
    db = SessionLocal()
    try:
        if name == "route_push":
            result = await route_push(secret=secret, db=db)
        elif name == "nightly_summary":
            result = await send_daily(secret=secret, db=db)
        else:
            raise ValueError(f"unknown job: {name}")
        print(f"[scheduler] {name} ok: {result}")
        failed = result.get("failed") or []
        if failed:
            await _alert(f"⚠️ FieldNotes {name} partial failure: {failed}")
        return result
    except Exception as e:
        print(f"[scheduler] {name} FAILED: {e}")
        await _alert(f"🚨 FieldNotes scheduler: {name} FAILED — {e}")
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def _loop(tick_seconds: int) -> None:
    while True:
        try:
            now = datetime.now(ET)
            for name in due_jobs(now):
                # Mark BEFORE firing: a failed job alerts once, never retry-spams.
                _last_fired[name] = now.date()
                await fire_job(name)
        except Exception as e:
            print(f"[scheduler] tick error: {e}")
        await asyncio.sleep(tick_seconds)


def scheduler_status() -> dict:
    return {
        "enabled": os.getenv("FIELDNOTES_SCHEDULER_ENABLED") == "1",
        "last_fired": {k: v.isoformat() for k, v in _last_fired.items()},
    }


def start_scheduler() -> Optional[asyncio.Task]:
    """Start the background loop. Returns None (disabled) unless explicitly enabled."""
    if os.getenv("FIELDNOTES_SCHEDULER_ENABLED") != "1":
        print("[scheduler] disabled (set FIELDNOTES_SCHEDULER_ENABLED=1 to enable)")
        return None
    tick = int(os.getenv("FIELDNOTES_SCHEDULER_TICK_SECONDS", "30"))
    print(f"[scheduler] ENABLED — tick {tick}s, jobs: {[s[0] for s in SCHEDULE]}")
    return asyncio.create_task(_loop(tick))
