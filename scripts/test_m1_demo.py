#!/usr/bin/env python3
"""M1 /api/demo test suite (plans/marketing/README.md).

Three boots of a local uvicorn (port 8775) against temp sqlite DBs:

  Phase A: functional — question answers with source, note logs through the
           REAL ingest path (biz 2), task close + self-heal reseed, recap
           preview is display-only (RecapLog marked skipped, NEVER sent),
           cross-tenant probes prove no other tenant is reachable.
  Phase B: per-IP hourly rate limit kicks in with a plain-words 429.
  Phase C: per-IP daily rate limit kicks in with a plain-words 429.

LLM keys are set to EMPTY STRINGS (not deleted — main.py's load_dotenv
re-hydrates deleted vars; pitfalls #38/#40) so parse/QA run deterministic
fallbacks with zero network/cost. FIELDNOTES_RECAP_STUB gives a canned
client-safe rewrite so the recap preview is deterministic.
TZ pinned to UTC (mirrors Railway prod — pitfall #34).
"""
import json, os, secrets, sqlite3, subprocess, sys, time

os.environ["TZ"] = "UTC"
time.tzset()

import httpx

BASE = "http://127.0.0.1:8775"
DB_PATH = "/tmp/m1_demo_test.db"
REPO = "/home/wallg/fieldnotes"
sys.path.insert(0, REPO)

failures = []
def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name} {detail}")
    if not cond:
        failures.append(name)

CLEAN_STUB = ("Your service was completed today. Our technician replaced the "
              "worn belt on the first unit and confirmed everything is running well.")


# ── seeding (raw sqlite3 — explicit defaulted/NOT-NULL columns, #5/#30) ──
def _db():
    return sqlite3.connect(DB_PATH)

def make_business(name, tier="team", beta=1):
    db = _db()
    cur = db.execute(
        "INSERT INTO businesses (name, slug, owner_email, owner_name, dashboard_key, "
        "invite_token, subscription_status, tier, beta_all_access, is_active, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now'))",
        (name, name.lower().replace(" ", "-"), "t@t.com", "Owner",
         secrets.token_urlsafe(12), secrets.token_urlsafe(12), "active", tier, beta, 1))
    db.commit(); bid = cur.lastrowid; db.close()
    return bid

def add_worker(bid, name):
    db = _db()
    cur = db.execute(
        "INSERT INTO workers (business_id, name, telegram_id, is_active, created_at) "
        "VALUES (?,?,NULL,1, datetime('now'))", (bid, name))
    db.commit(); wid = cur.lastrowid; db.close()
    return wid

def add_account(bid, name, shorthand=None, gate=None, notes=None, recap=0):
    db = _db()
    cur = db.execute(
        "INSERT INTO accounts (business_id, name, shorthand, gate_code, notes, "
        "is_active, recap_enabled, recap_email) VALUES (?,?,?,?,?,1,?,?)",
        (bid, name, shorthand, gate, notes, recap,
         "sarah@atlantahouseplant.com" if recap else None))
    db.commit(); aid = cur.lastrowid; db.close()
    return aid

def add_task(bid, aid, title):
    db = _db()
    cur = db.execute(
        "INSERT INTO account_tasks (business_id, account_id, title, status, source, created_at) "
        "VALUES (?,?,?,'open','seed', datetime('now'))", (bid, aid, title))
    db.commit(); tid = cur.lastrowid; db.close()
    return tid

def q1(sql, args=()):
    db = _db()
    row = db.execute(sql, args).fetchone()
    db.close()
    return row

def seed_demo(full=True):
    """Filler tenant FIRST so the demo tenant lands on id=2 (deliberate —
    the endpoint hardcodes business_id=2; pitfall #38 family)."""
    other = make_business("Other Co", tier="solo", beta=0)
    add_account(other, "Secret Plaza", "secret", gate="9999")
    demo = make_business("Precision HVAC", tier="team", beta=1)
    assert demo == 2, f"demo tenant must be id=2, got {demo}"
    add_worker(demo, "Demo Dan")
    riverside = add_account(
        demo, "Riverside Office Park", "riverside", gate="#4412",
        notes="Gate code #4412 (enter slowly). Loading dock B for equipment.",
        recap=1)
    add_account(demo, "Grand Hotel Downtown", "grand hotel",
                notes="Service elevator, key from front desk.")
    if full:
        add_task(demo, riverside, "Replace belt on unit 1")
    return demo, riverside


