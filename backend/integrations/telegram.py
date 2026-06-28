"""
FieldNotes — Telegram Bot Integration
Handles receiving messages and sending confirmations.
"""
import os
import httpx
from typing import Optional

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


async def send_message(chat_id: str, text: str) -> dict:
    """Send a message to a Telegram chat."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}
    
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML"
            }
        )
        return resp.json()


async def send_confirmation(chat_id: str, account_name: str, status: str) -> None:
    """Send a quick confirmation to a worker after processing their note."""
    emoji = "✅" if status == "all_good" else "⚠️"
    msg = f"{emoji} <b>{account_name}</b> logged — {status.replace('_', ' ').title()}"
    await send_message(chat_id, msg)


async def set_webhook(webhook_url: str) -> dict:
    """Register the webhook URL with Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}
    
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
            json={"url": webhook_url}
        )
        return resp.json()


async def delete_webhook() -> dict:
    """Remove the webhook (for testing)."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}
    
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
        )
        return resp.json()


async def get_updates(offset: int = 0, timeout: int = 10) -> dict:
    """Poll for new messages (dev mode — no HTTPS needed)."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not configured"}
    
    async with httpx.AsyncClient(timeout=timeout + 5) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            json={"offset": offset, "timeout": timeout}
        )
        return resp.json()
