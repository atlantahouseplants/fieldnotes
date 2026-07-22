# FieldNotes Content Engine — Build Handoff (Jul 22, 2026)

You are building an autonomous social-media content creation + distribution engine for FieldNotes.

**First moves, before writing any code:**
1. Load the fieldnotes skill: `skill_view(name='fieldnotes')`
2. Read the source material listed at the bottom of this brief
3. Skim the `blue-green-marketing-engine` skill — it is the proven pattern for this architecture (⚠️ its crons stay PAUSED — do not resume or modify them; Geoff confirmed Jul 18)

## What FieldNotes is

Voice-first field service logging SaaS for owner-operators (pool techs, lawn care, pest control, cleaners). Techs voice-note after each job; AI writes the recap, answers questions from history, drafts client updates. Live in prod: fieldnotesapp.io (Railway + managed Postgres). Self-serve demo at /app/try.html. Repo: `/home/wallg/fieldnotes` (github.com/sarahbloom97/fieldnotes, main branch — push with `env -u GITHUB_TOKEN git push`).

## The task — 4 Hermes cron components

| # | Component | Schedule | Job |
|---|-----------|----------|-----|
| 1 | **Strategist** | Mon 9am ET | Picks the week's themes from the marketing docs; rotates: gate-code moment, proof-of-service, founder story (AHP: 18 accounts, "the new guy never calls me anymore"), demo CTA. Output: week plan in `marketing/content-engine/` |
| 2 | **Content Engine** | Mon/Wed/Fri 1pm ET | Writes one post pair (FB + IG variants) from the week plan; generates an image card via `image_generate` (FAL is live); saves to `marketing/content-engine/queue/` |
| 3 | **Approval Queue** | per post | Delivers the post to Geoff's Telegram: preview + "reply approve / edit … / skip". NOTHING posts without Geoff's explicit approval. Propose the reply-polling mechanism to Geoff before building it |
| 4 | **Poster** | on approval | Publishes via Meta Graph API (FB Page + IG Business); logs URL + timestamp to `marketing/content-engine/published.jsonl`; reports the link to Telegram |

## Decisions (Geoff-approved — do not re-litigate)

- **FieldNotes only** (not AHP channels)
- **Approval-gated v1** — full-auto only after Geoff explicitly flips it
- **Meta-direct** — FB Page + IG via Graph API. No X ($100/mo API), no TikTok yet (API approval friction), no aggregator

## You will need Geoff — do this EARLY, he goes offline evenings

Meta setup is the one interactive, credential-gated step (~20 min):
1. Create/claim the FieldNotes FB Page + IG Business account (business.facebook.com) if they don't exist — walk Geoff through it
2. Meta app at developers.facebook.com with scopes: `pages_manage_posts`, `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`
3. Long-lived user token → long-lived Page token (page tokens derived this way are effectively non-expiring — still write a verify/refresh procedure into the skill)
4. Tokens go in `~/.hermes/.env` (`FIELDNOTES_META_PAGE_TOKEN`, `FIELDNOTES_META_PAGE_ID`, `FIELDNOTES_META_IG_ID`)

Do NOT fake or stub tokens. If Geoff isn't available, build everything else, test in dry-run mode, and leave token setup as the one explicit remaining step.

## Source material (read before writing any post)

- `marketing/social-posts.md` — voice, new copy, 15-sec video script
- `marketing/launch-week1-calendar.md` — cadence backbone
- `marketing/outreach-templates.md`
- `frontend/index.html` — landing copy (live positioning)
- Product truth: the fieldnotes skill + `backend/` — never invent features

## Guardrails

- **Voice:** plain, direct, owner-operator. No AI-isms ("revolutionize", "unlock", "supercharge"). Only claims backed by the repo or Geoff — a subagent once invented "join hundreds of businesses"; that must never ship
- **Cost:** Geoff is cost-sensitive. Cap FAL image generations at 5/day; log every generation (model, cost) to `marketing/content-engine/costs.jsonl`
- **Never** send emails or publish anything publicly without Geoff's explicit approval during this build
- **Prod DB is read-only** for this project (SELECT only, and only if a stat is genuinely needed for a post)
- When Geoff shoots the 15-second gate-code video, the Content Engine should treat it as a first-class asset (he hasn't shot it yet — check marketing/content-engine/ for it)

## Done means

1. All 4 crons created, visible in `cronjob action='list'`, delivered to Geoff's Telegram
2. One full end-to-end dry run: post generated → Telegram approval → Geoff approves → real post live on FB + IG → link reported back
3. New skill `fieldnotes-content-engine` saved: architecture, cron IDs, Meta token setup + refresh procedure, pitfalls
4. `marketing/content-engine/README.md` documents the system for future sessions
5. Everything committed + pushed; summary to Geoff
