# Claude Agent SDK - Best Practices

## Core Principles

### 1. Give Claude a Computer

The fundamental design principle: provide Claude with the same tools developers use daily.

**Good**:
- Bash access for running commands
- File system access for reading/writing
- Git for version control
- Development servers for testing

**Bad**:
- Over-abstracted tools that hide details
- Restricted environments without essential tools
- Custom tools that reinvent standard utilities

**Why**: Claude works best when it can work like a human developer - reading files, running tests, debugging with actual tools.

### 2. Design for Autonomy with Control

Balance between autonomous operation and human oversight.

**Autonomy**:
- Let agents make routine decisions
- Auto-approve safe operations (read, search)
- Allow iterative problem-solving

**Control**:
- Require approval for destructive operations
- Use permission modes appropriately
- Implement hooks for validation
- Monitor critical operations

**Pattern**:
```python
ClaudeAgentOptions(
    allowed_tools=["Read", "Edit", "Bash", "Grep"],
    permission_mode="acceptEdits",  # Auto-approve edits
    hooks={
        "PreToolUse": [validate_bash_commands]  # Validate dangerous commands
    }
)
```

### 3. Context is King

Effective context management is critical for agent success.

**Strategies**:
- Use subagents to isolate verbose operations
- Leverage file system as persistent context
- Structure files for easy navigation
- Keep progress files updated
- Use git history as context

**Anti-patterns**:
- Reading entire large files when grep suffices
- Mixing unrelated tasks in one session
- Losing context with overly aggressive compaction

## Architecture Patterns

### Orchestrator Pattern

Main agent coordinates specialized subagents.

**Structure**:
```
Main Agent (Orchestrator)
├── Research Agent (read-only, gathers info)
├── Implementation Agent (writes code)
├── Testing Agent (runs tests, validates)
└── Review Agent (checks quality, security)
```

**Implementation**:
```python
orchestrator_agents = {
    "researcher": AgentDefinition(
        description="Research specialist for gathering context",
        prompt="Find and summarize relevant information without making changes.",
        tools=["Read", "Grep", "Glob", "WebSearch"],
        model="haiku"  # Fast for research
    ),
    "implementer": AgentDefinition(
        description="Implementation specialist for writing code",
        prompt="Implement features following best practices and patterns.",
        tools=["Read", "Edit", "Write", "Bash"],
        model="sonnet"  # Balanced for coding
    ),
    "tester": AgentDefinition(
        description="Testing specialist for validation",
        prompt="Run tests, identify failures, verify correctness.",
        tools=["Bash", "Read"],
        model="haiku"  # Fast for testing
    ),
    "reviewer": AgentDefinition(
        description="Quality and security reviewer",
        prompt="Review code for bugs, security issues, and best practices.",
        tools=["Read", "Grep", "Bash"],
        model="opus"  # Best for critical review
    )
}
```

**Benefits**:
- Clear separation of concerns
- Parallel execution where possible
- Specialized expertise per domain
- Isolated contexts prevent pollution

### Incremental Progress Pattern

Structure work for incremental, verifiable progress.

**Components**:

**1. Initializer Agent** (first session only):
```python
async def initialize_project(prompt):
    """Set up environment for long-running work."""
    async for message in query(
        prompt=f"""{prompt}

Before starting implementation:
1. Create feature list (features.json) with all requirements
2. Create progress file (claude-progress.txt)
3. Write init.sh script to start dev environment
4. Make initial git commit
5. Document the architecture plan""",
        options=ClaudeAgentOptions(
            allowed_tools=["Write", "Bash"],
            permission_mode="acceptEdits"
        )
    ):
        if message.type == 'system' and message.subtype == 'init':
            return message.session_id
```

**2. Coding Agent** (subsequent sessions):
```python
async def incremental_development(session_id):
    """Make incremental progress on next feature."""
    async for message in query(
        prompt="""Continue development workflow:
1. Run pwd and check current directory
2. Read claude-progress.txt to see what's done
3. Read git log to understand recent changes
4. Run init.sh to start dev server
5. Test that existing features still work
6. Read features.json for next failing test
7. Implement ONE feature completely
8. Run tests to verify it works
9. Commit changes with descriptive message
10. Update claude-progress.txt with what you did
11. Mark feature as passing in features.json""",
        options=ClaudeAgentOptions(
            resume=session_id,
            allowed_tools=["Read", "Edit", "Write", "Bash"],
            permission_mode="acceptEdits"
        )
    ):
        yield message
```

