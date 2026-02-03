# Second Brain

An autonomous AI agent infrastructure built on Anthropic's Claude Agent SDK. Second Brain combines a conversational interface with persistent memory, scheduled automation, and tool integrations to create a personal AI assistant that maintains context across sessions.

## Features

- **Conversational Interface**: WebSocket-based chat with streaming responses
- **Persistent Memory**: Multi-layer memory system (semantic LTM, working memory, personal journal)
- **Task Automation**: Scheduled prompts with silent/visible execution
- **Tool Integration**: Gmail, Google Tasks/Calendar, Spotify, web search, and more
- **Agent Delegation**: Subagent system for context-efficient task execution
- **Skills System**: Reusable prompt templates for workflows like daily syncs, research, and project management

## Architecture

```
second_brain/
├── .claude/                    # Agent workspace (config, memory, scripts)
│   ├── agents/                 # Subagent definitions
│   ├── scripts/                # Python tools and LTM system
│   └── skills/                 # Skill templates
├── interface/
│   ├── client/                 # React + TypeScript frontend
│   └── server/                 # FastAPI backend + MCP tools
└── docs/                       # Architecture and reference docs
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed system documentation.

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- Anthropic API key

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/second-brain.git
   cd second-brain
   ```

2. Copy environment template and add your API keys:
   ```bash
   cp .env.example .env
   # Edit .env with your ANTHROPIC_API_KEY
   ```

3. Install dependencies:
   ```bash
   # Backend
   cd interface/server
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cd ../..

   # Frontend
   cd interface/client
   npm install
   cd ../..
   ```

4. Start the interface:
   ```bash
   ./interface/start.sh
   ```

5. Open http://localhost:5173 in your browser.

## Key Technologies

- **Backend**: FastAPI, Claude Agent SDK, FAISS (vector search)
- **Frontend**: React, TypeScript, Tailwind CSS
- **Memory**: SQLite for LTM, JSON for working memory
- **Integrations**: Google APIs, Spotify API, Perplexity API

## License

MIT
