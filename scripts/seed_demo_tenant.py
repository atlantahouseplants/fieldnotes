#!/usr/bin/env python3
"""
Seed the demo tenant (business_id=2, Precision HVAC) with rich account data
so "Ask FieldNotes" wows in sales demos: gate codes, contacts, warnings,
and a service history to answer "when did we last..." questions.

Idempotent — safe to re-run. Only touches business_id=2.
"""
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models import SessionLocal, Account, ServiceLog, Worker, Business

DEMO_BIZ = 2
MARKER = "[demo-seed]"

# P8: recap demo — Geoff-controlled address ONLY (sarah@atlantahouseplant.com,
# no trailing 's'). A demo user approving a recap must never email a real person.
RECAP_DEMO_ACCOUNT = "Riverside Office Park"
RECAP_DEMO_EMAIL = "sarah@atlantahouseplant.com"

ACCOUNT_NOTES = {
    "Riverside Office Park": (
        "Gate code #4412 (enter slowly, sensor is touchy). Loading dock B for equipment. "
        "Property manager: Dana Whitfield 404-555-0182. Alarm panel in utility closet, code 7291."
    ),
    "Grand Hotel Downtown": (
        "Use service elevator (key from front desk, ask for Luis 404-555-0143). "
        "No work in lobby before 10am. Rooftop units: hatch code 5521."
    ),
    "Mercy Medical Center": (
        "Badge required — check in at security desk, they hold your ID. "
        "No loud work near ICU (3rd floor). Facility contact: Priya Nair 404-555-0177."
    ),
    "Oakwood Apartments": (
        "Unit 12B has an aggressive dog — call tenant before entering (Marcus 404-555-0199). "
        "Gate clicker in truck #2. Pool gate code 8842."
    ),
}

DEMO_LOGS = [
    ("Riverside Office Park", -21, "Riverside: replaced compressor capacitor on unit 3, all good otherwise.", "all_good", [], []),
    ("Riverside Office Park", -7, "Riverside: quarterly PM done. Belt on unit 1 showing wear — replace next visit.", "follow_up_needed", [], ["Replace belt on unit 1"]),
    ("Grand Hotel Downtown", -14, "Grand Hotel: kitchen exhaust fan vibrating, tightened mounts. Ordered new fan motor.", "issues_found", ["Exhaust fan vibration"], ["Fan motor"]),
    ("Grand Hotel Downtown", -3, "Grand Hotel: installed new fan motor, tested good. GM says kitchen staff happy.", "all_good", [], []),
    ("Mercy Medical Center", -10, "Mercy: filters changed floors 1-4. 3rd floor ICU unit running hot — monitoring.", "issues_found", ["ICU unit running hot"], ["HEPA filters x6"]),
    ("Oakwood Apartments", -5, "Oakwood: fixed leak under unit 12B sink (tenant's dog barked the whole time). All good.", "all_good", [], []),
]


def main():
    db = SessionLocal()
    try:
        accounts = {a.name: a for a in db.query(Account).filter(Account.business_id == DEMO_BIZ).all()}

        updated = 0
        for name, notes in ACCOUNT_NOTES.items():
            a = accounts.get(name)
            if not a:
                print(f"  !! missing demo account: {name}")
                continue
            if MARKER in (a.notes or ""):
                continue
            a.notes = f"{(a.notes or '').strip()} {MARKER} {notes}".strip()
            updated += 1
        db.commit()
        print(f"accounts updated with access notes: {updated}")

        # P8: demo recap loop — one recap-enabled account so a stranger can
        # experience draft → approve → send. Email MUST be a Geoff-controlled
        # address (spec pitfall): recaps from the demo never reach a real client.
        biz = db.query(Business).filter(Business.id == DEMO_BIZ).first()
        if biz and biz.tier != "team":
            biz.tier = "team"   # recaps gate is Team-tier
            db.commit()
            print("demo tenant tier set to team (recaps gate)")
        riverside = accounts.get(RECAP_DEMO_ACCOUNT)
        if riverside and not riverside.recap_enabled:
            riverside.recap_enabled = True
            riverside.recap_email = RECAP_DEMO_EMAIL
            db.commit()
            print(f"recaps enabled for {RECAP_DEMO_ACCOUNT} → {RECAP_DEMO_EMAIL}")
        elif riverside:
            print(f"recaps already enabled for {RECAP_DEMO_ACCOUNT}")

        worker = db.query(Worker).filter(Worker.business_id == DEMO_BIZ, Worker.is_active == True).first()
        existing = db.query(ServiceLog).filter(
            ServiceLog.business_id == DEMO_BIZ, ServiceLog.raw_note.like("%[demo-log]%")
        ).count()
        if existing:
            print(f"demo logs already present ({existing}), skipping")
        else:
            created = 0
            for acct_name, days_ago, note, status, issues, supplies in DEMO_LOGS:
                a = accounts.get(acct_name)
                if not a or not worker:
                    continue
                log = ServiceLog(
                    business_id=DEMO_BIZ,
                    account_id=a.id,
                    worker_id=worker.id,
                    raw_note=f"[demo-log] {note}",
                    parsed_status=status,
                    parsed_issues=json.dumps(issues),
                    parsed_supplies=json.dumps(supplies),
                    parsed_followups=json.dumps([]),
                    parsed_customer_requests=json.dumps([]),
                    timestamp=datetime.utcnow() + timedelta(days=days_ago),
                    processing_time_ms=0,
                )
                db.add(log)
                created += 1
            db.commit()
            print(f"demo service logs created: {created}")

        print("DONE — demo tenant seeded. Try asking: 'gate code for Riverside?' / 'when did we last service Grand Hotel?' / 'any open issues?'")
    finally:
        db.close()


if __name__ == "__main__":
    main()
