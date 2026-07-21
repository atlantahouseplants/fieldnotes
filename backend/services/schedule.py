"""
FieldNotes — Schedule parsing + route computation (P4)

Parses Account.schedule free text ("Mon/Thu", "Tue (wk A) + Wed (wk B)",
"every other Tue", "1st of month", "Daily") into normalized entries and
syncs them into RouteEntry rows — which powers the existing missed-stop
detection in the daily summary, the new route Q&A intents, and the
morning route push.

Week anchor (shared with routes/summary.py): even ISO week = week_a, odd = week_b.
"""
import json
import re
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..models import Account, RouteEntry, ServiceLog

DAY_ALIASES = {
    "mon": "monday", "monday": "monday",
    "tue": "tuesday", "tues": "tuesday", "tuesday": "tuesday",
    "wed": "wednesday", "weds": "wednesday", "wednesday": "wednesday",
    "thu": "thursday", "thur": "thursday", "thurs": "thursday", "thursday": "thursday",
    "fri": "friday", "friday": "friday",
    "sat": "saturday", "saturday": "saturday",
    "sun": "sunday", "sunday": "sunday",
}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]

_WEEK_MARK = re.compile(r"\(?\b(?:wk|week)\s*([ab])\b\)?", re.I)
_MONTHLY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+of\s+(?:the\s+)?month\b", re.I)
_EVERY_OTHER = re.compile(r"\bevery\s+other\b", re.I)


def parse_schedule(text: str) -> dict:
    """
    Free-text schedule → normalized structure.
    Returns {"entries": [{"day": "monday", "week_type": "weekly"|"week_a"|"week_b"}],
             "monthly_day": int|None, "raw": text}
    """
    result = {"entries": [], "monthly_day": None, "raw": text or ""}
    if not text:
        return result

    m = _MONTHLY.search(text)
    if m:
        result["monthly_day"] = int(m.group(1))

    # Split multi-part schedules with per-part week types: "Tue (wk A) + Wed (wk B)"
    segments = re.split(r"\s*[+;&]\s*", text)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        week_type = "weekly"
        wm = _WEEK_MARK.search(seg)
        if wm:
            week_type = "week_a" if wm.group(1).lower() == "a" else "week_b"
        elif _EVERY_OTHER.search(seg):
            week_type = "week_a"  # anchored to even ISO weeks; owner adjusts if flipped

        days = [d for tok, d in DAY_ALIASES.items() if re.search(rf"\b{tok}\b", seg.lower())]
        days = list(dict.fromkeys(days))  # dedupe, preserve order

        if re.search(r"\bdaily\b", seg.lower()) and not days:
            days = WEEKDAYS[:]

        for d in days:
            entry = {"day": d, "week_type": week_type}
            if entry not in result["entries"]:
                result["entries"].append(entry)

    return result


def week_type_for(d: date) -> str:
    """Even ISO week = week_a, odd = week_b (matches routes/summary.py::_get_week_type)."""
    return "week_a" if d.isocalendar()[1] % 2 == 0 else "week_b"


def scheduled_on(account_parsed: dict, d: date) -> bool:
    """Is an account with this parsed schedule due on date d?"""
    md = account_parsed.get("monthly_day")
    if md and d.day == md:
        return True
    dow = d.strftime("%A").lower()
    wt = week_type_for(d)
    for e in account_parsed.get("entries", []):
        if e["day"] == dow and (e["week_type"] == "weekly" or e["week_type"] == wt):
            return True
    return False


