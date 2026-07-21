#!/usr/bin/env python3
"""
INFRA Task 2 — Alembic verification suite.

Against a fresh throwaway LOCAL Postgres (pgserver, no docker/sudo):
  1. `alembic upgrade head`   → all 8 app tables + alembic_version exist
  2. `alembic downgrade base` → all app tables dropped
  3. `alembic upgrade head`   → idempotent re-apply works
  4. `alembic check`          → no schema drift between models.py and migrations
  5. App boots on the migrated PG and /health reports db=ok

Never touches prod SQLite or any remote DB.
Usage: python3 scripts/test_alembic.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {
    "businesses", "accounts", "workers", "service_logs",
    "actions", "route_entries", "pending_subscriptions", "qa_events",
}

failures = []


def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def alembic(env, *args):
    r = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print(r.stdout[-1500:])
        print(r.stderr[-1500:], file=sys.stderr)
    return r.returncode == 0, r.stdout + r.stderr


def pg_tables(env):
    code = (
        "import os;from sqlalchemy import create_engine,inspect;"
        "print(','.join(sorted(inspect(create_engine(os.environ['DATABASE_URL'])).get_table_names())))"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    return set(filter(None, r.stdout.strip().split(",")))


def main():
    import pgserver

    pgdata = tempfile.mkdtemp(prefix="fn_pg_alembic_test_")
    print(f"[alembic-test] fresh PG at {pgdata}")
    srv = pgserver.get_server(pgdata)

    env = dict(os.environ, DATABASE_URL=srv.get_uri())
    env.pop("FIELDNOTES_LLM_PROVIDER", None)

    # 1. upgrade head
    ok, _ = alembic(env, "upgrade", "head")
    tables = pg_tables(env)
    check("upgrade head exit 0", ok)
    check("upgrade head creates all 8 tables", EXPECTED <= tables, f"got {sorted(tables)}")
    check("alembic_version present", "alembic_version" in tables)

    # 2. downgrade base
    ok, _ = alembic(env, "downgrade", "base")
    tables = pg_tables(env)
    check("downgrade base exit 0", ok)
    check("downgrade base drops all app tables", not (EXPECTED & tables), f"remaining {sorted(tables & EXPECTED)}")

    # 3. re-upgrade
    ok, _ = alembic(env, "upgrade", "head")
    tables = pg_tables(env)
    check("re-upgrade head exit 0", ok)
    check("re-upgrade restores all 8 tables", EXPECTED <= tables)

    # 4. alembic check (schema drift)
    ok, out = alembic(env, "check")
    check("alembic check — no drift between models and migrations", ok)

    # 5. boot app on migrated PG, hit /health
    sock = socket.socket(); sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]; sock.close()
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
        check("app boots on migrated PG, /health db=ok",
              bool(health and health.get("db") == "ok"), str(health))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    srv.cleanup() if hasattr(srv, "cleanup") else None
    shutil.rmtree(pgdata, ignore_errors=True)

    if failures:
        print(f"\n❌ {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("\n🎉 ALL ALEMBIC TESTS PASS")


if __name__ == "__main__":
    main()
