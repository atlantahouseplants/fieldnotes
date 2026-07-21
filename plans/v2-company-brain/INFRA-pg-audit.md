# PostgreSQL Readiness Audit Findings

This report details findings related to PostgreSQL readiness for the FieldNotes backend, focusing on potential SQLite-isms and areas that might require adjustment during a migration to PostgreSQL.

**Date:** July 20, 2026

---

## Summary of Findings

Overall, the codebase appears to use SQLAlchemy effectively, minimizing direct SQL and thus reducing the prevalence of blatant SQLite-specific syntax. The primary areas for consideration are string comparisons, especially those intended to be case-insensitive.

---

## Detailed Findings

### 1. `ilike()` Usage (SQLAlchemy's Case-Insensitive LIKE)

SQLAlchemy's `.ilike()` method is used for case-insensitive pattern matching. This is well-suited for PostgreSQL as SQLAlchemy will translate it to the `ILIKE` operator, which provides case-insensitive matching in PostgreSQL. For SQLite, `LIKE` is often case-insensitive by default depending on collation.

**Sites Found:**
*   `/home/wallg/fieldnotes/backend/routes/billing.py:86: biz = db.query(Business).filter(Business.owner_email.ilike(email)).first()`
*   `/home/wallg/fieldnotes/backend/routes/summary.py:60: Action.description.ilike("supply:%")`

**Recommendation:** No immediate changes are required for these instances. They are already using a PostgreSQL-compatible approach for case-insensitive matching.

### 2. `.lower()` for String Normalization and Comparison

Several instances involve converting strings to lowercase in Python application code, primarily for normalization (e.g., email addresses, account slugs) before storage or comparison. While this is a good practice for data consistency, any direct equality comparisons (`==`) involving these lowercased strings in SQLAlchemy queries will be case-sensitive in PostgreSQL. If these comparisons are *intended* to be case-insensitive at the database level, and the column data itself is not stored as lowercase, then these would need adjustment.

**Sites Found:**
*   `/home/wallg/fieldnotes/backend/routes/billing.py:120: email = email.strip().lower()`
*   `/home/wallg/fieldnotes/backend/routes/summary.py:68: today_dow = date.today().strftime("%A").lower()`
*   `/home/wallg/fieldnotes/backend/services/parser.py:165: note_lower = note.lower()`
*   `/home/wallg/fieldnotes/backend/routes/webhook.py:189: account_map[a.name.lower()] = a.id`
*   `/home/wallg/fieldnotes/backend/routes/webhook.py:191: account_map[a.shorthand.lower()] = a.id`
*   `/home/wallg/fieldnotes/backend/routes/webhook.py:197: account_hint = (parsed.get("account_hint") or "").lower()`
*   `/home/wallg/fieldnotes/backend/routes/onboarding.py:30: return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')`
*   `/home/wallg/fieldnotes/backend/routes/onboarding.py:67: PendingSubscription.email == data.owner_email.strip().lower()`
*   `/home/wallg/fieldnotes/backend/routes/onboarding.py:82: if not acc_name or acc_name.lower() in seen:`
*   `/home/wallg/fieldnotes/backend/routes/onboarding.py:84: seen.add(acc_name.lower())`

**Recommendation:**
*   For comparisons like `/home/wallg/fieldnotes/backend/routes/onboarding.py:67`, where `PendingSubscription.email` is compared against a lowercased input, it is crucial to ensure that the `email` column in PostgreSQL either stores data in lowercase or has a case-insensitive collation, or that the query is adapted to use `func.lower(PendingSubscription.email) == data.owner_email.strip().lower()` (requiring an index on `lower(email)` for performance) or `PendingSubscription.email.ilike(data.owner_email.strip())`.
*   For other instances where `.lower()` is used for dictionary keys or local string manipulation, no direct database impact is expected.
*   During migration, review these sites to confirm intended case-sensitivity behavior.

### 3. `startswith()` String Methods

These are Python string methods used for local string processing and conditional logic, not directly in SQL queries (except for the `DATABASE_URL` parsing which is application-level logic).

**Sites Found:**
*   `/home/wallg/fieldnotes/backend/models.py:19: if _DATABASE_URL.startswith("sqlite:///"):`
*   `/home/wallg/fieldnotes/backend/models.py:25: engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}) if DATABASE_URL.startswith("sqlite:///") else create_engine(DATABASE_URL)`
*   `/home/wallg/fieldnotes/backend/poller.py:21: if line and not line.startswith('#'):`
*   `/home/wallg/fieldnotes/backend/routes/webhook.py:73: if text.startswith("/start"):`
*   `/home/wallg/fieldnotes/backend/routes/webhook.py:97: if payload.startswith("owner_"):`
*   `/home/wallg/fieldnotes/backend/routes/webhook.py:116: if payload.startswith("invite_"):`

**Recommendation:** No changes are required. These are Python-level operations.

### 4. Raw SQL or SQLite-Specific Functions

No explicit raw SQL queries (e.g., using `text()` from SQLAlchemy without a `session.execute` for DDL, or `db.execute("PRAGMA ...")`) or SQLite-specific functions (e.g., `strftime` with SQLite-specific format strings that differ from PostgreSQL) were found that would impede PostgreSQL migration.

**Recommendation:** Continue to rely on SQLAlchemy's ORM for database interactions to maintain portability.

---

## Conclusion

The FieldNotes backend exhibits good PostgreSQL readiness due to its reliance on SQLAlchemy's ORM. The `.ilike()` usages are already PostgreSQL-compatible. The main area of attention for a full migration will be ensuring that case-insensitivity expectations from SQLite (where `LIKE` is often case-insensitive) are correctly translated or handled in PostgreSQL for direct equality comparisons or specific indexed columns. No immediate changes are required to existing query behavior based on this audit, beyond the `DATABASE_URL` support already implemented.