**Why This Works**:
- Each session has clear starting point
- Progress is documented for next session
- Work is bite-sized and verifiable
- Git history provides context
- Environment always in clean state

### Pipeline Pattern

Chain specialized agents for sequential processing.

**Example - Code Quality Pipeline**:
```python
async def quality_pipeline(filepath):
    """Run code through quality pipeline."""
    session_id = None

    # Step 1: Static analysis
    async for message in query(
        prompt=f"Use the analyzer subagent to check {filepath} for issues",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Grep", "Task"],
            agents={"analyzer": static_analyzer_agent}
        )
    ):
        if message.type == 'system':
            session_id = message.session_id

    # Step 2: Security scan
    async for message in query(
        prompt=f"Use the security subagent to scan {filepath}",
        options=ClaudeAgentOptions(
            resume=session_id,
            allowed_tools=["Read", "Grep", "Task"],
            agents={"security": security_scanner_agent}
        )
    ):
        pass

    # Step 3: Performance optimization
    async for message in query(
        prompt=f"Use the optimizer subagent to improve {filepath}",
        options=ClaudeAgentOptions(
            resume=session_id,
            allowed_tools=["Read", "Edit", "Task"],
            agents={"optimizer": optimizer_agent}
        )
    ):
        pass

    return session_id
```

### Parallel Execution Pattern

Run independent tasks concurrently.

```python
async def parallel_research(topic):
    """Research topic from multiple angles simultaneously."""
    tasks = [
        query(
            prompt=f"Research {topic} - focus on implementation details",
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "WebSearch", "Task"]
            )
        ),
        query(
            prompt=f"Research {topic} - focus on security concerns",
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "WebSearch", "Task"]
            )
        ),
        query(
            prompt=f"Research {topic} - focus on performance implications",
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "WebSearch", "Task"]
            )
        )
    ]

    # Run all in parallel
    results = await asyncio.gather(*tasks)
    return results
```

**Limits**: Maximum 10 concurrent subagents. Additional queue and run in batches.

## Tool Selection Best Practices

### Built-in Tool Combinations

**Read-only exploration**:
```python
allowed_tools=["Read", "Grep", "Glob"]
# Good for: Code review, analysis, research
```

**File editing**:
```python
allowed_tools=["Read", "Edit", "Glob"]
# Good for: Focused changes, refactoring
```

**Full development**:
```python
allowed_tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep"]
# Good for: Complete feature development
```

**Testing workflows**:
```python
allowed_tools=["Bash", "Read", "Grep"]
# Good for: Running tests, analyzing output
```

**Research with web access**:
```python
allowed_tools=["Read", "WebSearch", "PageParser", "Write"]
# Good for: Research, documentation gathering
```

### Tool Access Hierarchy

Progressively grant access based on trust:

**Level 1 - Read Only**:
```python
allowed_tools=["Read", "Grep", "Glob"]
permission_mode="default"
```

**Level 2 - File Modifications**:
```python
allowed_tools=["Read", "Edit", "Grep", "Glob"]
permission_mode="acceptEdits"
```

**Level 3 - Full Development**:
```python
allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"]
permission_mode="acceptEdits"
```

**Level 4 - Unrestricted** (use with caution):
```python
allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"]
permission_mode="bypassPermissions"  # Only in sandboxes!
```

### Tool-Specific Best Practices

**Bash Tool**:
- Use hooks to validate commands
- Block destructive operations (`rm -rf`, `sudo`, etc.)
- Limit network access if possible
- Log all command executions

**Edit Tool**:
- Prefer Edit over Write for changes (preserves file context)
- Use acceptEdits mode for faster iteration
- Review diffs before accepting in production

**WebSearch Tool**:
- Limit queries to prevent abuse
- Cache results when possible
- Set reasonable rate limits

**Task Tool**:
- Always include when using subagents
- Understand the ~20k token overhead per subagent
- Use background mode for non-interactive work

## Permission Management

### Permission Mode Selection

**Use `default` mode when**:
- Testing new agents
- Working with production systems
- Unsure about agent reliability

**Use `acceptEdits` mode when**:
- Rapid prototyping
- Working in isolated dev environment
- Trust agent's file operations

