---
source: https://platform.claude.com/docs/en/agent-sdk/claude-code-features
title: Use Claude Code features in the SDK
last_fetched: 2026-04-29T09:01:42.332201+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

Core concepts

Use Claude Code features in the SDK

[Getting started](/docs/en/overview)[Build with Claude Code](/docs/en/sub-agents)[Administration](/docs/en/admin-setup)[Configuration](/docs/en/settings)[Reference](/docs/en/cli-reference)[Agent SDK](/docs/en/agent-sdk/overview)[What's New](/docs/en/whats-new)[Resources](/docs/en/legal-and-compliance)

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
- [TypeScript V2 (preview)](/docs/en/agent-sdk/typescript-v2-preview)
- [Python SDK](/docs/en/agent-sdk/python)
- [Migration Guide](/docs/en/agent-sdk/migration-guide)

On this page

> ## Documentation Index
>
> Fetch the complete documentation index at: <https://code.claude.com/docs/llms.txt>
>
> Use this file to discover all available pages before exploring further.

The Agent SDK is built on the same foundation as Claude Code, which means your SDK agents have access to the same filesystem-based features: project instructions (`CLAUDE.md` and rules), skills, hooks, and more.
When you omit `settingSources`, `query()` reads the same filesystem settings as the Claude Code CLI: user, project, and local settings, CLAUDE.md files, and `.claude/` skills, agents, and commands. To run without these, pass `settingSources: []`, which limits the agent to what you configure programmatically. Managed policy settings and the global `~/.claude.json` config are read regardless of this option. See [What settingSources does not control](#what-settingsources-does-not-control).
For a conceptual overview of what each feature does and when to use it, see [Extend Claude Code](/docs/en/features-overview).

## [​](#control-filesystem-settings-with-settingsources) Control filesystem settings with settingSources

The setting sources option ([`setting_sources`](/docs/en/agent-sdk/python#claude-agent-options) in Python, [`settingSources`](/docs/en/agent-sdk/typescript#setting-source) in TypeScript) controls which filesystem-based settings the SDK loads. Pass an explicit list to opt in to specific sources, or pass an empty array to disable user, project, and local settings.
This example loads both user-level and project-level settings by setting `settingSources` to `["user", "project"]`:

Python

TypeScript

```shiki
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage

async for message in query(
 prompt="Help me refactor the auth module",
 options=ClaudeAgentOptions(
 # "user" loads from ~/.claude/, "project" loads from ./.claude/ in cwd.
 # Together they give the agent access to CLAUDE.md, skills, hooks, and
 # permissions from both locations.
 setting_sources=["user", "project"],
 allowed_tools=["Read", "Edit", "Bash"],
 ),
):
 if isinstance(message, AssistantMessage):
 for block in message.content:
 if hasattr(block, "text"):
 print(block.text)
 if isinstance(message, ResultMessage) and message.subtype == "success":
 print(f"\nResult: {message.result}")
```

Each source loads settings from a specific location, where `<cwd>` is the working directory you pass via the `cwd` option (or the process’s current directory if unset). For the full type definition, see [`SettingSource`](/docs/en/agent-sdk/typescript#setting-source) (TypeScript) or [`SettingSource`](/docs/en/agent-sdk/python#setting-source) (Python).

| Source | What it loads | Location |
| --- | --- | --- |
| `"project"` | Project CLAUDE.md, `.claude/rules/*.md`, project skills, project hooks, project `settings.json` | `<cwd>/.claude/` and each parent directory up to the filesystem root (stopping when a `.claude/` is found or no more parents exist) |
| `"user"` | User CLAUDE.md, `~/.claude/rules/*.md`, user skills, user settings | `~/.claude/` |
| `"local"` | CLAUDE.local.md (gitignored), `.claude/settings.local.json` | `<cwd>/` |

Omitting `settingSources` is equivalent to `["user", "project", "local"]`.
The `cwd` option determines where the SDK looks for project settings. If neither `cwd` nor any of its parent directories contains a `.claude/` folder, project-level features won’t load.

### [​](#what-settingsources-does-not-control) What settingSources does not control

`settingSources` covers user, project, and local settings. A few inputs are read regardless of its value:

| Input | Behavior | To disable |
| --- | --- | --- |
| Managed policy settings | Always loaded when present on the host | Remove the managed settings file |
| `~/.claude.json` global config | Always read | Relocate with `CLAUDE_CONFIG_DIR` in `env` |
| Auto memory at `~/.claude/projects/<project>/memory/` | Loaded by default into the system prompt | Set `autoMemoryEnabled: false` in settings, or `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env` |

Do not rely on default `query()` options for multi-tenant isolation. Because the inputs above are read regardless of `settingSources`, an SDK process can pick up host-level configuration and per-directory memory. For multi-tenant deployments, run each tenant in its own filesystem and set `settingSources: []` plus `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`. See [Secure deployment](/docs/en/agent-sdk/secure-deployment).

## [​](#project-instructions-claude-md-and-rules) Project instructions (CLAUDE.md and rules)

`CLAUDE.md` files and `.claude/rules/*.md` files give your agent persistent context about your project: coding conventions, build commands, architecture decisions, and instructions. When `settingSources` includes `"project"` (as in the example above), the SDK loads these files into context at session start. The agent then follows your project conventions without you repeating them in every prompt.

### [​](#claude-md-load-locations) CLAUDE.md load locations

| Level | Location | When loaded |
| --- | --- | --- |
| Project (root) | `<cwd>/CLAUDE.md` or `<cwd>/.claude/CLAUDE.md` | `settingSources` includes `"project"` |
| Project rules | `<cwd>/.claude/rules/*.md` | `settingSources` includes `"project"` |
| Project (parent dirs) | `CLAUDE.md` files in directories above `cwd` | `settingSources` includes `"project"`, loaded at session start |
| Project (child dirs) | `CLAUDE.md` files in subdirectories of `cwd` | `settingSources` includes `"project"`, loaded on demand when the agent reads a file in that subtree |
| Local (gitignored) | `<cwd>/CLAUDE.local.md` | `settingSources` includes `"local"` |
| User | `~/.claude/CLAUDE.md` | `settingSources` includes `"user"` |
| User rules | `~/.claude/rules/*.md` | `settingSources` includes `"user"` |

All levels are additive: if both project and user CLAUDE.md files exist, the agent sees both. There is no hard precedence rule between levels; if instructions conflict, the outcome depends on how Claude interprets them. Write non-conflicting rules, or state precedence explicitly in the more specific file (“These project instructions override any conflicting user-level defaults”).

You can also inject context directly via `systemPrompt` without using CLAUDE.md files. See [Modify system prompts](/docs/en/agent-sdk/modifying-system-prompts). Use CLAUDE.md when you want the same context shared between interactive Claude Code sessions and your SDK agents.

For how to structure and organize CLAUDE.md content, see [Manage Claude’s memory](/docs/en/memory).

## [​](#skills) Skills

Skills are markdown files that give your agent specialized knowledge and invocable workflows. Unlike `CLAUDE.md` (which loads every session), skills load on demand. The agent receives skill descriptions at startup and loads the full content when relevant.
Skills are discovered from the filesystem through `settingSources`. With default options, user and project skills load automatically. The `Skill` tool is enabled by default when you don’t specify `allowedTools`. If you are using an `allowedTools` allowlist, include `"Skill"` explicitly.

Python

TypeScript

```shiki
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

# Skills in .claude/skills/ are discovered automatically
# when settingSources includes "project"
async for message in query(
 prompt="Review this PR using our code review checklist",
 options=ClaudeAgentOptions(
 setting_sources=["user", "project"],
 allowed_tools=["Skill", "Read", "Grep", "Glob"],
 ),
):
 if isinstance(message, ResultMessage) and message.subtype == "success":
 print(message.result)
```

Skills must be created as filesystem artifacts (`.claude/skills/<name>/SKILL.md`). The SDK does not have a programmatic API for registering skills. See [Agent Skills in the SDK](/docs/en/agent-sdk/skills) for full details.

For more on creating and using skills, see [Agent Skills in the SDK](/docs/en/agent-sdk/skills).

## [​](#hooks) Hooks

The SDK supports two ways to define hooks, and they run side by side:

- **Filesystem hooks:** shell commands defined in `settings.json`, loaded when `settingSources` includes the relevant source. These are the same hooks you’d configure for [interactive Claude Code sessions](/docs/en/hooks-guide).
- **Programmatic hooks:** callback functions passed directly to `query()`. These run in your application process and can return structured decisions. See [Control execution with hooks](/docs/en/agent-sdk/hooks).

Both types execute during the same hook lifecycle. If you already have hooks in your project’s `.claude/settings.json` and you set `settingSources: ["project"]`, those hooks run automatically in the SDK with no extra configuration.
Hook callbacks receive the tool input and return a decision dict. Returning `{}` (an empty dict) means allow the tool to proceed. Returning `{"decision": "block", "reason": "..."}` prevents execution and the reason is sent to Claude as the tool result. See the [hooks guide](/docs/en/agent-sdk/hooks) for the full callback signature and return types.

Python

TypeScript

```shiki
from claude_agent_sdk import query, ClaudeAgentOptions, HookMatcher, ResultMessage

# PreToolUse hook callback. Positional args:
# input_data: HookInput dict with tool_name, tool_input, hook_event_name
# tool_use_id: str | None, the ID of the tool call being intercepted
# context: HookContext, carries session metadata
async def audit_bash(input_data, tool_use_id, context):
 command = input_data.get("tool_input", {}).get("command", "")
 if "rm -rf" in command:
 return {"decision": "block", "reason": "Destructive command blocked"}
 return {} # Empty dict: allow the tool to proceed

# Filesystem hooks from .claude/settings.json run automatically
# when settingSources loads them. You can also add programmatic hooks:
async for message in query(
 prompt="Refactor the auth module",
 options=ClaudeAgentOptions(
 setting_sources=["project"], # Loads hooks from .claude/settings.json
 hooks={
 "PreToolUse": [
 HookMatcher(matcher="Bash", hooks=[audit_bash]),
 ]
 },
 ),
):
 if isinstance(message, ResultMessage) and message.subtype == "success":
 print(message.result)
```

### [​](#when-to-use-which-hook-type) When to use which hook type

| Hook type | Best for |
| --- | --- |
| **Filesystem** (`settings.json`) | Sharing hooks between CLI and SDK sessions. Supports `"command"` (shell scripts), `"http"` (POST to an endpoint), `"mcp_tool"` (call a connected MCP server’s tool), `"prompt"` (LLM evaluates a prompt), and `"agent"` (spawns a verifier agent). These fire in the main agent and any subagents it spawns. |
| **Programmatic** (callbacks in `query()`) | Application-specific logic; returning structured decisions; in-process integration. Scoped to the main session only. |

The TypeScript SDK supports additional hook events beyond Python, including `SessionStart`, `SessionEnd`, `TeammateIdle`, and `TaskCompleted`. See the [hooks guide](/docs/en/agent-sdk/hooks) for the full event compatibility table.

For full details on programmatic hooks, see [Control execution with hooks](/docs/en/agent-sdk/hooks). For filesystem hook syntax, see [Hooks](/docs/en/hooks).

## [​](#choose-the-right-feature) Choose the right feature

The Agent SDK gives you access to several ways to extend your agent’s behavior. If you’re unsure which to use, this table maps common goals to the right approach.

| You want to… | Use | SDK surface |
| --- | --- | --- |
| Set project conventions your agent always follows | [CLAUDE.md](/docs/en/memory) | `settingSources: ["project"]` loads it automatically |
| Give the agent reference material it loads when relevant | [Skills](/docs/en/agent-sdk/skills) | `settingSources` + `allowedTools: ["Skill"]` |
| Run a reusable workflow (deploy, review, release) | [User-invocable skills](/docs/en/agent-sdk/skills) | `settingSources` + `allowedTools: ["Skill"]` |
| Delegate an isolated subtask to a fresh context (research, review) | [Subagents](/docs/en/agent-sdk/subagents) | `agents` parameter + `allowedTools: ["Agent"]` |
| Coordinate multiple Claude Code instances with shared task lists and direct inter-agent messaging | [Agent teams](/docs/en/agent-teams) | Not directly configured via SDK options. Agent teams are a CLI feature where one session acts as the team lead, coordinating work across independent teammates |
| Run deterministic logic on tool calls (audit, block, transform) | [Hooks](/docs/en/agent-sdk/hooks) | `hooks` parameter with callbacks, or shell scripts loaded via `settingSources` |
| Give Claude structured tool access to an external service | [MCP](/docs/en/agent-sdk/mcp) | `mcpServers` parameter |

**Subagents versus agent teams:** Subagents are ephemeral and isolated: fresh conversation, one task, summary returned to parent. Agent teams coordinate multiple independent Claude Code instances that share a task list and message each other directly. Agent teams are a CLI feature. See [What subagents inherit](/docs/en/agent-sdk/subagents#what-subagents-inherit) and the [agent teams comparison](/docs/en/agent-teams#compare-with-subagents) for details.

Every feature you enable adds to your agent’s context window. For per-feature costs and how these features layer together, see [Extend Claude Code](/docs/en/features-overview#understand-context-costs).

## [​](#related-resources) Related resources

- [Extend Claude Code](/docs/en/features-overview): Conceptual overview of all extension features, with comparison tables and context cost analysis
- [Skills in the SDK](/docs/en/agent-sdk/skills): Full guide to using skills programmatically
- [Subagents](/docs/en/agent-sdk/subagents): Define and invoke subagents for isolated subtasks
- [Hooks](/docs/en/agent-sdk/hooks): Intercept and control agent behavior at key execution points
- [Permissions](/docs/en/agent-sdk/permissions): Control tool access with modes, rules, and callbacks
- [System prompts](/docs/en/agent-sdk/modifying-system-prompts): Inject context without CLAUDE.md files

Was this page helpful?

YesNo

[How the agent loop works](/docs/en/agent-sdk/agent-loop)[Work with sessions](/docs/en/agent-sdk/sessions)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.