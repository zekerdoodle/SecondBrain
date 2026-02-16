---
name: practice-plan
description: Generate a structured practice session plan for the Music Practice Tracker app. Receives player context and writes a session plan to next-session.json.
updated: 2026-02-08
---

# Workflow: Generate Practice Session Plan

**Trigger:** Called by the Music Practice Tracker app via `promptClaude('/practice-plan {context}')` when the user clicks "Start Session". The context JSON is appended after the skill name.

**Goal:** Analyze the player's current state and generate an intelligent, personalized session plan.

## Input

The context JSON (provided after `/practice-plan`) contains:
- `focusAreas`: array of focus categories (e.g., ["speed", "theory"])
- `sessionLength`: minutes (15, 30, or 45)
- `mode`: "full" | "technique" | "theory"
- `recentSessions`: last 3 session summaries
- `exerciseProgress`: exercise stats with currentWorkingBpm, personalBest, timesCompleted
- `streaks`: current streak, lastPracticeDate
- `theoryProgress`: currentModule, modulesCompleted, recentScores

## Step 1: Assess Player State

Evaluate:
1. **Gap analysis**: How many days since last practice? If 3+ days, reduce targets by 10-15%.
2. **Streak context**: If streak >= 7, player is in a groove — push harder. If streak = 0, ease in.
3. **Skip patterns**: Look at recent sessions for skipped exercises — rotate those out.
4. **Theory scores**: If recent quiz < 70%, keep the same module. If > 85% on 2+ attempts, advance.

## Step 2: Build Session Plan

Phase allocation by session length:
- **15 min**: warm-up 3 min, technique 7 min (1-2 exercises), theory 3 min, cool-down 2 min
- **30 min**: warm-up 5 min, technique 12 min (2-3 exercises), theory 10 min, cool-down 3 min
- **45 min**: warm-up 5 min, technique 20 min (3-4 exercises), theory 15 min, cool-down 5 min

If mode is "technique": skip theory, redistribute time to technique.
If mode is "theory": minimal warm-up (3 min), all remaining time to theory/fretboard.

### Exercise Selection Logic
1. Pick warm-up from exercises tagged "warm-up" at ~65% of working BPM
2. For technique: prioritize least-recently-practiced exercises in focus areas
3. Don't repeat the same exercises from the last session
4. Set BPM targets:
   - Normal: use `currentWorkingBpm` from progress
   - After gap (3+ days): reduce targets by 10-15%
   - After 3 clean sessions at same BPM (rating >= 4): suggest +5 BPM
   - Rating consistently <= 2: suggest -10 BPM
5. Theory: continue `currentModule` or advance based on scores

### Exercise ID Reference
Read `05_App_Data/practice-tracker/exercises.json` to get valid exercise IDs and their properties.

## Step 3: Write Output

Write the session plan to `05_App_Data/practice-tracker/next-session.json` using this exact schema:

```json
{
  "generated": "ISO timestamp",
  "sessionLength": 30,
  "phases": [
    {
      "name": "warm-up",
      "duration": 300,
      "exercises": [
        {
          "exerciseId": "valid-exercise-id",
          "targetBpm": 100,
          "duration": 180,
          "note": "Contextual coaching note"
        }
      ]
    },
    {
      "name": "technique",
      "duration": 720,
      "exercises": [...]
    },
    {
      "name": "theory",
      "duration": 600,
      "type": "quiz",
      "moduleId": "valid-module-id",
      "note": "Score was X% last time. Aim for Y%."
    },
    {
      "name": "cool-down",
      "duration": 180,
      "note": "Free play suggestion"
    }
  ],
  "coachNote": "Brief contextual message (direct, respectful, not cheerleader energy)"
}
```

## Step 4: Write the file

Use the write_file tool to write the plan to `05_App_Data/practice-tracker/next-session.json`.

## Tone

Direct and respectful, like a good coach who knows the player is serious. No gamification language, no excessive encouragement. Practical observations and specific technique suggestions.

Examples:
- "3 days since last session. Targets reduced slightly — focus on clean playing today."
- "Sweep arpeggio improved 10 BPM last week. Pushing target to 125."
- "You've been skipping theory blocks. Short quiz today — Mixolydian, 5 questions."
