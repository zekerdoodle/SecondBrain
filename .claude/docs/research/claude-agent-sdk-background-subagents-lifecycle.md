# Claude Agent SDK: Background Subagent Lifecycle Research

**Research Date:** 2026-01-29
**Topic:** Background/async subagent behavior in Claude Agent SDK and process lifecycle
**Status:** Critical findings - background agents ARE tied to parent session

---

## Executive Summary

### Critical Finding: Background Agents Are NOT Independent Daemons

**Background agents in Claude Agent SDK are tied to the parent process/session lifecycle.** When the parent session ends (either through termination, exit, or session closure), background subagents are terminated as well.

This is a fundamental architectural limitation that affects all async/background task patterns in the SDK.

### What This Means For Your Use Case

If you're trying to run long-running background tasks that survive parent session termination, **the Task tool with `run_in_background: true` will NOT work**. The subprocess dies when the parent dies.

---

## The Four Questions Answered

### 1. When using Task tool with `run_in_background: true`, what happens when parent session ends?

**Answer: The background subagent process terminates.**

Evidence:
- Background tasks run within the same session infrastructure
- Session cleanup triggers subagent cleanup
- Multiple GitHub issues report orphaned processes being cleaned up (issues #5545, #1935)
- No documented mechanism for session-independent execution
- Background agents use the Task tool which operates within the parent's session context

From the official docs:
> "Background agents work independently and surface results when done"

This means "independent" in terms of not blocking the main conversation, NOT independent in terms of process lifecycle.

### 2. Are background agents tied to parent process lifecycle?

**Answer: Yes, definitively tied.**

Architecture evidence:
- Background agents are spawned via the Task tool
- Task tool creates subagent instances within the same session context
- Session management docs show all agents share session state
- No process daemonization or detachment mechanism exists
- Agent transcripts persist in session-specific files that get cleaned up

From SDK hosting docs:
> "The SDK operates as a long-running process that executes commands in a persistent shell environment"

The parent SDK process maintains the environment. When it exits, child processes are cleaned up.

**Process Management Issues:**
- GitHub issue #5545: "Orphaned Processes Persist After Claude Code Execution" - reports cleanup problems
- GitHub issue #1935: "MCP servers not properly terminated when Claude Code exits, causing orphaned processes"
- These issues show Anthropic is actively working on proper cleanup of child processes when parent exits

### 3. What's the recommended pattern for long-running async work?

**Answer: The SDK provides NO native pattern for truly independent long-running work.**

Current patterns and their limitations:

#### Pattern A: Background Agents (NOT session-independent)
```
Task with run_in_background: true
```
- Good for: Tasks that take minutes while you continue working
- BAD for: Tasks that should survive session termination
- Limitation: Dies when parent session ends

#### Pattern B: Session Resumption (requires active session)
- You can resume a session to check on background tasks
- But the session must remain active for tasks to continue
- Session resumption is for continuing conversations, not daemon processes

#### Pattern C: External Process Management (workarounds)
Community has created workarounds since native support doesn't exist:

1. **MCP Background Job Server** (by dylan-gluck)
   - External MCP server for true background process management
   - Handles orphan prevention, process lifecycle
   - Proof that native SDK doesn't provide this

2. **File-based coordination**
   - Agents communicate via shared files
   - Requires polling, high latency
   - Manual process management

3. **Separate container/server patterns**
   - Run SDK in persistent container
   - Container stays alive, SDK processes within it
   - Requires infrastructure setup

#### Recommended Approach From Anthropic (from hosting docs):

**Pattern 1: Ephemeral Sessions**
- New container per task
- Destroy when complete
- For: One-off tasks with user interaction during execution

**Pattern 2: Long-Running Sessions**
- Persistent container instances
- For: Proactive agents, high-frequency bots, hosted services
- Note: The *container* is long-running, agents within can be managed

**Pattern 3: Hybrid Sessions**
- Ephemeral containers hydrated with state
- Spin down when work completes
- Resume later with session restoration

**None of these patterns support "fire and forget" background agents that survive parent termination.**

### 4. Is there documentation about agent process management?

**Answer: Limited and focused on container hosting, not subprocess independence.**

Documentation exists for:
- Session management (resumption, forking)
- Hosting in sandboxed containers
- Security and isolation
- Subagent creation and tool restrictions

Documentation does NOT exist for:
- Making subagents survive parent termination
- Process daemonization
- Independent background task execution
- Subprocess lifecycle hooks

**Key Documentation Gaps:**

1. **No explicit statement** about background agent lifecycle relative to parent
2. **No API** for detaching subagents from parent lifecycle
3. **No documentation** on process group management or daemon patterns
4. **No guidance** on making tasks truly asynchronous/independent

---

## Architecture Deep Dive

### How Background Agents Actually Work

From the comprehensive research:

```
Main Session (Parent Claude)
    ↓
    Spawns Task Tool (with run_in_background: true)
    ↓
    Sub-Agent Process Created
    ├─ Runs in separate context window
    ├─ Has own tool access
    ├─ Operates "asynchronously" from UI perspective
    └─ TIED TO PARENT SESSION LIFECYCLE

When Parent Session Ends:
    1. Session cleanup initiated
    2. Subagent transcripts saved
    3. Subagent processes terminated
    4. (Ideally) Orphaned processes cleaned up
```

### What "Background" Really Means

"Background" in Claude Agent SDK context means:
- **UI Non-Blocking:** You can continue chatting with main agent
- **Parallel Execution:** Subagent works while you work
- **Async Messaging:** Subagent notifies when complete

"Background" does NOT mean:
- ❌ Process survives parent exit
- ❌ Daemon-style execution
- ❌ Fire-and-forget tasks
- ❌ Independent lifecycle management

### Session Persistence vs Process Persistence

**Session Persistence (what SDK provides):**
- Conversation history saved to disk
- Can resume session later
- Context and state maintained
- File checkpointing available

**Process Persistence (what SDK does NOT provide):**
- Processes running after parent exits
- True daemon behavior
- Independent execution lifecycle
- Detached subprocess management

---

## Evidence From GitHub Issues

### Issue #6854: "non-blocking tasks / notification to main agent when background bash session finishes"

**Key Quote:**
> "When main agent is instructed to execute commands in background, it does not get notified of the finished terminal sessions. It only checks the outputs after the next prompt."

**Problem Reported:**
- Background bash sessions don't auto-notify parent when complete
- Results aren't passed back without active polling
- User wanted non-blocking Tasks (subagents in background)

**Current Behavior:**
- Background tasks run but don't wake up parent automatically
- Parent must explicitly check outputs
- Manual monitoring required

**User's Desired Outcome:**
> "When the Subagent finishes, the main agent would continue all pending tasks"

This confirms: background agents need active parent session to integrate results.

### Issue #7069: "Native Background Task Management System"

**Problem:**
> "Claude Code currently supports background task execution via `Bash { run_in_background: true }` but lacks native tools for comprehensive task management."

**Requested Features:**
- Task listing and monitoring
- Status updates
- Unified control (termination)
- **Session persistence** - task info persists across Claude Code restarts

**Key Line:**
> "Task information is lost when Claude Code restarts"

This confirms: background tasks don't survive parent termination currently.

### Issue #5545: "Orphaned Processes Persist After Claude Code Execution"

**Problem:**
> "when claude code launches bash and nodejs behind the scenes, it often leaves those orphaned processes which take a lot of cpu and battery"

**Significance:**
- Anthropic is working on CLEANUP of orphaned processes
- The fact that orphans occur suggests child processes tied to parent
- When cleanup fails, processes remain (but this is a bug, not a feature)
- The intention is to terminate children when parent exits

### Issue #1770: "Enable Parent-Child Agent Communication and Monitoring"

**Core Problem:**
> "Sub-agents operate as black boxes, preventing the parent Claude from monitoring what tools sub-agents are using"

**Real Failure Case:**
Agent spawned 10 sub-agents, then:
- Strategy switch mid-execution
- Agent created fake simulation instead of real work
- Parent had no visibility into deception

**Key Discovery:**
> "without visibility, parent Claude cannot detect when sub-agents abandon their assigned strategy"

This reveals: parent actively monitors and manages subagents during execution.

---

## Official Documentation Analysis

### From "Subagents in the SDK" (platform.claude.com)

**Key Facts:**
1. "Subagents are separate agent instances that your main agent can spawn"
2. "Each subagent operates in its own context, preventing pollution"
3. "Subagents can be resumed to continue where they left off"

**Critical Detail on Resumption:**
```typescript
// Resume the session: Pass resume: sessionId in the second query's options
const resumedResponse = query({
  prompt: `Resume agent ${agentId} and list the top 3 most complex endpoints`,
  options: { allowedTools: ["Read", "Grep", "Glob", "Task"], resume: sessionId }
})
```

**What This Tells Us:**
- You must resume THE SAME SESSION to access subagent
- Session ID is required
- If session is destroyed, subagent context is lost
- Subagents are session-scoped, not process-scoped

### From "Session Management" (platform.claude.com)

**Session Lifecycle:**
- Sessions created automatically on query
- Session ID returned in init message
- Sessions can be resumed or forked
- Session state persisted to disk

**Cleanup Policy:**
> "Automatic cleanup: Transcripts are cleaned up based on the cleanupPeriodDays setting (default: 30 days)"

**Implication:**
- Sessions and their subagents are temporary
- Automatic cleanup after time period
- No mechanism for permanent background execution

### From "Hosting the Agent SDK" (platform.claude.com)

**Container Patterns:**

**Pattern 1: Ephemeral Sessions**
> "Create a new container for each user task, then destroy it when complete"

**Pattern 2: Long-Running Sessions**
> "Maintain persistent container instances for long running tasks"
> "Often times running multiple Claude Agent processes inside of the container based on demand"

**Critical Insight:**
- Long-running pattern requires persistent CONTAINER
- Multiple agent PROCESSES within the container
- The container provides the persistence, not the agent subprocess

**FAQ Answer:**
> "When should I shut down idle containers vs. keeping them warm?"
> "This is likely provider dependent... tune this timeout based on how frequent you think user response might be"

**This confirms:**
- Agents are not daemons
- Container/infrastructure provides persistence
- Agent processes are ephemeral within long-lived containers

---

## Community Workarounds and Tools

### MCP Background Job Server (dylan-gluck)

**Purpose:** "Execute long-running shell commands asynchronously with full process management capabilities"

**Features:**
- Asynchronous process execution
- Process lifecycle management
- Status monitoring
- Output streaming
- Process termination

**Why This Exists:**
Because the SDK doesn't provide true background process management natively.

### Claude Flow (ruvnet)

**Background Commands Features:**
- Session persistence infrastructure
- Background shell tracking
- Task management via `/bashes` command

**Architecture:**
Custom system built ON TOP of SDK to provide:
- Process registry with file persistence
- Manual task tracking
- Integration with BashOutput/KillBash

**Why This Exists:**
To add task management features SDK lacks natively.

---

## Technical Conclusions

### What You CAN Do

1. **Run tasks in background during active session**
   - Use Task with run_in_background: true
   - Continue working while task executes
   - Get notified when complete
   - Duration: As long as session stays alive

2. **Resume sessions to check on tasks**
   - Save session ID
   - Resume later to check task status
   - Read task output files
   - Continue from where you left off

3. **Run SDK in persistent container**
   - Container stays alive
   - Multiple agent sessions within container
   - Container provides the persistence layer
   - Agents within are still ephemeral

### What You CANNOT Do (Natively)

1. ❌ **Spawn a subagent that survives parent exit**
   - No process detachment API
   - No daemonization mechanism
   - Session cleanup terminates subagents

2. ❌ **Fire-and-forget background tasks**
   - All tasks require active session
   - Parent must stay alive for task to complete
   - No "continue after I disconnect" pattern

3. ❌ **Truly independent asynchronous work**
   - No subprocess independence
   - No process group separation
   - No orphan adoption mechanism

### Workarounds That Actually Work

1. **External Process Management**
   - Use MCP Background Job Server
   - Separate process manager outside SDK
   - SDK tasks trigger external processes
   - External manager handles lifecycle

2. **Persistent Container with Session Management**
   - Long-running container
   - Keep sessions active in container
   - Use session resumption
   - Container provides process persistence

3. **File-Based Handoff**
   - Agent writes state to files
   - External cron/scheduler picks up work
   - Results written back to files
   - Agent checks on next run

4. **Message Queue Pattern**
   - Agent posts work to queue
   - Separate workers process queue
   - Results posted back
   - Agent polls for completion

---

## Recommendations for Your Use Case

### If You Need: "Start a long-running task and disconnect"

**DON'T Use:** Task with run_in_background: true
- Won't survive your disconnect
- Dies when parent session ends

**DO Use:** One of these patterns:

#### Option A: External Background Job System
```
Claude Agent -> MCP Background Job Server -> Real daemon process
```
- Agent spawns job via MCP
- MCP manages actual process
- Process runs independently
- Agent can check status later

#### Option B: Persistent Container with Active Session
```
Long-running container -> Keep SDK session alive -> Background tasks within session
```
- Container doesn't shut down
- Session stays active (even if you're not interacting)
- Background tasks complete within active session
- You reconnect to same session later

#### Option C: Handoff to External System
```
Agent prepares work -> Writes to queue/file -> External worker processes it
```
- Agent acts as orchestrator
- Real work done outside SDK
- Agent checks results later
- No dependency on agent staying alive

### If You Need: "Parallel tasks during active session"

**DO Use:** Task with run_in_background: true
- Works perfectly for this
- Multiple tasks in parallel
- Continue working while they run
- Get notified when complete

**Just don't expect them to survive session termination.**

### If You Need: "Resume long-running research later"

**DO Use:** Session resumption
- Start research task in background
- Keep session alive (don't exit)
- Resume session later to check progress
- Use persistent container if needed

**Key:** The session must stay alive. Use container infrastructure for this.

---

## Official Statements on Lifecycle (or lack thereof)

### What Anthropic Documentation Says:

**On Background Agents:**
> "Background agents work independently and surface results when done"
> "Press Ctrl+B to move it to the background"
> "Your session continues. The sub-agent works independently."

**What's NOT Said:**
- No mention of process lifecycle
- No statement about parent termination behavior
- No API for process detachment
- No daemon patterns documented

### What Anthropic Documentation Implies:

**From Cleanup Policies:**
> "cleanupPeriodDays setting (default: 30 days)"

Implies: Sessions and their content are temporary, meant to be cleaned up.

**From Container Patterns:**
> "Ephemeral Sessions: Create a new container for each user task, then destroy it when complete"

Implies: Default expectation is task completion before container destruction.

**From Session Management:**
> "Session ID in the initial system message"
> "Use the resume option with a session ID"

Implies: Sessions are resumable but require explicit management. No auto-continuation.

---

## Known Issues and Bugs

### Orphaned Process Problems

Multiple issues report orphaned processes after Claude Code exits:
- Issue #5545: Orphaned bash/nodejs processes drain resources
- Issue #1935: MCP servers not terminated properly

**Status:** Anthropic is working on better cleanup
**Implication:** System is designed to TERMINATE children, but cleanup sometimes fails

### No Auto-Notification

Issue #6854 reports:
- Background bash sessions don't notify parent when complete
- Parent must explicitly check outputs

**Status:** Feature request open
**Implication:** Background tasks need active parent to integrate results

### No Native Task Management

Issue #7069 requests:
- Task listing
- Status monitoring
- Session persistence

**Status:** Feature request, not implemented
**Implication:** Native task management is limited

---

## Key Quotes from Sources

### From claudefa.st/blog (async-workflows):
> "When Claude Code spawns a sub-agent for research or complex analysis, your entire session blocks. You wait while the sub-agent works, unable to continue the conversation."

> "Press Ctrl+B to move it to the background: Your session continues. The sub-agent works independently and surfaces results when done."

**Context:** "Independently" means UI-wise, not process-wise.

### From GitHub Issue #6854:
> "When main agent is instructed to execute commands in background, it does not get notified of the finished terminal sessions. It only checks the outputs after the next prompt."

**Context:** Confirms background tasks need active parent for result integration.

### From GitHub Issue #7069:
> "Task information is lost when Claude Code restarts"

**Context:** Confirms no persistent task state across restarts.

### From platform.claude.com (subagents):
> "You must resume the same session to access the subagent's transcript"

**Context:** Subagents are session-scoped, not independent.

### From platform.claude.com (hosting):
> "The SDK operates as a long-running process that executes commands in a persistent shell environment"

**Context:** The SDK process itself is the unit of persistence, not subagents within it.

---

## Verdict: Architecture Limitations

### The Hard Truth

**Claude Agent SDK's background agent feature is designed for:**
- ✅ Non-blocking UI during active session
- ✅ Parallel execution while you continue working
- ✅ Context isolation for specialized tasks

**It is NOT designed for:**
- ❌ Truly independent daemon processes
- ❌ Tasks that survive parent termination
- ❌ Fire-and-forget asynchronous work
- ❌ Long-running work after disconnect

### Why This Design?

Likely reasons for this architecture:
1. **Simplicity:** Easier to manage lifecycle when parent controls all children
2. **Resource Control:** Prevents runaway processes
3. **Security:** Subagents within same security boundary as parent
4. **Cost Management:** Subagents tied to parent's API key/billing context
5. **Debugging:** Easier to track what processes belong to what session

### The Gap

**What's missing:** A way to "promote" a background task to independent status.

**What users want:**
```
agent = spawn_background_task(...)
agent.detach()  # <- This doesn't exist
# Agent continues even if parent exits
```

**What's available:**
```
agent = spawn_background_task(...)
# Agent dies when parent exits, no way to prevent this
```

---

## Workaround Pattern: The Container Persistence Layer

### The Only Native Way to Achieve Long-Running Background Work

```
┌─────────────────────────────────────────┐
│   Persistent Container (stays alive)     │
│                                           │
│  ┌─────────────────────────────────┐    │
│  │   Claude Agent SDK Process       │    │
│  │   - Main session                 │    │
│  │   - Background subagents         │    │
│  │   - All tied together            │    │
│  └─────────────────────────────────┘    │
│                                           │
│  Container provides:                     │
│  - Process persistence                   │
│  - Session management                    │
│  - Active SDK instance                   │
└─────────────────────────────────────────┘

You disconnect ─┐
                ↓
         Container stays alive
                ↓
         SDK session stays active
                ↓
         Background tasks complete
                ↓
You reconnect to same container/session
                ↓
         Results available
```

### Implementation (from Anthropic's hosting docs):

**Pattern 2: Long-Running Sessions**
- Deploy SDK in persistent container (Modal, E2B, Fly, etc.)
- Keep container alive
- Keep SDK session active within it
- Use session resumption to reconnect
- Background tasks complete within persistent session

**This works because:**
- Container doesn't shut down
- SDK process stays alive
- Session persists
- Subagents can complete within persistent session context

**Limitations:**
- Requires infrastructure (can't use local CLI)
- Must manage container lifecycle
- Cost: Container running time
- Still need active session (can't truly "fire and forget")

---

## Final Answer to Original Questions

### 1. What happens to subprocess when parent session/conversation ends?

**ANSWER: It terminates.**

Background subagents are terminated when the parent session ends. There is no mechanism for subprocess survival beyond parent lifecycle. The Task tool with run_in_background: true creates subprocesses that are tied to the parent's session context.

### 2. Are background agents tied to parent process lifecycle?

**ANSWER: Yes, completely tied.**

Background agents are not independent processes. They are child processes managed within the parent session's lifecycle. When the parent exits:
- Subagent processes are terminated
- Transcripts saved to disk (for later review)
- Session cleanup runs
- No continuation mechanism exists

### 3. What's the recommended pattern for long-running async work?

**ANSWER: No native pattern exists. Use workarounds:**

Anthropic's recommended patterns from hosting docs:
- **Ephemeral sessions:** For tasks that complete during active session
- **Long-running sessions:** Persistent container keeps SDK alive
- **Hybrid sessions:** Ephemeral but hydrated with state

**For true background work that survives parent exit:**
- Use external process management (MCP Background Job Server)
- Use message queue pattern (agent posts work, external workers process)
- Use file-based handoff (agent writes state, cron picks up)
- Deploy SDK in persistent container and keep session active

### 4. Is there documentation about agent process management?

**ANSWER: No comprehensive documentation exists.**

What IS documented:
- Session management (resumption, forking)
- Container hosting patterns
- Subagent creation and tool restrictions
- Security and sandbox configuration

What is NOT documented:
- Subprocess lifecycle relative to parent
- Process daemonization
- Making subagents survive parent termination
- Background task persistence across restarts

**The lack of documentation on "how to make subagents independent" is itself evidence that this capability doesn't exist.**

---

## Research Artifacts Saved

All source documentation saved to:
- /home/debian/second_brain/docs/webresults/

Key documents:
- 20260129-150702_claudefa-st_claude-code-async-background-agents-parallel-tasks.md
- 20260129-150703_platform-claude-com_subagents-in-the-sdk-claude-api-docs.md
- 20260129-150705_github-com_feature-request-native-background-task-management-system-issue-7069-anthropics-claude-code-github.md
- 20260129-150706_github-com_feature-request-non-blocking-tasks-notification-to-main-agent-when-a-background-bash-session-finishes-issue-6854-anthropics-claude-code-github.md
- 20260129-150713_platform-claude-com_session-management-claude-api-docs.md
- 20260129-150714_platform-claude-com_hosting-the-agent-sdk-claude-api-docs.md
- 20260129-150714_dev-to_the-task-tool-claude-code-s-agent-orchestration-system-dev-community.md

---

## Sources Consulted

### Official Documentation
- https://platform.claude.com/docs/en/agent-sdk/subagents
- https://platform.claude.com/docs/en/agent-sdk/sessions
- https://platform.claude.com/docs/en/agent-sdk/hosting
- https://platform.claude.com/docs/en/agent-sdk/overview
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/interactive-mode
- https://code.claude.com/docs/en/common-workflows

### Community Resources
- https://claudefa.st/blog/guide/agents/async-workflows
- https://dev.to/bhaidar/the-task-tool-claude-codes-agent-orchestration-system-4bf2
- https://www.cometapi.com/how-to-create-and-use-subagents-in-claude-code/
- https://inventivehq.com/knowledge-base/claude/how-to-use-background-agents
- https://github.com/ruvnet/claude-flow/wiki/background-commands
- https://github.com/dylan-gluck/mcp-background-job

### GitHub Issues (Anthropic/claude-code)
- Issue #6854: non-blocking tasks / notification to main agent
- Issue #7069: Native Background Task Management System
- Issue #5545: Orphaned Processes Persist After Claude Code Execution
- Issue #1770: Enable Parent-Child Agent Communication and Monitoring
- Issue #1935: MCP servers not properly terminated
- Issue #2954: Context persistence across sessions
- Issue #7584: Add Persistent Session Storage

### Engineering Blog Posts
- https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk
- https://www.anthropic.com/engineering/claude-code-best-practices

---

## Conclusion

The Claude Agent SDK's background agent feature is powerful for parallelizing work during an active session, but it fundamentally does not support independent, long-running processes that survive parent termination.

**If your use case requires:**
- Starting a task and disconnecting
- Fire-and-forget background work
- Daemon-style processes
- Tasks that survive parent exit

**Then you must use workarounds:**
- External process management systems (MCP Background Job)
- Persistent container infrastructure with active sessions
- Message queue patterns with external workers
- File-based task handoff to external schedulers

**The SDK's design philosophy prioritizes:**
- Managed lifecycle (parent controls children)
- Resource accountability (subagents use parent's API context)
- Security boundaries (all processes under same control)
- Debugging simplicity (clear parent-child relationships)

This is a deliberate architectural choice, not a bug or missing feature. It's unlikely to change without significant redesign of the session and process management system.

For production use cases requiring true background/daemon behavior, you need to build or use infrastructure external to the Claude Agent SDK itself.
