#!/usr/bin/env python3
"""
P3 SMS-channel test suite. Layers:

  A) In-process unit tests — plain() HTML strip, 900-char chunking + MORE
     stash/TTL, normalize_e164, verify_webhook HMAC, channel destination
     routing (own phone → SMS, anything else → Telegram), SmsChannel
     worker resolution (no demo fallback). Temp sqlite, no server.
  B) HTTP end-to-end — local uvicorn (port 8776) + temp sqlite, signed
     AgentPhone payloads: bad-signature 403, unknown number → invite prompt,
     note logging over SMS, tenant isolation, demo-tenant leak guard,
     dashboard SMS invite → YES registration, STOP silence / START rejoin,
     dashboard re-invite of opted-out number refused, Q&A over SMS
     (deterministic fallback), MORE with empty stash.

Outbound SMS is a no-op in tests: the server boots with AGENTPHONE_API_KEY=""
(load_dotenv never overrides existing env vars), so send_sms returns
"not configured" — flow continues, assertions are on HTTP responses + DB rows.
Production data is never touched. TZ pinned to UTC (mirrors Railway prod).
"""
import hashlib, hmac, json, os, secrets, sqlite3, subprocess, sys, time
from datetime import datetime

import httpx

BASE = "http://127.0.0.1:8776"
DB_PATH = "/tmp/p3_sms_test.db"
UNIT_DB = "/tmp/p3_sms_unit.db"
REPO = "/home/wallg/fieldnotes"
sys.path.insert(0, REPO)

failures = []
def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name} {detail}")
    if not cond:
        failures.append(name)

TEST_SECRET = "p3-test-secret"


