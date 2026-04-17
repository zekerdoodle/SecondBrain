---
source: https://platform.claude.com/docs/en/agent-sdk/slash-commands
title: Slash Commands in the SDK
last_fetched: 2026-04-17T09:03:18.728023+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

Customize behavior

Slash Commands in the SDK

[Getting started](/docs/en/overview)[Build with Claude Code](/docs/en/sub-agents)[Deployment](/docs/en/third-party-integrations)[Administration](/docs/en/setup)[Configuration](/docs/en/settings)[Reference](/docs/en/cli-reference)[Agent SDK](/docs/en/agent-sdk/overview)[What's New](/docs/en/whats-new)[Resources](/docs/en/legal-and-compliance)

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

Slash commands provide a way to control Claude Code sessions with special commands that start with `/`. These commands can be sent through the SDK to perform actions like compacting context, listing context usage, or invoking custom commands. Only commands that work without an interactive terminal are dispatchable through the SDK; the `system/init` message lists the ones available in your session.

## [​](#discovering-available-slash-commands) Discovering Available Slash Commands

The Claude Agent SDK provides information about available slash commands in the system initialization message. Access this information when your session starts:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
 prompt: "Hello Claude",
 options: { maxTurns: 1 }
})) {
 if (message.type === "system" && message.subtype === "init") {
 console.log("Available slash commands:", message.slash_commands);
 // Example output: ["/compact", "/context", "/cost"]
 }
}
```

## [​](#sending-slash-commands) Sending Slash Commands

Send slash commands by including them in your prompt string, just like regular text:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

// Send a slash command
for await (const message of query({
 prompt: "/compact",
 options: { maxTurns: 1 }
})) {
 if (message.type === "result") {
 console.log("Command executed:", message.result);
 }
}
```

## [​](#common-slash-commands) Common Slash Commands

### [​](#/compact-compact-conversation-history) `/compact` - Compact Conversation History

The `/compact` command reduces the size of your conversation history by summarizing older messages while preserving important context:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
 prompt: "/compact",
 options: { maxTurns: 1 }
})) {
 if (message.type === "system" && message.subtype === "compact_boundary") {
 console.log("Compaction completed");
 console.log("Pre-compaction tokens:", message.compact_metadata.pre_tokens);
 console.log("Trigger:", message.compact_metadata.trigger);
 }
}
```

### [​](#clearing-the-conversation) Clearing the conversation

The interactive `/clear` command is not available in the SDK. Each `query()` call already starts a fresh conversation, so to clear context, end the current `query()` and start a new one. The previous conversation stays on disk and can be returned to by passing its session ID to the [`resume` option](/docs/en/agent-sdk/sessions#resume-by-id).

## [​](#creating-custom-slash-commands) Creating Custom Slash Commands

In addition to using built-in slash commands, you can create your own custom commands that are available through the SDK. Custom commands are defined as markdown files in specific directories, similar to how subagents are configured.

The `.claude/commands/` directory is the legacy format. The recommended format is `.claude/skills/<name>/SKILL.md`, which supports the same slash-command invocation (`/name`) plus autonomous invocation by Claude. See [Skills](/docs/en/agent-sdk/skills) for the current format. The CLI continues to support both formats, and the examples below remain accurate for `.claude/commands/`.

### [​](#file-locations) File Locations

Custom slash commands are stored in designated directories based on their scope:

- **Project commands**: `.claude/commands/` - Available only in the current project (legacy; prefer `.claude/skills/`)
- **Personal commands**: `~/.claude/commands/` - Available across all your projects (legacy; prefer `~/.claude/skills/`)

### [​](#file-format) File Format

Each custom command is a markdown file where:

- The filename (without `.md` extension) becomes the command name
- The file content defines what the command does
- Optional YAML frontmatter provides configuration

#### [​](#basic-example) Basic Example

Create `.claude/commands/refactor.md`:

```shiki
Refactor the selected code to improve readability and maintainability.
Focus on clean code principles and best practices.
```

This creates the `/refactor` command that you can use through the SDK.

#### [​](#with-frontmatter) With Frontmatter

Create `.claude/commands/security-check.md`:

```shiki
---
allowed-tools: Read, Grep, Glob
description: Run security vulnerability scan
model: claude-opus-4-7
---

