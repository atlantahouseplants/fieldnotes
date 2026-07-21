# INFRA Task 9 — Railway Cost Report (exact, from railway.com/pricing, July 2026)

**Geoff approval required before Task 4 (deploy). Nothing below costs money until you click "Deploy."**

## Railway rates (per-second billed, monthly equivalents at 30 days)

| Resource | Rate | Monthly equiv |
|---|---|---|
| Memory | $0.00000386 / GB-sec | **$10.01 / GB / mo** |
| CPU | $0.00000772 / vCPU-sec | **$20.01 / vCPU / mo** |
| Volume (PG data) | $0.00000006 / GB-sec | **$0.16 / GB / mo** |
| Egress | $0.05 / GB | as used |

## FieldNotes estimate (2 services: uvicorn app + Postgres)

| Service | RAM | est. CPU avg | Storage | Monthly |
|---|---|---|---|---|
| App (FastAPI/uvicorn) | 0.25–0.5 GB | ~0.02 vCPU (idle + webhook bursts) | — | **$3.00–5.50** |
| Postgres | 0.25–0.5 GB | ~0.01 vCPU | 1 GB volume | **$2.90–5.40** |
| Egress (webhook JSON + dashboards, <1 GB) | | | | **<$0.05** |
| **Total usage** | | | | **≈ $6–11 / mo** |

## Plan: **Hobby — $5/mo, includes $5 of usage credits**

- Usage ≤ $5 → pay just $5. Usage over $5 → billed for the overage.
- Expected actual bill: **$5–11/mo**. Worst case under real load: ~$15/mo.
- Hobby includes: **2 custom domains** (needed for fieldnotesapp.io), healthcheck endpoints, one-click rollback, 7-day logs, 5 GB volume cap, 50 services. Free/$1 tier has NO custom domains after trial — not an option for us.
- Pro ($20/mo) is unnecessary at this scale.

## Free start

New Railway accounts get a **30-day trial with $5 credits, no credit card required**. The entire deploy + migration + cutover can be tested on trial credits. You only add a card when the trial ends (or to keep it running past $5 usage).

## What this replaces

- Current cost: $0 (WSL laptop + Cloudflare Tunnel) — but site dies on every WSL restart (already caused one full outage, Jul 20).
- After cutover: laptop reboots = zero customer impact. Nightly summaries + watchdog stop depending on the laptop being awake.
- Cloudflare (DNS/proxy), Vercel (demo + compliance pages), AgentMail, Stripe, Telegram: **unchanged, $0 delta**.

## Approval ask

**Approve Hobby plan ($5/mo, expect $5–11/mo total) → then Task 4 (deploy) can proceed.** Deploy itself is guided by `INFRA-railway-runbook.md` — click-through, ~30–45 min, trial credits first.
