# Agents v2 Architecture Specification

**Status:** Draft
**Created:** 2026-01-31
**Author:** Claude + Zeke

---

## Executive Summary

This document specifies a new agent architecture for Second Brain that replaces the current "subagent" paradigm (synchronous Task tool invocation) with independent **Agents** that can be:

1. **Invoked by Claude <3** with three modes: foreground, ping, trust
2. **Scheduled** to run at specific times or intervals
3. **Run independently** without blocking the main conversation

The key insight: Agents are autonomous workers that Claude <3 can delegate to, not just context-isolated helpers.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Zeke (User)                              │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Claude <3                                 │
│                     (Primary Agent)                              │
│                                                                  │
│  Toolkit:                                                        │
│    - Google (Tasks, Calendar, Gmail)                             │
│    - Spotify                                                     │
│    - Memory (journal, working_memory)                            │
│    - Scheduler (buffed - can schedule agents)                    │
│    - invoke_agent (NEW - replaces Task tool for agents)          │
│    - Read (limited file access)                                  │
│                                                                  │
│  Skills: sync, finance, expand-and-structure, project-task,      │
│          reflection                                              │
└─────────────────────────────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │    invoke_agent()     │
                    │  mode: foreground |   │
                    │        ping | trust   │
                    └───────────┬───────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│ Claude_Code  │      │  Info        │      │  Deep Think  │
│ (CLI-based)  │      │  Gatherer    │      │  (Opus)      │
│              │      │              │      │              │
│ Coding tasks │      │ Research &   │      │ Complex      │
│ Full tools   │      │ web search   │      │ reasoning    │
└──────────────┘      └──────────────┘      └──────────────┘
        │                       │                       │
        ▼                       ▼                       ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│ General      │      │  Librarian   │      │  Gardener    │
│ Purpose      │      │  (memory)    │      │  (memory)    │
│              │      │              │      │              │
│ Misc tasks   │      │ Extract      │      │ Maintain     │
│ Flexible     │      │ memories     │      │ memories     │
└──────────────┘      └──────────────┘      └──────────────┘
```

---

## Invocation Modes

### 1. Foreground ("I'll wait")

**Behavior:** Synchronous execution. Claude <3 blocks until the agent completes.

**Use cases:**
- Quick lookups ("What's the weather?")
- Simple code fixes
- Any task where Claude needs the result to continue

**Implementation:**
```python
result = await invoke_agent(
    name="information_gatherer",
    prompt="Find the current Bitcoin price",
    mode="foreground"
)
# Claude <3 waits here, then uses result
```

### 2. Ping ("Let me know when done")

**Behavior:** Async execution with notification injection.

**Flow:**
1. Claude <3 invokes agent → returns immediately with acknowledgment
2. Agent runs in background
3. On completion:
   - Agent's final reply is written to notification queue
   - If Claude <3 receives a prompt within 15 minutes → notification injected
   - If inactive for 15 minutes → Claude <3 is "woken up" in the original chat

**Use cases:**
- Research tasks ("Research the competition for X")
- Code tasks that take a while ("Refactor the auth system")
- Anything where Claude wants to know the outcome but doesn't need to block

**Implementation:**
```python
ack = await invoke_agent(
    name="claude_code",
    prompt="Add comprehensive tests to the scheduler",
    mode="ping"
)
# Returns immediately: "Claude_Code is working on your task..."
# Later: notification injected with agent's final reply
```

**Notification Injection:**
```
[AGENT NOTIFICATION]
Agent: Claude_Code
Status: Completed
Invoked: 2026-01-31 14:30:00
Completed: 2026-01-31 14:35:22

--- Agent Response ---
Added 12 test cases to scheduler_tool_test.py:
- test_add_task_basic
- test_add_task_silent
- test_daily_schedule_parsing
...
All tests passing.
```

### 3. Trust ("Just do it")

**Behavior:** Fire and forget. No notification, no injection.

**Use cases:**
- Maintenance tasks ("Clean up old temp files")
- Background processing ("Index the new documents")
- Anything where the work itself IS the result

**Implementation:**
```python
await invoke_agent(
    name="general_purpose",
    prompt="Archive completed project folders older than 30 days",
    mode="trust"
)
# Returns immediately, no further notification
# Results logged to agent execution log
```

---

## Agent Definitions

### Claude_Code

**Implementation:** CLI headless (`claude --print`)
**Model:** Opus (default), Sonnet (optional)
**Restored from:** `interface/server/mcp_tools/archive/claude_code.py`

**Purpose:** Software development tasks - coding, debugging, refactoring, implementation.

**Tools:** Full Claude Code toolset (Read, Write, Edit, Bash, Glob, Grep, etc.)

**Config:**
```yaml
name: claude_code
type: cli  # Special: uses claude CLI, not AgentSDK
model: opus
description: "Expert coding agent for software development. Handles code writing, debugging, refactoring, and technical implementation."
timeout_seconds: 600
```

### Information Gatherer

**Implementation:** AgentSDK
**Model:** Sonnet
**Based on:** Current `web-research` subagent

**Purpose:** Research, web search, documentation lookup, information synthesis.

**Tools:**
- Read, Glob (local file access)
- mcp__brain__web_search
- mcp__brain__page_parser
- Write (for saving research artifacts)

**Config:**
```yaml
name: information_gatherer
type: sdk
model: sonnet
description: "Research specialist for gathering information from web and local sources. Returns structured findings."
tools:
  - Read
  - Glob
  - Write
  - mcp__brain__web_search
  - mcp__brain__page_parser
