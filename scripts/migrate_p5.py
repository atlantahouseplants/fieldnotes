#!/usr/bin/env python3
"""
P5 migration: add businesses.beta_all_access if missing; grandfather all
EXISTING businesses into beta (beta_all_access=1) so nothing changes for
current tenants. New signups default via SQLAlchemy (models.py default=True
during beta — flip post-beta). Idempotent.
"""
import sqlite3, sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/home/wallg/fieldnotes/fieldnotes.db"

db = sqlite3.connect(DB)
cols = [r[1] for r in db.execute("PRAGMA table_info(businesses)").fetchall()]
if "beta_all_access" not in cols:
    db.execute("ALTER TABLE businesses ADD COLUMN beta_all_access BOOLEAN")
    print("added beta_all_access column")
else:
    print("beta_all_access column already present")

# Grandfather existing tenants into beta all-access (NULL or 0 -> 1)
cur = db.execute("UPDATE businesses SET beta_all_access=1 WHERE beta_all_access IS NULL OR beta_all_access=0")
db.commit()
print(f"grandfathered {cur.rowcount} businesses into beta_all_access=1")

for row in db.execute("SELECT id, name, tier, beta_all_access FROM businesses ORDER BY id").fetchall():
    print(" ", row)
db.close()
