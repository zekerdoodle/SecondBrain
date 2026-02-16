# Anthropic Agent SDK Documentation Mirror

This directory contains a local mirror of the [Anthropic Agent SDK documentation](https://platform.claude.com/docs/en/agent-sdk/).

## Purpose

This mirror enables Claude Code agents to reference up-to-date SDK documentation directly from the filesystem without needing to fetch from the web. This is especially useful for:

- Offline access to documentation
- Faster lookups without network latency
- Consistent documentation state during development sessions
- Reference material for building agents with the Claude Agent SDK

## Contents

The documentation covers:

- **Overview & Quickstart** - Getting started with the Agent SDK
- **Python SDK Reference** - Complete Python API reference
- **TypeScript SDK Reference** - Complete TypeScript API reference
- **Core Concepts:**
  - Sessions - Managing conversation sessions
  - Permissions - Controlling tool access
  - Hooks - Intercepting and modifying agent behavior
  - MCP - Model Context Protocol integration
  - Subagents - Delegating tasks to specialized agents
  - Custom Tools - Building your own tools
  - Structured Outputs - JSON schema validation
- **Deployment:**
  - Hosting - Container deployment patterns
  - Secure Deployment - Security hardening
  - Cost Tracking - Monitoring API usage

## For Claude Code Agents

When you need SDK reference material, check these files:

```
docs/anthropic_agent_sdk/
├── _index.md           # Overview and page listing
├── overview.md         # SDK overview and concepts
├── quickstart.md       # Getting started guide
├── python.md           # Python SDK reference
├── typescript.md       # TypeScript SDK reference
├── hooks.md            # Hooks system documentation
├── permissions.md      # Permission configuration
├── sessions.md         # Session management
├── mcp.md              # MCP server integration
├── subagents.md        # Subagent patterns
├── custom-tools.md     # Custom tool development
└── ...                 # Additional reference pages
```

## Sync Schedule

The documentation is automatically synced daily at 4:00 AM via a scheduled task. You can also manually trigger a sync:

```bash
python scripts/sync_anthropic_docs.py
```

## Sync Metadata

Check `_last_sync.json` for:
- When the last sync occurred
- Which pages were updated
- Any sync errors

## Note

This is a mirror of the official documentation. For the most authoritative and up-to-date information, refer to the source at https://platform.claude.com/docs/en/agent-sdk/.
