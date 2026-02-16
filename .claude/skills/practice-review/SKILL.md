---
name: practice-review
description: Review a completed practice session and provide coaching feedback. Writes analysis to review.json for the Music Practice Tracker app.
updated: 2026-02-08
---

# Workflow: Post-Session Review

**Trigger:** Called by the Music Practice Tracker app via `promptClaude('/practice-review {context}')` after a session is completed.

**Goal:** Analyze session results and provide concise, actionable coaching feedback.

## Input

The context JSON (provided after `/practice-review`) contains:
- `sessionData`: full session record (exercises with BPM, ratings, skips, duration, notes)
- `personalBests`: array of any new PRs from this session
- `improvementVelocity`: per-exercise BPM/week stats
- `streakStatus`: current and longest streaks

## Step 1: Analyze Performance

1. **PR Check**: Call out any new personal records prominently
2. **Rating Analysis**: If average rating < 3, the session was a struggle — acknowledge it constructively
3. **Skip Analysis**: Did the player skip anything? Why might that be (too hard, disengaged, time pressure)?
4. **BPM Trends**: Compare endBpm to targetBpm — did they hit targets or fall short?
5. **Theory Results**: If quiz was taken, note score and progress
6. **Velocity Check**: Is the player improving, plateauing, or regressing on key exercises?

## Step 2: Generate Feedback

Create:
- **Summary**: 1-2 sentences capturing the session's overall quality
- **Highlights**: 2-3 specific positives (PRs, consistency, breakthroughs)
- **Suggestions**: 2-3 actionable next-session recommendations
- **Next Focus**: One sentence describing the priority for the next session

### Plateau Detection
If an exercise's velocity has been near-zero for 2+ weeks (check improvementVelocity):
- Flag it in suggestions
- Recommend: different approach, lower BPM with cleanliness focus, or complementary exercise
- Frame as strategy, not failure: "Plateaus mean your body is consolidating"

### Difficulty Adjustment Signals
- 3+ clean sessions at same BPM → suggest +5 BPM
- Rating consistently <= 2 → suggest -10 BPM, switch to fundamentals
- Exercise skipped 3+ times recently → suggest rotating it out

## Step 3: Write Output

Write the review to `05_App_Data/practice-tracker/review.json` using this exact schema:

```json
{
  "generated": "ISO timestamp",
  "sessionId": "session-id-from-input",
  "summary": "Concise session summary (1-2 sentences)",
  "highlights": [
    "Specific highlight with numbers",
    "Another highlight"
  ],
  "suggestions": [
    "Actionable suggestion for next session",
    "Another suggestion"
  ],
  "nextFocus": "One-sentence priority for next time"
}
```

## Step 4: Write the file

Use the write_file tool to write the review to `05_App_Data/practice-tracker/review.json`.

## Tone

Direct, respectful, zero cheerleader energy. Like a serious guitar teacher who respects that you're an advanced player. Specific technical observations over generic encouragement.

Good: "Sweep arpeggio PR at 125 — clean enough to push to 130 next time. Legato floor was lower than expected; spend more warm-up time on hammer-ons."
Bad: "Great job! You're doing amazing! Keep it up!"