**Use `dontAsk` mode when**:
- Running read-only analysis
- No modifications needed
- Want faster execution

**Use `bypassPermissions` mode when**:
- Agents run in strict sandboxes only
- Fully automated workflows
- Trust is absolute (rare)

**Use `plan` mode when**:
- Want proposal before execution
- Code review scenario
- Teaching/demonstration

### Hook-Based Validation

Implement validation hooks for safety:

```python
def validate_bash_command(message):
    """Block dangerous bash commands."""
    if hasattr(message, 'tool_input'):
        command = message.tool_input.get('command', '')

        # Block dangerous patterns
        dangerous = ['rm -rf', 'sudo', '> /dev/', 'mkfs', 'dd if=']
        if any(pattern in command for pattern in dangerous):
            return {"action": "deny", "reason": "Dangerous command blocked"}

    return {"action": "continue"}

options = ClaudeAgentOptions(
    allowed_tools=["Bash"],
    hooks={"PreToolUse": [validate_bash_command]}
)
```

### Declarative Permission Rules

Use settings.json for team-wide policies:

```json
{
  "permissions": {
    "deny": [
      "Bash(command:rm -rf*)",
      "Bash(command:sudo*)",
      "Write(path:/etc/*)"
    ],
    "allow": [
      "Read(*)",
      "Grep(*)",
      "Edit(path:src/*)"
    ],
    "ask": [
      "Bash(*)",
      "Write(*)"
    ]
  }
}
```

## Subagent Best Practices

### When to Use Subagents

**DO use subagents for**:
- Parallel independent tasks
- High-volume operations (test runs, file searches)
- Specialized expertise (security, performance)
- Context isolation (experiments, research)

**DON'T use subagents for**:
- Quick single-file edits
- When latency matters (20k token overhead)
- Tasks requiring frequent interaction
- Shared context across tasks

### Subagent Design Principles

**1. Single Responsibility**:
```python
# Good
"code-reviewer": AgentDefinition(
    description="Reviews code for quality and security",
    ...
)

# Bad
"do-everything": AgentDefinition(
    description="Reviews, implements, tests, and deploys code",
    ...
)
```

**2. Clear Descriptions**:
```python
# Good
description="Security scanner. Use proactively after code changes to identify vulnerabilities like SQL injection, XSS, and auth issues."

# Bad
description="Reviews code"
```

**3. Minimal Tool Access**:
```python
# Good - read-only reviewer
tools=["Read", "Grep", "Glob"]

# Bad - reviewer can modify code
tools=["Read", "Edit", "Write", "Bash"]
```

**4. Appropriate Model Selection**:
```python
# Fast tasks
model="haiku"  # File searches, formatting

# Standard tasks
model="sonnet"  # Code implementation, analysis

# Complex tasks
model="opus"  # Architecture, security review
```

### Subagent Orchestration

**Parallel Pattern**:
```python
prompt = """
Research these areas in parallel:
1. Use the api-researcher to map all endpoints
2. Use the db-researcher to analyze schema
3. Use the auth-researcher to document auth flow

Combine findings into comprehensive summary.
"""
```

**Sequential Pattern**:
```python
prompt = """
Execute this pipeline:
1. Use the researcher to gather requirements
2. Use the implementer to write code
3. Use the tester to validate implementation
4. Use the reviewer to check quality
"""
```

**Conditional Pattern**:
```python
prompt = """
1. Use the analyzer to check code quality
2. If issues found, use the fixer to resolve them
3. If critical issues, escalate to human
4. Otherwise, use the deployer to ship changes
"""
```

## Context Engineering

### File System as Context

Structure your project for easy agent navigation:

**Progress Tracking**:
```
claude-progress.txt
---
2024-01-29 10:30 - Initialized project structure
2024-01-29 11:15 - Implemented user authentication
2024-01-29 14:20 - Added password validation
Next: Write tests for password validation
Known Issues: Email validation needs work
```

**Feature Lists**:
```json
// features.json
[
  {
    "category": "authentication",
    "description": "User can register with valid email",
    "passes": true,
    "implemented": "2024-01-29"
  },
  {
    "category": "authentication",
    "description": "Password must meet complexity requirements",
    "passes": false,
    "notes": "Need to implement validator"
  }
]
```

