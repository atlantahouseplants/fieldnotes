---
tags: [product, saas, mvp, active]
created: 2026-06-27
status: draft
---

# FieldNotes — Product Spec & MVP Plan

## Elevator Pitch

**FieldNotes turns a service worker's voice notes between stops into a fully updated CRM, daily summary, and action queue — with zero apps, zero forms, and zero training.**

The worker talks. The system does the rest.

---

## The Problem

### For the business owner:
- No real-time visibility into what happened in the field today
- Workers forget to log issues, flag follow-ups, or note supply needs
- End-of-week "what did we miss?" is a guessing game
- Field service software costs $100-349/mo and workers refuse to use it

### For the field worker:
- Hates typing into apps between stops
- Paperwork is the worst part of the job
- "I'll remember to tell the boss later" → never happens
- Text messages to the boss get buried and lost

---

## Target Market

**Primary:** 1-10 person service businesses where the owner is still in the field or managing field workers.

| Vertical | Examples | US businesses |
|----------|----------|---------------|
| Home services | Plumbers, electricians, HVAC, garage door | 500K+ |
| Outdoor services | Landscapers, arborists, pest control, pool service | 300K+ |
| Cleaning | Residential, commercial, window, carpet | 200K+ |
| Specialty trades | Interiorscape, elevator repair, fire safety | 100K+ |
| **Total addressable** | | **~1M+ businesses** |

**Ideal first customer profile:**
- 2-10 employees with field workers
- Currently using text messages + memory, or a CRM the workers ignore
- Owner frustrated by lack of visibility
- Paying $0-50/mo for any software (underserved)
- Service-based, recurring routes or job sites

---

## Product Overview

### Core Loop

