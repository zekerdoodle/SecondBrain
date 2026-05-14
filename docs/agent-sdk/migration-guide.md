---
source: https://platform.claude.com/docs/en/agent-sdk/migration-guide
title: Migrate to Claude Agent SDK
last_fetched: 2026-05-13T13:03:07.029993+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

SDK references

Migrate to Claude Agent SDK

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

## [​](#overview) Overview

The Claude Code SDK has been renamed to the **Claude Agent SDK** and its documentation has been reorganized. This change reflects the SDK’s broader capabilities for building AI agents beyond just coding tasks.

## [​](#what’s-changed) What’s Changed

| Aspect | Old | New |
| --- | --- | --- |
| **Package Name (TS/JS)** | `@anthropic-ai/claude-code` | `@anthropic-ai/claude-agent-sdk` |
| **Python Package** | `claude-code-sdk` | `claude-agent-sdk` |
| **Documentation Location** | Claude Code docs | API Guide → Agent SDK section |

**Documentation Changes:** The Agent SDK documentation has moved from the Claude Code docs to the API Guide under a dedicated [Agent SDK](/docs/en/agent-sdk/overview) section. The Claude Code docs now focus on the CLI tool and automation features.

## [​](#migration-steps) Migration Steps

### [​](#for-typescript/javascript-projects) For TypeScript/JavaScript Projects

**1. Uninstall the old package:**

```shiki
npm uninstall @anthropic-ai/claude-code
```

**2. Install the new package:**

```shiki
npm install @anthropic-ai/claude-agent-sdk
```

**3. Update your imports:**
Change all imports from `@anthropic-ai/claude-code` to `@anthropic-ai/claude-agent-sdk`:

```shiki
// Before
import { query, tool, createSdkMcpServer } from "@anthropic-ai/claude-code";

// After
import { query, tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";
```

**4. Update package.json dependencies:**
If you have the package listed in your `package.json`, update it:
Before:

```shiki
{
 "dependencies": {
 "@anthropic-ai/claude-code": "^0.0.42"
 }
}
```

After:

```shiki
{
 "dependencies": {
 "@anthropic-ai/claude-agent-sdk": "^0.2.0"
 }
}
```

That’s it! No other code changes are required.

### [​](#for-python-projects) For Python Projects

**1. Uninstall the old package:**

```shiki
pip uninstall claude-code-sdk
```

**2. Install the new package:**

```shiki
pip install claude-agent-sdk
```

**3. Update your imports:**
Change all imports from `claude_code_sdk` to `claude_agent_sdk`:

```shiki
# Before
from claude_code_sdk import query, ClaudeCodeOptions

# After
from claude_agent_sdk import query, ClaudeAgentOptions
```

**4. Update type names:**
Change `ClaudeCodeOptions` to `ClaudeAgentOptions`:

```shiki
# Before
from claude_code_sdk import query, ClaudeCodeOptions

options = ClaudeCodeOptions(model="claude-opus-4-7")

# After
from claude_agent_sdk import query, ClaudeAgentOptions

options = ClaudeAgentOptions(model="claude-opus-4-7")
```