**Init Scripts**:
```bash
#!/bin/bash
# init.sh - Start development environment

# Start database
docker-compose up -d postgres

# Install dependencies
npm install

# Run migrations
npm run migrate

# Start dev server
npm run dev

echo "Environment ready at http://localhost:3000"
```

### Context Boundaries

Define clear boundaries for different concerns:

```
project/
├── claude-progress.txt        # Overall progress
├── init.sh                    # Environment setup
├── features.json              # Feature requirements
├── docs/
│   ├── architecture.md        # System design
│   └── decisions/             # ADRs
├── src/                       # Implementation
└── tests/                     # Tests
```

### Git as Context

Use git history effectively:

**Descriptive Commits**:
```bash
# Good
git commit -m "Add password strength validation with regex checks"

# Bad
git commit -m "Update auth"
```

**Agent Workflow**:
```python
prompt = """
Before making changes:
1. Run 'git log --oneline -20' to see recent work
2. Run 'git diff' to check uncommitted changes
3. Plan changes that build on existing work

After making changes:
1. Run tests to verify nothing broke
2. Git commit with descriptive message
3. Update claude-progress.txt
"""
```

## Testing and Verification

### Agent-Driven Testing

Prompt agents to verify their own work:

**End-to-End Testing**:
```python
prompt = """
After implementing the feature:
1. Start the application
2. Use browser automation to test as a user would
3. Verify each step works correctly
4. Take screenshots of working feature
5. Mark test as passing only if everything works
"""
```

**Unit Testing**:
```python
prompt = """
For each function you implement:
1. Write unit tests covering normal cases
2. Write tests for edge cases
3. Write tests for error conditions
4. Run tests and verify all pass
5. Achieve >80% coverage
"""
```

### Verification Strategies

**Rule-Based Verification**:
```python
prompt = """
Code must pass these checks:
1. No hardcoded credentials or API keys
2. All inputs are validated
3. Error handling for all external calls
4. Functions have docstrings
5. No TODO or FIXME comments

Run linter and fix all issues before proceeding.
"""
```

**Visual Verification**:
```python
# For UI work
options = ClaudeAgentOptions(
    allowed_tools=["Read", "Edit", "Write", "Bash", "Puppeteer"],
    mcp_servers={"puppeteer": puppeteer_config}
)

prompt = """
After making UI changes:
1. Start the application
2. Navigate to changed pages
3. Take screenshots
4. Verify layout matches requirements
5. Test interactive elements
"""
```

**Performance Verification**:
```python
prompt = """
After optimization:
1. Run performance benchmark
2. Compare before/after metrics
3. Verify no regressions
4. Document improvement percentage
"""
```

## Production Deployment

### Sandboxing Requirements

**Always run production agents in sandboxes**:

**Recommended providers**:
- Modal Sandbox
- E2B
- Cloudflare Sandboxes
- Fly Machines

**Sandbox configuration**:
```python
from modal import Sandbox

sandbox = Sandbox.create(
    "debian-slim",
    timeout=3600,  # 1 hour
    cpu=2,
    memory=2048,  # 2GB
    workdir="/workspace"
)

# Run agent inside sandbox
result = sandbox.exec(
    "python", "agent.py",
    env={"ANTHROPIC_API_KEY": api_key}
)
```

### Resource Management

**Set limits**:
```python
options = ClaudeAgentOptions(
    max_turns=50,  # Prevent infinite loops
    allowed_tools=["Read", "Edit", "Bash"],
    permission_mode="acceptEdits"
)
```

**Monitor usage**:
```python
token_usage = 0
tool_calls = 0

async for message in query(...):
    if hasattr(message, 'usage'):
        token_usage += message.usage.get('total_tokens', 0)

    if hasattr(message, 'content'):
        for block in message.content:
            if getattr(block, 'type', None) == 'tool_use':
                tool_calls += 1

print(f"Used {token_usage} tokens and {tool_calls} tool calls")
```

### Error Handling

**Graceful degradation**:
```python
async def resilient_agent(prompt, max_retries=3):
    """Agent with retry logic."""
    for attempt in range(max_retries):
        try:
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(...)
            ):
                if message.type == 'result':
                    if message.subtype == 'success':
                        return message.result
                    elif message.subtype == 'error_during_execution':
                        if attempt < max_retries - 1:
                            prompt = f"Previous attempt failed. Try again: {prompt}"
                            continue
                        else:
                            raise Exception("Max retries exceeded")
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
```

