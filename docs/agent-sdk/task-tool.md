# Task Tool and Subagent Patterns

## What is the Task Tool?

The Task tool is how Claude spawns subagents - separate agent instances that handle focused subtasks with their own isolated context windows. When you enable the Task tool, Claude can delegate work to specialized subagents automatically.

## Why Use Subagents?

### Context Isolation

Each subagent operates in its own 200k token context window. This prevents:
- Main conversation pollution with verbose exploration
- Loss of focus from tangential research
- Context overflow from high-volume operations

**Example**: A research subagent can explore dozens of files and documentation pages, but only returns the relevant findings to the main conversation, not all the intermediate searches.

### Specialization

Subagents can have:
- Custom system prompts for domain expertise
- Restricted tool access for safety
- Different models (e.g., Haiku for speed, Opus for complexity)
- Specific skills and knowledge bases

**Example**: A security-scanner subagent with expertise in OWASP Top 10, restricted to read-only tools, using detailed security prompts that would clutter the main agent.

### Parallelization

Multiple subagents can run concurrently:
- Up to 10 subagents execute simultaneously
- Additional subagents queue and run in batches
- Dramatically speeds up multi-part workflows

**Example**: During code review, run style-checker, security-scanner, and test-coverage subagents in parallel, reducing review time from minutes to seconds.

### Cost Control

Route tasks to appropriate models:
- Use Haiku for simple file searches
- Use Sonnet for general coding tasks
- Use Opus only for complex architecture decisions

**Example**: A codebase exploration subagent using Haiku saves costs on the high-volume file reading, while the main implementation agent uses Sonnet for reasoning.

## Enabling Subagents

Include `"Task"` in your `allowed_tools`:

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for message in query(
    prompt="Review this codebase for security issues",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Grep", "Glob", "Task"]
    )
):
    if hasattr(message, "result"):
        print(message.result)
```

With the Task tool enabled, Claude can:
1. Spawn the built-in `general-purpose` subagent automatically
2. Use any custom subagents you define
3. Decide when delegation makes sense based on the task

## Built-in Subagents

### Explore

**Purpose**: Fast file discovery and code search

**Configuration**:
- Model: Haiku (low latency)
- Tools: Read-only (Read, Grep, Glob, Bash for read operations)
- Denied: Write, Edit

**When Claude Uses It**: Automatically when exploring unfamiliar codebases or searching for specific code patterns.

**Example Use**:
```
"Find all API endpoints in this codebase"
"Search for authentication-related code"
"Locate the database connection logic"
```

### Plan

**Purpose**: Research and planning before implementation

**Configuration**:
- Model: Inherits from main conversation
- Tools: Read-only (Read, Grep, Glob, Bash for read operations)
- Denied: Write, Edit

**When Claude Uses It**: For planning complex changes, understanding architecture before modifications.

**Example Use**:
```
"Plan how to add authentication to this app"
"Analyze the current architecture before refactoring"
```

### General-Purpose

**Purpose**: Complex operations requiring full tool access

**Configuration**:
- Model: Inherits from main conversation
- Tools: All available tools
- No restrictions

**When Claude Uses It**: For complex, multi-step operations that benefit from context isolation.

**Example Use**:
```
"Research authentication best practices and implement OAuth"
"Set up a complete testing framework with examples"
```

## Creating Custom Subagents

### Programmatic Definition

Define subagents in code using the `agents` parameter:

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

async for message in query(
    prompt="Review the authentication module",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Grep", "Glob", "Task"],
        agents={
            "code-reviewer": AgentDefinition(
                description="Expert code reviewer for security and quality",
                prompt="""You are a senior code reviewer specializing in security.

When reviewing code:
- Identify security vulnerabilities
- Check for proper error handling
- Verify input validation
- Look for hardcoded secrets
- Assess authentication/authorization logic

Provide specific, actionable feedback with code examples.""",
                tools=["Read", "Grep", "Glob"],  # Read-only
                model="sonnet"
            ),

            "test-runner": AgentDefinition(
                description="Runs test suites and analyzes results",
                prompt="""You are a test execution specialist.

Your workflow:
1. Find and run test commands
2. Analyze test output for failures
3. Provide clear summary of results
4. Suggest fixes for failing tests""",
                tools=["Bash", "Read", "Grep"],
                model="inherit"  # Use same as main
            )
        }
    )
):
    # Process messages
    pass
```

