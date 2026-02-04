# Session Management and Resumption

## What is a Session?

A session represents a continuous conversation with Claude, maintaining full context across multiple interactions. Sessions enable:

- **Context Preservation**: Complete conversation history, tool calls, and results
- **Resumption**: Pick up exactly where you left off after disconnections or restarts
- **Forking**: Branch from a point to explore alternative approaches
- **State Management**: Persistent conversation state across agent restarts

## Session Lifecycle

### 1. Session Creation

Every `query()` call creates a new session automatically:

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for message in query(
    prompt="Help me build a web application",
    options=ClaudeAgentOptions(model="claude-sonnet-4-5")
):
    # First message is system init with session_id
    if message.type == 'system' and message.subtype == 'init':
        session_id = message.session_id
        print(f"Session started: {session_id}")
```

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

let sessionId: string | undefined;

for await (const message of query({
  prompt: "Help me build a web application",
  options: { model: "claude-sonnet-4-5" }
})) {
  if (message.type === 'system' && message.subtype === 'init') {
    sessionId = message.session_id;
    console.log(`Session started: ${sessionId}`);
  }
}
```

**Session ID Format**: UUID string (e.g., `"session-abc123..."`)

### 2. Capturing Session ID

The session ID appears in the **first message** (system init message):

```python
let session_id = None

async for message in query(
    prompt="Analyze this codebase",
    options=ClaudeAgentOptions(allowed_tools=["Read", "Grep"])
):
    # Capture session ID from init message
    if hasattr(message, 'type') and message.type == 'system':
        if hasattr(message, 'subtype') and message.subtype == 'init':
            session_id = message.session_id
            print(f"Session ID: {session_id}")
            # Store this for later resumption

    # Process other messages
    if hasattr(message, 'result'):
        print(message.result)
```

**Best Practice**: Store session IDs in a database or file for later resumption.

### 3. Session Storage

Sessions are stored locally by Claude Code:

**Location**: `~/.claude/projects/{project_name}/{session_id}/`

**Contents**:
- `transcript.jsonl` - Full conversation history
- `checkpoints/` - File version snapshots
- `subagents/` - Subagent transcripts

**Persistence**: Sessions persist until cleaned up (default 30 days).

## Resuming Sessions

### Basic Resumption

Resume a previous session by passing the session ID:

```python
from claude_agent_sdk import query, ClaudeAgentOptions

# Previous session
session_id = "session-abc123"

# Resume the conversation
async for message in query(
    prompt="Continue implementing the authentication system",
    options=ClaudeAgentOptions(
        resume=session_id,  # Resume previous session
        allowed_tools=["Read", "Edit", "Write", "Bash"]
    )
):
    if hasattr(message, 'result'):
        print(message.result)
```

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";

const sessionId = "session-abc123";

for await (const message of query({
  prompt: "Continue implementing the authentication system",
  options: {
    resume: sessionId,
    allowedTools: ["Read", "Edit", "Write", "Bash"]
  }
})) {
  if ('result' in message) console.log(message.result);
}
```

**What Happens**:
1. Claude Code loads the full transcript from disk
2. Claude receives all previous context
3. Conversation continues seamlessly from where it stopped
4. File checkpoints available for reverting changes

### Multi-Turn Sessions

Build multi-turn workflows by resuming between interactions:

```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def multi_turn_workflow():
    session_id = None

    # Turn 1: Initial analysis
    async for message in query(
        prompt="Analyze the user authentication code",
        options=ClaudeAgentOptions(allowed_tools=["Read", "Grep", "Glob"])
    ):
        if message.type == 'system' and message.subtype == 'init':
            session_id = message.session_id
        if hasattr(message, 'result'):
            print("Analysis:", message.result)

    # Turn 2: Make improvements
    async for message in query(
        prompt="Add password strength validation based on your analysis",
        options=ClaudeAgentOptions(
            resume=session_id,
            allowed_tools=["Read", "Edit", "Write"]
        )
    ):
        if hasattr(message, 'result'):
            print("Improvements:", message.result)

    # Turn 3: Add tests
    async for message in query(
        prompt="Write tests for the password validation you just added",
        options=ClaudeAgentOptions(
            resume=session_id,
            allowed_tools=["Read", "Write", "Bash"]
        )
    ):
        if hasattr(message, 'result'):
            print("Tests:", message.result)

    return session_id

