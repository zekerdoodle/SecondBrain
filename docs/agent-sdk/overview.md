# Claude Agent SDK - Overview

## What is the Claude Agent SDK?

The Claude Agent SDK (formerly Claude Code SDK) enables you to build AI agents with Claude Code's capabilities. It provides the same tools, agent loop, and context management that power Claude Code, programmable in Python and TypeScript.

The SDK allows you to create autonomous agents that can:
- Read and edit files
- Run bash commands
- Search codebases
- Execute complex workflows
- Connect to external services via MCP
- Manage context across multiple sessions

## Core Architecture

### Agent Loop

The SDK implements a continuous feedback loop:
1. **Gather Context** - Agent searches files, reads documentation, queries databases
2. **Take Action** - Agent executes tools, writes code, modifies files
3. **Verify Work** - Agent checks results, validates output, iterates if needed
4. **Repeat** - Process continues until task completion

### Key Design Principle

The fundamental design principle is **giving Claude a computer**. By providing access to tools that developers use daily (bash, file system, etc.), Claude can work like human programmers do - reading files, writing code, running tests, and debugging iteratively.

## Core Concepts

### 1. Agents

An agent is an autonomous Claude instance that:
- Maintains its own context window (200k tokens)
- Uses tools to gather information and take actions
- Makes decisions about which tools to use based on the task
- Operates independently or as part of a hierarchy

**Main Agent**: The primary agent you interact with directly.

**Subagents**: Specialized agents spawned by the main agent or other subagents to handle focused subtasks with isolated context.

### 2. Tools

Tools are the capabilities your agent can use. The SDK provides built-in tools:

**Core Tools**:
- `Read` - Read file contents
- `Write` - Create new files
- `Edit` - Modify existing files
- `Glob` - Find files by pattern
- `Grep` - Search file contents
- `Bash` - Execute shell commands

**Advanced Tools**:
- `Task` - Spawn subagents for parallel or isolated work
- `WebSearch` - Search the internet
- `PageParser` - Extract content from web pages
- `AskUserQuestion` - Request clarification from users

**MCP Tools**: Connect to external services via Model Context Protocol (databases, APIs, etc.)

### 3. Sessions

A session represents a continuous conversation with context. Sessions enable:
- **Context Preservation**: Full conversation history maintained
- **Resumption**: Continue from where you left off across restarts
- **Forking**: Branch from a point to explore alternatives
- **Compaction**: Automatic summarization when approaching context limits

### 4. Context Management

The SDK provides sophisticated context management:

**Context Window**: 200k tokens per agent instance

**Compaction**: Automatic summarization of older messages when context fills up, preserving critical information

**Isolation**: Subagents have separate context windows, preventing pollution of main conversation

**File System as Context**: Project structure and files serve as persistent context across sessions

### 5. Permissions

Control what agents can do through permission modes:

**Permission Modes**:
- `default` - Prompts for approval on all tool uses
- `acceptEdits` - Auto-approves file operations, prompts for others
- `dontAsk` - Auto-approves read-only operations
- `bypassPermissions` - Auto-approves all operations (use with caution)
- `plan` - Prevents tool execution, only generates plans

**Permission Evaluation Order**:
1. Hooks (can allow, deny, or continue)
2. Permission rules (declarative allow/deny in settings.json)
3. Permission mode
4. `canUseTool` callback (for runtime approval)

## Installation

### Prerequisites
- Python 3.10+ or Node.js 18+
- Claude Code CLI
- Anthropic API key

### Install Claude Code

**macOS/Linux (Homebrew)**:
```bash
brew install anthropics/tap/claude-code
```

**Windows (WinGet)**:
```bash
winget install Anthropic.ClaudeCode
```

**Authenticate**:
```bash
claude
# Follow prompts to authenticate
```

### Install SDK

**Python**:
```bash
pip install claude-agent-sdk
```

**TypeScript**:
```bash
npm install @anthropic-ai/claude-agent-sdk
```

