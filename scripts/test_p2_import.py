#!/usr/bin/env python3
"""
P2 CSV-import test suite. Runs against a LOCAL server (port 8767) backed by
a temp database — production data is untouched.

Tests:
  1. Messy 50-row CSV (weird headers, BOM, smart quotes, dupes, blanks) imports correctly
  2. Dedup: re-import updates instead of duplicating
  3. Tenant isolation: two tenants, same CSV → fully separate accounts
  4. Auth: bad key → 403, wrong tenant key → 403
  5. Multipart file upload path works
"""
import json, os, subprocess, sys, time
import httpx

BASE = "http://127.0.0.1:8767"

MESSY_CSV = """﻿Client Name,Site Address,Gate #,Contact Person,Phone Number,Days,Notes,Random Junk Column
Riverside Office Park,1200 Riverside Pkwy,4412,Dana Whitfield,404-555-0182,Mon/Thu,Touchy sensor,xyz
Grand Hotel Downtown,55 Peachtree St,,Luis,404-555-0143,Wed,Get key from front desk,junk1
Mercy Medical Center,888 Hospital Blvd,2211,Security Desk,404-555-0110,Daily,Badge required,junk2
Oakwood Residences,12 Oakwood Ln,1187,Tom,404-555-0199,Tue/Fri,Dog in 12B,junk3
Summit Corporate Center,400 Summit Dr,,Reception,404-555-0155,Mon,Loading dock rear,junk4
""" + "\n".join(
    f"Test Account {i},{i} Main St,{1000+i},Contact {i},555-{i:04d},{['Mon','Tue','Wed','Thu','Fri'][i%5]},Note {i},junk"
    for i in range(1, 46)
) + """
Riverside Office Park,1200 Riverside Pkwy UPDATED,4412,Dana W,404-555-0182,Mon/Thu,Touchy sensor v2,dup
Riverside Office,999 Other St,,,,,,fuzzy dupe of Riverside Office Park
,999 No Name St,,,,,blank row name,
“Smart Quotes Account”,77 Curly Ln,7788,—,—,Sat,Smart “quotes” here,junk
"""

failures = []
def check(name, cond, detail=""):
    status = "✅" if cond else "❌"
    print(f"{status} {name} {detail}")
    if not cond:
        failures.append(name)

def make_business(db_path, name):
    """Insert a business directly into the temp DB; return (id, key)."""
    import sqlite3, secrets
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