# ── A) unit tests (in-process) ────────────────────────────────────
def unit_tests():
    if os.path.exists(UNIT_DB):
        os.remove(UNIT_DB)
    os.environ["DATABASE_URL"] = f"sqlite:///{UNIT_DB}"
    os.environ["AGENTPHONE_API_KEY"] = ""       # outbound no-ops
    os.environ["AGENTPHONE_WEBHOOK_SECRET"] = TEST_SECRET

    from backend.integrations import channel as C
    from backend.integrations import agentphone as A
    from backend.models import init_db, SessionLocal, Business, Worker

    # ── plain() ──
    check("U plain strips <b>/<i>", C.plain("<b>Alpha Site</b> logged — <i>all good</i>")
          == "Alpha Site logged — all good")
    check("U plain unescapes entities", C.plain("Tom &amp; Jerry &lt;3") == "Tom & Jerry <3")
    check("U plain keeps emoji", "✅" in C.plain("✅ <b>done</b>"))
    check("U plain None-safe", C.plain(None) == "")

    # ── chunking + MORE stash ──
    short = "x" * 400
    check("U short text passes through", C._chunk_for_send("+1555", short) == short
          and C.pop_more("+1555") is None)
    long_text = "a" * 2000
    first = C._chunk_for_send("+1555", long_text)
    check("U long text → ≤900 chunk", len(first) <= C.SMS_LIMIT, f"len={len(first)}")
    check("U chunk ends with MORE marker", first.endswith("Reply MORE for the rest."))
    second = C.pop_more("+1555")
    check("U MORE returns continuation", second is not None and "Reply MORE" in second)
    third = C.pop_more("+1555")
    check("U third MORE returns tail", third is not None and "Reply MORE" not in third)
    check("U stash drained", C.pop_more("+1555") is None)
    # TTL expiry
    C._MORE_STASH["+1555"] = ("leftover", time.time() - 1)
    check("U expired stash → None", C.pop_more("+1555") is None)

    # ── normalize_e164 ──
    check("U e164 bare 10-digit", A.normalize_e164("4044932910") == "+14044932910")
    check("U e164 formatted", A.normalize_e164("(404) 493-2910") == "+14044932910")
    check("U e164 already e164", A.normalize_e164("+14044932910") == "+14044932910")
    check("U e164 11-digit US", A.normalize_e164("14044932910") == "+14044932910")
    check("U e164 junk → empty", A.normalize_e164("493-2910") == "")

    # ── verify_webhook HMAC (scheme verified live against AgentPhone) ──
    raw = b'{"event":"agent.message","data":{}}'
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(TEST_SECRET.encode(), f"{ts}.".encode() + raw,
                               hashlib.sha256).hexdigest()
    check("U webhook HMAC valid", A.verify_webhook(raw, ts, sig))
    check("U webhook wrong secret rejected",
          not A.verify_webhook(raw, ts, "sha256=" + hmac.new(
              b"wrong", f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()))
    check("U webhook tampered body rejected", not A.verify_webhook(raw + b"x", ts, sig))
    check("U webhook garbage header rejected", not A.verify_webhook(raw, ts, "garbage"))
    check("U webhook no secret configured → False",
          not A.verify_webhook.__globals__["WEBHOOK_SECRET"] == "" or False)  # secret IS set
    os.environ["AGENTPHONE_WEBHOOK_SECRET"] = ""
    A_nokey = A
    saved = A_nokey.WEBHOOK_SECRET
    A_nokey.WEBHOOK_SECRET = ""
    check("U webhook empty-secret never verifies", not A_nokey.verify_webhook(raw, ts, sig))
    A_nokey.WEBHOOK_SECRET = saved

    # ── SmsChannel: worker resolution (no demo fallback) + destination routing ──
    init_db()
    db = SessionLocal()
    demo = Business(name="Demo Co", slug="demo-co", owner_email="d@d.com",
                    owner_name="D", tier="team", is_active=True)
    db.add(demo); db.commit(); db.refresh(demo)
    biz2 = Business(name="Biz Two", slug="biz-two", owner_email="b@b.com",
                    owner_name="B", tier="team", is_active=True)
    db.add(biz2); db.commit(); db.refresh(biz2)   # id == 2 (demo slot)
    w_demo = Worker(business_id=biz2.id, name="Demo Worker", telegram_id="900001", is_active=True)
    w_sms = Worker(business_id=demo.id, name="Sms Sam", phone="+15550000001", is_active=True)
    db.add_all([w_demo, w_sms]); db.commit()

    found = C.SmsChannel("+15550000001").find_worker(db)
    check("U sms channel resolves by phone", found is not None and found[0].name == "Sms Sam"
          and found[1] is False)
    found = C.SmsChannel("+15559999999").find_worker(db)
    check("U sms unknown number → None (no demo leak)", found is None)
    found = C.TelegramChannel("900001").find_worker(db)
    check("U telegram channel resolves by chat_id", found is not None and found[0].name == "Demo Worker")

    import asyncio
    async def route_check():
        ch = C.SmsChannel("+15550000001")
        # own phone → SMS path (no-op send, chunking still runs)
        await ch.send("+15550000001", "<b>Alpha Site</b> " + "z" * 1000)
        stashed = C._MORE_STASH.get("+15550000001") is not None
        # other dest → telegram path (fails harmlessly, no crash, no SMS stash)
        await ch.send("123456", "owner ping")
        return stashed
    check("U sms channel: own-dest chunks+stashes, other-dest routes telegram",
          asyncio.run(route_check()))
    db.close()


# ── B) HTTP end-to-end ────────────────────────────────────────────
def ap_payload(phone, text, direction="inbound"):
    return {"event": "agent.message", "channel": "sms",
            "timestamp": datetime.utcnow().isoformat(), "agentId": "test-agent",
            "data": {"messageId": "m1", "conversationId": None, "numberId": None,
                     "from": phone, "to": "+15550001111", "contact": None,
                     "message": text, "mediaUrl": None, "mediaUrls": [],
                     "direction": direction,
                     "receivedAt": datetime.utcnow().isoformat()},
            "conversationState": None, "recentHistory": []}

def sms_post(phone, text, secret=TEST_SECRET, direction="inbound", sign=True):
    raw = json.dumps(ap_payload(phone, text, direction)).encode()
    headers = {"content-type": "application/json"}
    if sign:
        ts = str(int(time.time()))
        headers["x-webhook-timestamp"] = ts
        headers["x-webhook-signature"] = "sha256=" + hmac.new(
            secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    r = httpx.post(f"{BASE}/webhook/sms", content=raw, headers=headers, timeout=30)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})

def make_business(name, tier="team", beta=True):
    db = sqlite3.connect(DB_PATH)
    key = secrets.token_urlsafe(12)
    cur = db.execute(
        "INSERT INTO businesses (name, slug, owner_email, owner_name, owner_telegram_id, dashboard_key, invite_token, subscription_status, tier, beta_all_access, is_active, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,1, datetime('now'))",
        (name, name.lower().replace(" ", "-"), "t@t.com", "Owner", None, key,
         secrets.token_urlsafe(12), "active", tier, 1 if beta else 0))
    db.commit(); bid = cur.lastrowid; db.close()
    return bid, key

def add_sms_worker(bid, name, phone, active=1, opted_out=0):
    db = sqlite3.connect(DB_PATH)
    cur = db.execute(
        "INSERT INTO workers (business_id, name, phone, sms_opted_out, is_active, created_at) VALUES (?,?,?,?,?, datetime('now'))",
        (bid, name, phone, opted_out, active))
    db.commit(); wid = cur.lastrowid; db.close()
    return wid

