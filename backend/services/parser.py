"""
FieldNotes — AI Note Parser Service
Parses worker voice/text notes into structured service data.
"""
import json
import time
import os
import httpx
from typing import Optional

# Provider selection — prefer DeepSeek (cheapest), fall back to others
LLM_PROVIDER = os.getenv("FIELDNOTES_LLM_PROVIDER", "deepseek")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = "https://api.openai.com/v1"

PARSE_PROMPT = """You are a field service note parser. Given a worker's voice or text note, extract structured data.

The worker is between stops and sending a quick note. Expect shorthand, typos, voice transcription errors. Handle it intelligently.

Return JSON with these fields:
{
  "account_hint": "the account/location name mentioned (try to match from known accounts)",
  "status": "all_good | issues_found | needs_supplies | follow_up_needed | urgent",
  "issues": ["list of problems found"],
  "supplies": ["list of supplies needed for next visit"],
  "follow_ups": ["list of things to do next time"],
  "customer_requests": ["things the client asked for"],
  "summary": "one-line clean summary of the stop"
}

Rules:
- If the worker says "all good" or equivalent, status is "all_good" and arrays can be empty
- Use the exact words the worker used — don't embellish
- If you're unsure about account, put your best guess in account_hint
- Keep it brief — these are between-stop notes, not reports

Worker note: {note}

JSON:"""


async def parse_note(worker_note: str, known_accounts: Optional[list[str]] = None) -> dict:
    """
    Parse a worker's voice/text note into structured service data.
    
    Args:
        worker_note: The raw text from the worker
        known_accounts: List of account names/shorthands to help matching
    
    Returns:
        dict with parsed fields
    """
    t0 = time.time()
    
    # Build context-aware prompt if we have known accounts
    prompt = PARSE_PROMPT.replace("{note}", worker_note)
    if known_accounts:
        accts_str = ", ".join(known_accounts)
        prompt = prompt.replace(
            "(try to match from known accounts)",
            f"Known accounts: {accts_str}. Match the worker's mention to one of these."
        )
    
    try:
        result = await _call_deepseek(prompt)
    except Exception as e:
        try:
            result = await _call_openai(prompt)
        except Exception:
            # Graceful fallback: basic extraction without AI
            result = _basic_parse(worker_note)
    
    elapsed_ms = int((time.time() - t0) * 1000)
    result["processing_time_ms"] = elapsed_ms
    return result


async def _call_deepseek(prompt: str) -> dict:
    """Call DeepSeek API for cheap, fast parsing."""
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY not set")
    
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500,
                "response_format": {"type": "json_object"}
            }
        )
        resp.raise_for_status()
        data = resp.json()
        return json.loads(data["choices"][0]["message"]["content"])


async def _call_openai(prompt: str) -> dict:
    """Fall back to OpenAI if DeepSeek fails."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")
    
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{OPENAI_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500,
                "response_format": {"type": "json_object"}
            }
        )
        resp.raise_for_status()
        data = resp.json()
        return json.loads(data["choices"][0]["message"]["content"])


def _basic_parse(note: str) -> dict:
    """Deterministic fallback — no AI, just basic extraction."""
    note_lower = note.lower()
    
    # Simple status detection
    if any(w in note_lower for w in ["all good", "all set", "done", "fine", "ok", "no issues"]):
        status = "all_good"
    elif any(w in note_lower for w in ["urgent", "emergency", "broke", "flood", "fire"]):
        status = "urgent"
    elif any(w in note_lower for w in ["need", "buy", "order", "out of", "supply"]):
        status = "needs_supplies"
    elif any(w in note_lower for w in ["next time", "follow up", "check back", "later"]):
        status = "follow_up_needed"
    else:
        status = "issues_found"
    
    return {
        "account_hint": note.split(":")[0].strip() if ":" in note else note[:30].strip(),
        "status": status,
        "issues": [],
        "supplies": [],
        "follow_ups": [],
        "customer_requests": [],
        "summary": note[:200].strip(),
        "processing_time_ms": 0
    }