**5. Review [breaking changes](#breaking-changes)**
Make any code changes needed to complete the migration.

## [​](#breaking-changes) Breaking changes

To improve isolation and explicit configuration, Claude Agent SDK v0.1.0 introduces breaking changes for users migrating from Claude Code SDK. Review this section carefully before migrating.

### [​](#python-claudecodeoptions-renamed-to-claudeagentoptions) Python: ClaudeCodeOptions renamed to ClaudeAgentOptions

**What changed:** The Python SDK type `ClaudeCodeOptions` has been renamed to `ClaudeAgentOptions`.
**Migration:**

```shiki
# BEFORE (claude-code-sdk)
from claude_code_sdk import query, ClaudeCodeOptions

options = ClaudeCodeOptions(model="claude-opus-4-7", permission_mode="acceptEdits")

# AFTER (claude-agent-sdk)
from claude_agent_sdk import query, ClaudeAgentOptions

options = ClaudeAgentOptions(model="claude-opus-4-7", permission_mode="acceptEdits")
```

**Why this changed:** The type name now matches the “Claude Agent SDK” branding and provides consistency across the SDK’s naming conventions.

### [​](#system-prompt-no-longer-default) System prompt no longer default

**What changed:** The SDK no longer uses Claude Code’s system prompt by default.
**Migration:**

TypeScript

Python

```shiki
// BEFORE (v0.0.x) - Used Claude Code's system prompt by default
const result = query({ prompt: "Hello" });

// AFTER (v0.1.0) - Uses minimal system prompt by default
// To get the old behavior, explicitly request Claude Code's preset:
const result = query({
 prompt: "Hello",
 options: {
 systemPrompt: { type: "preset", preset: "claude_code" }
 }
});

// Or use a custom system prompt:
const result = query({
 prompt: "Hello",
 options: {
 systemPrompt: "You are a helpful coding assistant"
 }
});
```

**Why this changed:** Provides better control and isolation for SDK applications. You can now build agents with custom behavior without inheriting Claude Code’s CLI-focused instructions.

### [​](#settings-sources-default) Settings sources default

This default was briefly changed in v0.1.0 and then reverted, so no migration action is needed.
**Current behavior:** Omitting `settingSources` on `query()` loads user, project, and local filesystem settings, matching the CLI. This includes `~/.claude/settings.json`, `.claude/settings.json`, `.claude/settings.local.json`, CLAUDE.md files, and custom commands.
To run isolated from filesystem settings, pass an empty array:

TypeScript

Python

```shiki
const result = query({
 prompt: "Hello",
 options: {
 settingSources: [] // No filesystem settings loaded
 }
});

// Or load only specific sources:
const result = query({
 prompt: "Hello",
 options: {
 settingSources: ["project"] // Only project settings
 }
});
```

Isolation is especially important for CI/CD pipelines, deployed applications, test environments, and multi-tenant systems where local customizations should not leak in.

SDK v0.1.0 briefly defaulted to no settings loaded; this was reverted in subsequent releases. Python SDK 0.1.59 and earlier treated an empty list the same as omitting the option, so upgrade before relying on `setting_sources=[]`. See [What settingSources does not control](/docs/en/agent-sdk/claude-code-features#what-settingsources-does-not-control) for inputs that are read even when `settingSources` is `[]`.

## [​](#why-the-rename) Why the Rename?

The Claude Code SDK was originally designed for coding tasks, but it has evolved into a powerful framework for building all types of AI agents. The new name “Claude Agent SDK” better reflects its capabilities:

- Building business agents (legal assistants, finance advisors, customer support)
- Creating specialized coding agents (SRE bots, security reviewers, code review agents)
- Developing custom agents for any domain with tool use, MCP integration, and more

## [​](#getting-help) Getting Help

If you encounter any issues during migration:
**For TypeScript/JavaScript:**

1. Check that all imports are updated to use `@anthropic-ai/claude-agent-sdk`
2. Verify your package.json has the new package name
3. Run `npm install` to ensure dependencies are updated

**For Python:**

1. Check that all imports are updated to use `claude_agent_sdk`
2. Verify your requirements.txt or pyproject.toml has the new package name
3. Run `pip install claude-agent-sdk` to ensure the package is installed

## [​](#next-steps) Next Steps

- Explore the [Agent SDK Overview](/docs/en/agent-sdk/overview) to learn about available features
- Check out the [TypeScript SDK Reference](/docs/en/agent-sdk/typescript) for detailed API documentation
- Review the [Python SDK Reference](/docs/en/agent-sdk/python) for Python-specific documentation
- Learn about [Custom Tools](/docs/en/agent-sdk/custom-tools) and [MCP Integration](/docs/en/agent-sdk/mcp)

Was this page helpful?

YesNo

[Python SDK](/docs/en/agent-sdk/python)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.