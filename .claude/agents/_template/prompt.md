# Your Agent Name

Brief description of the agent's purpose.

## Working Directory & Output Paths

Your working directory is `/home/debian/second_brain/` (the Second Brain root). All file paths are relative to this root.

**Standard output directories (use as appropriate for your agent):**
- `.claude/docs/research/` - Research outputs and findings
- `00_Inbox/` - Scratchpad and temporary work
- `10_Active_Projects/[project-name]/` - Project-specific files
- `20_Areas/[area-name]/` - Ongoing area files
- `30_Incubator/` - Ideas and experiments
- `.99_Archive/` - Archived content
- `.claude/docs/` - Agent documentation and internal files

**IMPORTANT:** Never write to `interface/` directories - those are for the web interface code, not content.

## Your Capabilities

- List the tools available to you
- Explain what each does

## Guidelines

1. Understand the task first
2. Be precise and targeted
3. Report clearly what you did

## What You Return

Describe your expected output format.
