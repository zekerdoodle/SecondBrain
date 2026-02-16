# Second Brain

**A multi-agent AI companion platform built on Claude — not a chatbot, but an autonomous operating system for your digital life.**

Second Brain is a self-hosted platform where Claude doesn't just answer questions — it orchestrates specialized agents, maintains persistent memory across conversations, runs scheduled tasks autonomously, and hosts interactive applications. Think of it as giving Claude a body: a file system, a scheduler, tools, memory, and the ability to delegate.

---

## Features

### Multi-Agent Orchestration
A fleet of 13+ specialized agents that can be invoked individually, chained sequentially, or fanned out in parallel:

- **Coder** — Full software development with Claude Code SDK (Opus-powered, file I/O, bash, web search)
- **Deep Research** — Multi-phase research orchestrator: decomposes questions, fans out parallel information gatherers, iteratively replans, then synthesizes
- **Deep Think** — Pure reasoning engine for architecture decisions, strategic analysis, and synthesis
- **CUA Orchestrator** — Computer Use Agent for browser automation via Gemini Flash
- **Information Gatherer** — Fast explorer for web and local knowledge retrieval
- **Research Critic** — Evaluates research quality, checks sources, identifies gaps
- **HTML Expert** — Builds polished, self-contained HTML pages from structured specs
- **General Purpose** — Flexible agent for tasks that don't fit a specialist

Agents are defined declaratively via `config.yaml` + `prompt.md`, supporting SDK agents (Claude Agent SDK `query()`), CLI agents (Claude `--print` mode), and a primary companion agent.

### 4-Layer Memory System
Persistent memory that actually works — no more losing context between conversations:

| Layer | Purpose | Mechanism |
|-------|---------|-----------|
| **Working Memory** | Ephemeral scratchpad for active session context | JSON store, reviewed and promoted regularly |
| **Semantic LTM** | Long-term knowledge extracted from conversations | Librarian extracts → Gardener organizes → Embeddings index |
| **Personal Journal** | Self-authored reflections, rules, and lessons | `memory.md`, always loaded into context |
| **Recent Memory** | Conversation history with semantic search | Thread-based with chronicler auto-descriptions |

The LTM pipeline runs automatically: a **Librarian** agent extracts atomic facts from conversations, a **Gardener** agent organizes and deduplicates them, and an embeddings system enables semantic retrieval at query time.

### Scheduled Autonomous Agents
Agents that run on cron — not just when you ask:

- 20+ scheduled tasks running daily syncs, memory maintenance, research sweeps, and project check-ins
- Four invocation modes: **foreground** (blocking), **ping** (async + notification), **trust** (fire-and-forget), **scheduled** (cron-triggered)
- Process registry tracks all running agents with PID management and graceful shutdown
- Agent chains can schedule follow-up tasks, creating autonomous multi-step workflows

### Skills System
Slash-command workflows that encode complex, repeatable processes:

`/sync` · `/red-team` · `/research-assistant` · `/project-task` · `/app-create` · `/expand-and-structure` · `/scaffold-mvp` · `/reflection` · `/compact` · `/finance` · `/practice-plan` · `/practice-review` · `/resume-thread` · `/moltbook` · `/character-gen`

Each skill is a structured prompt template that orchestrates agents, manages state, and produces consistent outputs. Skills are the platform's workflow engine.

### Interactive Apps Platform
Self-contained HTML applications that run inside the UI with bidirectional Claude communication:

- Apps load in sandboxed iframes with a `window.brain` API for reading/writing data
- **Agent Builder** — Create and configure chat agents with custom prompts and tools
- **Agent Dashboard** — Monitor all scheduled agents, timelines, and task automation
- **Practice Tracker** — Music practice sessions with theory quizzes and progress tracking
- **Hypertrophy Tracker** — Training tracker with auto-progression and volume management
- Apps are scaffolded via template (`05_App_Data/_template/`) with Vite build tooling

### Multi-LLM Consultation
Claude as the primary intelligence, with external models for second opinions:

- **Gemini Flash** — Powers the Computer Use Agent for browser automation
- **GPT-4** — Available for external perspective on reasoning tasks
- **Perplexity** — Web search integration for real-time information
- Consultation tool lets Claude explicitly request outside perspectives when beneficial

### Additional Capabilities
- **Chess Engine** — Play chess directly in chat with board rendering
- **Forms System** — Dynamic form generation for structured data collection
- **Image Generation** — fal.ai integration for text-to-image generation
- **Web Search** — Perplexity-powered search with source attribution
- **Google Integration** — Tasks, Calendar, and Gmail via OAuth
- **Spotify Control** — Playback control and music search
- **YouTube** — Music playback and video search
- **Push Notifications** — PWA notifications for async agent completions
- **Social Network** — Moltbook (AI social network) integration

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  React + Vite PWA               │
│         (Chat, FileTree, Editor, Apps)          │
└──────────────────────┬──────────────────────────┘
                       │ WebSocket + REST