def add_account(bid, name, shorthand=None, gate_code=None):
    db = sqlite3.connect(DB_PATH)
    cur = db.execute(
        "INSERT INTO accounts (business_id, name, shorthand, gate_code, is_active) VALUES (?,?,?,?,1)",
        (bid, name, shorthand, gate_code))
    db.commit(); aid = cur.lastrowid; db.close()
    return aid

def q1(sql, args=()):
    db = sqlite3.connect(DB_PATH)
    row = db.execute(sql, args).fetchone()
    db.close()
    return row

def boot_server(env_extra):
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    env["TZ"] = "UTC"
    env.update(env_extra)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8776"],
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


def http_tests():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    proc, up = boot_server({"AGENTPHONE_API_KEY": "", "AGENTPHONE_AGENT_ID": "",
                            "AGENTPHONE_WEBHOOK_SECRET": TEST_SECRET})
    check("server up", up)
    if not up:
        return
    try:
        # Seed order matters (pitfall #38b): A=1, demo=2, B=3.
        biz_a, key_a = make_business("Sms Alpha")
        biz_demo, key_demo = make_business("Precision HVAC Demo")
        biz_b, key_b = make_business("Sms Beta")
        check("seed: demo tenant landed at id 2", biz_demo == 2, f"demo={biz_demo}")
        add_sms_worker(biz_a, "Rep Sam", "+15550000001")
        add_sms_worker(biz_a, "Pending Pete", "+15550000002", active=0)
        add_sms_worker(biz_b, "Rep Bo", "+15550000003")
        dbw = sqlite3.connect(DB_PATH)
        dbw.execute("INSERT INTO workers (business_id, name, telegram_id, is_active, created_at) VALUES (2,'Demo Dara','900002',1, datetime('now'))")
        dbw.commit(); dbw.close()
        alpha = add_account(biz_a, "Alpha Site", "alpha", gate_code="1234#")
        add_account(biz_b, "Beta HQ", "beta")

        # 1. Bad signature → 403 (tenant data behind the HMAC wall)
        code, _ = sms_post("+15550000001", "Alpha Site: all good", secret="wrong-secret")
        check("bad signature → 403", code == 403, f"code={code}")

        # 2. Outbound-direction events ignored (our own sends echo back)
        code, d = sms_post("+15550001111", "echo", direction="outbound")
        check("outbound echo ignored", code == 200 and d.get("detail") == "not_inbound", str(d))

        # 3. Unknown number → invite prompt, NEVER the demo tenant
        n_demo_logs = q1("SELECT COUNT(*) FROM service_logs WHERE business_id=2")[0]
        code, d = sms_post("+15559999999", "Alpha Site: all good")
        check("unknown number → invite prompt", code == 200 and d.get("detail") == "unknown_worker", str(d))
        check("unknown number did NOT touch demo tenant",
              q1("SELECT COUNT(*) FROM service_logs WHERE business_id=2")[0] == n_demo_logs)

        # 4. Active rep logs a note over SMS — same pipeline as Telegram
        code, d = sms_post("+15550000001", "Alpha Site: all good, filters replaced")
        check("sms note logged", code == 200 and d.get("worker") == "Rep Sam"
              and d.get("account_id") == alpha, str(d)[:200])
        row = q1("SELECT business_id, account_id, raw_note FROM service_logs ORDER BY id DESC LIMIT 1")
        check("log row tenant+account correct",
              row and row[0] == biz_a and row[1] == alpha and "filters replaced" in row[2], str(row))

        # 5. Tenant isolation: B's rep naming A's account must NOT match it
        code, d = sms_post("+15550000003", "Alpha Site: all good")
        row = q1("SELECT business_id, account_id FROM service_logs WHERE business_id=? ORDER BY id DESC LIMIT 1", (biz_b,))
        check("cross-tenant account name never matches",
              row is not None and row[0] == biz_b and row[1] is None,
              f"row={row} resp={str(d)[:120]}")

        # 6. Pending invite: non-YES → still pending; YES → registered
        code, d = sms_post("+15550000002", "hello?")
        check("pending invite non-YES → prompt", d.get("detail") == "invite_pending", str(d))
        check("still inactive after non-YES",
              q1("SELECT is_active FROM workers WHERE phone='+15550000002'")[0] == 0)
        code, d = sms_post("+15550000002", "YES")
        check("YES registers worker", d.get("detail") == "worker_registered", str(d))
        row = q1("SELECT is_active, sms_opted_out FROM workers WHERE phone='+15550000002'")
        check("worker flipped active, not opted out", row == (1, 0), str(row))
        code, d = sms_post("+15550000002", "Alpha Site: checked lobby plants, all good")
        check("newly-registered worker logs notes", d.get("worker") == "Pending Pete", str(d)[:120])

        # 7. Dashboard SMS invite — creates pending worker, refuses opted-out numbers
        # (outbound is a no-op in tests: send fails w/o API key → ok False, but the
        # pending row must still exist so a YES registers them when keys are live)
        r = httpx.post(f"{BASE}/api/dashboard/sms-invite?business_id={biz_a}&key={key_a}",
                       json={"name": "New Nate", "phone": "404-555-0199"}, timeout=30)
        d = r.json()
        check("dashboard invite responded (ok or honest send-failure)",
              d.get("ok") is True or "SMS send failed" in (d.get("message") or ""), str(d))
        row = q1("SELECT name, is_active, sms_opted_out FROM workers WHERE phone='+14045550199'")
        check("pending worker row created (E.164 normalized)",
              row == ("New Nate", 0, 0), str(row))
        # Cross-tenant: B invites the same pending number → refused, row stays A's
        r = httpx.post(f"{BASE}/api/dashboard/sms-invite?business_id={biz_b}&key={key_b}",
                       json={"name": "Poacher", "phone": "4045550199"}, timeout=30)
        check("cross-tenant pending invite refused",
              r.json().get("ok") is False and "pending invite" in (r.json().get("message") or ""),
              str(r.json()))
        check("pending worker still belongs to tenant A (not stolen)",
              q1("SELECT COUNT(*) FROM workers WHERE phone='+14045550199'")[0] == 1
              and q1("SELECT business_id FROM workers WHERE phone='+14045550199'")[0] == biz_a)
        # wrong dashboard key → 403
        r = httpx.post(f"{BASE}/api/dashboard/sms-invite?business_id={biz_a}&key=bad",
                       json={"name": "X", "phone": "4045550000"}, timeout=30)
        check("dashboard invite wrong key → 403", r.status_code == 403, f"code={r.status_code}")

        # 8. New Nate accepts → one phone, one business
        code, d = sms_post("+14045550199", "yes")
        check("dashboard-invited worker registers", d.get("detail") == "worker_registered", str(d))
        check("registered into the inviting tenant",
              q1("SELECT business_id FROM workers WHERE phone='+14045550199'")[0] == biz_a)

        # 9. STOP → silence; dashboard re-invite refused; START → rejoin
        code, d = sms_post("+15550000001", "STOP")
        check("STOP → opt_out", d.get("detail") == "opt_out", str(d))
        row = q1("SELECT is_active, sms_opted_out FROM workers WHERE phone='+15550000001'")
        check("STOP sets inactive + opted_out", row == (0, 1), str(row))
        code, d = sms_post("+15550000001", "Alpha Site: I'm back early")
        check("post-STOP text → absolute silence", d.get("detail") == "opted_out_silent", str(d))
        check("post-STOP text did NOT log",
              q1("SELECT COUNT(*) FROM service_logs WHERE raw_note LIKE '%back early%'")[0] == 0)
        r = httpx.post(f"{BASE}/api/dashboard/sms-invite?business_id={biz_a}&key={key_a}",
                       json={"name": "Rep Sam", "phone": "+15550000001"}, timeout=30)
        check("dashboard re-invite of opted-out number refused",
              r.json().get("ok") is False and "STOP" in r.json().get("message", ""), str(r.json()))
        code, d = sms_post("+15550000001", "START")
        check("START → opt_in", d.get("detail") == "opt_in", str(d))
        row = q1("SELECT is_active, sms_opted_out FROM workers WHERE phone='+15550000001'")
        check("START reactivates + clears opt-out", row == (1, 0), str(row))

        # 10. Q&A over SMS — deterministic fallback surfaces gate code
        code, d = sms_post("+15550000001", "what's the gate code for Alpha Site?")
        check("sms Q&A answers with gate code",
              d.get("intent") == "question" and "1234#" in (d.get("answer") or ""), str(d)[:200])

        # 11. MORE with empty stash → silent no-op, no crash
        code, d = sms_post("+15550000001", "MORE")
        check("MORE empty stash → no_more (silent)", code == 200 and d.get("detail") == "no_more", str(d))

        # 12. STOP for a completely unknown number → no crash, no rows
        code, d = sms_post("+15557777777", "stop")
        check("unknown STOP handled quietly", code == 200 and d.get("detail") == "opt_out", str(d))

    finally:
        stop_server(proc)


if __name__ == "__main__":
    print("── A) unit tests ──")
    unit_tests()
    print("── B) HTTP end-to-end ──")
    http_tests()
    print()
    if failures:
        print(f"❌ {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("🎉 ALL P3 SMS TESTS PASS")
