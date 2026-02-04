# Second Brain File Structure Rules

## Folder Hierarchy

```
00_Inbox/           # Temporary landing zone - process within 24-48h
  agent_outputs/    # Where scheduled agents write their results
  
10_Active_Projects/ # Projects with defined goals and end dates
  {project-name}/   # kebab-case, no number prefixes
    README.md       # Required: goal, status, next steps
    
20_Areas/           # Ongoing responsibilities (no end date)
  {area-name}/      # kebab-case, descriptive names
  
30_Incubator/       # Ideas not yet projects - low commitment exploration

.99_Archive/        # Completed/abandoned work (hidden, prefix dot)

.claude/            # Claude system directory
  docs/             # Reference documentation
  logs/             # System logs (auto-managed)
  .secrets/         # Credentials (gitignored)
```

## Naming Conventions

1. **Folders**: `kebab-case` (lowercase, hyphens)
2. **Files**: `snake_case.md` or `kebab-case.md` (consistent within folder)
3. **No number prefixes** on project/area folders (the parent folder provides context)
4. **README.md required** in every active project folder

## What Goes Where

| Content Type | Location |
|--------------|----------|
| New stuff to process | `00_Inbox/` |
| Time-bound work with goals | `10_Active_Projects/` |
| Ongoing life areas | `20_Areas/` |
| "Maybe someday" ideas | `30_Incubator/` |
| Done/abandoned | `.99_Archive/` |
| Agent task outputs | `00_Inbox/agent_outputs/` |

## Anti-Patterns to Fix

- ❌ Duplicate folders for same concept (merge them)
- ❌ Number prefixes on subfolders (`11_Career` → `career-pivot`)
- ❌ Root-level orphan folders (move to appropriate parent)
- ❌ Empty folders with no README (delete or document)
- ❌ Stale projects with no activity 30+ days (archive or update)

## Weekly Maintenance Checklist

1. Process `00_Inbox/` - nothing older than 48h
2. Check for duplicate/overlapping folders
3. Verify active projects have recent activity or archive them
4. Remove empty folders
5. Ensure naming conventions are followed
