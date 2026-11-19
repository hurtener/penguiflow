# Roadmap Continuation Prompt

Use this document to continue PenguiFlow roadmap work in a fresh session or repository. It captures all context, conventions, and output format requirements so you can pick up exactly where we left off.

---

## Context

The file `docs/ROADMAP.md` exists in the PenguiFlow repository. It is a living document that tracks:

1. **Roadmap table** — all features with monthly dots (●) showing active development, and a Done column (✅ / 🔲)
2. **Jira tickets** — broken down into subtasks with hours estimates and specific Mon-Fri dates (USA holidays excluded)
3. **Quality & Evaluation Pipeline** — a second section covering QE-001 through QE-014

The document was last updated May 2026. April and May actuals are complete (<160h/month). June is the current planning month (~160h budget).

---

## What Has Been Done

### Table structure
The roadmap uses a table with columns:
```
Theme | Description | Impact if not accomplished | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Ongoing | Done
```

37 items in the main roadmap (PF-001 to PF-027 in the Jira section) + 14 items in the Q&E pipeline (QE-001 to QE-014).

### Main roadmap — Done items (✅)
- Migration to dynamic agents (Jan–Feb)
- Industry-standard tool integrations MCP+UTCP (Jan–Feb)
- Track agent reasoning step-by-step (Jan–Feb)
- Run tools in parallel (Jan–Feb)
- Auto-recover from errors (Jan–Feb)
- Conversation continuity across agents (Jan–Mar)
- Unified State Store (Feb–Mar)
- Control memory costs per agent (Feb–Apr)
- Skills and documentation (Jan–Mar)
- See agent thinking in real-time (Feb–Mar)
- Background tasks (Feb–May)
- Concurrent execution control (Apr–May)
- Task groups (Feb–May)
- Pause and resume agent work (Apr–Jun)
- Human-in-the-loop approval for sensitive actions (Feb–Jun)
- Scheduled reports (Mar–Jun)
- Tasks complete even if system restarts (Mar–May)
- A2A protocol full spec (Feb–Jun)
- Remote Agent Discovery (Mar–Jun)
- Cross-agent context sharing (Apr–Jul)
- A2A observability (Apr–Jul)
- Multi-agent workflow skills (Apr–Jun)
- Parallel agent execution (Apr–May)
- HITL for multi-agent decisions (Apr–Jun)
- Routing Agent RC1 (Apr–Jun, shipped to RC1 in separate repo)
- Judge agent reflection loop (Apr–Jun)
- Automatic dataset generation for prompt optimization (Jan–Apr)

### Main roadmap — Not done (🔲)
- Proactive report back (May–Jun, in progress)
- User notification system (Jun–Jul)
- Rate limiting & runaway prevention (Apr–Jun, in progress)
- Agent registry (Apr–Jun, in progress)
- All voice items (LiveKit, Integration, STT, TTS, Interrupt, Confidence, Mode Switch, Visual) — Jun onward
- Agent registry (Apr–May, partial work done)

### Quality & Evaluation — Done (✅)
- QE-001: Judge agent (reflection loop) — Apr–Jun
- QE-009: Automatic dataset generation for prompt optimization — Jan–Apr

### Quality & Evaluation — Not done (🔲)
- QE-002 through QE-008, QE-010 through QE-014 (various dates from Jun through Dec)

---

## Jira Ticket Conventions

### Numbering
- PF-001 to PF-015: Done (retrospective, Apr–May work)
- PF-016 to PF-027: Voice + remaining multi-agent (Jun onward, some in progress)
- PF-028 to PF-030: Q&E pipeline for June
- Future tickets should continue from PF-031

### Ticket Format
```markdown
#### PF-NNN: Ticket Title
**Active:** <months from roadmap dots> | **Estimate:** <hours> | **Status:** ✅ Done | 🔲 Not started | 🔲 In progress
**Dependencies:** <optional>

| Subtask | Hours | Date |
|---|---|---|
| <subtask description> | 8h | <Month Day–Day (DayOfWeek–DayOfWeek)> |
```