### Set API Key

Create `.env` file:
```
ANTHROPIC_API_KEY=your-api-key
```

Or use authenticated Claude Code (recommended).

## Quick Start Example

**Python**:
```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
    async for message in query(
        prompt="Find and fix the bug in auth.py",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Bash"],
            permission_mode="acceptEdits"
        )
    ):
        if hasattr(message, "result"):
            print(message.result)

asyncio.run(main())
```

**TypeScript**:
```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Find and fix the bug in auth.py",
  options: {
    allowedTools: ["Read", "Edit", "Bash"],
    permissionMode: "acceptEdits"
  }
})) {
  if ('result' in message) {
    console.log(message.result);
  }
}
```

## SDK vs Other Claude Tools

| Feature | Agent SDK | Claude API | Claude Code CLI | Claude.ai |
|---------|-----------|------------|-----------------|-----------|
| **Autonomous tool use** | ✓ | Manual | ✓ | ✓ |
| **Programmatic control** | ✓ | ✓ | - | - |
| **File system access** | ✓ | - | ✓ | - |
| **Subagents** | ✓ | - | ✓ | - |
| **Session resumption** | ✓ | - | ✓ | ✓ |
| **Context management** | ✓ Auto | Manual | ✓ Auto | ✓ Auto |
| **MCP integration** | ✓ | - | ✓ | - |
| **Use case** | Build agents | Build apps | Terminal coding | Interactive use |

## Common Use Cases

### Development Agents
- Automated bug fixing
- Code review and refactoring
- Test generation and execution
- Documentation generation

### Research Agents
- Deep research across large document sets
- Multi-source information synthesis
- Competitive analysis

### Data Agents
- Database queries and analysis
- ETL pipeline creation
- Report generation

### Automation Agents
- Email processing and triage
- Customer support ticket handling
- Invoice/receipt processing
- Scheduled task execution

### Content Agents
- Translation workflows
- Content moderation
- Video/image processing
- Documentation maintenance

## Architecture Best Practices

### Orchestrator Pattern

Use a main orchestrator agent that coordinates specialized subagents:

```
Main Agent (Orchestrator)
├── Research Agent (read-only, gathers context)
├── Implementation Agent (writes code)
├── Testing Agent (runs tests, validates)
└── Review Agent (checks quality, security)
```

**Benefits**:
- Clear separation of concerns
- Parallel execution where possible
- Isolated contexts prevent pollution
- Specialized expertise per domain

### Context Engineering

Use the file system as persistent context:

**Progress Files**: `claude-progress.txt` tracks work completed
**Feature Lists**: JSON files define requirements and status
**Git History**: Commits document changes and rationale
**Init Scripts**: `init.sh` sets up development environment

### Incremental Progress

Structure tasks for incremental completion:
1. Initializer agent sets up environment
2. Coding agents work on one feature at a time
3. Each session leaves environment in clean, committable state
4. Next session picks up from clear checkpoint

## Message Types

The SDK streams different message types as work progresses:

**System Messages**:
- `init` - Session started, includes session_id and available tools
- `compact_boundary` - Context compaction occurred

**Assistant Messages**:
- Contains Claude's reasoning (text blocks)
- Contains tool calls (tool_use blocks)

**Tool Result Messages**:
- Results from tool executions
- Errors if tool failed

**Result Messages**:
- `success` - Task completed successfully
- `error_during_execution` - Error occurred
- `stopped` - Stopped by hooks or rules
- `max_turns_reached` - Hit turn limit

## Configuration Options

Key options when calling `query()`:

**Core Options**:
- `allowed_tools` - List of tools agent can use
- `disallowed_tools` - Tools to explicitly block
- `permission_mode` - How to handle tool permissions
- `model` - Which Claude model to use (sonnet, opus, haiku)
- `system_prompt` - Custom instructions for the agent

