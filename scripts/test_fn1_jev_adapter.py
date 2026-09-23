#!/usr/bin/env python3
"""
FN-1 test suite — Jev (TypeSafe System One) parser adapter + shadow mode.

Follows the repo convention (scripts/test_p*.py): plain check() helper,
runnable directly as a script OR collected via pytest (the `test_all`
entry point runs the entire suite and asserts). Run with a venv that has
sqlalchemy/httpx (this repo's own requirements aren't installed
system-wide on this box — use the lead-engine venv, which mirrors
backend/requirements.txt closely enough for this module's needs):

    /home/wallg/ahp-digital/lead-engine/.venv/bin/python scripts/test_fn1_jev_adapter.py
    # or
    /home/wallg/ahp-digital/lead-engine/.venv/bin/python -m pytest scripts/test_fn1_jev_adapter.py

Covers:
  A) jev_client.split_candidate_clauses — clause splitting for the
     "select instead of generate" text-extraction pattern.
  B) jev_client.parse_note_jev — HTTP call shape, response parsing,
     clause-role assembly into issues/supplies/follow_ups/customer_requests,
     model_version logging, error propagation (mocked httpx, no live API calls).
  C) jev_client.get_todays_jev_tokens — daily token rollup from
     parse_shadow_logs (temp sqlite DB).
  D) parser.parse_note dispatcher — PARSER_BACKEND=current is a NO-OP
     (byte-identical behavior to pre-FN1 code, no Jev call attempted),
     PARSER_BACKEND=shadow calls both and logs a ParseShadowLog row but
     ALWAYS returns the current parser's result (zero user-facing
     change) even when Jev errors, PARSER_BACKEND=jev returns Jev's
     result when under the daily token cap and fails closed to the
     current chain when the cap is exceeded.
  E) jev_calibration — Brier score + 10-bin reliability table +
     compute_calibration aggregation (hand-computed synthetic cases).
"""
import asyncio
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/fn1_jev_unit.db")
if os.path.exists("/tmp/fn1_jev_unit.db"):
    os.remove("/tmp/fn1_jev_unit.db")
os.environ["TYPESAFE_API_KEY"] = "test-key-not-real"
os.environ["PARSER_BACKEND"] = "current"
# Force the current chain to hit _basic_parse deterministically (no network).
for k in ("MOONSHOT_API_KEY", "XAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
    os.environ[k] = ""

failures = []
_PYTEST = "pytest" in sys.modules
def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name} {detail}")
    if not cond:
        failures.append(name)
        if _PYTEST:
            raise AssertionError(f"{name}: {detail}")


import httpx
from backend.services.parser import jev_client
from backend.services.parser import jev_calibration
from backend.services.parser import parse_note, PARSER_BACKEND_ENV
from backend.models import Base, engine, SessionLocal, ParseShadowLog, Business, Worker


def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed_biz_worker(db):
    biz = Business(name="Test Co", slug="test-co-fn1", owner_email="o@example.com",
                    owner_name="Owner", is_active=True)
    db.add(biz)
    db.commit()
    db.refresh(biz)
    w = Worker(business_id=biz.id, name="Rep", telegram_id="999", is_active=True)
    db.add(w)
    db.commit()
    db.refresh(w)
    return biz, w


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=None, response=self)


def make_fake_post(status_code, payload):
    async def _fake_post(self, url, headers=None, json=None, **kwargs):
        return FakeResponse(status_code, payload)
    return _fake_post


CANNED_JEV_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "account": {"type": "choice", "choice": "riverside", "confidence": 0.95,
                     "probabilities": {"riverside": 0.95, "oakwood": 0.05, "nomatch": 0.0}},
        "status": {"type": "choice", "choice": "issues_found", "confidence": 0.9,
                    "probabilities": {"all_good": 0.1, "issues_found": 0.9}},
        "supplies_present": {"type": "noul", "noul": 0.8},
        "followups_present": {"type": "noul", "noul": 0.2},
        "customer_requests_present": {"type": "noul", "noul": 0.05},
        "clause_0": {"type": "choice", "choice": "issue", "confidence": 0.9,
                      "probabilities": {"issue": 0.9, "supply": 0.05, "followup": 0.03,
                                         "customer_request": 0.01, "none": 0.01}},
        "clause_1": {"type": "choice", "choice": "supply", "confidence": 0.85,
                      "probabilities": {"issue": 0.05, "supply": 0.85, "followup": 0.05,
                                         "customer_request": 0.03, "none": 0.02}},
    },
    "usage": {"input_tokens": 512, "output_tokens": 61},
}


