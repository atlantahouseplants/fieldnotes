#!/usr/bin/env python3
"""
INFRA Task 5 — /health hardening tests.

  1. Real app boot (temp sqlite) → /health = 200 {status:healthy, db:ok}
  2. DB failure injected → /health = 503 {status:unhealthy, db:down}
  3. Against live PG (throwaway pgserver, alembic-migrated) → 200 db:ok

Usage: python3 scripts/test_health.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
failures = []


def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


CHILD = r"""
import json, os, sys
sys.path.insert(0, os.environ["FN_REPO_ROOT"])
from fastapi.testclient import TestClient
from backend.main import app
import backend.main as m

out = {}
with TestClient(app) as client:
    r = client.get("/health")
    out["good"] = {"code": r.status_code, "body": r.json()}

    # Inject DB failure: SessionLocal raises
    class Boom:
        def __call__(self, *a, **k):
            raise RuntimeError("db connection refused (injected)")
    orig = m.SessionLocal
    m.SessionLocal = Boom()
    try:
        r = client.get("/health")
        out["bad"] = {"code": r.status_code, "body": r.json()}
    finally:
        m.SessionLocal = orig

    # Recovery after failure
    r = client.get("/health")
    out["recovered"] = {"code": r.status_code, "body": r.json()}

print(json.dumps(out))
"""


def run_child(env):
    env = dict(env, FN_REPO_ROOT=str(REPO_ROOT))
    r = subprocess.run([sys.executable, "-c", CHILD], env=env, cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print(r.stdout[-1500:]); print(r.stderr[-1500:], file=sys.stderr)
        return None
    for line in reversed(r.stdout.strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return None


def main():
    # 1+2: temp sqlite
    tmpdb = tempfile.mktemp(prefix="fn_health_", suffix=".db")
    res = run_child(dict(os.environ, DATABASE_URL=f"sqlite:///{tmpdb}"))
    check("app boots + health 200/db ok (sqlite)",
          bool(res and res["good"]["code"] == 200 and res["good"]["body"]["db"] == "ok"
               and res["good"]["body"]["status"] == "healthy"), str(res and res["good"]))
    check("injected DB failure → 503 unhealthy/db down",
          bool(res and res["bad"]["code"] == 503 and res["bad"]["body"] == {"status": "unhealthy", "db": "down"}),
          str(res and res["bad"]))
    check("recovers to 200 after failure cleared",
          bool(res and res["recovered"]["code"] == 200 and res["recovered"]["body"]["db"] == "ok"))

    # 3: live PG
    import pgserver
    pgdata = tempfile.mkdtemp(prefix="fn_pg_health_")
    srv = pgserver.get_server(pgdata)
    res = run_child(dict(os.environ, DATABASE_URL=srv.get_uri()))
    check("health 200/db ok against migrated PG (startup ran alembic)",
          bool(res and res["good"]["code"] == 200 and res["good"]["body"]["db"] == "ok"),
          str(res and res["good"]))

    if failures:
        print(f"\n❌ {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("\n🎉 ALL HEALTH TESTS PASS")


if __name__ == "__main__":
    main()