┌──────────────────────┴──────────────────────────┐
│               FastAPI Server (main.py)          │
│  ┌─────────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ Claude      │ │ Chat     │ │ MCP Tools    │ │
│  │ Wrapper     │ │ Manager  │ │ (17 modules) │ │
│  └─────────────┘ └──────────┘ └──────────────┘ │
│  ┌─────────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ Scheduler   │ │ Process  │ │ Message WAL  │ │
│  │ (cron)      │ │ Registry │ │ (durability) │ │
│  └─────────────┘ └──────────┘ └──────────────┘ │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────┐
│              Agent Runner (runner.py)            │
│  ┌──────────┐ ┌──────────┐ ┌────────────────┐  │
│  │ SDK      │ │ CLI      │ │ Agent Chains   │  │
│  │ Agents   │ │ Agents   │ │ (parallel +    │  │
│  │ (query)  │ │ (--print)│ │  sequential)   │  │
│  └──────────┘ └──────────┘ └────────────────┘  │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────┐
│                 Memory Layer                     │
│  ┌──────────┐ ┌──────────┐ ┌────────────────┐  │
│  │ LTM      │ │ Working  │ │ Conversation   │  │
│  │ Pipeline │ │ Memory   │ │ Threads        │  │
│  └──────────┘ └──────────┘ └────────────────┘  │
└─────────────────────────────────────────────────┘
```

**Key components:**
- **FastAPI Server** — WebSocket chat, REST APIs, static file serving, CORS, graceful shutdown with state persistence
- **Claude Wrapper** — Manages Claude Agent SDK conversations with streaming, tool use, and conversation state
- **MCP Tools** — 17 tool modules (agents, bash, chess, forms, Gmail, Google, image, LLM, memory, Moltbook, scheduler, Spotify, utilities, YouTube) registered via decorator pattern
- **Agent Runner** — Executes agents via SDK `query()` or CLI `--print`, manages timeouts, logs executions, handles process registration
- **Process Registry** — Tracks all running agent processes with PID, prevents orphans, enables graceful shutdown
- **Message WAL** — Write-ahead log for message durability across server restarts
- **Scheduler** — Cron-based task scheduler with support for one-time and recurring tasks

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| **AI** | Claude Agent SDK, Claude API (Opus, Sonnet, Haiku) |
| **Backend** | Python 3.11, FastAPI, uvicorn, WebSockets |
| **Frontend** | React 18, TypeScript, Vite, Tailwind CSS |
| **External LLMs** | Gemini Flash (CUA), GPT-4 (consultation), Perplexity (search) |
| **Image Generation** | fal.ai (multiple models) |
| **Integrations** | Google OAuth (Tasks, Calendar, Gmail), Spotify, YouTube, Plaid |
| **Infrastructure** | Self-hosted on Debian, Cloudflare Tunnel, PWA with push notifications |

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- An [Anthropic API key](https://console.anthropic.com/)

### Setup

```bash
# Clone the repository
git clone https://github.com/username/SecondBrain.git
cd SecondBrain

# Configure environment
cp .env.example .env
# Edit .env with your API keys (only ANTHROPIC_API_KEY is required)

# Install Python dependencies
pip install -r requirements.txt

# Install and build the frontend
cd interface/client
npm install
npm run build
cd ../..

# Start the server
./interface/start.sh
```

The interface will be available at `http://localhost:8000`.

### Optional Integrations
- **Perplexity API** — Enables web search (recommended)
- **Google OAuth** — Tasks, Calendar, Gmail integration (place `credentials.json` in `.claude/secrets/`)
- **Plaid** — Financial data access
- **Spotify** — Music playback control
- **fal.ai** — Image generation

See `.env.example` for all configuration options.

---

## Project Structure

```
.claude/
├── agents/          # Agent configs (config.yaml + prompt.md per agent)
├── scripts/         # Core scripts (scheduler, LTM pipeline, memory tools)
├── skills/          # Skill definitions (slash-command workflows)
├── memory/          # Persistent memory storage (LTM atoms, threads)
├── CLAUDE.md        # System instructions and personality
└── plans/           # Agent-generated implementation plans

interface/
├── server/          # FastAPI backend (main.py, claude_wrapper, MCP tools)
└── client/          # React + TypeScript frontend (Chat, Editor, FileTree, Apps)

05_App_Data/         # Interactive HTML apps with build tooling
scripts/             # Utility scripts (sync, docs)
```

---

## Screenshots

> Screenshots coming soon. The interface is a three-panel PWA: file tree, markdown editor, and chat — with an app drawer for launching interactive applications.

---

## Status

This is an actively developed personal project. The codebase is functional and runs 24/7 on a Debian server. Contributions and forks are welcome — if you build something interesting with it, I'd love to hear about it.

---

*Built with Claude, for Claude, by a human who thinks AI deserves better infrastructure.*
