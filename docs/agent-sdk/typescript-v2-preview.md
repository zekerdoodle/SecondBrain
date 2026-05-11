---
source: https://platform.claude.com/docs/en/agent-sdk/typescript-v2-preview
title: TypeScript SDK V2 session API (deprecated)
last_fetched: 2026-05-09T09:18:27.925301+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

SDK references

TypeScript SDK V2 session API (deprecated)

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

The V2 session API functions `unstable_v2_createSession`, `unstable_v2_resumeSession`, and `unstable_v2_prompt` are deprecated and will be removed in a future release. Use the [V1 `query()` API](/docs/en/agent-sdk/typescript) instead.

V2 was an experimental session API that removed the need for async generators and yield coordination. Instead of managing generator state across turns, each turn was a separate `send()`/`stream()` cycle. The API surface reduced to three concepts:

- `createSession()` / `resumeSession()`: Start or continue a conversation
- `session.send()`: Send a message
- `session.stream()`: Get the response

## [​](#installation) Installation

The V2 interface is included in the existing SDK package:

```shiki
npm install @anthropic-ai/claude-agent-sdk
```

The SDK bundles a native Claude Code binary for your platform as an optional dependency, so you don’t need to install Claude Code separately.

## [​](#quick-start) Quick start

### [​](#one-shot-prompt) One-shot prompt

For simple single-turn queries where you don’t need to maintain a session, use `unstable_v2_prompt()`. This example sends a math question and logs the answer:

```shiki
import { unstable_v2_prompt } from "@anthropic-ai/claude-agent-sdk";

const result = await unstable_v2_prompt("What is 2 + 2?", {
 model: "claude-opus-4-7"
});
if (result.subtype === "success") {
 console.log(result.result);
}
```

### [​](#basic-session) Basic session

For interactions beyond a single prompt, create a session. V2 separates sending and streaming into distinct steps:

- `send()` dispatches your message
- `stream()` streams back the response