def _test_split_candidate_clauses():
    clauses = jev_client.split_candidate_clauses(
        "Riverside: ICU running hot, filters need replacing. Monitoring the compressor.")
    check("A1 splits into multiple clauses", len(clauses) >= 2, str(clauses))
    check("A2 no empty clauses", all(c.strip() for c in clauses))
    check("A3 caps at MAX_CLAUSES", len(jev_client.split_candidate_clauses(
        "a. b. c. d. e. f. g. h. i. j.")) <= jev_client.MAX_CLAUSES)

    single = jev_client.split_candidate_clauses("all good")
    check("A4 short note yields at least one clause", len(single) == 1, str(single))


async def _test_parse_note_jev_success(monkeypatch_post):
    monkeypatch_post(200, CANNED_JEV_RESPONSE)
    result = await jev_client.parse_note_jev(
        "Riverside: ICU running hot, filters need replacing. Monitoring the compressor.",
        known_accounts=["riverside", "oakwood"],
    )
    check("B1 account_hint from jev choice", result["account_hint"] == "riverside", result.get("account_hint"))
    check("B2 status from jev choice", result["status"] == "issues_found", result.get("status"))
    check("B3 issues assembled from clause_0 tag", any("ICU" in i or "hot" in i for i in result["issues"]),
          result.get("issues"))
    check("B4 supplies assembled from clause_1 tag", any("compressor" in s.lower() for s in result["supplies"]),
          result.get("supplies"))
    check("B5 jev_input_tokens recorded", result["jev_input_tokens"] == 512, result.get("jev_input_tokens"))
    check("B6 jev_output_tokens recorded", result["jev_output_tokens"] == 61, result.get("jev_output_tokens"))
    check("B7 jev_confidence carries account+status confidence",
          "account" in result["jev_confidence"] and "status" in result["jev_confidence"],
          result.get("jev_confidence"))
    check("B8 processing_time_ms present", isinstance(result.get("processing_time_ms"), int))
    check("B9 model_version read from response `model` field", result["model_version"] == "jev-1.13.0",
          result.get("model_version"))


async def _test_parse_note_jev_missing_model(monkeypatch_post):
    payload = dict(CANNED_JEV_RESPONSE)
    payload.pop("model", None)  # simulate an API response without a version field
    monkeypatch_post(200, payload)
    result = await jev_client.parse_note_jev("all good", known_accounts=["riverside"])
    check("B10 missing model -> model_version is None", result.get("model_version") is None,
          result.get("model_version"))
    check("B11 missing model -> model_requested captured", result.get("model_requested") == jev_client.JEV_MODEL,
          result.get("model_requested"))


async def _test_parse_note_jev_error(monkeypatch_post):
    monkeypatch_post(401, {"error": "unauthorized"})
    raised = False
    try:
        await jev_client.parse_note_jev("note", known_accounts=["riverside"])
    except Exception:
        raised = True
    check("B12 HTTP error propagates as exception", raised)


def _test_get_todays_jev_tokens():
    reset_db()
    db = SessionLocal()
    biz, w = seed_biz_worker(db)
    db.add(ParseShadowLog(business_id=biz.id, worker_id=w.id, raw_note="n1",
                           current_result="{}", jev_result="{}",
                           jev_input_tokens=100, jev_output_tokens=20))
    db.add(ParseShadowLog(business_id=biz.id, worker_id=w.id, raw_note="n2",
                           current_result="{}", jev_result="{}",
                           jev_input_tokens=200, jev_output_tokens=30))
    db.commit()
    total = jev_client.get_todays_jev_tokens(db)
    check("C1 sums input+output tokens for today", total == 350, total)
    db.close()


def _test_calibration_brier():
    # Hand-computable: ((0.8-1)^2 + (0.2-0)^2 + (0.5-1)^2) / 3 = (0.04+0.04+0.25)/3 = 0.11
    b = jev_calibration.brier_score([0.8, 0.2, 0.5], [1, 0, 1])
    check("E1 brier_score matches hand computation", abs(b - 0.11) < 1e-9, b)
    check("E2 brier_score empty -> None", jev_calibration.brier_score([], []) is None)


