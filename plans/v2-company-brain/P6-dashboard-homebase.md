# P6 — Dashboard Home Base (Owner UX)

**Status: 🔲 NOT STARTED** · Origin: Geoff direction, 2026-07-21 (route day voice notes)

## The two-user model (never confuse them)

| | OWNER (the customer, pays) | REP (the user, free) |
|---|---|---|
| Lives in | **Dashboard** (home base) + chat | **Chat only** (Telegram/SMS) |
| Wants | Oversight without phone interruptions, proof of service, nothing falls through cracks | Do the job, not paperwork |
| Tech tolerance | Low-medium | **Zero** — will abandon anything with friction |
| Device | Phone, between stops | Phone, gloved/wet hands |

**Design law: these are blue-collar businesses. The product bridges AI/automation to people who don't do tech. Every flow must work for someone who has never used "software."** If a rep needs training, we failed. If an owner needs a manual, we failed.

## Simplicity rules (apply to EVERYTHING below)

**Geoff's mandate (2026-07-21): clean, simple, intuitive, user-friendly UI is CRITICAL to this product's success. It is a feature, not polish.**

1. One screen, cards, big touch targets — no menus-within-menus
2. Every action ≤ 2 taps from the dashboard
3. Plain language: "Add a customer", not "Create account entity"
4. **Everything the dashboard does, the chat can also do** — owner's choice (owner is often also in the field)
5. Never ask a question that has a reasonable default

## UI standard (binding for all frontend work)

- **Mobile-first.** Owners and reps are on phones, outdoors, between stops. Design for a 390px screen first.
- **Touch targets ≥ 44px**, high contrast, readable in sunlight (no gray-on-gray).
- **Brand system (already in use — keep consistent):** primary green `#1b5e20`, white rounded cards (`border-radius:14px`, soft shadow), system font stack (`-apple-system, sans-serif`), page bg `#f5f7f5`. Match `frontend/import.html` styling exactly.
- **Plain HTML/CSS/JS only** — no frameworks, no build step. Must load fast on a cracked-phone 4G connection.
- **Forms:** minimum possible fields, smart defaults, big inputs, inline validation in plain words ("We need a name for this customer").
- **Feedback:** every action gets an immediate plain-words confirmation ("Customer added ✓") — never a silent success or a raw error.
- **No logins anywhere** — key-locked URLs. No settings pages, filters, or search bars unless the jobs table demands them.
- **Bilingual-ready:** keep UI strings short and simple so ES translation later is cheap; reps may be Spanish-first.

## Owner jobs-to-be-done → what we build

| Job (owner says) | Solution | Status |
|---|---|---|
| "What happened today?" | Today's feed on dashboard | ✅ exists |
| "What's outstanding?" | Action queue view | ✅ exists |
| "I signed a new client" | **Add account** — big button, 4-field form (name, address, gate/access, schedule). ALSO chat: "New account: Smith, 121 Main St, gate 4412, Tue/Fri" → bot parses + confirms | ❌ P6a + P6b |
| "Client called me about something" | **Quick note box** on dashboard (pick account → type note → becomes a ServiceLog attributed to owner). ALSO chat: "Note for Smith: ..." | ❌ P6a + P6b |
| "Need to re-import / update my list" | **Import button on dashboard** → import.html (closes the known gap — page exists, just not linked) | ❌ P6a (1 hr) |
| "Hired a new guy" | **Invite worker button** on dashboard (reuses existing invite-token flow; SMS invite after P3) | ❌ P6a |
| "Fired a guy" | Deactivate worker (one tap, confirm) | ❌ P6c |
| "Are my guys actually using it?" | Usage strip: notes per rep this week, last-active | ❌ P6c |
| "Manage my plan" | Stripe customer portal link on dashboard | ❌ P6c |

## Build slices

- **P6a (4 PM session):** dashboard quick-action bar — Import button, Add Account form, Quick Note box, Invite Worker button. All POST to new key-locked endpoints (`/dashboard/add-account`, `/dashboard/add-note` reusing the existing pipeline so owner notes parse like rep notes). Plain HTML like the rest of the frontend, no framework.
- **P6b:** chat owner commands — extend the P1 intent router: `new_account` / `add_note` intents for messages from the owner's chat_id. Bot confirms what it did in plain words.
- **P6c:** worker deactivate, usage strip, Stripe portal link.

## Acceptance criteria

- [ ] Owner can add an account, log a note, invite a rep, and reach import — all from the dashboard, phone, ≤2 taps each
- [ ] Same four things possible by just texting the bot
- [ ] A note added by the owner appears in today's feed + nightly summary like any rep note
- [ ] Stranger test: someone who has never seen the product completes "add a customer" unaided

## Pitfalls

- Dashboard auth stays the key-locked URL model (no logins — that's the simplicity moat). Every new endpoint takes business_id+key, middleware-enforced.
- Owner-added notes go THROUGH the AI parser (not raw insert) so issues/follow-ups still get extracted — one pipeline, no special cases.
- Don't build settings pages, filters, or search bars yet. If it isn't in the jobs table above, it's clutter.
