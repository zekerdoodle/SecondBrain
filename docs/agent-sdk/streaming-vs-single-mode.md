---
source: https://platform.claude.com/docs/en/agent-sdk/streaming-vs-single-mode
title: Streaming Input
last_fetched: 2026-04-29T09:03:34.097910+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

Input and output

Streaming Input

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

## [​](#overview) Overview

The Claude Agent SDK supports two distinct input modes for interacting with agents:

- **Streaming Input Mode** (Default & Recommended) - A persistent, interactive session
- **Single Message Input** - One-shot queries that use session state and resuming

This guide explains the differences, benefits, and use cases for each mode to help you choose the right approach for your application.

## [​](#streaming-input-mode-recommended) Streaming Input Mode (Recommended)

Streaming input mode is the **preferred** way to use the Claude Agent SDK. It provides full access to the agent’s capabilities and enables rich, interactive experiences.
It allows the agent to operate as a long lived process that takes in user input, handles interruptions, surfaces permission requests, and handles session management.

### [​](#how-it-works) How It Works

Environment/File SystemTools/HooksClaude AgentYour ApplicationEnvironment/File SystemTools/HooksClaude AgentYour ApplicationSession stays alivePersistent file systemstate maintainedInitialize with AsyncGeneratorYield Message 1Execute toolsRead filesFile contentsWrite/Edit filesSuccess/ErrorStream partial responseStream more content...Complete Message 1Yield Message 2 + ImageProcess image & executeAccess filesystemOperation resultsStream response 2Queue Message 3Interrupt/CancelHandle interruption

### [​](#benefits) Benefits

## Image Uploads

Attach images directly to messages for visual analysis and understanding

## Queued Messages

Send multiple messages that process sequentially, with ability to interrupt

## Tool Integration

Full access to all tools and custom MCP servers during the session

## Hooks Support

Use lifecycle hooks to customize behavior at various points

## Real-time Feedback

See responses as they’re generated, not just final results

## Context Persistence

Maintain conversation context across multiple turns naturally

### [​](#implementation-example) Implementation Example

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";
import { readFile } from "fs/promises";

async function* generateMessages() {
 // First message
 yield {
 type: "user" as const,
 message: {
 role: "user" as const,
 content: "Analyze this codebase for security issues"
 }
 };

 // Wait for conditions or user input
 await new Promise((resolve) => setTimeout(resolve, 2000));

 // Follow-up with image
 yield {
 type: "user" as const,
 message: {
 role: "user" as const,
 content: [
 {
 type: "text",
 text: "Review this architecture diagram"
 },
 {
 type: "image",
 source: {
 type: "base64",
 media_type: "image/png",
 data: await readFile("diagram.png", "base64")
 }
 }
 ]
 }
 };
}

// Process streaming responses
for await (const message of query({
 prompt: generateMessages(),
 options: {
 maxTurns: 10,
 allowedTools: ["Read", "Grep"]
 }
})) {
 if (message.type === "result") {
 console.log(message.result);
 }
}
```

## [​](#single-message-input) Single Message Input

Single message input is simpler but more limited.

### [​](#when-to-use-single-message-input) When to Use Single Message Input

Use single message input when:

- You need a one-shot response
- You do not need image attachments, hooks, etc.
- You need to operate in a stateless environment, such as a lambda function

### [​](#limitations) Limitations

Single message input mode does **not** support:

- Direct image attachments in messages
- Dynamic message queueing
- Real-time interruption
- Hook integration
- Natural multi-turn conversations

### [​](#implementation-example-2) Implementation Example

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

// Simple one-shot query
for await (const message of query({
 prompt: "Explain the authentication flow",
 options: {
 maxTurns: 1,
 allowedTools: ["Read", "Grep"]
 }
})) {
 if (message.type === "result") {
 console.log(message.result);
 }
}

// Continue conversation with session management
for await (const message of query({
 prompt: "Now explain the authorization process",
 options: {
 continue: true,
 maxTurns: 1
 }
})) {
 if (message.type === "result") {
 console.log(message.result);
 }
}
```

Was this page helpful?

YesNo

[Work with sessions](/docs/en/agent-sdk/sessions)[Handle approvals and user input](/docs/en/agent-sdk/user-input)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.