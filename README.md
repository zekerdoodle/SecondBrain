# Second Brain

A personal AI assistant system built on Claude Code, featuring a full-stack web interface, agent orchestration, and long-term memory.

## Overview

Second Brain is a Personal Knowledge Management (PKM) system enhanced with AI capabilities. It provides:

- **Web Interface**: React + FastAPI PWA for mobile/desktop
- **Multi-Agent System**: Specialized agents for different tasks (deep thinking, information gathering, code execution)
- **Long-Term Memory**: Embedding-based memory with atomic storage and retrieval
- **MCP Tools**: Modular tooling system for Gmail, Spotify, Google Tasks, scheduling, and more
- **Skills Framework**: Customizable skill prompts for specific tasks

## Architecture

```
second_brain/
├── interface/              # Full-stack web application
│   ├── client/            # React + Vite + TypeScript frontend
│   └── server/            # FastAPI backend with MCP tools
├── .claude/               # Agent system
│   ├── agents/            # Agent configurations and runners
│   ├── docs/              # System documentation
│   ├── scripts/           # Utility scripts and tools
│   └── skills/            # Skill definitions
```

## Key Features

### Agent System
- **Primary Agent**: Main conversation handler with memory access
- **Claude Code Agent**: Specialized for coding tasks
- **Deep Think Agent**: Extended reasoning for complex problems
- **Information Gatherer**: Research and data collection
- **Background Agents**: Gardener (knowledge organization) and Librarian (memory management)

### MCP Tools
Modular tool system including:
- Gmail integration (read/send emails)
- Google Tasks management
- Spotify playback control
- Web search capabilities
- Scheduled task execution
- Push notifications

### Memory System
- Atomic memory storage with thread-based organization
- Embedding-based semantic search using FAISS
- Memory retrieval with query rewriting
- Automatic memory consolidation

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- Claude API key

### Installation

1. Clone the repository:
```bash
git clone https://github.com/zekerdoodle/SecondBrain.git
cd SecondBrain
```

2. Create a `.env` file based on `.env.example`:
```bash
cp .env.example .env
# Edit .env with your API keys
```

3. Start the interface:
```bash
./interface/start.sh
```

## Configuration

### Environment Variables
See `.env.example` for required configuration:
- `ANTHROPIC_API_KEY`: Claude API access
- `GOOGLE_*`: Google API credentials for Gmail/Tasks
- `SPOTIFY_*`: Spotify API credentials
- Additional service-specific keys

### Agent Configuration
Each agent in `.claude/agents/` has:
- `config.yaml`: Agent settings and model configuration
- `prompt.md`: System prompt for the agent

### Skills
Skills in `.claude/skills/` provide specialized behavior patterns that can be invoked during conversations.

## Development

### Running the Server
```bash
cd interface/server
python main.py
```

### Running the Client
```bash
cd interface/client
npm install
npm run dev
```

## License

Private repository - contact owner for usage rights.
