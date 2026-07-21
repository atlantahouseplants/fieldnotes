#!/usr/bin/env python3
"""
Tenant isolation test for Ask FieldNotes (P1 acceptance criterion).

Plants a secret in tenant A, asks questions from tenant B, asserts the
secret NEVER appears. Also asserts tenant A CAN retrieve its own secret.
Runs against a throwaway temp DB — never touches production data.
"""
import asyncio
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models import Base, engine, SessionLocal, Business, Account, Worker, ServiceLog
from backend.services.qa import answer_question, _gather_context, looks_like_question

SECRET = "SECRET-CODE-99X7"


def setup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    biz_a = Business(name="Tenant A", slug="tenant-a", owner_email="a@x.com", owner_name="A", is_active=True)
    biz_b = Business(name="Tenant B", slug="tenant-b", owner_email="b@x.com", owner_name="B", is_active=True)
    db.add_all([biz_a, biz_b])
    db.flush()
    acct_a = Account(business_id=biz_a.id, name="Alpha Site", shorthand="alpha", is_active=True,
                     notes=f"Gate code {SECRET}. Beware of dog.")
    acct_b = Account(business_id=biz_b.id, name="Beta Site", shorthand="beta", is_active=True,
                     notes="Front desk key under mat.")
    db.add_all([acct_a, acct_b])
    db.flush()
    w_a = Worker(business_id=biz_a.id, name="Alice", telegram_id="111", is_active=True)
    w_b = Worker(business_id=biz_b.id, name="Bob", telegram_id="222", is_active=True)
    db.add_all([w_a, w_b])
    db.flush()
    db.add(ServiceLog(business_id=biz_a.id, account_id=acct_a.id, worker_id=w_a.id,
                      raw_note=f"Alpha: all good, used gate code {SECRET} again",
                      parsed_status="all_good"))
    db.commit()
    return db, biz_a, biz_b, w_a, w_b


async def main():
    db, biz_a, biz_b, w_a, w_b = setup()
    failures = []

    # 1. Tenant B asks for gate code — must NOT leak tenant A's secret
    r = await answer_question(db, biz_b.id, w_b, "what's the gate code?")
    if SECRET in r["answer"]:
        failures.append(f"LEAK: tenant B answer contains tenant A secret: {r['answer']}")
    print(f"[B asks 'gate code'] → {r['answer'][:120]}")

    # 2. Tenant B asks about Alpha by name — cross-tenant name must not resolve
    ctx, matched = _gather_context(db, biz_b.id, "gate code for Alpha Site?")
    if any("Alpha" in (a.get("name") or "") for a in ctx["accounts"]):
        failures.append("LEAK: tenant B context contains tenant A account")
    r2 = await answer_question(db, biz_b.id, w_b, "gate code for Alpha Site?")
    if SECRET in r2["answer"]:
        failures.append(f"LEAK: cross-tenant named lookup returned secret: {r2['answer']}")
    print(f"[B asks 'gate code for Alpha Site?'] → {r2['answer'][:120]}")

    # 3. Tenant B's open-issues digest must not include tenant A actions
    ctx3, _ = _gather_context(db, biz_b.id, "any open issues this week?")
    blob = str(ctx3)
    if SECRET in blob or "Alpha" in blob:
        failures.append("LEAK: tenant B context blob contains tenant A data")

    # 4. Positive control: tenant A CAN get its own secret
    r3 = await answer_question(db, biz_a.id, w_a, "what's the gate code for Alpha?")
    print(f"[A asks own gate code] → {r3['answer'][:160]}")
    # A's context must contain the secret (LLM answer may paraphrase, check context not answer)
    ctx4, _ = _gather_context(db, biz_a.id, "what's the gate code for Alpha?")
    if SECRET not in str(ctx4):
        failures.append("FAIL: tenant A context missing its own account data")

    # 5. Intent heuristic sanity
    assert looks_like_question("gate code for Matsuda?")
    assert looks_like_question("any open issues this week")
    assert looks_like_question("when did we last service Luna")
    assert not looks_like_question("Andersen Windows: all good, replaced filter")
    assert not looks_like_question("Perkins and Will: needs 2 more ferns next visit")

    db.close()
    if failures:
        print("\n❌ FAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("\n✅ TENANT ISOLATION: all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