### Logging and Monitoring

**Comprehensive logging**:
```python
import logging

logger = logging.getLogger(__name__)

async for message in query(...):
    # Log tool uses
    if hasattr(message, 'content'):
        for block in message.content:
            if getattr(block, 'type', None) == 'tool_use':
                logger.info(f"Tool called: {block.name}", extra={
                    'tool': block.name,
                    'input': block.input
                })

    # Log results
    if message.type == 'result':
        logger.info(f"Session ended: {message.subtype}", extra={
            'session_id': message.session_id,
            'subtype': message.subtype
        })
```

## Common Anti-Patterns

### 1. God Agent

**Bad**:
```python
# One agent does everything
prompt = "Build a full e-commerce site with auth, payments, inventory, and analytics"
```

**Good**:
```python
# Orchestrator + specialized subagents
orchestrator_agents = {
    "auth-specialist": ...,
    "payment-specialist": ...,
    "inventory-specialist": ...,
    "analytics-specialist": ...
}
```

### 2. Context Pollution

**Bad**:
```python
# Main conversation filled with verbose output
prompt = "Search through all 1000 log files and find errors"
```

**Good**:
```python
# Use subagent to isolate verbose work
prompt = "Use the log-analyzer subagent to search logs and report only critical errors"
```

### 3. One-Shot Everything

**Bad**:
```python
# Try to do entire project in one turn
prompt = "Implement complete authentication system with OAuth, 2FA, and password reset"
```

**Good**:
```python
# Break into incremental steps
prompts = [
    "Implement basic username/password auth",
    "Add password hashing with bcrypt",
    "Add JWT token generation",
    "Implement password reset flow",
    "Add OAuth providers",
    "Implement 2FA with TOTP"
]
```

### 4. Ignoring Verification

**Bad**:
```python
# No verification that changes work
prompt = "Fix the bug and move on"
```

**Good**:
```python
# Verify changes work
prompt = """
Fix the bug by:
1. Understanding root cause
2. Implementing fix
3. Running tests to verify fix works
4. Testing manually if no automated tests
5. Confirming bug is resolved
"""
```

### 5. Weak Permissions

**Bad**:
```python
# Too permissive in production
permission_mode="bypassPermissions"  # On production server!
```

**Good**:
```python
# Appropriate restrictions
permission_mode="acceptEdits"  # In sandbox
# + validation hooks
# + monitoring
```

### 6. Poor Context Management

**Bad**:
```python
# Reading entire 10k-line file when only need one function
prompt = "Read main.py and tell me what the auth function does"
```

**Good**:
```python
# Targeted context gathering
prompt = "Grep for 'def auth' in main.py and read that function"
```

### 7. Ignoring Tool Costs

**Bad**:
```python
# Spawning unnecessary subagents
prompt = "Use a subagent to check if file exists"
# 20k token overhead for simple check!
```

**Good**:
```python
# Use appropriate tools
prompt = "Check if auth.py exists using Bash"
# Simple command, no subagent needed
```

### 8. Brittle Session Management

**Bad**:
```python
# Losing session ID
async for message in query(...):
    pass  # Never captured session_id!
# Can't resume later
```

**Good**:
```python
# Always capture session ID
session_id = None
async for message in query(...):
    if message.type == 'system' and message.subtype == 'init':
        session_id = message.session_id
        save_session_id(session_id)
```

### 9. No Progress Tracking

**Bad**:
```python
# No way to know what's been done
prompt = "Continue working on the project"
# Agent has no idea what's done
```

**Good**:
```python
# Clear progress tracking
prompt = """
Check claude-progress.txt for what's done.
Check features.json for what's next.
Work on next incomplete feature.
Update progress when done.
"""
```

### 10. Unconstrained Bash

**Bad**:
```python
# No validation on bash commands
allowed_tools=["Bash"]
permission_mode="bypassPermissions"
# Agent can run ANY command!
```

**Good**:
```python
# Validate bash commands
allowed_tools=["Bash"]
permission_mode="acceptEdits"
hooks={"PreToolUse": [validate_bash_commands]}
# + Sandbox environment
# + Command logging
```

## Performance Optimization

### Model Selection

**Choose models strategically**:

```python
task_models = {
    "file_search": "haiku",      # Fast, cheap
    "code_formatting": "haiku",   # Simple task
    "feature_implementation": "sonnet",  # Balanced
    "architecture_design": "opus",  # Complex reasoning
    "security_review": "opus",     # Critical task
}
```

### Parallel Execution

**Maximize throughput**:
```python
# Good - 3 parallel subagents
prompt = "Research auth, database, and API in parallel with 3 subagents"

# Better - stay under 10 concurrent limit
prompt = "Research 8 modules in parallel: {modules}"
# Automatically queues in batches of 10
```

### Context Optimization

**Minimize token usage**:

```python
# Bad - reads entire file
tools=["Read"]
prompt = "Read the 5000-line main.py file"

# Good - targeted reading
tools=["Grep", "Read"]
prompt = "Grep for 'class Auth' in main.py, then read that class"
```

### Caching Strategies

**Reuse expensive operations**:

```python
# Cache research results
research_cache = {}

async def get_research(topic):
    if topic in research_cache:
        return research_cache[topic]

    result = await run_research_agent(topic)
    research_cache[topic] = result
    return result
```

## Security Best Practices

### 1. Always Sandbox Production Agents

Never run agents directly on production servers:

```python
# Bad
agent.run(on_production_server=True)

# Good
sandbox = create_secure_sandbox()
agent.run(in_sandbox=sandbox)
```

### 2. Validate All External Inputs

```python
def validate_user_prompt(prompt):
    """Validate user input before passing to agent."""
    # Check for prompt injection attempts
    dangerous_patterns = [
        "ignore previous instructions",
        "disregard all rules",
        "system:",
    ]

    if any(pattern in prompt.lower() for pattern in dangerous_patterns):
        raise ValueError("Suspicious prompt detected")

    return prompt
```

### 3. Use Least Privilege

Grant minimal necessary permissions:

```python
# Research agent - read only
research_agent = ClaudeAgentOptions(
    allowed_tools=["Read", "Grep", "Glob"],
    disallowed_tools=["Write", "Edit", "Bash"]
)

# Implementation agent - no destructive bash
implementation_agent = ClaudeAgentOptions(
    allowed_tools=["Read", "Edit", "Write"],
    hooks={"PreToolUse": [block_dangerous_commands]}
)
```

### 4. Log Everything

```python
def log_tool_use(message):
    """Log all tool uses for audit trail."""
    if hasattr(message, 'tool_input'):
        logger.info("Tool use", extra={
            'tool': message.tool_name,
            'input': message.tool_input,
            'timestamp': datetime.utcnow(),
            'session_id': message.session_id
        })
```

### 5. Implement Rate Limiting

```python
from collections import defaultdict
import time

class RateLimiter:
    def __init__(self, max_requests=100, window=3600):
        self.max_requests = max_requests
        self.window = window
        self.requests = defaultdict(list)

    def allow_request(self, user_id):
        now = time.time()
        requests = self.requests[user_id]

        # Remove old requests
        requests = [r for r in requests if now - r < self.window]

        if len(requests) >= self.max_requests:
            return False

        requests.append(now)
        self.requests[user_id] = requests
        return True
```

### 6. Rotate Credentials

```python
# Bad - hardcoded credentials
api_key = "sk-abc123..."

# Good - from secure vault with rotation
def get_api_key():
    return vault.get_secret("anthropic_api_key", version="latest")
```

### 7. Monitor for Abuse

```python
def monitor_suspicious_activity(message):
    """Detect and alert on suspicious patterns."""
    if hasattr(message, 'tool_input'):
        # Check for unusual patterns
        if 'sudo' in str(message.tool_input):
            alert("Sudo command attempted", severity="high")
        if 'curl' in str(message.tool_input) and 'pastebin' in str(message.tool_input):
            alert("Potential data exfiltration", severity="critical")
```

## Testing Strategies

### Unit Testing Agents

```python
import pytest
from claude_agent_sdk import query, ClaudeAgentOptions

@pytest.mark.asyncio
async def test_code_reviewer_detects_sql_injection():
    """Test that security reviewer catches SQL injection."""
    # Create file with SQL injection vulnerability
    with open('test_file.py', 'w') as f:
        f.write('query = f"SELECT * FROM users WHERE id={user_id}"')

    # Run security reviewer
    found_issue = False
    async for message in query(
        prompt="Review test_file.py for security issues",
        options=ClaudeAgentOptions(
            allowed_tools=["Read"],
            agents={"security": security_reviewer_agent}
        )
    ):
        if hasattr(message, 'result'):
            if 'sql injection' in message.result.lower():
                found_issue = True

    assert found_issue, "Security reviewer should detect SQL injection"
```

