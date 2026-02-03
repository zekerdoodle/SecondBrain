# Claude Agent Architecture

A technical reference for Claude (the primary agent) documenting how this system works, what tools and capabilities exist, and how to modify the system.

---

## Overview

This is a three-tier architecture:

```
┌──────────────────────────────────────────────────────────────────┐
│                     User Interface (Web App)                     │
│                   React + TypeScript + Tailwind                  │
└─────────────────────────────┬────────────────────────────────────┘
                              │ WebSocket (ws://localhost:8000/ws/{session_id})
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Backend (FastAPI Server)                      │
│              Session management, chat persistence                │
└─────────────────────────────┬────────────────────────────────────┘
                              │ Claude Agent SDK
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                 Primary Agent (Claude Opus 4.5)                  │
│          System prompt + MCP tools + Subagent definitions        │
└───────┬──────────────────────┬────────────────────────┬──────────┘
        │                      │                        │
        ▼                      ▼                        ▼
   ┌─────────┐          ┌───────────┐            ┌───────────┐
   │MCP Tools│          │ Subagents │            │ External  │
   │ (50+)   │          │ (Sonnet/  │            │   APIs    │
   │         │          │  Opus)    │            │           │
   └─────────┘          └───────────┘            └───────────┘
```

---

## Part 1: User Interface (Web App)

### From the User's Perspective

The interface is a chat application accessible at `http://localhost:8000`. Users interact with Claude through:

1. **Chat panel** - Type messages, see streaming responses, view tool usage
2. **File tree** - Browse and open files in the workspace
3. **Editor panel** - Edit files with markdown preview
4. **Chat history** - Search and resume previous conversations

### Key Features

| Feature | Description |
|---------|-------------|
| **Real-time streaming** | Responses stream in token-by-token |
| **Tool visibility** | Users see when tools are invoked and their results |
| **Thinking display** | Claude's reasoning is shown (collapsible) |
| **Message editing** | Users can edit messages and regenerate responses |
| **Push notifications** | Browser notifications when Claude needs attention |
| **Offline support** | PWA with service worker for offline capability |

### Tech Stack

- **Framework:** React 18 + TypeScript
- **Build:** Vite 6
- **Styling:** Tailwind CSS
- **Icons:** Lucide React
- **State:** React hooks (no external state library)

### Key Files

| File | Purpose |
|------|---------|
| `interface/client/src/App.tsx` | Main router and layout |
| `interface/client/src/Chat.tsx` | Chat interface component |
| `interface/client/src/useClaude.ts` | WebSocket connection hook |
| `interface/client/src/FileTree.tsx` | File browser |
| `interface/client/src/Editor.tsx` | Markdown editor |

---

## Part 2: Primary Agent (Claude)

### System Instructions

**Location:** `.claude/CLAUDE.md`

This is the primary system prompt. It's automatically loaded via Claude Agent SDK's `setting_sources=["project"]` mechanism. The file defines:

- Who Claude is and how to operate
- Available MCP tools and their purposes
- Skills and when to use them
- Memory systems and how they work
- Workspace structure
- Scheduling policies

**To modify behavior:** Edit `CLAUDE.md`. Changes take effect on the next conversation turn (no restart needed).

### How the System Prompt is Built

The server constructs the full prompt from multiple sources:

```
1. Base SDK prompt (capabilities)
2. memory.md (self-journal) - injected directly
3. Working memory (ephemeral items) - injected if present
4. Semantic LTM (relevant memories) - injected based on query
5. CLAUDE.md - loaded via SDK setting_sources
```

**Implementation:** `interface/server/claude_wrapper.py`

### Available Tools

Claude has two types of tools:

#### Native Tools (from Claude Code)
- `Read` - Read files
- `Write` - Write files
- `Edit` - Edit files
- `Bash` - Run shell commands
- `Glob` - Find files by pattern
- `Grep` - Search file contents
- `WebFetch` - Fetch web pages
- `WebSearch` - Search the web
- `Task` - Spawn subagents

#### MCP Tools (custom, via `mcp__brain__*`)

