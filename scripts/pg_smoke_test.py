#!/usr/bin/env python3
"""
INFRA Task 1 — Postgres smoke test.

Spins up a throwaway LOCAL Postgres (via pgserver — no docker, no sudo),
points the FieldNotes backend at it via DATABASE_URL, and verifies:
  1. App boots against PG (init_db creates all expected tables)
  2. One row can be inserted + read back in EVERY table
  3. /health returns 200 with db=ok against PG

Never touches the production SQLite file or any remote DB.
Usage: python3 scripts/pg_smoke_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TABLES = {
    "businesses", "accounts", "workers", "service_logs",
    "actions", "route_entries", "pending_subscriptions", "qa_events",
}

# Child process code: runs with DATABASE_URL pointed at the temp PG.
# Exercises models.py end-to-end: engine creation, init_db, insert+read each table.
CHILD_CODE = r"""
import os, sys, json
sys.path.insert(0, os.environ["FN_REPO_ROOT"])
from backend.models import (
    engine, SessionLocal, init_db, Base,
    Business, Account, Worker, ServiceLog, Action,
    RouteEntry, PendingSubscription, QaEvent,
)
from sqlalchemy import inspect, text

init_db()
tables = set(inspect(engine).get_table_names())
expected = set(json.loads(os.environ["FN_EXPECTED_TABLES"]))
missing = expected - tables
if missing:
    print(json.dumps({"ok": False, "error": f"missing tables: {sorted(missing)}", "tables": sorted(tables)}))
    sys.exit(1)

db = SessionLocal()
try:
    biz = Business(name="PG Smoke Co", slug="pg-smoke", owner_email="smoke@test.dev",
                   owner_name="Smoke", tier="crew", is_active=True)
    db.add(biz); db.commit(); db.refresh(biz)

    acc = Account(business_id=biz.id, name="Smoke Account", shorthand="smoke",
                  gate_code="1234#", access_notes="test", schedule="Mon",
                  schedule_parsed='{"days":["monday"]}', is_active=True)
    db.add(acc); db.commit(); db.refresh(acc)

    worker = Worker(business_id=biz.id, name="Smoke Worker", telegram_id="pg_smoke_tg_1", is_active=True)
    db.add(worker); db.commit(); db.refresh(worker)

    log = ServiceLog(business_id=biz.id, account_id=acc.id, worker_id=worker.id,
                     raw_note="smoke note", parsed_status="all good", processing_time_ms=1)
    db.add(log); db.commit(); db.refresh(log)

    action = Action(business_id=biz.id, service_log_id=log.id, account_id=acc.id,
                    description="smoke action", priority="this_week", status="pending",
                    source="service_log")
    db.add(action); db.commit()

    route = RouteEntry(business_id=biz.id, account_id=acc.id, day_of_week="monday",
                       week_type="weekly", route_order=1, is_active=True)
    db.add(route); db.commit()

    pending = PendingSubscription(email="smoke@test.dev", plan="crew",
                                  stripe_customer_id="cus_smoke", stripe_subscription_id="sub_smoke")
    db.add(pending); db.commit()

    qa = QaEvent(business_id=biz.id, worker_id=worker.id, question="gate code?",
                 answer="1234#", sources='["account"]')
    db.add(qa); db.commit()

    # Read-back verification
    checks = {
        "businesses": db.query(Business).filter_by(slug="pg-smoke").count(),
        "accounts": db.query(Account).filter_by(business_id=biz.id).count(),
        "workers": db.query(Worker).filter_by(telegram_id="pg_smoke_tg_1").count(),
        "service_logs": db.query(ServiceLog).filter_by(business_id=biz.id).count(),
        "actions": db.query(Action).filter_by(business_id=biz.id).count(),
        "route_entries": db.query(RouteEntry).filter_by(business_id=biz.id).count(),
        "pending_subscriptions": db.query(PendingSubscription).filter_by(email="smoke@test.dev").count(),
        "qa_events": db.query(QaEvent).filter_by(business_id=biz.id).count(),
    }
    # Tenant isolation sanity: a second business must see zero of the above
    other = Business(name="Other Co", slug="other-co", owner_email="other@test.dev",
                     owner_name="Other", is_active=True)
    db.add(other); db.commit(); db.refresh(other)
    iso = db.query(ServiceLog).filter_by(business_id=other.id).count()

    dialect = engine.dialect.name
    dbver = db.execute(text("SELECT version()")).scalar()
    if not all(v == 1 for v in checks.values()) or iso != 0:
        print(json.dumps({"ok": False, "error": "row verification failed",
                          "checks": checks, "isolation_count": iso}))
        sys.exit(1)
    print(json.dumps({"ok": True, "dialect": dialect, "pg_version": dbver.split(",")[0],
                      "checks": checks, "isolation_count": iso, "tables": sorted(tables)}))
