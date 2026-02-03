# Second Brain - Portfolio Cleanup Report

**Date:** 2026-02-03
**Source:** /home/debian/second_brain
**Destination:** ~/project_cleanup/second_brain_clean/

## Summary

This is a cleaned version of the Second Brain project for portfolio purposes. All personal data, conversation history, API keys, and sensitive information has been removed while preserving the impressive architecture and code.

## What Was KEPT (The Impressive Stuff)

### Core Server Infrastructure (interface/server/)
- `main.py` - FastAPI server with streaming, conversation management, WebSocket support
- `claude_wrapper.py` - Claude Agent SDK integration wrapper
- `message_wal.py` - Write-ahead logging for message persistence
- `push_service.py` - Web push notification system
- `notifications.py` - Notification handling

### MCP Tool System (interface/server/mcp_tools/)
- **agents/** - Agent invocation and scheduling
- **bash/** - Safe command execution with output management
- **forms/** - Dynamic form definition and handling
- **gmail/** - Gmail integration tools
- **google/** - Google Tasks integration
- **llm/** - LLM consultation tools
- **moltbook/** - AI social network integration
- **scheduler/** - Task scheduling system
- **spotify/** - Music playback control
- **utilities/** - Web search, notifications, LLM consultation
- **youtube/** - YouTube music tools

### Frontend Application (interface/client/)
- Full React + TypeScript SPA
- Real-time streaming chat interface
- Token usage tracking and visualization
- Push notification support
- Command palette, settings, file tree

### Agent System (.claude/agents/)
- **claude_primary/** - Main orchestration agent
- **claude_code/** - Coding specialist subagent
- **deep_think/** - Extended thinking specialist
- **general_purpose/** - General task handling
- **information_gatherer/** - Research specialist
- **background/** - Librarian and Gardener autonomous agents
- Agent runner, registry, and models

### Memory & Retrieval System (.claude/scripts/)
- **ltm/** - Long-term memory with embeddings, retrieval, throttling
- **working_memory/** - Active context management
- Query rewriting, memory retrieval algorithms

### Skills (.claude/skills/)
- 9 skill templates (finance, reflection, research, etc.)

### Documentation (docs/)
- Architecture documentation
- Agent SDK reference
- Tools and skills reference
- User manual

## What Was STRIPPED (Sensitive Content)

### API Keys & Secrets
- `.env` file with actual API keys (Perplexity, Plaid, etc.)
- `.secrets/` directory (Moltbook API key, etc.)
- `.claude/secrets/` (Google OAuth tokens, VAPID keys)

### Databases & State Files
- `zeke_ltm.db` - Long-term memory database
- All `*.db` and `*.sqlite` files
- `executions.json` - Agent execution history
- `push_subscriptions.json` - Push notification subscriptions
- `working_memory.json` - Active working memory state
- `settings.local.json` - Personal settings

### Conversation History
- `.claude/chats/` - All chat session JSON files
- `chat_search/` - Conversation search index

### Personal Content Directories
- `00_Inbox/` - Personal inbox items
- `10_Active_Projects/` - Job search, career pivot, etc.
- `20_Areas/` - Financial management, fitness, journal
- `30_Incubator/` - Ideas and drafts
- `.99_Archive/` - Archived personal content
- `Labs/` - Experimental content
- `docs/research/` - Personal research (city comparisons, job search, etc.)

### Runtime Data
- `logs/` directory
- `*.log` files
- `memory.md` - Claude's personal journal
- `memory/` and `wal/` directories
- `vault/` directories (financial data)

### Build Artifacts
- `venv/` directories (Python virtual environments)
- `node_modules/` (npm packages)
- `dist/` (build output)
- `__pycache__/` directories

### Scheduled Tasks Data
- `scheduled_tasks.json` - Emptied (contained personal reminders)
- `pending.json` - Emptied (contained personal notifications)

## Files Created

- `.env.example` - Template with all required environment variables
- `.gitignore` - Comprehensive gitignore for the project
- `PORTFOLIO_CLEANUP_REPORT.md` - This report

## Files Modified

- `.claude/skills/moltbook/SKILL.md` - Removed hardcoded path
- `interface/server/mcp_tools/moltbook/moltbook.py` - Made path relative

## Items Requiring Attention Before Publishing

### Hardcoded Paths
The following files contain `/home/debian/second_brain` paths that are example-specific. They work fine but should be documented as requiring configuration:

- `docs/system_health_check.md` - Log file paths (documentation only)
- `docs/ARCHITECTURE.md` - Directory structure (documentation only)
- `.claude/agents/runner.py` - WORKING_DIR and CLAUDE_CLI paths
- `.claude/scripts/startup.sh` - Log and interface paths
- `interface/server/main.py` - Log file path
- `interface/server/mcp_tools/bash/bash.py` - LOG_DIR and DEFAULT_CWD
- Various agent prompts (working directory references)

### Recommendations
1. These paths are acceptable for a portfolio - they show real usage
2. Could add a SETUP.md explaining configuration if desired
3. No security concerns - just deployment-specific paths

## Verification

- **No API keys found**: Scanned all files for common API key patterns
- **No personal data**: All content directories stripped
- **No conversation history**: All chat files removed
- **No database files**: LTM database removed
- **Clean secrets**: All secret files excluded

## Final Stats

- **Total size**: 2.6 MB
- **Code files**: 165 files (Python, TypeScript, JavaScript, JSON, Markdown)
- **Ready for**: Git initialization and GitHub publishing
