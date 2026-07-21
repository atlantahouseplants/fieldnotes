# P7 — Account Tasks (the Delegation Loop)

**Status: 🔲 NOT STARTED** · Depends on: P6a (account page on dashboard) · Origin: Geoff's pool-cover scenario, 2026-07-21

## The scenario this solves

Client calls the pool company owner: "my pool cover needs repair." Today that becomes a sticky note, a text the owner forgets to send, or a rep who walks in blind. **FieldNotes becomes the liaison:**

```
Client request → owner tells FieldNotes → task attached to account
→ rep sees it BEFORE/DURING the visit (with supplies needed)
→ rep completes it → owner auto-confirmed → record lives on the account
```

Works for evergreen visits AND special circumstances. The rep never walks in unaware.

## Data model — `account_tasks` (NEW table)

| Column | Notes |
|---|---|
| id, business_id, account_id | tenant-scoped like everything |
| title, details | "Repair pool cover" / "client said tear on deep-end corner" |
| status | open / done / cancelled |
| assigned_worker_id | nullable — unassigned = whole crew sees it |
| supplies_needed | text — "cover patch kit, 12ft strap" |
| due_date | nullable; open tasks surface regardless |
| source | chat_owner / chat_rep / dashboard |
| created_by_worker_id | null = owner created |
| created_at, closed_at, closed_by_worker_id | |

## Flows

### 1. Create (3 paths, all equal)
- **Owner chat:** "Task for Smith: repair pool cover, needs patch kit, Mike, Thursday" → intent router (P1) new `create_task` intent → parse → create → confirm back in plain words ("Got it — Mike will see 'repair pool cover' when he's at Smith Thursday.")
- **Rep chat:** "Task for Smith: filter housing cracked, needs replacement" → same flow (rep-flagged work)
- **Dashboard:** account page → "Add task" form (P6a builds the page)

### 2. Surface (the rep must NEVER miss it)
- **Morning route push (P4):** each stop annotated — "Smith — ⚠️ 1 open task: repair pool cover (patch kit)"
- **At log time:** rep logs/asks about an account with open tasks → bot reply appends: "⚠️ Open task here: repair pool cover — supplies: patch kit"
- **Q&A:** "anything special at Smith today?" → tasks included in retrieval
- **Dashboard account page:** open tasks section on top

### 3. Complete
- **Explicit:** "done with the cover at Smith" → parser matches open task → close it
- **Implicit:** parser detects completion language in a normal log ("replaced the filter") → proposes close: "Mark 'replace filter' done? ✓/✗" — confirm before closing, never auto-close silently
- **Owner notified on close:** "✅ Mike completed: repair pool cover at Smith." → daily summary AND instant chat ping (owner chat only, not email spam)

### 4. Supplies rollup
- Open tasks' `supplies_needed` + parser-flagged supplies roll into the nightly summary's supplies section and the morning push ("truck list: patch kit, DE filter ×2")

## Tasks (implementable units)

1. `account_tasks` model + migration (follow models.py conventions; explicit is_active-style defaults)
2. `services/tasks.py`: create/list/close, tenant-scoped, account-match reuse from qa.py
3. Intent router: `create_task` / `close_task` intents + plain-word confirmations
4. Log-time surfacing hook (append open tasks to bot replies for that account)
5. Morning push annotation (P4 integration point)
6. Dashboard: account page tasks section + add/close buttons
7. Owner completion ping
8. Completion-detection confirm flow (✓/✗ reply handling)
9. Tests: full loop (owner creates → rep sees at log → rep closes → owner pinged); tenant isolation; unassigned task visible to all reps
10. AHP dogfood: Geoff runs his real route with it (special requests = his swaps/replacements workflow)

## Acceptance criteria

- [ ] The pool-cover scenario works end-to-end in under 30 seconds of owner effort
- [ ] Rep cannot log an account without seeing its open tasks
- [ ] No silent auto-closes — completion always confirmed
- [ ] Supplies from tasks appear in morning push + nightly summary
- [ ] Tenant isolation test passes
- [ ] Works identically via chat and dashboard

## Pitfalls

- Task matching on close: match by account first, then title similarity — if 2+ open tasks could match, ASK which one. Never guess-close.
- Owner chat vs rep chat: identify by Worker.role / owner flag — owner-only pings must not go to reps.
- Keep tasks OUT of the LLM context window bulk — retrieval fetches open tasks for the matched account only (cheap, precise).
- This table will be the highest-write target after service_logs — index (business_id, account_id, status) from day one.
