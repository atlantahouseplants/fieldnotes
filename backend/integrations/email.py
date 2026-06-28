"""
FieldNotes — Resend Email Integration
Sends daily summary emails to business owners.
"""
import os
import httpx
from typing import Optional

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = "FieldNotes <fieldnotes@fieldnotes.ai>"


async def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    reply_to: Optional[str] = None
) -> dict:
    """Send an email via Resend API."""
    if not RESEND_API_KEY:
        return {"ok": False, "error": "RESEND_API_KEY not configured"}
    
    payload = {
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html_body
    }
    if reply_to:
        payload["reply_to"] = reply_to
    
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload
        )
        return resp.json()


async def send_daily_summary(to_email: str, summary_data: dict) -> dict:
    """Send the end-of-day summary email to the business owner."""
    date_str = summary_data.get("date", "today")
    stops_completed = summary_data.get("stops_completed", 0)
    stops_expected = summary_data.get("stops_expected", 0)
    issues_flagged = summary_data.get("issues_flagged", 0)
    actions_pending = summary_data.get("actions_pending", 0)
    supplies_needed = summary_data.get("supplies_needed", [])
    stops_missed = summary_data.get("stops_missed", [])
    workers = summary_data.get("workers_active", [])
    
    # Build HTML
    missed_html = ""
    if stops_missed:
        items = "".join(f"<li>{s}</li>" for s in stops_missed)
        missed_html = f"""
        <tr>
            <td style="color:#e74c3c;font-weight:bold">⚠️ Missed Stops</td>
            <td><ul style="margin:0;padding-left:15px">{items}</ul></td>
        </tr>"""
    
    supplies_html = ""
    if supplies_needed:
        items = "".join(f"<li>{s}</li>" for s in supplies_needed)
        supplies_html = f"""
        <tr>
            <td style="color:#f39c12;font-weight:bold">📦 Supplies Needed</td>
            <td><ul style="margin:0;padding-left:15px">{items}</ul></td>
        </tr>"""
    
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:0 auto;padding:20px;background:#f5f6fa">
    <div style="background:#fff;border-radius:12px;padding:30px;box-shadow:0 2px 8px rgba(0,0,0,0.08)">
        <h1 style="color:#2c3e50;margin:0 0 5px;font-size:24px">📋 FieldNotes Daily Summary</h1>
        <p style="color:#7f8c8d;margin:0 0 25px">{date_str} · Workers: {', '.join(workers) if workers else 'N/A'}</p>
        
        <table style="width:100%;border-collapse:collapse">
            <tr>
                <td style="padding:10px 15px;background:#eaf7ea;border-radius:6px;font-weight:bold">✅ Stops Completed</td>
                <td style="padding:10px 15px;font-size:20px;font-weight:bold;color:#27ae60">{stops_completed}/{stops_expected}</td>
            </tr>
            <tr><td style="height:8px"></td></tr>
            <tr>
                <td style="padding:10px 15px;background:#fef9e7;border-radius:6px;font-weight:bold">🔔 Issues Flagged</td>
                <td style="padding:10px 15px;font-size:20px;font-weight:bold;color:#e67e22">{issues_flagged}</td>
            </tr>
            <tr><td style="height:8px"></td></tr>
            <tr>
                <td style="padding:10px 15px;background:#fdecea;border-radius:6px;font-weight:bold">📌 Actions Pending</td>
                <td style="padding:10px 15px;font-size:20px;font-weight:bold;color:#e74c3c">{actions_pending}</td>
            </tr>
            {missed_html}
            {supplies_html}
        </table>
        
        <p style="color:#95a5a6;font-size:12px;margin-top:25px;text-align:center">
            FieldNotes — View full dashboard → 
        </p>
    </div>
</body>
</html>"""
    
    subject = f"📋 FieldNotes: {stops_completed}/{stops_expected} stops · {issues_flagged} issues"
    return await send_email(to_email, subject, html)
