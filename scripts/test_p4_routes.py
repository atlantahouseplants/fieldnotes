#!/usr/bin/env python3
"""
P4 route-awareness E2E. Runs against a LOCAL server (port 8768) on a temp DB.

Tests:
  1. "route today" → deterministic route answer (no LLM), correct accounts
  2. "route tomorrow" → tomorrow's accounts
  3. "did we skip anyone this week?" → missed list (past days only)
  4. Log a stop → it flips to ✅ done in route answer
  5. Note containing the word "route" is NOT stolen by Q&A
  6. /summary/route-push: secret-gated, sends per-worker + owner
  7. Tenant isolation: routes never cross tenants
"""
import json, os, sqlite3, subprocess, sys, time
from datetime import date, timedelta
import httpx

# Pin both this script and the server subprocess to UTC: log timestamps are
# datetime.utcnow() while route lookups use date.today(). On a non-UTC box the
# two diverge between 8pm-midnight local and the "logged stop flips" check
# fails spuriously. Prod (Railway) runs TZ=UTC, so this mirrors prod.
os.environ["TZ"] = "UTC"
time.tzset()

BASE = "http://127.0.0.1:8768"
DB_PATH = "/tmp/p4_route_test.db"
SECRET_HDR = {"x-telegram-bot-api-secret-token": None}
CRON = "test-cron-secret"
failures = []

def check(name, cond, detail=""):
    print(("✅" if cond else "❌"), name, detail)
    if not cond:
        failures.append(name)

def tg_update(chat_id, text, upd_id):
    return {"update_id": upd_id,
            "message": {"message_id": upd_id, "from": {"id": chat_id, "first_name": "T"},
                        "chat": {"id": chat_id}, "text": text}}