### Subtask Rules
1. Each subtask is 8h or 12h (half-day to 1.5 days)
2. Dates are Mon–Fri only, specific (e.g., "Jun 8–9 (Mon–Tue)")
3. USA holidays excluded: Memorial Day (May 25), Juneteenth (Jun 19), Independence Day (Jul 3 observed), Labor Day (Sep 7), etc.
4. Subtasks for a ticket must fall within the ticket's Active month range
5. If a ticket spans multiple months (e.g., Active: Apr–Jun), spread subtasks across those months matching when the work actually happened

### Date Assignment Rules
1. **If a feature has dots in months AND an X in Done** → the ticket is retrospective. Subtask dates go in the months where the dots are. Do NOT cram all work into one month if dots span multiple months.
2. **If a feature has dots in months and NO X in Done** → the ticket is in progress/planned. Subtask dates should cover the full dot range. Partial work already done in earlier months should be reflected in early subtasks, remaining work in later months.
3. **If a feature has NO dots in a month** → do NOT assign work to that month.
4. **If a feature was started in Apr–May but is NOT done** → the ticket must show early subtasks in Apr/May (partial work already done) AND later subtasks in Jun (remaining work). Do NOT create a ticket that starts fresh in Jun.

### Hours Budget
- April 2026: < 160h actual (single person)
- May 2026: < 160h actual (single person)
- June 2026: ~ 160h projected (single person)
- July 2026: ~ 160h projected
- Each subsequent month: ~ 160h

---

## Quality & Evaluation Item Format (QE-)

Each QE item in the breakdown section follows this structure:

```markdown
#### QE-NNN: Title
| Field | Value |
|---|---|
| **Domain** | 🐧 PenguiFlow | 🏗️ Platform |
| **Status** | ✅ Done | 🔲 Not started |
| **Active** | <months> |
| **Description** | <1-2 sentence description of what it does> |
| **Impact if not done** | <what the user/company loses without this feature> |
| **Sample tools** | <concrete API surface, code examples, integration points> |
| **Other tasks** | <related PF or QE tickets this depends on or feeds into> |

**Jira:** PF-NNN (Ticket Title) — <hours>h — <dates> (if active in Jun or later)
```

Domain rules:
- 🐧 **PenguiFlow** = library-level feature (lives in `penguiflow/` package, ships as part of the library)
- 🏗️ **Platform** = external/infrastructure feature (separate repo, UI, MCP server, workflow tooling)

---

## Updated Information from User

During the conversation, the user made these corrections that MUST be reflected:

1. **A2A was fully built and shipped** — not partial. Mark all A2A items as ✅ Done.
2. **Routing Agent exists in another repo** — using A2A end-to-end with content sharing and task subscription. Mark as ✅ Done (RC1 shipped).
3. **A2A observability is done** — end-to-end task visibility is working.
4. **Remote Agent Discovery** — "Shipped but we still have to register all available agents to the router agent" — mark as ✅ Done with a note about remaining registration work.
5. **Concurrent execution control** — was built and marked with X. Mark as ✅ Done.
6. **Multi-agent workflow skills** — built and marked with X. Mark as ✅ Done.
7. **HITL for multi-agent decisions** — built and marked with X. Mark as ✅ Done.
8. **Scheduled reports** — built and marked with X. Mark as ✅ Done.
9. **Routing Agent description** — "Shipped up-to RC1. Without it: users must know which agent to call — defeats 'just ask.' user experience and isolates knowledge."

---

## Expected Output Format for Continuation

When continuing this work, the expected artifacts are:

