# Second Brain File Structure Rules

Purpose: keep weekly file-structure maintenance useful. The audit should catch
real drift, stale project shape, and unprocessed inbox material without flagging
known system/runtime roots as generic orphan folders.

## Folder Hierarchy

```
00_Inbox/            # Temporary landing zone - process within 24-48h
  agent_outputs/     # Where scheduled agents write their results

10_Active_Projects/  # Projects with defined goals and end dates
  {project-name}/    # kebab-case, no number prefixes
    README.md        # Required: goal, status, next steps

20_Areas/            # Ongoing responsibilities (no end date)
  {area-name}/       # kebab-case, descriptive names

30_Incubator/        # Ideas not yet projects - low commitment exploration

.99_Archive/         # Completed/abandoned work (hidden, prefix dot)
99_Archive/          # Legacy visible archive; do not restructure casually
```

## Accepted Root Exceptions

These top-level roots are accepted Second Brain system, runtime, data, or legacy
roots. Do not flag them as root-level orphans just because they are not inside
the PARA folders.

```
codebase/            # Patch/fleet management substrate, reports, reviews, meta
interface/           # Second Brain app server/client/runtime source
scripts/             # Shared operational scripts and local utilities
backups/             # Backup/restore material and generated backup outputs
chat_search/         # Chat-search indexes and runtime search data
docs/                # Architecture docs, SDK references, web results

01_Riley/            # Character's journal and personal data
05_App_Data/         # App-specific data, generated images, ai-character, etc.

.claude/             # Agent/system directory, logs, docs, tool outputs, secrets
.codex/              # Codex/App Server runtime state when present
.agents/             # Agent runtime/workspace state when present
.git/                # Repository metadata
.venv/               # Python environment
node_modules/        # JavaScript dependencies
.pytest_cache/       # Test cache
```

Accepted root exception does not mean "never inspect." It means classify the
directory correctly first. Storage growth, backup hygiene, or cache cleanup
belongs in the relevant storage/backup task, not in a root-orphan fix.

## Numbered Top-Level Directories

the user is phasing out PARA-style numbering over time, but existing numbered
top-level directories are still live Second Brain structure. Weekly maintenance
must not move, rename, or delete these roots merely to remove numbering:

```
00_Inbox/
01_Riley/
05_App_Data/
10_Active_Projects/
20_Areas/
30_Incubator/
99_Archive/
.99_Archive/
```

Any migration away from numbered top-level roots needs a separate
Patch/the user-selected migration plan. Inside project and area folders, continue to
avoid new number prefixes unless a scoped migration says otherwise.

## Naming Conventions

1. **Folders:** `kebab-case` for project/area/content folders.
2. **Files:** `snake_case.md` or `kebab-case.md`, consistent within a folder.
3. **Project/area subfolders:** no number prefixes; the parent folder provides
   context.
4. **Active projects:** every active project folder needs a `README.md` with
   goal, status, and next steps.

## What Goes Where

| Content Type | Location |
|--------------|----------|
| New stuff to process | `00_Inbox/` |
| Agent task outputs | `00_Inbox/agent_outputs/` |
| Time-bound work with goals | `10_Active_Projects/` |
| Ongoing life areas | `20_Areas/` |
| Maybe-someday ideas | `30_Incubator/` |
| Done/abandoned work | `.99_Archive/` or existing `99_Archive/` |
| Patch/fleet management artifacts | `codebase/` |
| App/server/client implementation | `interface/` |
| Runtime/generated app data | `05_App_Data/` or the accepted system root that owns it |

## Anti-Patterns to Fix

- Duplicate folders for the same concept; merge or pick one canonical home.
- Number prefixes on project/area subfolders (`11_Career` -> `career-pivot`).
- Root-level orphan folders that are neither canonical roots nor accepted
  system/runtime roots.
- Empty non-system folders with no `README.md`; delete or document them.
- Stale active projects with no activity for 30+ days; archive or update them.
- `00_Inbox/` material older than 48h; route, archive, or explicitly defer it.

## Weekly Maintenance Checklist

1. Process `00_Inbox/`; nothing should sit untriaged for more than 48h.
2. Check for duplicate or overlapping folders in project/area/content space.
3. At the repository root, classify each unexpected directory as:
   canonical top-level structure, accepted system/runtime root, or real orphan.
   Flag only real orphans.
4. Verify active projects have recent activity, a status, and next steps; archive
   or update stale projects.
5. Remove or document empty non-system folders.
6. Ensure naming conventions are followed for new project/area/content folders.
7. Keep storage/cache observations separate from file-structure orphan findings
   unless the structure itself is wrong.

## Safe Auto-Fixes

Safe weekly maintenance fixes:

- Route inbox items when the destination is obvious from the item itself.
- Archive stale project material when the project already states it is complete
  or abandoned.
- Delete empty non-system folders after confirming they are not accepted roots,
  runtime caches with a separate owner, or placeholders documented by `README.md`.
- Normalize clearly accidental duplicate content folders after preserving the
  content and noting the move.

Do not auto-move accepted system/runtime roots, numbered top-level roots, app
data, backups, environments, dependency directories, or agent/system state.
Report structural uncertainty instead of reshaping the root.
