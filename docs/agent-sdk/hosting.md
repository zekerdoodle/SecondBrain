---
source: https://platform.claude.com/docs/en/agent-sdk/hosting
title: Hosting the Agent SDK
last_fetched: 2026-05-06T09:06:00.362938+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

Deployment

Hosting the Agent SDK

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

The Claude Agent SDK differs from traditional stateless LLM APIs in that it maintains conversational state and executes commands in a persistent environment. This guide covers the architecture, hosting considerations, and best practices for deploying SDK-based agents in production.

For security hardening beyond basic sandboxing (including network controls, credential management, and isolation options), see [Secure Deployment](/docs/en/agent-sdk/secure-deployment).

## [​](#hosting-requirements) Hosting Requirements

### [​](#container-based-sandboxing) Container-Based Sandboxing

For security and isolation, the SDK should run inside a sandboxed container environment. This provides process isolation, resource limits, network control, and ephemeral filesystems.
The SDK also supports [programmatic sandbox configuration](/docs/en/agent-sdk/typescript#sandboxsettings) for command execution.

### [​](#system-requirements) System Requirements

Each SDK instance requires:

- **Runtime dependencies**
 - Python 3.10+ for the Python SDK, or Node.js 18+ for the TypeScript SDK
 - Both SDK packages bundle a native Claude Code binary for the host platform, so no separate Claude Code or Node.js install is needed for the spawned CLI
- **Resource allocation**
 - Recommended: 1GiB RAM, 5GiB of disk, and 1 CPU (vary this based on your task as needed)
- **Network access**
 - Outbound HTTPS to `api.anthropic.com`
 - Optional: Access to MCP servers or external tools

## [​](#understanding-the-sdk-architecture) Understanding the SDK Architecture

Unlike stateless API calls, the Claude Agent SDK operates as a **long-running process** that:

- **Executes commands** in a persistent shell environment
- **Manages file operations** within a working directory
- **Handles tool execution** with context from previous interactions

## [​](#sandbox-provider-options) Sandbox Provider Options

Several providers specialize in secure container environments for AI code execution:

- **[Modal Sandbox](https://modal.com/docs/guide/sandbox)** - [demo implementation](https://modal.com/docs/examples/claude-slack-gif-creator)
- **[Cloudflare Sandboxes](https://github.com/cloudflare/sandbox-sdk)**
- **[Daytona](https://www.daytona.io/)**
- **[E2B](https://e2b.dev/)**
- **[Fly Machines](https://fly.io/docs/machines/)**
- **[Vercel Sandbox](https://vercel.com/docs/functions/sandbox)**

For self-hosted options (Docker, gVisor, Firecracker) and detailed isolation configuration, see [Isolation Technologies](/docs/en/agent-sdk/secure-deployment#isolation-technologies).

## [​](#production-deployment-patterns) Production Deployment Patterns

### [​](#pattern-1-ephemeral-sessions) Pattern 1: Ephemeral Sessions

Create a new container for each user task, then destroy it when complete.
Best for one-off tasks, the user may still interact with the AI while the task is completing, but once completed the container is destroyed.
**Examples:**

- Bug Investigation & Fix: Debug and resolve a specific issue with relevant context
- Invoice Processing: Extract and structure data from receipts/invoices for accounting systems
- Translation Tasks: Translate documents or content batches between languages
- Image/Video Processing: Apply transformations, optimizations, or extract metadata from media files

### [​](#pattern-2-long-running-sessions) Pattern 2: Long-Running Sessions

Maintain persistent container instances for long running tasks. Often times running *multiple* Claude Agent processes inside of the container based on demand.
Best for proactive agents that take action without the users input, agents that serve content or agents that process high amounts of messages.
**Examples:**

- Email Agent: Monitors incoming emails and autonomously triages, responds, or takes actions based on content
- Site Builder: Hosts custom websites per user with live editing capabilities served through container ports
- High-Frequency Chat Bots: Handles continuous message streams from platforms like Slack where rapid response times are critical

### [​](#pattern-3-hybrid-sessions) Pattern 3: Hybrid Sessions

Ephemeral containers that are hydrated with history and state, possibly from a database or from the SDK’s session resumption features.
Best for containers with intermittent interaction from the user that kicks off work and spins down when the work is completed but can be continued.
**Examples:**

- Personal Project Manager: Helps manage ongoing projects with intermittent check-ins, maintains context of tasks, decisions, and progress
- Deep Research: Conducts multi-hour research tasks, saves findings and resumes investigation when user returns
- Customer Support Agent: Handles support tickets that span multiple interactions, loads ticket history and customer context

### [​](#pattern-4-single-containers) Pattern 4: Single Containers

Run multiple Claude Agent SDK processes in one global container.
Best for agents that must collaborate closely together. This is likely the least popular pattern because you will have to prevent agents from overwriting each other.
**Examples:**

- **Simulations**: Agents that interact with each other in simulations such as video games.

## [​](#faq) FAQ

### [​](#how-do-i-communicate-with-my-sandboxes) How do I communicate with my sandboxes?

When hosting in containers, expose ports to communicate with your SDK instances. Your application can expose HTTP/WebSocket endpoints for external clients while the SDK runs internally within the container.

### [​](#what-is-the-cost-of-hosting-a-container) What is the cost of hosting a container?

The dominant cost of serving agents is the tokens; containers vary based on what you provision, but a minimum cost is roughly 5 cents per hour running.

### [​](#when-should-i-shut-down-idle-containers-vs-keeping-them-warm) When should I shut down idle containers vs. keeping them warm?

This is likely provider dependent, different sandbox providers will let you set different criteria for idle timeouts after which a sandbox might spin down.
You will want to tune this timeout based on how frequent you think user response might be.

### [​](#how-often-should-i-update-the-claude-code-cli) How often should I update the Claude Code CLI?

The Claude Code CLI is versioned with semver, so any breaking changes will be versioned.

### [​](#how-do-i-monitor-container-health-and-agent-performance) How do I monitor container health and agent performance?

Since containers are just servers the same logging infrastructure you use for the backend will work for containers.

### [​](#how-long-can-an-agent-session-run-before-timing-out) How long can an agent session run before timing out?

An agent session will not timeout, but consider setting a ‘maxTurns’ property to prevent Claude from getting stuck in a loop.

## [​](#next-steps) Next Steps

- [Secure Deployment](/docs/en/agent-sdk/secure-deployment) - Network controls, credential management, and isolation hardening
- [TypeScript SDK - Sandbox Settings](/docs/en/agent-sdk/typescript#sandboxsettings) - Configure sandbox programmatically
- [Sessions Guide](/docs/en/agent-sdk/sessions) - Learn about session management
- [Permissions](/docs/en/agent-sdk/permissions) - Configure tool permissions
- [Cost Tracking](/docs/en/agent-sdk/cost-tracking) - Monitor API usage
- [MCP Integration](/docs/en/agent-sdk/mcp) - Extend with custom tools

Was this page helpful?

YesNo

[Todo Lists](/docs/en/agent-sdk/todo-tracking)[Securely deploying AI agents](/docs/en/agent-sdk/secure-deployment)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.