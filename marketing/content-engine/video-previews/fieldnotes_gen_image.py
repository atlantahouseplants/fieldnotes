#!/usr/bin/env python3
"""FieldNotes skit image generator — Gemini 2.5 Flash Image (nano banana), $0.

Usage:
    fieldnotes_gen_image.py "<prompt>" <out.png>

Requires GEMINI_API_KEY (or GOOGLE_API_KEY) in env or ~/.hermes/.env.
Prints "OK <path> <bytes>" on success; exit 1 with NO IMAGE on failure.

Prompt conventions for skits (learned Jul 26):
- Always: "Photorealistic candid photo, vertical 9:16 portrait, documentary style"
- ALWAYS include "no readable text, no watermarks" — AI text renders as gibberish
- Avoid phone screens with visible UI text; avoid logos/signage
- Character consistency trick: reuse the same character description verbatim
  across prompts (navy work shirt + cap, 30s, etc.)
"""
import base64
import json
import os
import sys
import urllib.request


def load_key():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY=") or line.startswith("GOOGLE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def main():
    prompt, out = sys.argv[1], sys.argv[2]
    key = load_key()
    if not key:
        print("NO KEY: set GEMINI_API_KEY or GOOGLE_API_KEY", file=sys.stderr)
        sys.exit(1)
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={key}",
        data=json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read())
    except Exception as e:
        print(f"NO IMAGE: request failed: {e}", file=sys.stderr)
        sys.exit(1)
    for p in d.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        if "inlineData" in p:
            data = base64.b64decode(p["inlineData"]["data"])
            with open(out, "wb") as f:
                f.write(data)
            print(f"OK {out} {len(data)}")
            return
    print(f"NO IMAGE: {json.dumps(d)[:300]}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