def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    secret = None
    for line in open("/home/wallg/fieldnotes/.env"):
        if line.startswith("TELEGRAM_SECRET="):
            secret = line.split("=", 1)[1].strip()
    SECRET_HDR["x-telegram-bot-api-secret-token"] = secret

    env = dict(os.environ, DATABASE_URL=f"sqlite:///{DB_PATH}", FIELDNOTES_CRON_SECRET=CRON, TZ="UTC")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8768"],
        cwd="/home/wallg/fieldnotes", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                httpx.get(f"{BASE}/health", timeout=2); break
            except Exception:
                time.sleep(0.5)

        # Seed tenant: business + worker + scheduled accounts, directly in DB
        db = sqlite3.connect(DB_PATH)
        db.execute("INSERT INTO businesses (name, slug, owner_email, owner_name, owner_telegram_id, dashboard_key, invite_token, subscription_status, tier, beta_all_access, is_active, created_at) "
                   "VALUES ('Route Co','route-co','r@r.com','Rick','999001','k','it','active','crew',0,1,datetime('now'))")
        db.execute("INSERT INTO workers (business_id, name, telegram_id, is_active, created_at) "
                   "VALUES (1,'Rick','999001',1,datetime('now'))")
        today = date.today()
        dow = today.strftime("%A").lower()
        tmr = (today + timedelta(days=1)).strftime("%A").lower()
        yest = (today - timedelta(days=1)).strftime("%A").lower()
        accts = [("Today Stop A", today), ("Today Stop B", today), ("Tomorrow Stop", today + timedelta(days=1)),
                 ("Yesterday Miss", today - timedelta(days=1))]
        for i, (nm, d) in enumerate(accts, 1):
            db.execute("INSERT INTO accounts (business_id, name, is_active, created_at) VALUES (1,?,1,datetime('now'))", (nm,))
        db.execute("INSERT INTO route_entries (business_id, account_id, day_of_week, week_type, is_active) VALUES (1,1,?,'weekly',1)", (dow,))
        db.execute("INSERT INTO route_entries (business_id, account_id, day_of_week, week_type, is_active) VALUES (1,2,?,'weekly',1)", (dow,))
        db.execute("INSERT INTO route_entries (business_id, account_id, day_of_week, week_type, is_active) VALUES (1,3,?,'weekly',1)", (tmr,))
        db.execute("INSERT INTO route_entries (business_id, account_id, day_of_week, week_type, is_active) VALUES (1,4,?,'weekly',1)", (yest,))
        db.commit(); db.close()

        uid = 1000
        def ask(text):
            nonlocal uid
            uid += 1
            r = httpx.post(f"{BASE}/webhook/telegram", json=tg_update(999001, text, uid),
                           headers=SECRET_HDR, timeout=60)
            return r.json()

        # 1. route today
        d = ask("route today")
        check("route today answered", "Today Stop A" in d.get("answer", "") and "Today Stop B" in d.get("answer", ""), d.get("answer", "")[:120])
        check("route today excludes tomorrow", "Tomorrow Stop" not in d.get("answer", ""))
        check("route shows ⬜ pending", "⬜" in d.get("answer", ""))

        # 2. route tomorrow
        d = ask("what's my route tomorrow?")
        check("route tomorrow", "Tomorrow Stop" in d.get("answer", ""), d.get("answer", "")[:120])
        check("route tomorrow excludes today", "Today Stop A" not in d.get("answer", ""))

        # 3. missed this week
        d = ask("did we skip anyone this week?")
        check("missed lists Yesterday Miss", "Yesterday Miss" in d.get("answer", ""), d.get("answer", "")[:150])
        check("missed excludes today (pending)", "Today Stop A" not in d.get("answer", ""))

        # 4. log a stop → done
        d = ask("Today Stop A: all good, filters changed")
        check("log note still logs", d.get("account") is not None or d.get("ok"), str(d)[:100])
        d = ask("route today")
        check("logged stop flips to ✅", "✅" in d.get("answer", "") and "1/2 logged" in d.get("answer", ""), d.get("answer", "")[:200])

        # 5. note with word 'route' NOT stolen
        d = ask("Today Stop B: delivery van blocking route to loading dock, noted")
        check("'route' in note not stolen", "account" in d or "summary" in d, str(d)[:120])

        # 6. route-push endpoint
        r = httpx.post(f"{BASE}/summary/route-push?secret=wrong", timeout=30)
        check("route-push bad secret → 403", r.status_code == 403)
        r = httpx.post(f"{BASE}/summary/route-push?secret={CRON}", timeout=60)
        d = r.json()
        check("route-push sends to business", any(s["business"] == "Route Co" for s in d["sent"]), str(d)[:200])
        check("route-push 2 stops", d["sent"][0]["stops"] == 2, str(d["sent"]))

        # 7. tenant isolation
        db = sqlite3.connect(DB_PATH)
        db.execute("INSERT INTO businesses (name, slug, owner_email, owner_name, dashboard_key, invite_token, subscription_status, tier, beta_all_access, is_active, created_at) "
                   "VALUES ('Other Co','other-co','o@o.com','Olive','k2','it2','active','crew',0,1,datetime('now'))")
        db.execute("INSERT INTO workers (business_id, name, telegram_id, is_active, created_at) "
                   "VALUES (2,'Olive','999002',1,datetime('now'))")
        db.commit(); db.close()
        d = ask.__wrapped__ if hasattr(ask, "__wrapped__") else None
        r = httpx.post(f"{BASE}/webhook/telegram", json=tg_update(999002, "route today", 2001),
                       headers=SECRET_HDR, timeout=60)
        d2 = r.json()
        check("other tenant sees no Route Co stops", "Today Stop" not in d2.get("answer", ""), d2.get("answer", "")[:100])
    finally:
        proc.terminate(); proc.wait(timeout=10)

    print()
    if failures:
        print("FAILURES:", failures); sys.exit(1)
    print("🎉 ALL P4 ROUTE TESTS PASS")

if __name__ == "__main__":
    main()