asyncio.run(multi_turn_workflow())
```

## Forking Sessions

Forking creates a new session that starts from a previous session's state. This allows exploring different approaches without modifying the original.

### When to Fork vs Continue

| Action | Use Case | Result |
|--------|----------|--------|
| **Continue** (default) | Linear progression, build on previous work | Same session ID, updates transcript |
| **Fork** | Try alternative approach, A/B testing, experiments | New session ID, original unchanged |

### How to Fork

Set `fork_session=True` (Python) or `forkSession: true` (TypeScript):

```python
from claude_agent_sdk import query, ClaudeAgentOptions

# Original session
original_session_id = "session-abc123"

# Fork to try different approach
async for message in query(
    prompt="Now redesign this as a GraphQL API instead",
    options=ClaudeAgentOptions(
        resume=original_session_id,
        fork_session=True,  # Creates new branch
        allowed_tools=["Read", "Edit", "Write"]
    )
):
    if message.type == 'system' and message.subtype == 'init':
        forked_session_id = message.session_id
        print(f"Forked session: {forked_session_id}")
        # This is a NEW session ID

# Original session unchanged, can still resume it
async for message in query(
    prompt="Add authentication to the REST API",
    options=ClaudeAgentOptions(
        resume=original_session_id,
        fork_session=False,  # Default: continue original
    )
):
    pass
```

### Fork Use Cases

**A/B Testing Implementations**:
```python
# Try two different approaches from same starting point
original = await implement_feature(session_id)

fork_a = await try_approach_a(session_id, fork=True)
fork_b = await try_approach_b(session_id, fork=True)

# Compare results, choose best one
```

**Experimental Changes**:
```python
# Explore risky refactoring without affecting main session
forked = await try_refactoring(session_id, fork=True)
if looks_good:
    use_forked_session()
else:
    continue_original_session()
```

**Multi-Path Workflows**:
```python
# Design phase
design_session = await create_design()

# Fork for parallel implementation paths
backend_session = await implement_backend(design_session, fork=True)
frontend_session = await implement_frontend(design_session, fork=True)
```

## Session Resume Examples

### Complete Example: Resume After Crash

```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions
import json

async def save_session_metadata(session_id, metadata):
    """Save session info for later resumption."""
    with open('.claude_sessions.json', 'w') as f:
        json.dump({'current_session': session_id, **metadata}, f)

async def load_session_metadata():
    """Load saved session info."""
    try:
        with open('.claude_sessions.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return None

async def resilient_workflow():
    # Check for existing session
    saved = await load_session_metadata()
    session_id = saved['current_session'] if saved else None

    if session_id:
        print(f"Resuming previous session: {session_id}")
        prompt = "Continue from where we left off"
    else:
        print("Starting new session")
        prompt = "Start building a todo app with authentication"

    # Start or resume session
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            resume=session_id,  # None for new, ID for resume
            allowed_tools=["Read", "Edit", "Write", "Bash"]
        )
    ):
        # Capture session ID
        if message.type == 'system' and message.subtype == 'init':
            session_id = message.session_id
            await save_session_metadata(session_id, {
                'started_at': message.timestamp,
                'project': 'todo-app'
            })

        # Process results
        if hasattr(message, 'result'):
            print(message.result)

asyncio.run(resilient_workflow())
```

### Resume Subagent Sessions

Resuming subagents requires the parent session:

```typescript
import { query, type SDKMessage } from "@anthropic-ai/claude-agent-sdk";

