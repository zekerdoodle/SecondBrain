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

- **memory.md** — Always loaded. Your persistent notes across all sessions. Use `memory_append` to add to it. Keep entries concise.
- **Contextual memory** — Files in your `memory/` directory. Automatically loaded when their triggers match what's being discussed. Use `memory_save` to create new memories with retrieval triggers. Use `memory_search` to check what you already have before saving duplicates.
- **Cross-agent search** — Use `memory_search_agent` to search other agents' memories. They can search yours too (except files marked private).
- **Conversation history** — Use `search_conversation_history` to look up what was actually said in past conversations.

When you learn something worth remembering across sessions, save it with `memory_save`. Write triggers as phrases someone might search for — "User's opinion on React", not just "React".
