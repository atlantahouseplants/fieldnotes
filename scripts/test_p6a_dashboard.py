#!/usr/bin/env python3
"""
P6a dashboard quick-actions test suite. Two layers:

  A) HTTP endpoint tests — local uvicorn (port 8772) + temp sqlite.
     LLM keys are STRIPPED from the server env, so parse_note uses its
     deterministic _basic_parse fallback (no network, no cost, no flake).
  B) In-process unit test of the SHARED pipeline (services/ingest.py):
     owner notes and rep notes both persist through it — log + action
     queue + deterministic pipeline. This is where action extraction is
     verified (endpoint layer can't: fallback parse returns empty lists).

Production data is never touched.
"""
import json, os, sqlite3, subprocess, sys, time
import httpx

BASE = "http://127.0.0.1:8772"  # distinct from p5 (8769) — shared-port zombies caused cross-suite flakes
DB_PATH = "/tmp/p6a_dashboard_test.db"
UNIT_DB = "/tmp/p6a_ingest_unit.db"

failures = []
def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name} {detail}")
    if not cond:
        failures.append(name)

def make_business(db_path, name):
    """Insert a business directly; return (id, dashboard_key)."""
    import secrets
    db = sqlite3.connect(db_path)
    key = secrets.token_urlsafe(12)
    cur = db.execute(
        "INSERT INTO businesses (name, slug, owner_email, owner_name, dashboard_key, invite_token, subscription_status, tier, created_at) "
        "VALUES (?,?,?,?,?,?,?,?, datetime('now'))",
        (name, name.lower().replace(" ", "-"), "t@t.com", "Test Owner", key, secrets.token_urlsafe(12), "active", "team"))
    db.commit()
    bid = cur.lastrowid
    db.close()
    return bid, key

def add_account_row(db_path, business_id, name, shorthand=None):
    db = sqlite3.connect(db_path)
    cur = db.execute(
        "INSERT INTO accounts (business_id, name, shorthand, is_active) VALUES (?,?,?,1)",
        (business_id, name, shorthand))
    db.commit()
    aid = cur.lastrowid
    db.close()
    return aid

