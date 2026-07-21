#!/usr/bin/env python3
"""
INFRA Task 3 — SQLite → PostgreSQL data migration.

Copies ALL data from the FieldNotes SQLite file into a Postgres database,
preserving primary keys (FK integrity) and resetting PG sequences afterwards.

Safety design:
  - DRY-RUN BY DEFAULT: prints source/target row counts for every table and exits.
    Writing requires --yes.
  - Refuses to run if any target table is non-empty (no silent merges).
  - FK-ordered copy: businesses → accounts/workers/pending_subscriptions →
    service_logs/actions/route_entries/qa_events.
  - Single transaction: any failure rolls back everything.
  - Verifies row counts for ALL tables after copy, incl. per-business tenant
    counts (isolation sanity).
  - NEVER point this at a live SQLite file mid-write — copy it aside first
    (cp fieldnotes.db /tmp/snapshot.db) or pass --sqlite to a snapshot.

Usage:
  python3 scripts/migrate_sqlite_to_pg.py                      # dry-run
  python3 scripts/migrate_sqlite_to_pg.py --yes                # actually copy
  python3 scripts/migrate_sqlite_to_pg.py --sqlite /tmp/x.db --pg postgresql://...

Target PG URL comes from --pg or DATABASE_URL env (must be postgresql://).
"""
import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# FK-safe copy order
TABLES = [
    "businesses",
    "accounts",
    "workers",
    "pending_subscriptions",
    "service_logs",
    "actions",
    "route_entries",
    "qa_events",
]

# Tables with serial PKs whose sequences must be reset after explicit-id inserts
SEQUENCED = TABLES  # all 8 have integer autoincrement PKs

# FK map: table -> [(column, parent_table, nullable)]
# SQLite never enforced these — prod data can contain orphans (e.g. account_id=0).
# Nullable orphans are repaired to NULL; non-nullable orphans are skipped. Both are LOUD.
FKS = {
    "accounts": [("business_id", "businesses", False)],
    "workers": [("business_id", "businesses", False)],
    "pending_subscriptions": [],
    "service_logs": [("business_id", "businesses", False),
                     ("account_id", "accounts", True),
                     ("worker_id", "workers", False)],
    "actions": [("business_id", "businesses", False),
                ("account_id", "accounts", True),
                ("service_log_id", "service_logs", True)],
    "route_entries": [("business_id", "businesses", False),
                      ("account_id", "accounts", False)],
    "qa_events": [("business_id", "businesses", False),
                  ("worker_id", "workers", True)],
}

# DateTime columns per table — SQLite stores them as ISO strings; PG needs real timestamps
DATETIME_COLS = {
    "businesses": ["created_at"],
    "accounts": ["created_at"],
    "workers": ["created_at"],
    "pending_subscriptions": ["created_at"],
    "service_logs": ["timestamp"],
    "actions": ["created_at", "completed_at"],
    "route_entries": [],
    "qa_events": ["created_at"],
}


def sqlite_counts(path: str) -> dict:
    uri = f"file:{path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        out = {}
        for t in TABLES:
            try:
                out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = None  # table missing in source
        return out
    finally:
        con.close()


