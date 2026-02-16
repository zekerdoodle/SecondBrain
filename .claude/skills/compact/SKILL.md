---
name: compact
description: Compact conversation history to free up context window space. Summarizes older messages while preserving key information.
updated: 2026-02-03
---

# Workflow: Compact Conversation

**Trigger:** Manual request via `/compact` command.
**Goal:** Summarize older conversation history to free up context window space while preserving critical information.

## How This Works

The Claude Agent SDK has built-in automatic compaction that triggers at ~80% context usage. This command allows manual compaction when you want to:

1. **Proactively free space** before starting a large task
2. **Clean up** after a verbose exploration/research phase
3. **Prepare** for long-running operations that need more context room

## What Happens During Compaction

1. **Older messages are summarized** - Claude creates a condensed summary of earlier conversation turns
2. **Recent interactions are preserved** - The most recent messages remain unchanged
3. **Critical information is retained** - Key decisions, file paths, and important context survive compaction
4. **A compact boundary is marked** - The transcript records when compaction occurred

## Execution

When the user invokes `/compact`, respond with:

1. **Acknowledge the request**: "Triggering context compaction..."
2. **Explain what will happen**: Briefly note that older messages will be summarized while preserving recent context
3. **Provide feedback**: "Context compacted successfully. Older messages have been summarized to free up space for new work."

## Notes

- Compaction is automatic when context reaches ~80% capacity (configurable via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`)
- Manual compaction is useful before starting intensive tasks
- Subagents have isolated context windows and compact independently
- Session checkpoints are preserved during compaction