// Helper to extract agent ID from message content
function extractAgentId(message: SDKMessage): string | undefined {
  if (!('message' in message)) return undefined;
  const content = JSON.stringify(message.message.content);
  const match = content.match(/agentId:\s*([a-f0-9-]+)/);
  return match?.[1];
}

let sessionId: string | undefined;
let agentId: string | undefined;

// First invocation - spawn Explore subagent
for await (const message of query({
  prompt: "Use the Explore agent to find all API endpoints",
  options: { allowedTools: ['Read', 'Grep', 'Glob', 'Task'] }
})) {
  // Capture session ID
  if ('session_id' in message) sessionId = message.session_id;

  // Extract subagent ID
  const extracted = extractAgentId(message);
  if (extracted) agentId = extracted;

  if ('result' in message) console.log(message.result);
}

// Resume subagent with follow-up task
if (agentId && sessionId) {
  for await (const message of query({
    prompt: `Resume agent ${agentId} and analyze the 3 most complex endpoints`,
    options: {
      allowedTools: ['Read', 'Grep', 'Glob', 'Task'],
      resume: sessionId  // Must resume parent session
    }
  })) {
    if ('result' in message) console.log(message.result);
  }
}
```

**Important**: You must resume the parent session to access subagent context. Subagent transcripts are tied to their parent session.

## Context Management in Sessions

### Context Windows

Each session has a 200k token context window:
- Includes full conversation history
- All tool calls and results
- System messages and metadata

**Subagents**: Each has separate 200k token window (isolated from parent).

### Automatic Compaction

When context approaches the limit, automatic compaction occurs:

**Trigger**: Default at 80% of context (160k tokens)

**Process**:
1. Older messages are summarized
2. Recent interactions preserved
3. Critical information retained
4. Compaction boundary marked in transcript

**Configure threshold**:
```bash
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=50  # Compact at 50% (100k tokens)
```

**Detection in stream**:
```python
async for message in query(...):
    if message.type == 'system' and message.subtype == 'compact_boundary':
        pre_tokens = message.compact_metadata['preTokens']
        print(f"Context compacted at {pre_tokens} tokens")
```

### Manual Compaction

Trigger compaction manually in Claude Code CLI:
```
/compact
```

**When to use**:
- Before long-running operations
- When you know you'll need more context space
- After verbose exploration phase

## Session Persistence

### Storage Duration

Sessions persist on disk until cleanup:

**Default**: 30 days

**Configure**:
```json
// settings.json
{
  "cleanupPeriodDays": 60
}
```

**Locations**:
- Project sessions: `~/.claude/projects/{project}/`
- User sessions: `~/.claude/`

### Session Files

Each session directory contains:

```
~/.claude/projects/my-project/session-abc123/
├── transcript.jsonl          # Full conversation history
├── checkpoints/              # File version snapshots
│   ├── checkpoint-1/
│   ├── checkpoint-2/
│   └── ...
└── subagents/                # Subagent transcripts
    ├── agent-xyz.jsonl
    └── ...
```

**transcript.jsonl**: JSONL file with one message per line
**checkpoints/**: Git-like snapshots for file reverting
**subagents/**: Independent transcripts for each subagent

## File Checkpointing

Sessions include file checkpointing for tracking and reverting changes.

### How Checkpoints Work

**Automatic checkpoints** created when:
- Agent modifies files (Edit, Write tools)
- Agent runs destructive commands
- Major milestones in workflow

**Manual checkpoints** in CLI:
```
/checkpoint "Completed authentication"
```

### Reverting Changes

Revert to a checkpoint:

**In CLI**:
```
/revert checkpoint-3
```

**Programmatically**:
Check checkpoint files in `checkpoints/` directory and restore manually.

### Viewing Checkpoint History

```
/checkpoints
```

Shows:
- Checkpoint IDs
- Timestamps
- Descriptions
- Files modified

## Long-Running Sessions

For tasks spanning hours or days, sessions provide continuity.

### Environment Setup Pattern

For long-running projects, use an initializer pattern:

**Session 1 - Initialization**:
```python
async for message in query(
    prompt="""Set up development environment:
    1. Create feature list (tests.json)
    2. Create progress file (claude-progress.txt)
    3. Write init.sh to start dev server
    4. Make initial git commit""",
    options=ClaudeAgentOptions(
        allowed_tools=["Write", "Bash"],
        permission_mode="acceptEdits"
    )
):
    if message.type == 'system' and message.subtype == 'init':
        init_session_id = message.session_id