def sqlite_rows(path: str, table: str):
    uri = f"file:{path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def sanitize(table: str, rows: list, parent_ids: dict) -> tuple:
    """Repair FK orphans (loud) + parse ISO datetime strings. Returns (rows, repairs)."""
    import datetime as _dt
    repairs = []
    out = []
    for row in rows:
        skip = False
        for col, parent, nullable in FKS.get(table, []):
            val = row.get(col)
            if val is not None and val not in parent_ids[parent]:
                if nullable:
                    repairs.append(f"{table}.id={row.get('id')}: {col}={val} orphaned → NULL")
                    row[col] = None
                else:
                    repairs.append(f"{table}.id={row.get('id')}: {col}={val} orphaned, NOT NULL → ROW SKIPPED")
                    skip = True
                    break
        if skip:
            continue
        for col in DATETIME_COLS.get(table, []):
            v = row.get(col)
            if isinstance(v, str):
                try:
                    row[col] = _dt.datetime.fromisoformat(v)
                except ValueError:
                    repairs.append(f"{table}.id={row.get('id')}: unparseable {col}={v!r} → NULL")
                    row[col] = None
        out.append(row)
    return out, repairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=str(REPO_ROOT / "fieldnotes.db"))
    ap.add_argument("--pg", default=None, help="postgresql:// URL (or set DATABASE_URL)")
    ap.add_argument("--yes", action="store_true", help="actually write (default is dry-run)")
    args = ap.parse_args()

    import os
    pg_url = args.pg or os.environ.get("DATABASE_URL", "")
    if pg_url.startswith("postgres://"):
        pg_url = "postgresql://" + pg_url[len("postgres://"):]
    if not pg_url.startswith("postgresql"):
        print("❌ target must be postgresql:// (set --pg or DATABASE_URL). "
              "Refusing to 'migrate' sqlite→sqlite.")
        sys.exit(1)

    src = sqlite_counts(args.sqlite)
    print(f"SOURCE sqlite: {args.sqlite}")
    for t in TABLES:
        print(f"  {t:24s} {src[t] if src[t] is not None else 'MISSING'}")

    from sqlalchemy import create_engine, inspect, MetaData, Table, text
    eng = create_engine(pg_url)
    insp = inspect(eng)
    existing = set(insp.get_table_names())
    missing = [t for t in TABLES if t not in existing]
    if missing:
        print(f"❌ target PG is missing tables {missing} — run `alembic upgrade head` first")
        sys.exit(1)

    md = MetaData()
    with eng.connect() as c:
        tgt_counts = {t: c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in TABLES}
    print(f"TARGET pg: {pg_url.split('@')[-1]}")
    for t in TABLES:
        print(f"  {t:24s} {tgt_counts[t]}")

    if not args.yes:
        print("\nDRY-RUN (default). Re-run with --yes to copy. No data was written.")
        return

    nonempty = {t: n for t, n in tgt_counts.items() if n}
    if nonempty:
        print(f"❌ target tables not empty: {nonempty} — refusing to merge. Migrate into a FRESH database.")
        sys.exit(1)

    print("\nCopying (single transaction) ...")
    copied_ids = {t: set() for t in TABLES}
    sanitized = {}
    all_repairs = []
    with eng.begin() as c:
        for t in TABLES:
            if src[t] is None:
                print(f"  skip {t} (missing in source)")
                continue
            rows = sqlite_rows(args.sqlite, t)
            rows, repairs = sanitize(t, rows, copied_ids)
            all_repairs.extend(repairs)
            sanitized[t] = rows
            if rows:
                table = Table(t, md, autoload_with=eng)
                c.execute(table.insert(), rows)
                copied_ids[t] = {r["id"] for r in rows}
            print(f"  {t:24s} copied {len(rows)}")
        # Reset sequences past the max explicit id so future inserts don't collide
        for t in SEQUENCED:
            if copied_ids[t]:
                c.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{t}','id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 1))"
                ))
    print("Sequences reset.")
    if all_repairs:
        print(f"\n⚠️  DATA REPAIRS ({len(all_repairs)}) — review these:")
        for r in all_repairs:
            print(f"  - {r}")

    # Verification: counts vs SANITIZED source (orphan-skipped rows excluded),
    # plus per-business tenant breakdown.
    ok = True
    with eng.connect() as c:
        for t in TABLES:
            expected = len(sanitized.get(t, [])) if src[t] is not None else 0
            got = c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            note = ""
            if src[t] is not None and expected != src[t]:
                note = f" ({src[t] - expected} orphan row(s) skipped)"
            status = "✅" if got == expected else "❌"
            if got != expected:
                ok = False
            print(f"  {status} {t:24s} expected={expected} target={got}{note}")
        # Tenant-level spot check on the tenant-scoped tables
        from collections import Counter
        for t in ("accounts", "service_logs", "actions", "route_entries", "qa_events"):
            srows = sanitized.get(t)
            if not srows:
                continue
            s_cnt = Counter(r["business_id"] for r in srows)
            rows = c.execute(text(f"SELECT business_id, COUNT(*) FROM {t} GROUP BY business_id")).fetchall()
            t_cnt = {r[0]: r[1] for r in rows}
            if dict(s_cnt) != t_cnt:
                ok = False
                print(f"  ❌ tenant breakdown mismatch on {t}: sqlite={dict(s_cnt)} pg={t_cnt}")
            else:
                print(f"  ✅ tenant breakdown matches on {t} ({len(t_cnt)} tenants)")

    if not ok:
        print("\n❌ VERIFICATION FAILED — investigate before proceeding")
        sys.exit(1)
    print("\n✅ MIGRATION COMPLETE — all counts verified, tenant isolation intact")


if __name__ == "__main__":
    main()
