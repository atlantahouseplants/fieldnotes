# P2 — Rich Client Import (CSV + structured account fields)

**Status: 🔲 NOT STARTED** · Depends on: nothing (but P1 benefits from it; build P1 first)

## Goal

A new business can get their REAL client list into FieldNotes in <5 minutes, with the fields that make Q&A useful: address, gate/access codes, contacts, notes, service schedule. Cold-start the knowledge base instead of waiting for logs to accumulate.

## Scope

### 1. Account model extension (`backend/models.py`)
Add columns to Account: `address`, `gate_code`, `access_notes`, `contact_name`, `contact_phone`, `schedule` (free-text day pattern, e.g. "Mon/Thu" — structured recurrence comes in P4), `notes`. SQLite migration via CREATE-new-copy pattern (project convention, pitfall #16) or add-column ALTERs — SQLite supports `ALTER TABLE ADD COLUMN`; use that, it's cleaner here.

### 2. CSV import endpoint
- `POST /onboarding/import-csv` (key-locked: business_id + key, or invite-token flow for brand-new signups).
- Accepts: file upload (multipart) AND pasted CSV text (mobile-friendly — owners will do this from their phone).
- Expected columns (flexible header mapping — accept common aliases): name, address, gate/access code, access notes, contact, phone, schedule, notes. Use the LLM to map arbitrary headers → our fields (one classify call per upload, pass headers only, NOT the data rows — cheaper + avoids PII through the LLM). Parse rows deterministically after mapping.
- Dedup: same account name (case-insensitive) within business → update fields, don't duplicate.
- Response: counts (created/updated/skipped) + first-3 preview so the owner can sanity-check.

### 3. start.html + dashboard wiring
- Onboarding wizard step: "Import your client list" — download a CSV template, upload/paste. Show the resulting account count.
- Dashboard: account detail view shows the new fields (read-only v1).

### 4. Google Sheets path (stretch, same phase if cheap)
Many pool/lawn companies keep the list in Sheets. "Paste your sheet link" → fetch published/CSV-export URL → same pipeline. Only if <1 day extra; otherwise cut.

## Tasks

1. Model migration (7 columns) + verify existing seeds/tests still work
2. Header-mapping LLM call + deterministic row parser (`services/csv_import.py`)
3. `/onboarding/import-csv` endpoint (multipart + text) with dedup + preview response
4. start.html import step + template CSV download
5. Dashboard account detail rendering
6. Tests: 50-row realistic pool-company CSV (messy headers, missing fields, dupes); tenant A's import invisible to tenant B
7. AHP dogfood: import Geoff's real 18-account list (from the wiki) as the first real tenant dataset

## Acceptance criteria

- [ ] Pool-company CSV (name/address/gate code/contact/schedule) imports in one shot, dupes merged
- [ ] Paste-from-phone path works (no file picker required)
- [ ] Tenant isolation test passes
- [ ] Q&A (P1) immediately answers gate-code questions for imported accounts
- [ ] AHP's 18 accounts live as proof

## Pitfalls

- Don't send data ROWS through the LLM (PII + cost) — headers only for mapping.
- Phone-CSV paste: Excel junk (smart quotes, BOM) — normalize encoding.
- Account name is the dedup key; fuzzy duplicates ("Smith Residence" vs "Smith") → create both, list in response as "possible dupes" for owner review. Don't auto-merge.
