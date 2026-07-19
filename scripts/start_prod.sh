#!/usr/bin/env bash
# FieldNotes production start (WSL). Both processes die on WSL restart — re-run this script.
# Tunnel ID: c99ac814-eb1f-4e2f-b725-a5c7c0f69f1f (config: ~/.cloudflared/config.yml)
export $(grep -v '^#' /home/wallg/.hermes/.env | grep -E '^(XAI_API_KEY|DEEPSEEK_API_KEY|OPENAI_API_KEY)=' | xargs)
cd "$(dirname "$0")/.."
nohup python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8765 > /tmp/fieldnotes-api.log 2>&1 &
nohup "$HOME/.local/bin/cloudflared" tunnel run fieldnotes > /tmp/fieldnotes-tunnel.log 2>&1 &
sleep 3
curl -s --max-time 10 https://fieldnotesapp.io/health && echo " — FieldNotes LIVE" || echo "check /tmp/fieldnotes-*.log"
