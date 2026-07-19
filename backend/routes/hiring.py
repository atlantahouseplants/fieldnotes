"""
Form submission endpoint — added to FieldNotes API.
Receives hiring applications from ahp-hiring.vercel.app
"""
from fastapi import APIRouter, Form
import httpx
import os
from datetime import datetime
import json

router = APIRouter(tags=["hiring"])

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8682558667:AAF4AsW4RZG58f7Xe6VZq6vu5P2SCGPqDtw")
TELEGRAM_CHANNEL = "8093038758"


@router.post("/api/hiring/apply")
async def submit_application(
    name: str = Form(...),
    phone: str = Form(...),
    experience: str = Form(...),
    transport: str = Form(...),
    start: str = Form(""),
    lang: str = Form("en"),
):
    """Receive a hiring application and notify via Telegram."""
    
    exp_labels = {
        "indoor": "Indoor plant care",
        "nursery": "Garden center/nursery",
        "landscaping": "Landscaping",
        "other": "Other plant experience",
        "none": "No experience"
    }
    trans_labels = {
        "yes": "✅ Has car",
        "rideshare": "⚠️ Rideshare only",
        "no": "❌ No transport"
    }
    
    exp_label = exp_labels.get(experience, experience)
    trans_label = trans_labels.get(transport, transport)
    
    # Build Telegram message
    msg = (
        f"🌿 <b>New Applicant — {name}</b>\n"
        f"📱 {phone}\n"
        f"🪴 {exp_label}\n"
        f"🚗 {trans_label}\n"
        f"📅 Start: {start or 'Not specified'}\n"
        f"🌐 Lang: {lang}"
    )
    
    # Send Telegram notification
    if TELEGRAM_TOKEN:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={
                        "chat_id": TELEGRAM_CHANNEL,
                        "text": msg,
                        "parse_mode": "HTML"
                    },
                    timeout=10
                )
        except Exception as e:
            print(f"Telegram notify failed: {e}")
    
    # Log to file
    try:
        log_entry = {
            "name": name, "phone": phone, "experience": experience,
            "transport": transport, "start": start, "lang": lang,
            "timestamp": datetime.utcnow().isoformat()
        }
        log_path = os.path.join(os.path.dirname(__file__), "../../applicants.json")
        entries = []
        try:
            with open(log_path) as f:
                entries = json.load(f)
        except:
            pass
        entries.append(log_entry)
        with open(log_path, "w") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        print(f"Log failed: {e}")
    
    return {"ok": True, "message": "Application received"}
