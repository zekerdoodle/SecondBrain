---
source: https://platform.claude.com/docs/en/agent-sdk/cost-tracking
title: Track cost and usage
last_fetched: 2026-04-09T09:01:53.861175+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

Control and observability

Track cost and usage

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

The Claude Agent SDK provides detailed token usage information for each interaction with Claude. This guide explains how to properly track costs and understand usage reporting, especially when dealing with parallel tool uses and multi-step conversations.
For complete API documentation, see the [TypeScript SDK reference](/docs/en/agent-sdk/typescript) and [Python SDK reference](/docs/en/agent-sdk/python).

## [​](#understand-token-usage) Understand token usage

The TypeScript and Python SDKs expose the same usage data with different field names:

- **TypeScript** provides per-step token breakdowns on each assistant message (`message.message.id`, `message.message.usage`), per-model cost via `modelUsage` on the result message, and a cumulative total on the result message.
- **Python** provides per-step token breakdowns on each assistant message (`message.usage`, `message.message_id`), per-model cost via `model_usage` on the result message, and the accumulated total on the result message (`total_cost_usd` and `usage` dict).

Both SDKs use the same underlying cost model and expose the same granularity. The difference is in field naming and where per-step usage is nested.
Cost tracking depends on understanding how the SDK scopes usage data:

