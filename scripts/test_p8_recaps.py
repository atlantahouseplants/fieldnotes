#!/usr/bin/env python3
"""
P8 client-recaps test suite. Layers:

  A) In-process unit tests — parsers, safety filter (jargon/money/verbatim),
     resolve_account, batching/merge, draft hold-on-failure, approval flow
     (✓/✗/edit) with stubbed LLM + email. Temp sqlite, no server.
  B) HTTP end-to-end — local uvicorn (port 8774) + temp sqlite, three phases:
       Phase A: FIELDNOTES_RECAP_STUB=clean text + FIELDNOTES_EMAIL_STUB=1
                → full loop: enable → rep logs → draft → owner ✓/✗/edit → sent.
                Plus batching, gating, isolation, dashboard, summary.
       Phase B: stub returns JARGON → safety filter trips → held, nothing sent.
       Phase C: no stub, no LLM keys → rewrite fails → held (never raw).
  C) Demo seed idempotency — recap-enabled account at a Geoff-controlled address.

THE hard rule under test everywhere: the raw worker note NEVER reaches a
client — no raw fallback anywhere in the pipeline.

Production data is never touched. TZ pinned to UTC (mirrors Railway prod).
"""
import asyncio, json, os, secrets, sqlite3, subprocess, sys, time
from datetime import datetime

import httpx

BASE = "http://127.0.0.1:8774"
DB_PATH = "/tmp/p8_recaps_test.db"
UNIT_DB = "/tmp/p8_recaps_unit.db"
SEED_DB = "/tmp/p8_recaps_seed.db"
REPO = "/home/wallg/fieldnotes"
sys.path.insert(0, REPO)

failures = []
def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name} {detail}")
    if not cond:
        failures.append(name)

CLEAN_STUB = ("Your service was completed today. All plants were watered and "
              "groomed, and two plants near the lobby are doing well.")
JARGON_STUB = "We were milking the snake plants again and barely watered."


