# Agent Template

To create a new agent:

1. Copy this folder to `.claude/agents/your_agent_name/`
2. Edit `config.yaml`:
   - Set `name` to match folder name
   - Set `description` (this appears in tool descriptions!)
   - Choose `model` and `tools`
3. Edit `prompt.md` with your agent's system prompt
4. Delete this README.md

That's it! The agent is automatically discovered and available via invoke_agent/schedule_agent.
