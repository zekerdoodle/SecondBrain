---
source: https://platform.claude.com/docs/en/agent-sdk/mcp
title: Connect to external tools with MCP
last_fetched: 2026-05-13T13:03:01.357436+00:00
---

[Claude Code Docs home page![light logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/light.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=78fd01ff4f4340295a4f66e2ea54903c)![dark logo](https://mintcdn.com/claude-code/c5r9_6tjPMzFdDDT/logo/dark.svg?fit=max&auto=format&n=c5r9_6tjPMzFdDDT&q=85&s=1298a0c3b3a1da603b190d0de0e31712)](/docs/en/overview)

![US](https://d3gk2c5xim1je2.cloudfront.net/flags/US.svg)

English

Search...

Ctrl KAsk AI

Search...

Navigation

Extend with tools

Connect to external tools with MCP

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

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io/docs/getting-started/intro) is an open standard for connecting AI agents to external tools and data sources. With MCP, your agent can query databases, integrate with APIs like Slack and GitHub, and connect to other services without writing custom tool implementations.
MCP servers can run as local processes, connect over HTTP, or execute directly within your SDK application.

This page covers MCP configuration for the Agent SDK. To add MCP servers to the Claude Code CLI so they load in every project, see [MCP installation scopes](/docs/en/mcp#mcp-installation-scopes).

## [​](#quickstart) Quickstart

This example connects to the [Claude Code documentation](https://code.claude.com/docs) MCP server using [HTTP transport](#httpsse-servers) and uses [`allowedTools`](#allow-mcp-tools) with a wildcard to permit all tools from the server.

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
 prompt: "Use the docs MCP server to explain what hooks are in Claude Code",
 options: {
 mcpServers: {
 "claude-code-docs": {
 type: "http",
 url: "https://code.claude.com/docs/mcp"
 }
 },
 allowedTools: ["mcp__claude-code-docs__*"]
 }
})) {
 if (message.type === "result" && message.subtype === "success") {
 console.log(message.result);
 }
}
```

The agent connects to the documentation server, searches for information about hooks, and returns the results.

## [​](#add-an-mcp-server) Add an MCP server

You can configure MCP servers in code when calling `query()`, or in a `.mcp.json` file loaded via [`settingSources`](#from-a-config-file).

### [​](#in-code) In code

Pass MCP servers directly in the `mcpServers` option:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
 prompt: "List files in my project",
 options: {
 mcpServers: {
 filesystem: {
 command: "npx",
 args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/projects"]
 }
 },
 allowedTools: ["mcp__filesystem__*"]
 }
})) {
 if (message.type === "result" && message.subtype === "success") {
 console.log(message.result);
 }
}
```

### [​](#from-a-config-file) From a config file

Create a `.mcp.json` file at your project root. The file is picked up when the `project` setting source is enabled, which it is for default `query()` options. If you set `settingSources` explicitly, include `"project"` for this file to load:

```shiki
{
 "mcpServers": {
 "filesystem": {
 "command": "npx",
 "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/projects"]
 }
 }
}
```

## [​](#allow-mcp-tools) Allow MCP tools

MCP tools require explicit permission before Claude can use them. Without permission, Claude will see that tools are available but won’t be able to call them.

### [​](#tool-naming-convention) Tool naming convention

MCP tools follow the naming pattern `mcp__<server-name>__<tool-name>`. For example, a GitHub server named `"github"` with a `list_issues` tool becomes `mcp__github__list_issues`.

### [​](#grant-access-with-allowedtools) Grant access with allowedTools

Use `allowedTools` to specify which MCP tools Claude can use:

```shiki
const _ = {
 options: {
 mcpServers: {
 // your servers
 },
 allowedTools: [
 "mcp__github__*", // All tools from the github server
 "mcp__db__query", // Only the query tool from db server
 "mcp__slack__send_message" // Only send_message from slack server
 ]
 }
};
```

Wildcards (`*`) let you allow all tools from a server without listing each one individually.

**Prefer `allowedTools` over permission modes for MCP access.** `permissionMode: "acceptEdits"` does not auto-approve MCP tools (only file edits and filesystem Bash commands). `permissionMode: "bypassPermissions"` does auto-approve MCP tools but also disables all other safety prompts, which is broader than necessary. A wildcard in `allowedTools` grants exactly the MCP server you want and nothing more. See [Permission modes](/docs/en/agent-sdk/permissions#permission-modes) for a full comparison.

### [​](#discover-available-tools) Discover available tools

To see what tools an MCP server provides, check the server’s documentation or connect to the server and inspect the `system` init message:

```shiki
for await (const message of query({ prompt: "...", options })) {
 if (message.type === "system" && message.subtype === "init") {
 console.log("Available MCP tools:", message.mcp_servers);
 }
}
```

## [​](#transport-types) Transport types

MCP servers communicate with your agent using different transport protocols. Check the server’s documentation to see which transport it supports:

- If the docs give you a **command to run** (like `npx @modelcontextprotocol/server-github`), use stdio
- If the docs give you a **URL**, use HTTP or SSE
- If you’re building your own tools in code, use an SDK MCP server

### [​](#stdio-servers) stdio servers

Local processes that communicate via stdin/stdout. Use this for MCP servers you run on the same machine:

- In code
- .mcp.json

TypeScript

Python

```shiki
const _ = {
 options: {
 mcpServers: {
 github: {
 command: "npx",
 args: ["-y", "@modelcontextprotocol/server-github"],
 env: {
 GITHUB_TOKEN: process.env.GITHUB_TOKEN
 }
 }
 },
 allowedTools: ["mcp__github__list_issues", "mcp__github__search_issues"]
 }
};
```

```shiki
{
 "mcpServers": {
 "github": {
 "command": "npx",
 "args": ["-y", "@modelcontextprotocol/server-github"],
 "env": {
 "GITHUB_TOKEN": "${GITHUB_TOKEN}"
 }
 }
 }
}
```

### [​](#http/sse-servers) HTTP/SSE servers

Use HTTP or SSE for cloud-hosted MCP servers and remote APIs:

- In code
- .mcp.json

TypeScript

Python

```shiki
const _ = {
 options: {
 mcpServers: {
 "remote-api": {
 type: "sse",
 url: "https://api.example.com/mcp/sse",
 headers: {
 Authorization: `Bearer ${process.env.API_TOKEN}`
 }
 }
 },
 allowedTools: ["mcp__remote-api__*"]
 }
};
```

```shiki
{
 "mcpServers": {
 "remote-api": {
 "type": "sse",
 "url": "https://api.example.com/mcp/sse",
 "headers": {
 "Authorization": "Bearer ${API_TOKEN}"
 }
 }
 }
}
```

For the streamable HTTP transport, use `"type": "http"` instead. In `.mcp.json` and other JSON config files, `"streamable-http"` is accepted as an alias for `"http"`. The programmatic `mcpServers` option accepts only `"http"`.

### [​](#sdk-mcp-servers) SDK MCP servers

Define custom tools directly in your application code instead of running a separate server process. See the [custom tools guide](/docs/en/agent-sdk/custom-tools) for implementation details.

## [​](#mcp-tool-search) MCP tool search

When you have many MCP tools configured, tool definitions can consume a significant portion of your context window. Tool search solves this by withholding tool definitions from context and loading only the ones Claude needs for each turn.
Tool search is enabled by default. See [Tool search](/docs/en/agent-sdk/tool-search) for configuration options and details.
For more detail, including best practices and using tool search with custom SDK tools, see the [tool search guide](/docs/en/agent-sdk/tool-search).

## [​](#authentication) Authentication

Most MCP servers require authentication to access external services. Pass credentials through environment variables in the server configuration.

### [​](#pass-credentials-via-environment-variables) Pass credentials via environment variables

Use the `env` field to pass API keys, tokens, and other credentials to the MCP server:

- In code
- .mcp.json

TypeScript

Python

```shiki
const _ = {
 options: {
 mcpServers: {
 github: {
 command: "npx",
 args: ["-y", "@modelcontextprotocol/server-github"],
 env: {
 GITHUB_TOKEN: process.env.GITHUB_TOKEN
 }
 }
 },
 allowedTools: ["mcp__github__list_issues"]
 }
};
```

```shiki
{
 "mcpServers": {
 "github": {
 "command": "npx",
 "args": ["-y", "@modelcontextprotocol/server-github"],
 "env": {
 "GITHUB_TOKEN": "${GITHUB_TOKEN}"
 }
 }
 }
}
```

The `${GITHUB_TOKEN}` syntax expands environment variables at runtime.

See [List issues from a repository](#list-issues-from-a-repository) for a complete working example with debug logging.

### [​](#http-headers-for-remote-servers) HTTP headers for remote servers

For HTTP and SSE servers, pass authentication headers directly in the server configuration:

- In code
- .mcp.json

TypeScript

Python

```shiki
const _ = {
 options: {
 mcpServers: {
 "secure-api": {
 type: "http",
 url: "https://api.example.com/mcp",
 headers: {
 Authorization: `Bearer ${process.env.API_TOKEN}`
 }
 }
 },
 allowedTools: ["mcp__secure-api__*"]
 }
};
```

```shiki
{
 "mcpServers": {
 "secure-api": {
 "type": "http",
 "url": "https://api.example.com/mcp",
 "headers": {
 "Authorization": "Bearer ${API_TOKEN}"
 }
 }
 }
}
```

The `${API_TOKEN}` syntax expands environment variables at runtime.

### [​](#oauth2-authentication) OAuth2 authentication

The [MCP specification supports OAuth 2.1](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization) for authorization. The SDK doesn’t handle OAuth flows automatically, but you can pass access tokens via headers after completing the OAuth flow in your application:

TypeScript

Python

```shiki
// After completing OAuth flow in your app
const accessToken = await getAccessTokenFromOAuthFlow();

const options = {
 mcpServers: {
 "oauth-api": {
 type: "http",
 url: "https://api.example.com/mcp",
 headers: {
 Authorization: `Bearer ${accessToken}`
 }
 }
 },
 allowedTools: ["mcp__oauth-api__*"]
};
```

## [​](#examples) Examples

### [​](#list-issues-from-a-repository) List issues from a repository

This example connects to the [GitHub MCP server](https://github.com/modelcontextprotocol/servers/tree/main/src/github) to list recent issues. The example includes debug logging to verify the MCP connection and tool calls.
Before running, create a [GitHub personal access token](https://github.com/settings/tokens) with `repo` scope and set it as an environment variable:

```shiki
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
 prompt: "List the 3 most recent issues in anthropics/claude-code",
 options: {
 mcpServers: {
 github: {
 command: "npx",
 args: ["-y", "@modelcontextprotocol/server-github"],
 env: {
 GITHUB_TOKEN: process.env.GITHUB_TOKEN
 }
 }
 },
 allowedTools: ["mcp__github__list_issues"]
 }
})) {
 // Verify MCP server connected successfully
 if (message.type === "system" && message.subtype === "init") {
 console.log("MCP servers:", message.mcp_servers);
 }

 // Log when Claude calls an MCP tool
 if (message.type === "assistant") {
 for (const block of message.message.content) {
 if (block.type === "tool_use" && block.name.startsWith("mcp__")) {
 console.log("MCP tool called:", block.name);
 }
 }
 }

 // Print the final result
 if (message.type === "result" && message.subtype === "success") {
 console.log(message.result);
 }
}
```

### [​](#query-a-database) Query a database

This example uses the [Postgres MCP server](https://github.com/modelcontextprotocol/servers/tree/main/src/postgres) to query a database. The connection string is passed as an argument to the server. The agent automatically discovers the database schema, writes the SQL query, and returns the results:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

// Connection string from environment variable
const connectionString = process.env.DATABASE_URL;

for await (const message of query({
 // Natural language query - Claude writes the SQL
 prompt: "How many users signed up last week? Break it down by day.",
 options: {
 mcpServers: {
 postgres: {
 command: "npx",
 // Pass connection string as argument to the server
 args: ["-y", "@modelcontextprotocol/server-postgres", connectionString]
 }
 },
 // Allow only read queries, not writes
 allowedTools: ["mcp__postgres__query"]
 }
})) {
 if (message.type === "result" && message.subtype === "success") {
 console.log(message.result);
 }
}
```

## [​](#error-handling) Error handling

MCP servers can fail to connect for various reasons: the server process might not be installed, credentials might be invalid, or a remote server might be unreachable.
The SDK emits a `system` message with subtype `init` at the start of each query. This message includes the connection status for each MCP server. Check the `status` field to detect connection failures before the agent starts working:

TypeScript

Python

```shiki
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
 prompt: "Process data",
 options: {
 mcpServers: {
 "data-processor": dataServer
 }
 }
})) {
 if (message.type === "system" && message.subtype === "init") {
 const failedServers = message.mcp_servers.filter((s) => s.status !== "connected");

 if (failedServers.length > 0) {
 console.warn("Failed to connect:", failedServers);
 }
 }

 if (message.type === "result" && message.subtype === "error_during_execution") {
 console.error("Execution failed");
 }
}
```

## [​](#troubleshooting) Troubleshooting

### [​](#server-shows-“failed”-status) Server shows “failed” status

Check the `init` message to see which servers failed to connect:

```shiki
if (message.type === "system" && message.subtype === "init") {
 for (const server of message.mcp_servers) {
 if (server.status === "failed") {
 console.error(`Server ${server.name} failed to connect`);
 }
 }
}
```

Common causes:

- **Missing environment variables**: Ensure required tokens and credentials are set. For stdio servers, check the `env` field matches what the server expects.
- **Server not installed**: For `npx` commands, verify the package exists and Node.js is in your PATH.
- **Invalid connection string**: For database servers, verify the connection string format and that the database is accessible.
- **Network issues**: For remote HTTP/SSE servers, check the URL is reachable and any firewalls allow the connection.

### [​](#tools-not-being-called) Tools not being called

If Claude sees tools but doesn’t use them, check that you’ve granted permission with `allowedTools`:

```shiki
const _ = {
 options: {
 mcpServers: {
 // your servers
 },
 allowedTools: ["mcp__servername__*"] // Required for Claude to use the tools
 }
};
```

### [​](#connection-timeouts) Connection timeouts

The MCP SDK has a default timeout of 60 seconds for server connections. If your server takes longer to start, the connection will fail. For servers that need more startup time, consider:

- Using a lighter-weight server if available
- Pre-warming the server before starting your agent
- Checking server logs for slow initialization causes

## [​](#related-resources) Related resources

- **[Custom tools guide](/docs/en/agent-sdk/custom-tools)**: Build your own MCP server that runs in-process with your SDK application
- **[Permissions](/docs/en/agent-sdk/permissions)**: Control which MCP tools your agent can use with `allowedTools` and `disallowedTools`
- **[TypeScript SDK reference](/docs/en/agent-sdk/typescript)**: Full API reference including MCP configuration options
- **[Python SDK reference](/docs/en/agent-sdk/python)**: Full API reference including MCP configuration options
- **[MCP server directory](https://github.com/modelcontextprotocol/servers)**: Browse available MCP servers for databases, APIs, and more

Was this page helpful?

YesNo

[Give Claude custom tools](/docs/en/agent-sdk/custom-tools)[Scale to many tools with tool search](/docs/en/agent-sdk/tool-search)

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.