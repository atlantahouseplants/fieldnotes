"""
FieldNotes — AI Note Parser Service
Parses worker voice/text notes into structured service data.
"""
import json
import re
import time
import os
import httpx
from typing import Optional

# Provider selection — chain order: Moonshot(Kimi) → xAI/Grok → DeepSeek → OpenAI → basic.
# Moonshot is first because as of Aug 2026 xAI (403), DeepSeek (402), and OpenAI (429)
# are ALL credit-exhausted — the chain was silently degrading to _basic_parse in prod.
# When xAI is topped up, move _call_xai back to the front (one-line reorder).
LLM_PROVIDER = os.getenv("FIELDNOTES_LLM_PROVIDER", "xai")
MOONSHOT_API_KEY = os.getenv("MOONSHOT_API_KEY", "")
MOONSHOT_BASE = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
MOONSHOT_MODEL = os.getenv("MOONSHOT_MODEL", "kimi-k3")
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE = "https://api.x.ai/v1"
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


def build_prompt(worker_note: str, known_accounts: Optional[list[str]] = None) -> str:
    """Build the parse prompt exactly as production does (shared with eval harness)."""
    prompt = PARSE_PROMPT.replace("{note}", worker_note)
    if known_accounts:
        accts_str = ", ".join(known_accounts)
        prompt = prompt.replace(
            "(try to match from known accounts)",
            f"Known accounts: {accts_str}. Match the worker's mention to one of these."
        )
    return prompt


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
    prompt = build_prompt(worker_note, known_accounts)
    
    try:
        if MOONSHOT_API_KEY:
            result = await _call_moonshot(prompt)
        elif XAI_API_KEY:
            result = await _call_xai(prompt)
        else:
            result = await _call_deepseek(prompt)
    except Exception as e:
        try:
            result = await _call_xai(prompt)
        except Exception:
            try:
                result = await _call_deepseek(prompt)
            except Exception:
                try:
                    result = await _call_openai(prompt)
                except Exception:
                    # Graceful fallback: basic extraction without AI
                    result = _basic_parse(worker_note)
    
    elapsed_ms = int((time.time() - t0) * 1000)
    result["processing_time_ms"] = elapsed_ms
    return result


async def _call_moonshot(prompt: str) -> dict:
    """Call Moonshot (Kimi) API for parsing. OpenAI-compatible endpoint.
    Note: kimi-k3 rejects temperature != 1, so we omit it. No response_format
    support guaranteed — extract the JSON object from the content defensively."""
    if not MOONSHOT_API_KEY:
        raise ValueError("MOONSHOT_API_KEY not set")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{MOONSHOT_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {MOONSHOT_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": MOONSHOT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                # kimi-k3 is a reasoning model — reasoning tokens count against
                # max_tokens; 600 truncates the JSON or leaves content EMPTY.
                "max_tokens": 4000,
            }
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data["choices"][0]["message"]["content"] or "").strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```\s*$", "", content)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Defensive: model may wrap JSON in prose
            start, end = content.find("{"), content.rfind("}")
            if start == -1 or end <= start:
                raise ValueError(f"Moonshot returned non-JSON: {content[:100]}")
            return json.loads(content[start:end + 1])


async def _call_xai(prompt: str) -> dict:
    """Call xAI Grok API for parsing."""
    if not XAI_API_KEY:
        raise ValueError("XAI_API_KEY not set")
    
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{XAI_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "grok-4.5",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500,
                "response_format": {"type": "json_object"}
            }
        )
        resp.raise_for_status()
        data = resp.json()
        return json.loads(data["choices"][0]["message"]["content"])


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