```
┌─────────────────────────────────────────────────────────┐
│                   THE FIELDNOTES LOOP                     │
├─────────────────────────────────────────────────────────┤
│                                                           │
│   WORKER                          SYSTEM                 │
│   ──────                          ──────                 │
│                                                           │
│   "Acme Office: all good,         Account identified     │
│    replaced filter,               Service logged         │
│    upstairs unit making noise"    Issue flagged          │
│        │                          Follow-up created      │
│        │   ── Telegram ──►        Supply list updated    │
│        │                          CRM note appended      │
│    Next stop...                   Queue updated          │
│                                                           │
│                    END OF DAY                             │
│                                                           │
│   OWNER ←── Email: "Today's summary, 3 flagged issues,   │
│                      1 stop missed, supply list ready"    │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Zero new apps.** Workers use the messaging app already on their phone.
2. **10 seconds between stops.** Not a form. Not a report. Just talk.
3. **Deterministic actions.** Same input → same output. No AI hallucination in the execution layer.
4. **Owner sees everything.** Daily summary, missed stops, flagged issues — all in one place.
5. **No feature creep.** Does 3 things well. Not 50 things poorly.

---

## MVP Feature Set

### ✅ IN SCOPE (v1.0 — 30-day build)

| # | Feature | Description |
|---|---------|-------------|
| 1 | **Telegram input** | Workers send voice or text notes. System processes inline. |
| 2 | **Account identification** | Matches shorthand names to defined accounts/locations. |
| 3 | **Service logging** | Timestamped log per account per visit with raw + processed notes. |
| 4 | **Issue flagging** | Extracts problems, replacements needed, customer requests. |
| 5 | **Action queue** | Auto-populates: urgent, this-week, next-visit, supply list. |
| 6 | **Daily summary email** | End-of-day digest: stops completed, issues flagged, supplies needed. |
| 7 | **Missed stop detection** | Compares today's logs vs. scheduled stops. Pings owner if gaps. |
| 8 | **Simple web dashboard** | Owner can see today's activity, pending actions, account history. |
| 9 | **Account management** | Define accounts, assign to routes/days, basic contact info. |
| 10 | **Multi-worker support** | Each worker has their own Telegram account linked. Notes tagged by worker. |

### ❌ OUT OF SCOPE (v2+)

- CRM integrations (GHL, Salesforce, HubSpot)
- Invoicing or payment processing
- Custom mobile app (Telegram/SMS is the platform)
- Client-facing portals or recap emails
- Advanced analytics or reporting
- GPS tracking or time logging
- Calendar integration or scheduling

---

## Tech Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       SYSTEM ARCHITECTURE                      │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  INPUT LAYER                                                   │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐                │
│  │ Telegram │    │   SMS    │    │ WhatsApp │  (v2)          │
│  │   Bot    │    │ (Twilio) │    │          │                │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘                │
│       │               │               │                       │
│       └───────────────┼───────────────┘                       │
│                       │                                       │
│                       ▼                                       │
│  ┌─────────────────────────────────────────────┐             │
│  │            MESSAGE INGESTION LAYER           │             │
│  │  • Receive messages from all platforms       │             │
│  │  • Route to appropriate business account     │             │
│  │  • Basic validation (account exists, etc.)   │             │
│  └─────────────────────┬───────────────────────┘             │
│                        │                                      │
│                        ▼                                      │
│  ┌─────────────────────────────────────────────┐             │
│  │              AI PROCESSING LAYER             │             │
│  │  • Parse account name + shorthand            │             │
│  │  • Extract: status, issues, supplies,        │             │
│  │    follow-ups, customer requests             │             │
│  │  • Classify severity and queue placement     │             │
│  │  • Generate structured output                │             │
│  └─────────────────────┬───────────────────────┘             │
│                        │                                      │
│                        ▼                                      │
│  ┌─────────────────────────────────────────────┐             │
│  │          DETERMINISTIC EXECUTION LAYER       │             │
│  │  • Log service (account + timestamp + notes) │             │
│  │  • Update action queues (no duplicates)      │             │
│  │  • Track replacements/supplies               │             │
│  │  • Run accountability checks                 │             │
│  │  • Generate daily summary                    │             │
│  └─────────────────────┬───────────────────────┘             │
│                        │                                      │
│                        ▼                                      │
│  ┌─────────────────────────────────────────────┐             │
│  │               OUTPUT LAYER                   │             │
│  │  • Web dashboard (owner view)                │             │
│  │  • Daily summary email                       │             │
│  │  • Missed-stop alerts                        │             │
│  │  • Export to CSV/PDF (v1.5)                  │             │
│  └─────────────────────────────────────────────┘             │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

### Technology Choices

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Backend framework** | Python + FastAPI | Geoff knows Python. Battle-tested for APIs. |
| **Database** | SQLite (MVP) → PostgreSQL (scale) | Zero setup for MVP. Supabase when ready. |
| **AI processing** | Hermes Agent or direct LLM API | Already proven with AHP workflow |
| **Task queue** | Simple async queue (Celery or background tasks) | Handle message processing without blocking |
| **Web dashboard** | React or HTMX + simple templates | Keep it simple. Owner-facing, not consumer-facing. |
| **Hosting** | Single VPS ($5-10/mo) or Vercel + Supabase free tier | Near-zero cost to launch |
| **Email** | Resend (free tier: 100 emails/day) | Simple, modern API |
| **Auth** | Email/password + magic link | Simple for business owners |
| **Messaging** | Telegram Bot API (MVP) | Free, proven, works globally |

---

## 30-Day Build Plan

### Week 1: Foundation (Jun 30 – Jul 6)

| Day | Task | Deliverable |
|-----|------|-------------|
| 1-2 | Project scaffold: FastAPI app, DB schema, basic auth | Working skeleton |
| 3-4 | Account management CRUD: create/edit accounts, assign routes | Owners can define their accounts |
| 5-6 | Telegram bot integration: receive messages, basic webhook | Messages flow into system |
| 7 | AI processing layer: parse account + extract structured data | Raw notes → structured output |

### Week 2: Core Processing (Jul 7 – Jul 13)

| Day | Task | Deliverable |
|-----|------|-------------|
| 8-9 | Service logging: timestamped entries per account | Workers' notes create log entries |
| 10-11 | Issue extraction + action queue generation | System flags issues, creates follow-ups |
| 12-13 | Deterministic execution scripts (port from AHP system) | Action queue, replacements, supply list |
| 14 | End-of-day summary generation | System generates daily digest |

### Week 3: Output & Visibility (Jul 14 – Jul 20)

| Day | Task | Deliverable |
|-----|------|-------------|
| 15-16 | Web dashboard: today's activity, account history | Owner can see what happened |
| 17-18 | Daily summary email via Resend | Digest lands in owner's inbox |
| 19 | Missed stop detection + alert | Accountability ping |
| 20 | Multi-worker support: tag notes by worker | Owner knows who did what |

### Week 4: Polish & Launch (Jul 21 – Jul 27)

| Day | Task | Deliverable |
|-----|------|-------------|
| 21-22 | UX polish: onboarding flow, setup wizard | New user can configure in <30 min |
| 23-24 | Testing: real-world scenarios, edge cases | System handles messy input |
| 25-26 | Landing page + documentation | Marketing site live |
| 27 | LAUNCH: first 3 beta customers onboarded | Real businesses using it |

---

## Pricing Strategy

### MVP Pricing (beta customers)
- **$49/mo** — up to 3 workers, unlimited accounts, daily summaries
- **30-day free trial** — no credit card required
- Beta pricing locked in for life for first 10 customers

### Post-Beta Pricing
| Tier | Price | Workers | Features |
|------|-------|---------|----------|
| Solo | $39/mo | 1 | Core features |
| Team | $79/mo | Up to 5 | + Multi-worker, priority support |
| Crew | $149/mo | Up to 15 | + Custom integrations, API access |

### Why $49-79 is the sweet spot:
- Below the psychological $100 barrier
- Above "just a text message" territory (signals value)
- Competitive: Housecall Pro starts at $49 (but is bloated)
- High margin: hosting + AI API = ~$0.50-2.00/customer/month

---

## Competitive Landscape

| Competitor | Price | FieldNotes Advantage |
|-----------|-------|---------------------|
| **Housecall Pro** | $49-349/mo | Workers hate apps. FieldNotes uses their phone's messaging app. |
| **Jobber** | $39-179/mo | Complex setup. FieldNotes: define accounts, start talking. |
| **ServiceTitan** | $200-800+/mo | Enterprise only. FieldNotes is for the 1-10 person shop. |
| **Text messages + hope** | $0 | Free, but nothing gets logged, followed up, or tracked. |
| **Google Forms** | $0 | Workers still have to type. No AI processing. |

**The real competition isn't software — it's "we just text the boss."** FieldNotes is the upgrade path from chaos to clarity without the bloat.

---

## Go-to-Market Strategy

### Phase 1: Beta (Month 1-2)
- 3-5 businesses, hand-picked by Geoff
- Free or heavily discounted
- Goal: testimonials, case studies, product refinement

### Phase 2: Referral Growth (Month 3-6)
- Each beta customer refers 1-2 others
- Target: 10-20 paying customers
- Content: Geoff's story ("How I run 17 accounts solo")

### Phase 3: Outbound (Month 6+)
- Targeted outreach to service business owners
- Facebook groups, trade associations, local business networks
- Geoff's domain expertise as the differentiator ("I use this myself")

---

## The Unfair Advantage

Geoff is not building a hypothetical SaaS. He **is** the customer. He's been running this exact workflow for months:

- He knows the pain of field worker documentation
- He knows what features actually matter vs. what sounds good
- He can demo a working system live — not a mockup
- He has 20+ years in a service business

**Most SaaS founders build what they THINK service businesses need. Geoff already KNOWS.**

---

## Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Too niche | Medium | Start with one vertical, expand. The core workflow applies to any field service. |
| AI hallucination in processing | High | Deterministic execution layer. AI only does parsing + classification. Actions are scripted. |
| Telegram dependency | Medium | Add SMS (Twilio) in v1.5, WhatsApp in v2. Multi-platform from the start. |
| Geoff's time (still running AHP) | High | Build leverages Hermes + Claude Code heavily. 30-day sprint, then maintenance mode. |
| Competition from big players adding voice | Low | Big players are bloated. Simplicity is the moat. |

---

## Next Actions (immediate)

- [ ] Name it: FieldNotes? ServiceLog? RouteVoice? (decide)
- [ ] Validate: talk to 3 service business owners this week — would they pay for this?
- [ ] Tech spike: confirm Telegram bot + FastAPI + SQLite stack works end-to-end
- [ ] Domain: register something simple and professional
- [ ] First build session: scaffold the project and get messages flowing

---

## Appendix A: The AHP System (Proof of Concept)

Geoff's existing Atlanta Houseplants workflow is the fully-functional prototype:

- **Input:** Voice/text via Telegram between route stops
- **Processing:** AI parses account, status, issues, supplies
- **Output:** Service logs, account updates, action queues, procurement dashboard, daily accountability check
- **Scripts:** 4 deterministic Python scripts handle all state changes
- **Cron:** 4:30 PM route day check, weekly verification

This document was generated from the live system described in `skill:ahp-route-capture`.

---

## Appendix B: Competitor Deep-Dive Notes

*To be populated during validation calls — what are actual businesses using now?*