finally:
    db.close()
"""


def main():
    import pgserver

    pgdata = tempfile.mkdtemp(prefix="fn_pg_smoke_")
    print(f"[smoke] starting throwaway postgres at {pgdata} ...")
    srv = pgserver.get_server(pgdata)
    uri = srv.get_uri()
    print(f"[smoke] pg up: {uri}")

    # pgserver URIs may use a unix-socket host dir; psycopg2 handles it, but
    # SQLAlchemy needs the host as a URL-encoded query param for socket dirs.
    from sqlalchemy.engine import make_url
    url = make_url(uri)
    if url.host and url.host.startswith("/"):
        url = url.set(host=None, query={**url.query, "host": url.host})
    pg_url = url.render_as_string(hide_password=False)

    env = dict(os.environ)
    env["DATABASE_URL"] = pg_url
    env["FN_REPO_ROOT"] = str(REPO_ROOT)
    env["FN_EXPECTED_TABLES"] = json.dumps(sorted(EXPECTED_TABLES))
    # Isolate from any real .env influence
    env.pop("FIELDNOTES_LLM_PROVIDER", None)

    print("[smoke] booting backend against PG (subprocess) ...")
    proc = subprocess.run(
        [sys.executable, "-c", CHILD_CODE],
        env=env, capture_output=True, text=True, timeout=180,
        cwd=str(REPO_ROOT),
    )
    out = proc.stdout.strip().splitlines()
    result = None
    for line in out:
        if line.startswith("{"):
            result = json.loads(line)
    if proc.returncode != 0 or not result or not result.get("ok"):
        print("[smoke] FAILED")
        print(proc.stdout[-3000:])
        print(proc.stderr[-3000:])
        sys.exit(1)

    print(f"[smoke] dialect={result['dialect']}  server={result['pg_version']}")
    print(f"[smoke] tables created: {len(result['tables'])} -> {', '.join(result['tables'])}")
    print(f"[smoke] insert+read 1 row/table: {result['checks']}")
    print(f"[smoke] tenant isolation check (other biz sees 0 logs): {result['isolation_count']}")

    # /health against PG: boot uvicorn on a random port, hit /health
    import socket, urllib.request
    sock = socket.socket(); sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]; sock.close()
    print(f"[smoke] booting uvicorn on :{port} for /health check ...")
    srvlog = tempfile.NamedTemporaryFile(prefix="fn_smoke_uvicorn_", suffix=".log", delete=False)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=str(REPO_ROOT),
        stdout=srvlog, stderr=subprocess.STDOUT,
    )
    try:
        health = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                    health = json.loads(r.read())
                break
            except Exception:
                time.sleep(0.5)
        if not health or health.get("db") != "ok":
            print(f"[smoke] FAILED — /health did not report db=ok: {health}")
            srvlog.flush()
            print(open(srvlog.name).read()[-2500:])
            sys.exit(1)
        print(f"[smoke] /health against PG: {health}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    print("\n✅ PG SMOKE TEST PASSED — backend is fully Postgres-compatible")


if __name__ == "__main__":
    main()
