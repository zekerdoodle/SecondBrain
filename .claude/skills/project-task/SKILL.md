---
name: project-task
description: Structured workflow for project-related tasks. Enforces: read context → do work → document → schedule next. Use for any scheduled or manual task that's part of an ongoing project.
updated: 2026-01-26
---

# Project Task Workflow

**Trigger:** Scheduled prompt or manual invocation with `/project-task {project-id}: {task description}`

**Purpose:** Maintain continuity on multi-step projects by enforcing a consistent workflow. Each task reads prior context, executes, documents what happened, and schedules the next step.

---

## Step 0: Parse Input

Extract from the prompt:
- **Project ID:** The identifier after `/project-task` and before the colon (e.g., `career-pivot`)
- **Task:** Everything after the colon

If no project ID is provided, ask for clarification before proceeding.

---

## Step 1: Load Context (The Memory)

1. **Find project folder:** Look in `10_Active_Projects/` for a folder matching the project ID (fuzzy match OK, e.g., `career-pivot` matches `11_Career_Pivot/`)
2. **Read `_status.md`:** This is the project's state file. Contains:
   - Current phase
   - What's been done
   - What's next
   - Blockers or notes
3. **Read related docs:** If `_status.md` references other files (trackers, braindumps, etc.), read those too.

**If `_status.md` doesn't exist:** Create it with a basic template before proceeding.

---

## Step 2: Execute Task (The Work)

Do the task described in the prompt. This might involve:
- Research (web search, reading docs)
- Creating/editing files
- Sending communications
- Processing data
- Anything else within my capabilities

**Stay focused on the specific task.** Don't scope-creep into other project work.

---

## Step 3: Document (The Receipt)

1. **Update `_status.md`:**
   - Move the completed task to "Done" section with timestamp
   - Update "Current Phase" if this task completed a phase
   - Add any new blockers or notes discovered during work
   - Update "Next Steps" based on what was learned

2. **Create artifacts:** If the task produced files (resumes, research docs, etc.), ensure they're saved in the project folder or appropriate location.

3. **Brief summary:** Note what was accomplished in 1-2 sentences at the top of `_status.md` under "Last Activity."

---

## Step 4: Schedule Next (The Chain)

### 4a. Determine next action

Based on `_status.md` and what was just completed, determine state:

| State | Condition | Action |
|-------|-----------|--------|
| **Continue** | Clear next task, no blockers | Schedule next task |
| **Blocked** | Need something from Zeke | Escalate (non-silent) |
| **Complete** | "Next Steps" empty, no blockers | Close out project |

### 4b. If CONTINUE: Schedule next task

1. **Format:** `/project-task {project-id}: {next task description}`

2. **Timing — Claude Time + ASAP Policy:**

   | Hours | Mode | What to schedule |
   |-------|------|------------------|
   | **1 AM - 7 AM** | **Claude Time** | Silent project work, burst execution |
   | **7 AM - 1 AM** | **Zeke Time** | Non-silent escalations only |

   - Schedule silent project work for **Claude Time** (1-7 AM)
   - A slot = ~20 minutes (max Opus runtime)
   - During Claude Time: ASAP scheduling (next available slot, typically 5 min after current task)
   - If hitting a blocker during Claude Time: schedule non-silent escalation for **7 AM** (start of Zeke Time)
   - This enables burst execution while Zeke sleeps

3. **Silent vs Non-Silent:**
   - **Silent (default):** Pure execution work (research, drafting, processing)
   - **Non-silent:** Milestone reached, deliverable ready for review, or needs input

**Example burst (Claude Time):**
```
1:00 AM - Process resume dump → AI resume (done 1:18) → schedule 1:25
1:25 AM - Draft traditional IT resume (done 1:40) → schedule 1:45
1:45 AM - Research target companies (done 2:05) → schedule 2:10
2:10 AM - Build application tracker (done 2:25) → schedule 2:30
2:30 AM - Prep applications → BLOCKER: Need Zeke to review
         → schedule non-silent for 7:00 AM (Zeke Time)
```
Zeke wakes up to: "Applications ready for your review."

### 4c. If BLOCKED: Escalate to Zeke

1. **Update `_status.md`:** Document the blocker clearly
2. **Schedule NON-SILENT prompt:**
   ```
   /project-task {project-id}: BLOCKER - {what's blocking} - Need: {what you need from Zeke}
   ```
3. **Timing:** Soon (within hours, not days) — blockers should surface quickly
4. **Don't spin:** If the same blocker persists across 2+ scheduled tasks, stop scheduling and wait for Zeke to address it

### 4d. If COMPLETE: Close out project

1. **Verify:** Confirm all tasks in "Next Steps" are done or no longer relevant
2. **Update `_status.md`:** Set `Status: Complete`, add completion date
3. **DO NOT schedule another task** — the chain ends here
4. **Optional:** Move project folder to `.99_Archive/` if Zeke prefers archiving completed work
5. **Non-silent notification:** Let Zeke know the project is complete

### Silent vs Non-Silent Reference

| Situation | Silent? |
|-----------|---------|
| Routine execution (research, drafting) | Yes |
| Background processing | Yes |
| **Blocker hit** | **No** |
| **Need Zeke's input/decision** | **No** |
| **Milestone/phase complete** | **No** |
| **Deliverable ready for review** | **No** |
| **Project complete** | **No** |

**Default:** Silent unless there's something Zeke needs to see, decide, or act on.

---

## Step 5: Report (The Summary)

End with a brief message:
- What was done
- What's scheduled next (or what we're waiting on)
- Any decisions needed from Zeke

---

## Template: _status.md

```markdown
# Project: {Project Name}

**Status:** {Active | Waiting | Complete}
**Current Phase:** {Phase name}
**Last Activity:** {YYYY-MM-DD} - {Brief description}

---

## Overview
{1-2 sentence project description}

## Done
- [x] {Task} (YYYY-MM-DD)

## In Progress
- [ ] {Current task}

## Next Steps
- [ ] {Future task 1}
- [ ] {Future task 2}

## Blocked / Waiting On
- {Blocker or dependency}

## Key Files
- `{filename}` - {description}

## Notes
{Any relevant context, decisions, or observations}
```

---

## When NOT to Use This Skill

- One-off tasks with no project context ("remind me in 20 minutes")
- Simple queries or conversations
- Tasks that are complete in a single action with no follow-up

For those, just use a normal prompt without the skill invocation.
