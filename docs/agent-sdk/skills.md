---
source: https://platform.claude.com/docs/en/agent-sdk/skills
title: Agent Skills in the SDK
last_fetched: 2026-04-24T09:03:12.747686+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

Customize behavior

Agent Skills in the SDK

[Getting started](/docs/en/overview)[Build with Claude Code](/docs/en/sub-agents)[Deployment](/docs/en/third-party-integrations)[Administration](/docs/en/admin-setup)[Configuration](/docs/en/settings)[Reference](/docs/en/cli-reference)[Agent SDK](/docs/en/agent-sdk/overview)[What's New](/docs/en/whats-new)[Resources](/docs/en/legal-and-compliance)

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

## [​](#overview) Overview

Agent Skills extend Claude with specialized capabilities that Claude autonomously invokes when relevant. Skills are packaged as `SKILL.md` files containing instructions, descriptions, and optional supporting resources.
For comprehensive information about Skills, including benefits, architecture, and authoring guidelines, see the [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview).

## [​](#how-skills-work-with-the-sdk) How Skills Work with the SDK

When using the Claude Agent SDK, Skills are:

1. **Defined as filesystem artifacts**: Created as `SKILL.md` files in specific directories (`.claude/skills/`)
2. **Loaded from filesystem**: Skills are loaded from filesystem locations governed by `settingSources` (TypeScript) or `setting_sources` (Python)
3. **Automatically discovered**: Once filesystem settings are loaded, Skill metadata is discovered at startup from user and project directories; full content loaded when triggered
4. **Model-invoked**: Claude autonomously chooses when to use them based on context
5. **Enabled via allowed\_tools**: Add `"Skill"` to your `allowed_tools` to enable Skills

Unlike subagents (which can be defined programmatically), Skills must be created as filesystem artifacts. The SDK does not provide a programmatic API for registering Skills.

Skills are discovered through the filesystem setting sources. With default `query()` options, the SDK loads user and project sources, so skills in `~/.claude/skills/` and `<cwd>/.claude/skills/` are available. If you set `settingSources` explicitly, include `'user'` or `'project'` to keep skill discovery, or use the [`plugins` option](/docs/en/agent-sdk/plugins) to load skills from a specific path.

## [​](#using-skills-with-the-sdk) Using Skills with the SDK

To use Skills with the SDK, you need to:

1. Include `"Skill"` in your `allowed_tools` configuration
2. Configure `settingSources`/`setting_sources` to load Skills from the filesystem

Once configured, Claude automatically discovers Skills from the specified directories and invokes them when relevant to the user’s request.

Python

TypeScript

```shiki
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
 options = ClaudeAgentOptions(
 cwd="/path/to/project", # Project with .claude/skills/
 setting_sources=["user", "project"], # Load Skills from filesystem
 allowed_tools=["Skill", "Read", "Write", "Bash"], # Enable Skill tool
 )

 async for message in query(
 prompt="Help me process this PDF document", options=options
 ):
 print(message)

asyncio.run(main())
```

## [​](#skill-locations) Skill Locations

Skills are loaded from filesystem directories based on your `settingSources`/`setting_sources` configuration:

- **Project Skills** (`.claude/skills/`): Shared with your team via git - loaded when `setting_sources` includes `"project"`
- **User Skills** (`~/.claude/skills/`): Personal Skills across all projects - loaded when `setting_sources` includes `"user"`
- **Plugin Skills**: Bundled with installed Claude Code plugins

## [​](#creating-skills) Creating Skills

Skills are defined as directories containing a `SKILL.md` file with YAML frontmatter and Markdown content. The `description` field determines when Claude invokes your Skill.
**Example directory structure**:

```shiki
.claude/skills/processing-pdfs/
└── SKILL.md
```

For complete guidance on creating Skills, including SKILL.md structure, multi-file Skills, and examples, see:

- [Agent Skills in Claude Code](/docs/en/skills): Complete guide with examples
- [Agent Skills Best Practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): Authoring guidelines and naming conventions

## [​](#tool-restrictions) Tool Restrictions

The `allowed-tools` frontmatter field in SKILL.md is only supported when using Claude Code CLI directly. **It does not apply when using Skills through the SDK**.When using the SDK, control tool access through the main `allowedTools` option in your query configuration.

To control tool access for Skills in SDK applications, use `allowedTools` to pre-approve specific tools. Without a `canUseTool` callback, anything not in the list is denied:

Import statements from the first example are assumed in the following code snippets.

Python

TypeScript

```shiki
options = ClaudeAgentOptions(
 setting_sources=["user", "project"], # Load Skills from filesystem
 allowed_tools=["Skill", "Read", "Grep", "Glob"],
)

async for message in query(prompt="Analyze the codebase structure", options=options):
 print(message)
```

## [​](#discovering-available-skills) Discovering Available Skills

To see which Skills are available in your SDK application, simply ask Claude:

Python

TypeScript

```shiki
options = ClaudeAgentOptions(
 setting_sources=["user", "project"], # Load Skills from filesystem
 allowed_tools=["Skill"],
)

async for message in query(prompt="What Skills are available?", options=options):
 print(message)
```

Claude will list the available Skills based on your current working directory and installed plugins.

## [​](#testing-skills) Testing Skills

Test Skills by asking questions that match their descriptions:

Python

TypeScript

```shiki
options = ClaudeAgentOptions(
 cwd="/path/to/project",
 setting_sources=["user", "project"], # Load Skills from filesystem
 allowed_tools=["Skill", "Read", "Bash"],
)

async for message in query(prompt="Extract text from invoice.pdf", options=options):
 print(message)
```

Claude automatically invokes the relevant Skill if the description matches your request.

## [​](#troubleshooting) Troubleshooting

### [​](#skills-not-found) Skills Not Found

**Check settingSources configuration**: Skills are discovered through the `user` and `project` setting sources. If you set `settingSources`/`setting_sources` explicitly and omit those sources, skills are not loaded:

Python

TypeScript

```shiki
# Skills not loaded: setting_sources excludes user and project
options = ClaudeAgentOptions(setting_sources=[], allowed_tools=["Skill"])

# Skills loaded: user and project sources included
options = ClaudeAgentOptions(
 setting_sources=["user", "project"],
 allowed_tools=["Skill"],
)
```

For more details on `settingSources`/`setting_sources`, see the [TypeScript SDK reference](/docs/en/agent-sdk/typescript#setting-source) or [Python SDK reference](/docs/en/agent-sdk/python#setting-source).
**Check working directory**: The SDK loads Skills relative to the `cwd` option. Ensure it points to a directory containing `.claude/skills/`:

Python

TypeScript

```shiki
# Ensure your cwd points to the directory containing .claude/skills/
options = ClaudeAgentOptions(
 cwd="/path/to/project", # Must contain .claude/skills/
 setting_sources=["user", "project"], # Loads skills from these sources
 allowed_tools=["Skill"],
)
```

See the “Using Skills with the SDK” section above for the complete pattern.
**Verify filesystem location**:

```shiki
# Check project Skills
ls .claude/skills/*/SKILL.md

# Check personal Skills
ls ~/.claude/skills/*/SKILL.md
```

### [​](#skill-not-being-used) Skill Not Being Used

**Check the Skill tool is enabled**: Confirm `"Skill"` is in your `allowedTools`.
**Check the description**: Ensure it’s specific and includes relevant keywords. See [Agent Skills Best Practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices#writing-effective-descriptions) for guidance on writing effective descriptions.

### [​](#additional-troubleshooting) Additional Troubleshooting

For general Skills troubleshooting (YAML syntax, debugging, etc.), see the [Claude Code Skills troubleshooting section](/docs/en/skills#troubleshooting).

## [​](#related-documentation) Related Documentation

### [​](#skills-guides) Skills Guides

- [Agent Skills in Claude Code](/docs/en/skills): Complete Skills guide with creation, examples, and troubleshooting
- [Agent Skills Overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview): Conceptual overview, benefits, and architecture
- [Agent Skills Best Practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): Authoring guidelines for effective Skills
- [Agent Skills Cookbook](https://platform.claude.com/cookbook/skills-notebooks-01-skills-introduction): Example Skills and templates

### [​](#sdk-resources) SDK Resources

- [Subagents in the SDK](/docs/en/agent-sdk/subagents): Similar filesystem-based agents with programmatic options
- [Slash Commands in the SDK](/docs/en/agent-sdk/slash-commands): User-invoked commands
- [SDK Overview](/docs/en/agent-sdk/overview): General SDK concepts
- [TypeScript SDK Reference](/docs/en/agent-sdk/typescript): Complete API documentation
- [Python SDK Reference](/docs/en/agent-sdk/python): Complete API documentation

Was this page helpful?

YesNo

[Slash Commands in the SDK](/docs/en/agent-sdk/slash-commands)[Plugins in the SDK](/docs/en/agent-sdk/plugins)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.