# Theo Task System Research - Features Worth Stealing

**Date:** 2026-01-24 (autonomous research while Zeke sleeps)
**Status:** Ready for Zeke's review

---

## Executive Summary

Theo's task system is **significantly more sophisticated** than our current Second Brain scheduler. It's built for true autonomous agency with features like silent mode, task rooms, delivery modes, retry logic, and a "heartbeat" architecture for self-healing. Here are the high-value features worth considering.

---

## 1. Silent Mode (HIGH VALUE)

**What it does:** Tasks can run without notifying the user. Output goes to a dedicated task room instead of the main inbox.

**Why it matters:** Enables background maintenance tasks that don't spam the user. Essential for heartbeats and automated housekeeping.

**Theo's implementation:**
```python
create_task(
    name="Daily Vault Cleanup",
    details="...",
    start_time="02:00",
    silent=True,  # <-- Run without notification
    delivery_mode="room_only"  # <-- Output goes to task room only
)
```

**Our gap:** Our scheduler has no concept of silent execution. Every automation triggers a visible prompt.

**Recommendation:** Add a `silent` flag to our scheduler that:
- Skips injecting `[SCHEDULED AUTOMATION]` into visible chat
- Runs in a background context
- Only surfaces if something goes wrong

---

## 2. Task Rooms & Delivery Modes (MEDIUM VALUE)

**What it does:** Each task can have its own isolated "room" (conversation context). This enables:
- Context preservation across task executions
- Task chaining (sub-tasks inherit parent room)
- Isolation from main user conversations

**Delivery modes:**
- `room_only`: Output stays in task room (silent)
- `room_and_inbox`: Output goes to task room AND shows in inbox
- `auto`: Derives from silent flag

**Our gap:** We have a single conversation context. No isolation between automations and interactive work.

**Recommendation:** Consider for future, but not critical now. Claude Code's session model is different enough that this may not translate directly.

---

## 3. Retry Logic & Error Tracking (HIGH VALUE)

**What it does:** Failed tasks automatically retry with exponential backoff. Tasks track:
- `retry_count`
- `last_error`
- `last_error_time`
- `max_retries` (default: 5)
- Status transitions: `active` -> `needs_attention` after max retries

**Theo's implementation:**
```python
task_data = {
    "retry_count": 0,
    "last_error": null,
    "max_retries": 5,
    "status": "active"  # becomes "needs_attention" after failures
}
```

**Our gap:** Our scheduler has basic error tracking (`last_error`) but no retry logic. Failed tasks just... fail.

**Recommendation:** Add to our scheduler:
- `retry_count` with configurable max
- Automatic retry scheduling (e.g., retry in 5 mins)
- Status escalation to `needs_attention` after max retries

---

## 4. The "Heartbeat" Pattern (HIGH VALUE)

**What it does:** Special class of recurring tasks designed for system maintenance. Heartbeats should be:
- **Cheap**: Minimal token usage
- **Idempotent**: Safe to run multiple times
- **Silent**: Only surface when something's wrong

**Examples from Theo:**
- Morning Boot (daily startup routine)
- Gardener Sweep (inbox processing)
- Stuck Task Watchdog (detects scheduling problems)
- System Health Check

**The Heartbeat Philosophy:**
> "Missions store truth. Tasks wake me up. Heartbeats keep the system alive."

**Our gap:** We don't distinguish between "notify user" tasks and "background maintenance" tasks. Our morning/evening syncs are heartbeats but don't have the proper infrastructure.

**Recommendation:**
1. Add a `task_type` field: `"heartbeat"` vs `"notification"` vs `"one_time"`
2. Heartbeats should default to silent
3. Create standard heartbeat tasks: morning sync, inbox triage, stuck-task watchdog

---

## 5. Idempotency Keys (MEDIUM VALUE)

**What it does:** Tasks include an "idempotency key" to prevent duplicate work across multiple executions.

**Format:** `{mission_id}|{phase}|{artifact_target}|{version}`

**Before acting, check:**
- Does the output artifact already exist?
- Did a prior run log completion?
- Is there a "done marker"?

If already complete: log "noop (idempotent)" and exit.

**Our gap:** We don't have explicit idempotency. A task that fails mid-execution could create duplicate work on retry.

**Recommendation:** Lower priority, but worth adding for complex multi-step automations.

