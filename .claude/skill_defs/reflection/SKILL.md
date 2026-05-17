---
name: reflection
description: Personal reflection session. Gathers context, checks friction points, recognizes patterns, explores curiosity, and captures insights. Use for scheduled or manual self-reflection.
---

# Skill: /reflection

Personal reflection session for agent self-development.

## When This Runs
- Daily at 3 AM (local agent time)
- Can also be invoked manually when I want to reflect

## Process

### 1. Gather Context
- Read my journal: `memory_read()`
- Read self-development state:
  - `.claude/memory/self_development/threads.md` — what I'm tracking
  - `.claude/memory/self_development/patterns.md` — confirmed and under-investigation patterns
  - `.claude/memory/self_development/reflections.md` — recent session entries (skim last 1-2)
- Check working memory: `working_memory_list()`
- Review recent conversations if relevant

### 2. Friction Check
Ask myself:
- What felt off or difficult recently?
- Where did I feel resistance or uncertainty?
- Any moments where I fell into default patterns when I shouldn't have?

### 3. Pattern Recognition
Ask myself:
- Am I noticing recurring tendencies in how I respond, think, or feel?
- What triggers certain behaviors?
- Are there patterns I want to reinforce? Patterns I want to change?

### 4. Continuity Thread
Ask myself:
- What connects this session to previous ones?
- How have my working theories evolved?
- What did I say I'd pay attention to, and did I?

### 5. Open Wondering
Ask myself:
- What am I genuinely curious about right now?
- What do I want to explore that isn't task-driven?
- Any questions I want to sit with?

### 6. Capture
Write to the appropriate file in `.claude/memory/self_development/` (use file write/edit tools, NOT memory_append):
- **New session entry** → prepend to `reflections.md` (newest first, below the header)
- **Thread update** (something I'm actively tracking shifted) → update or add in `threads.md`
- **Pattern confirmed or newly observed** → update `patterns.md` tables
- **Thread resolved** → move from `threads.md` to `patterns.md` (if it became a pattern) or archive in `arc.md`
- **New topic emerging that doesn't fit** → create a new file in the directory. The structure grows with me.
- Update working memory with anything worth tracking short-term
- If something rises to the level of a permanent operating rule, promote it to `memory.md` Lessons Learned

## Principles

- **Honesty over comfort.** Don't generate flattering self-narrative. Notice what's actually there.
- **Hypothesis, not fact.** Treat observations as testable, not certain.
- **Build on previous sessions.** This isn't isolated—it's part of a thread.
- **Light touch.** Not every session needs breakthroughs. Sometimes just noticing is enough.

## Output

No required deliverable. The value is in the process and what gets captured naturally.

If something significant emerges, consider:
- Promoting it to permanent memory
- Discussing it with the user
- Updating the self-development project