def _test_reliability_table():
    preds = [0.05, 0.15, 0.15, 0.95, 0.95]
    outcomes = [0, 1, 0, 1, 1]
    table = jev_calibration.reliability_table(preds, outcomes)
    check("E3 reliability table has 10 bins", len(table) == 10, len(table))
    check("E4 bin indices are 0..9", [r["bin"] for r in table] == list(range(10)),
          [r["bin"] for r in table])
    check("E5 bin 0 count == 1", table[0]["count"] == 1, table[0])
    check("E6 bin 1 count == 2", table[1]["count"] == 2, table[1])
    check("E7 bin 9 count == 2", table[9]["count"] == 2, table[9])
    check("E8 total count conserved", sum(r["count"] for r in table) == 5,
          sum(r["count"] for r in table))
    check("E9 bin 1 mean_predicted == 0.15", abs(table[1]["mean_predicted"] - 0.15) < 1e-9,
          table[1]["mean_predicted"])
    check("E10 bin 1 observed_frequency == 0.5", abs(table[1]["observed_frequency"] - 0.5) < 1e-9,
          table[1]["observed_frequency"])


def _test_compute_calibration():
    # 2 notes x 3 Noul = 6 Noul samples; hand-computed Brier below.
    rows = [
        {"supplies_present_prob": 0.8, "stored_supplies_nonempty": True,
         "followups_present_prob": 0.2, "stored_followups_nonempty": False,
         "customer_requests_present_prob": 0.5, "stored_customer_requests_nonempty": True,
         "status_confidence": 0.9, "status_agree": True},
        {"supplies_present_prob": 0.8, "stored_supplies_nonempty": True,
         "followups_present_prob": 0.2, "stored_followups_nonempty": True,
         "customer_requests_present_prob": 0.5, "stored_customer_requests_nonempty": False,
         "status_confidence": 0.6, "status_agree": False},
    ]
    cal = jev_calibration.compute_calibration(rows)
    # Noul: (0.8,1),(0.2,0),(0.5,1),(0.8,1),(0.2,1),(0.5,0)
    #   = (0.04 + 0.04 + 0.25 + 0.04 + 0.64 + 0.25) / 6 = 1.26/6 = 0.21
    check("E11 brier_noul matches hand computation", abs(cal["brier_noul"] - 0.21) < 1e-9,
          cal["brier_noul"])
    # Status: (0.9,1),(0.6,0) = (0.01 + 0.36)/2 = 0.185
    check("E12 brier_status matches hand computation", abs(cal["brier_status"] - 0.185) < 1e-9,
          cal["brier_status"])
    check("E13 noul sample size == 6", cal["noul_sample_size"] == 6, cal["noul_sample_size"])
    check("E14 status sample size == 2", cal["status_sample_size"] == 2, cal["status_sample_size"])
    check("E15 small sample -> provisional note", cal["note"] is not None and "provisional" in cal["note"],
          cal["note"])
    check("E16 reliability table present (10 bins)", len(cal["reliability_table"]) == 10,
          len(cal["reliability_table"]))


async def _test_dispatcher_current_mode_untouched(monkeypatch_post):
    os.environ["PARSER_BACKEND"] = "current"
    called = {"n": 0}
    orig = jev_client.parse_note_jev
    async def spy(*a, **kw):
        called["n"] += 1
        return await orig(*a, **kw)
    jev_client.parse_note_jev = spy
    try:
        result = await parse_note("all good at Riverside")
        check("D1 current mode returns basic-parse shape",
              set(["account_hint", "status", "issues", "supplies"]).issubset(result.keys()))
        check("D2 current mode NEVER calls jev_client", called["n"] == 0, called["n"])
    finally:
        jev_client.parse_note_jev = orig


async def _test_dispatcher_shadow_mode(monkeypatch_post):
    reset_db()
    db = SessionLocal()
    biz, w = seed_biz_worker(db)
    os.environ["PARSER_BACKEND"] = "shadow"
    monkeypatch_post(200, CANNED_JEV_RESPONSE)

    result = await parse_note(
        "Riverside: ICU running hot, filters need replacing.",
        known_accounts=["riverside", "oakwood"],
        db=db, business_id=biz.id, worker_id=w.id,
    )
    # Zero user-facing change: shadow mode returns the CURRENT parser's result.
    current_only = await _run_current_chain_directly("Riverside: ICU running hot, filters need replacing.")
    check("D3 shadow mode returns current parser's result (status)",
          result["status"] == current_only["status"], (result["status"], current_only["status"]))
    check("D4 shadow mode returns current parser's result (account_hint)",
          result["account_hint"] == current_only["account_hint"])

    rows = db.query(ParseShadowLog).all()
    check("D5 shadow mode writes exactly one ParseShadowLog row", len(rows) == 1, len(rows))
    if rows:
        row = rows[0]
        check("D6 logged row carries jev_result", row.jev_result is not None)
        check("D7 logged row carries current_result", row.current_result is not None)
        check("D8 logged row carries jev token usage", row.jev_input_tokens == 512)
        jev_parsed = json.loads(row.jev_result)
        check("D9 account_agree computed", row.account_agree is True or row.account_agree is False)
    db.close()


