# INFRA Task 4 — Railway Deploy Runbook (click-through)

**Prereq:** Geoff has approved the cost report (`INFRA-cost-report.md`). ~30–45 min. Trial credits cover everything — no card needed to start.

**Cutover order (HARD RULE): data → host → webhooks.** This runbook does host+data only. Webhook flips are Task 6 and happen LAST, after the Railway app is verified serving real data.

---

## Step 1 — Railway account (2 min)

1. Go to https://railway.com → **Login → GitHub** (use the GitHub account that owns `atlantahouseplants/fieldnotes`).
2. Authorize Railway. You land on the dashboard with a $5 trial credit — no card.

## Step 2 — Create project + Postgres (3 min)

1. **New Project** → **Provision PostgreSQL** (do the DB first so its URL exists for the app).
2. Click the Postgres service → **Variables** tab → note `DATABASE_URL` exists (you won't copy it by hand — Railway can inject it).

## Step 3 — Deploy the app from GitHub (5 min)

1. Same project → **+ Create → GitHub Repo** → pick `atlantahouseplants/fieldnotes` (private is fine — Railway prompts for repo access; grant it).
2. Railway auto-builds with Nixpacks using the committed `nixpacks.toml`:
   - install: `pip install -r backend/requirements.txt`
   - start: `uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8765}`
3. First build takes ~2–4 min. It will **fail healthchecks/crash on first boot** because env vars aren't set yet — expected. Continue.

## Step 4 — Env vars (10 min)

App service → **Variables** → add each of these. **Names only below — copy VALUES from `fieldnotes/.env` on the WSL box (never paste them into git/chat):**

| Variable | Source | Notes |
|---|---|---|
| `DATABASE_URL` | Railway: **Add Variable → Reference → Postgres → `DATABASE_URL`** | use the reference picker, not a hand copy |
| `XAI_API_KEY` | fieldnotes/.env | primary parser |
| `DEEPSEEK_API_KEY` | fieldnotes/.env | parser fallback |
| `OPENAI_API_KEY` | **~/.hermes/.env** (NOT in fieldnotes/.env) | last-resort parser fallback |
| `TELEGRAM_BOT_TOKEN` | fieldnotes/.env | |
| `TELEGRAM_SECRET` | fieldnotes/.env | webhook secret header |
| `STRIPE_SECRET_KEY` | fieldnotes/.env | |
| `STRIPE_PUBLISHABLE_KEY` | fieldnotes/.env | |
| `STRIPE_PRICE_SOLO` | fieldnotes/.env | |
| `STRIPE_PRICE_TEAM` | fieldnotes/.env | |
| `STRIPE_PRICE_CREW` | fieldnotes/.env | |
| `STRIPE_WEBHOOK_SECRET` | fieldnotes/.env | stays the same after Task 6 URL flip |
| `FIELDNOTES_CRON_SECRET` | fieldnotes/.env | route-push / nightly summary crons |
| `FIELDNOTES_FOUNDER_CHAT_ID` | fieldnotes/.env | signup alarm → Geoff |
| `AGENTMAIL_API_KEY` | fieldnotes/.env | email primary |
| `AGENTMAIL_INBOX` | fieldnotes/.env | |

Every save triggers a redeploy — set them all, then let the last redeploy settle.

## Step 5 — Public domain + healthcheck (5 min)

1. App service → **Settings → Networking → Generate Domain** → you get `something.up.railway.app`.
2. **Settings → Healthcheck**: path `/health`, timeout 30s. (Startup runs `alembic upgrade head` automatically — first boot creates the schema.)
3. Verify: `curl https://<your>.up.railway.app/health` → `{"status":"healthy","db":"ok"}`.
   - If 503/crashloop: check deploy logs — 99% it's a missing/typo'd env var.

## Step 6 — Data migration (data → host) (10 min)

Run from the WSL box (script is committed, dry-run first):

```bash
cd /home/wallg/fieldnotes
# 1. safe snapshot of live sqlite
python3 -c "import sqlite3; s=sqlite3.connect('file:fieldnotes.db?mode=ro',uri=True); d=sqlite3.connect('/tmp/fn_migrate_snapshot.db'); s.backup(d)"
# 2. DRY RUN against Railway PG (prints counts, writes nothing)
python3 scripts/migrate_sqlite_to_pg.py --sqlite /tmp/fn_migrate_snapshot.db --pg '<Railway Postgres DATABASE_URL>'
# 3. real copy (single transaction, verifies every table + tenant breakdown)
python3 scripts/migrate_sqlite_to_pg.py --sqlite /tmp/fn_migrate_snapshot.db --pg '<Railway Postgres DATABASE_URL>' --yes
```

Expect: `✅ MIGRATION COMPLETE — all counts verified`. The 2 known `account_id=0` orphan actions get repaired to NULL (loud warning, expected).

Get the Railway `DATABASE_URL` for the CLI: Postgres service → Variables → copy the **public** URL (Railway provides a public proxy URL; the internal `*.railway.internal` one won't work from WSL).

## Step 7 — Verify the Railway app serves REAL data (5 min)

1. `curl https://<your>.up.railway.app/health` → db ok.
2. Open the live dashboard against Railway: `https://<your>.up.railway.app/app/live.html` — should show the real 42-account / 4-business data.
3. Spot-check a tenant: dashboard for biz 3 (AHP) shows its accounts; demo biz 2 unchanged. **Tenant isolation check: no cross-tenant data anywhere.**

## Step 8 — Cloudflare DNS (do WITH Task 6, not before)

CNAME `fieldnotesapp.io` → `<your>.up.railway.app` + add the custom domain in Railway (Settings → Networking → Custom Domain). **Do this at the same sitting as the webhook flips** — until then the tunnel keeps serving prod traffic.

---

## STOP — Task 6 gates (before ANY webhook flip)

- [ ] Railway app verified serving migrated data (Step 7)
- [ ] **Only then:** Telegram `setWebhook` → Railway URL (tunnel webhook dies immediately — this is the single-server rule, no overlap)
- [ ] Stripe dashboard: endpoint `we_1Tv3KaI5nMxajhKyOcOt8D1V` URL → Railway URL (same signing secret)
- [ ] Keep tunnel processes alive 24h as fallback, but NOT serving webhooks
- [ ] UptimeRobot (Task 7): monitor `https://fieldnotesapp.io/health` every 5 min → alert Geoff

## Rollback

If anything breaks after webhook flip: `setWebhook` back to the tunnel URL + restart `scripts/start_prod.sh`. Data is safe — sqlite file untouched, PG is a copy. One-click deploy rollback in Railway covers bad deploys.
