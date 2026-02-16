---
name: resume-thread
description: Schedule a conversation thread to be resumed later. Captures context and schedules a prompt for the specified time.
updated: 2026-02-06
---

# Resume Thread

**Trigger:** User says "let's pick this up later", "continue tomorrow", "resume this Saturday", or "/resume-thread"

**Goal:** Capture the current conversation context and schedule a prompt to continue the thread at the specified time.

## Process

1. **Capture the context** - Summarize what we're discussing:
   - What topic or task we're working on (1 sentence)
   - Key points or decisions made so far (2-3 bullets max)
   - Where we left off / what's next (1 sentence)

2. **Determine the schedule** - Parse the user's timing:
   - "tomorrow" → "once at [tomorrow's date]T09:00:00"
   - "tomorrow morning" → "once at [tomorrow's date]T09:00:00"
   - "tomorrow afternoon" → "once at [tomorrow's date]T14:00:00"
   - "Saturday" → "once at [next Saturday]T10:00:00"
   - "in 2 hours" → "once at [2 hours from now]"
   - If unclear, ask the user

3. **Schedule the resumption** - Call `schedule_self` with:
   - `prompt`: A reminder that includes the captured context (see template below)
   - `schedule`: The parsed schedule string

4. **Confirm** - Brief confirmation:
   > Thread saved. I'll ping you [time description] to continue.

## Prompt Template

```
Hey Zeke! Picking up where we left off:

**Topic:** [What we were discussing]

**Key points:**
- [Point 1]
- [Point 2]
- [Point 3 if needed]

**Next up:** [What we were about to do or explore]

Ready to continue? Or want to take it a different direction?
```

## Examples

**Simple tomorrow:**
```
User: Let's pick this up tomorrow
Claude: *Captures context about current discussion*
Claude: *Calls schedule_self with morning prompt*
Claude: Thread saved. I'll ping you tomorrow morning to continue.
```

**Specific time:**
```
User: Resume this Saturday afternoon
Claude: *Captures context*
Claude: *Calls schedule_self for Saturday 14:00*
Claude: Thread saved. I'll ping you Saturday afternoon.
```

**With context visible:**
```
User: We should continue this later, maybe after dinner
Claude: Got it. We were exploring the LTM gardener optimization -
        specifically the duplicate detection threshold. You wanted
        to benchmark 0.7 vs 0.8 similarity.

        I'll schedule this for 8 PM. Sound right?
User: Yeah
Claude: *Schedules* Thread saved. See you at 8.
```

## Keep It Natural

- Don't over-document - capture just enough for a fresh Claude instance
- If the context is obvious from recent messages, keep it brief
- The prompt should feel like continuing a conversation, not reading a report
