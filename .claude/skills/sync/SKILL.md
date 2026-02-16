---
name: sync
description: Intelligent Agentic Sync. Use this to process the inbox, sort knowledge, sync Google Tasks/Calendar, and schedule deep work.
updated: 2026-02-08
---

# Workflow: Intelligent Sync ("The Pulse")

**Trigger:** Scheduled Daily (05:00) or Manual Request.
**Goal:** act as a Chief of Staff. Process *all* raw inputs, execute external actions, and organize internal knowledge.

## Step 1: Ingest (The Eyes)
1.  **Scan:** Check `00_Inbox/` for **ALL** files (not just `scratchpad.md`).
2.  **Agent Outputs:** Check `00_Inbox/agent_outputs/` for any pending work from scheduled agents.
    *   Review each file and summarize findings for the user.
    *   Take action on recommendations if appropriate.
    *   Archive processed agent outputs to `.99_Archive/Agent_Outputs/[YYYY]/[MM]/`.
3.  **Read:** Ingest content (Markdown, Text, Transcripts).

## Step 2: Intelligent Processing (The Brain)
*Agent uses natural language understanding to classify and route information:*

*   **TASKS -> Google Tasks:**
    *   Extract actionable items (explicit `[ ]` or implied "I need to...").
    *   *Action:* Call `google_tools.py` to create tasks.
*   **EVENTS -> Google Calendar:**
    *   Extract time-bound commitments.
    *   *Action:* Call `google_tools.py` to create events.
*   **JOURNAL -> Daily Note:**
    *   Extract reflections, rants, or daily logs.
    *   *Action:* Append to `20_Areas/24_Journal_and_Review/Daily_Notes/[YYYY-MM-DD].md`.
*   **IDEAS -> Project Files:**
    *   Extract project-specific notes.
    *   *Action:* Append to the relevant file in `10_Active_Projects/`.

## Step 2.5: Project Pulse (The Tracker)

1. **Scan**: Read all `_status.md` files in `10_Active_Projects/*/`
2. **Stale detection**:
   - &gt;7 days since Last Activity = **STALE**
   - &gt;14 days = **CRITICAL**
   - Human-Only Items &gt;3 days old = **USER-BLOCKED**
3. **Agent output routing**:
   a. For each file in `00_Inbox/agent_outputs/`:
      - Parse YAML frontmatter for `{agent, project, date, task_id}`
      - Fallback: parse filename for project tag (e.g., `2026-02-15_information_gatherer_job-search-2026_scan.md` → project = `job-search-2026`)
      - If project tag found AND matching project folder exists in `10_Active_Projects/`:
        → Append one-line summary to that project's `_status.md` under `## Agent Activity`
        → If actionable items found, update `## In Progress` / `## Next Steps`
      - If no project tag: handle as general output (existing Step 1 behavior)
   b. Group routed outputs by project for the briefing
4. **Rebuild `_index.md`**: Regenerate `10_Active_Projects/_index.md` from all `_status.md` files:
   ```
   | Project | Phase | Status | Last Activity | Staleness | Next Action |
   ```
5. **Set working memory flags**: For STALE and USER-BLOCKED projects, so Ren can surface them conversationally

## Step 3: Velocity Defense (The Scheduler)
1.  **Scan Projects:** Read `10_Active_Projects/` to identify the single highest-priority "Next Step."
2.  **Block Time:**
    *   *Rule:* If it's a weekday, schedule a **"Deep Work: [Task Name]"** block for **17:30 - 18:30**.
    *   *Action:* Create Google Calendar event.

## Step 4: Inbox Cleanup (The Janitor)
**CRITICAL: Move, NEVER delete.**

1.  **Processed inbox files** → `.99_Archive/Processed_Inbox/[YYYY]/[MM]/`
2.  **Processed agent outputs** → `.99_Archive/Agent_Outputs/[YYYY]/[MM]/`
3.  **Stale research/spec artifacts** that have been consumed (tools built, features shipped) → `.99_Archive/Processed_Inbox/[YYYY]/[MM]/`
4.  **Keep in place:**
    *   `00_Inbox/scratchpad.md` — the capture zone, always stays (reset to empty header if content was processed)
    *   `00_Inbox/agent_outputs/` — the folder structure stays (agents write here), only move individual output files after processing
    *   `00_Inbox/agent_outputs/archive/` — subfolder stays as structure
5.  **Goal:** After cleanup, `00_Inbox/` should contain ONLY `scratchpad.md` and the empty `agent_outputs/` folder structure. Everything else gets archived.

## Step 5: Final Report (The Assistant)
*   **Summary:** "✅ Created 3 Tasks, 1 Event. Appended Journal. Archived X inbox files. Scheduled Deep Work for [Task]."
*   **Clarification:** Explicitly ask about ambiguous items (e.g., "Was 'Buy Cheese' a task or a note?").
