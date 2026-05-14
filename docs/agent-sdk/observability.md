---
source: https://platform.claude.com/docs/en/agent-sdk/observability
title: Agent SDK overview
last_fetched: 2026-05-14T09:02:42.232223+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

Agent SDK

Agent SDK overview

[Getting started](/docs/en/overview)[Build with Claude Code](/docs/en/agents)[Administration](/docs/en/admin-setup)[Configuration](/docs/en/settings)[Reference](/docs/en/cli-reference)[Agent SDK](/docs/en/agent-sdk/overview)[What's New](/docs/en/whats-new)[Resources](/docs/en/legal-and-compliance)

##### Agent SDK

- [Overview](/docs/en/agent-sdk/overview)
- [Quickstart](/docs/en/agent-sdk/quickstart)

##### Core concepts

- [How the agent loop works](/docs/en/agent-sdk/agent-loop)
- [Use Claude Code features](/docs/en/agent-sdk/claude-code-features)
- [Work with sessions](/docs/en/agent-sdk/sessions)

##### Input and output

- [Streaming Input](/docs/en/agent-sdk/streaming-vs-single-mode)
- [Handle approvals and user input](/docs/en/agent-sdk/user-input)
- [Stream responses in real-time](/docs/en/agent-sdk/streaming-output)
- [Get structured output from agents](/docs/en/agent-sdk/structured-outputs)

##### Extend with tools

- [Give Claude custom tools](/docs/en/agent-sdk/custom-tools)
- [Connect to external tools with MCP](/docs/en/agent-sdk/mcp)
- [Scale to many tools with tool search](/docs/en/agent-sdk/tool-search)
- [Subagents in the SDK](/docs/en/agent-sdk/subagents)

##### Customize behavior

- [Modifying system prompts](/docs/en/agent-sdk/modifying-system-prompts)
- [Slash Commands in the SDK](/docs/en/agent-sdk/slash-commands)
- [Agent Skills in the SDK](/docs/en/agent-sdk/skills)
- [Plugins in the SDK](/docs/en/agent-sdk/plugins)

##### Control and observability

- [Configure permissions](/docs/en/agent-sdk/permissions)
- [Intercept and control agent behavior with hooks](/docs/en/agent-sdk/hooks)
- [Rewind file changes with checkpointing](/docs/en/agent-sdk/file-checkpointing)
- [Track cost and usage](/docs/en/agent-sdk/cost-tracking)
- [Observability with OpenTelemetry](/docs/en/agent-sdk/observability)
- [Todo Lists](/docs/en/agent-sdk/todo-tracking)

##### Deployment

- [Hosting the Agent SDK](/docs/en/agent-sdk/hosting)
- [Securely deploying AI agents](/docs/en/agent-sdk/secure-deployment)

##### SDK references

- [TypeScript SDK](/docs/en/agent-sdk/typescript)
- [TypeScript V2 (deprecated)](/docs/en/agent-sdk/typescript-v2-preview)
- [Python SDK](/docs/en/agent-sdk/python)
- [Migration Guide](/docs/en/agent-sdk/migration-guide)

On this page

> ## Documentation Index
>
> Fetch the complete documentation index at: <https://code.claude.com/docs/llms.txt>
>
> Use this file to discover all available pages before exploring further.

Starting June 15, 2026, Agent SDK and `claude -p` usage on subscription plans will draw from a new monthly Agent SDK credit, separate from your interactive usage limits. See [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) for details.

Build AI agents that autonomously read files, run commands, search the web, edit code, and more. The Agent SDK gives you the same tools, agent loop, and context management that power Claude Code, programmable in Python and TypeScript.

Python

TypeScript

```shiki
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
 async for message in query(
 prompt="Find and fix the bug in auth.py",
 options=ClaudeAgentOptions(allowed_tools=["Read", "Edit", "Bash"]),
 ):
 print(message) # Claude reads the file, finds the bug, edits it

asyncio.run(main())
```

The Agent SDK includes built-in tools for reading files, running commands, and editing code, so your agent can start working immediately without you implementing tool execution. Dive into the quickstart or explore real agents built with the SDK:

[## Quickstart

Build a bug-fixing agent in minutes](/docs/en/agent-sdk/quickstart)

[## Example agents

Email assistant, research agent, and more](https://github.com/anthropics/claude-agent-sdk-demos)

## [​](#get-started) Get started

1

Install the SDK

- TypeScript
- Python

```shiki
npm install @anthropic-ai/claude-agent-sdk
```

```shiki
pip install claude-agent-sdk
```

The TypeScript SDK bundles a native Claude Code binary for your platform as an optional dependency, so you don’t need to install Claude Code separately.

2

Set your API key

Get an API key from the [Console](https://platform.claude.com/), then set it as an environment variable:

```shiki
export ANTHROPIC_API_KEY=your-api-key
```

The SDK also supports authentication via third-party API providers:

- **Amazon Bedrock**: set `CLAUDE_CODE_USE_BEDROCK=1` environment variable and configure AWS credentials
- **Claude Platform on AWS**: set `CLAUDE_CODE_USE_ANTHROPIC_AWS=1` and `ANTHROPIC_AWS_WORKSPACE_ID`, then configure AWS credentials
- **Google Vertex AI**: set `CLAUDE_CODE_USE_VERTEX=1` environment variable and configure Google Cloud credentials
- **Microsoft Azure**: set `CLAUDE_CODE_USE_FOUNDRY=1` environment variable and configure Azure credentials

See the setup guides for [Bedrock](/docs/en/amazon-bedrock), [Claude Platform on AWS](/docs/en/claude-platform-on-aws), [Vertex AI](/docs/en/google-vertex-ai), or [Azure AI Foundry](/docs/en/microsoft-foundry) for details.

Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Please use the API key authentication methods described in this document instead.

3

Run your first agent

This example creates an agent that lists files in your current directory using built-in tools.

Python

TypeScript

```shiki
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
 async for message in query(
 prompt="What files are in this directory?",
 options=ClaudeAgentOptions(allowed_tools=["Bash", "Glob"]),
 ):
 if hasattr(message, "result"):
 print(message.result)

asyncio.run(main())
```

**Ready to build?** Follow the [Quickstart](/docs/en/agent-sdk/quickstart) to create an agent that finds and fixes bugs in minutes.

## [​](#capabilities) Capabilities

Everything that makes Claude Code powerful is available in the SDK:

- Built-in tools
- Hooks
- Subagents
- MCP
- Permissions
- Sessions

Your agent can read files, run commands, and search codebases out of the box. Key tools include:

| Tool | What it does |
| --- | --- |
| **Read** | Read any file in the working directory |
| **Write** | Create new files |
| **Edit** | Make precise edits to existing files |
| **Bash** | Run terminal commands, scripts, git operations |
| **Monitor** | Watch a background script and react to each output line as an event |
| **Glob** | Find files by pattern (`**/*.ts`, `src/**/*.py`) |
| **Grep** | Search file contents with regex |
| **WebSearch** | Search the web for current information |
| **WebFetch** | Fetch and parse web page content |
| **[AskUserQuestion](/docs/en/agent-sdk/user-input#handle-clarifying-questions)** | Ask the user clarifying questions with multiple choice options |

This example creates an agent that searches your codebase for TODO comments:

Python

TypeScript

```shiki
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
 async for message in query(
 prompt="Find all TODO comments and create a summary",
 options=ClaudeAgentOptions(allowed_tools=["Read", "Glob", "Grep"]),
 ):
 if hasattr(message, "result"):
 print(message.result)

asyncio.run(main())
```

Run custom code at key points in the agent lifecycle. SDK hooks use callback functions to validate, log, block, or transform agent behavior.**Available hooks:** `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, and more.This example logs all file changes to an audit file:

Python

TypeScript

```shiki
import asyncio
from datetime import datetime
from claude_agent_sdk import query, ClaudeAgentOptions, HookMatcher

async def log_file_change(input_data, tool_use_id, context):
 file_path = input_data.get("tool_input", {}).get("file_path", "unknown")
 with open("./audit.log", "a") as f:
 f.write(f"{datetime.now()}: modified {file_path}\n")
 return {}

async def main():
 async for message in query(
 prompt="Refactor utils.py to improve readability",
 options=ClaudeAgentOptions(
 permission_mode="acceptEdits",
 hooks={
 "PostToolUse": [
 HookMatcher(matcher="Edit|Write", hooks=[log_file_change])
 ]
 },
 ),
 ):
 if hasattr(message, "result"):
 print(message.result)

asyncio.run(main())
```

[Learn more about hooks →](/docs/en/agent-sdk/hooks)

Spawn specialized agents to handle focused subtasks. Your main agent delegates work, and subagents report back with results.Define custom agents with specialized instructions. Include `Agent` in `allowedTools` since subagents are invoked via the Agent tool:

Python

TypeScript

```shiki
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

async def main():
 async for message in query(
 prompt="Use the code-reviewer agent to review this codebase",
 options=ClaudeAgentOptions(
 allowed_tools=["Read", "Glob", "Grep", "Agent"],
 agents={
 "code-reviewer": AgentDefinition(
 description="Expert code reviewer for quality and security reviews.",
 prompt="Analyze code quality and suggest improvements.",
 tools=["Read", "Glob", "Grep"],
 )
 },
 ),
 ):
 if hasattr(message, "result"):
 print(message.result)

asyncio.run(main())
```

Messages from within a subagent’s context include a `parent_tool_use_id` field, letting you track which messages belong to which subagent execution.[Learn more about subagents →](/docs/en/agent-sdk/subagents)

Connect to external systems via the Model Context Protocol: databases, browsers, APIs, and [hundreds more](https://github.com/modelcontextprotocol/servers).This example connects the [Playwright MCP server](https://github.com/microsoft/playwright-mcp) to give your agent browser automation capabilities:

Python

TypeScript

```shiki
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
 async for message in query(
 prompt="Open example.com and describe what you see",
 options=ClaudeAgentOptions(
 mcp_servers={
 "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}
 }
 ),
 ):
 if hasattr(message, "result"):
 print(message.result)

asyncio.run(main())
```

[Learn more about MCP →](/docs/en/agent-sdk/mcp)

Control exactly which tools your agent can use. Allow safe operations, block dangerous ones, or require approval for sensitive actions.

For interactive approval prompts and the `AskUserQuestion` tool, see [Handle approvals and user input](/docs/en/agent-sdk/user-input).

This example creates a read-only agent that can analyze but not modify code. `allowed_tools` pre-approves `Read`, `Glob`, and `Grep`.

Python

TypeScript

```shiki
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
 async for message in query(
 prompt="Review this code for best practices",
 options=ClaudeAgentOptions(
 allowed_tools=["Read", "Glob", "Grep"],
 ),
 ):
 if hasattr(message, "result"):
 print(message.result)

asyncio.run(main())
```

[Learn more about permissions →](/docs/en/agent-sdk/permissions)

Maintain context across multiple exchanges. Claude remembers files read, analysis done, and conversation history. Resume sessions later, or fork them to explore different approaches.This example captures the session ID from the first query, then resumes to continue with full context:

Python

TypeScript

```shiki
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions, SystemMessage, ResultMessage

async def main():
 session_id = None

 # First query: capture the session ID
 async for message in query(
 prompt="Read the authentication module",
 options=ClaudeAgentOptions(allowed_tools=["Read", "Glob"]),
 ):
 if isinstance(message, SystemMessage) and message.subtype == "init":
 session_id = message.data["session_id"]

 # Resume with full context from the first query
 async for message in query(
 prompt="Now find all places that call it", # "it" = auth module
 options=ClaudeAgentOptions(resume=session_id),
 ):
 if isinstance(message, ResultMessage):
 print(message.result)

asyncio.run(main())
```

[Learn more about sessions →](/docs/en/agent-sdk/sessions)

### [​](#claude-code-features) Claude Code features

The SDK also supports Claude Code’s filesystem-based configuration. With default options the SDK loads these from `.claude/` in your working directory and `~/.claude/`. To restrict which sources load, set `setting_sources` (Python) or `settingSources` (TypeScript) in your options.

| Feature | Description | Location |
| --- | --- | --- |
| [Skills](/docs/en/agent-sdk/skills) | Specialized capabilities defined in Markdown | `.claude/skills/*/SKILL.md` |
| [Slash commands](/docs/en/agent-sdk/slash-commands) | Custom commands for common tasks | `.claude/commands/*.md` |
| [Memory](/docs/en/agent-sdk/modifying-system-prompts) | Project context and instructions | `CLAUDE.md` or `.claude/CLAUDE.md` |
| [Plugins](/docs/en/agent-sdk/plugins) | Extend with custom commands, agents, and MCP servers | Programmatic via `plugins` option |

## [​](#compare-the-agent-sdk-to-other-claude-tools) Compare the Agent SDK to other Claude tools

The Claude Platform offers multiple ways to build with Claude. Here’s how the Agent SDK fits in:

- Agent SDK vs Client SDK
- Agent SDK vs Claude Code CLI
- Agent SDK vs Managed Agents

The [Anthropic Client SDK](https://platform.claude.com/docs/en/api/client-sdks) gives you direct API access: you send prompts and implement tool execution yourself. The **Agent SDK** gives you Claude with built-in tool execution.With the Client SDK, you implement a tool loop. With the Agent SDK, Claude handles it:

Python

TypeScript

```shiki
# Client SDK: You implement the tool loop
response = client.messages.create(...)
while response.stop_reason == "tool_use":
 result = your_tool_executor(response.tool_use)
 response = client.messages.create(tool_result=result, **params)

# Agent SDK: Claude handles tools autonomously
async for message in query(prompt="Fix the bug in auth.py"):
 print(message)
```

Same capabilities, different interface:

| Use case | Best choice |
| --- | --- |
| Interactive development | CLI |
| CI/CD pipelines | SDK |
| Custom applications | SDK |
| One-off tasks | CLI |
| Production automation | SDK |

Many teams use both: CLI for daily development, SDK for production. Workflows translate directly between them.

[Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) is a hosted REST API: Anthropic runs the agent and the sandbox, and your application sends events and streams back results. The **Agent SDK** is a library that runs the agent loop inside your own process.

| | Agent SDK | Managed Agents |
| --- | --- | --- |
| **Runs in** | Your process, your infrastructure | Anthropic-managed infrastructure |
| **Interface** | Python or TypeScript library | REST API |
| **Agent works on** | Files on your infrastructure | A managed sandbox per session |
| **Session state** | JSONL on your filesystem | Anthropic-hosted event log |
| **Custom tools** | In-process Python or TypeScript functions | Claude triggers the tool; you execute and return results |
| **Best for** | Local prototyping, agents that work directly on your filesystem and services | Production agents without operating sandbox or session infrastructure, long-running and asynchronous sessions |

A common path is to prototype with the Agent SDK locally, then move to Managed Agents for production.

## [​](#changelog) Changelog

View the full changelog for SDK updates, bug fixes, and new features:

- **TypeScript SDK**: [view CHANGELOG.md](https://github.com/anthropics/claude-agent-sdk-typescript/blob/main/CHANGELOG.md)
- **Python SDK**: [view CHANGELOG.md](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md)

## [​](#reporting-bugs) Reporting bugs

If you encounter bugs or issues with the Agent SDK:

- **TypeScript SDK**: [report issues on GitHub](https://github.com/anthropics/claude-agent-sdk-typescript/issues)
- **Python SDK**: [report issues on GitHub](https://github.com/anthropics/claude-agent-sdk-python/issues)

## [​](#branding-guidelines) Branding guidelines

For partners integrating the Claude Agent SDK, use of Claude branding is optional. When referencing Claude in your product:
**Allowed:**

- “Claude Agent” (preferred for dropdown menus)
- “Claude” (when within a menu already labeled “Agents”)
- ” Powered by Claude” (if you have an existing agent name)

**Not permitted:**

- “Claude Code” or “Claude Code Agent”
- Claude Code-branded ASCII art or visual elements that mimic Claude Code

Your product should maintain its own branding and not appear to be Claude Code or any Anthropic product. For questions about branding compliance, contact the Anthropic [sales team](https://www.anthropic.com/contact-sales).

## [​](#license-and-terms) License and terms

Use of the Claude Agent SDK is governed by [Anthropic’s Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms), including when you use it to power products and services that you make available to your own customers and end users, except to the extent a specific component or dependency is covered by a different license as indicated in that component’s LICENSE file.

## [​](#next-steps) Next steps

[## Quickstart

Build an agent that finds and fixes bugs in minutes](/docs/en/agent-sdk/quickstart)

[## Example agents

Email assistant, research agent, and more](https://github.com/anthropics/claude-agent-sdk-demos)

[## TypeScript SDK

Full TypeScript API reference and examples](/docs/en/agent-sdk/typescript)

[## Python SDK

Full Python API reference and examples](/docs/en/agent-sdk/python)

Was this page helpful?

YesNo

[Quickstart](/docs/en/agent-sdk/quickstart)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.