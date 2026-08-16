# P10 — Channel Strategy: SMS-Default Flip (DECISION DOC)

**Status:** RATIFIED by Geoff 2026-08-14. Phase 0 (vendor unblock) in progress.
**Origin:** Aug 14 strategy session — Geoff questioned Telegram-first from the end user's
shoes. Multi-model review (Hermes/kimi + Claude + Grok 4.6, briefed independently) returned
a unanimous 3-0 verdict. GPT unavailable (OpenAI credit-dead).

## Decision

1. **SMS becomes the default worker channel** the moment the AgentPhone 10DLC send gate
   opens. Telegram demotes to a *supported* channel (founder, dogfood, demo deep-links,
   cost-sensitive crews) — never the onboarding path.
2. **No native app. Ever, for techs.** "Add to Home Screen" on the key-locked dashboard is
   the entire answer to "where's the app." Revisit owner auth (magic-link email) only when a
   PAYING tenant asks.
3. **WhatsApp is the #2 channel candidate** — spec after 2-3 real crews are live on SMS,
   build only if Spanish-first crews show up in the beta mix. Same channel seam as P3.
4. **Two-user law stays sacred:** tech = chat only; owner = dashboard + nightly email +
   optional chat. If a tech ever needs the dashboard, the design failed.

## Why (the evidence)

- Telegram regular usage in the US is ~9% and skews knowledge-worker/enthusiast. The ICP's
  labor pool (Spanish-first landscaping/cleaning/pool crews in metro Atlanta) lives on
  SMS + WhatsApp. Requiring Telegram = an app install + identity creation + bot-start
  ritual — the exact "no apps, no forms, no training" promise broken at the front door.
- The funnel already admitted this: M2 (try.html) replaced "download Telegram and text our
  bot" because that friction killed the demo. The storefront was telling on the strategy.
- SMS cost is a COGS line, not a barrier: 1-2¢/segment, ~$10-25/mo/tech worst case, against
  $79-149/mo plans. Absorb or cap (Crew-gated + per-tenant monthly budget — already the
  unit-econ plan).
- Category graveyard confirms "no app": Jobber/Housecall Pro have polished tech apps AND
  still lose to crew resistance. FieldNotes' wedge is refusing that fight.
- The channel seam (P3, pitfall #47) makes this a **defaults + copy change, not a rebuild.**

## The honest caveat (from the same session)

The channel question decides whether onboarding scales at tenant #20. We're at tenant #1.
The bigger gap is zero paying strangers + unfinished dogfood. The flip is cheap (days, not
weeks) so do it now — but it must NOT become another build cycle that delays selling.
Founder-led sales (FB groups 3:1, 10 warm owners, try.html demo) runs in parallel the whole
time, and beta crews get white-gloved onto whatever channel works until SMS is live.

## Execution plan

**Phase 0 — vendor unblock (Geoff, ~1hr, WEEK OF AUG 17):**
- [ ] Nudge AgentPhone support (dashboard login sarah@atlantahouseplant.com + their calendar
      link). Draft message below.
- [ ] Top up AgentPhone balance ($25 filing consumed the $25 credit) — after they confirm.
- [ ] Watchdog cron 791a9e839d95 (every 2h) pings when the send gate opens — no build needed.

### AgentPhone support nudge — DRAFT (Geoff sends)

> Subject: 10DLC approved 3 weeks ago — outbound still 403ing
>
> Hi — my 10DLC registration for Atlanta Houseplants LLC shows APPROVED since July 23
> (GET /v1/register/status confirms), but POST /v1/messages still returns 403 "Complete
> 10DLC registration first." It's been ~3 weeks, so this looks like an entitlement sync
> issue on your side rather than a registration problem.
>
> Two asks:
> 1. Can you check why the send gate hasn't opened for my account and force the entitlement
>    through?
> 2. Confirm my balance status — the $25 filing consumed my initial $25 credit and I want to
>    top up as soon as sends actually work.
>
> Happy to jump on a quick call if easier — send me your calendar link.
>
> Thanks, Geoff

**Phase 1 — when the gate opens (2-3 build sessions):**
- [ ] Real-phone E2E FIRST: invite Geoff's phone on demo biz 2 → YES → log → ask. Prove the
      pipe before touching any copy.
- [ ] Flip onboarding default: worker invite = SMS first; Telegram link becomes "also works
      on Telegram." Dashboard invite panel already exists — reorder, don't rewrite.
- [ ] COGS guardrails in the same pass: per-tenant monthly SMS budget cap (soft warn → hard
      stop), short confirmations, MORE-stash for long replies, Crew-tier gating per
      unit-econ note. Watch the AHP-branded 10DLC campaign vs "FieldNotes" text content —
      tolerable for beta, dedicated number/campaign before real volume.
- [ ] Copy pass: strip "Telegram" from landing/pricing first screen. Sell the habit:
      "Your crew texts this number between stops. You get the log, the supply list, and a
      recap for the customer."

**Phase 2 — parallel, the actual priority (ongoing, 20 min/day):**
- [ ] Founder-led sales per GTM ranking: FB owner groups → warm 10 → Reddit pain-mining.
      try.html is the closer (needs no Telegram, no SMS — works today).
- [ ] Finish AHP dogfood (plans/ahp-dogfood/). Proof in the pudding.
- [ ] White-glove each beta crew onto whatever channel they already use; hand-install
      Telegram if SMS isn't ready. Manual is fine at 1-2 signups/week.

**Phase 3 — after 2-3 real crews on SMS (4-8 weeks out):**
- [ ] Measure the only adoption metric that matters: **% of invited techs who send a real
      note in week 1, by channel.** Data settles every remaining Telegram question.
- [ ] Spec WhatsApp through the channel seam (subclass Channel, route envelope — same
      pattern as P3). Build only if the beta mix justifies it.
- [ ] Revisit owner magic-link auth only on a paying complaint.

## Explicitly NOT doing

- No native app, no App Store listing.
- No WhatsApp build before SMS is proven with real crews (one vendor fight at a time).
- No dashboard chrome, no owner auth work, no per-endpoint polish without a paying complaint.
- No Telegram removal — it stays for demo deep-links, dogfood, and cost-free crews.

## One-sentence version

Unblock the vendor, flip the default, then stop building and go get the 10 betas — the
channel strategy means nothing until strangers are texting notes.