Analyze the codebase for security vulnerabilities including:
- SQL injection risks
- XSS vulnerabilities
- Exposed credentials
- Insecure configurations
```

### [​](#using-custom-commands-in-the-sdk) Using Custom Commands in the SDK

Once defined in the filesystem, custom commands are automatically available through the SDK:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

// Use a custom command
for await (const message of query({
 prompt: "/refactor src/auth/login.ts",
 options: { maxTurns: 3 }
})) {
 if (message.type === "assistant") {
 console.log("Refactoring suggestions:", message.message);
 }
}

// Custom commands appear in the slash_commands list
for await (const message of query({
 prompt: "Hello",
 options: { maxTurns: 1 }
})) {
 if (message.type === "system" && message.subtype === "init") {
 // Will include both built-in and custom commands
 console.log("Available commands:", message.slash_commands);
 // Example: ["/compact", "/context", "/cost", "/refactor", "/security-check"]
 }
}
```

### [​](#advanced-features) Advanced Features

#### [​](#arguments-and-placeholders) Arguments and Placeholders

Custom commands support dynamic arguments using placeholders:
Create `.claude/commands/fix-issue.md`:

```shiki
---
argument-hint: [issue-number] [priority]
description: Fix a GitHub issue
---

Fix issue #$1 with priority $2.
Check the issue description and implement the necessary changes.
```

Use in SDK:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

// Pass arguments to custom command
for await (const message of query({
 prompt: "/fix-issue 123 high",
 options: { maxTurns: 5 }
})) {
 // Command will process with $1="123" and $2="high"
 if (message.type === "result") {
 console.log("Issue fixed:", message.result);
 }
}
```

#### [​](#bash-command-execution) Bash Command Execution

Custom commands can execute bash commands and include their output:
Create `.claude/commands/git-commit.md`:

```shiki
---
allowed-tools: Bash(git add *), Bash(git status *), Bash(git commit *)
description: Create a git commit
---

## Context

- Current status: !`git status`
- Current diff: !`git diff HEAD`

## Task

Create a git commit with appropriate message based on the changes.
```

#### [​](#file-references) File References

Include file contents using the `@` prefix:
Create `.claude/commands/review-config.md`:

```shiki
---
description: Review configuration files
---

Review the following configuration files for issues:
- Package config: @package.json
- TypeScript config: @tsconfig.json
- Environment config: @.env

Check for security issues, outdated dependencies, and misconfigurations.
```

### [​](#organization-with-namespacing) Organization with Namespacing

Organize commands in subdirectories for better structure:

```shiki
.claude/commands/
├── frontend/
│ ├── component.md # Creates /component (project:frontend)
│ └── style-check.md # Creates /style-check (project:frontend)
├── backend/
│ ├── api-test.md # Creates /api-test (project:backend)
│ └── db-migrate.md # Creates /db-migrate (project:backend)
└── review.md # Creates /review (project)
```

The subdirectory appears in the command description but doesn’t affect the command name itself.

### [​](#practical-examples) Practical Examples

#### [​](#code-review-command) Code Review Command

Create `.claude/commands/code-review.md`:

```shiki
---
allowed-tools: Read, Grep, Glob, Bash(git diff *)
description: Comprehensive code review
---

## Changed Files
!`git diff --name-only HEAD~1`

## Detailed Changes
!`git diff HEAD~1`

## Review Checklist

Review the above changes for:
1. Code quality and readability
2. Security vulnerabilities
3. Performance implications
4. Test coverage
5. Documentation completeness

Provide specific, actionable feedback organized by priority.
```

#### [​](#test-runner-command) Test Runner Command

Create `.claude/commands/test.md`:

```shiki
---
allowed-tools: Bash, Read, Edit
argument-hint: [test-pattern]
description: Run tests with optional pattern
---

Run tests matching pattern: $ARGUMENTS

1. Detect the test framework (Jest, pytest, etc.)
2. Run tests with the provided pattern
3. If tests fail, analyze and fix them
4. Re-run to verify fixes
```

Use these commands through the SDK:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

// Run code review
for await (const message of query({
 prompt: "/code-review",
 options: { maxTurns: 3 }
})) {
 // Process review feedback
}

// Run specific tests
for await (const message of query({
 prompt: "/test auth",
 options: { maxTurns: 5 }
})) {
 // Handle test results
}
```

## [​](#see-also) See Also

- [Slash Commands](/docs/en/skills) - Complete slash command documentation
- [Subagents in the SDK](/docs/en/agent-sdk/subagents) - Similar filesystem-based configuration for subagents
- [TypeScript SDK reference](/docs/en/agent-sdk/typescript) - Complete API documentation
- [SDK overview](/docs/en/agent-sdk/overview) - General SDK concepts
- [CLI reference](/docs/en/cli-reference) - Command-line interface

Was this page helpful?

YesNo

[Modifying system prompts](/docs/en/agent-sdk/modifying-system-prompts)[Agent Skills in the SDK](/docs/en/agent-sdk/skills)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.