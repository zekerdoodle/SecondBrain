# Background Agent Creation Playbook

Follow these steps to create a new background agent. Total time: 1-2 hours of CC work.

## Step 1: Design (10 min)

Before writing code, answer these questions:

- **What does this agent process?** (Input format)
- **What does it produce?** (Output JSON schema)
- **How often should it run?** (Trigger frequency)
- **What side effects does it have?** (File writes, state updates, notifications)
- **What model does it need?** (Haiku for simple extraction, Sonnet for nuanced analysis)

## Step 2: Config + Prompt (15 min)

1. Copy this `_template/` directory to a new folder:
   ```
   cp -r .claude/agents/background/_template/ .claude/agents/background/{your_agent}/
   ```

2. Edit `config.yaml`:
   - Set `name` to your agent's name
   - Set `description`
   - Choose `model` (sonnet, haiku, opus)
   - Set `timeout_seconds` (120 for simple, 300 for complex)
   - Define `output_format` schema

3. Edit `prompt.md`:
   - Write a clear system prompt
   - Define input/output format expectations
   - Include processing rules and constraints

## Step 3: Build Runner (30-60 min)

1. Edit `template_runner.py` → rename to `{agent_name}_runner.py`
2. Customize the marked sections:
   - `AGENT_NAME` constant
   - `OUTPUT_SCHEMA` to match your config.yaml
   - `_build_input()` to format your data as a prompt
   - `apply_side_effects()` to do file writes, state updates, etc.
3. Add any domain-specific logic

## Step 4: Wire Invocation (15 min)

Choose one or both:

### Option A: MCP Tool (for on-demand invocation)
Create a tool in `interface/server/mcp_tools/` that calls your runner's `run_full_cycle()`.

### Option B: Scheduler Only (for periodic runs)
Add a scheduled task via `schedule_agent` or directly in `scheduled_tasks.json`.

## Step 5: Schedule (5 min)

```python
schedule_agent(
    agent="{agent_name}",
    prompt="Your task description",
    schedule="daily at 3:00am",  # or "every 2 hours", etc.
    silent=True  # Background agents are silent by default
)
```

## Step 6: Verify (10 min)

1. Run one cycle manually (call `run_full_cycle()` from a test script)
2. Check the output is valid JSON matching your schema
3. Verify side effects worked (files written, state updated)
4. Enable the schedule

## Reference: Existing Background Agents

| Agent | Model | Frequency | Input | Output | Side Effects |
|-------|-------|-----------|-------|--------|-------------|
| Librarian | Sonnet | Every 20 min | Conversation exchanges | Extracted memories (JSON) | Writes to LTM store |
| Gardener | Sonnet | Daily 3 AM | Thread/atom data | Maintenance actions (JSON) | Updates LTM threads |
| Chronicler | Haiku | Every 2 hours | Thread metadata | Thread descriptions (JSON) | Updates thread descriptions |

## Common Patterns

- **No tools**: Background agents NEVER get tools. All side effects happen in the runner.
- **JSON output**: Always use `output_format` with a JSON schema. Agents follow schemas reliably.
- **Idempotent**: Design runners to be safe to re-run. Use timestamps and deduplication.
- **Silent**: Background agents are silent by default. Only notify if something exceptional happens.
- **Logging**: Use `logger.info()` and `logger.error()` — these show up in server logs.
