---
source: https://platform.claude.com/docs/en/agent-sdk/todo-tracking
title: Todo Lists
last_fetched: 2026-05-09T09:16:34.430021+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

Control and observability

Todo Lists

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
- [TypeScript V2 (deprecated)](/docs/en/agent-sdk/typescript-v2-preview)
- [Python SDK](/docs/en/agent-sdk/python)
- [Migration Guide](/docs/en/agent-sdk/migration-guide)

On this page

> ## Documentation Index
>
> Fetch the complete documentation index at: <https://code.claude.com/docs/llms.txt>
>
> Use this file to discover all available pages before exploring further.

Todo tracking provides a structured way to manage tasks and display progress to users. The Claude Agent SDK includes built-in todo functionality that helps organize complex workflows and keep users informed about task progression.

### [​](#todo-lifecycle) Todo Lifecycle

Todos follow a predictable lifecycle:

1. **Created** as `pending` when tasks are identified
2. **Activated** to `in_progress` when work begins
3. **Completed** when the task finishes successfully
4. **Removed** when all tasks in a group are completed

### [​](#when-todos-are-used) When Todos Are Used

The SDK automatically creates todos for:

- **Complex multi-step tasks** requiring 3 or more distinct actions
- **User-provided task lists** when multiple items are mentioned
- **Non-trivial operations** that benefit from progress tracking
- **Explicit requests** when users ask for todo organization

## [​](#examples) Examples

### [​](#monitoring-todo-changes) Monitoring Todo Changes

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
 prompt: "Optimize my React app performance and track progress with todos",
 options: { maxTurns: 15 }
})) {
 // Todo updates are reflected in the message stream
 if (message.type === "assistant") {
 for (const block of message.message.content) {
 if (block.type === "tool_use" && block.name === "TodoWrite") {
 const todos = block.input.todos;

 console.log("Todo Status Update:");
 todos.forEach((todo, index) => {
 const status =
 todo.status === "completed" ? "✅" : todo.status === "in_progress" ? "🔧" : "❌";
 console.log(`${index + 1}. ${status} ${todo.content}`);
 });
 }
 }
 }
}
```

### [​](#real-time-progress-display) Real-time Progress Display

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

class TodoTracker {
 private todos: any[] = [];

 displayProgress() {
 if (this.todos.length === 0) return;

 const completed = this.todos.filter((t) => t.status === "completed").length;
 const inProgress = this.todos.filter((t) => t.status === "in_progress").length;
 const total = this.todos.length;

 console.log(`\nProgress: ${completed}/${total} completed`);
 console.log(`Currently working on: ${inProgress} task(s)\n`);

 this.todos.forEach((todo, index) => {
 const icon =
 todo.status === "completed" ? "✅" : todo.status === "in_progress" ? "🔧" : "❌";
 const text = todo.status === "in_progress" ? todo.activeForm : todo.content;
 console.log(`${index + 1}. ${icon} ${text}`);
 });
 }

 async trackQuery(prompt: string) {
 for await (const message of query({
 prompt,
 options: { maxTurns: 20 }
 })) {
 if (message.type === "assistant") {
 for (const block of message.message.content) {
 if (block.type === "tool_use" && block.name === "TodoWrite") {
 this.todos = block.input.todos;
 this.displayProgress();
 }
 }
 }
 }
 }
}

// Usage
const tracker = new TodoTracker();
await tracker.trackQuery("Build a complete authentication system with todos");
```

## [​](#related-documentation) Related Documentation

- [TypeScript SDK Reference](/docs/en/agent-sdk/typescript)
- [Python SDK Reference](/docs/en/agent-sdk/python)
- [Streaming vs Single Mode](/docs/en/agent-sdk/streaming-vs-single-mode)
- [Custom Tools](/docs/en/agent-sdk/custom-tools)

Was this page helpful?

YesNo

[Observability with OpenTelemetry](/docs/en/agent-sdk/observability)[Hosting the Agent SDK](/docs/en/agent-sdk/hosting)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.