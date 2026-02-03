---
name: sync
description: Intelligent Agentic Sync. Use this to process the inbox, sort knowledge, sync Google Tasks/Calendar, and schedule deep work.
updated: 2026-02-01
---

# Workflow: Intelligent Sync ("The Pulse")

**Trigger:** Scheduled Daily (05:00) or Manual Request.
**Goal:** act as a Chief of Staff. Process *all* raw inputs, execute external actions, and organize internal knowledge.

## Step 1: Ingest (The Eyes)
1.  **Scan:** Check `00_Inbox/` for **ALL** files (not just `scratchpad.md`).
2.  **Agent Outputs:** Check `00_Inbox/agent_outputs/` for any pending work from scheduled agents.
    *   Review each file and summarize findings for the user.
    *   Take action on recommendations if appropriate.
    *   Archive processed agent outputs to `99_Archive/Agent_Outputs/[YYYY]/[MM]/`.
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

## Step 3: Velocity Defense (The Scheduler)
1.  **Scan Projects:** Read `10_Active_Projects/` to identify the single highest-priority "Next Step."
2.  **Block Time:**
    *   *Rule:* If it's a weekday, schedule a **"Deep Work: [Task Name]"** block for **17:30 - 18:30**.
    *   *Action:* Create Google Calendar event.

## Step 4: Cleanup (The Janitor)
1.  **Archive:**
    *   Move processed files from `00_Inbox/` to `99_Archive/Processed_Inbox/[YYYY]/[MM]/`.
    *   *Note:* NEVER delete data. Always archive.
2.  **Reset:**
    *   Ensure `00_Inbox/scratchpad.md` exists (empty) for new capture.

## Step 5: Final Report (The Assistant)
*   **Summary:** "✅ Created 3 Tasks, 1 Event. Appended Journal. Scheduled Deep Work for [Task]."
*   **Clarification:** Explicitly ask about ambiguous items (e.g., "Was 'Buy Cheese' a task or a note?").
