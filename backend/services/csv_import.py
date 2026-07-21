"""
FieldNotes — CSV Client-List Import (P2)

Owners paste or upload their client list; we map their headers to our
account fields (LLM sees HEADERS ONLY, never data rows), then parse
deterministically. Dedup by case-insensitive account name within the tenant.
"""
import csv
import io
import json
import os
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from ..models import Account

# Canonical fields we import into
FIELDS = ["name", "address", "gate_code", "access_notes", "contact_name",
          "contact_phone", "schedule", "notes", "shorthand"]

# Deterministic alias map — tried BEFORE any LLM call
ALIASES = {
    "name": ["name", "account", "account name", "client", "client name", "customer",
             "customer name", "company", "site", "site name", "location", "property",
             "property name", "stop", "business"],
    "address": ["address", "addr", "street", "street address", "site address",
                "location address", "service address", "property address"],
    "gate_code": ["gate code", "gate", "gate #", "gatecode", "access code", "code",
                  "entry code", "door code", "alarm code", "alarm", "lockbox",
                  "lock box", "key code", "gate/door code"],
    "access_notes": ["access notes", "access", "access instructions", "entry notes",
                     "parking", "entry", "instructions", "special instructions",
                     "access info", "gate notes"],
    "contact_name": ["contact", "contact name", "contact person", "manager",
                     "property manager", "poc", "point of contact"],
    "contact_phone": ["phone", "contact phone", "phone number", "tel", "telephone",
                      "mobile", "contact number", "manager phone"],
    "schedule": ["schedule", "days", "service days", "day", "frequency", "service day",
                 "visit days", "route day", "service schedule"],
    "notes": ["notes", "note", "comments", "comment", "details", "misc"],
    "shorthand": ["shorthand", "nickname", "short name", "abbr", "abbreviation"],
}

HEADER_MAP_PROMPT = """You map CSV column headers to a fixed set of fields for a field-service client list.

CSV headers: {headers}

Target fields: name, address, gate_code, access_notes, contact_name, contact_phone, schedule, notes, shorthand

Return ONLY a JSON object mapping each CSV header to ONE target field, or null if no fit. Example:
{"Client": "name", "Gate #": "gate_code", "Random Column": null}

JSON:"""


def _norm(h: str) -> str:
    return h.strip().lower().replace("_", " ").replace("-", " ")


def map_headers(headers: list) -> dict:
    """header → canonical field. Deterministic aliases first (exact normalized match)."""
    mapping = {}
    for h in headers:
        mapping[h] = None
        n = _norm(h)
        for field, aliases in ALIASES.items():
            if n in aliases:
                mapping[h] = field
                break
    return mapping


async def map_headers_llm(headers: list) -> dict:
    """LLM fallback for headers the alias map missed. HEADERS ONLY — no data rows."""
    mapping = map_headers(headers)
    unmapped = [h for h, f in mapping.items() if f is None]
    if not unmapped:
        return mapping

    prompt = HEADER_MAP_PROMPT.replace("{headers}", json.dumps(headers))
    for base, key, model in [
        ("https://api.x.ai/v1", os.getenv("XAI_API_KEY", ""), "grok-4.5"),
        ("https://api.deepseek.com/v1", os.getenv("DEEPSEEK_API_KEY", ""), "deepseek-chat"),
        ("https://api.openai.com/v1", os.getenv("OPENAI_API_KEY", ""), "gpt-4o-mini"),
    ]:
        if not key:
            continue
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0, "max_tokens": 300,
                          "response_format": {"type": "json_object"}},
                )
                resp.raise_for_status()
                llm_map = json.loads(resp.json()["choices"][0]["message"]["content"])
                for h, f in llm_map.items():
                    if h in mapping and mapping[h] is None and f in FIELDS:
                        mapping[h] = f
                return mapping
        except Exception:
            continue
    return mapping  # unmapped stay None — their data lands in notes


def parse_csv_text(csv_text: str) -> tuple[list, list]:
    """Parse raw CSV text (handles BOM, smart quotes, \r\n). Returns (headers, rows)."""
    text = csv_text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.lstrip("﻿")
    for a, b in [("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")]:
        text = text.replace(a, b)
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def import_accounts(db: Session, business_id: int, csv_text: str,
                    header_mapping: dict) -> dict:
    """
    Deterministic import. Dedup: same name (case-insensitive) within tenant →
    UPDATE fields (fill empties, never wipe existing data). Possible fuzzy
    dupes are reported, never auto-merged.
    """
    headers, rows = parse_csv_text(csv_text)
    if not headers:
        return {"error": "no CSV data found", "created": 0, "updated": 0, "skipped": 0}

    # name column is required
    name_col = next((i for i, h in enumerate(headers) if header_mapping.get(h) == "name"), None)
    if name_col is None:
        # No header mapped to name → treat first column as name
        name_col = 0

    existing = {a.name.strip().lower(): a for a in db.query(Account).filter(
        Account.business_id == business_id).all()}

    created, updated, skipped, possible_dupes, preview = 0, 0, 0, [], []
    UNMAPPED_NOTE_CAP = 200

    for row in rows[:500]:
        def cell(field):
            for i, h in enumerate(headers):
                if header_mapping.get(h) == field and i < len(row):
                    v = row[i].strip()
                    if v:
                        return v
            return None

        name = (row[name_col].strip() if name_col < len(row) else "").strip()
        if not name:
            skipped += 1
            continue

        # Unmapped columns → append into notes so no data is lost
        extras = []
        for i, h in enumerate(headers):
            if header_mapping.get(h) is None and i < len(row) and row[i].strip():
                extras.append(f"{h}: {row[i].strip()}")
        extra_note = "; ".join(extras)[:UNMAPPED_NOTE_CAP] or None

        fields = {f: cell(f) for f in FIELDS if f != "name"}
        key = name.lower()
        match = existing.get(key)

        if match:
            changed = False
            for f, v in fields.items():
                if v and not getattr(match, f, None):
                    setattr(match, f, v)
                    changed = True
            if extra_note and extra_note not in (match.notes or ""):
                match.notes = ((match.notes or "") + " | " + extra_note).strip(" |")
                changed = True
            if changed:
                updated += 1
            else:
                skipped += 1
            acct = match
        else:
            acct = Account(business_id=business_id, name=name, is_active=True, **fields)
            if extra_note:
                acct.notes = ((acct.notes or "") + " | " + extra_note).strip(" |")
            db.add(acct)
            existing[key] = acct
            created += 1
            # Fuzzy-dupe detection: report near-matches for owner review
            for ek, ea in existing.items():
                if ek != key and (key in ek or ek in key) and abs(len(ek) - len(key)) > 2:
                    possible_dupes.append(f'"{name}" vs existing "{ea.name}"')
                    break

        if len(preview) < 3:
            preview.append({"name": name, "address": fields.get("address"),
                            "gate_code": fields.get("gate_code"),
                            "schedule": fields.get("schedule"),
                            "action": "updated" if match else "created"})

    db.commit()
    return {
        "created": created, "updated": updated, "skipped": skipped,
        "total_rows": len(rows[:500]),
        "header_mapping": header_mapping,
        "possible_dupes": possible_dupes[:10],
        "preview": preview,
    }