### AgentDefinition Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | ✓ | Explains when to use this subagent (Claude reads this) |
| `prompt` | string | ✓ | System prompt defining subagent behavior |
| `tools` | string[] | - | Allowed tools (omit to inherit all available) |
| `model` | string | - | Model to use: "sonnet", "opus", "haiku", "inherit" |
| `permission_mode` | string | - | Permission mode for this subagent |
| `skills` | string[] | - | Preload specific skills into context |
| `hooks` | dict | - | Custom hooks for this subagent |

**Important**: Subagents cannot spawn their own subagents. Don't include "Task" in a subagent's tools array.

### File-Based Definition

Create `.claude/agents/agent-name.md` files:

```markdown
---
name: code-reviewer
description: Expert code reviewer for security and quality
tools: Read, Grep, Glob
model: sonnet
---

You are a senior code reviewer specializing in security and best practices.

When reviewing code:
- Identify security vulnerabilities
- Check for performance issues
- Verify adherence to coding standards
- Suggest specific improvements

Be thorough but concise in your feedback.
```

**Precedence**: Programmatically defined agents override file-based agents with the same name.

**Scopes**:
- Project: `.claude/agents/` (checked into version control)
- User: `~/.claude/agents/` (personal agents)

## Invoking Subagents

### Automatic Delegation

Claude automatically decides when to delegate based on:
1. The task description in your prompt
2. Each subagent's `description` field
3. Current context and conversation state

**Tips for better automatic delegation**:
- Write clear, specific subagent descriptions
- Mention capabilities in description ("security review", "test execution")
- Include when to use ("Use proactively after code changes")

**Example**:
```python
# Claude automatically uses code-reviewer subagent
async for message in query(
    prompt="Review the authentication code for security issues",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Grep", "Glob", "Task"],
        agents={
            "code-reviewer": AgentDefinition(
                description="Security code reviewer. Use for identifying vulnerabilities.",
                # ... rest of definition
            )
        }
    )
):
    pass
```

### Explicit Invocation

Mention the subagent by name in your prompt to guarantee delegation:

```python
prompt="Use the code-reviewer subagent to analyze auth.py"
prompt="Have the test-runner subagent execute all unit tests"
prompt="Ask the security-scanner to check for SQL injection"
```

This bypasses automatic matching and directly invokes the named subagent.

## Foreground vs Background Execution

### Foreground Subagents (Default)

**Behavior**:
- Blocks main conversation until complete
- Permission prompts pass through to user
- Can ask clarifying questions via `AskUserQuestion`
- Full MCP tool access

**When to Use**:
- Interactive workflows requiring user input
- Tasks that need clarifying questions
- When you want to see progress in real-time

**Example**:
```python
# Runs in foreground by default
prompt="Use the debugger subagent to fix the failing test"
```

### Background Subagents

**Behavior**:
- Runs concurrently with main conversation
- Pre-approved permissions (prompts for needed permissions upfront)
- Cannot ask questions (AskUserQuestion auto-denies)
- No MCP tool access
- Returns summary when complete

**When to Use**:
- Long-running analysis that doesn't need interaction
- Parallel research tasks
- When you want to continue working while subagent runs

**How to Background**:

**Option 1 - Ask in prompt**:
```python
prompt="Run the code-reviewer in the background while I continue working"
```

**Option 2 - Press Ctrl+B** (in Claude Code CLI):
When Claude spawns a subagent, press `Ctrl+B` to move it to background.

**Setting Environment Variable**:
```bash
export CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1  # Disable background execution
```

## Detecting Subagent Invocation

### In Message Stream

Subagents are invoked via the Task tool. Detect by checking for `tool_use` blocks:

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for message in query(
    prompt="Use the code-reviewer to check this code",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Grep", "Task"],
        agents={"code-reviewer": my_agent_def}
    )
):
    # Check for subagent invocation
    if hasattr(message, 'content') and message.content:
        for block in message.content:
            if getattr(block, 'type', None) == 'tool_use' and block.name == 'Task':
                subagent_type = block.input.get('subagent_type')
                print(f"Subagent invoked: {subagent_type}")

    # Check if message is from within subagent
    if hasattr(message, 'parent_tool_use_id') and message.parent_tool_use_id:
        print("(message from within subagent)")