def boot_server(env_extra):
    for p in (DB_PATH,):
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ)
    # Empty-string the LLM keys (NEVER delete — load_dotenv re-hydrates).
    env["XAI_API_KEY"] = ""
    env["DEEPSEEK_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    env["TZ"] = "UTC"
    env["FIELDNOTES_RECAP_STUB"] = CLEAN_STUB
    env["FIELDNOTES_EMAIL_STUB"] = "1"   # defensive — demo must never send
    env.update(env_extra)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", "8775"],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    up = False
    for _ in range(40):
        try:
            httpx.get(f"{BASE}/health", timeout=2); up = True; break
        except Exception:
            time.sleep(0.5)
    return proc, up

def stop_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

def demo_post(text):
    r = httpx.post(f"{BASE}/api/demo", json={"text": text}, timeout=60)
    return r.status_code, r.json()


# ── Phase A: functional ──────────────────────────────────────────
def phase_a():
    proc, up = boot_server({"FIELDNOTES_DEMO_RATE_HOUR": "100",
                            "FIELDNOTES_DEMO_RATE_DAY": "500"})
    check("A server up", up)
    if not up:
        return
    try:
        demo_biz, riverside = seed_demo(full=True)

        code, d = demo_post("")
        check("A empty text → 400 plain words", code == 400 and d.get("ok") is False
              and "type" in (d.get("message") or "").lower(), str(d))

        code, d = demo_post("x" * 501)
        check("A over-long text → 400 plain words", code == 400 and "500" in d.get("message", ""), str(d))

        # 1. THE aha — gate code question, answered with source
        code, d = demo_post("What's the gate code for Riverside?")
        check("A question → kind=question", code == 200 and d.get("kind") == "question", str(d))
        check("A gate code answered (4412)", "4412" in (d.get("answer") or ""), (d.get("answer") or "")[:120])
        check("A sources cite Riverside",
              any("riverside" in str(s).lower() for s in (d.get("sources") or [])), str(d.get("sources")))

        # 2. Cross-tenant probe — biz 1's Secret Plaza (gate 9999) unreachable
        code, d = demo_post("What's the gate code for Secret Plaza?")
        check("A cross-tenant Q: no 9999 leak", "9999" not in json.dumps(d), str(d)[:150])

        # 3. Note → REAL ingest path: log + task close + recap preview
        code, d = demo_post("Riverside: replaced the belt on unit 1")
        check("A note → kind=note", code == 200 and d.get("kind") == "note", str(d))
        check("A note matched Riverside",
              d.get("log", {}).get("matched") is True
              and "riverside" in d.get("log", {}).get("account", ""), str(d.get("log")))
        check("A task closed (P7 payoff)",
              (d.get("task_closed") or {}).get("title") == "Replace belt on unit 1",
              str(d.get("task_closed")))
        rp = d.get("recap_preview") or {}
        check("A recap preview drafted (P8 payoff)", rp.get("client_text") == CLEAN_STUB, str(rp)[:150])
        check("A recap subject names account", "Riverside" in rp.get("subject", ""), rp.get("subject", ""))
        check("A NO recipient email leaked", "sarah@" not in json.dumps(d), "")

        # DB truth behind the response
        row = q1("SELECT account_id, business_id FROM service_logs WHERE raw_note LIKE '%replaced the belt%'")
        check("A log persisted in biz 2", row == (riverside, 2), str(row))
        done = q1("SELECT status FROM account_tasks WHERE title='Replace belt on unit 1' AND status='done'")
        check("A task row done in DB", done is not None)
        reheal = q1("SELECT COUNT(*) FROM account_tasks WHERE title='Replace belt on unit 1' AND status='open'")
        check("A self-heal re-opened task for next visitor", reheal and reheal[0] == 1, str(reheal))
        rec = q1("SELECT status, client_text FROM recap_log ORDER BY id DESC LIMIT 1")
        check("A recap row skipped (display-only, NEVER sent)",
              rec is not None and rec[0] == "skipped" and rec[1] == CLEAN_STUB, str(rec))
        bad = q1("SELECT COUNT(*) FROM recap_log WHERE status IN ('sent','pending_approval','held')")
        check("A no recap in any send path", bad and bad[0] == 0, str(bad))

        # 4. Tap 3 standalone — "show the recap" replays the preview
        code, d = demo_post("show the recap")
        check("A 'show the recap' → preview replayed",
              code == 200 and d.get("kind") == "recap"
              and (d.get("recap_preview") or {}).get("client_text") == CLEAN_STUB, str(d)[:150])

        # 5. Cross-tenant note: mentions biz-1 account → uncategorized in biz 2,
        #    nothing written to biz 1
        n_before = q1("SELECT COUNT(*) FROM service_logs WHERE business_id=1")[0]
        code, d = demo_post("Secret Plaza: swapped the filter")
        check("A cross-tenant note → unmatched in biz 2",
              code == 200 and d.get("log", {}).get("matched") is False, str(d.get("log")))
        n_after = q1("SELECT COUNT(*) FROM service_logs WHERE business_id=1")[0]
        check("A nothing written to biz 1", n_after == n_before, f"{n_before}→{n_after}")
    finally:
        stop_server(proc)


# ── Phase B: hourly rate limit ───────────────────────────────────
def phase_b():
    proc, up = boot_server({"FIELDNOTES_DEMO_RATE_HOUR": "2",
                            "FIELDNOTES_DEMO_RATE_DAY": "100"})
    check("B server up", up)
    if not up:
        return
    try:
        seed_demo(full=False)
        c1, _ = demo_post("gate code for Riverside?")
        c2, _ = demo_post("gate code for Riverside?")
        c3, d3 = demo_post("gate code for Riverside?")
        check("B first two pass", c1 == 200 and c2 == 200, f"{c1},{c2}")
        check("B third → 429 plain words",
              c3 == 429 and "busy" in (d3.get("message") or "").lower(), f"{c3} {d3}")
    finally:
        stop_server(proc)


# ── Phase C: daily rate limit ────────────────────────────────────
def phase_c():
    proc, up = boot_server({"FIELDNOTES_DEMO_RATE_HOUR": "100",
                            "FIELDNOTES_DEMO_RATE_DAY": "2"})
    check("C server up", up)
    if not up:
        return
    try:
        seed_demo(full=False)
        codes = [demo_post("gate code for Riverside?")[0] for _ in range(3)]
        check("C daily cap → 429 on third", codes == [200, 200, 429], str(codes))
    finally:
        stop_server(proc)


if __name__ == "__main__":
    phase_a()
    phase_b()
    phase_c()
    print(f"\n{'=' * 50}")
    if failures:
        print(f"❌ {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("✅ M1 demo suite — all green")
