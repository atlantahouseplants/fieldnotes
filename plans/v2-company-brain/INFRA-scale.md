# INFRA — Paying-Client Infrastructure

**Status: 🔲 NOT STARTED** · Runs PARALLEL to P1/P2 · Blocks: charging real money

## The problem (concrete)

Today FieldNotes runs on: WSL laptop → uvicorn :8765 → Cloudflare Tunnel → fieldnotesapp.io, SQLite file. On 2026-07-20 the site was DOWN (530) because WSL restarted and nothing auto-recovered — a paying customer hitting that is a churn event. Nightly summaries silently died with it. Fine for beta. Unacceptable for revenue.

## Target architecture (boring on purpose)

```
Users → fieldnotesapp.io (Cloudflare, unchanged)
      → Managed host (Railway or Render, ~$7-20/mo)
          - uvicorn/FastAPI (same code, Dockerfile or nixpacks)
          - Managed Postgres (Railway PG / Render PG / Supabase free tier to start)
          - Health checks + auto-restart + deploy rollback
      → Telegram webhook / AgentPhone webhook → host URL (not tunnel)
```

### Why not keep the tunnel
Zero-cost but: dies with the laptop, no deploy rollback, no metrics, SQLite-on-laptop = no backups. The tunnel stays as the DEV bridge (it's great for that — see cloudflare-tunnel-expose skill).

### Migration steps

1. **Postgres move FIRST** (decouples data from laptop):
   - SQLAlchemy models already DB-agnostic. `DATABASE_URL` env switch; SQLite stays default for local dev.
   - Migrate data: dump SQLite → load PG (small dataset: <10 businesses, few hundred logs — script it, verify counts).
   - Alembic? Project has used manual CREATE-copy migrations so far. For PG, adopt Alembic NOW (paid data = schema discipline). One-time setup, worth it.
2. **Deploy to Railway** (recommendation over Render: simpler GH integration, PG built-in, usage pricing fine at our scale):
   - Repo auto-deploy on push to main. Env vars: XAI/DeepSeek/OpenAI keys, TELEGRAM_BOT_TOKEN + SECRET, AgentMail, Stripe (all from fieldnotes/.env — NEVER commit it).
   - Persistent process replaces start_prod.sh permanently.
3. **Flip webhooks** (Telegram setWebhook → Railway URL; Stripe webhook endpoint → Railway URL; AgentPhone when P3 lands). Keep tunnel URLs alive 24h during cutover as fallback.
4. **Observability minimum:** uptime monitor (UptimeRobot free → hits /health every 5min, alerts Geoff's Telegram via a simple cron or UptimeRobot's own email→AgentMail), Railway deploy notifications on, daily PG backup (Railway does auto-backups on paid PG; verify).
5. **DNS/SSL:** fieldnotesapp.io stays on Cloudflare → CNAME to Railway. www redirect unchanged.

### What does NOT change
- Vercel demo page (fieldnotes-livid.vercel.app/demo.html) — static + Edge Function, stays.
- ahp-pages.vercel.app compliance pages — stay.
- AgentMail, Stripe, Telegram bot — same accounts.

## Tasks

1. DATABASE_URL env switch + PG smoke test locally (docker postgres or Supabase free)
2. Alembic init + baseline migration from current models
3. SQLite→PG data migration script + count verification
4. Railway project + service + PG + env vars + GH auto-deploy
5. Health endpoint hardening (/health returns 200 + DB check)
6. Webhook cutovers (Telegram, Stripe) + 24h tunnel fallback window
7. UptimeRobot + alert path to Geoff
8. Retire start_prod.sh dependency; update SKILL.md runbook (mark tunnel as dev-only)
9. Cost report to Geoff: exact monthly $ before he approves flip

## Acceptance criteria

- [ ] `sudo reboot` of the WSL box = zero customer impact (test it)
- [ ] Deploy = git push; rollback = one click
- [ ] DB backed up daily, restore tested once
- [ ] All webhooks on host URLs; tunnel serving dev only
- [ ] Monthly cost documented and approved by Geoff

## Pitfalls

- **Cutover order matters:** data → host → webhooks. Flipping Telegram webhook before PG migration strands messages.
- Stripe webhook URL change = update the endpoint in Stripe dashboard (we_1Tv3KaI5nMxajhKyOcOt8D1V) AND keep signature secret the same.
- Don't let two servers (laptop + Railway) run the Telegram webhook simultaneously — same 409-conflict class of problem as duplicate pollers.
- SQLite-relative-path pitfall dies here — DATABASE_URL must be absolute/env in all contexts.
