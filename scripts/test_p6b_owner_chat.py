#!/usr/bin/env python3
"""
P6b owner-chat-commands test suite. Layers:

  A) In-process unit tests — deterministic intent parsers in
     services/accounts.py (no server, no LLM).
  B) HTTP end-to-end — local uvicorn (port 8773) + temp sqlite, LLM keys
     stripped so parse_note uses the deterministic fallback. Covers the
     owner bootstrap (owner with NO worker row), "New account: …",
     "Note for X: …", "invite", tenant isolation, and demo-mode survival.

DB layout is deliberate: biz_demo is created SECOND so it lands on id=2 —
the hardcoded demo-tenant id in webhook.py — letting us prove unknown
telegrams still fall through to demo instead of being hijacked by the
owner bootstrap.

Production data is never touched. TZ pinned to UTC (mirrors Railway prod).
"""
import json, os, secrets, sqlite3, subprocess, sys, time

import httpx

BASE = "http://127.0.0.1:8773"
DB_PATH = "/tmp/p6b_owner_chat_test.db"
REPO = "/home/wallg/fieldnotes"
sys.path.insert(0, REPO)

failures = []
def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name} {detail}")
    if not cond:
        failures.append(name)

# ── A) unit tests (in-process) ────────────────────────────────────
def unit_tests():
    os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    from backend.services import accounts as A

    na = A.parse_new_account("New account: Smith, 121 Main St, gate 4412, Tue/Fri")
    check("U new_account full parse",
          na == {"name": "Smith", "address": "121 Main St",
                 "gate_code": "4412", "schedule": "Tue/Fri"}, str(na))
    na = A.parse_new_account("Add customer: Delta Hotel")
    check("U new_account name-only",
          na == {"name": "Delta Hotel", "address": None,
                 "gate_code": None, "schedule": None}, str(na))
    na = A.parse_new_account("New account: X, 1 Main St, Tue/Fri, gate 9")
    check("U new_account swapped gate/schedule",
          na is not None and na["gate_code"] == "9" and na["schedule"] == "Tue/Fri", str(na))
    yc = A.parse_new_account("Add account: Y Corp")
    check("U new_account 'add account:'", yc is not None and yc["name"] == "Y Corp")
    check("U new_account non-intent passes", A.parse_new_account("Smith Office: all good") is None)
    check("U new_account empty body passes", A.parse_new_account("New account:") is None)

    nf = A.parse_note_for("Note for Smith Office: gate code changed to 5521")
    check("U note_for parse", nf == ("Smith Office", "gate code changed to 5521"), str(nf))
    nf = A.parse_note_for("log on Smith Tower: compressor rattling")
    check("U note_for 'log on' variant", nf == ("Smith Tower", "compressor rattling"), str(nf))
    check("U note_for non-intent passes", A.parse_note_for("Smith: x") is None)
    check("U note_for empty body passes", A.parse_note_for("Note for Smith:") is None)
    check("U note_for empty account passes", A.parse_note_for("Note for: something") is None)

    check("U invite bare", A.invite_intent("invite"))
    check("U invite worker", A.invite_intent("Invite worker"))
    check("U invite add-a-tech", A.invite_intent("add a tech"))
    check("U invite add-new-guy", A.invite_intent("Add new guy"))
    check("U invite sentence passes", not A.invite_intent("invite him over for lunch"))
    check("U invite doesn't eat add-customer", not A.invite_intent("add customer: X"))


# ── B) HTTP end-to-end ────────────────────────────────────────────
SECRET_HDR = {}
def tg_update(chat_id, text):
    return {"update_id": 1, "message": {"message_id": 1,
            "chat": {"id": chat_id, "first_name": "T"}, "text": text,
            "from": {"id": chat_id}}}

def make_business(name, owner_tg=None):
    db = sqlite3.connect(DB_PATH)
    key = secrets.token_urlsafe(12)
    cur = db.execute(
        "INSERT INTO businesses (name, slug, owner_email, owner_name, owner_telegram_id, dashboard_key, invite_token, subscription_status, tier, beta_all_access, is_active, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,1,1, datetime('now'))",
        (name, name.lower().replace(" ", "-"), "t@t.com", "Owner", owner_tg, key,
         secrets.token_urlsafe(12), "active", "crew"))
    db.commit(); bid = cur.lastrowid; db.close()
    return bid, key

def add_worker(bid, name, tg):
    db = sqlite3.connect(DB_PATH)
    cur = db.execute("INSERT INTO workers (business_id, name, telegram_id, is_active, created_at) VALUES (?,?,?,1, datetime('now'))",
                     (bid, name, str(tg)))
    db.commit(); wid = cur.lastrowid; db.close()
    return wid

