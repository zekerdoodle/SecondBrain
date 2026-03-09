---
name: sync
description: Chain-based intelligent sync. Each phase is a separate agent with ONE job. Cleanup is structural, not aspirational.
updated: 2026-02-24
---

# Sync ("The Pulse") — Chain-Based

**Trigger:** `/sync` (morning) or `/sync-evening` (evening)
**Mechanism:** `invoke_agent_chain` — one agent per phase, one job per agent.

**Why chains:** The old monolithic sync asked one agent to do 5 things. By step 4, cleanup got dropped every time. Now each phase is a separate agent whose ONLY job is that phase. You can't "forget" cleanup when cleanup is your entire purpose.

---

## Morning Sync (`/sync`)

### Step 1: Dispatch the chain

Call `invoke_agent_chain` with these three steps, `summarize: false`, `on_failure: "alert_and_stop"`:

#### Chain Step 1 → `jack`: Ingest & Clean

```
Sync Phase 1: Ingest & Clean. You have ONE job.

1. LIST all .md files in ~/second_brain/00_Inbox/agent_outputs/ (top-level only, not subdirectories)
   For each file: read it, write a 1-2 line summary.
   Save the manifest (filename + summary per file) to /tmp/sync_manifest.md

2. ARCHIVE all those files to ~/second_brain/.99_Archive/Agent_Outputs/YYYY/MM/ (current year/month, create dir if needed)

3. READ ~/second_brain/00_Inbox/scratchpad.md
   If it has content beyond the default template, extract it and append to /tmp/sync_manifest.md under "## Scratchpad Contents"
   Reset scratchpad to:
   # Scratchpad

   Quick capture zone. Dump anything here — tasks, ideas, rants, whatever.
   Claude processes this during sync.

   ---

4. CHECK for any other files in ~/second_brain/00_Inbox/ besides scratchpad.md (excluding memory-v2-prompts/ subdirectory)
   If found: archive to .99_Archive/Processed_Inbox/YYYY/MM/ and note them in manifest

5. VERIFY by running: find ~/second_brain/00_Inbox/ -type f -not -name "scratchpad.md" -not -path "*/memory-v2-prompts/*"
   If ANYTHING appears, archive it NOW. Do not finish until this returns empty.

Output the manifest as your final response.
```

#### Chain Step 2 → `life_admin`: Route & Schedule

```
Sync Phase 2: Route & Schedule. You have ONE job.

Read /tmp/sync_manifest.md — this is everything found in the inbox during Phase 1.

CRITICAL ROUTING RULE — the user's calendar and tasks are for USER'S actions only:
- Items that require USER to act (applications, meetings, decisions, personal tasks) → Google Tasks / Google Calendar
- Items that are agent or Character responsibilities (code fixes, memory cleanup, system maintenance, scheduled agent work) → Write to 00_Inbox/sync_agent_tasks.md instead. Do NOT create Google Tasks or Calendar events for these.

When in doubt about ownership: if a human doesn't need to do it, it's an agent task.

**Examples — NEVER goes to Google Tasks/Calendar:**
- Git backup failures, server errors, code bugs → nightly_queue.md
- CUA rewrites, agent prompt updates, skill edits → sync_agent_tasks.md
- Memory cleanup, storage audits, file structure issues → sync_agent_tasks.md
- Moltbook API failures, scheduler issues → sync_agent_tasks.md
- Any task where the action is taken by an agent, not the user

**Examples — DOES go to Google Tasks:**
- Job applications to review or submit
- Personal appointments, meetings, deadlines
- Purchases, errands, financial tasks
- Decisions only the user can make (not just "approve agent work")

1. EXTRACT the user-actionable items from the manifest → Create Google Tasks
2. EXTRACT the user time-bound items → Create Google Calendar events
3. EXTRACT agent/Character items → Write to 00_Inbox/sync_agent_tasks.md (Character will schedule these herself)
4. JOURNAL entries → Append to ~/second_brain/20_Areas/journal-and-review/daily-notes/YYYY-MM-DD.md
5. CHECK today's Google Calendar — list all events
6. CHECK today's Google Tasks — list incomplete tasks

If the manifest file doesn't exist or is empty, skip routing and just do steps 5-6.

Output: What you routed (and WHERE — Google vs agent tasks file) + today's full calendar + pending tasks.
```

#### Chain Step 3 → `jack`: Project Pulse

```
Sync Phase 3: Project Pulse. You have ONE job.

Scan all _status.md files in ~/second_brain/10_Active_Projects/*/

For each project:
1. Read its _status.md
2. Check "Last Activity" date (or last modified date of any file in the project folder)
   - >7 days since activity = flag as STALE
   - >14 days = flag as CRITICAL
3. Check for "Human-Only Items" or "Blocked" sections — items >3 days old = flag as USER-BLOCKED

Regenerate ~/second_brain/10_Active_Projects/_index.md with current status of all projects.

Output: One-line status per project, flagging any STALE/CRITICAL/BLOCKED items.
```

### Step 2: Compose the briefing

After the chain completes, synthesize results into a **concise morning briefing** for the user:

- **📥 Inbox:** What was found and processed (from chain step 1)
- **✅ Actions:** Tasks/events created FOR USER (from chain step 2)
- **📅 Today:** Calendar + pending tasks (from chain step 2)
- **📊 Projects:** Health check, flag anything stale or blocked (from chain step 3)
- **❓ Needs you:** Anything ambiguous that requires the user's input

Also: check 00_Inbox/sync_agent_tasks.md — if it has items, include them in the briefing under a "🤖 Suggested Agent Dispatches" section. Do NOT auto-schedule them. List each item with a one-line description and proposed agent. Character or the user will approve and dispatch.

Keep it scannable. the user reads this with coffee. No essays.

---

## Evening Sync (`/sync-evening`)

### Step 1: Dispatch a lighter chain

Call `invoke_agent_chain` with TWO steps:

#### Chain Step 1 → `jack`: Clean

```
Evening cleanup. ONE job.

1. Check ~/second_brain/00_Inbox/agent_outputs/ for any .md files (top-level only)
2. For each, read and write a 1-line summary
3. Archive all to ~/second_brain/.99_Archive/Agent_Outputs/YYYY/MM/
4. Check scratchpad for new content beyond template
5. VERIFY: find ~/second_brain/00_Inbox/ -type f -not -name "scratchpad.md" -not -path "*/memory-v2-prompts/*"
   If anything found, archive it. Don't finish until clean.

Output what you found (or "inbox was clean").
```

#### Chain Step 2 → `life_admin`: Day Review

```
Evening review. ONE job.

1. Check Google Calendar — what happened today? What's tomorrow?
2. Check Google Tasks — what got completed today? What's still pending?
3. Preview tomorrow's agenda

Output: Today's recap + tomorrow's preview.
```

### Step 2: Compose the evening wrap-up

Light touch:
- What came in since morning (from step 1)
- Day recap + tomorrow preview (from step 2)
- Anything to flag for tomorrow

---

## Safety Nets

- Dedicated cleanup agents run at 08:30 and 21:30 as backup in case chain Phase 1 fails.
- These are redundant by design. If the chain works, they find nothing and exit instantly.

## Architecture Notes

- **One job per agent** = can't skip steps. Each agent's ONLY job is its phase.
- **Manifest pattern:** Phase 1 writes to /tmp/sync_manifest.md. Phase 2 reads from there. Disk is the handoff, not context.
- **Verification is built-in:** Phase 1 MUST run `find` and confirm empty before finishing.
- **Chain order matters:** Clean → Route → Pulse. Each phase must complete before the next starts.
