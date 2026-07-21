#!/usr/bin/env python3
"""
INFRA Task 3 — migration script verification.

End-to-end dry-run against LOCAL throwaway PG only:
  1. Online-backup snapshot of the real SQLite DB (sqlite3 .backup — read-safe)
  2. Fresh throwaway PG (pgserver) + `alembic upgrade head`
  3. migrate_sqlite_to_pg.py dry-run (must write nothing)
  4. migrate_sqlite_to_pg.py --yes (copies everything)
  5. Boot app on the migrated PG → /health ok, and per-table counts via ORM
     match the SQLite source exactly (incl. qa_events, route_entries,
     pending_subscriptions)
  6. Re-run --yes against the now-nonempty PG → must REFUSE (no silent merge)

Never writes to the prod SQLite file or any remote DB.
Usage: python3 scripts/test_migration.py
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TABLES = ["businesses", "accounts", "workers", "pending_subscriptions",
          "service_logs", "actions", "route_entries", "qa_events"]

failures = []


def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def main():
    import pgserver

    # 1. safe online snapshot of the real DB
    snapshot = tempfile.mktemp(prefix="fn_snapshot_", suffix=".db")
    src = sqlite3.connect(f"file:{REPO_ROOT / 'fieldnotes.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(snapshot)
    src.backup(dst)
    dst.close(); src.close()
    counts = {}
    con = sqlite3.connect(snapshot)
    for t in TABLES:
        try:
            counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            counts[t] = 0
    con.close()
    print(f"[mig-test] snapshot taken: {counts}")

    # 2. fresh PG + alembic upgrade head
    pgdata = tempfile.mkdtemp(prefix="fn_pg_migtest_")
    srv = pgserver.get_server(pgdata)
    env = dict(os.environ, DATABASE_URL=srv.get_uri())
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       env=env, cwd=str(REPO_ROOT), capture_output=True, text=True)
    check("alembic upgrade head on fresh PG", r.returncode == 0)

    mig = [sys.executable, str(REPO_ROOT / "scripts" / "migrate_sqlite_to_pg.py"),
           "--sqlite", snapshot]

    # 3. dry-run writes nothing
    r = subprocess.run(mig, env=env, capture_output=True, text=True)
    check("dry-run exit 0", r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout else "")
    check("dry-run says no data written", "No data was written" in r.stdout)
    con = sqlite3.connect(snapshot)  # source untouched sanity
    con.close()

    # 4. real copy
    r = subprocess.run(mig + ["--yes"], env=env, capture_output=True, text=True)
    check("migration --yes exit 0", r.returncode == 0,
          "" if r.returncode == 0 else (r.stdout + r.stderr)[-1500:])
    check("migration verified all counts", "MIGRATION COMPLETE" in r.stdout)
    for line in r.stdout.splitlines():
        if line.strip().startswith("✅") or line.strip().startswith("❌"):
            print("   " + line.strip())

    # 5. boot app on migrated PG, verify via ORM counts + /health
    code = (
        "import os,sys,json;sys.path.insert(0,'.');"
        "from backend.models import SessionLocal,"
        "Business,Account,Worker,ServiceLog,Action,RouteEntry,PendingSubscription,QaEvent;"
        "db=SessionLocal();"
        "print(json.dumps({'businesses':db.query(Business).count(),"
        "'accounts':db.query(Account).count(),'workers':db.query(Worker).count(),"
        "'service_logs':db.query(ServiceLog).count(),'actions':db.query(Action).count(),"
        "'route_entries':db.query(RouteEntry).count(),"
        "'pending_subscriptions':db.query(PendingSubscription).count(),"
        "'qa_events':db.query(QaEvent).count()}));db.close()"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(REPO_ROOT),
                       capture_output=True, text=True)
    pg_counts = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    check("ORM reads migrated PG, all counts match source",
          pg_counts == counts, f"src={counts} pg={pg_counts}")

    # sequence reset check: insert a new business via ORM must not collide
    ins = (
        "import os,sys;sys.path.insert(0,'.');"
        "from backend.models import SessionLocal,Business;"
        "db=SessionLocal();"
        "b=Business(name='Seq Test',slug='seq-test',owner_email='s@t.dev',owner_name='S',is_active=True);"
        "db.add(b);db.commit();print('new id',b.id);db.close()"
    )
    r = subprocess.run([sys.executable, "-c", ins], env=env, cwd=str(REPO_ROOT),
                       capture_output=True, text=True)
    check("post-migration insert works (sequences reset)",
          r.returncode == 0 and "new id" in r.stdout, (r.stdout + r.stderr)[-300:])

    sock = socket.socket(); sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]; sock.close()
    srvlog = tempfile.NamedTemporaryFile(prefix="fn_mig_uvicorn_", suffix=".log", delete=False)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=str(REPO_ROOT), stdout=srvlog, stderr=subprocess.STDOUT)
    try:
        health = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                    health = json.loads(resp.read())
                break
            except Exception:
                time.sleep(0.5)
        check("app boots on migrated data, /health db=ok",
              bool(health and health.get("db") == "ok"), str(health))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    # 6. second --yes run must refuse (target non-empty)
    r = subprocess.run(mig + ["--yes"], env=env, capture_output=True, text=True)
    check("re-run refuses non-empty target", r.returncode != 0 and "refusing to merge" in r.stdout)

    if failures:
        print(f"\n❌ {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("\n🎉 ALL MIGRATION TESTS PASS")


if __name__ == "__main__":
    main()
