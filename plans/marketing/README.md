# Self-Serve Demo + Marketing Refresh

**Status board (update + commit after every work session):**

| Task | Name | Status | Owner agent | Last touched |
|------|------|--------|-------------|--------------|
| M1 | `/api/demo` public endpoint (biz-2 locked, rate-limited) | 🔲 not started | — | — |
| M2 | Phone-mockup demo UI (`frontend/try.html`) | 🔲 not started | — | — |
| M3 | Landing page rebuild (demo above the fold) | 🔲 not started | — | — |
| M4 | Pricing matrix update (P6–P8 features) | 🔲 not started | — | — |
| M5 | Social/launch copy rewrite (gate-code moment) | 🔲 not started | — | — |

Legend: 🔲 not started · 🔨 in progress · ✅ done

**Workflow rules:** same as v2 hub — `git pull` first (parallel sessions share this repo), claim a task (🔨 + your session name) before starting, commit + update this board after every session, push to origin at the end. Format: `mkt(Mx): <what changed>`.

---

## Why this exists (Geoff, Jul 22)

The product is v2 (the company brain) but every public surface still sells v1 (a logging tool). The marketing must show **what FieldNotes does, who it's for, the value it brings — then let the prospect EXPERIENCE it themselves.** Geoff's mandate: "a great first-touch user experience where they can clearly see the value and then feel the power of it themselves."

## The aha moment (design center — everything serves this)