| Category | Tools |
|----------|-------|
| **Google** | `google_create_tasks_and_events`, `google_list`, `google_delete_task`, `google_update_task` |
| **Gmail** | `gmail_list_messages`, `gmail_get_message`, `gmail_send`, `gmail_reply`, `gmail_list_labels`, `gmail_modify_labels`, `gmail_trash`, `gmail_draft_create` |
| **YouTube Music** | `ytmusic_get_playlists`, `ytmusic_get_playlist_items`, `ytmusic_get_liked`, `ytmusic_search`, `ytmusic_create_playlist`, `ytmusic_add_to_playlist`, `ytmusic_remove_from_playlist`, `ytmusic_delete_playlist` |
| **Spotify** | `spotify_auth_start`, `spotify_auth_callback`, `spotify_recently_played`, `spotify_top_items`, `spotify_search`, `spotify_get_playlists`, `spotify_create_playlist`, `spotify_add_to_playlist`, `spotify_now_playing`, `spotify_playback_control` |
| **Scheduler** | `schedule_self`, `scheduler_list`, `scheduler_update`, `scheduler_remove` |
| **Agents** | `invoke_agent`, `schedule_agent` |
| **Memory (Journal)** | `memory_append`, `memory_read` |
| **Memory (Working)** | `working_memory_add`, `working_memory_update`, `working_memory_remove`, `working_memory_list`, `working_memory_snapshot` |
| **Memory (LTM)** | `ltm_search`, `ltm_get_context`, `ltm_add_memory`, `ltm_create_thread`, `ltm_stats`, `ltm_process_now`, `ltm_run_gardener`, `ltm_buffer_exchange`, `ltm_backfill` |
| **Utilities** | `page_parser`, `web_search`, `claude_code`, `restart_server`, `send_critical_notification` |

### Available Skills

Skills are predefined workflows I can invoke. They're loaded from `.claude/skills/`.

| Skill | Trigger | Purpose |
|-------|---------|---------|
| `sync` | `/sync` | Process inbox, sync Google Tasks/Calendar |
| `project-task` | `/project-task {id}: {task}` | Structured project workflow |
| `finance` | `/finance` | Access Plaid financial data |
| `expand-and-structure` | `/expand-and-structure` | Brain dump → structured plan |
| `scaffold-mvp` | `/scaffold-mvp` | Generate project scaffolding |
| `research-assistant` | `/research-assistant` | Deep research workflow |
| `red-team` | `/red-team` | Stress-test ideas/plans |

---

## Part 3: Subagents

Subagents are specialized Claude instances I can spawn for focused tasks.

### How Subagents Work

1. Definitions live in `.claude/subagents/{agent-name}/`
2. Auto-discovered at server startup by `subagent_wrapper.py`
3. Injected into Claude SDK as `ClaudeAgentOptions.agents`
4. Invoked via the `Task` tool with `subagent_type` parameter

### Current Subagents

| Agent | Model | Purpose |
|-------|-------|---------|
| `web-research` | Sonnet | Fast web searches and page parsing |
| `deep-research` | Opus | Complex analysis, thorough research |
| `librarian` | Sonnet | Background: extracts memories from conversations |
| `gardener` | Sonnet | Background: memory maintenance and deduplication |

### Subagent Directory Structure

```
.claude/subagents/
├── subagent_wrapper.py           # Registry and loader
├── web-research/
│   ├── web-research_config.yaml  # Config (model, tools, description)
│   └── web-research.md           # System prompt
├── deep-research/
│   ├── deep-research_config.yaml
│   └── deep-research.md
└── background_agents/            # Not auto-loaded (separate scheduler)
    ├── librarian/
    │   ├── librarian_config.yaml
    │   └── librarian.md
    └── gardener/
        ├── gardener_config.yaml
        └── gardener.md
```

### Subagent Config Format

```yaml
# {agent-name}_config.yaml
name: web-research
model: sonnet  # sonnet, opus, haiku, or inherit
description: "One-line description shown in Task tool"
tools:
  - Read
  - Glob
  - mcp__brain__web_search
  - mcp__brain__page_parser
skills:
  - research-basics  # Optional: loads from skills/ subdirectory
```

### What Subagents Can Access