```

**Subsequent Sessions - Incremental Work**:
```python
async for message in query(
    prompt="""Continue development:
    1. Read claude-progress.txt to see what's done
    2. Check tests.json for next failing test
    3. Implement one feature at a time
    4. Update progress and commit""",
    options=ClaudeAgentOptions(
        resume=init_session_id,
        allowed_tools=["Read", "Edit", "Write", "Bash"]
    )
):
    pass
```

### Progress Tracking

Use files to track progress across sessions:

**claude-progress.txt**:
```
2024-01-29 10:30 - Set up project structure
2024-01-29 11:15 - Implemented user authentication
2024-01-29 14:20 - Added password validation
Next: Write tests for password validation
```

**tests.json**:
```json
[
  {
    "description": "User can register with valid email",
    "passes": true
  },
  {
    "description": "Password must be 8+ characters",
    "passes": false
  }
]
```

Claude reads these files at session start to understand current state.

## Session Best Practices

### 1. Always Capture Session IDs

Store session IDs immediately:
```python
if message.type == 'system' and message.subtype == 'init':
    session_id = message.session_id
    # Save to database, file, or memory
    save_session_id(session_id)
```

### 2. Use Descriptive Prompts on Resume

Help Claude understand context:
```python
prompt = "Continue implementing the authentication feature from last session"
# Better than: "Continue"
```

### 3. Track Session Metadata

Store useful metadata:
```python
session_metadata = {
    'session_id': session_id,
    'started_at': timestamp,
    'project': 'web-app',
    'last_task': 'authentication',
    'status': 'in-progress'
}
```

### 4. Fork for Experiments

Don't pollute main session with experimental work:
```python
# Try risky refactoring in fork
experimental = query(
    prompt="Try aggressive refactoring",
    options=ClaudeAgentOptions(
        resume=session_id,
        fork_session=True
    )
)
```

### 5. Clean Up Old Sessions

Delete sessions you don't need:
```bash
# Manually clean old sessions
rm -rf ~/.claude/projects/old-project/session-xyz/
```

Or configure shorter cleanup period:
```json
{"cleanupPeriodDays": 7}
```

### 6. Use Checkpoints Strategically

Create checkpoints before risky operations:
```
/checkpoint "Before major refactoring"
```

### 7. Handle Missing Sessions Gracefully

```python
try:
    async for message in query(
        prompt="Continue",
        options=ClaudeAgentOptions(resume=session_id)
    ):
        pass
except Exception as e:
    if "session not found" in str(e):
        # Start new session
        print("Previous session expired, starting fresh")
        session_id = None
```

### 8. Monitor Context Usage

Track tokens to avoid unexpected compaction:
```python
if message.type == 'system' and message.subtype == 'compact_boundary':
    print(f"Warning: Context compacted at {message.compact_metadata['preTokens']} tokens")
    # Consider forking for fresh context
```

### 9. Document Session Purpose

Include context in first message:
```python
prompt = """Project: E-commerce website
Goal: Implement shopping cart functionality
Previous session: Completed product catalog
Current task: Add cart UI and API endpoints"""
```

### 10. Test Resumption Regularly

Verify sessions resume correctly:
```python
# Test: Start session, stop, resume
session_id = start_session()
# ... do some work ...
# Restart application
resume_and_verify(session_id)
```

## Troubleshooting

### Session Not Found

**Problem**: Resume fails with "session not found".

**Causes**:
- Session ID typo
- Session expired (past cleanupPeriodDays)
- Session directory deleted
- Wrong project directory

**Solutions**:
- Verify session ID is correct
- Check session directory exists: `~/.claude/projects/{project}/{session_id}/`
- Start new session if expired
- Ensure working directory matches project

### Resume Starts New Session

**Problem**: Session appears to start fresh despite resume parameter.

**Causes**:
- Session ID is None or empty string
- Session not found (see above)
- Different project directory

**Debug**:
```python
print(f"Resuming session: {session_id}")  # Verify not None
print(f"Current directory: {os.getcwd()}")  # Check project

