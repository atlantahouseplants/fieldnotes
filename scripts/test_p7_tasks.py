#!/usr/bin/env python3
"""
P7 account-tasks test suite. Layers:

  A) In-process unit tests — deterministic parsers/matchers in
     services/tasks.py (no server, no LLM).
  B) HTTP end-to-end — local uvicorn (port 8771) + temp sqlite, LLM keys
     stripped so parse_note uses the deterministic fallback. Full loop:
     owner creates via chat → rep sees at log time → rep closes → confirmed.
     Plus YES/NO proposals, ambiguity, guards, dashboard endpoints,
     morning-push annotation, and tenant isolation.

Production data is never touched. TZ pinned to UTC (mirrors Railway prod).
"""
import json, os, secrets, sqlite3, subprocess, sys, time
from datetime import datetime

import httpx

BASE = "http://127.0.0.1:8771"
DB_PATH = "/tmp/p7_tasks_test.db"
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
    from backend.services import tasks as T

    ci = T.task_create_intent("Task for Smith Office: repair pool cover, needs patch kit, Thursday")
    check("U create intent parsed", ci == ("Smith Office", "repair pool cover, needs patch kit, Thursday"), str(ci))
    check("U non-task passes", T.task_create_intent("Smith Office: all good") is None)

    class W:  # minimal worker stand-in
        def __init__(s, n): s.name, s.id = n, 7
    parts = T.parse_task_body("repair pool cover, needs patch kit, Mike, Thursday", [W("Mike R")])
    check("U body title", parts["title"] == "repair pool cover", str(parts))
    check("U body supplies", parts["supplies"] == "patch kit", str(parts))
    check("U body worker", parts["assigned"] is not None and parts["assigned"].name == "Mike R")
    check("U body due", parts["due"] == "Thursday", str(parts))

    parts2 = T.parse_task_body("check the lobby pump", [])
    check("U bare title", parts2 == {"title": "check the lobby pump", "supplies": None, "assigned": None, "due": None}, str(parts2))

    check("U close language", T.task_close_language("done with the cover at Smith"))
    check("U non-close language", not T.task_close_language("Smith Office: all good"))

    class T_:  # minimal task stand-in
        def __init__(s, t): s.title, s.supplies_needed = t, None
    open_t = [T_("repair pool cover"), T_("repair gate hinge")]
    cands = T.match_open_tasks(open_t, "finished the repair at Smith")
    check("U ambiguous match → 2 candidates", len(cands) == 2, str([c.title for c in cands]))
    cands = T.match_open_tasks(open_t, "done with the filter at Smith")
    check("U no overlap → 0 candidates", len(cands) == 0)
    cands = T.match_open_tasks([T_("repair pool cover")], "repaired the pool cover, looks good")
    check("U log-note overlap → 1 candidate", len(cands) == 1)

    ann = T.tasks_annotation([T_("repair pool cover")])
    check("U annotation", ann.startswith("⚠️ 1 open task:") and "repair pool cover" in ann, ann)
    check("U yes/no", T.is_yes("Yes") and T.is_no("NO") and T.is_yes_or_no("yep") and not T.is_yes_or_no("maybe"))


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
           if k not in ("XAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")}
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    env["TZ"] = "UTC"  # mirror Railway prod; avoid local/UTC date flips
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8771"],
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

        OWNER_TG, MIKE_TG = 700099, 700001
        biz_a, key_a = make_business("Tenant Alpha", owner_tg=str(OWNER_TG))
        biz_b, key_b = make_business("Tenant Beta")
        mike_id = add_worker(biz_a, "Mike R", MIKE_TG)
        add_worker(biz_a, "Geoff Owner", OWNER_TG)  # owner is also a worker row
        add_worker(biz_b, "Beta Rep", 700777)
        smith = add_account(biz_a, "Smith Office", "office")
        tower = add_account(biz_a, "Smith Tower", "tower")
        add_account(biz_b, "Beta Plaza", "beta")

        # 1. Rep creates a task via chat
        code, d = wh(MIKE_TG, "Task for Smith Office: repair pool cover, needs patch kit, Thursday")
        check("chat create_task", d.get("intent") == "create_task" and d.get("account") == "Smith Office", str(d))
        check("chat create supplies+due", d.get("supplies") == "patch kit" and d.get("due") == "Thursday", str(d))
        task_id = d.get("task_id")
        row = q1("SELECT source, created_by_worker_id, status, business_id FROM account_tasks WHERE id=?", (task_id,))
        check("task row: chat_rep/by Mike/open/tenant A",
              row == ("chat_rep", mike_id, "open", biz_a), str(row))

        # 2. Owner creates via chat → source chat_owner, created_by NULL
        code, d = wh(OWNER_TG, "Task for Smith Tower: check lobby pump, Mike")
        check("owner create_task", d.get("intent") == "create_task" and d.get("assigned_to") == "Mike R", str(d))
        row = q1("SELECT source, created_by_worker_id, assigned_worker_id FROM account_tasks WHERE id=?", (d.get("task_id"),))
        check("owner task: chat_owner/NULL creator/assigned Mike",
              row is not None and row[0] == "chat_owner" and row[1] is None and row[2] == mike_id, str(row))

        # 3. Unknown + ambiguous account
        code, d = wh(MIKE_TG, "Task for Nowhereville: fix the thing")
        check("create unknown account → plain error", d.get("error") == "account_not_found", str(d))
        code, d = wh(MIKE_TG, "Task for Smith: do a thing")
        check("create ambiguous (smith) → asks which", d.get("error") == "account_ambiguous", str(d))

        # 4. Log-time surfacing: rep logs at Smith Office → open task rides along
        code, d = wh(MIKE_TG, "Smith Office: quarterly service complete, all good")
        check("log at task account → open_tasks=1", d.get("open_tasks") == 1, str(d))
        check("no proposal (no title overlap)", d.get("proposed_task_id") is None, str(d))

        # 5. Implicit completion proposal
        code, d = wh(MIKE_TG, "Smith Office: repaired the pool cover, looks good")
        check("log overlapping task → proposal", d.get("proposed_task_id") == task_id, str(d))
        pend = q1("SELECT task_id FROM pending_task_closes WHERE worker_id=?", (mike_id,))
        check("pending row stored", pend and pend[0] == task_id, str(pend))

        # 6. YES closes; owner-ping path runs without breaking the reply
        code, d = wh(MIKE_TG, "yes")
        check("YES → close_task via confirm", d.get("intent") == "close_task" and d.get("via") == "confirm", str(d))
        row = q1("SELECT status, closed_by_worker_id FROM account_tasks WHERE id=?", (task_id,))
        check("task done, closed by Mike", row == ("done", mike_id), str(row))
        check("pending cleared", q1("SELECT COUNT(*) FROM pending_task_closes WHERE worker_id=?", (mike_id,))[0] == 0)

        # 7. NO keeps it open
        code, d = wh(OWNER_TG, "Task for Smith Office: replace return filter")
        t2 = d.get("task_id")
        code, d = wh(MIKE_TG, "Smith Office: swapped the return filter in ceiling grid")
        check("second proposal offered", d.get("proposed_task_id") == t2, str(d))
        code, d = wh(MIKE_TG, "no")
        check("NO → declined, stays open", d.get("intent") == "close_task_declined"
              and q1("SELECT status FROM account_tasks WHERE id=?", (t2,))[0] == "open", str(d))

        # 8. Explicit close
        code, d = wh(MIKE_TG, "done with the return filter at Smith Office")
        check("explicit close", d.get("intent") == "close_task" and d.get("via") == "explicit", str(d))
        check("task done", q1("SELECT status FROM account_tasks WHERE id=?", (t2,))[0] == "done")

        # 9. Ambiguous close → asks, closes nothing
        code, d = wh(OWNER_TG, "Task for Smith Office: repair pool cover")
        code, d = wh(OWNER_TG, "Task for Smith Office: repair gate hinge")
        code, d = wh(MIKE_TG, "finished the repair at Smith Office")
        check("ambiguous close → asks", d.get("error") == "ambiguous" and len(d.get("candidates", [])) == 2, str(d))
        n_open = q1("SELECT COUNT(*) FROM account_tasks WHERE business_id=? AND status='open'", (biz_a,))[0]
        check("nothing closed on ambiguity", n_open == 3, f"open={n_open}")  # 2 repair + Smith Tower pump

        # 10. Guard: completion language at an account with NO open tasks → normal log
        code, d = wh(MIKE_TG, "Smith Tower: all done, filters changed")
        check("guard: plain note still logs", "status" in d and d.get("intent") is None, str(d))

        # 11. Morning route annotation (route Q&A carries open tasks)
        dow = datetime.utcnow().strftime("%A").lower()
        db = sqlite3.connect(DB_PATH)
        db.execute("INSERT INTO route_entries (business_id, account_id, day_of_week, week_type, route_order, is_active) VALUES (?,?,?,?,0,1)",
                   (biz_a, smith, dow, "weekly"))
        db.commit(); db.close()
        code, d = wh(MIKE_TG, "route today")
        check("route answer annotates open tasks",
              "open task" in d.get("answer", "") and "repair pool cover" in d.get("answer", ""),
              d.get("answer", "")[:200])

        # 12. Dashboard endpoints
        r = httpx.post(f"{BASE}/api/dashboard/add-task?business_id={biz_a}&key={key_a}",
                       json={"account": "Smith Office", "title": "order filters", "supplies": "16x20 filters"}, timeout=30)
        d = r.json()
        check("dash add-task", d.get("ok") is True, str(d))
        dash_task = d.get("task_id")
        row = q1("SELECT source, created_by_worker_id FROM account_tasks WHERE id=?", (dash_task,))
        check("dash task: dashboard/NULL owner", row == ("dashboard", None), str(row))

        r = httpx.get(f"{BASE}/api/dashboard/tasks?business_id={biz_a}&key={key_a}", timeout=30)
        d = r.json()
        titles = [t["title"] for t in d.get("open", [])]
        check("dash list open incl account names",
              "order filters" in titles and all("account" in t for t in d["open"]), str(titles))
        check("dash list closed", any(t["title"] == "repair pool cover" for t in d.get("closed", [])), str(d.get("closed"))[:150])

        r = httpx.post(f"{BASE}/api/dashboard/close-task?task_id={dash_task}&business_id={biz_a}&key={key_a}", timeout=30)
        check("dash close-task", r.json().get("ok") is True, r.text[:150])
        check("dash closed row", q1("SELECT status FROM account_tasks WHERE id=?", (dash_task,))[0] == "done")

        # 13. Key-lock on all three
        codes = [
            httpx.get(f"{BASE}/api/dashboard/tasks?business_id={biz_a}&key=WRONG", timeout=15).status_code,
            httpx.post(f"{BASE}/api/dashboard/add-task?business_id={biz_a}&key=WRONG",
                       json={"account": "Smith Office", "title": "x"}, timeout=15).status_code,
            httpx.post(f"{BASE}/api/dashboard/close-task?task_id=1&business_id={biz_a}&key=WRONG", timeout=15).status_code,
        ]
        check("wrong key → 403 (all 3)", codes == [403, 403, 403], str(codes))

        # 14. Tenant isolation
        r = httpx.get(f"{BASE}/api/dashboard/tasks?business_id={biz_b}&key={key_b}", timeout=30)
        d = r.json()
        check("tenant B sees none of A's tasks", d.get("open") == [] and d.get("closed") == [], str(d)[:150])
        r = httpx.post(f"{BASE}/api/dashboard/close-task?task_id={dash_task}&business_id={biz_b}&key={key_b}", timeout=30)
        check("cross-tenant close → 404", r.status_code == 404, str(r.status_code))
        n = q1("SELECT COUNT(*) FROM account_tasks WHERE business_id=?", (biz_b,))[0]
        check("zero B rows after all of A's activity", n == 0, f"n={n}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    env_path = os.path.join(REPO, ".env")
    secret = ""
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("TELEGRAM_SECRET="):
                secret = line.strip().split("=", 1)[1]
    if secret:
        SECRET_HDR["x-telegram-bot-api-secret-token"] = secret

    unit_tests()
    http_tests()
    print()
    if failures:
        print(f"❌ {len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("✅ ALL PASSED")