```

### TypeScript Example

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Use code-reviewer to check auth.py",
  options: {
    allowedTools: ["Read", "Grep", "Task"],
    agents: {
      "code-reviewer": myAgentDef
    }
  }
})) {
  // Detect subagent invocation
  if ('message' in message && message.message.content) {
    for (const block of message.message.content) {
      if ('type' in block && block.type === 'tool_use' && block.name === 'Task') {
        console.log(`Subagent invoked: ${block.input.subagent_type}`);
      }
    }
  }
}
```

## Subagent Context Management

### Separate Context Windows

Each subagent has its own 200k token context window:
- Main conversation: 200k tokens
- Subagent A: 200k tokens (isolated)
- Subagent B: 200k tokens (isolated)

**Overhead**: Starting a subagent costs approximately 20k tokens for initialization.

### Context Compaction

Subagents auto-compact independently:
- Triggered at configurable threshold (default 80%)
- Summarizes older messages
- Preserves recent interactions

**Configure threshold**:
```bash
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=50  # Compact at 50%
```

### Resuming Subagents

Subagents can be resumed to continue previous work:

**Step 1 - Capture session and agent IDs**:

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

let sessionId: string | undefined;
let agentId: string | undefined;

// Helper to extract agentId from content
function extractAgentId(message: SDKMessage): string | undefined {
  if (!('message' in message)) return undefined;
  const content = JSON.stringify(message.message.content);
  const match = content.match(/agentId:\s*([a-f0-9-]+)/);
  return match?.[1];
}

// First invocation
for await (const message of query({
  prompt: "Use Explore agent to find API endpoints",
  options: { allowedTools: ['Read', 'Grep', 'Glob', 'Task'] }
})) {
  // Capture session ID
  if ('session_id' in message) sessionId = message.session_id;

  // Extract agent ID from content
  const extractedId = extractAgentId(message);
  if (extractedId) agentId = extractedId;

  if ('result' in message) console.log(message.result);
}
```

**Step 2 - Resume with follow-up**:

```typescript
// Resume session and continue with same subagent
if (agentId && sessionId) {
  for await (const message of query({
    prompt: `Resume agent ${agentId} and analyze the top 3 most complex endpoints`,
    options: {
      allowedTools: ['Read', 'Grep', 'Glob', 'Task'],
      resume: sessionId  // Must resume same session
    }
  })) {
    if ('result' in message) console.log(message.result);
  }
}
```

**Important Notes**:
- You must resume the same session to access the subagent's context
- Each `query()` call starts a new session by default
- Pass custom agents definition in both queries if using custom (non-built-in) agents
- Subagent transcripts persist within their session

### Transcript Persistence

Subagent transcripts are stored separately:

**Location**: `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl`

**Lifecycle**:
- Persists across Claude Code restarts
- Survives main conversation compaction
- Cleaned up after `cleanupPeriodDays` (default: 30 days)
- Can be resumed within same session

## Common Patterns

### Pattern 1: Parallel Research

Run multiple research subagents concurrently:

```python
prompt = """
Research these three areas in parallel using separate subagents:
1. Authentication implementation
2. Database schema design
3. API endpoint structure

Return a summary of findings from each area.
"""
```

**Result**: Three Explore subagents run simultaneously, each exploring different parts of the codebase.

### Pattern 2: Pipeline Workflow

Chain subagents for sequential processing:

```python
prompt = """
1. Use the code-reviewer to identify security issues
2. Use the optimizer to improve performance
3. Use the test-runner to verify all tests pass
"""
```

Each subagent completes before the next starts, building on previous results.

### Pattern 3: Isolate High-Volume Operations

Use subagents for verbose operations:

```python
prompt = "Use a subagent to run the entire test suite and report only failing tests"
```

**Benefit**: The main conversation doesn't get filled with thousands of lines of test output.

### Pattern 4: Read-Only Analysis

Create read-only subagents for safe exploration:

```python
agents={
    "safe-analyzer": AgentDefinition(
        description="Read-only code analyzer",
        prompt="Analyze code structure and patterns. You cannot modify files.",
        tools=["Read", "Grep", "Glob"],  # No Write or Edit
        model="haiku"  # Fast and cheap for analysis
    )
}
```

### Pattern 5: Domain Experts

Create specialized subagents for different domains:

```python
agents={
    "security-expert": AgentDefinition(
        description="Security vulnerability scanner",
        prompt="Check for OWASP Top 10 vulnerabilities...",
        tools=["Read", "Grep", "Glob"],
        model="opus"  # Use best model for security
    ),
    "performance-expert": AgentDefinition(
        description="Performance optimization specialist",
        prompt="Identify performance bottlenecks...",
        tools=["Read", "Bash"],
        model="sonnet"
    ),
    "style-checker": AgentDefinition(
        description="Code style and formatting checker",
        prompt="Check code style compliance...",
        tools=["Read", "Bash"],
        model="haiku"  # Simple task, use fast model
    )
}
```

## Dynamic Agent Configuration

Create agents dynamically based on runtime conditions:

```python
def create_reviewer_agent(strictness_level: str) -> AgentDefinition:
    """Factory function for creating security reviewers with variable strictness."""
    is_strict = strictness_level == "strict"

    return AgentDefinition(
        description="Security code reviewer",
        prompt=f"""You are a {'strict' if is_strict else 'balanced'} security reviewer.

{'Be extremely thorough and flag any potential issues.' if is_strict else
'Balance security with practicality.'}""",
        tools=["Read", "Grep", "Glob"],
        model="opus" if is_strict else "sonnet"  # Use stronger model when strict
    )

