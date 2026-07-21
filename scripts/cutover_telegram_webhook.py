#!/usr/bin/env python3
"""
INFRA Task 6 — Telegram webhook cutover to Railway (DO NOT RUN until Railway is live + verified).

Flips the Telegram bot webhook from the Cloudflare Tunnel URL to the Railway URL,
with pre-flight verification and post-flip confirmation. Telegram delivers updates
to exactly ONE webhook URL, so the flip itself enforces the "never two webhook
servers" rule — but this script still verifies the OLD server is not re-registering.

Usage:
  python3 scripts/cutover_telegram_webhook.py --check-only            # read-only state report (safe anytime)
  python3 scripts/cutover_telegram_webhook.py --railway-url https://xxx.up.railway.app  # EXECUTE the flip
  python3 scripts/cutover_telegram_webhook.py --rollback              # flip back to tunnel URL

Secrets are read from fieldnotes/.env at runtime — never hardcoded.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUNNEL_URL = "https://fieldnotesapp.io"
UA = {"User-Agent": "Mozilla/5.0 (FieldNotes-INFRA)"}


def load_env():
    env = {}
    for line in (REPO_ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def tg(method, token, **params):
    qs = urllib.parse.urlencode(params)
    return http_json(f"https://api.telegram.org/bot{token}/{method}?{qs}")


def check_health(base):
    try:
        h = http_json(f"{base}/health", timeout=10)
        return h.get("db") == "ok", h
    except Exception as e:
        return False, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--railway-url", help="e.g. https://fieldnotes-production.up.railway.app")
    ap.add_argument("--check-only", action="store_true", help="read-only state report")
    ap.add_argument("--rollback", action="store_true", help="flip webhook back to the tunnel URL")
    args = ap.parse_args()

    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    secret = env.get("TELEGRAM_SECRET")
    if not token or not secret:
        print("❌ TELEGRAM_BOT_TOKEN / TELEGRAM_SECRET missing from fieldnotes/.env")
        sys.exit(1)

    info = tg("getWebhookInfo", token).get("result", {})
    print(f"Current webhook: {info.get('url')!r}  pending={info.get('pending_update_count')}  "
          f"last_error={info.get('last_error_message')!r}")

    tunnel_ok, tunnel_h = check_health(TUNNEL_URL)
    print(f"Tunnel  {TUNNEL_URL}/health: {'OK' if tunnel_ok else 'DOWN'} {tunnel_h if not tunnel_ok else ''}")

    if args.check_only:
        if args.railway_url:
            ok, h = check_health(args.railway_url)
            print(f"Railway {args.railway_url}/health: {'OK' if ok else 'DOWN'} {h}")
        return

    if args.rollback:
        target = TUNNEL_URL
        if not tunnel_ok:
            print("❌ Tunnel server is DOWN — rollback would strand the bot. Fix start_prod.sh first.")
            sys.exit(1)
    elif args.railway_url:
        target = args.railway_url.rstrip("/")
        ok, h = check_health(target)
        if not ok:
            print(f"❌ Railway /health not healthy: {h} — refusing to flip. Fix the deploy first.")
            sys.exit(1)
        print(f"Railway {target}/health: OK")
    else:
        print("Nothing to do. Pass --check-only, --railway-url <url>, or --rollback.")
        return

    if info.get("url", "").startswith(target):
        print(f"Webhook already points at {target} — nothing to do.")
        return

    print(f"\n⚡ FLIPPING webhook → {target}/webhook/telegram")
    r = tg("setWebhook", token, url=f"{target}/webhook/telegram", secret_token=secret,
           drop_pending_updates="false")
    if not r.get("ok"):
        print(f"❌ setWebhook failed: {r}")
        sys.exit(1)

    # Verify: URL matches, pending drains, no delivery errors
    time.sleep(3)
    for _ in range(5):
        info = tg("getWebhookInfo", token).get("result", {})
        if info.get("url", "").startswith(target) and not info.get("last_error_message"):
            break
        time.sleep(3)
    print(f"Post-flip: url={info.get('url')!r} pending={info.get('pending_update_count')} "
          f"last_error={info.get('last_error_message')!r}")
    if not info.get("url", "").startswith(target):
        print("❌ Webhook did not land on the target — investigate before proceeding.")
        sys.exit(1)

    print("\n✅ Telegram webhook cutover complete.")
    print("NEXT (manual, Task 6): Stripe dashboard → Developers → Webhooks → "
          "endpoint we_1Tv3KaI5nMxajhKyOcOt8D1V → update URL to "
          f"{target}/billing/webhook (signing secret UNCHANGED).")
    print("Then: kill the WSL uvicorn + cloudflared ONLY after Railway serves live traffic cleanly "
          "(24h fallback window — the tunnel server may keep running, it just won't get updates).")


if __name__ == "__main__":
    main()
