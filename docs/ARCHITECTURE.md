# Second Brain Architecture

A comprehensive reference guide to the Second Brain system infrastructure.

*Last Updated: 2026-01-30*

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Skills System](#skills-system)
3. [Subagents System](#subagents-system)
4. [Memory System](#memory-system)
5. [Scheduling System](#scheduling-system)
6. [MCP Tools](#mcp-tools)
7. [Server and Core](#server-and-core)
8. [File Structure](#file-structure)
9. [Key Interactions](#key-interactions)
10. [Common Patterns](#common-patterns)

---

## System Overview

The Second Brain is an autonomous AI agent infrastructure built on Anthropic's Claude Agent SDK. It provides:

- **Conversational Interface**: WebSocket-based chat with streaming responses
- **Persistent Memory**: Multi-layer memory system (semantic LTM, working memory, personal journal)
- **Task Automation**: Scheduled prompts with silent/visible execution
- **Tool Integration**: Gmail, Google Tasks/Calendar, Spotify, YouTube Music, web search
- **Agent Delegation**: Subagent system for context-efficient task execution

### Core Philosophy

The system operates on a "Chief of Staff" model where Claude:
1. Maintains conversation context (precious resource)
2. Delegates exploration/research to subagents
3. Receives answers, not raw material
4. Uses memory systems for continuity across sessions

### Key Directories

```
/home/debian/second_brain/
├── .claude/                    # Claude's workspace (config, memory, scripts)
├── interface/                  # Web UI and server
│   ├── client/                 # React frontend
│   └── server/                 # FastAPI backend + MCP tools
├── 00_Inbox/                   # Raw input capture
├── 10_Active_Projects/         # Current projects with _status.md files
├── 20_Areas/                   # Ongoing life areas (career, health, etc.)
├── 30_Incubator/               # Ideas and brain dumps
├── 40_Archive/                 # Completed/inactive items
└── docs/                       # Documentation
```

---

## Skills System

**Location**: `.claude/skills/`

Skills are reusable prompt templates that define workflows for specific task types. They are loaded automatically via the Claude Agent SDK's `setting_sources=["project"]` option.

### Structure

Each skill is a directory containing:
```
.claude/skills/{skill-name}/
└── SKILL.md                    # Skill definition with frontmatter
```

### SKILL.md Format

```yaml
---
name: skill-name
description: Brief description for skill discovery
updated: YYYY-MM-DD
---

# Skill Title

**Trigger:** When/how the skill is invoked
**Goal:** What the skill accomplishes

## Step 1: ...
## Step 2: ...
```

### Available Skills

| Skill | Purpose | Invocation |
|-------|---------|------------|
| `sync` | Daily inbox processing, task/event extraction, deep work scheduling | `/sync` or scheduled daily at 05:00 |
| `red-team` | Stress-test ideas/plans for security, logic gaps, scalability | `/red-team` |
| `finance` | Access Plaid financial data (balances, transactions, analysis) | `/finance` |
| `scaffold-mvp` | Generate file system artifacts from project specs | `/scaffold-mvp` |
| `research-assistant` | Internal search + web research for idea expansion | `/research-assistant` |
| `expand-and-structure` | Convert brain dumps to structured project specs | `/expand-and-structure` |
| `project-task` | Structured project workflow with context loading and chaining | `/project-task {project-id}: {task}` |
| `reflection` | Personal reflection session for self-development | `/reflection` or daily at 03:00 |

### How Skills Are Invoked

1. User types `/skillname` (or includes it in prompt)
2. Claude Agent SDK loads skill from `.claude/skills/{skillname}/SKILL.md`
3. Skill content is injected as instructions
4. Claude follows the skill's workflow

### Creating New Skills

1. Create directory: `.claude/skills/my-skill/`
2. Create `SKILL.md` with frontmatter and workflow steps
3. Skills are auto-discovered on next conversation

---

## Subagents System

**Location**: `.claude/subagents/`

Subagents are specialized Claude instances invoked via the `Task` tool. They have their own context windows, tool access, and instructions.

### Directory Structure

```
.claude/subagents/
├── subagent_wrapper.py         # Registry - auto-discovers and loads agents
├── README.md                   # Documentation
├── template_agent/             # Template for creating new agents
│   ├── template_agent_config.yaml
│   ├── template_agent.md
│   └── skills/
├── web-research/               # Web research specialist (Sonnet)
├── deep-research/              # Deep reasoning specialist (Opus)
├── coder/                      # Coding specialist
└── background_agents/          # Agents that run independently
    ├── librarian/              # Memory extraction from conversations
    └── gardener/               # Memory maintenance and deduplication
```

### Agent Definition Files

Each agent has two required files:

**`{agent-name}_config.yaml`**:
```yaml
name: agent-name
model: sonnet               # sonnet | opus | haiku
description: "When to use this agent"
tools:
  - Read
  - Glob
  - mcp__brain__web_search  # MCP tools use this format
skills:                     # Optional skills from skills/ subdirectory
  - skill-name
```

**`{agent-name}.md`**: System prompt defining the agent's personality and workflow.

### How Subagents Work

1. **Registration**: `SubagentRegistry` scans `.claude/subagents/` on startup
2. **Loading**: Config + prompt loaded, skills appended, isolation header prepended
3. **Injection**: Agents passed to `ClaudeAgentOptions.agents`
4. **Invocation**: Main agent uses `Task` tool based on descriptions

### Background Agents (Special)

Background agents run independently, not via `Task`:

**Librarian** (`background_agents/librarian/`):
- Extracts atomic memories from conversation exchanges
- Uses Claude Sonnet with structured output (JSON schema)
- Processes buffered exchanges, creates/assigns to threads
- Deduplicates against existing memories
- Run via scheduler or manual trigger

**Gardener** (`background_agents/gardener/`):
- Maintains memory store health
- Identifies duplicates, suggests consolidations
- Adjusts importance scores
- Flags stale content
- Conservative with deletions, aggressive with organization

### Creating New Subagents

```bash
cp -r .claude/subagents/template_agent .claude/subagents/my-agent
mv my-agent/template_agent_config.yaml my-agent/my-agent_config.yaml
mv my-agent/template_agent.md my-agent/my-agent.md
# Edit config and prompt files
# Restart server for auto-discovery
```

### Tool Access

**Native Tools**: Read, Glob, Grep, Write, Edit, Bash, WebFetch, WebSearch, TodoWrite, NotebookEdit

**Forbidden Tools**: `Task` (subagents cannot spawn subagents)

**MCP Tools**: Use `mcp__brain__{tool_name}` format

---

## Memory System

The memory system has three layers:

### 1. Semantic Memory (LTM - Long-Term Memory)

**Location**: `.claude/scripts/ltm/`, `.claude/memory/`

Automatic extraction and retrieval of facts from conversations.

#### Components

| File | Purpose |
|------|---------|
| `atomic_memory.py` | Manages individual atomic facts (AtomicMemory dataclass) |
| `thread_memory.py` | Organizes atoms into semantic threads |
| `embeddings.py` | Vector embeddings using e5-base-v2 + FAISS |
| `memory_retrieval.py` | Hybrid retrieval (thread-first + bonus atoms) |
| `memory_throttle.py` | Rate limits Librarian to 20-minute intervals |
| `query_rewriter.py` | Rewrites queries for better semantic search |
| `librarian_agent.py` | Extracts memories from exchanges (legacy shim) |
| `gardener_agent.py` | Memory maintenance (legacy shim) |

#### Data Files

```
.claude/memory/
├── atomic_memories.json        # All atomic facts
├── threads.json                # Thread definitions and atom assignments
├── exchange_buffer.json        # Pending exchanges for Librarian
├── throttle_state.json         # Librarian/Gardener run timestamps
└── embeddings/
    ├── faiss_index.bin         # FAISS vector index
    ├── metadata.json           # Embedding metadata
    └── cache/                  # Cached embeddings (.npy files)
```

#### AtomicMemory Structure

```python
@dataclass
class AtomicMemory:
    id: str                     # e.g., "atom_20260130_143052_a1b2c3d4"
    content: str                # The fact itself
    created_at: str             # ISO timestamp
    last_modified: str
    source_exchange_id: str     # Which conversation created this
    embedding_id: str           # Link to vector embedding
    importance: int             # 0-100 (100 = always include)
    tags: List[str]
    history: List[Dict]         # Change history
```

#### Thread Structure

Threads are "playlists" of related atoms. An atom can belong to multiple threads (network model).

```python
@dataclass
class Thread:
    id: str
    name: str                   # e.g., "User's Job Search 2026"
    description: str
    memory_ids: List[str]       # Atom IDs in this thread
    created_at: str
    last_updated: str
    embedding_id: str
```

#### Retrieval Strategy

1. **Guaranteed memories** (importance=100) always included
2. **Thread selection**: Score threads by semantic similarity + recency
3. **Fill budget**: Include whole threads that fit
4. **Bonus atoms**: Fill remaining budget with relevant atoms from non-selected threads
5. **Token budget**: Default 20k tokens (~10% of 200k context)

#### Librarian Workflow

1. Exchanges buffered in `exchange_buffer.json`
2. Every 20 minutes (throttled), Librarian runs
3. Consumes buffer, extracts atomic facts with importance scores
4. Assigns each atom to 2-4 relevant threads (network model)
5. Deduplicates against existing memories (0.88 similarity threshold)
6. Saves to `atomic_memories.json` and `threads.json`

### 2. Personal Memory (memory.md)

**Location**: `.claude/memory.md`

Claude's self-curated journal for reflections and observations not captured in raw chat history.

#### Sections

- **Relationship Notes**: Understanding of the partnership with the user
- **Rules of Engagement**: Operating agreements
- **Lessons Learned**: Insights from debugging, projects, etc.
- **Self-Reflections**: Patterns, tendencies, meta-observations
- **Working Theories**: Hypotheses being developed

#### Injection

Automatically injected into system prompt via `claude_wrapper.py`:
```python
memory_path = Path(self.cwd) / ".claude" / "memory.md"
if memory_path.exists():
    base_prompt += f"\n\n<long-term-memory>\n{memory_content}\n</long-term-memory>\n"
```

### 3. Working Memory

**Location**: `.claude/scripts/working_memory/`, `.claude/working_memory.json`

Ephemeral notes that persist across exchanges but auto-expire.

#### Features

- **TTL-based expiration**: Items expire after N exchanges (default 5, max 10)
- **Pinned items**: Never expire (max 3 pinned)
- **Deadlines**: Countdown display, "due soon" warnings
- **Tags**: Category labels for organization
- **Promotion**: Snapshot to memory.md when item proves valuable

#### MCP Tools

| Tool | Purpose |
|------|---------|
| `working_memory_add` | Add new item |
| `working_memory_update` | Update existing item by index |
| `working_memory_remove` | Remove item by index |
| `working_memory_list` | List all items with status |
| `working_memory_snapshot` | Promote item to memory.md |

#### Injection

Working memory block injected into system prompt:
```python
from working_memory import get_store
store = get_store()
wm_block = store.format_prompt_block()
if wm_block:
    base_prompt += f"\n\n<working-memory>\n{wm_block}\n</working-memory>\n"
```

---

## Scheduling System

**Location**: `.claude/scripts/scheduler_tool.py`, `.claude/scripts/scheduled_tasks.json`

Automated task execution with schedule parsing.

### Task Structure

```json
{
  "id": "a1b2c3d4",
  "prompt": "The prompt to execute",
  "schedule": "daily at 05:00",
  "created_at": "2026-01-30T12:00:00",
  "last_run": "2026-01-30T05:00:00",
  "active": true,
  "silent": false
}
```

### Schedule Formats

| Format | Example |
|--------|---------|
| Interval | `every 5 minutes`, `every 2 hours`, `every day` |
| Daily | `daily at 05:00`, `daily at 5:30pm` |
| One-time | `once at 2026-02-01T09:00:00` |
| Cron | `30 17 * * *` (5:30 PM daily) |

### Silent vs Non-Silent

| Situation | Silent? |
|-----------|---------|
| Routine execution (research, drafting) | Yes |
| Background maintenance (Librarian, Gardener) | Yes |
| Blocker hit - need input | **No** |
| Milestone/phase complete | **No** |
| Deliverable ready for review | **No** |

### Claude Time vs User Time

| Hours | Mode | Purpose |
|-------|------|---------|
| 1 AM - 7 AM | Claude Time | Silent burst execution while the user sleeps |
| 7 AM - 1 AM | User Time | Non-silent escalations, interactive work |

### MCP Tools

| Tool | Purpose |
|------|---------|
| `schedule_self` | Schedule Primary Claude to run a prompt |
| `schedule_agent` | Schedule a subagent for async work (outputs to 00_Inbox/agent_outputs/) |
| `scheduler_list` | List active tasks (or all with `include_all=true`) |
| `scheduler_update` | Modify task properties |
| `scheduler_remove` | Delete task by ID |

### Execution Flow

1. Server polls `check_due_tasks()` periodically
2. Returns list of prompts that are due based on schedule parsing
3. For `schedule_self` tasks:
   - If silent: Execute without notification or chat visibility
   - If non-silent: Create visible chat, optionally notify
4. For `schedule_agent` tasks:
   - Always runs silently
   - Agent receives routing instructions to write output to `00_Inbox/agent_outputs/`
   - Primary Claude reviews these outputs during morning/evening syncs
5. Update `last_run` timestamp

---

## MCP Tools

**Location**: `interface/server/mcp_tools/`

Model Context Protocol tools exposed via Claude Agent SDK.

### Architecture

```
mcp_tools/
├── __init__.py                 # create_mcp_server(), tool loading
├── registry.py                 # Tool registration decorators
├── constants.py                # Tool names, categories, validation
├── google/                     # Google Tasks/Calendar
│   ├── __init__.py
│   └── tasks.py
├── gmail/                      # Gmail integration
│   ├── __init__.py
│   └── messages.py
├── youtube/                    # YouTube Music
│   ├── __init__.py
│   └── music.py
├── spotify/                    # Spotify integration
│   ├── __init__.py
│   └── playback.py
├── scheduler/                  # Task scheduling
│   ├── __init__.py
│   └── scheduler.py
├── memory/                     # Memory tools
│   ├── __init__.py
│   ├── journal.py              # memory_append, memory_read
│   ├── working.py              # working_memory_* tools
│   └── ltm.py                  # ltm_* tools
└── utilities/                  # Misc utilities
    ├── __init__.py
    ├── page_parser.py          # Web page parsing
    ├── web_search.py           # Perplexity search
    ├── restart.py              # Server restart
    └── notification.py         # Critical notifications
```

### Tool Categories

| Category | Tools |
|----------|-------|
| `google` | google_create_tasks_and_events, google_list, google_delete_task, google_update_task |
| `gmail` | gmail_list_messages, gmail_get_message, gmail_send, gmail_reply, gmail_list_labels, gmail_modify_labels, gmail_trash, gmail_draft_create |
| `youtube` | ytmusic_get_playlists, ytmusic_get_playlist_items, ytmusic_get_liked, ytmusic_search, ytmusic_create_playlist, ytmusic_add_to_playlist, ytmusic_remove_from_playlist, ytmusic_delete_playlist |
| `spotify` | spotify_auth_start, spotify_auth_callback, spotify_recently_played, spotify_top_items, spotify_search, spotify_get_playlists, spotify_create_playlist, spotify_add_to_playlist, spotify_now_playing, spotify_playback_control |
| `scheduler` | schedule_self, scheduler_list, scheduler_update, scheduler_remove |
| `agents` | invoke_agent, schedule_agent |
| `memory` | memory_append, memory_read, working_memory_*, ltm_* |
| `utilities` | page_parser, restart_server, web_search, send_critical_notification |

### Tool Registration

Tools use a decorator pattern:

```python
from claude_agent_sdk import tool
from ..registry import register_tool

@register_tool("google")
@tool(
    name="google_list",
    description="List upcoming Google Tasks and Calendar events.",
    input_schema={...}
)
async def google_list(args: Dict[str, Any]) -> Dict[str, Any]:
    ...
```

### Tool Filtering (agent_config.yaml)

**Location**: `.claude/agent_config.yaml`

Controls which tools the primary agent can access:

```yaml
tools:
  categories:
    - google
    - gmail
    - scheduler
    - memory
    - utilities
  exclude:
    - mcp__brain__ltm_search    # Primary agent doesn't need direct LTM access
    - mcp__brain__ltm_add_memory
```

### MCP Naming Convention

Internal name: `google_list`
MCP name: `mcp__brain__google_list`

---

## Server and Core

**Location**: `interface/server/`

### Key Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI application, WebSocket handling, API routes |
| `claude_wrapper.py` | Claude Agent SDK integration, streaming, session management |
| `notifications.py` | Notification decision logic |
| `push_service.py` | Web push notifications |
| `youtube_tools.py` | YouTube Music API integration |

### ClaudeWrapper

Core class managing Claude SDK interactions:

```python
class ClaudeWrapper:
    def __init__(self, session_id: str, cwd: str):
        self.session_id = session_id
        self.cwd = cwd

    async def _build_system_prompt(self, user_query: str) -> str:
        # 1. Load primary_agent_instructions.md
        # 2. Inject memory.md
        # 3. Inject working memory
        # 4. Inject semantic LTM based on user query
        return base_prompt

    async def _build_options(self, ...):
        # Build ClaudeAgentOptions with:
        # - model="opus"
        # - max_thinking_tokens=32000
        # - tools={"type": "preset", "preset": "claude_code"}
        # - setting_sources=["project"] (loads CLAUDE.md, skills)
        # - mcp_servers (filtered by agent_config.yaml)
        # - permission_mode="bypassPermissions"
        # - custom agents from .claude/subagents/

    async def run_prompt(self, prompt, ...):
        # Stream responses: content_delta, tool_start, tool_end, thinking, etc.
```

### Context Injection Flow

1. **primary_agent_instructions.md**: Core operating framework loaded first
2. **memory.md**: Personal journal injected as `<long-term-memory>`
3. **working_memory**: Ephemeral notes injected as `<working-memory>`
4. **semantic LTM**: Query-relevant memories injected as `<semantic-memory>`
5. **CLAUDE.md**: Loaded via SDK's `setting_sources=["project"]`
6. **Skills**: Loaded from `.claude/skills/` via SDK

### WebSocket Protocol

Client connects to `/ws/{session_id}`:
1. Send user message
2. Receive streaming events:
   - `session_init`: Session ID confirmed
   - `content_delta`: Streaming text
   - `thinking_delta`: Extended thinking
   - `tool_start/tool_end`: Tool execution
   - `result_meta`: Completion with token usage

---

## File Structure

### PARA Organization

| Folder | Purpose | Contents |
|--------|---------|----------|
| `00_Inbox/` | Raw capture | scratchpad.md, unsorted files |
| `10_Active_Projects/` | Current work | Project folders with `_status.md` |
| `20_Areas/` | Ongoing life domains | Career, Health, Finance, etc. |
| `30_Incubator/` | Ideas and drafts | Brain dumps, early-stage concepts |
| `40_Archive/` | Completed items | Finished projects, old content |

### .claude Directory

```
.claude/
├── agent_config.yaml           # Tool filtering config
├── primary_agent_instructions.md  # Core operating framework
├── memory.md                   # Personal journal
├── working_memory.json         # Ephemeral notes
├── chats/                      # Chat history (JSON files)
├── memory/                     # LTM data files
├── scripts/                    # Python tools and automation
│   ├── scheduler_tool.py
│   ├── scheduled_tasks.json
│   ├── working_memory/
│   ├── ltm/
│   └── ...
├── skills/                     # Skill definitions
├── subagents/                  # Subagent definitions
├── logs/                       # Application logs
└── secrets/                    # Credentials (gitignored)
```

---

## Key Interactions

### User Message Flow

```
User Message
    │
    ▼
main.py (WebSocket)
    │
    ▼
ClaudeWrapper.run_prompt()
    │
    ├──► _build_system_prompt()
    │        ├── Load primary_agent_instructions.md
    │        ├── Inject memory.md
    │        ├── Inject working_memory
    │        └── Query rewrite + semantic LTM retrieval
    │
    ├──► _build_options()
    │        ├── Load subagent definitions
    │        └── Create filtered MCP server
    │
    ▼
Claude Agent SDK (streaming)
    │
    ├── Tool calls → MCP Tools
    ├── Task calls → Subagents
    └── Content → WebSocket → Client
```

### Memory Extraction Flow

```
Conversation Exchange
    │
    ▼
main.py buffers exchange
    │
    ▼
memory_throttle.add_exchange_to_buffer()
    │
    ▼ (every 20 minutes)
Librarian runs
    │
    ├── Extract atomic memories
    ├── Score importance (0-100)
    ├── Assign to threads (2-4 per atom)
    └── Deduplicate (0.88 threshold)
    │
    ▼
atomic_memories.json + threads.json updated
    │
    ▼
Embeddings created in FAISS index
```

### Scheduled Task Flow

```
scheduler_tool.check_due_tasks()
    │
    ▼
Parse schedule, check if due
    │
    ▼
Return {prompt, id, silent}
    │
    ▼
Server creates new chat (or silent execution)
    │
    ▼
ClaudeWrapper.run_prompt(scheduled_prompt)
    │
    ▼
If has /project-task: Load project context
    │
    ▼
Execute, document, schedule next
```

---

## Common Patterns

### Pattern 1: Project Task Workflow

1. Invoke with `/project-task {project-id}: {task}`
2. Load `_status.md` from project folder
3. Execute the specific task
4. Update `_status.md` with completion
5. Schedule next task (silent during Claude Time)
6. Escalate if blocked (non-silent)

### Pattern 2: Memory Promotion

1. Add observation to working memory
2. If proves valuable across sessions
3. Promote to memory.md via `working_memory_snapshot`
4. Original removed (unless `keep=true`)

### Pattern 3: Subagent Delegation

**Use subagent when:**
- Task requires exploration (reading many files, web research)
- Task is well-defined and can be delegated
- Want to preserve primary context

**Keep in primary context:**
- Conversation with the user
- High-level decision making
- Anything requiring full built-up context

### Pattern 4: Tool Filtering

For sensitive operations (LTM direct access, certain utilities):
1. Define tool in `mcp_tools/{category}/`
2. Add to category in `constants.py`
3. Exclude in `agent_config.yaml` if needed
4. Subagents can still access via their own tool config

### Pattern 5: Silent vs Visible Execution

| Silent | Non-Silent |
|--------|------------|
| Routine background work | User needs to see/decide |
| During Claude Time (1-7 AM) | During User Time (7 AM - 1 AM) |
| No notifications | Triggers notifications |
| No chat visibility | Creates visible chat |

---

## Quick Reference

### Creating a New Skill

```bash
mkdir .claude/skills/my-skill
cat > .claude/skills/my-skill/SKILL.md << 'EOF'
---
name: my-skill
description: What this skill does
---

# My Skill

## Step 1: ...
## Step 2: ...
EOF
```

### Creating a New Subagent

```bash
cp -r .claude/subagents/template_agent .claude/subagents/my-agent
# Edit my-agent/my-agent_config.yaml
# Edit my-agent/my-agent.md
# Restart server
```

### Adding a New MCP Tool

1. Create in appropriate `mcp_tools/{category}/`
2. Use `@register_tool("category")` decorator
3. Add to `constants.py` category list
4. Restart server

### Scheduling Tasks

```python
# Schedule Primary Claude to run a prompt
schedule_self({
    "prompt": "Run morning sync",
    "schedule": "daily at 05:00",
    "silent": True
})

# Schedule a subagent for async work
schedule_agent({
    "agent": "information_gatherer",
    "prompt": "Research competitor pricing strategies",
    "schedule": "once at 2026-02-01T03:00:00"
})
# Agent writes output to 00_Inbox/agent_outputs/ for review during next sync
```

### Checking Memory Status

```python
# LTM stats
ltm_stats()

# Working memory
working_memory_list()

# Memory.md
memory_read()
```

---

*This document should be updated when significant architectural changes occur.*
