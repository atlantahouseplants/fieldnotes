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

## Surfaces (Jul 23+)

One queue item can fan out to multiple distribution surfaces via a `surfaces` dict
(poster: `fieldnotes_post_meta.py publish`). Backward-compatible: items without
`surfaces` post legacy fb_feed+ig_feed only.

| surface | channel | media | notes |
|---|---|---|---|
| fb_feed | FB Page | image | long text OK |
| fb_video | FB Page | video | USE THIS for FB video — `fb_reel` is rejected by FB's reel ingest ("unable to process the media", even with AAC audio, Jul 23 ×2) |
| ig_feed | IG | image | `shouldShareToFeed: true` required |
| ig_story | IG | image | `shouldShareToFeed: false` required (schema demands the field) |
| ig_reel | IG | video | `shouldShareToFeed: true` required; Buffer returns id, reel processes async |
| tiktok | TikTok | video | caption + hashtags |

**Video requirements (hard-won):** every render MUST include an audio stream —
silent MP4s get rejected/mishandled (renderer now adds silent AAC via lavfi
anullsrc). Re-hosted videos MUST use a NEW filename: fieldnotesapp.io sits behind
Cloudflare with `max-age=14400` edge cache — overwriting a URL keeps serving stale
bytes to Buffer for up to 4h.

**Daily schedule (Jul 23+):** Content Engine runs EVERY day 1pm. Format rotation:
Mon/Wed/Sat feed · Tue/Fri video (tiktok+ig_reel+fb_video) · Thu feed+story ·
Sun story-only. Strategist plans 7 slots/week accordingly.

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

## Video layer — HyperFrames (PRIMARY since Jul 25) + PIL motion cards (fallback)

**Primary: HyperFrames multi-scene compositions + voiceover.**
`~/.hermes/scripts/fieldnotes_hf_video.py` (repo copy: `video-previews/fieldnotes_hf_video.py`)
takes a scene spec JSON, generates a branded composition (charcoal #1A1D21 / lime #C6F135 /
Inter), runs `hyperframes check` + render, builds a NARRATION track, and self-verifies
(ffprobe + frame stddev). Scene types: `hook` (kicker + headline + dim sub), `value` (1-3 idea
beats), `brand` (rule + FieldNotes + tagline), `cta` (STANDARDIZED end-card —
fieldnotesapp.io/app/try.html, never customized; the reusable sub-composition piece, identical
across all videos). One composition = one render = no stitching. Renders ~30-60s on WSL.
Generated compositions live in `hf-compositions/<id>/` (gitignored). Demos:
`video-previews/template-demo.mp4` (silent), `video-previews/narration-demo.mp4` (voiced).
If the script exits non-zero → fall back to the PIL renderer below.

**Narration ("say" per scene, $0):** every scene carries a "say" line (spoken-style, <= 15
words). The generator synthesizes each via **edge-tts** (Microsoft, free, no key — venv has
edge-tts 7.2.7), measures it, and STRETCHES the scene duration to fit natural speech
(audio + 0.35s) — voice sets the pace, never tempo-crushed. Scenes then get their segments
concatenated and muxed as the video's AAC track (silent scenes get silence; no "say" at all
→ silent AAC). Default voice `en-US-GuyNeural`, overridable via spec "voice". Upgrade paths:
ElevenLabs free tier (10k chars/mo — our ~4k/mo usage fits) for more human voices; ElevenLabs
Starter ($5/mo) to clone GEOFF's voice for founder-story posts. HeyGen avatar ($29/mo,
free tier is watermarked/unusable) only after a free-tier taste test.

**Fallback: `video-previews/fieldnotes_render_video.py`** — vertical 1080x1920 MP4s from
PIL pre-rendered frames + ffmpeg xfade. Three style previews delivered to Geoff Jul 22
(`style-preview-{A,B,C}.mp4`); **default template = A (lower-third bar)**. Timing constants
are EMPIRICAL: T=3.6s/frame, xfade offsets 3.1/6.7 → 6.73s final. Verify every render:
ffprobe (duration/1080x1920/h264) + extracted frame at t=5s with PIL stddev > 10.
Music undecided (all videos are silent + silent AAC).

## Gate-code video asset

Geoff hasn't shot the 15-sec gate-code video yet (script in `marketing/social-posts.md`).
When it lands in this directory, the Content Engine treats it as a first-class asset:
build the post around it, upload as video to FB (IG Reels later).
