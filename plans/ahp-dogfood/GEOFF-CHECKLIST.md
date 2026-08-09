# GEOFF'S CHECKLIST — Sign up AHP as FieldNotes Client #1 (Sat Aug 8, 5:00 PM)

Everything you need, nothing else. ~10 minutes. Do this exactly like a stranger would —
no shortcuts. If anything feels broken or confusing, DON'T push through it — tell Hermes,
that's a finding (FINDINGS.md).

## Before you start
- Phone or desktop, either works
- Have your account list ready (just the names — Hermes enriches schedules/gate codes/contacts
  afterward via the CSV import tool)
- Decided: **Team plan + coupon BETA49** (= $49/mo locked for life). We deliberately did NOT
  buy Crew — hitting the route-push gate on Team is finding #1.

## The steps

**1. Checkout (billing)**
- Go to https://fieldnotesapp.io/app/pricing.html
- Pick **Team** ($79 → BETA49 makes it $49)
- Coupon code: **BETA49**
- Email at checkout: **sarah@atlantahouseplant.com**  ← must match step 2 exactly
- ⚠️ Trial is supposed to be 30 days, NO card required. If it demands a card anyway,
  that's finding #2 — screenshot it, tell Hermes, then you can still proceed.

**2. Signup (start page)**
- After checkout you'll land on / be sent to the start page:
  https://fieldnotesapp.io/app/start.html
- Business name: **Atlanta Houseplants**
- Owner name: **Geoff Wall**
- Owner email: **sarah@atlantahouseplant.com** (same as checkout — this is what links
  your payment to your business)
- Paste account names, one per line (names only)
- Submit → success screen shows your dashboard URL + two Telegram links

**3. Telegram linking (1 min)**
- Tap the **owner link** → expect: "👑 You're linked as the owner of Atlanta Houseplants"
- Tap the **worker invite link** → expect: "🎉 You're connected to Atlanta Houseplants"
- (The worker tap moves your registration from the old seeded tenant to the new one — by design.)

**4. Watch for the founder alarm**
- Your own Telegram should get: "🌱 New FieldNotes signup! Atlanta Houseplants — Geoff Wall
  (team trial) · N accounts · biz #N"
- First time it's ever announced a real customer.

**5. Tell Hermes "done"**
- Hermes then: verifies the DB (Stripe linked, tier=team, trialing), builds the full CSV
  from the vault masters, imports through import.html, retires the old seeded biz 3,
  sets up the recaps pilot on one account.

## After tonight
- Monday route day: log your stops in the FieldNotes bot instead of Hermes chat
- The dogfood rule: if FieldNotes can do it, do it in FieldNotes. Every time you reach
  for Hermes instead, that's a finding.

## Reference
- Full journey doc + status board: fieldnotes repo → plans/ahp-dogfood/README.md
- Bug/UX log: plans/ahp-dogfood/FINDINGS.md