- **`query()` call:** one invocation of the SDK’s `query()` function. A single call can involve multiple steps (Claude responds, uses tools, gets results, responds again). Each call produces one [`result`](/docs/en/agent-sdk/typescript#sdk-result-message) message at the end.
- **Step:** a single request/response cycle within a `query()` call. Each step produces assistant messages with token usage.
- **Session:** a series of `query()` calls linked by a session ID (using the `resume` option). Each `query()` call within a session reports its own cost independently.

The following diagram shows the message stream from a single `query()` call, with token usage reported at each step and the authoritative total at the end:
![Diagram showing a query producing two steps of messages. Step 1 has four assistant messages sharing the same ID and usage (count once), Step 2 has one assistant message with a new ID, and the final result message shows total_cost_usd for billing.](https://mintcdn.com/claude-code/gvy2DIUELtNA8qD3/images/agent-sdk/message-usage-flow.svg?fit=max&auto=format&n=gvy2DIUELtNA8qD3&q=85&s=88cba82134f8f7994d780c3f153b83fc)

1

Each step produces assistant messages

When Claude responds, it sends one or more assistant messages. In TypeScript, each assistant message contains a nested `BetaMessage` (accessed via `message.message`) with an `id` and a [`usage`](https://platform.claude.com/docs/en/api/messages) object with token counts (`input_tokens`, `output_tokens`). In Python, the `AssistantMessage` dataclass exposes the same data directly via `message.usage` and `message.message_id`. When Claude uses multiple tools in one turn, all messages in that turn share the same ID, so deduplicate by ID to avoid double-counting.

2

The result message provides the authoritative total

When the `query()` call completes, the SDK emits a result message with `total_cost_usd` and cumulative `usage`. This is available in both TypeScript ([`SDKResultMessage`](/docs/en/agent-sdk/typescript#sdk-result-message)) and Python ([`ResultMessage`](/docs/en/agent-sdk/python#result-message)). If you make multiple `query()` calls (for example, in a multi-turn session), each result only reflects the cost of that individual call. If you only need the total cost, you can ignore the per-step usage and read this single value.

## [​](#get-the-total-cost-of-a-query) Get the total cost of a query

The result message ([TypeScript](/docs/en/agent-sdk/typescript#sdk-result-message), [Python](/docs/en/agent-sdk/python#result-message)) is the last message in every `query()` call. It includes `total_cost_usd`, the cumulative cost across all steps in that call. This works for both success and error results. If you use sessions to make multiple `query()` calls, each result only reflects the cost of that individual call.
The following examples iterate over the message stream from a `query()` call and print the total cost when the `result` message arrives:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({ prompt: "Summarize this project" })) {
 if (message.type === "result") {
 console.log(`Total cost: $${message.total_cost_usd}`);
 }
}
```

## [​](#track-per-step-and-per-model-usage) Track per-step and per-model usage

The examples in this section use TypeScript field names. In Python, the equivalent fields are [`AssistantMessage.usage`](/docs/en/agent-sdk/python#assistant-message) and `AssistantMessage.message_id` for per-step usage, and [`ResultMessage.model_usage`](/docs/en/agent-sdk/python#result-message) for per-model breakdowns.

### [​](#track-per-step-usage) Track per-step usage

Each assistant message contains a nested `BetaMessage` (accessed via `message.message`) with an `id` and `usage` object with token counts. When Claude uses tools in parallel, multiple messages share the same `id` with identical usage data. Track which IDs you’ve already counted and skip duplicates to avoid inflated totals.

Parallel tool calls produce multiple assistant messages whose nested `BetaMessage` shares the same `id` and identical usage. Always deduplicate by ID to get accurate per-step token counts.

The following example accumulates input and output tokens across all steps, counting each unique message ID only once:

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

const seenIds = new Set<string>();
let totalInputTokens = 0;
let totalOutputTokens = 0;

for await (const message of query({ prompt: "Summarize this project" })) {
 if (message.type === "assistant") {
 const msgId = message.message.id;

 // Parallel tool calls share the same ID, only count once
 if (!seenIds.has(msgId)) {
 seenIds.add(msgId);
 totalInputTokens += message.message.usage.input_tokens;
 totalOutputTokens += message.message.usage.output_tokens;
 }
 }
}

console.log(`Steps: ${seenIds.size}`);
console.log(`Input tokens: ${totalInputTokens}`);
console.log(`Output tokens: ${totalOutputTokens}`);
```

### [​](#break-down-usage-per-model) Break down usage per model

The result message includes [`modelUsage`](/docs/en/agent-sdk/typescript#model-usage), a map of model name to per-model token counts and cost. This is useful when you run multiple models (for example, Haiku for subagents and Opus for the main agent) and want to see where tokens are going.
The following example runs a query and prints the cost and token breakdown for each model used:

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({ prompt: "Summarize this project" })) {
 if (message.type !== "result") continue;

 for (const [modelName, usage] of Object.entries(message.modelUsage)) {
 console.log(`${modelName}: $${usage.costUSD.toFixed(4)}`);
 console.log(` Input tokens: ${usage.inputTokens}`);
 console.log(` Output tokens: ${usage.outputTokens}`);
 console.log(` Cache read: ${usage.cacheReadInputTokens}`);
 console.log(` Cache creation: ${usage.cacheCreationInputTokens}`);
 }
}
```

## [​](#accumulate-costs-across-multiple-calls) Accumulate costs across multiple calls

Each `query()` call returns its own `total_cost_usd`. The SDK does not provide a session-level total, so if your application makes multiple `query()` calls (for example, in a multi-turn session or across different users), accumulate the totals yourself.
The following examples run two `query()` calls sequentially, add each call’s `total_cost_usd` to a running total, and print both the per-call and combined cost:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

// Track cumulative cost across multiple query() calls
let totalSpend = 0;

const prompts = [
 "Read the files in src/ and summarize the architecture",
 "List all exported functions in src/auth.ts"
];

for (const prompt of prompts) {
 for await (const message of query({ prompt })) {
 if (message.type === "result") {
 totalSpend += message.total_cost_usd;
 console.log(`This call: $${message.total_cost_usd}`);
 }
 }
}

console.log(`Total spend: $${totalSpend.toFixed(4)}`);
```

## [​](#handle-errors-caching-and-token-discrepancies) Handle errors, caching, and token discrepancies

For accurate cost tracking, account for failed conversations, cache token pricing, and occasional reporting inconsistencies.

### [​](#resolve-output-token-discrepancies) Resolve output token discrepancies

In rare cases, you might observe different `output_tokens` values for messages with the same ID. When this occurs:

1. **Use the highest value:** the final message in a group typically contains the accurate total.
2. **Verify against total cost:** the `total_cost_usd` in the result message is authoritative.
3. **Report inconsistencies:** file issues at the [Claude Code GitHub repository](https://github.com/anthropics/claude-code/issues).

### [​](#track-costs-on-failed-conversations) Track costs on failed conversations

Both success and error result messages include `usage` and `total_cost_usd`. If a conversation fails mid-way, you still consumed tokens up to the point of failure. Always read cost data from the result message regardless of its `subtype`.

### [​](#track-cache-tokens) Track cache tokens

The Agent SDK automatically uses [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) to reduce costs on repeated content. You do not need to configure caching yourself. The usage object includes two additional fields for cache tracking:

- `cache_creation_input_tokens`: tokens used to create new cache entries (charged at a higher rate than standard input tokens).
- `cache_read_input_tokens`: tokens read from existing cache entries (charged at a reduced rate).

Track these separately from `input_tokens` to understand caching savings. In TypeScript, these fields are typed on the [`Usage`](/docs/en/agent-sdk/typescript#usage) object. In Python, they appear as keys in the [`ResultMessage.usage`](/docs/en/agent-sdk/python#result-message) dict (for example, `message.usage.get("cache_read_input_tokens", 0)`).

## [​](#related-documentation) Related documentation

- [TypeScript SDK Reference](/docs/en/agent-sdk/typescript) - Complete API documentation
- [SDK Overview](/docs/en/agent-sdk/overview) - Getting started with the SDK
- [SDK Permissions](/docs/en/agent-sdk/permissions) - Managing tool permissions

Was this page helpful?

YesNo

[Rewind file changes with checkpointing](/docs/en/agent-sdk/file-checkpointing)[Observability with OpenTelemetry](/docs/en/agent-sdk/observability)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.