def sync_route_entries(db: Session, business_id: int) -> dict:
    """
    Rebuild RouteEntry rows from Account.schedule for a tenant.
    Idempotent: creates missing, removes entries whose schedule text disappeared.
    Stores the normalized parse back onto Account.schedule_parsed.
    """
    accounts = db.query(Account).filter(
        Account.business_id == business_id, Account.is_active == True).all()

    wanted = {}  # (account_id, day, week_type) -> account
    for a in accounts:
        parsed = parse_schedule(a.schedule)
        a.schedule_parsed = json.dumps(parsed) if (parsed["entries"] or parsed["monthly_day"]) else None
        for e in parsed["entries"]:
            wanted[(a.id, e["day"], e["week_type"])] = a

    existing = db.query(RouteEntry).filter(RouteEntry.business_id == business_id).all()
    existing_keys = {(r.account_id, r.day_of_week, r.week_type): r for r in existing}

    created = removed = 0
    for key, acct in wanted.items():
        if key in existing_keys:
            if not existing_keys[key].is_active:
                existing_keys[key].is_active = True  # reactivate revived schedule
                created += 1
        else:
            db.add(RouteEntry(business_id=business_id, account_id=acct.id,
                              day_of_week=key[1], week_type=key[2], is_active=True))
            created += 1
    for key, row in existing_keys.items():
        if key not in wanted:
            row.is_active = False
            removed += 1

    db.commit()
    return {"created": created, "deactivated": removed, "accounts_with_schedules":
            sum(1 for a in accounts if a.schedule_parsed)}


def route_for_date(db: Session, business_id: int, target: date) -> list:
    """
    Accounts due on target date with done/remaining status.
    Returns [{"account_id", "name", "schedule", "done": bool}]
    """
    dow = target.strftime("%A").lower()
    wt = week_type_for(target)
    entries = db.query(RouteEntry).filter(
        RouteEntry.business_id == business_id,
        RouteEntry.is_active == True,
        RouteEntry.day_of_week == dow,
    ).filter((RouteEntry.week_type == "weekly") | (RouteEntry.week_type == wt)).all()

    # Monthly schedules live only on Account.schedule_parsed (RouteEntry can't express them)
    accounts = {a.id: a for a in db.query(Account).filter(
        Account.business_id == business_id, Account.is_active == True).all()}
    due_ids = {e.account_id for e in entries}
    for a in accounts.values():
        if a.schedule_parsed:
            try:
                parsed = json.loads(a.schedule_parsed)
                if parsed.get("monthly_day") == target.day and a.id not in due_ids:
                    due_ids.add(a.id)
            except (ValueError, TypeError):
                pass

    day_start = target.isoformat()
    day_end = (target + timedelta(days=1)).isoformat()
    logged = {l.account_id for l in db.query(ServiceLog).filter(
        ServiceLog.business_id == business_id,
        ServiceLog.timestamp >= day_start,
        ServiceLog.timestamp < day_end,
        ServiceLog.account_id.isnot(None)).all()}

    return [{"account_id": aid, "name": accounts[aid].name if aid in accounts else f"#{aid}",
             "schedule": accounts[aid].schedule if aid in accounts else None,
             "done": aid in logged}
            for aid in sorted(due_ids, key=lambda i: accounts[i].name.lower() if i in accounts else "")]


def missed_this_week(db: Session, business_id: int, today: date = None) -> list:
    """Stops due Mon→today this week with no service log. Returns account names + due dates."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    missed = []
    d = monday
    while d < today:  # strictly past days — today's stops are pending, not missed
        for stop in route_for_date(db, business_id, d):
            if not stop["done"]:
                missed.append({"name": stop["name"], "due": d.isoformat(),
                               "weekday": d.strftime("%A")})
        d += timedelta(days=1)
    return missed


def format_route_message(business_name: str, target: date, stops: list) -> str:
    """Telegram HTML morning-route message."""
    label = target.strftime("%a %b %-d") if target == date.today() else target.strftime("%a %b %-d")
    day_word = "Today" if target == date.today() else (
        "Tomorrow" if target == date.today() + timedelta(days=1) else label)
    lines = [f"🌅 <b>{business_name} — {day_word}'s route</b> ({label})"]
    if not stops:
        lines.append("No stops scheduled. Enjoy the lighter day 🌿")
        return "\n".join(lines)
    for i, s in enumerate(stops, 1):
        mark = "✅" if s["done"] else "⬜"
        lines.append(f"{mark} {i}. <b>{s['name']}</b>")
    done = sum(1 for s in stops if s["done"])
    lines.append("")
    lines.append(f"{done}/{len(stops)} logged. Reply with a note at each stop — I'll file it.")
    return "\n".join(lines)