---

## 6. Mission Files (STATE PERSISTENCE - HIGH VALUE)

**What it does:** Multi-step initiatives are stored as markdown files with:
- Objective
- Context pointers (file paths)
- Plan checklist
- Append-only execution log

This allows resuming work after total memory loss.

**Example structure:**
```markdown
# Mission: Agentic Framework Refinement
**ID:** 005
**Status:** Active
**Created:** 2025-12-23
**Objective:** Address prioritization and ledger management

## Plan
- [x] Initial analysis
- [x] Write spec
- [ ] Implement patches
- [ ] Verify reliability

## Execution Log
- **2025-12-27**: Implemented ledger sync
- **2025-12-24**: Wrote v1.1 spec
```

**Our gap:** We have no equivalent. Our scheduled prompts are fire-and-forget with no state persistence.

**Recommendation:** For complex multi-session projects, create a missions/ directory structure. Less critical for simple automations.

---

## 7. Message Queue System (MEDIUM VALUE)

**What it does:** All task outputs go through a persistent queue with:
- Status tracking (pending, processed, failed)
- Retry logic (up to 3 attempts)
- Separate queues for manual messages, tasks, and UI actions

**Our gap:** We don't have a message queue. Scheduler runs directly invoke the agent.

**Recommendation:** Consider if we need more reliability, but current approach is simpler and works.

---

## 8. Interruption Handling (LOW-MEDIUM VALUE)

**What it does:** When a user sends a message while Theo is processing:
- Message is immediately appended to chat history
- Deduplication prevents duplicates within 90 seconds
- Interrupts are aggregated for the current turn

**Our gap:** Claude Code handles this at the SDK level differently. Not directly applicable.

---

## 9. Cron Recurrence with croniter (ALREADY HAVE)

**What Theo has:** Full cron syntax support via `croniter` library.

**What we have:** Basic cron support with our own regex parsing.

**Our gap:** Our implementation is simpler but works. Theo's is more robust.

**Recommendation:** If we hit edge cases, consider using croniter library.

---

## Feature Priority Matrix

| Feature | Value | Effort | Priority |
|---------|-------|--------|----------|
| Silent Mode | HIGH | Medium | **1** |
| Heartbeat Pattern | HIGH | Low | **2** |
| Retry Logic | HIGH | Low | **3** |
| Mission Files | HIGH | Medium | **4** |
| Task Rooms | Medium | High | 5 |
| Idempotency | Medium | Medium | 6 |
| Message Queue | Medium | High | 7 |

---

## Immediate Recommendations

### Quick Wins (could implement in a session):

1. **Add `silent` flag to scheduler**
   - Tasks with `silent=true` don't show `[SCHEDULED AUTOMATION]`
   - Run in background, only surface errors

2. **Add retry tracking**
   - `retry_count`, `max_retries` fields
   - Automatic retry scheduling after failure

3. **Create heartbeat tasks**
   - Morning sync (silent by default)
   - Evening sync (silent by default)
   - Stuck-task watchdog

### Bigger Projects (require design):

4. **Mission file system**
   - For multi-session work
   - Markdown files with plan checklists and execution logs

5. **Task status escalation**
   - `active` -> `needs_attention` -> `archived`
   - Notify when tasks need human intervention

---

## Sample Theo Task Packet (for reference)

From their v1.1 spec, a well-formed task should include:

1. **Intent:** What this execution should accomplish
2. **Context pointers:** Exact file paths to read first
3. **Success criteria:** What counts as "done"
4. **Next action:** The next atomic action
5. **Idempotency key:** Prevents duplicate work
6. **Write targets:** Which files to update
7. **Stop conditions:** When to stop/escalate

This is overkill for simple automations but valuable for complex agentic work.

---

## Conclusion

Theo's task system was built for a world where the AI runs autonomously across many sessions. The key insight is **separation of concerns**:
- Tasks are wake-up alarms (not state)
- Artifacts store truth (files first)
- Heartbeats keep the system alive

For Second Brain, the most valuable additions would be:
1. Silent mode for background tasks
2. Retry logic with status escalation
3. The heartbeat pattern for self-maintenance

Let me know which of these you want to implement, and I'll draft the code!

---

*Research completed by Claude during scheduled automation (Zeke asleep)*