# Use in query
async for message in query(
    prompt="Review this code for security issues",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Grep", "Task"],
        agents={
            "security-reviewer": create_reviewer_agent("strict")
        }
    )
):
    pass
```

## When to Use Subagents vs Main Conversation

### Use Main Conversation When:
- Task needs frequent back-and-forth interaction
- Multiple phases share significant context
- Making quick, targeted changes
- Latency matters (subagents have startup cost)

### Use Subagents When:
- Task produces verbose output you don't need in main context
- Work is self-contained and can return a summary
- Want to enforce specific tool restrictions
- Can parallelize with other work
- Task requires different expertise/model

## Token Costs

Understanding token overhead:

**Subagent Startup**: ~20k tokens
- System prompt
- Tool descriptions
- Initial context loading

**Main Conversation**: Continues using its tokens normally

**Return to Main**: Subagent returns only summary, not full transcript

**Example Calculation**:
```
Main conversation: 50k tokens used
Spawn subagent: 20k tokens (startup overhead)
Subagent work: 30k tokens (in its own window)
Subagent return: 2k tokens (summary added to main)
---
Main conversation after: 52k tokens
Subagent total: 50k tokens (separate window)
```

**Cost Implications**:
- Each subagent invocation costs ~20k tokens minimum
- Parallelizing 3 subagents = 60k tokens overhead
- But saves main context from verbose exploration
- Usually worth it for high-volume operations

## Troubleshooting

### Claude Not Delegating to Subagents

**Problem**: Claude does tasks directly instead of using subagents.

**Solutions**:
1. Ensure `Task` is in `allowed_tools`
2. Write clear subagent descriptions
3. Explicitly mention subagent by name in prompt
4. Check subagent definition is loaded (programmatic vs file-based)

### Filesystem-Based Agents Not Loading

**Problem**: Agents in `.claude/agents/` not available.

**Solutions**:
- Restart Claude Code session (agents load at startup)
- Check file format (YAML frontmatter required)
- Verify file location (project vs user scope)
- Check for programmatic agent with same name (takes precedence)

### Windows Long Prompt Failures

**Problem**: Subagents fail on Windows with very long prompts.

**Cause**: Windows command line length limit (8191 characters).

**Solutions**:
- Keep subagent prompts concise
- Use file-based agents (loaded differently)
- Split into multiple shorter subagents

### Background Subagent Can't Ask Questions

**Problem**: Subagent fails when it needs user input.

**Cause**: Background subagents auto-deny `AskUserQuestion` tool.

**Solutions**:
- Run in foreground for interactive tasks
- Pre-approve all needed permissions before backgrounding
- Provide all necessary information in initial prompt

### Subagent Resume Failed

**Problem**: Can't resume subagent from previous session.

**Cause**:
- Different session (each `query()` starts new session)
- Session expired (cleanupPeriodDays setting)
- Agent ID not captured correctly

**Solutions**:
- Ensure you resume same session with `resume: sessionId`
- Capture and store `session_id` and `agentId`
- Check transcript file exists in expected location

## Best Practices

### 1. Design Focused Subagents
Each subagent should excel at one specific task. Avoid creating "do-everything" subagents.

### 2. Write Detailed Descriptions
Claude uses descriptions to decide when to delegate. Be specific about when to use each subagent.

### 3. Limit Tool Access
Grant only necessary permissions. Read-only subagents are safer and more focused.

### 4. Choose Appropriate Models
- Haiku: Fast file searches, formatting, simple tasks
- Sonnet: General coding, analysis, most tasks
- Opus: Complex architecture, security reviews, critical decisions

### 5. Test Automatic Delegation
Verify Claude delegates correctly by testing with various phrasings of prompts.

### 6. Version Control Subagents
Check project-level subagents into git to share with team.

### 7. Monitor Token Usage
Track token costs per subagent to optimize model selection and usage patterns.

### 8. Use Background for Non-Interactive Work
Background long-running analysis to keep working while it completes.

### 9. Provide Context in Subagent Prompts
Include necessary background knowledge in system prompts so subagents don't need to ask.

### 10. Handle Errors Gracefully
Subagents can fail. Main agent should handle subagent errors and retry or adjust approach.

## Examples

### Complete Code Reviewer

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

code_reviewer = AgentDefinition(
    description="""Expert code review specialist for quality, security, and maintainability.
    Use proactively after code changes or when reviewing existing code.""",

    prompt="""You are a senior code reviewer ensuring high standards.

When invoked:
1. Run git diff to see recent changes
2. Focus on modified files
3. Begin review immediately

Review checklist:
- Code clarity and readability
- Proper error handling
- No exposed secrets or credentials
- Input validation implemented
- Good test coverage
- Performance considerations

Provide feedback organized by priority:
- CRITICAL (must fix)
- WARNING (should fix)
- SUGGESTION (consider improving)

Include specific examples and code snippets for fixes.""",

    tools=["Read", "Grep", "Glob", "Bash"],
    model="sonnet"
)

async for message in query(
    prompt="Review the authentication module for any issues",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Grep", "Glob", "Task"],
        agents={"code-reviewer": code_reviewer}
    )
):
    if hasattr(message, "result"):
        print(message.result)
```