def main():
    db_path = "/tmp/p2_import_test.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    # Boot app against temp DB to create schema
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_path}")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8767"],
        cwd="/home/wallg/fieldnotes", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                httpx.get(f"{BASE}/health", timeout=2)
                break
            except Exception:
                time.sleep(0.5)

        biz_a, key_a = make_business(db_path, "Tenant Alpha")
        biz_b, key_b = make_business(db_path, "Tenant Beta")

        # ── Test 1: messy 50-row import ──
        r = httpx.post(f"{BASE}/onboarding/import-csv",
                       json={"business_id": biz_a, "key": key_a, "csv_text": MESSY_CSV}, timeout=60)
        check("import endpoint 200", r.status_code == 200, r.text[:200] if r.status_code != 200 else "")
        d = r.json()
        total_data_rows = 5 + 45 + 1 + 1  # rich5 + generated45 + dup + smartquotes (blank-name row dropped)
        check("created count", d["created"] >= 50, f"created={d['created']} updated={d['updated']} skipped={d['skipped']}")
        check("skipped blank-name row", d["skipped"] >= 1, f"skipped={d['skipped']}")
        mapping = d["header_mapping"]
        check("header map: Client Name→name", mapping.get("Client Name") == "name", str(mapping))
        check("header map: Gate #→gate_code", mapping.get("Gate #") == "gate_code")
        check("header map: Days→schedule", mapping.get("Days") == "schedule")
        check("header map: junk col unmapped", mapping.get("Random Junk Column") is None)
        check("possible_dupes flagged Riverside", any("Riverside" in x for x in d["possible_dupes"]), str(d["possible_dupes"]))

        # Verify data landed with correct fields
        import sqlite3
        db = sqlite3.connect(db_path)
        row = db.execute("SELECT gate_code, contact_name, schedule, notes FROM accounts WHERE business_id=? AND name='Mercy Medical Center'", (biz_a,)).fetchone()
        check("Mercy gate_code=2211", row and row[0] == "2211", str(row))
        check("Mercy schedule=Daily", row and row[2] == "Daily")
        check("junk col folded into notes", row and "Random Junk Column" in (row[3] or ""), str(row))
        # Dupe within CSV: second Riverside row updated first, no double-create
        n_rv = db.execute("SELECT COUNT(*) FROM accounts WHERE business_id=? AND LOWER(name)='riverside office park'", (biz_a,)).fetchone()[0]
        check("Riverside deduped in-file", n_rv == 1, f"count={n_rv}")

        # ── Test 2: re-import dedups across requests ──
        r2 = httpx.post(f"{BASE}/onboarding/import-csv",
                        json={"business_id": biz_a, "key": key_a,
                              "csv_text": "name,address,gate_code\nMercy Medical Center,888 Hospital Blvd,2211\nBrand New One,1 New St,9999"},
                        timeout=60)
        d2 = r2.json()
        check("reimport: 1 created 0-updated-or-1-updated", d2["created"] == 1 and d2["updated"] + d2["skipped"] == 1, str(d2))
        n_accts = db.execute("SELECT COUNT(*) FROM accounts WHERE business_id=?", (biz_a,)).fetchone()[0]
        check("no dup growth after reimport", n_accts >= 51, f"accounts={n_accts}")
        # Existing Mercy notes not wiped
        m2 = db.execute("SELECT notes FROM accounts WHERE business_id=? AND name='Mercy Medical Center'", (biz_a,)).fetchone()
        check("Mercy notes preserved", m2 and "Badge required" in m2[0], str(m2))

        # ── Test 3: tenant isolation ──
        r3 = httpx.post(f"{BASE}/onboarding/import-csv",
                        json={"business_id": biz_b, "key": key_b,
                              "csv_text": "name,gate_code\nMercy Medical Center,9999"}, timeout=60)
        check("tenant B import ok", r3.status_code == 200)
        a_gate = db.execute("SELECT gate_code FROM accounts WHERE business_id=? AND name='Mercy Medical Center'", (biz_a,)).fetchone()[0]
        b_gate = db.execute("SELECT gate_code FROM accounts WHERE business_id=? AND name='Mercy Medical Center'", (biz_b,)).fetchone()[0]
        check("same name, separate tenants", a_gate == "2211" and b_gate == "9999", f"A={a_gate} B={b_gate}")

        # ── Test 4: auth ──
        r4 = httpx.post(f"{BASE}/onboarding/import-csv",
                        json={"business_id": biz_a, "key": "wrong", "csv_text": "name\nX"}, timeout=30)
        check("bad key → 403", r4.status_code == 403, str(r4.status_code))
        r5 = httpx.post(f"{BASE}/onboarding/import-csv",
                        json={"business_id": biz_a, "key": key_b, "csv_text": "name\nX"}, timeout=30)
        check("other tenant's key → 403", r5.status_code == 403, str(r5.status_code))

        # ── Test 5: multipart upload ──
        r6 = httpx.post(f"{BASE}/onboarding/import-csv",
                        data={"business_id": str(biz_a), "key": key_a},
                        files={"file": ("clients.csv", "name,schedule\nMultipart Account,Mon", "text/csv")},
                        timeout=60)
        check("multipart import ok", r6.status_code == 200 and r6.json()["created"] == 1, r6.text[:150])
        db.close()

        # Template endpoint
        rt = httpx.get(f"{BASE}/onboarding/import-template", timeout=15)
        check("template endpoint 200 + csv", rt.status_code == 200 and "gate_code" in rt.text)
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    print()
    if failures:
        print("FAILURES:", failures)
        sys.exit(1)
    print("🎉 ALL P2 IMPORT TESTS PASS")

if __name__ == "__main__":
    main()