### 1. Updated Roadmap Table
Add new rows or modify existing ones following the exact table format. Each row has:
- Theme (the feature name)
- Description (business value statement)
- Impact if not accomplished (what's lost)
- 12 month columns (● or empty)
- Ongoing column
- Done column (✅ or 🔲)

### 2. Jira Tickets
For each item active in the current planning window:
- One PF-NNN ticket per roadmap row
- Subtasks with 8h/12h estimates
- Specific Mon-Fri dates (no generic month ranges)
- Hours that total < 160h for the month

### 3. Q&E Breakdown
For QE items, the structured table with Domain, Status, Active, Description, Impact, Sample tools, Other tasks, and optional Jira reference.

### 4. Capacity Overview
ASCII tree showing which tickets/subtasks run in which weeks, with holidays annotated.

### 5. Hours Summary Table
Per-month: working days, hours, status.

---

## Key Files

| File | Purpose |
|---|---|
| `docs/ROADMAP.md` | Main roadmap document (single source of truth) |
| `docs/ROADMAP_WORK_CONTINUATION.md` | This file — session handoff prompt |
| `docs/RFC/ToDo/RFC_IDEAS_BACKLOG_2026_01.md` | Ideas backlog (LLM routing, caching, etc.) |
| `docs/RFC_SCHEDULED_TASKS.md` | Draft RFC for scheduled tasks |
| `docs/proposals/RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md` | Draft RFC for eval datasets |
| `docs/proposals/RFC_A2A_ROUTER_AND_CONVERSATION_CONTINUITY.md` | Draft RFC for A2A router |
| `docs/RFC/ToDo/RFC_REACTPLANNER_VISION_INPUT.md` | Draft RFC for vision input |
| `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md` | Draft RFC for skill learning |
| `docs/RFC/ToDo/RFC_STATESTORE_STANDARD_FOLLOWUPS.md` | StateStore production hardening |
| `docs/RFC/ToDo/RFC_SKILLS_LEARNING_V213.md` | Skills learning RFC |
| `CHANGELOG.md` | Unreleased changes tracker |
| `AGENTS.md` | Development guide and principles |

---

## Next Likely Work

### Priority 1: June Capacity Planning
June has ~160h budget across these active tickets:
- PF-016: Proactive Report Back (24h remaining)
- PF-017: User Notification System (24h)
- PF-018: Rate Limiting & Runaway Prevention (24h remaining)
- PF-019: Agent Registry (16h remaining)
- PF-020: LiveKit Infrastructure (24h)
- PF-021: Voice Integration (24h)
- PF-022: STT Integration (16h)
- PF-023: TTS Pipeline (16h)
- PF-028: Define Scoring Criteria (16h)
- PF-029: Store Evaluation History (24h)
- PF-030: Prompt Optimization Pipeline (32h)

Total: ~232h — **over budget**. Need to prioritize or defer ~72h worth of work to July.

### Priority 2: July+ Ticket Creation
Create tickets for QE-004 through QE-008 and QE-011 through QE-014 that start in Jul onward.

### Priority 3: Verify Date Accuracy
Every ticket's subtask dates must exactly match the roadmap dots. If a roadmap row shows ● in Apr, May, Jun and X in Done, the ticket must have subtasks in Apr, May, AND Jun — not all crammed into one month.

---

## Common Mistakes to Avoid

- ❌ Creating a ticket for a done item with future-only dates (must match actual active period)
- ❌ Creating a ticket for an in-progress item that ignores earlier partial work (must show early months' work as already done)
- ❌ Assigning work to months where the roadmap has no ● dot
- ❌ Using generic month ranges like "April" instead of specific dates like "Apr 8–9 (Wed–Thu)"
- ❌ Forgetting USA holidays (May 25 Memorial Day, Jun 19 Juneteenth, Jul 3 Independence Day observed, Sep 7 Labor Day)
- ❌ Going over 160h/month budget without a note about prioritization/deferral
- ❌ Mixing PenguiFlow and Platform items without the Domain marker (🐧 vs 🏗️)