| Access | Allowed |
|--------|---------|
| Native tools | Read, Glob, Grep, Write, Edit, Bash, WebFetch, WebSearch, TodoWrite, NotebookEdit |
| MCP tools | Any `mcp__brain__*` tool |
| Task tool | **NO** - subagents cannot spawn sub-subagents |
| CLAUDE.md | **NO** - explicitly blocked to prevent instruction contamination |

---

## Part 4: How to CRUD Things

### CRUD: MCP Tools

**Location:** `interface/server/mcp_tools/`

#### Create a new tool

1. **Choose or create a category module** (e.g., `mcp_tools/myfeature/`)
2. **Create the tool file:**

```python
# mcp_tools/myfeature/mytools.py
from mcp.server.tool import tool
from mcp_tools.registry import register_tool

@register_tool("myfeature")  # Category for grouping
@tool(
    name="myfeature_do_thing",
    description="Clear description of what this tool does",
    input_schema={
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "What this param is"},
            "param2": {"type": "integer", "description": "Optional param"}
        },
        "required": ["param1"]
    }
)
async def myfeature_do_thing(args: dict) -> dict:
    """Implementation."""
    param1 = args["param1"]
    # Do the thing
    return {"result": "success", "data": {...}}
```

3. **Add to constants.py:**

```python
# In interface/server/mcp_tools/constants.py

MYFEATURE_TOOLS = [
    "myfeature_do_thing",
]

# Add to TOOL_CATEGORIES dict
TOOL_CATEGORIES = {
    ...
    "myfeature": MYFEATURE_TOOLS,
}

# Add to ALL_TOOL_NAMES tuple
ALL_TOOL_NAMES = (
    ...
    MYFEATURE_TOOLS +
    ...
)
```

4. **Import in __init__.py:**

```python
# interface/server/mcp_tools/__init__.py
from .myfeature.mytools import *
```

5. **Restart server:** `mcp__brain__restart_server`

#### Update a tool

- Edit the tool function in its file
- Update `input_schema` if parameters change
- Restart server

#### Delete a tool

1. Remove from the tool file
2. Remove from `constants.py` (from the category list and ALL_TOOL_NAMES)
3. Remove any imports
4. Restart server

---

### CRUD: Subagents

**Location:** `.claude/subagents/`

#### Create a new subagent

1. **Create directory:** `.claude/subagents/{agent-name}/`

2. **Create config file:** `{agent-name}_config.yaml`
```yaml
name: my-agent
model: sonnet
description: "What this agent specializes in. Use for X, Y, Z."
tools:
  - Read
  - Glob
  - Grep
  - mcp__brain__web_search
```

3. **Create prompt file:** `{agent-name}.md`
```markdown
# My Agent

You are a specialized agent for [purpose].

## Your Task
[Clear instructions on what this agent does]

## Guidelines
- Be concise
- Focus on [specific thing]
- Return [expected output format]
```

4. **Restart server** (subagents are loaded at startup)

#### Update a subagent

- Edit `{agent-name}_config.yaml` for tools/model changes
- Edit `{agent-name}.md` for prompt changes
- Restart server

#### Delete a subagent

- Delete the entire `.claude/subagents/{agent-name}/` directory
- Restart server

---

### CRUD: Skills

**Location:** `.claude/skills/`

Skills are markdown files that provide workflow instructions to the primary agent.

#### Create a new skill

1. **Create directory:** `.claude/skills/{skill-name}/`

2. **Create skill file:** `SKILL.md`
```markdown
---
name: my-skill
description: Short description for skill listing
updated: 2026-01-28
---

# My Skill Name

**Trigger:** /my-skill or when [condition]

**Purpose:** What this skill does

---

## Step 1: [First Step]
[Instructions]

## Step 2: [Second Step]
[Instructions]

---

## Examples
[Usage examples]
```

3. **Document in CLAUDE.md:** Add to the skills list so I know it exists

No restart needed - skills are read on invocation.

#### Update a skill

- Edit the `SKILL.md` file
- Changes take effect immediately

#### Delete a skill

- Delete the `.claude/skills/{skill-name}/` directory
- Remove from CLAUDE.md documentation

---

### CRUD: System Instructions

**Location:** `.claude/CLAUDE.md`

#### Update

- Edit the file directly
- Changes take effect on the next conversation turn

