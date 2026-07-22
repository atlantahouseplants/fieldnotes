#!/usr/bin/env python3
"""
P5 feature-gate E2E. Runs against a LOCAL server (port 8769) on a temp DB.

Tests:
  1. Solo (no beta) worker asks a question → upgrade prompt, NO tenant data leaked
  2. Solo gated attempt recorded in qa_events ([GATED:qa])
  3. Team worker asks a question → real answer (gate code returned)
  4. Team worker asks route question → gated (Crew required)
  5. Crew worker asks route question → deterministic route answer
  6. Solo + beta_all_access → EVERYTHING passes (qa + routes)
  7. /accounts/usage: 403 without key; 200 with key; counts + features correct
  8. /onboarding/import-csv: Solo → 402 with upgrade detail; Team → import ok
  9. /summary/route-push: gated biz skipped with reason
 10. Normal log notes still log for a Solo tenant (v1 product ungated)
"""
import json, os, sqlite3, subprocess, sys, time
from datetime import date
import httpx

BASE = "http://127.0.0.1:8769"
DB_PATH = "/tmp/p5_gate_test.db"
SECRET_HDR = {"x-telegram-bot-api-secret-token": None}
CRON = "test-cron-secret"
failures = []

def check(name, cond, detail=""):
    print(("✅" if cond else "❌"), name, str(detail)[:140])
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

    env = dict(os.environ, DATABASE_URL=f"sqlite:///{DB_PATH}", FIELDNOTES_CRON_SECRET=CRON)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8769"],
        cwd="/home/wallg/fieldnotes", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                httpx.get(f"{BASE}/health", timeout=2); break
            except Exception:
                time.sleep(0.5)

        # Seed 4 tenants: (id, name, tier, beta)
        # 1 Solo no-beta, 2 Team no-beta, 3 Crew no-beta, 4 Solo WITH beta
        db = sqlite3.connect(DB_PATH)
        tenants = [(1, "Solo Co", "solo", 0), (2, "Team Co", "team", 0),
                   (3, "Crew Co", "crew", 0), (4, "Beta Co", "solo", 1)]
        for i, nm, tier, beta in tenants:
            tg = f"99910{i}"
            db.execute("INSERT INTO businesses (name, slug, owner_email, owner_name, dashboard_key, invite_token, subscription_status, tier, beta_all_access, is_active, created_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,1,datetime('now'))",
                       (nm, nm.lower().replace(" ", "-"), f"o{i}@x.com", "Owner", f"k{i}", f"it{i}", "active", tier, beta))
            db.execute("INSERT INTO workers (business_id, name, telegram_id, is_active, created_at) "
                       "VALUES (?,?,?,1,datetime('now'))", (i, f"W{i}", tg))
            db.execute("INSERT INTO accounts (business_id, name, gate_code, is_active, created_at) "
                       f"VALUES ({i},'Alpha Site','SECRET-CODE-{i}',1,datetime('now'))")
            db.execute("INSERT INTO route_entries (business_id, account_id, day_of_week, week_type, is_active) "
                       f"VALUES ({i},{i},?,'weekly',1)", (date.today().strftime("%A").lower(),))
        db.commit(); db.close()

        uid = 2000
        def ask(chat_id, text):
            nonlocal uid
            uid += 1
            r = httpx.post(f"{BASE}/webhook/telegram", json=tg_update(chat_id, text, uid),
                           headers=SECRET_HDR, timeout=60)
            return r.json()

        # 1. Solo no-beta: question → gated, no data leak
        r = ask(999101, "what's the gate code for Alpha Site?")
        check("solo qa gated", r.get("gated") is True and r.get("feature") == "qa", r)
        check("solo gate: no secret leaked", "SECRET-CODE-1" not in r.get("answer", ""), r.get("answer", "")[:80])
        check("solo gate: upgrade link shown", "pricing.html" in r.get("answer", ""), "")

        # 2. Gated attempt recorded
        db = sqlite3.connect(DB_PATH)
        ev = db.execute("SELECT answer, sources FROM qa_events WHERE business_id=1").fetchall()
        db.close()
        check("gated attempt recorded", len(ev) == 1 and ev[0][0].startswith("[GATED:qa]"), ev)

        # 3. Team: qa passes, real answer
        r = ask(999102, "what's the gate code for Alpha Site?")
        check("team qa answered", not r.get("gated") and "SECRET-CODE-2" in r.get("answer", ""), r.get("answer", "")[:100])

        # 4. Team: route question → gated (crew)
        r = ask(999102, "route today")
        check("team routes gated", r.get("gated") is True and r.get("feature") == "routes", r)

        # 5. Crew: route question → route answer
        r = ask(999103, "route today")
        check("crew route answered", not r.get("gated") and "Alpha Site" in r.get("answer", ""), r.get("answer", "")[:100])

        # 6. Beta solo: qa AND routes pass
        r = ask(999104, "what's the gate code for Alpha Site?")
        check("beta qa passes", not r.get("gated") and "SECRET-CODE-4" in r.get("answer", ""), r.get("answer", "")[:100])
        r = ask(999104, "route today")
        check("beta routes pass", not r.get("gated") and "Alpha Site" in r.get("answer", ""), r.get("answer", "")[:100])

        # 7. Usage endpoint
        r = httpx.get(f"{BASE}/accounts/usage?business_id=1", timeout=10)
        check("usage 403 without key", r.status_code == 403, r.status_code)
        r = httpx.get(f"{BASE}/accounts/usage?business_id=1&key=k1", timeout=10).json()
        check("usage counts", r["usage"]["gated_attempts_this_month"] == 1 and r["usage"]["workers"] == 1, r)
        check("usage features solo", r["features"] == {"qa": False, "csv_import": False, "routes": False, "sms": False, "morning_push": False, "recaps": False}, r["features"])
        r4 = httpx.get(f"{BASE}/accounts/usage?business_id=4&key=k4", timeout=10).json()
        check("usage beta features all true", all(r4["features"].values()) and r4["beta_all_access"] is True, r4["features"])

        # 8. import-csv gate
        csv_body = "name,gate_code\nNew Site,9999\n"
        r = httpx.post(f"{BASE}/onboarding/import-csv",
                       json={"business_id": 1, "key": "k1", "csv_text": csv_body}, timeout=30)
        check("solo import 402", r.status_code == 402, r.status_code)
        d = r.json().get("detail", {})
        check("402 has upgrade detail", d.get("error") == "feature_gated" and d.get("required_tier") == "team", d)
        r = httpx.post(f"{BASE}/onboarding/import-csv",
                       json={"business_id": 2, "key": "k2", "csv_text": csv_body}, timeout=60)
        check("team import ok", r.status_code == 200 and r.json().get("created") == 1, r.status_code)

        # 9. route-push: solo+team gated, crew passes
        r = httpx.post(f"{BASE}/summary/route-push?secret={CRON}", timeout=30).json()
        skipped_gated = {s["business"] for s in r["skipped"] if "gated" in s.get("reason", "")}
        check("route-push gates solo+team", {"Solo Co", "Team Co"} <= skipped_gated, r["skipped"])
        check("route-push sends crew", any(s["business"] == "Crew Co" for s in r["sent"]), r["sent"])

        # 10. Solo tenant: plain log note still logs (v1 ungated)
        r = ask(999101, "Alpha Site: all good, filters replaced")
        check("solo logging ungated", r.get("intent") != "question" and r.get("status") is not None, r)

        print()
        if failures:
            print("❌ FAILURES:", failures); sys.exit(1)
        print("🎉 ALL P5 GATE TESTS PASS")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    main()
