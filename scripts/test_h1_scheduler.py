#!/usr/bin/env python3
"""
H1 scheduler suite — in-process, no server boot needed (port 8777 reserved).

Covers:
  - due_jobs: exact-time fire, grace window, weekend skip, same-day refire block
  - start_scheduler: disabled by default, enabled with env, returns a task
  - fire_job: route_push + nightly_summary plumbing against a temp DB,
    partial-failure alert path, exception alert path
  - scheduler_status shape (what /health exposes)

Env safety: TELEGRAM_BOT_TOKEN is forced EMPTY before imports so any
send_message call is a designed no-op (integrations/telegram reads the token
at module import time). No network, no cost.
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime

# --- env BEFORE any backend imports (pitfall: module-level token reads) ---
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["FIELDNOTES_CRON_SECRET"] = "test-secret"
os.environ.pop("FIELDNOTES_SCHEDULER_ENABLED", None)
_tmp = tempfile.mkdtemp(prefix="h1_sched_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models import Base, engine, SessionLocal, Business  # noqa: E402
from backend.services import scheduler  # noqa: E402

CHECKS = []


def check(name, cond, detail=None):
    CHECKS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}: {name}{(' — ' + str(detail)) if (detail and not cond) else ''}")


ET = scheduler.ET


def et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# ---------- due_jobs: pure scheduling logic ----------

# Tuesday Aug 18 2026 (weekday() == 1)
tue = et(2026, 8, 18, 6, 45)
check("route_push due at exactly 6:45am Tue", scheduler.due_jobs(tue, {}) == ["route_push"])
check("route_push still due inside grace (7:15am)", scheduler.due_jobs(et(2026, 8, 18, 7, 15), {}) == ["route_push"])
check("route_push NOT due past grace (7:46am)", scheduler.due_jobs(et(2026, 8, 18, 7, 46), {}) == [])
check("route_push NOT due before schedule (6:44am)", scheduler.due_jobs(et(2026, 8, 18, 6, 44), {}) == [])

check("nightly_summary due at 7:00pm", scheduler.due_jobs(et(2026, 8, 18, 19, 0), {}) == ["nightly_summary"])
check("nightly_summary due at end of grace (9:00pm)", scheduler.due_jobs(et(2026, 8, 18, 21, 0), {}) == ["nightly_summary"])
check("nightly_summary NOT due past grace (9:01pm)", scheduler.due_jobs(et(2026, 8, 18, 21, 1), {}) == [])

# Saturday Aug 22 2026
sat = et(2026, 8, 22, 6, 45)
check("nothing fires Saturday", scheduler.due_jobs(sat, {}) == [])
check("nothing fires Saturday evening", scheduler.due_jobs(et(2026, 8, 22, 19, 0), {}) == [])

# same-day refire blocked
fired = {"route_push": tue.date()}
check("no refire same day", scheduler.due_jobs(tue, fired) == [])
check("fires again next day", scheduler.due_jobs(et(2026, 8, 19, 6, 45), fired) == ["route_push"])

# both jobs due at once never happens (times differ), but independent tracking works
both_fired = {"route_push": tue.date(), "nightly_summary": tue.date()}
check("independent last-fired tracking", scheduler.due_jobs(et(2026, 8, 19, 19, 0), both_fired) == ["nightly_summary"])


# ---------- start_scheduler gating ----------

async def t_gating():
    os.environ.pop("FIELDNOTES_SCHEDULER_ENABLED", None)
    check("disabled by default returns None", scheduler.start_scheduler() is None)
    os.environ["FIELDNOTES_SCHEDULER_ENABLED"] = "yes"
    check("'yes' does not enable (must be '1')", scheduler.start_scheduler() is None)
    os.environ["FIELDNOTES_SCHEDULER_ENABLED"] = "1"
    os.environ["FIELDNOTES_SCHEDULER_TICK_SECONDS"] = "3600"
    task = scheduler.start_scheduler()
    check("enabled returns a task", task is not None and not task.done())
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    os.environ.pop("FIELDNOTES_SCHEDULER_ENABLED", None)
    os.environ.pop("FIELDNOTES_SCHEDULER_TICK_SECONDS", None)

asyncio.run(t_gating())

check("status disabled shape", scheduler.scheduler_status() == {"enabled": False, "last_fired": {}})


# ---------- fire_job plumbing (temp DB) ----------

Base.metadata.create_all(engine)
db = SessionLocal()
biz = Business(name="Sched Test Co", slug="sched-test", owner_email="owner@example.com",
               owner_name="Test Owner", is_active=True, tier="crew",
               owner_telegram_id="999999")  # fake id; token empty so sends no-op-fail
db.add(biz)
db.commit()
db.close()

alerts = []


async def fake_alert(text):
    alerts.append(text)


async def t_fire():
    orig_alert = scheduler._alert
    scheduler._alert = fake_alert
    try:
        # route_push: business has no route entries today -> skipped, still ok
        r = await scheduler.fire_job("route_push")
        check("fire_job route_push ok", r.get("ok") is True, r)
        check("route_push reported the tenant (skipped, no stops)",
              any(s.get("business") == "Sched Test Co" for s in r.get("skipped", [])), r)

        # nightly_summary: Telegram send no-op-fails (empty token) -> failed list -> alert
        r = await scheduler.fire_job("nightly_summary")
        check("fire_job nightly_summary ok envelope", r.get("ok") is True, r)
        check("nightly_summary partial failure surfaced", len(r.get("failed", [])) == 1, r)
        check("partial failure alerted founder", any("partial failure" in a for a in alerts), alerts)

        # exception path: bad secret -> 403 HTTPException -> alert + ok False
        alerts.clear()
        os.environ["FIELDNOTES_CRON_SECRET"] = ""
        r = await scheduler.fire_job("route_push")
        check("exception path returns ok False", r.get("ok") is False, r)
        check("exception alerted founder", any("FAILED" in a for a in alerts), alerts)
        os.environ["FIELDNOTES_CRON_SECRET"] = "test-secret"

        # unknown job
        r = await scheduler.fire_job("bogus")
        check("unknown job -> ok False", r.get("ok") is False)
    finally:
        scheduler._alert = orig_alert

asyncio.run(t_fire())


# ---------- summary ----------

failed = [n for n, ok, _ in CHECKS if not ok]
print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
if failed:
    print("FAILURES:", failed)
    sys.exit(1)
print("ALL GREEN")