# ── A) unit tests (in-process) ────────────────────────────────────
def unit_tests():
    if os.path.exists(UNIT_DB):
        os.remove(UNIT_DB)
    os.environ["DATABASE_URL"] = f"sqlite:///{UNIT_DB}"
    from backend.models import (init_db, SessionLocal, Business, Account,
                                Worker, ServiceLog, RecapLog)
    from backend.services import recaps as R

    init_db()

    # ── parsers ──
    check("U on-intent basic", R.parse_recaps_on("Recaps on for Smith Office: jane@smith.com")
          == ("Smith Office", "jane@smith.com"))
    check("U on-intent singular+trailing dot",
          R.parse_recaps_on("recap on for Delta Hotel: ops@delta.io.") == ("Delta Hotel", "ops@delta.io"))
    check("U on-intent bad email → None", R.parse_recaps_on("recaps on for Smith: not-an-email") is None)
    check("U on-intent no match", R.parse_recaps_on("Smith Office: all good") is None)
    check("U off-intent", R.parse_recaps_off("Recaps off for Smith Office") == "Smith Office")
    check("U off-intent no match", R.parse_recaps_off("recaps on for Smith: a@b.co") is None)
    check("U malformed on-intent caught", R.malformed_recaps_on("recaps on for Smith: not-an-email")
          and R.malformed_recaps_on("Recaps on for Smith Office:"))
    check("U malformed: good cmd / plain note not flagged",
          not R.malformed_recaps_on("recaps on for Smith: a@b.co")
          and not R.malformed_recaps_on("Smith Office: watered everything"))
    check("U list intents", R.recaps_list_intent("which clients get recaps?")
          and R.recaps_list_intent("recaps list") and R.recaps_list_intent("recap status"))
    check("U list non-match", not R.recaps_list_intent("recaps on for Smith: a@b.co")
          and not R.recaps_list_intent("Smith Office: watered"))
    check("U edit parse", R.parse_edit("edit: Your plants were serviced today.") == "Your plants were serviced today.")
    check("U edit multiline", R.parse_edit("Edit: line one\nline two") == "line one\nline two")
    check("U edit empty → None", R.parse_edit("edit:") is None)
    check("U edit non-match", R.parse_edit("edited the lobby plants") is None)

    # ── safety filter — the P0 backstop ──
    ok, _ = R.passes_safety_filter(CLEAN_STUB, "smith office: watered everything, milking the zz plant")
    check("U filter clean passes", ok)
    ok, why = R.passes_safety_filter("We were milking the snake plants.", "raw")
    check("U filter jargon blocked", not ok and why == "banned:milking", why)
    ok, why = R.passes_safety_filter("Visit cost $50 in supplies.", "raw")
    check("U filter money blocked", not ok and why == "money", why)
    ok, why = R.passes_safety_filter("That will be 50 dollars extra.", "raw")
    check("U filter money-words blocked", not ok and why == "money", why)
    raw = "smith office: watered all plants and checked the lobby fig tree carefully today"
    ok, why = R.passes_safety_filter(
        "Watered all plants and checked the lobby fig tree carefully today, all good.", raw)
    check("U filter verbatim leak blocked", not ok and why == "verbatim", why)
    ok, why = R.passes_safety_filter("   ", "raw")
    check("U filter empty blocked", not ok and why == "empty", why)

    # ── DB-backed: resolve/enable/batching ──
    db = SessionLocal()
    biz = Business(name="Unit Biz", slug="unit-biz", owner_email="o@o.com",
                   owner_name="O", tier="team", is_active=True)
    db.add(biz); db.commit(); db.refresh(biz)
    gated = Business(name="Solo Biz", slug="solo-biz", owner_email="s@s.com",
                     owner_name="S", tier="solo", beta_all_access=False, is_active=True)
    db.add(gated); db.commit(); db.refresh(gated)
    w = Worker(business_id=biz.id, name="Unit Rep", telegram_id="u1", is_active=True)
    db.add(w); db.commit(); db.refresh(w)
    smith = Account(business_id=biz.id, name="Smith Office", shorthand="office", is_active=True)
    tower = Account(business_id=biz.id, name="Smith Tower", shorthand="tower", is_active=True)
    db.add_all([smith, tower]); db.commit(); db.refresh(smith); db.refresh(tower)

    acct, err = R.resolve_account(db, biz.id, "smith office")
    check("U resolve exact (case-insens)", acct is not None and acct.id == smith.id and err is None)
    acct, err = R.resolve_account(db, biz.id, "tower")
    check("U resolve shorthand", acct is not None and acct.id == tower.id)
    acct, err = R.resolve_account(db, biz.id, "smith tow")
    check("U resolve fuzzy prefix", acct is not None and acct.id == tower.id)
    acct, err = R.resolve_account(db, biz.id, "smith")
    check("U resolve ambiguous", acct is None and err == "ambiguous")
    acct, err = R.resolve_account(db, biz.id, "nowhere")
    check("U resolve not_found", acct is None and err == "not_found")

    acct, err = R.enable_recaps(db, biz.id, "Smith Office", "Jane@Smith.COM")
    check("U enable recaps", err is None and acct.recap_enabled and acct.recap_email == "jane@smith.com")
    acct, err = R.enable_recaps(db, biz.id, "smith", "a@b.co")
    check("U enable ambiguous → error", acct is None and "More than one" in (err or ""))
    acct, err = R.disable_recaps(db, biz.id, "nowhere")
    check("U disable unknown → error", acct is None and "Couldn't find" in (err or ""))

    def mklog(text):
        log = ServiceLog(business_id=biz.id, account_id=smith.id, worker_id=w.id,
                         raw_note=text, parsed_status="all_good", processing_time_ms=0)
        db.add(log); db.commit(); db.refresh(log)
        return log

    # plan_for_log: recap off account → None
    l0 = mklog("smith office: quick check, all fine")
    check("U plan skips recap-off account", R.plan_for_log(db, biz, l0, tower) is None)

    # gated tenant with a pre-enabled account (e.g. pre-downgrade state) → held [GATED:recap]
    gacct = Account(business_id=gated.id, name="Gated Office", is_active=True,
                    recap_enabled=True, recap_email="g@g.co")
    db.add(gacct); db.commit(); db.refresh(gacct)
    gw = Worker(business_id=gated.id, name="G Rep", telegram_id="g1", is_active=True)
    db.add(gw); db.commit(); db.refresh(gw)
    glog = ServiceLog(business_id=gated.id, account_id=gacct.id, worker_id=gw.id,
                      raw_note="gated: did the rounds", parsed_status="all_good", processing_time_ms=0)
    db.add(glog); db.commit(); db.refresh(glog)
    check("U plan gated → no draft returned", R.plan_for_log(db, gated, glog, gacct) is None)
    row = db.query(RecapLog).filter(RecapLog.business_id == gated.id).first()
    check("U gated telemetry row held+[GATED:recap]",
          row is not None and row.status == "held" and row.client_text == "[GATED:recap]", str(row and (row.status, row.client_text)))

    # happy path: draft created, then a second log inside the window merges
    l1 = mklog("smith office: watered everything, looking good")
    r1 = R.plan_for_log(db, biz, l1, smith)
    check("U plan creates drafting row", r1 is not None and r1.status == "drafting")
    check("U plan source ids [l1]", json.loads(r1.source_log_ids) == [l1.id])
    l2 = mklog("smith office: forgot to mention, rotated the lobby fig")
    r2 = R.plan_for_log(db, biz, l2, smith)
    check("U plan merges into same row", r2.id == r1.id and json.loads(r2.source_log_ids) == [l1.id, l2.id])
    check("U merge combines source text", "rotated the lobby fig" in (r2.source_text or ""))
    n_rows = db.query(RecapLog).filter(RecapLog.business_id == biz.id).count()
    check("U merge keeps ONE row", n_rows == 1, f"rows={n_rows}")

    # ── draft_and_notify: clean → pending; jargon → held; None → held ──
    pings = []
    async def fake_send(chat_id, text):
        pings.append((chat_id, text))
        return {"ok": True}
    biz.owner_telegram_id = "owner-tg"; db.commit()

    async def run_drafts():
        # jargon rewrite → held, client_text wiped
        orig = R.rewrite_client_safe
        R.rewrite_client_safe = lambda *a, **k: asyncio.sleep(0, result=JARGON_STUB)
        await R.draft_and_notify(db, biz, r2, fake_send)
        R.rewrite_client_safe = orig
        db.refresh(r2)
        held_jargon = (r2.status == "held" and r2.client_text is None)

        # LLM total failure → held (never raw)
        R.rewrite_client_safe = lambda *a, **k: asyncio.sleep(0, result=None)
        await R.draft_and_notify(db, biz, r2, fake_send)
        R.rewrite_client_safe = orig
        db.refresh(r2)
        held_fail = (r2.status == "held" and r2.client_text is None)

        # clean rewrite → pending_approval + owner ping
        R.rewrite_client_safe = lambda *a, **k: asyncio.sleep(0, result=CLEAN_STUB)
        await R.draft_and_notify(db, biz, r2, fake_send)
        R.rewrite_client_safe = orig
        db.refresh(r2)
        pending = (r2.status == "pending_approval" and r2.client_text == CLEAN_STUB)
        return held_jargon, held_fail, pending

    held_jargon, held_fail, pending = asyncio.run(run_drafts())
    check("U draft jargon → held, nothing questionable stored", held_jargon)
    check("U draft LLM-fail → held", held_fail)
    check("U draft clean → pending_approval", pending)
    check("U owner pinged (never rep)", pings and all(c == "owner-tg" for c, _ in pings), str(len(pings)))

    # ── handle_owner_reply: ✓ / ✗ / edit / banned-edit ──
    sent_box = []
    async def fake_send_recap(db_, biz_, recap_, account_, text_):
        sent_box.append(text_)
        return {"ok": True, "provider": "stub"}

    async def run_approvals():
        orig_sr = R.send_recap
        R.send_recap = fake_send_recap
        out = {}
        # ✓ sends
        res = await R.handle_owner_reply(db, biz, "yes", fake_send, "owner-tg")
        db.refresh(r2)
        out["yes"] = (res and res.get("intent") == "recap_sent"
                      and r2.status == "sent" and r2.sent_at is not None
                      and sent_box[-1] == CLEAN_STUB)

        # fresh pending → ✗ skips
        l3 = mklog("smith office: weekly visit done")
        r3 = R.plan_for_log(db, biz, l3, smith)
        r3.client_text, r3.status = CLEAN_STUB, "pending_approval"; db.commit()
        res = await R.handle_owner_reply(db, biz, "no", fake_send, "owner-tg")
        db.refresh(r3)
        out["no"] = (res and res.get("intent") == "recap_skipped" and r3.status == "skipped")

        # fresh pending → edit sends the owner's words
        l4 = mklog("smith office: swapped two planters")
        r4 = R.plan_for_log(db, biz, l4, smith)
        r4.client_text, r4.status = CLEAN_STUB, "pending_approval"; db.commit()
        res = await R.handle_owner_reply(db, biz, "edit: Your planters were refreshed today.", fake_send, "owner-tg")
        db.refresh(r4)
        out["edit"] = (res and res.get("intent") == "recap_sent"
                       and r4.status == "sent" and r4.client_text == "Your planters were refreshed today."
                       and sent_box[-1] == "Your planters were refreshed today.")

        # fresh pending → banned edit → held, NOT sent
        l5 = mklog("smith office: more work")
        r5 = R.plan_for_log(db, biz, l5, smith)
        r5.client_text, r5.status = CLEAN_STUB, "pending_approval"; db.commit()
        before = len(sent_box)
        res = await R.handle_owner_reply(db, biz, "edit: we were milking the ferns", fake_send, "owner-tg")
        db.refresh(r5)
        out["banned_edit"] = (res and res.get("intent") == "recap_held"
                              and r5.status == "held" and len(sent_box) == before)

        # nothing pending → None (caller falls through). r5 is HELD and held
        # recaps stay owner-actionable (rescue via edit) — resolve it first.
        r5.status = "skipped"; db.commit()
        out["none_pending"] = (await R.handle_owner_reply(db, biz, "yes", fake_send, "owner-tg") is None)

        # non-yes/no/edit with pending → None (doesn't swallow normal notes)
        l6 = mklog("smith office: another visit")
        r6 = R.plan_for_log(db, biz, l6, smith)
        r6.client_text, r6.status = CLEAN_STUB, "pending_approval"; db.commit()
        out["random_text_passthrough"] = (
            await R.handle_owner_reply(db, biz, "what's the route tomorrow?", fake_send, "owner-tg") is None)
        R.send_recap = orig_sr
        return out

    out = asyncio.run(run_approvals())
    check("U ✓ sends + stamps sent_at", out["yes"])
    check("U ✗ skips", out["no"])
    check("U edit sends owner text", out["edit"])
    check("U banned edit → held, never sent", out["banned_edit"])
    check("U no pending → passthrough", out["none_pending"])
    check("U non-answer text → passthrough", out["random_text_passthrough"])

    # ── email template: branded + opt-out, no banned content ──
    html = R.recap_email_html("Precision HVAC", "Riverside Office Park", "Jul 22, 2026", CLEAN_STUB)
    check("U template branded + opt-out",
          "Precision HVAC" in html and "Riverside Office Park" in html
          and "stop" in html.lower() and "FieldNotes" in html)
    ok, _ = R.passes_safety_filter(html, "raw note text")
    check("U template passes own filter", ok)

    db.close()


