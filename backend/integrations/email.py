"""
FieldNotes — Email Integration
Sends daily summary emails to business owners.
Transport: AgentMail (primary, fieldnotesapp@agentmail.to) → Resend (fallback).
"""
import os
import httpx
from typing import Optional

AGENTMAIL_API_KEY = os.getenv("AGENTMAIL_API_KEY", "")
AGENTMAIL_INBOX = os.getenv("AGENTMAIL_INBOX", "fieldnotesapp@agentmail.to")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = f"FieldNotes <{AGENTMAIL_INBOX}>"


async def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    reply_to: Optional[str] = None
) -> dict:
    """Send an email — AgentMail first, Resend as fallback."""
    if AGENTMAIL_API_KEY:
        payload = {
            "to": [to_email],
            "subject": subject,
            "html": html_body,
            "labels": ["fieldnotes", "daily-summary"],
        }
        if reply_to:
            payload["reply_to"] = [reply_to]
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.agentmail.to/v0/inboxes/{AGENTMAIL_INBOX}/messages/send",
                headers={
                    "Authorization": f"Bearer {AGENTMAIL_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=payload
            )
            if resp.status_code < 300:
                return {"ok": True, "provider": "agentmail", "result": resp.json()}
            # fall through to Resend on AgentMail failure
            agentmail_err = f"{resp.status_code}: {resp.text[:200]}"
    else:
        agentmail_err = "AGENTMAIL_API_KEY not configured"

    if RESEND_API_KEY:
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
            out = resp.json()
            out["ok"] = resp.status_code < 300
            out["provider"] = "resend"
            return out

    return {"ok": False, "error": f"no working email provider (agentmail: {agentmail_err}; resend: {'not configured' if not RESEND_API_KEY else 'failed'})"}


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
