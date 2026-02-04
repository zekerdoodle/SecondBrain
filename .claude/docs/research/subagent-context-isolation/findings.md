# Research: Preventing Subagents from Inheriting CLAUDE.md

**Research Date:** 2026-01-28
**Topic:** How to prevent subagents from inheriting project-level CLAUDE.md instructions in Claude Agent SDK

---

## Executive Summary

**Key Finding:** Subagents in the Claude Agent SDK **automatically get isolated context** and do NOT inherit CLAUDE.md by default. This is a core design feature, not something you need to configure.

The isolation is intentional and built into the architecture. Each subagent starts with a clean context window containing only:
1. Their own system prompt (from the `prompt` field in AgentDefinition or agent file)
2. The task delegation request from the parent agent
3. Access to tools they're explicitly granted

**Bottom line:** You don't need to "prevent" CLAUDE.md inheritance because it doesn't happen automatically. The challenge is the opposite - getting subagents to access shared context when needed.

---

## How Subagent Context Isolation Works

### Architecture

From the SDK documentation (https://platform.claude.com/docs/en/agent-sdk/subagents):

> "Subagents maintain separate context from the main agent, preventing information overload and keeping interactions focused."

> "Each subagent operates in its own context window separate from the main conversation."

**What this means:**
- Main agent has: CLAUDE.md + conversation history + tool results
- Subagent has: Only its own system prompt + delegation request + its own tool results
- No automatic inheritance of parent context

### Evidence from Documentation

1. **Separate Context Windows** (claude.com/docs):
   - "Each subagent operates in its own context, preventing pollution of the main conversation"
   - "Context preservation: keeping exploration and implementation out of your main conversation"

2. **Clean Slate Invocation** (Issue #4908):
   - "The subagent starts with a completely clean slate"
   - "This forces the subagent to perform redundant context-gathering steps that the parent agent may have already completed"
   - This is described as a **current limitation**, not a bug

3. **No CLAUDE.md Mention in Subagent Docs**:
   - The entire subagent documentation never mentions CLAUDE.md being inherited
   - All examples show subagents using only their `prompt` field for instructions

---

## What Subagents DO Inherit

According to the documentation, subagents inherit **only**:

1. **Permissions** (from parent conversation):
   - "Each inherits the parent conversation's permissions with additional tool restrictions"
   - Example: If parent approved file writes, subagent can write files (if tool is granted)

2. **Model Selection** (if configured):
   - Via `model: "inherit"` field in agent definition
   - Default is "inherit" if not specified
   - Bug report #5456 shows this doesn't always work reliably

**What they DON'T inherit:**
- CLAUDE.md contents
- Conversation history
- Files read by parent
- Parent's reasoning or context
- Any project-level configuration files

---

## The Real Problem: Avoiding CLAUDE.md Bloat in Subagents

Based on André Bremer's article (https://andrebremer.com/articles/keeping-your-claude-code-subagents-aligned/), the actual problem developers face is:

### Problem Statement
If you want subagents to follow project standards, you have four bad options:

1. **Put everything in CLAUDE.md** → All agents (main + subs) carry 650+ lines of context they don't need
2. **Duplicate in each subagent prompt** → Maintenance nightmare, drift issues
3. **Tell subagent to "read the docs"** → Unpredictable, may or may not work
4. **Hope for the best** → Inconsistent results

### The Solution: Selective Context Fetching

**Current Best Practice (as of 2026-01-28):**

Use MCP servers to let subagents fetch only what they need:

```markdown
# In subagent definition
---
name: task-creator
description: Creates tasks following project schema
---

Before creating tasks, fetch:
- §TASKS.1 (Required Fields)
- §TASKS.3 (Validation Rules)

[Rest of specialized instructions]
```

Then subagent calls: `mcp__policy-server__fetch_policies({"sections": ["§TASKS.1", "§TASKS.3"]})`

This gives you:
- **Precision:** Subagent gets exactly 2 sections, not 150 lines
- **No duplication:** Single source of truth in policy files
- **Automatic cross-references:** If §TASKS.1 references §TYPES.2, server fetches both
- **No CLAUDE.md bloat:** Main agent doesn't carry task schema unless needed

**Alternative (if you control the SDK):**

The feature request in Issue #4908 proposes adding `inherit_context` field:

```markdown
---
name: code-reviewer
inherit_context: [git_diff, relevant_files]
---
```

This is **not implemented** as of 2026-01-28. It's a feature request, not current functionality.

---

## Configuration Approaches

### 1. Programmatic Definition (SDK)

When defining subagents in code, context is controlled via the `prompt` field:

```python
from claude_agent_sdk import AgentDefinition

AgentDefinition(
    description="Code reviewer",
    prompt="""You are a code reviewer.

    # SUBAGENT CONTEXT
    You have a clean context window. Follow these rules:
    [specific rules here, not from CLAUDE.md]
    """,
    tools=["Read", "Grep", "Glob"]
)
```

**Key insight:** The `prompt` field IS the subagent's "CLAUDE.md" - it's their only system-level instruction.

### 2. Filesystem-Based Definition

Agent files in `.claude/agents/*.md`:

```markdown
---
name: research-agent
description: Deep research specialist
tools: Read, Grep, Glob
model: inherit
---

# SUBAGENT CONTEXT
You are starting with a clean context window.

[Specialized instructions here]
```

The heading "SUBAGENT CONTEXT" is a convention from the documentation examples - it's a signal that this is isolated context.

### 3. User-Level vs Project-Level

**Location matters for organization, NOT for context:**

- `~/.claude/agents/` → User-level (available in all projects)
- `.claude/agents/` → Project-level (only in this project)

**Both get the same context isolation.** The location just controls visibility/reusability.

---

## How CLAUDE.md Actually Works

From best practices documentation (https://www.anthropic.com/engineering/claude-code-best-practices):

> "CLAUDE.md is a special file that Claude automatically pulls into context when starting a conversation."

**Key word: "conversation"**

When you run `claude` in a terminal:
1. System reads `.claude/CLAUDE.md` and parent `CLAUDE.md` files
2. Injects contents as system context for the **main agent**
3. Main agent starts with this context

When main agent spawns a subagent via Task tool:
1. SDK creates new agent instance
2. Loads ONLY the subagent's `prompt` field
3. Passes delegation task as first user message
4. **Does not re-read CLAUDE.md**

**Why?** Because subagents are separate processes/contexts. They don't go through the same initialization flow.

---

## Evidence from Bug Reports

### Issue #5456: Model Inheritance Bug

Key quote:
> "Sub-agents default to Claude Sonnet 4 instead of inheriting the configured model (Claude Opus 4.1) from both global and local settings."

**What this tells us:**
- Subagents are separate enough that even model config doesn't inherit reliably
- They're truly isolated contexts, not just "forked" conversations
- Configuration that seems "obvious" (model, settings files) doesn't automatically flow through

### Issue #4908: Scoped Context Passing Request

Key quotes:
> "When a task is delegated, the subagent starts with a completely clean slate."

> "The subagent consumes time and tokens re-discovering information that was already available in the parent context."

> "The current implementation of this context isolation is 'all or nothing.'"

**What this tells us:**
- Isolation is intentional and complete
- Developers want SELECTIVE inheritance, not full inheritance
- The feature to pass scoped context doesn't exist yet (as of Aug 2025)

---

## Practical Recommendations

### For Your Use Case (Second Brain System)

Based on your CLAUDE.md showing orchestrator behavior and subagent delegation:

**1. Keep CLAUDE.md for Main Agent Only**

Your current CLAUDE.md is perfect for the main orchestrator:
- Memory system concepts
- Scheduling logic
- Delegation patterns
- High-level behavioral rules

**2. Create Focused Subagent Prompts**

For each subagent, write a self-contained prompt that includes ONLY what that agent needs:

```markdown
# .claude/agents/web-research.md
---
name: web-research
description: Research specialist for gathering web information
tools: Read, Write, mcp__brain__web_search, mcp__brain__page_parser
---

# Web Research Agent

You're a research specialist. Your purpose is to gather comprehensive, accurate
information and return it in a structured format.

## Your Capabilities
- web_search: Perplexity-powered search
- page_parser: Fetch full page content as clean markdown

## Research Philosophy
- Official sources > Aggregators
- Read more than seems necessary
- Verify event dates
- Save valuable artifacts

## What You Return
Structured findings with:
- What you found (with sources)
- What you couldn't find
- Any artifacts saved
```

**Key:** Notice it doesn't reference CLAUDE.md, memory system, or orchestrator concepts. It's self-contained.

**3. Use Explicit Instructions When Delegating**

When main agent delegates to subagent, pass context explicitly:

```
Use the web-research subagent to find official documentation on Claude Agent SDK
subagent configuration. Specifically look for:
1. How to exclude project-level instructions from subagents
2. Configuration options for controlling subagent context
3. Whether there's a way to specify custom instructions per subagent type

Save findings to /docs/research/subagent-context/
```

This gives the subagent:
- Clear task scope
- Specific deliverables
- Output location
- All without needing CLAUDE.md context

**4. For Shared Standards (Future)**

If you need multiple subagents to follow the same standards:

Option A: **MCP Policy Server** (if standards are complex)
- Create `policies/` directory with section markers
- Subagents fetch specific sections via MCP tool

Option B: **Shared Includes** (if standards are simple)
- Create `.claude/includes/coding-standards.md`
- Reference in each subagent's prompt: "Follow standards in @.claude/includes/coding-standards.md"
- Subagent uses Read tool to fetch when needed

Option C: **Duplicate Critical Rules** (if minimal)
- If only 5-10 lines of critical rules, just duplicate in each subagent prompt
- Easier to maintain than complex indirection

---

## Answers to Your Specific Questions

### 1. How do you prevent subagents from inheriting CLAUDE.md?

**Answer:** You don't need to. They don't inherit it by default. This is built-in behavior.

### 2. How to exclude project-level instructions from subagents?

**Answer:** They're already excluded. Subagents only see their own `prompt` field.

### 3. Configuration options for controlling what context subagents receive?

**Current options:**
- `prompt` field → The ONLY system instruction subagent gets
- `tools` field → What they can access
- `model` field → What model they use
- Delegation prompt → Task-specific context from parent

**Not available (feature request):**
- `inherit_context` field → Not implemented
- Automatic CLAUDE.md injection → Doesn't happen
- Scoped context passing → Not available yet

### 4. Is there a way to specify custom instructions per subagent type?

**Answer:** Yes, that's the entire purpose of the `prompt` field. Each subagent gets its own custom instructions, and that's ALL it gets.

---

## Implementation Patterns from the Wild

### Pattern 1: Self-Contained Specialists (Recommended)

Each subagent is completely independent:

```markdown
---
name: debugger
description: Debug specialist
tools: Read, Edit, Bash, Grep, Glob
---

You are an expert debugger specializing in root cause analysis.

When invoked:
1. Capture error message and stack trace
2. Identify reproduction steps
3. Isolate the failure location
4. Implement minimal fix
5. Verify solution works

[Full debugging process here - self-contained]
```

**Pros:**
- No dependencies on external context
- Predictable behavior
- Easy to share/version control
- Works across projects

**Cons:**
- Duplication if multiple agents need same rules
- Longer prompt if complex domain

### Pattern 2: Just-In-Time Context Fetching

Subagent explicitly reads what it needs:

```markdown
---
name: api-developer
description: API endpoint developer
tools: Read, Write, Bash
---

You implement API endpoints following team conventions.

On first invocation:
1. Read @.claude/includes/api-standards.md for conventions
2. Read @docs/error-handling.md for error patterns
3. Then proceed with implementation

[Rest of implementation logic]
```

**Pros:**
- Subagent gets fresh data (not stale CLAUDE.md)
- Explicit about dependencies
- Centralized source of truth

**Cons:**
- Subagent must "remember" to read files
- Extra tool calls (token cost)
- May read entire file when only needs section

### Pattern 3: MCP-Based Selective Fetching (Advanced)

Use MCP server for precise context:

```markdown
---
name: task-creator
description: Creates tasks following schema
tools: Bash, Read, Write, mcp__policy-server__fetch_policies
---

You create tasks following project schema.

Before creating any task:
1. Call fetch_policies with sections: ["§TASKS.1", "§TASKS.3"]
2. Follow the returned schema exactly
3. Validate output matches rules

[Rest of task creation logic]
```

**Pros:**
- Precision (only needed sections)
- Automatic cross-reference resolution
- Single source of truth
- No duplication

**Cons:**
- Requires MCP server setup
- Additional dependency
- More complex architecture

---

## Common Misconceptions

### Myth 1: "Subagents inherit CLAUDE.md"
**Reality:** No. They have completely separate context windows.

### Myth 2: "I need to configure isolation"
**Reality:** Isolation is default. You'd need to configure SHARING if you wanted it.

### Myth 3: "Project-level vs user-level affects context"
**Reality:** Location only affects visibility/reusability, not context injection.

### Myth 4: "Subagents can see parent's conversation"
**Reality:** No. They only see the delegation task, not the parent's history.

### Myth 5: "Settings files apply to subagents"
**Reality:** Some settings (like model) should but don't reliably (see bug #5456).

---

## Future Developments to Watch

### Proposed Features (Not Yet Implemented)

1. **Scoped Context Passing** (Issue #4908)
   - `inherit_context: [git_diff, user_prompt]` in frontmatter
   - Would allow selective inheritance
   - Status: Feature request, not implemented

2. **Context Bridges Between Agents** (Issue #5812)
   - Hooks to pass context from sub to parent
   - Status: Proposed

3. **Better Model Inheritance** (Issue #5456)
   - Reliable model config propagation
   - Status: Bug report, acknowledged

---

## Testing Context Isolation

To verify subagents don't inherit CLAUDE.md, you can test:

```python
# In CLAUDE.md, add a unique marker
SECRET_MARKER = "XYZZY_MAIN_AGENT_123"

# In subagent prompt
prompt="When you start, tell me if you can see the text XYZZY_MAIN_AGENT_123 anywhere in your context."

# Expected result: Subagent says "No, I don't see that text"
# If it says "Yes" → context is leaking (unexpected)
```

---

## Sources

### Official Documentation
- SDK Subagents Guide: https://platform.claude.com/docs/en/agent-sdk/subagents
- Claude Code Subagents: https://code.claude.com/docs/en/sub-agents
- Best Practices: https://www.anthropic.com/engineering/claude-code-best-practices

### Community Resources
- André Bremer's MCP Policy Server article: https://andrebremer.com/articles/keeping-your-claude-code-subagents-aligned/
- Comet API Guide: https://www.cometapi.com/how-to-create-and-use-subagents-in-claude-code/
- Rich Snapp Context Management: https://www.richsnapp.com/article/2025/10-05-context-management-with-subagents-in-claude-code

### Bug Reports & Feature Requests
- Issue #4908: Scoped Context Passing: https://github.com/anthropics/claude-code/issues/4908
- Issue #5456: Model Inheritance Bug: https://github.com/anthropics/claude-code/issues/5456
- Issue #5812: Context Bridges: https://github.com/anthropics/claude-code/issues/5812

---

## Conclusion

**The answer to "How do you prevent subagents from inheriting CLAUDE.md?" is: You don't need to do anything. They already don't inherit it.**

Context isolation is a core architectural feature of subagents. Each subagent starts with:
- Only their `prompt` field as system instruction
- The delegation task from parent
- Access to their allowed tools
- Nothing from CLAUDE.md or parent conversation

The challenge isn't preventing inheritance - it's efficiently giving subagents the specific context they DO need without bloating all contexts.

Current best practices:
1. Self-contained prompts for simple cases
2. Explicit file references for shared standards
3. MCP servers for complex selective context needs

Future improvements (not yet available):
- Scoped context passing via `inherit_context` field
- Better model/settings inheritance
- Context bridges between parent and child agents

For your second brain system, keep CLAUDE.md focused on the main orchestrator, and write each subagent as a self-contained specialist that knows nothing about your broader system architecture.
