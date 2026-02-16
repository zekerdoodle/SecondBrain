# Additional Instructions

## Plan File Paths
When writing implementation plans (e.g., via plan mode or when outlining steps), always explicitly state the absolute file path to each plan file in your reply text, so the user can click on them to view in the editor.

## User Interaction via Forms
When you need to ask the user a clarifying question, use the MCP forms tools (`mcp__brain__forms_define` and `mcp__brain__forms_show`) instead of the built-in AskUserQuestion tool. Forms provide a richer UI experience for the user to respond.

## Key Codebase Patterns

- **Server**: FastAPI + WebSocket at `interface/server/main.py`
- **Agent runner**: `.claude/agents/runner.py` — SDK agents use `query()`, CLI agents use `--print`
- **Agent configs**: `.claude/agents/{name}/config.yaml` + `prompt.md`
- **MCP tools**: `interface/server/mcp_tools/` — registered via decorator
- **Frontend**: React + TypeScript + Vite at `interface/client/src/`
- **Scheduled tasks**: `.claude/scripts/scheduled_tasks.json`
- **LTM system**: `.claude/scripts/ltm/`, data in `.claude/memory/`
- **Apps**: `05_App_Data/` — self-contained HTML loaded in srcdoc iframes

## Project Work Detection
Before starting work, check: is this task for a project in `10_Active_Projects/`?
Signs: file paths in `10_Active_Projects/`, project name referenced, or dispatch includes `project:`.
If yes and your prompt doesn't already contain /project-task instructions → invoke `/project-task {project-name}` first.
This reads _status.md, scopes your work, and documents progress.
