---
name: project-task
description: "Structured workflow for project-related tasks. Enforces read context, do work, document, schedule next. Use for any scheduled or manual task that's part of an ongoing project."
updated: 2026-02-15
---

# Project Task Workflow

**Trigger:** Scheduled prompt or manual invocation with `/project-task {project-id}: {task description}`

**Purpose:** Maintain continuity on multi-step projects by enforcing a consistent workflow. Each task reads prior context, executes, documents what happened, and schedules the next step.

---

## Step 0: Parse Input

Extract from the prompt:
- **Project ID:** The identifier after `/project-task` and before the colon (e.g., `job-search-2026`)
- **Task:** Everything after the colon

If no project ID is provided, ask for clarification before proceeding.

---

## Step 1: Load Context (The Memory)

1. **Find project folder:** Look in `10_Active_Projects/` for a folder matching the project ID (fuzzy match OK, e.g., `career-pivot` matches `job-search-2026/`)
2. **Read `_status.md`:** This is the project's state file. Contains:
   - Current phase
   - What's been done
   - What's next
   - Blockers or notes
   - Agent activity (auto-populated by morning sync)
   - Related agents and their project connections
3. **Read related docs:** If `_status.md` references other files (trackers, braindumps, etc.), read those too.
4. **Check related agent outputs:** If `_status.md` has a `## Related Agents` section, check `00_Inbox/agent_outputs/` for recent outputs from those agents that haven't been processed yet.

**If `_status.md` doesn't exist:** Create it with the enhanced template (see bottom of this file) before proceeding.

---

## Step 2: Execute Task (The Work)

Do the task described in the prompt. This might involve:
- Research (web search, reading docs)
- Creating/editing files
- Sending communications
- Processing data
- Dispatching sub-agents for parallel work
- Anything else within my capabilities

**Stay focused on the specific task.** Don't scope-creep into other project work.

**Agent dispatch:** When dispatching sub-agents for project work, **always include the `project` parameter** set to the project folder name. This tags agent output for automatic routing back to the project's `_status.md` during morning sync.

Example:
```
invoke_agent(name="information_gatherer", prompt="...", project="job-search-2026")
schedule_agent(agent="deep_think", prompt="...", schedule="once at ...", project="job-search-2026")
```

---

## Step 3: Document (The Receipt)

1. **Update `_status.md`:**
   - Move the completed task to "Done" section with timestamp
   - Update "Current Phase" if this task completed a phase
   - Add any new blockers or notes discovered during work
   - Update "Next Steps" based on what was learned
   - Update "Next Scheduled" field with whatever is queued next
   - Add entry to "Decision Log" if any decisions were made during this task

2. **Create artifacts:** If the task produced files (resumes, research docs, etc.), ensure they're saved in the project folder or appropriate location. Reference them in "Key Files."

3. **Brief summary:** Note what was accomplished in 1-2 sentences at the top of `_status.md` under "Last Activity."

4. **Update `_index.md`:** Regenerate `10_Active_Projects/_index.md` from all `_status.md` files (quick scan of Last Activity dates and Status fields).

---

## Step 4: Schedule Next (The Chain)

### 4a. Determine next action

Based on `_status.md` and what was just completed, determine state:

| State | Condition | Action |
|-------|-----------|--------|
| **Continue** | Clear next task, no blockers | Schedule next task |
| **Blocked** | Need something from the user | Escalate (non-silent) |
| **Complete** | "Next Steps" empty, no blockers | Close out project |

### 4b. If CONTINUE: Schedule next task

1. **Format:** `/project-task {project-id}: {next task description}`

2. **Timing — ASAP Policy:**
   - Work dispatches **immediately** when possible — no waiting for a batch window
   - For chained project work: schedule ASAP (5 min after current task completes)
   - For work that needs human input: schedule non-silent prompt at a reasonable hour
   - If the user explicitly says "later" or "sleep on it": use the nightly queue as a deliberation buffer

3. **Silent vs Non-Silent:**
   - **Silent (default):** Pure execution work (research, drafting, processing)
   - **Non-silent:** Milestone reached, deliverable ready for review, or needs input

**Example burst:**
```
Task 1 completes at 2:18 AM → schedule Task 2 for 2:25 AM
Task 2 completes at 2:40 AM → schedule Task 3 for 2:45 AM
Task 3 hits BLOCKER → schedule non-silent escalation for 7:00 AM
```
the user wakes up to: "Applications ready for your review."

### 4c. If BLOCKED: Escalate to the user

1. **Update `_status.md`:** Document the blocker clearly under "Blocked / Waiting On" and add to "Human-Only Items" with `Since:` date
2. **Schedule NON-SILENT prompt:**
   ```
   /project-task {project-id}: BLOCKER - {what's blocking} - Need: {what you need from the user}
   ```
3. **Timing:** Soon (within hours, not days) — blockers should surface quickly
4. **Don't spin:** If the same blocker persists across 2+ scheduled tasks, stop scheduling and wait for the user to address it

### 4d. If COMPLETE: Close out project

1. **Verify:** Confirm all tasks in "Next Steps" are done or no longer relevant
2. **Update `_status.md`:** Set `Status: Complete`, add completion date
3. **DO NOT schedule another task** — the chain ends here
4. **Optional:** Move project folder to `.99_Archive/` if the user prefers archiving completed work
5. **Non-silent notification:** Let the user know the project is complete
6. **Update `_index.md`:** Remove from active projects index

### Silent vs Non-Silent Reference

| Situation | Silent? |
|-----------|---------|
| Routine execution (research, drafting) | Yes |
| Background processing | Yes |
| **Blocker hit** | **No** |
| **Need the user's input/decision** | **No** |
| **Milestone/phase complete** | **No** |
| **Deliverable ready for review** | **No** |
| **Project complete** | **No** |

**Default:** Silent unless there's something the user needs to see, decide, or act on.

---

## Step 5: Report (The Summary)

End with a brief message:
- What was done
- What's scheduled next (or what we're waiting on)
- Any decisions needed from the user

---

## Template: _status.md (Enhanced)

```markdown
# Project: {Project Name}

**Status:** {Active | Waiting | Complete}
**Current Phase:** {Phase name}
**Last Activity:** {YYYY-MM-DD} - {Brief description}
**Owner:** {Ren | CC | the user}
**Next Scheduled:** {What's already queued, or "nothing"}

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

## Human-Only Items
- [ ] {Task only the user can do} -- Since: {YYYY-MM-DD}

## Blocked / Waiting On
- {Blocker or dependency}

## Agent Activity
<!-- Auto-populated by morning sync from tagged agent outputs -->
- [{YYYY-MM-DD}] `{agent}`: {one-line summary}

## Related Agents
- `job_scan` -> job-search-2026
- `daily_news_ai` -> check for relevant opportunities

## Decision Log
- {YYYY-MM-DD}: {Decision made and WHY}

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