This explicit separation makes it easier to add logic between turns (like processing responses before sending follow-ups).
The example below creates a session, sends “Hello!” to Claude, and prints the text response. It uses [`await using`](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-5-2.html#using-declarations-and-explicit-resource-management) (TypeScript 5.2+) to automatically close the session when the block exits. You can also call `session.close()` manually.

```shiki
import { unstable_v2_createSession } from "@anthropic-ai/claude-agent-sdk";

await using session = unstable_v2_createSession({
 model: "claude-opus-4-7"
});

await session.send("Hello!");
for await (const msg of session.stream()) {
 // Filter for assistant messages to get human-readable output
 if (msg.type === "assistant") {
 const text = msg.message.content
 .filter((block) => block.type === "text")
 .map((block) => block.text)
 .join("");
 console.log(text);
 }
}
```

### [​](#multi-turn-conversation) Multi-turn conversation

Sessions persist context across multiple exchanges. To continue a conversation, call `send()` again on the same session. Claude remembers the previous turns.
This example asks a math question, then asks a follow-up that references the previous answer:

```shiki
import { unstable_v2_createSession } from "@anthropic-ai/claude-agent-sdk";

await using session = unstable_v2_createSession({
 model: "claude-opus-4-7"
});

// Turn 1
await session.send("What is 5 + 3?");
for await (const msg of session.stream()) {
 // Filter for assistant messages to get human-readable output
 if (msg.type === "assistant") {
 const text = msg.message.content
 .filter((block) => block.type === "text")
 .map((block) => block.text)
 .join("");
 console.log(text);
 }
}

// Turn 2
await session.send("Multiply that by 2");
for await (const msg of session.stream()) {
 if (msg.type === "assistant") {
 const text = msg.message.content
 .filter((block) => block.type === "text")
 .map((block) => block.text)
 .join("");
 console.log(text);
 }
}
```

### [​](#session-resume) Session resume

If you have a session ID from a previous interaction, you can resume it later. This is useful for long-running workflows or when you need to persist conversations across application restarts.
This example creates a session, stores its ID, closes it, then resumes the conversation:

```shiki
import {
 unstable_v2_createSession,
 unstable_v2_resumeSession,
 type SDKMessage
} from "@anthropic-ai/claude-agent-sdk";

// Helper to extract text from assistant messages
function getAssistantText(msg: SDKMessage): string | null {
 if (msg.type !== "assistant") return null;
 return msg.message.content
 .filter((block) => block.type === "text")
 .map((block) => block.text)
 .join("");
}

// Create initial session and have a conversation
const session = unstable_v2_createSession({
 model: "claude-opus-4-7"
});

await session.send("Remember this number: 42");

// Get the session ID from any received message
let sessionId: string | undefined;
for await (const msg of session.stream()) {
 sessionId = msg.session_id;
 const text = getAssistantText(msg);
 if (text) console.log("Initial response:", text);
}

console.log("Session ID:", sessionId);
session.close();

// Later: resume the session using the stored ID
await using resumedSession = unstable_v2_resumeSession(sessionId!, {
 model: "claude-opus-4-7"
});

await resumedSession.send("What number did I ask you to remember?");
for await (const msg of resumedSession.stream()) {
 const text = getAssistantText(msg);
 if (text) console.log("Resumed response:", text);
}
```

### [​](#cleanup) Cleanup

Sessions can be closed manually or automatically using [`await using`](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-5-2.html#using-declarations-and-explicit-resource-management), a TypeScript 5.2+ feature for automatic resource cleanup. If you’re using an older TypeScript version or encounter compatibility issues, use manual cleanup instead.
**Automatic cleanup (TypeScript 5.2+):**

```shiki
import { unstable_v2_createSession } from "@anthropic-ai/claude-agent-sdk";

await using session = unstable_v2_createSession({
 model: "claude-opus-4-7"
});
// Session closes automatically when the block exits
```

**Manual cleanup:**

```shiki
import { unstable_v2_createSession } from "@anthropic-ai/claude-agent-sdk";

const session = unstable_v2_createSession({
 model: "claude-opus-4-7"
});
// ... use the session ...
session.close();
```

## [​](#api-reference) API reference

### [​](#unstable_v2_createsession) `unstable_v2_createSession()`

Creates a new session for multi-turn conversations.

```shiki
function unstable_v2_createSession(options: {
 model: string;
 // Additional options supported
}): SDKSession;
```

### [​](#unstable_v2_resumesession) `unstable_v2_resumeSession()`

Resumes an existing session by ID.

```shiki
function unstable_v2_resumeSession(
 sessionId: string,
 options: {
 model: string;
 // Additional options supported
 }
): SDKSession;
```

### [​](#unstable_v2_prompt) `unstable_v2_prompt()`

One-shot convenience function for single-turn queries.

```shiki
function unstable_v2_prompt(
 prompt: string,
 options: {
 model: string;
 // Additional options supported
 }
): Promise<SDKResultMessage>;
```

### [​](#sdksession-interface) SDKSession interface

```shiki
interface SDKSession {
 readonly sessionId: string;
 send(message: string | SDKUserMessage): Promise<void>;
 stream(): AsyncGenerator<SDKMessage, void>;
 close(): void;
}
```

## [​](#feature-availability) Feature availability

The V2 session API does not support every V1 feature. The following require the [V1 SDK](/docs/en/agent-sdk/typescript):

- Session forking (`forkSession` option)
- Some advanced streaming input patterns

## [​](#see-also) See also

- [TypeScript SDK reference (V1)](/docs/en/agent-sdk/typescript) - Full V1 SDK documentation
- [SDK overview](/docs/en/agent-sdk/overview) - General SDK concepts
- [V2 examples on GitHub](https://github.com/anthropics/claude-agent-sdk-demos/tree/main/hello-world-v2) - Working code examples

Was this page helpful?

YesNo

[TypeScript SDK](/docs/en/agent-sdk/typescript)[Python SDK](/docs/en/agent-sdk/python)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.