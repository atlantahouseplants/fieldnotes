"""FieldNotes — AgentPhone (SMS) integration. P3.

Outbound: POST /v1/messages (snake_case agent_id/to_number/body — verified live).
Inbound: master account webhook POSTs to our /webhook/sms, signed with
x-webhook-signature: sha256=<HMAC-SHA256(secret, "{x-webhook-timestamp}.{raw_body}")>
(scheme verified against a live test delivery, Jul 2026).

Pitfalls baked in:
- Python urllib default UA gets Cloudflare 1010 on their CDN — always a browser UA.
- 10DLC: outbound 403s until carrier registration clears (approved 2026-07-23).
- 429s observed (125s cooldown on register endpoint) — callers must not retry-loop.
"""
import hmac
import hashlib
import os
import re

import httpx

AGENTPHONE_API_KEY = os.getenv("AGENTPHONE_API_KEY", "")
AGENTPHONE_AGENT_ID = os.getenv("AGENTPHONE_AGENT_ID", "")
AGENTPHONE_NUMBER = os.getenv("AGENTPHONE_NUMBER", "")
WEBHOOK_SECRET = os.getenv("AGENTPHONE_WEBHOOK_SECRET", "")

_API = "https://api.agentphone.ai"
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


async def send_sms(to_number: str, body: str) -> dict:
    """Send one SMS via the shared FieldNotes number. Returns {ok, ...}.

    Never raises — a failed send must not break the webhook reply path.
    """
    if not AGENTPHONE_API_KEY or not AGENTPHONE_AGENT_ID:
        return {"ok": False, "error": "AGENTPHONE_API_KEY/AGENTPHONE_AGENT_ID not configured"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_API}/v1/messages",
                headers={"Authorization": f"Bearer {AGENTPHONE_API_KEY}",
                         "User-Agent": _BROWSER_UA},
                json={"agent_id": AGENTPHONE_AGENT_ID,
                      "to_number": to_number, "body": body},
            )
            try:
                data = dict(resp.json())
            except Exception:
                data = {"raw": resp.text[:500]}
            data["ok"] = resp.status_code < 300
            if resp.status_code == 429:
                data["error"] = "rate_limited"
            return data
    except Exception as e:
        return {"ok": False, "error": str(e)}


def verify_webhook(raw_body: bytes, timestamp: str, signature_header: str) -> bool:
    """Verify x-webhook-signature: sha256=<hex HMAC(secret, "{ts}.{raw}")>.

    Verified against a live AgentPhone test delivery (Jul 2026): ts.body MATCH,
    body_only and ts_body_concat do not.
    """
    if not WEBHOOK_SECRET:
        return False
    sent = signature_header.split("=", 1)[1] if "=" in signature_header else signature_header
    calc = hmac.new(WEBHOOK_SECRET.encode(),
                    f"{timestamp}.".encode() + raw_body,
                    hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc, sent)


def normalize_e164(raw: str) -> str:
    """'404-493-2910' / '(404) 493-2910' / '14044932910' → '+14044932910' (US default)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}" if len(digits) >= 11 else ""