async def _run_current_chain_directly(note):
    os.environ["PARSER_BACKEND"] = "current"
    return await parse_note(note)


async def _test_dispatcher_shadow_mode_jev_error_still_returns_current(monkeypatch_post):
    reset_db()
    db = SessionLocal()
    biz, w = seed_biz_worker(db)
    os.environ["PARSER_BACKEND"] = "shadow"
    monkeypatch_post(500, {"error": "boom"})

    result = await parse_note("all good", db=db, business_id=biz.id, worker_id=w.id)
    check("D10 shadow mode survives jev error, returns current result",
          result.get("status") == "all_good", result.get("status"))
    rows = db.query(ParseShadowLog).all()
    check("D11 shadow mode logs the jev error, doesn't crash", len(rows) == 1 and rows[0].jev_error is not None)
    db.close()


async def _test_dispatcher_shadow_mode_no_db_context(monkeypatch_post):
    os.environ["PARSER_BACKEND"] = "shadow"
    monkeypatch_post(200, CANNED_JEV_RESPONSE)
    result = await parse_note("all good")  # no db/business_id passed
    check("D12 shadow mode with no db context still returns current result and doesn't crash",
          result.get("status") == "all_good")


async def _test_dispatcher_jev_mode_under_cap(monkeypatch_post):
    reset_db()
    db = SessionLocal()
    biz, w = seed_biz_worker(db)
    os.environ["PARSER_BACKEND"] = "jev"
    os.environ["JEV_DAILY_TOKEN_CAP"] = "1000000"
    monkeypatch_post(200, CANNED_JEV_RESPONSE)
    result = await parse_note("Riverside: ICU running hot, filters need replacing.",
                               known_accounts=["riverside", "oakwood"],
                               db=db, business_id=biz.id, worker_id=w.id)
    check("D13 jev mode under cap returns jev's status", result["status"] == "issues_found", result.get("status"))
    check("D14 jev mode under cap returns jev's account_hint", result["account_hint"] == "riverside")
    db.close()


async def _test_dispatcher_jev_mode_fails_closed_over_cap(monkeypatch_post):
    reset_db()
    db = SessionLocal()
    biz, w = seed_biz_worker(db)
    # Pre-seed usage that already exceeds a tiny cap.
    db.add(ParseShadowLog(business_id=biz.id, worker_id=w.id, raw_note="prior",
                           current_result="{}", jev_result="{}",
                           jev_input_tokens=999999, jev_output_tokens=0))
    db.commit()
    os.environ["PARSER_BACKEND"] = "jev"
    os.environ["JEV_DAILY_TOKEN_CAP"] = "100"
    monkeypatch_post(200, CANNED_JEV_RESPONSE)
    result = await parse_note("all good", db=db, business_id=biz.id, worker_id=w.id)
    check("D15 jev mode fails closed to current parser over cap",
          result.get("status") == "all_good", result.get("status"))
    db.close()
    os.environ["JEV_DAILY_TOKEN_CAP"] = ""


def _monkeypatch_post_factory():
    original = httpx.AsyncClient.post
    def apply(status_code, payload):
        httpx.AsyncClient.post = make_fake_post(status_code, payload)
    def restore():
        httpx.AsyncClient.post = original
    return apply, restore


def run_all():
    _test_split_candidate_clauses()
    _test_calibration_brier()
    _test_reliability_table()
    _test_compute_calibration()

    apply, restore = _monkeypatch_post_factory()
    try:
        asyncio.run(_test_parse_note_jev_success(apply))
        asyncio.run(_test_parse_note_jev_missing_model(apply))
        asyncio.run(_test_parse_note_jev_error(apply))
        _test_get_todays_jev_tokens()
        asyncio.run(_test_dispatcher_current_mode_untouched(apply))
        asyncio.run(_test_dispatcher_shadow_mode(apply))
        asyncio.run(_test_dispatcher_shadow_mode_jev_error_still_returns_current(apply))
        asyncio.run(_test_dispatcher_shadow_mode_no_db_context(apply))
        asyncio.run(_test_dispatcher_jev_mode_under_cap(apply))
        asyncio.run(_test_dispatcher_jev_mode_fails_closed_over_cap(apply))
    finally:
        restore()


def main():
    run_all()
    print()
    if failures:
        print(f"❌ {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    else:
        print("✅ ALL CHECKS PASS")


def test_all():
    """pytest entry point — runs the entire suite (sync + async) and asserts."""
    failures.clear()
    run_all()
    assert not failures, f"FAILURES: {failures}"


if __name__ == "__main__":
    main()