**Primary aha:** *"I just asked it the gate code... and it knew. My new guy would never have to call me."*
(Geoff's own story — he asked his AI for a gate code from the road and it pulled it up. ICP = owner-operator about to make their first hire; all the knowledge is in their head and their phone rings all day.)

**Closer aha (gets the credit card):** the client recap email — *"Your crew logs a note, your customer gets a professional visit summary. Timestamped proof you were there."* (Dispute armor — the One Street "Claudia" scenario.)

**NOT the aha:** note parsing. That's v1 table stakes and looks like "software." The old Vercel parse-only demo (fieldnotes-livid.vercel.app/demo.html + api/parse.js) is superseded by this build.

**Tagline:** "Never call the office again." Demo kicker: "You just ran a whole visit from a text message."

## M1 — `POST /api/demo` endpoint

Public demo endpoint that lets a webpage drive the LIVE demo tenant (**business_id=2, "Precision HVAC", 12 seeded accounts, Riverside recap-enabled** — verified in prod PG Jul 22).

- `POST /api/demo { "text": "..." }` → routes the text through the REAL pipeline for biz 2 only:
  - Question → `services/qa.py` answer (returns answer + sources)
  - Note → `services/ingest.py::persist_parsed_note` (log + action queue + task close — the same path reps use)
- **HARD RULES:**
  - `business_id` is hardcoded to 2 server-side. No parameter, no path, no way to touch another tenant. Tenant isolation is the #1 review criterion even here.
  - **Rate-limited** (per-IP, e.g. ~10/hr + ~30/day — check for an existing limiter in deps/main first; a simple in-memory or sqlite-backed counter is fine). This is a public unauthenticated LLM-calling endpoint — Geoff's unit-economics fear applies. Consider DeepSeek-primary for demo calls if the chain makes that cheap to express; document what you chose.
  - **NEVER sends real email/Telegram.** The recap step (M2 tap 3) must render the recap preview from `services/recaps.py` draft logic WITHOUT calling send — return the drafted client_text for display only. Set `FIELDNOTES_EMAIL_STUB=1`-equivalent behavior inside the demo path itself, not via env.
  - Honest errors in plain words ("demo's busy, try again in a bit" on rate limit) — never raw 500s.
- Tests: `scripts/test_m1_demo.py` (fresh port — check the port registry in skill pitfall #33; 8775+ likely free). Assert: question answered with source, note logs + appears in biz 2 logs, task close works, rate limit kicks in, and a probe that NO cross-tenant data is reachable.

## M2 — Phone-mockup demo UI (`frontend/try.html`)

A fake text-message thread (phone frame) wired to `/api/demo`. **Zero install, zero signup** — this replaces "download Telegram and text our bot" as the first-touch demo (ICP doesn't have Telegram; that friction kills the funnel).

- **Three one-tap prompts, in story order** (tappable chips that "send" the message):
  1. 📝 **Log it:** "Riverside: replaced the belt on unit 1" → parsed log card appears + the open "replace belt" task shows ✅ closed (P7 payoff)
  2. ❓ **Ask it:** "What's the gate code for Riverside?" → instant answer with source cited (P1 payoff — THE aha)
  3. 📧 **Prove it:** "show the recap" (or auto-follows tap 1) → renders the client-safe recap email preview (P8 payoff)
- Free-text input also works ("type your own") but the 3 chips are the guided path.
- After tap 3: kicker line + CTA → "Start your 30-day trial — no credit card" → start.html.
- **UI standard (BINDING — Geoff's mandate, see skill):** mobile-first at 390px, touch targets ≥44px, brand `#1b5e20` green + white rounded cards + system font stack (match `frontend/import.html`), plain HTML/CSS/JS only — NO frameworks/build step, all user-derived strings through `esc()`, immediate plain-words confirmations, loading state while waiting (LLM latency ~2-5s — show a "typing…" bubble).
- Verify in a real browser per skill pitfall #35: `browser_navigate` + `browser_console` — computed-style assertions + drive the real handlers (`dispatchEvent`), assert against the live API. No screenshot→vision loops.

## M3 — Landing rebuild (`frontend/index.html`)

- **Demo above the fold** (embed the phone mockup or link prominently to try.html — designer's choice, but a first-touch visitor must be one tap from the magic).
- Hero: "Never call the office again." Sub: "Your crew texts FieldNotes. It logs the work, answers their questions, and proves every visit to your customers."
- Who it's for: pool, lawn/landscape, pest, cleaning, HVAC, plant service — 1–10 workers, owner still on the route. NOT for companies with dispatchers (don't invite Jobber comparisons).
- Value props mapped to the demo taps they just did (log → ask → prove), then pricing teaser + 30-day trial CTA.
- Keep the existing dark charcoal + lime rebrand (Jul 18) — this is a content/structure rebuild, not a re-skin. Match UI mandate where they conflict (mobile-first wins).

## M4 — Pricing matrix update (`frontend/pricing.html`)

Already rebranded + 3-tier matrix exists. Add the v2 features to the matrix: Q&A ("Ask FieldNotes"), route awareness, account tasks/delegation, client recaps (Team+), quick-action dashboard, owner chat commands. Feature→tier mapping must match `backend/deps.py::FEATURE_TIERS` exactly (recaps=team, routes=crew, qa/csv=team — check the file, don't guess). **Do NOT touch BETA49 copy** — that's an open Geoff decision (restrict-to-Team recommendation stands).

## M5 — Social/launch copy rewrite (`marketing/`)

Rewrite `social-posts.md` + outreach templates to lead with the gate-code moment, not feature lists. The 15-second route-day video script: phone in truck → "gate code for Riverside?" → answer appears → "never call the office again." Keep week-1 calendar structure (`marketing/launch-week1-calendar.md`) but refresh the post copy. All `fieldnotes.io` → fieldnotesapp.io discipline still applies.

## Verification before "done"

1. Full test suite green (all existing suites + new M1 suite) — `TZ=UTC` pinned per skill pitfall #34.
2. Live on Railway: try.html loads on a phone-width viewport, all 3 taps work against prod, rate limit returns friendly error, no email actually sends on demo taps (check sarah@ inbox stays quiet / recap_log has no demo-origin `sent` rows).
3. Landing + try.html pass the browser-console computed-style checks (pitfall #35).
4. Update this board + the fieldnotes skill (M-phase shipped, any new pitfalls — e.g. demo-endpoint lessons).
5. Short summary to Geoff.

## Key facts (verified Jul 22 — don't re-derive)

- Demo tenant: business_id=2 "Precision HVAC", tier=team, 12 accounts, Riverside Office Park recap-enabled → sarah@atlantahouseplant.com (Geoff-controlled ONLY).
- Gate code answer for Riverside is seeded (the Q&A demo already works for strangers via Telegram demo fallback — we're porting that magic to the web).
- QA: `backend/services/qa.py`. Ingest: `backend/services/ingest.py::persist_parsed_note`. Recaps: `backend/services/recaps.py` (draft logic — use the sync planning path, not the async notify path).
- LLM chain everywhere: Grok→DeepSeek→OpenAI→deterministic fallback (parser.py pattern).
- Prod = Railway (auto-deploys on push to main). WSL sqlite is FROZEN — never read for live data.
- `backend/main.py` calls `load_dotenv()` — test subprocesses must set stripped keys to `""`, not remove them (skill pitfalls #38/#40).