async for message in query(
    options=ClaudeAgentOptions(resume=session_id)
):
    if message.type == 'system' and message.subtype == 'init':
        print(f"Actual session ID: {message.session_id}")
        # Compare with expected
```

### Subagent Resume Failed

**Problem**: Can't resume subagent from previous session.

**Causes**:
- Not resuming parent session (subagents tied to parent)
- Agent ID not captured correctly
- Subagent transcript expired

**Solutions**:
- Always resume parent session first
- Extract and store agent ID from Task tool results
- Check subagent transcript exists in session directory

### Context Compaction Too Frequent

**Problem**: Context compacts more often than desired.

**Causes**:
- Verbose tool outputs
- Many long messages
- Low compaction threshold

**Solutions**:
- Increase threshold: `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90`
- Use subagents for verbose operations
- Summarize long outputs before returning

### Fork Created Instead of Continue

**Problem**: New session created when you wanted to continue.

**Cause**: `fork_session=True` set accidentally.

**Solution**:
```python
options=ClaudeAgentOptions(
    resume=session_id,
    fork_session=False  # Explicitly set to continue
)
```

## Advanced Patterns

### Session Pool for Multi-User Apps

```python
class SessionManager:
    def __init__(self):
        self.sessions = {}  # user_id -> session_id

    async def get_or_create_session(self, user_id):
        if user_id not in self.sessions:
            # Create new session
            async for message in query(
                prompt="Initialize user workspace",
                options=ClaudeAgentOptions(...)
            ):
                if message.type == 'system':
                    self.sessions[user_id] = message.session_id
        return self.sessions[user_id]

    async def continue_session(self, user_id, prompt):
        session_id = await self.get_or_create_session(user_id)
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(resume=session_id)
        ):
            yield message
```

### Persistent Task Queue

```python
import asyncio
from collections import deque

class PersistentTaskQueue:
    def __init__(self, session_id):
        self.session_id = session_id
        self.tasks = deque()

    async def add_task(self, task_description):
        self.tasks.append(task_description)

    async def process_next(self):
        if not self.tasks:
            return None

        task = self.tasks.popleft()
        async for message in query(
            prompt=f"Process task: {task}",
            options=ClaudeAgentOptions(resume=self.session_id)
        ):
            if hasattr(message, 'result'):
                return message.result
```

### Multi-Branch Development

```python
class DevelopmentBranches:
    def __init__(self, main_session_id):
        self.main_session = main_session_id
        self.branches = {}  # feature_name -> session_id

    async def create_feature_branch(self, feature_name):
        """Fork main session for feature development."""
        async for message in query(
            prompt=f"Start implementing {feature_name}",
            options=ClaudeAgentOptions(
                resume=self.main_session,
                fork_session=True
            )
        ):
            if message.type == 'system':
                self.branches[feature_name] = message.session_id
        return self.branches[feature_name]

    async def merge_feature(self, feature_name):
        """Review feature branch and merge to main."""
        feature_session = self.branches[feature_name]
        # In practice, you'd review changes and manually merge
        # This is conceptual - actual merging requires file operations
```

## Related Topics

- **Overview**: Core concepts and architecture
- **Task Tool**: Using subagents with sessions
- **Best Practices**: Patterns for reliable agents
- **Hosting**: Deploying sessions in production

## Resources

- Session Management Docs: https://platform.claude.com/docs/en/agent-sdk/sessions
- Long-Running Agents: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- GitHub Issues on Sessions: https://github.com/anthropics/claude-code/issues?q=session