### Integration Testing

```python
@pytest.mark.asyncio
async def test_full_feature_pipeline():
    """Test complete feature implementation pipeline."""
    session_id = None

    # Step 1: Research
    async for message in query(
        prompt="Research authentication best practices",
        options=ClaudeAgentOptions(allowed_tools=["Read", "WebSearch"])
    ):
        if message.type == 'system':
            session_id = message.session_id

    # Step 2: Implement
    async for message in query(
        prompt="Implement authentication based on research",
        options=ClaudeAgentOptions(
            resume=session_id,
            allowed_tools=["Read", "Write", "Edit"]
        )
    ):
        pass

    # Step 3: Test
    async for message in query(
        prompt="Write and run tests for authentication",
        options=ClaudeAgentOptions(
            resume=session_id,
            allowed_tools=["Write", "Bash"]
        )
    ):
        pass

    # Verify auth.py was created and tests pass
    assert os.path.exists('auth.py')
    result = subprocess.run(['pytest', 'test_auth.py'], capture_output=True)
    assert result.returncode == 0
```

### Load Testing

```python
async def load_test_agent(num_concurrent=10):
    """Test agent under load."""
    async def run_agent_task(task_id):
        start = time.time()
        async for message in query(
            prompt=f"Process task {task_id}",
            options=ClaudeAgentOptions(...)
        ):
            pass
        return time.time() - start

    # Run concurrent agents
    tasks = [run_agent_task(i) for i in range(num_concurrent)]
    durations = await asyncio.gather(*tasks)

    avg_duration = sum(durations) / len(durations)
    print(f"Average task duration: {avg_duration:.2f}s")
    assert avg_duration < 60, "Tasks taking too long"
```

## Monitoring and Observability

### Key Metrics to Track

```python
class AgentMetrics:
    def __init__(self):
        self.total_sessions = 0
        self.successful_sessions = 0
        self.failed_sessions = 0
        self.total_tokens = 0
        self.total_tool_calls = 0
        self.tool_call_counts = defaultdict(int)
        self.session_durations = []

    def record_session(self, session_data):
        self.total_sessions += 1

        if session_data['success']:
            self.successful_sessions += 1
        else:
            self.failed_sessions += 1

        self.total_tokens += session_data['tokens']
        self.total_tool_calls += session_data['tool_calls']

        for tool, count in session_data['tools_used'].items():
            self.tool_call_counts[tool] += count

        self.session_durations.append(session_data['duration'])

    def get_summary(self):
        return {
            'success_rate': self.successful_sessions / self.total_sessions,
            'avg_tokens': self.total_tokens / self.total_sessions,
            'avg_tool_calls': self.total_tool_calls / self.total_sessions,
            'avg_duration': sum(self.session_durations) / len(self.session_durations),
            'most_used_tool': max(self.tool_call_counts, key=self.tool_call_counts.get)
        }
```

### Distributed Tracing

```python
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer(__name__)

async def traced_query(prompt, options):
    """Query with distributed tracing."""
    with tracer.start_as_current_span("agent_session") as span:
        span.set_attribute("prompt", prompt[:100])  # First 100 chars
        span.set_attribute("model", options.model)

        try:
            async for message in query(prompt, options):
                # Trace tool uses
                if hasattr(message, 'content'):
                    for block in message.content:
                        if getattr(block, 'type', None) == 'tool_use':
                            with tracer.start_as_current_span("tool_use") as tool_span:
                                tool_span.set_attribute("tool_name", block.name)
                                tool_span.set_attribute("tool_input", str(block.input))

                if message.type == 'result':
                    span.set_attribute("result_type", message.subtype)
                    if message.subtype == 'success':
                        span.set_status(Status(StatusCode.OK))
                    else:
                        span.set_status(Status(StatusCode.ERROR))

        except Exception as e:
            span.set_status(Status(StatusCode.ERROR))
            span.record_exception(e)
            raise
```

## Cost Optimization

### Track Costs per Operation