```

### General Purpose

**Implementation:** AgentSDK
**Model:** Sonnet

**Purpose:** Flexible agent for miscellaneous tasks that don't fit other specialists.

**Tools:**
- Read, Write, Edit
- Glob, Grep
- Bash (limited)
- WebFetch

**Config:**
```yaml
name: general_purpose
type: sdk
model: sonnet
description: "Flexible agent for general tasks. Can read/write files, run commands, and handle miscellaneous work."
tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - WebFetch
```

### Deep Think

**Implementation:** AgentSDK
**Model:** Opus
**Based on:** Current `deep-research` subagent

**Purpose:** Complex reasoning, architecture decisions, strategic planning, thorough analysis.

**Tools:**
- Read, Glob, Grep (research)
- Write (for outputting analysis)
- WebSearch (optional)

**Config:**
```yaml
name: deep_think
type: sdk
model: opus
description: "Deep reasoning specialist for complex problems. Use for architecture decisions, strategic planning, thorough analysis."
tools:
  - Read
  - Glob
  - Grep
  - Write
  - mcp__brain__web_search
```

### Librarian (Background)

**Implementation:** AgentSDK
**Model:** Sonnet
**Status:** Existing, keep as-is

**Purpose:** Extract memories from conversation exchanges.

**Changes:**
- Move to `agents/background/librarian/`
- Enable invocation by Claude <3 (currently scheduler-only)

### Gardener (Background)

**Implementation:** AgentSDK
**Model:** Sonnet
**Status:** Existing, keep as-is

**Purpose:** Memory maintenance - deduplication, consolidation, importance adjustment.

**Changes:**
- Move to `agents/background/gardener/`
- Enable invocation by Claude <3

---

## Directory Structure

```
.claude/agents/
├── __init__.py
├── registry.py              # Agent discovery and loading
├── runner.py                # Execution engine (3 modes)
├── notifications.py         # Ping mode notification queue
├── models.py                # AgentConfig, InvocationMode, etc.
│
├── claude_code/
│   ├── config.yaml
│   └── prompt.md            # System prompt for CLI
│
├── information_gatherer/
│   ├── config.yaml
│   └── prompt.md
│
├── general_purpose/
│   ├── config.yaml
│   └── prompt.md
│
├── deep_think/
│   ├── config.yaml
│   └── prompt.md
│
└── background/
    ├── librarian/
    │   ├── config.yaml
    │   ├── prompt.md        # librarian.md (existing)
    │   └── runner.py        # librarian_runner.py (existing)
    │
    └── gardener/
        ├── config.yaml
        ├── prompt.md
        └── runner.py
```

---

## Notification System (Ping Mode)

### Notification Queue

**Location:** `.claude/agents/notifications/pending.json`

**Structure:**
```json
{
  "notifications": [
    {
      "id": "uuid",
      "agent": "claude_code",
      "invoked_at": "2026-01-31T14:30:00Z",
      "completed_at": "2026-01-31T14:35:22Z",
      "source_chat_id": "abc-123",
      "agent_response": "Added 12 test cases...",
      "status": "pending"  // pending | injected | expired
    }
  ]
}
```

### Injection Logic

**On any prompt to Claude <3:**
1. Check `pending.json` for notifications with `status: pending`
2. For each pending notification:
   - If `completed_at` is within 15 minutes → inject into prompt
   - Mark as `status: injected`
3. Injected notifications appear as system context at the start of Claude's turn

**Wake-up Logic (15 min inactive):**
1. Background task checks every minute
2. If notification is pending AND `completed_at` > 15 min ago AND no recent activity:
   - Create a synthetic "wake-up" in the `source_chat_id`
   - Inject the notification
   - Mark as `status: injected`

### Injection Format

```markdown
---
**[Agent Notification]**

**Agent:** Claude_Code
**Task started:** 2026-01-31 14:30:00
**Completed:** 2026-01-31 14:35:22

**Agent's Response:**
> Added 12 test cases to scheduler_tool_test.py covering:
> - Basic task creation
> - Silent mode
> - Daily schedule parsing
> - Cron syntax
>
> All tests passing. Run `pytest .claude/scripts/test_scheduler.py` to verify.