**Context Options**:
- `resume` - Session ID to continue previous session
- `fork_session` - Create new branch from resumed session
- `max_turns` - Limit number of agent turns

**Advanced Options**:
- `agents` - Subagent definitions
- `mcp_servers` - External tool integrations
- `hooks` - Custom code at lifecycle points
- `can_use_tool` - Runtime approval callback

## Error Handling

Agents can fail in several ways:

**Tool Execution Errors**: Tool fails to execute (file not found, command error)
- Agent receives error message and can retry or adjust approach

**Permission Denied**: Tool use blocked by permissions
- Agent must ask for different approach or request permission

**Context Overflow**: Approaching token limit
- Automatic compaction occurs
- Can configure compaction threshold

**Max Turns Reached**: Hit turn limit
- Set `max_turns` to prevent infinite loops
- Agent stops with result message

**Session Errors**: Resume failed, fork failed
- Check session_id is valid
- Ensure session hasn't expired (based on cleanup settings)

## Security Considerations

### Sandboxing

**Production deployments should run in sandboxed containers**:
- Process isolation
- Resource limits
- Network controls
- Ephemeral file systems

**Sandbox Providers**:
- Modal Sandbox
- Cloudflare Sandboxes
- E2B
- Fly Machines
- Docker (self-hosted)
- gVisor (self-hosted)

### Permission Hardening

1. **Use least privilege**: Only grant necessary tools
2. **Avoid bypassPermissions**: Especially with subagents
3. **Validate tool inputs**: Use hooks to check parameters
4. **Limit tool access**: Restrict file paths, commands
5. **Monitor tool usage**: Log all tool executions

### Credential Management

- Store API keys in environment variables
- Use MCP server authentication properly
- Don't commit credentials to git
- Rotate keys regularly

## Performance Optimization

### Model Selection

Choose model based on task complexity:
- **Haiku**: Fast, low-cost, simple tasks (file search, formatting)
- **Sonnet**: Balanced, most general tasks (coding, analysis)
- **Opus**: Complex reasoning, critical tasks (architecture, security)

### Context Efficiency

- Use subagents to isolate verbose operations
- Compact proactively with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`
- Structure files for easy navigation
- Use `Grep` instead of reading entire files

### Parallel Execution

- Run independent subagents concurrently (up to 10 parallel)
- Use background tasks for long-running operations
- Batch similar operations together

## Monitoring and Observability

### Logging

Stream messages to capture:
- Tool calls and parameters
- Tool results and errors
- Agent reasoning
- Session events (compaction, forking)

### Metrics

Track important metrics:
- Token usage (input/output)
- Tool execution count by type
- Session duration
- Success/failure rates
- Cost per session

### Debugging

Use message stream to debug:
1. Check `init` message for available tools
2. Track `tool_use` blocks for agent decisions
3. Examine tool results for errors
4. Review `compact_boundary` for context issues

## Next Steps

- **Task Tool & Subagents**: Learn how to use subagents for parallel and specialized work
- **Session Management**: Deep dive into session resumption and forking
- **Best Practices**: Patterns for building reliable agents
- **MCP Integration**: Connect to external services
- **Hooks**: Add custom logic at key points
- **Deployment**: Host agents in production

## Resources

**Official Documentation**: https://platform.claude.com/docs/en/agent-sdk/overview

**GitHub Repositories**:
- TypeScript SDK: https://github.com/anthropics/claude-agent-sdk-typescript
- Python SDK: https://github.com/anthropics/claude-agent-sdk-python
- Demo Applications: https://github.com/anthropics/claude-agent-sdk-demos

**Community Resources**:
- Anthropic Discord
- Claude Code Documentation: https://code.claude.com/docs
- Engineering Blog: https://www.anthropic.com/engineering

**Tutorials**:
- Full Workshop (YouTube): https://www.youtube.com/watch?v=TqC1qOfiVcQ
- Building Agents Blog Post: https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk
- Long-Running Agents: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