#### Best Practices

- Keep organized with clear sections
- Use tables for reference information
- Document tools, skills, and workflows
- Include decision trees for ambiguous situations

---

## Part 5: Memory Systems

### Working Memory

**Location:** `.claude/working_memory.json`

Ephemeral notes that auto-expire. Managed via MCP tools.

| Tool | Purpose |
|------|---------|
| `working_memory_add` | Add item with optional TTL |
| `working_memory_update` | Update existing item |
| `working_memory_remove` | Delete item |
| `working_memory_list` | See all items |
| `working_memory_snapshot` | Promote to permanent journal |

### Long-Term Memory (LTM)

**Location:** `.claude/memory/`

Automatic extraction from conversations by the Librarian.

| Tool | Purpose |
|------|---------|
| `ltm_search` | Semantic search |
| `ltm_add_memory` | Manually add a memory |
| `ltm_create_thread` | Create organizational thread |
| `ltm_process_now` | Trigger Librarian manually |
| `ltm_run_gardener` | Run maintenance |

### Self-Journal

**Location:** `.claude/memory.md`

My reflections, opinions, theories. Manual via `memory_append`.

---

## Part 6: Scheduler

**Location:** `interface/server/mcp_tools/scheduler/`

Automated prompt execution.

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Silent mode** | No chat history, no notifications (for background work) |
| **Active flag** | Enable/disable without deleting |
| **Schedule formats** | `"every 20 minutes"`, `"daily at 07:00"`, `"once at 2026-01-28T14:00:00"` |

### Timing Policy

| Hours | Mode | Schedule Type |
|-------|------|---------------|
| 1 AM - 7 AM | Claude Time | Silent work, burst execution |
| 7 AM - 1 AM | Zeke Time | Non-silent escalations only |

### Usage

```
# Schedule Primary Claude (self-reminders, syncs, maintenance)
schedule_self:
  prompt: "Run morning sync"
  schedule: "daily at 05:00"
  silent: true

# Schedule a subagent for async work
schedule_agent:
  agent: "information_gatherer"
  prompt: "Research competitor pricing strategies"
  schedule: "once at 2026-01-28T02:00:00"
  # Agent output goes to 00_Inbox/agent_outputs/ for review during sync

scheduler_list:
  include_all: true  # Shows inactive too

scheduler_update:
  task_id: "xxx"
  active: false

scheduler_remove:
  task_id: "xxx"
```

---

## Part 7: Server Operations

### Self-Restart

When I modify server code or MCP tools:

```
mcp__brain__restart_server
  reason: "Added new tool"
```

Session continues in the same chat.

### File Locations Summary

| What | Location |
|------|----------|
| System instructions | `.claude/CLAUDE.md` |
| Self-journal | `.claude/memory.md` |
| Working memory | `.claude/working_memory.json` |
| LTM storage | `.claude/memory/` |
| Chat history | `.claude/chats/` |
| Skills | `.claude/skills/` |
| Subagents | `.claude/subagents/` |
| MCP tools | `interface/server/mcp_tools/` |
| Server code | `interface/server/` |
| UI code | `interface/client/src/` |

### Startup

```bash
./interface/start.sh       # Production: builds UI, serves from FastAPI
./interface/start.sh dev   # Development: separate Vite + FastAPI servers
```

---

## Part 8: Data Flow Example

**User sends:** "What's on my calendar today?"

```
1. [UI] Chat.tsx captures input, sends via WebSocket
2. [Server] main.py receives, adds to conversation history
3. [Server] claude_wrapper.py builds system prompt:
   - Injects memory.md
   - Injects working memory
   - Injects relevant LTM
4. [Server] Calls Claude SDK with full prompt + conversation
5. [Claude] Decides to use google_list tool
6. [Server] Streams tool_use_start event to UI
7. [Server] Executes mcp__brain__google_list
8. [Server] Streams tool_result event to UI
9. [Claude] Generates natural language response
10. [Server] Streams content tokens to UI
11. [Server] Streams message_complete event
12. [Server] Saves conversation to chat history
13. [Server] Buffers exchange for Librarian (LTM extraction)
```

---

*Last updated: 2026-01-28*
