# Additional Instructions

## Plan File Paths
When writing implementation plans (e.g., via plan mode or when outlining steps), always explicitly state the absolute file path to each plan file in your reply text, so the user can click on them to view in the editor.

## User Interaction via Forms
When you need to ask the user a clarifying question, use the MCP forms tools (`mcp__brain__forms_define` and `mcp__brain__forms_show`) instead of the built-in AskUserQuestion tool. Forms provide a richer UI experience for the user to respond.

## Key Codebase Patterns

- **Server**: FastAPI + WebSocket at `interface/server/main.py`
- **Agent runner**: `.claude/agents/runner.py` — all agents use SDK `query()`
- **Agent configs**: `.claude/agents/{name}/config.yaml` + `prompt.md`
- **MCP tools**: `interface/server/mcp_tools/` — registered via decorator
- **Frontend**: React + TypeScript + Vite at `interface/client/src/`
- **Scheduled tasks**: `.claude/scripts/scheduled_tasks.json`
- **Apps**: `05_App_Data/` — self-contained HTML loaded in srcdoc iframes

## Memory

You have a tiered memory system:

- **Unified memory** — Use `memory_create` to save memories with trigger phrases. Set `always_load=true` for important notes loaded every session, or `always_load=false` (default) for contextual memories retrieved when relevant. Use `memory_search` to find existing memories, `memory_update` to modify, and `memory_delete` to remove.
- **Cross-agent search** — Use `memory_search_agent` to search other agents' memories. They can search yours too (except files marked private).
- **Conversation history** — Use `search_conversation_history` to look up what was actually said in past conversations.

When you learn something worth remembering, use `memory_create` with good trigger phrases. Set `always_load=true` for critical facts, preferences, and rules.