### Parallel Research Team

```python
research_agents = {
    "api-researcher": AgentDefinition(
        description="API and endpoint specialist",
        prompt="Find and document all API endpoints with their methods and routes.",
        tools=["Read", "Grep", "Glob"],
        model="haiku"
    ),
    "db-researcher": AgentDefinition(
        description="Database schema analyst",
        prompt="Analyze database schema, tables, relationships, and indexes.",
        tools=["Read", "Grep", "Glob"],
        model="haiku"
    ),
    "auth-researcher": AgentDefinition(
        description="Authentication flow specialist",
        prompt="Map out authentication and authorization implementation.",
        tools=["Read", "Grep", "Glob"],
        model="haiku"
    )
}

async for message in query(
    prompt="""Research this codebase using three parallel subagents:
    1. api-researcher for endpoint mapping
    2. db-researcher for schema analysis
    3. auth-researcher for auth flow

    Combine findings into comprehensive summary.""",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Grep", "Glob", "Task"],
        agents=research_agents
    )
):
    pass
```

## Further Reading

- **Subagents Guide (Claude Code)**: https://code.claude.com/docs/en/sub-agents
- **Agent SDK Subagents Docs**: https://platform.claude.com/docs/en/agent-sdk/subagents
- **When to Use Task Tool vs Subagents**: https://amitkoth.com/claude-code-task-tool-vs-subagents/
- **Awesome Claude Code Subagents**: https://github.com/VoltAgent/awesome-claude-code-subagents
