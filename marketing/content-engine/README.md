# FieldNotes Content Engine

Autonomous social-media content creation + **FULL-AUTO** distribution for FieldNotes.
Built Jul 22, 2026; approval gate removed same evening (Geoff's call — "I trust you").
Companion skill: `fieldnotes-content-engine` (credentials + pitfalls).

**Publishing = Buffer** (GraphQL API, `https://api.buffer.com`) — pivoted same day from
the original Meta-direct plan; no Meta app/tokens needed. Geoff's Buffer account has 3
channels connected: FB Page `FieldNotes`, IG `fieldnotesappio`, TikTok `fieldnotesappio`
(TikTok unused in v1).

## Architecture — 3 Hermes cron components

| # | Component | Schedule (ET) | What it does |
|---|-----------|---------------|--------------|
| 1 | **Strategist** (`FieldNotes Content Strategist`) | Mon 9:00am | Reads the marketing docs, picks the week's 3 themes (rotates: gate-code moment, proof-of-service, founder story, objection handling, solo-op-to-first-hire, demo CTA; one VIDEO slot/week), writes `plans/week-YYYY-MM-DD.md` |
| 2 | **Content Engine** (`FieldNotes Content Engine`) | Mon/Wed/Fri 1:00pm | Writes one post pair (FB + IG variants) from the week plan, generates an image card, saves `queue/<id>.json` with **status=`approved` (auto-post)** |
| 3 | **Poster** (`FieldNotes Poster`) | every 15 min | `no_agent` script `fieldnotes_poster.sh` (wrapper → `fieldnotes_post_meta.py publish`): publishes every approved item to FB + IG, appends to `published.jsonl`, Telegrams Geoff the live links. Silent when nothing is approved |

**FULL AUTO — no approval gate.** Geoff gets the Poster's live-link report after each post
ships. Voice + privacy rules below are the quality control — they are binding in every cron prompt.

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
post_meta.py verify-token           # check Buffer token + list channels
post_meta.py test-draft             # create+delete a Buffer draft (safe plumbing check)
```

Credential: `BUFFER_ACCESS_TOKEN` in `~/.hermes/.env` — Buffer Personal Key
(publish.buffer.com/settings/api), 1-year expiry (Jul 22 2027). Channel IDs are
constants in the script. **Refresh:** settings/api → New Key → 1 year → all scopes →
replace in `.env`, then `verify-token`. Never inline token values in code.

## Voice + privacy rules (binding for every post)

- Plain, direct, owner-operator. No AI-isms ("revolutionize", "unlock", "supercharge", "game-changer").
- Only claims backed by the repo or Geoff. **Never invent traction** — no customer counts,
  testimonials, ratings. Honest framings: "Built for crews who run their business from text
  messages", "30 days free, no credit card".
- **AHP PRIVACY (Geoff's hard rule, Jul 22 evening):** never the real account count, revenue,
  or client/company names in any post. Business size is ALWAYS phrased as
  **"50+ corporate and commercial properties in the Metro Atlanta area"** (intentionally
  reads bigger than reality — competitors watch). Founder-story angles stay:
  "my new guy never calls me anymore", "I built it for my own business", commercial plant
  service in Atlanta. Generic/fictional place names ("the Riverside office") are fine.
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

## Video layer — motion-card renderer (Phase A live Jul 22)

`video-previews/fieldnotes_render_video.py` — vertical 1080x1920 MP4s from PIL pre-rendered
frames + ffmpeg xfade (no drawtext escaping, no Ken Burns). Three style previews delivered to
Geoff Jul 22 (`style-preview-{A,B,C}.mp4`); **default template = A (lower-third bar)** —
Geoff delegated the pick. Timing constants are EMPIRICAL: T=3.6s/frame, xfade offsets 3.1/6.7
→ 6.73s final. Verify every render: ffprobe (duration/1080x1920/h264) + extracted frame at
t=5s with PIL stddev > 10. Phase B: wire into the Friday video slot → TikTok via Buffer.
Music undecided (previews are silent).

## Gate-code video asset

Geoff hasn't shot the 15-sec gate-code video yet (script in `marketing/social-posts.md`).
When it lands in this directory, the Content Engine treats it as a first-class asset:
build the post around it, upload as video to FB (IG Reels later).