---
```

---

## Scheduler Upgrades

### Current Capabilities

- Schedule prompts to Claude <3
- Formats: `every X minutes`, `daily at HH:MM`, `once at DATETIME`, cron
- Silent mode (no notification)

### New Capabilities

**1. Schedule Agent Invocations:**

```python
add_task(
    type="agent",
    agent="librarian",
    prompt="Process buffered exchanges",
    schedule="every 30 minutes",
    mode="trust",  # foreground | ping | trust
    silent=True
)
```

**2. Task Format Update:**

```json
{
  "id": "abc123",
  "type": "prompt",           // "prompt" (existing) | "agent" (new)
  "prompt": "...",
  "schedule": "daily at 2:00am",
  "active": true,
  "silent": false,

  // New fields for type="agent":
  "agent": "claude_code",     // Agent name
  "mode": "trust"             // Invocation mode
}
```

**3. Scheduler Tool Updates:**

New tool: `schedule_agent`
```yaml
name: schedule_agent
description: "Schedule an agent to run at a specific time or interval"
input_schema:
  properties:
    agent:
      type: string
      description: "Agent name: claude_code, information_gatherer, general_purpose, deep_think, librarian, gardener"
    prompt:
      type: string
      description: "Task description for the agent"
    schedule:
      type: string
      description: "When to run: 'every X minutes', 'daily at HH:MM', 'once at DATETIME', or cron"
    mode:
      type: string
      enum: [foreground, ping, trust]
      default: trust
    silent:
      type: boolean
      default: true
```

---

## Claude <3 Tool Changes

### Remove

- **Task tool** for subagent invocation (replaced by `invoke_agent`)

### Add

- **invoke_agent** - Invoke any agent with specified mode

### Modify

- **scheduler tools** - Add `schedule_agent` capability

### invoke_agent Tool Spec

```yaml
name: invoke_agent
description: |
  Invoke a specialized agent to handle a task.

  Agents available:
  - claude_code: Software development (coding, debugging, refactoring)
  - information_gatherer: Research and web search
  - general_purpose: Flexible agent for misc tasks
  - deep_think: Complex reasoning and analysis (uses Opus)
  - librarian: Memory extraction (usually scheduled)
  - gardener: Memory maintenance (usually scheduled)

  Invocation modes:
  - foreground: Wait for result (blocking)
  - ping: Run async, notify when done
  - trust: Fire and forget

input_schema:
  properties:
    agent:
      type: string
      enum: [claude_code, information_gatherer, general_purpose, deep_think, librarian, gardener]
    prompt:
      type: string
      description: "Task description for the agent"
    mode:
      type: string
      enum: [foreground, ping, trust]
      default: foreground
    model_override:
      type: string
      enum: [sonnet, opus, haiku]
      description: "Override the agent's default model (optional)"
  required: [agent, prompt]
```

---

## Migration Plan

### Phase 1: Foundation

1. Create `.claude/agents/` directory structure
2. Create `registry.py` - agent discovery
3. Create `models.py` - config schemas
4. Create `runner.py` - basic execution (foreground only)

### Phase 2: Agent Migration

1. Restore `claude_code` from archive → adapt to new structure
2. Move `librarian` and `gardener` to `agents/background/`
3. Create `information_gatherer` (based on web-research)
4. Create `general_purpose`
5. Create `deep_think` (based on deep-research)

### Phase 3: Invocation Modes

1. Implement ping mode in `runner.py`
2. Create `notifications.py` - queue management
3. Implement notification injection in `main.py`
4. Implement wake-up logic
5. Implement trust mode

### Phase 4: Scheduler Integration

1. Update `scheduler_tool.py` - support `type: agent`
2. Update `main.py:scheduler_loop()` - route to agents
3. Add `schedule_agent` MCP tool

### Phase 5: Claude <3 Integration

1. Create `invoke_agent` MCP tool
2. Update `agent_config.yaml` - add invoke_agent, schedule_agent
3. Update `primary_agent_instructions.md` - document new capabilities

### Phase 6: Cleanup

1. Archive `.claude/subagents/` to `.99_Archive/subagents_v1/`
2. Remove Task tool subagent injection from `claude_wrapper.py`
3. Update documentation
4. Test everything

---

## Open Questions

1. **Agent execution logging:** Where should agent runs be logged? Separate log file? Database?

2. **Concurrent execution limits:** Should we limit how many agents can run simultaneously?

3. **Agent-to-agent communication:** Can agents invoke other agents? (Probably no for v1)

4. **Error handling in ping mode:** If an agent fails, how is Claude <3 notified?

5. **Working memory integration:** Should ping notifications go to working memory instead of a separate queue?

---

## Appendix: Config Schema

### Agent Config (config.yaml)

```yaml
# Required
name: string              # Unique identifier
type: sdk | cli           # sdk = AgentSDK, cli = claude CLI
model: sonnet | opus | haiku
description: string       # Shown to Claude <3 for selection

# Optional
tools: [string]           # For type=sdk only
timeout_seconds: int      # Default: 300 for sdk, 600 for cli
output_format:            # For type=sdk with structured output
  type: json_schema
  schema: {...}
```

### Invocation Request

```python
@dataclass
class AgentInvocation:
    agent: str                    # Agent name
    prompt: str                   # Task description
    mode: Literal["foreground", "ping", "trust"]
    source_chat_id: str           # For ping mode notifications
    model_override: Optional[str] # Override default model
    invoked_at: datetime
```

### Invocation Result

```python
@dataclass
class AgentResult:
    agent: str
    status: Literal["success", "error", "timeout"]
    response: str                 # Agent's final output
    started_at: datetime
    completed_at: datetime
    error: Optional[str]
```