```python
# Anthropic API pricing (as of 2024)
PRICING = {
    'opus': {'input': 15.00, 'output': 75.00},    # per 1M tokens
    'sonnet': {'input': 3.00, 'output': 15.00},
    'haiku': {'input': 0.25, 'output': 1.25}
}

def calculate_cost(tokens_in, tokens_out, model):
    """Calculate cost of API call."""
    rates = PRICING[model]
    cost = (tokens_in * rates['input'] + tokens_out * rates['output']) / 1_000_000
    return cost

# Track costs
session_cost = 0
async for message in query(...):
    if hasattr(message, 'usage'):
        cost = calculate_cost(
            message.usage['input_tokens'],
            message.usage['output_tokens'],
            model
        )
        session_cost += cost

print(f"Session cost: ${session_cost:.4f}")
```

### Cost Optimization Strategies

**1. Use appropriate models**:
```python
# Use Haiku for simple tasks (5-10x cheaper)
"file-search": AgentDefinition(model="haiku", ...)
"code-implementation": AgentDefinition(model="sonnet", ...)
"architecture-review": AgentDefinition(model="opus", ...)
```

**2. Limit tool outputs**:
```python
# Bad - returns all 10k test results
prompt = "Run entire test suite and show all output"

# Good - returns summary only
prompt = "Run tests and report only failures with error messages"
```

**3. Reuse session context**:
```python
# Bad - new session for each query
for task in tasks:
    async for msg in query(prompt=task, options=...):
        pass  # Full initialization cost each time

# Good - resume session
session_id = None
for task in tasks:
    async for msg in query(prompt=task, options=ClaudeAgentOptions(resume=session_id)):
        if msg.type == 'system':
            session_id = msg.session_id
```

**4. Batch operations**:
```python
# Bad - separate queries
for file in files:
    query(f"Review {file}", ...)

# Good - batch in one query
query(f"Review these files in parallel: {files}", ...)
```

## Documentation and Maintenance

### Document Your Agents

```python
"""
Authentication Agent

Purpose: Implements authentication features following security best practices

Capabilities:
- Password hashing with bcrypt
- JWT token generation
- OAuth 2.0 flows
- 2FA with TOTP
- Password reset workflows

Tools: Read, Edit, Write, Bash
Model: Sonnet (balanced for security + implementation)
Permission Mode: acceptEdits

Usage:
    async for msg in query(
        prompt="Implement password reset flow",
        options=auth_agent_options
    )

Security Considerations:
- Never stores plaintext passwords
- Uses cryptographically secure random generation
- Validates all inputs
- Logs authentication events

Limitations:
- Doesn't implement biometric auth
- Requires manual review of OAuth scopes

Last Updated: 2024-01-29
Maintainer: Security Team
"""
```

### Version Control for Agents

```
.claude/agents/
├── README.md              # Agent documentation
├── code-reviewer.md       # Agent definitions
├── security-scanner.md
└── CHANGELOG.md           # Agent changes
```

**CHANGELOG.md**:
```markdown
# Agent Changelog

## [2.0.0] - 2024-01-29
### Changed
- code-reviewer now uses Opus for better detection
- Added performance checks to review criteria

### Security
- security-scanner now checks for all OWASP Top 10

## [1.5.0] - 2024-01-15
### Added
- New test-runner agent for automated testing
```

## Recommended Learning Path

1. **Start Simple**: Single agent, read-only tools
2. **Add Modifications**: Enable Edit tool with acceptEdits
3. **Try Subagents**: Use Task tool for parallel work
4. **Implement Sessions**: Add resumption for continuity
5. **Add Verification**: Implement testing and validation
6. **Deploy Sandboxed**: Move to production in containers
7. **Optimize**: Track costs, improve performance
8. **Scale**: Multiple agents, orchestration patterns

## Further Reading

- **Official Guides**:
  - Building Agents: https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk
  - Long-Running Agents: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

- **Documentation**:
  - Agent SDK Overview: https://platform.claude.com/docs/en/agent-sdk/overview
  - Subagents Guide: https://platform.claude.com/docs/en/agent-sdk/subagents
  - Permissions: https://platform.claude.com/docs/en/agent-sdk/permissions

- **Community Resources**:
  - GitHub Demos: https://github.com/anthropics/claude-agent-sdk-demos
  - Awesome Subagents: https://github.com/VoltAgent/awesome-claude-code-subagents
  - Workshop Video: https://www.youtube.com/watch?v=TqC1qOfiVcQ