# ── B) HTTP end-to-end ────────────────────────────────────────────
SECRET_HDR = {}
def tg_update(chat_id, text):
    return {"update_id": 1, "message": {"message_id": 1,
            "chat": {"id": chat_id, "first_name": "T"}, "text": text,
            "from": {"id": chat_id}}}

def make_business(name, owner_tg=None, tier="team", beta=True):
    db = sqlite3.connect(DB_PATH)
    key = secrets.token_urlsafe(12)
    cur = db.execute(
        "INSERT INTO businesses (name, slug, owner_email, owner_name, owner_telegram_id, dashboard_key, invite_token, subscription_status, tier, beta_all_access, is_active, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,1, datetime('now'))",
        (name, name.lower().replace(" ", "-"), "t@t.com", "Owner", owner_tg, key,
         secrets.token_urlsafe(12), "active", tier, 1 if beta else 0))
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
    cur = db.execute("INSERT INTO accounts (business_id, name, shorthand, is_active, recap_enabled) VALUES (?,?,?,1,0)",
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

def boot_server(env_extra):
    # Empty-string the LLM keys (don't delete): main.py's load_dotenv re-reads
    # .env and would silently re-hydrate deleted keys, but it never overrides
    # vars already present in the environment.
    env = {k: v for k, v in os.environ.items()
           if k not in ("FIELDNOTES_RECAP_STUB", "FIELDNOTES_EMAIL_STUB")}
    env["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
    env["TZ"] = "UTC"
    env.update(env_extra)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8774"],
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


def phase_a():
    """Clean stub + email stub → the full happy-path loop."""
    proc, up = boot_server({"FIELDNOTES_RECAP_STUB": CLEAN_STUB, "FIELDNOTES_EMAIL_STUB": "1"})
    check("server up (phase A)", up)
    if not up:
        return
    try:
        OWNER_TG, REP_TG = 800099, 800001
        biz_a, key_a = make_business("Recap Alpha", owner_tg=str(OWNER_TG))
        biz_b, key_b = make_business("Recap Beta", owner_tg="800777")
        add_worker(biz_a, "Rep Ray", REP_TG)
        add_worker(biz_a, "Owner Olive", OWNER_TG)
        add_worker(biz_b, "Owner Bob", 800777)
        smith = add_account(biz_a, "Smith Office", "office")
        add_account(biz_a, "Smith Tower", "tower")
        add_account(biz_b, "Beta Plaza", "beta")

        # 1. Rep tries to enable → deflection
        code, d = wh(REP_TG, "recaps on for Smith Office: jane@smith.com")
        check("rep enable → not_owner", d.get("intent") == "recap_setup" and d.get("error") == "not_owner", str(d))
        check("rep enable changed nothing",
              q1("SELECT recap_enabled FROM accounts WHERE id=?", (smith,))[0] == 0)

        # 2. Owner enables → row updated
        code, d = wh(OWNER_TG, "Recaps on for Smith Office: Jane@Smith.com")
        check("owner enable", d.get("intent") == "recap_setup" and d.get("enabled") is True, str(d))
        row = q1("SELECT recap_enabled, recap_email FROM accounts WHERE id=?", (smith,))
        check("enable persisted (email lowercased)", row == (1, "jane@smith.com"), str(row))

        # 3. Bad email / unknown / ambiguous
        n_logs_before = q1("SELECT COUNT(*) FROM service_logs WHERE business_id=?", (biz_a,))[0]
        code, d = wh(OWNER_TG, "recaps on for Smith Office: not-an-email")
        check("bad email → plain-words error, not a note",
              d.get("intent") == "recap_setup" and d.get("error") == "bad_email", str(d)[:200])
        check("bad email did NOT log a service note",
              q1("SELECT COUNT(*) FROM service_logs WHERE business_id=?", (biz_a,))[0] == n_logs_before)
        check("bad email changed nothing",
              q1("SELECT recap_email FROM accounts WHERE id=?", (smith,))[0] == "jane@smith.com")
        code, d = wh(OWNER_TG, "recaps on for Nowhereville: a@b.co")
        check("unknown account → plain error", d.get("intent") == "recap_setup" and "Couldn't find" in (d.get("error") or ""), str(d))
        code, d = wh(OWNER_TG, "recaps on for Smith: a@b.co")
        check("ambiguous → plain error", "More than one" in (d.get("error") or ""), str(d))

        # 4. "which clients get recaps?" — owner and rep both get the list
        code, d = wh(OWNER_TG, "which clients get recaps?")
        check("list intent (owner)", d.get("intent") == "recaps_list" and d.get("count") == 1, str(d))
        code, d = wh(REP_TG, "recaps list")
        check("list intent (rep allowed)", d.get("intent") == "recaps_list" and d.get("count") == 1, str(d))

        # 5. Rep logs at Smith Office → draft pending_approval with STUB text (never raw)
        code, d = wh(REP_TG, "Smith Office: watered everything, milking the tall zz near the stairs")
        check("log at recap account ok", code == 200 and "status" in d, str(d)[:120])
        row = q1("SELECT status, client_text, source_log_ids FROM recap_log WHERE business_id=? ORDER BY id DESC LIMIT 1", (biz_a,))
        check("draft pending_approval", row is not None and row[0] == "pending_approval", str(row))
        check("client_text is the clean rewrite", row and row[1] == CLEAN_STUB, str(row))
        check("client_text is NOT the raw note", row and "milking" not in (row[1] or ""))
        recap_id = q1("SELECT MAX(id) FROM recap_log WHERE business_id=?", (biz_a,))[0]

        # 6. Summary shows the pending recap
        r = httpx.get(f"{BASE}/summary/today?business_id={biz_a}&key={key_a}", timeout=30)
        check("summary counts pending recap", r.json().get("recaps_pending") == 1, str(r.json())[:150])

        # 7. Batching: second note inside the visit window merges — ONE row, two logs
        code, d = wh(REP_TG, "Smith Office: forgot to mention, rotated the lobby fig")
        row = q1("SELECT status, source_log_ids, source_text FROM recap_log WHERE id=?", (recap_id,))
        check("second note merged (2 source ids)", row and len(json.loads(row[1])) == 2, str(row))
        check("merge re-drafted to pending", row and row[0] == "pending_approval")
        n = q1("SELECT COUNT(*) FROM recap_log WHERE business_id=? AND account_id=?", (biz_a, smith))[0]
        check("still ONE recap row for the visit", n == 1, f"n={n}")
        check("source text combines both notes", row and "rotated the lobby fig" in (row[2] or ""))

        # 8. Owner ✓ → sent (email stubbed)
        code, d = wh(OWNER_TG, "yes")
        check("owner YES → recap_sent", d.get("intent") == "recap_sent", str(d))
        row = q1("SELECT status, sent_at, client_text FROM recap_log WHERE id=?", (recap_id,))
        check("row sent + stamped", row and row[0] == "sent" and row[1] is not None, str(row))
        check("sent text = approved rewrite", row and row[2] == CLEAN_STUB)

        # 9. Next visit → owner ✗ → skipped
        wh(REP_TG, "Smith Office: monthly deep water done")
        recap2 = q1("SELECT MAX(id) FROM recap_log WHERE business_id=?", (biz_a,))[0]
        check("new visit → new recap row", recap2 != recap_id)
        code, d = wh(OWNER_TG, "no")
        check("owner NO → skipped", d.get("intent") == "recap_skipped", str(d))
        check("row skipped", q1("SELECT status FROM recap_log WHERE id=?", (recap2,))[0] == "skipped")

        # 10. Next visit → owner edit → edited text sends
        wh(REP_TG, "Smith Office: swapped the entry planters")
        recap3 = q1("SELECT MAX(id) FROM recap_log WHERE business_id=?", (biz_a,))[0]
        code, d = wh(OWNER_TG, "edit: Your entry planters were refreshed today — everything looks great.")
        check("owner edit → sent", d.get("intent") == "recap_sent", str(d))
        row = q1("SELECT status, client_text FROM recap_log WHERE id=?", (recap3,))
        check("edited text is what sent",
              row and row[0] == "sent" and row[1] == "Your entry planters were refreshed today — everything looks great.", str(row))

        # 11. Recaps off → no new rows
        code, d = wh(OWNER_TG, "recaps off for Smith Office")
        check("owner disable", d.get("intent") == "recap_setup" and d.get("enabled") is False, str(d))
        wh(REP_TG, "Smith Office: routine visit, all good")
        n = q1("SELECT COUNT(*) FROM recap_log WHERE business_id=?", (biz_a,))[0]
        check("recap-off account → no new recap row", n == 3, f"n={n}")

        # 12. Tenant isolation: Beta owner "yes" never touches Alpha recaps
        code, d = wh(800777, "yes")
        check("beta owner YES not consumed by recaps", d.get("intent") != "recap_sent", str(d)[:120])
        check("zero recap rows for tenant B",
              q1("SELECT COUNT(*) FROM recap_log WHERE business_id=?", (biz_b,))[0] == 0)

        # 13. Tier gate: solo tenant (no beta) — enable attempt gated
        biz_g, key_g = make_business("Gated Gamma", owner_tg="800555", tier="solo", beta=False)
        add_worker(biz_g, "Owner Gwen", 800555)
        gacct = add_account(biz_g, "Gamma Office", "gamma")
        code, d = wh(800555, "recaps on for Gamma Office: g@g.co")
        check("gated enable → upgrade message", d.get("intent") == "recap_setup" and d.get("gated") is True, str(d))
        check("gated enable changed nothing",
              q1("SELECT recap_enabled FROM accounts WHERE id=?", (gacct,))[0] == 0)
        code, d = wh(800555, "which clients get recaps?")
        check("gated list → upgrade message", d.get("gated") is True, str(d))

        # 14. Gated tenant with pre-enabled account (pre-downgrade) → held telemetry, no draft
        db = sqlite3.connect(DB_PATH)
        db.execute("UPDATE accounts SET recap_enabled=1, recap_email='g@g.co' WHERE id=?", (gacct,))
        db.commit(); db.close()
        add_worker(biz_g, "Rep Gus", 800556)
        wh(800556, "Gamma Office: did the full rounds")
        row = q1("SELECT status, client_text FROM recap_log WHERE business_id=?", (biz_g,))
        check("gated trigger → held [GATED:recap] row",
              row is not None and row[0] == "held" and row[1] == "[GATED:recap]", str(row))

        # 15. Dashboard set-recap endpoint
        r = httpx.post(f"{BASE}/api/dashboard/set-recap?business_id={biz_a}&key={key_a}",
                       json={"account_id": smith, "enabled": True, "email": "Lobby@Smith.com"}, timeout=30)
        check("dash set-recap on", r.json().get("ok") is True, r.text[:150])
        check("dash set-recap persisted",
              q1("SELECT recap_enabled, recap_email FROM accounts WHERE id=?", (smith,)) == (1, "lobby@smith.com"))
        r = httpx.post(f"{BASE}/api/dashboard/set-recap?business_id={biz_a}&key={key_a}",
                       json={"account_id": smith, "enabled": True, "email": "bogus"}, timeout=30)
        check("dash bad email rejected", r.json().get("ok") is False, r.text[:150])
        r = httpx.post(f"{BASE}/api/dashboard/set-recap?business_id={biz_a}&key={key_a}",
                       json={"account_id": smith, "enabled": False}, timeout=30)
        check("dash set-recap off", r.json().get("ok") is True
              and q1("SELECT recap_enabled FROM accounts WHERE id=?", (smith,))[0] == 0)
        r = httpx.post(f"{BASE}/api/dashboard/set-recap?business_id={biz_a}&key=WRONG",
                       json={"account_id": smith, "enabled": True, "email": "a@b.co"}, timeout=30)
        check("dash wrong key → 403", r.status_code == 403, str(r.status_code))
        # cross-tenant account id → not found
        r = httpx.post(f"{BASE}/api/dashboard/set-recap?business_id={biz_b}&key={key_b}",
                       json={"account_id": smith, "enabled": True, "email": "a@b.co"}, timeout=30)
        check("dash cross-tenant account → not found", r.json().get("ok") is False, r.text[:120])

        # 16. THE hard rule, suite-wide: no client_text anywhere contains raw-note jargon
        db = sqlite3.connect(DB_PATH)
        leaked = db.execute(
            "SELECT COUNT(*) FROM recap_log WHERE client_text IS NOT NULL "
            "AND client_text != '[GATED:recap]' AND (client_text LIKE '%milking%' OR client_text LIKE '%$%')").fetchone()[0]
        db.close()
        check("no raw jargon in ANY client_text", leaked == 0, f"leaked={leaked}")
    finally:
        stop_server(proc)


def _fresh_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

def _mk_enabled_tenant():
    OWNER_TG, REP_TG = 810099, 810001
    biz, key = make_business("Hold Tenant", owner_tg=str(OWNER_TG))
    add_worker(biz, "Rep Hank", REP_TG)
    add_worker(biz, "Owner Hope", OWNER_TG)
    acct = add_account(biz, "Hold Office", "hold")
    db = sqlite3.connect(DB_PATH)
    db.execute("UPDATE accounts SET recap_enabled=1, recap_email='h@h.co' WHERE id=?", (acct,))
    db.commit(); db.close()
    return biz, REP_TG

def phase_b():
    """Jargon stub → the safety filter must hold the draft; nothing sends."""
    _fresh_db()
    proc, up = boot_server({"FIELDNOTES_RECAP_STUB": JARGON_STUB, "FIELDNOTES_EMAIL_STUB": "1"})
    check("server up (phase B)", up)
    if not up:
        return
    try:
        biz, rep = _mk_enabled_tenant()
        wh(rep, "Hold Office: watered everything, all good")
        row = q1("SELECT status, client_text FROM recap_log WHERE business_id=?", (biz,))
        check("jargon rewrite → held", row is not None and row[0] == "held", str(row))
        check("jargon text NOT stored for send", row and row[1] is None, str(row))
        # owner ✓ on a held recap with no text → stays held, still nothing sent
        code, d = wh(810099, "yes")
        check("✓ on empty held recap → not sent", d.get("intent") != "recap_sent", str(d))
        check("row still not sent", q1("SELECT status FROM recap_log WHERE business_id=?", (biz,))[0] in ("held",))
    finally:
        stop_server(proc)

def phase_c():
    """No stub, no LLM keys → rewrite chain exhausts → held (never raw)."""
    _fresh_db()
    proc, up = boot_server({"FIELDNOTES_EMAIL_STUB": "1",
                            "XAI_API_KEY": "", "DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""})
    check("server up (phase C)", up)
    if not up:
        return
    try:
        biz, rep = _mk_enabled_tenant()
        wh(rep, "Hold Office: full service, plants thriving")
        row = q1("SELECT status, client_text FROM recap_log WHERE business_id=?", (biz,))
        check("LLM failure → held", row is not None and row[0] == "held", str(row))
        check("no raw fallback stored", row and row[1] is None, str(row))
    finally:
        stop_server(proc)

def http_tests():
    _fresh_db()
    phase_a()
    phase_b()
    phase_c()


# ── C) demo seed idempotency ──────────────────────────────────────
def seed_tests():
    for p in (SEED_DB,):
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{SEED_DB}", TZ="UTC")
    setup = (
        "import sys; sys.path.insert(0, %r)\n"
        "from backend.models import init_db, SessionLocal, Business, Account, Worker\n"
        "init_db()\n"
        "db = SessionLocal()\n"
        "db.add(Business(name='A', slug='a', owner_email='a@a', owner_name='A', is_active=True))\n"
        "db.add(Business(name='Precision HVAC', slug='demo', owner_email='d@d', owner_name='D', tier='solo', is_active=True))\n"
        "db.commit()\n"
        "db.add(Account(business_id=2, name='Riverside Office Park', is_active=True))\n"
        "db.add(Worker(business_id=2, name='Demo Dan', telegram_id='d1', is_active=True))\n"
        "db.commit(); db.close()\n"
    ) % REPO
    subprocess.run([sys.executable, "-c", setup], env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for i in (1, 2):
        r = subprocess.run([sys.executable, "scripts/seed_demo_tenant.py"],
                           cwd=REPO, env=env, capture_output=True, text=True)
        check(f"seed run {i} exit 0", r.returncode == 0, r.stderr[-200:] if r.returncode else "")
    db = sqlite3.connect(SEED_DB)
    row = db.execute("SELECT recap_enabled, recap_email FROM accounts WHERE business_id=2 AND name='Riverside Office Park'").fetchone()
    tier = db.execute("SELECT tier FROM businesses WHERE id=2").fetchone()[0]
    db.close()
    check("seed enables recaps at Geoff-controlled address",
          row == (1, "sarah@atlantahouseplant.com"), str(row))
    check("seed lifts demo tenant to team tier (recaps gate)", tier == "team", tier)


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
    seed_tests()
    print()
    if failures:
        print(f"❌ {len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("✅ ALL PASSED")