def add_account(bid, name, shorthand=None):
    db = sqlite3.connect(DB_PATH)
    cur = db.execute("INSERT INTO accounts (business_id, name, shorthand, is_active) VALUES (?,?,?,1)",
                     (bid, name, shorthand))
    db.commit(); aid = cur.lastrowid; db.close()
    return aid

def q1(sql, args=()):
    db = sqlite3.connect(DB_PATH)
    row = db.execute(sql, args).fetchone()
    db.close()
    return row

def wh(chat_id, text):
    r = httpx.post(f"{BASE}/webhook/telegram", json=tg_update(chat_id, text),
                   headers=SECRET_HDR, timeout=30)
    return r.status_code, r.json()

def http_tests():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    env = {k: v for k, v in os.environ.items()
           if k not in ("XAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                        "TELEGRAM_BOT_TOKEN", "TELEGRAM_SECRET")}
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    env["TZ"] = "UTC"  # mirror Railway prod; avoid local/UTC date flips
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8773"],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(40):
            try:
                httpx.get(f"{BASE}/health", timeout=2); up = True; break
            except Exception:
                time.sleep(0.5)
        check("server up", up)
        if not up:
            return

        # Local .env carries TELEGRAM_SECRET — the server loads it via dotenv,
        # so our requests must present the same header (mirrors P7 suite).
        secret = ""
        with open(os.path.join(REPO, ".env")) as f:
            for line in f:
                if line.startswith("TELEGRAM_SECRET="):
                    secret = line.strip().split("=", 1)[1]
        if secret:
            SECRET_HDR["x-telegram-bot-api-secret-token"] = secret

        OWNER_TG, MIKE_TG = 800099, 800001
        BETA_OWNER_TG, BETA_REP_TG = 800088, 800777
        biz_a, key_a = make_business("Tenant Alpha", owner_tg=str(OWNER_TG))
        biz_demo, _ = make_business("Demo Co")          # lands on id=2 (hardcoded demo tenant)
        biz_b, key_b = make_business("Tenant Beta", owner_tg=str(BETA_OWNER_TG))
        demo_worker = add_worker(biz_demo, "Demo Dan", 900001)
        mike_id = add_worker(biz_a, "Mike R", MIKE_TG)  # owner has NO worker row — bootstrap case
        add_worker(biz_b, "Beta Rep", BETA_REP_TG)
        add_account(biz_a, "Smith Office", "office")
        add_account(biz_a, "Smith Tower", "tower")
        add_account(biz_b, "Beta Plaza", "beta")
        check("tenant ids as expected (demo=2)", biz_demo == 2 and biz_a == 1 and biz_b == 3,
              f"a={biz_a} demo={biz_demo} b={biz_b}")

        # 1. Owner bootstrap: no worker row, plain note lands in own tenant
        code, d = wh(OWNER_TG, "Smith Office: serviced the lobby, all good")
        check("owner bootstrap note logged", code == 200 and d.get("worker") == "Owner", str(d))
        row = q1("SELECT w.name, w.telegram_id, l.business_id FROM service_logs l "
                 "JOIN workers w ON w.id = l.worker_id WHERE l.business_id=? ORDER BY l.id DESC LIMIT 1", (biz_a,))
        check("log attributed to synthetic Owner worker, tenant A",
              row is not None and row[0] == "Owner" and row[1] is None and row[2] == biz_a, str(row))

        # 2. new_account — full positional parse + schedule sync
        code, d = wh(OWNER_TG, "New account: Delta Hotel, 5 Peachtree St, gate 8899, Tue/Fri")
        check("new_account intent", d.get("intent") == "new_account" and d.get("account") == "Delta Hotel", str(d))
        new_id = d.get("account_id")
        row = q1("SELECT address, gate_code, schedule, business_id FROM accounts WHERE id=?", (new_id,))
        check("account row fields", row == ("5 Peachtree St", "8899", "Tue/Fri", biz_a), str(row))
        check("schedule_parsed stored",
              q1("SELECT schedule_parsed FROM accounts WHERE id=?", (new_id,))[0] is not None)
        routes = q1("SELECT COUNT(*) FROM route_entries WHERE account_id=? AND business_id=?", (new_id, biz_a))
        check("route entries synced (Tue+Fri)", routes and routes[0] >= 2, str(routes))

        # 3. dup guard
        n_before = q1("SELECT COUNT(*) FROM accounts WHERE business_id=?", (biz_a,))[0]
        code, d = wh(OWNER_TG, "New account: Delta Hotel")
        check("dup → plain error, no new row",
              d.get("intent") == "new_account" and d.get("error")
              and q1("SELECT COUNT(*) FROM accounts WHERE business_id=?", (biz_a,))[0] == n_before, str(d))

        # 4. rep CANNOT create accounts — text becomes a normal note
        code, d = wh(MIKE_TG, "New account: Sneaky Inc")
        check("rep new_account falls through (no intent)", d.get("intent") != "new_account", str(d))
        check("no Sneaky Inc row",
              q1("SELECT COUNT(*) FROM accounts WHERE name='Sneaky Inc'")[0] == 0)

        # 5. owner note_for
        code, d = wh(OWNER_TG, "Note for Smith Office: gate code changed to 5521")
        check("owner note_for", d.get("intent") == "note_for" and d.get("account") == "Smith Office", str(d))
        row = q1("SELECT raw_note FROM service_logs WHERE id=?", (d.get("log_id"),))
        check("note body stored (prefix stripped)",
              row and row[0] == "gate code changed to 5521", str(row))

        # 6. rep note_for
        code, d = wh(MIKE_TG, "Note for Smith Tower: compressor rattling")
        check("rep note_for", d.get("intent") == "note_for" and d.get("account") == "Smith Tower", str(d))
        row = q1("SELECT worker_id FROM service_logs WHERE id=?", (d.get("log_id"),))
        check("rep note attributed to rep", row and row[0] == mike_id, str(row))

        # 7. note_for ambiguous → asks, saves nothing
        logs_before = q1("SELECT COUNT(*) FROM service_logs")[0]
        code, d = wh(MIKE_TG, "Note for Smith: something")
        check("note_for ambiguous → asks which", d.get("error") == "account_ambiguous", str(d))
        check("ambiguous saved nothing", q1("SELECT COUNT(*) FROM service_logs")[0] == logs_before)

        # 8. note_for unknown → plain error, saves nothing
        code, d = wh(MIKE_TG, "Note for Nowhere: x")
        check("note_for unknown → plain error", d.get("error") == "account_not_found", str(d))
        check("unknown saved nothing", q1("SELECT COUNT(*) FROM service_logs")[0] == logs_before)

        # 9. owner invite → link + token
        code, d = wh(OWNER_TG, "invite")
        check("owner invite", d.get("intent") == "invite" and not d.get("error"), str(d))
        tok = q1("SELECT invite_token FROM businesses WHERE id=?", (biz_a,))[0]
        check("invite token persisted", bool(tok), str(tok))

        # 10. rep invite → deflection, other tenant's token not rotated
        beta_tok_before = q1("SELECT invite_token FROM businesses WHERE id=?", (biz_b,))[0]
        code, d = wh(BETA_REP_TG, "invite")
        check("rep invite deflected", d.get("intent") == "invite" and d.get("error") == "not_owner", str(d))
        check("beta token untouched",
              q1("SELECT invite_token FROM businesses WHERE id=?", (biz_b,))[0] == beta_tok_before)

        # 11. tenant isolation: beta owner bootstraps into beta only
        code, d = wh(BETA_OWNER_TG, "New account: Beta Suites")
        check("beta owner new_account in own tenant", d.get("intent") == "new_account", str(d))
        row = q1("SELECT business_id FROM accounts WHERE name='Beta Suites'")
        check("Beta Suites in tenant B", row and row[0] == biz_b, str(row))
        check("tenant A account count unchanged",
              q1("SELECT COUNT(*) FROM accounts WHERE business_id=?", (biz_a,))[0] == n_before)

        # 12. demo mode survives: unknown telegram (not owner anywhere) → demo tenant
        code, d = wh(800555, "Lobby unit: test note")
        check("unknown tg still hits demo worker", d.get("worker") == "Demo Dan", str(d))
        row = q1("SELECT business_id FROM service_logs ORDER BY id DESC LIMIT 1")
        check("demo note in demo tenant", row and row[0] == biz_demo, str(row))

        # 13. owner bootstrap + Q&A still answers (tier=crew)
        code, d = wh(OWNER_TG, "what's the gate code for Smith Office?")
        check("owner Q&A answered", d.get("intent") == "question" and not d.get("gated"), str(d))

        # 14. dashboard add-account still works through the shared service
        r = httpx.post(f"{BASE}/api/dashboard/add-account",
                       params={"business_id": biz_a, "key": key_a},
                       json={"name": "Shared Path Co", "schedule": "Mon"}, timeout=15)
        d = r.json()
        check("dashboard add-account via shared service", d.get("ok") is True, str(d))
        row = q1("SELECT COUNT(*) FROM route_entries WHERE account_id=(SELECT id FROM accounts WHERE name='Shared Path Co')")
        check("dashboard schedule still synced", row and row[0] >= 1, str(row))
        r = httpx.post(f"{BASE}/api/dashboard/add-account",
                       params={"business_id": biz_a, "key": key_a},
                       json={"name": "Shared Path Co"}, timeout=15)
        check("dashboard dup guard intact", r.json().get("ok") is False, str(r.json()))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    unit_tests()
    http_tests()
    print(f"\n{'=' * 50}")
    if failures:
        print(f"❌ {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("✅ ALL P6B CHECKS PASSED")