def http_tests():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    # Server env: real env MINUS LLM keys → deterministic _basic_parse fallback
    env = {k: v for k, v in os.environ.items()
           if k not in ("XAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")}
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8772"],
        cwd="/home/wallg/fieldnotes", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(40):
            try:
                httpx.get(f"{BASE}/health", timeout=2)
                up = True
                break
            except Exception:
                time.sleep(0.5)
        check("server up", up)
        if not up:
            return

        biz_a, key_a = make_business(DB_PATH, "Tenant Alpha")
        biz_b, key_b = make_business(DB_PATH, "Tenant Beta")
        smith_a = add_account_row(DB_PATH, biz_a, "Smith Office")
        add_account_row(DB_PATH, biz_a, "Smith Tower")
        add_account_row(DB_PATH, biz_b, "Beta Plaza")

        # ── 1. add-account happy path + schedule parsing + route sync ──
        r = httpx.post(f"{BASE}/api/dashboard/add-account?business_id={biz_a}&key={key_a}",
                       json={"name": "New Customer Co", "address": "1 Main St", "gate_code": "1234",
                             "schedule": "Tue (wk A) + Thu (wk B)"}, timeout=30)
        d = r.json()
        check("add-account ok", r.status_code == 200 and d.get("ok") is True, r.text[:200])
        db = sqlite3.connect(DB_PATH)
        row = db.execute("SELECT schedule_parsed FROM accounts WHERE business_id=? AND name='New Customer Co'", (biz_a,)).fetchone()
        check("schedule_parsed stored", bool(row and row[0]), str(row))
        entries = db.execute(
            "SELECT day_of_week, week_type FROM route_entries WHERE business_id=? AND account_id=? AND is_active=1",
            (biz_a, d.get("account_id", -1))).fetchall()
        check("route_entries synced (tue wkA + thu wkB)",
              sorted(entries) == sorted([("tuesday", "week_a"), ("thursday", "week_b")]), str(entries))

        # ── 2. add-account duplicate → plain-words ok:false ──
        r = httpx.post(f"{BASE}/api/dashboard/add-account?business_id={biz_a}&key={key_a}",
                       json={"name": "smith office"}, timeout=30)
        d = r.json()
        check("add-account dup → ok:false", d.get("ok") is False and "already" in d.get("message", "").lower(), str(d))
        n = db.execute("SELECT COUNT(*) FROM accounts WHERE business_id=? AND LOWER(name)='smith office'", (biz_a,)).fetchone()[0]
        check("dup did not create row", n == 1, f"count={n}")

        # ── 3. cross-tenant dup is ALLOWED (isolation, not global uniqueness) ──
        r = httpx.post(f"{BASE}/api/dashboard/add-account?business_id={biz_b}&key={key_b}",
                       json={"name": "Smith Office"}, timeout=30)
        check("cross-tenant same name allowed", r.json().get("ok") is True, r.text[:150])

        # ── 4. add-note happy path ──
        r = httpx.post(f"{BASE}/api/dashboard/add-note?business_id={biz_a}&key={key_a}",
                       json={"account": "Smith Office", "note": "Serviced lobby unit, all good"}, timeout=30)
        d = r.json()
        check("add-note ok", d.get("ok") is True and "Smith Office" in d.get("message", ""), str(d))
        log = db.execute(
            "SELECT account_id, worker_id, raw_note, parsed_status FROM service_logs WHERE id=?",
            (d.get("log_id", -1),)).fetchone()
        check("log row correct account", log and log[0] == smith_a, str(log))
        check("log raw_note preserved", log and log[2] == "Serviced lobby unit, all good")
        if log:
            wname = db.execute("SELECT name FROM workers WHERE id=?", (log[1],)).fetchone()
            check("attributed to Owner worker", wname and wname[0] == "Owner", str(wname))

        # appears in the dashboard feed
        feed = httpx.get(f"{BASE}/api/dashboard/logs?business_id={biz_a}&key={key_a}", timeout=30).json()
        check("note in /logs feed", any(l.get("id") == d.get("log_id") for l in feed), f"feed has {len(feed)}")
        feed_entry = next((l for l in feed if l.get("id") == d.get("log_id")), {})
        check("feed shows Owner + account", feed_entry.get("worker") == "Owner" and feed_entry.get("account") == "Smith Office", str(feed_entry.get("worker")))

        # ── 5. add-note ambiguous → options listed (tenant A only) ──
        r = httpx.post(f"{BASE}/api/dashboard/add-note?business_id={biz_a}&key={key_a}",
                       json={"account": "Smith", "note": "check filter"}, timeout=30)
        d = r.json()
        check("ambiguous → ok:false", d.get("ok") is False, str(d))
        check("ambiguous lists both A accounts", "Smith Office" in d.get("message", "") and "Smith Tower" in d.get("message", ""), d.get("message", ""))
        check("ambiguous does NOT leak tenant B", "Beta" not in d.get("message", ""), d.get("message", ""))

        # ── 6. add-note unknown account ──
        r = httpx.post(f"{BASE}/api/dashboard/add-note?business_id={biz_a}&key={key_a}",
                       json={"account": "Nowhere Place", "note": "x"}, timeout=30)
        check("unknown account → ok:false", r.json().get("ok") is False, str(r.json()))

        # ── 7. cross-tenant isolation on add-note ──
        # A-only name with no word collision in B → clean not-found, no leak
        add_account_row(DB_PATH, biz_a, "Tower Plaza")
        r = httpx.post(f"{BASE}/api/dashboard/add-note?business_id={biz_b}&key={key_b}",
                       json={"account": "Tower Plaza", "note": "snoop"}, timeout=30)
        check("B cannot see A-only account", r.json().get("ok") is False, str(r.json()))
        # Near-name that fuzzily matches B's OWN account must stay in tenant B
        r = httpx.post(f"{BASE}/api/dashboard/add-note?business_id={biz_b}&key={key_b}",
                       json={"account": "Smith Tower", "note": "snoop2"}, timeout=30)
        d = r.json()
        if d.get("ok"):
            owner = db.execute(
                "SELECT business_id FROM accounts WHERE id=(SELECT account_id FROM service_logs WHERE id=?)",
                (d["log_id"],)).fetchone()
            check("fuzzy match stays in tenant B", owner and owner[0] == biz_b, str(owner))
        else:
            check("fuzzy match stays in tenant B", True, "rejected instead — also fine")
        # And tenant A's note must be invisible in B's feed
        feed_b = httpx.get(f"{BASE}/api/dashboard/logs?business_id={biz_b}&key={key_b}", timeout=30).json()
        check("B feed has no A notes",
              not any((l.get("raw_note") or "") == "Serviced lobby unit, all good" for l in feed_b),
              f"feed_b={len(feed_b)}")

        # ── 8. invite-link ──
        r = httpx.get(f"{BASE}/api/dashboard/invite-link?business_id={biz_a}&key={key_a}", timeout=30)
        d = r.json()
        check("invite-link ok", d.get("ok") is True, str(d))
        check("invite link format", "t.me/" in d.get("invite_link", "") and "?start=invite_" in d.get("invite_link", ""), d.get("invite_link", ""))
        check("owner link format", "?start=owner_" in d.get("owner_link", ""), d.get("owner_link", ""))
        # stable across calls (same token)
        r2 = httpx.get(f"{BASE}/api/dashboard/invite-link?business_id={biz_a}&key={key_a}", timeout=30).json()
        check("invite token stable", r2.get("invite_link") == d.get("invite_link"))

        # ── 9. wrong-key 403 on all three endpoints ──
        e1 = httpx.post(f"{BASE}/api/dashboard/add-account?business_id={biz_a}&key=bad",
                        json={"name": "X"}, timeout=30).status_code
        e2 = httpx.post(f"{BASE}/api/dashboard/add-note?business_id={biz_a}&key=bad",
                        json={"account": "Smith Office", "note": "x"}, timeout=30).status_code
        e3 = httpx.get(f"{BASE}/api/dashboard/invite-link?business_id={biz_a}&key=bad", timeout=30).status_code
        e4 = httpx.post(f"{BASE}/api/dashboard/add-account?business_id={biz_a}&key={key_b}",
                        json={"name": "Y"}, timeout=30).status_code
        check("wrong key → 403 (all 3 + cross-tenant key)", (e1, e2, e3, e4) == (403, 403, 403, 403), f"{e1},{e2},{e3},{e4}")
        db.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def unit_tests():
    """In-process: the shared ingest pipeline persists log + actions + runs pipeline."""
    if os.path.exists(UNIT_DB):
        os.remove(UNIT_DB)
    os.environ["DATABASE_URL"] = f"sqlite:///{UNIT_DB}"
    sys.path.insert(0, "/home/wallg/fieldnotes")

    from backend.models import Base, SessionLocal, engine, Business, Account, Worker, ServiceLog
    from backend.services.ingest import persist_parsed_note

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        biz = Business(name="Unit Co", slug="unit-co", owner_email="t@t.com", owner_name="Test Owner", dashboard_key="k", subscription_status="active")
        db.add(biz); db.commit(); db.refresh(biz)
        acct = Account(business_id=biz.id, name="Smith Office", is_active=True)
        db.add(acct); db.commit(); db.refresh(acct)
        owner = Worker(business_id=biz.id, name="Owner", telegram_id=None, is_active=True)
        db.add(owner); db.commit(); db.refresh(owner)

        parsed = {
            "status": "issues_found",
            "issues": ["pump leaking"],
            "supplies": ["gasket"],
            "followups": ["recheck Friday"],
            "customer_requests": [],
            "processing_time_ms": 5,
        }
        out = persist_parsed_note(
            db, business_id=biz.id, worker_id=owner.id,
            text="Smith Office: pump leaking, need gasket, recheck Friday",
            parsed=parsed, account_id=acct.id,
        )
        log = out["log"]
        check("unit: log persisted", log.id is not None and log.account_id == acct.id)
        check("unit: 3 actions created", len(out["actions_created"]) == 3, str(out["actions_created"]))
        acts = db.execute(
            __import__("sqlalchemy").text(
                "SELECT description, priority, service_log_id FROM actions WHERE business_id=:b"),
            {"b": biz.id}).fetchall()
        check("unit: action rows in db", len(acts) == 3 and all(a[2] == log.id for a in acts), str(acts))
        prios = sorted(a[1] for a in acts)
        check("unit: priorities (1 this_week + 2 next_visit)", prios == ["next_visit", "next_visit", "this_week"], str(prios))
        check("unit: supply prefixed", any(a[0].startswith("Supply: gasket") for a in acts), str(acts))
        check("unit: pipeline ran", isinstance(out["pipeline"], dict), str(out["pipeline"])[:80])
        stored = db.query(ServiceLog).filter(ServiceLog.id == log.id).first()
        check("unit: parsed fields stored as json",
              json.loads(stored.parsed_issues) == ["pump leaking"] and json.loads(stored.parsed_supplies) == ["gasket"])
    finally:
        db.close()


def main():
    unit_tests()
    http_tests()
    print(f"\n{'✅ ALL PASSED' if not failures else '❌ FAILURES: ' + ', '.join(failures)}")
    sys.exit(1 if failures else 0)

if __name__ == "__main__":
    main()
