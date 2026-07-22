# FieldNotes Content Engine

Autonomous social-media content creation + approval-gated distribution for FieldNotes
(FB Page + IG Business via Meta Graph API). Built Jul 22, 2026. Companion skill:
`fieldnotes-content-engine` (has Meta token setup/refresh + pitfalls).

## Architecture — 4 Hermes cron components

| # | Component | Schedule (ET) | What it does |
|---|-----------|---------------|--------------|
| 1 | **Strategist** (`FieldNotes Content Strategist`) | Mon 9:00am | Reads the marketing docs, picks the week's 3 themes (rotates: gate-code moment, proof-of-service, founder story, objection handling, solo-op-to-first-hire, demo CTA), writes `plans/week-YYYY-MM-DD.md` |
| 2 | **Content Engine** (`FieldNotes Content Engine`) | Mon/Wed/Fri 1:00pm | Writes one post pair (FB + IG variants) from the week plan, generates an image card via `image_generate` (FAL, cap 5/day, logged to `costs.jsonl`), saves `queue/<id>.json` (status=`pending`) |
| 3 | **Approval Queue** (`FieldNotes Approval Delivery`) | Mon/Wed/Fri 1:20pm | Finds pending queue items, delivers preview to Geoff's Telegram in a *continuable thread*. Geoff replies `approve` / `skip` / `edit: <new text>` — his reply wakes the agent, which runs `fieldnotes_post_meta.py approve|skip|edit` |
| 4 | **Poster** (`FieldNotes Poster`) | every 15 min | `no_agent` script: publishes every `status=approved` item to FB + IG, appends to `published.jsonl`, Telegrams Geoff the live links. Silent when nothing is approved |

**Nothing posts without Geoff's explicit approval.** Full-auto only if Geoff explicitly flips it.

## Files

```
marketing/content-engine/
├── README.md            ← this file
├── HANDOFF.md           ← original build brief (Jul 22)
├── plans/               ← Strategist output: week-YYYY-MM-DD.md
├── queue/               ← one JSON per post (schema below)
│   └── assets/          ← generated image cards (<id>.png)
├── published.jsonl      ← append-only: {id, theme, fb_url, ig_url, at}
└── costs.jsonl          ← append-only: {at, kind, model, cost_usd, post_id}
```

## Queue item schema (`queue/<id>.json`)

```json
{
  "id": "2026-07-24-gate-code",
  "theme": "gate-code moment",
  "fb_text": "...(FB variant, hashtags inline or none)...",
  "ig_text": "...(IG variant + hashtags)...",
  "image": {"local_path": "/abs/path/queue/assets/<id>.png",
            "source_url": "https://... (public URL — IG needs this)",
            "prompt": "...", "model": "...", "cost_usd": 0.0},
  "status": "pending | approved | skipped | published | failed",
  "created_at": "ISO", "approved_at": "ISO", "published": {"fb_url": "...", "ig_url": "...", "at": "ISO"}
}
```

`<id>` = `YYYY-MM-DD-<theme-slug>` (one post per run day).

## Poster script

`~/.hermes/scripts/fieldnotes_post_meta.py` — stdlib-only:

```
post_meta.py status                 # queue states
post_meta.py approve <queue.json>   # mark approved (agent runs this on Geoff's reply)
post_meta.py skip <queue.json>
post_meta.py edit <queue.json> fb|ig <textfile>
post_meta.py publish [--dry-run]    # publish all approved (the Poster cron runs this)
post_meta.py verify-token           # check Meta creds + token expiry
```

Meta creds live in `~/.hermes/.env`: `FIELDNOTES_META_PAGE_TOKEN`,
`FIELDNOTES_META_PAGE_ID`, `FIELDNOTES_META_IG_ID`
(+ `FIELDNOTES_META_APP_ID` / `FIELDNOTES_META_APP_SECRET` for token refresh/debug).
Never inline token values in code — read at runtime.

## Voice rules (binding for every post)

- Plain, direct, owner-operator. No AI-isms ("revolutionize", "unlock", "supercharge", "game-changer").
- Only claims backed by the repo or Geoff. **Never invent traction** — no customer counts,
  testimonials, ratings. Honest framings: "Built for crews who run their business from text
  messages", "30 days free, no credit card".
- Founder story facts that ARE allowed (from Geoff): AHP, 18 accounts, commercial plant service,
  "my new guy never calls me anymore", "I built it for my own business".
- Every post ends with ONE CTA. Primary: `fieldnotesapp.io/app/try.html` (60-sec demo).
- Source voice: `marketing/social-posts.md`, `marketing/outreach-templates.md`, `frontend/index.html`.
- Product truth: the `fieldnotes` skill + `backend/` — never invent features.

## Cost guardrail + image tiers

Max **5 FAL image generations/day**. Content Engine counts today's `costs.jsonl` entries before
calling `image_generate`. Two-tier image strategy:
1. `image_generate` (FAL) when available and under cap — **currently UNAVAILABLE: FAL_KEY is not
   set in ~/.hermes/.env** (Jul 22; uncomment/set it or use Nous Portal managed image gen).
2. PIL fallback: `~/.hermes/scripts/fieldnotes_card.py chat|card` — deterministic branded cards
   (exact charcoal/lime, crisp text, $0). Default tier while FAL is unset.

Every generation is logged: `{at, kind:"image", model, cost_usd, post_id}`.

## Public image hosting (required for IG)

IG publishing needs a public `image_url`. Cards are copied to `frontend/assets/cards/<id>.png`,
committed, and pushed (`env -u GITHUB_TOKEN git push`); Railway auto-deploys and the image is
public at `https://fieldnotesapp.io/app/assets/cards/<id>.png`. FB uses local multipart upload
and works even without hosting.

## Gate-code video asset

Geoff hasn't shot the 15-sec gate-code video yet (script in `marketing/social-posts.md`).
When it lands in this directory, the Content Engine treats it as a first-class asset:
build the post around it, upload as video to FB (IG Reels later).
