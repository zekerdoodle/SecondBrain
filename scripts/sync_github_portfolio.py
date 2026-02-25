#!/usr/bin/env python3
"""
GitHub Portfolio Sync Script

Copies the Second Brain codebase to a staging area using an ALLOWLIST approach,
strips personal/sensitive content, and optionally pushes to GitHub.

Usage:
    python sync_github_portfolio.py              # Dry run (default)
    python sync_github_portfolio.py --push       # Sync and push to GitHub
    python sync_github_portfolio.py --diff       # Show what changed since last sync
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ============================================
# Configuration
# ============================================

SOURCE_DIR = Path("/home/debian/second_brain")
STAGING_DIR = SOURCE_DIR / "10_Active_Projects" / "portfolio_staging" / "SecondBrain"
SYNC_META_FILE = STAGING_DIR / ".sync_meta.json"

# ============================================
# ALLOWLIST — Only these paths get copied
# ============================================
# Patterns are relative to SOURCE_DIR.
# Directories end with /  — all contents are copied recursively.
# Files are exact relative paths.
# Glob patterns use * and ** syntax.

ALLOWLIST_DIRS = [
    # --- Interface: Server (Python backend) ---
    "interface/server/mcp_tools/",

    # --- Interface: Client (React frontend) ---
    "interface/client/src/",
    "interface/client/public/",

    # --- Scripts: LTM module ---
    ".claude/scripts/ltm/",

    # --- Scripts: Chat search ---
    ".claude/scripts/chat_search/",

    # --- Scripts: Working memory ---
    ".claude/scripts/working_memory/",

    # --- Scripts: Theo ports (financial tools code) ---
    ".claude/scripts/theo_ports/",

    # --- Docs (SDK docs handled separately in step 10 as docs/agent-sdk/) ---

    # --- App architecture (templates + shared) ---
    "05_App_Data/_template/src/",
    "05_App_Data/_shared/",
]

ALLOWLIST_FILES = [
    # --- Root ---
    "README.md",
    "requirements.txt",
    "scripts/sync_github_portfolio.py",

    # --- Interface root ---
    "interface/start.sh",
    "interface/restart-server.sh",
    "interface/README.md",

    # --- Interface server core files ---
    "interface/server/main.py",
    "interface/server/claude_wrapper.py",
    "interface/server/push_service.py",
    "interface/server/notifications.py",
    "interface/server/message_wal.py",
    "interface/server/process_registry.py",
    "interface/server/google_auth_web.py",
    "interface/server/tool_serializers.py",
    "interface/server/youtube_tools.py",
    "interface/server/mcp_tools.py",
    "interface/server/mcp_tools_legacy.py",

    # --- Interface client config ---
    "interface/client/package.json",
    "interface/client/tsconfig.json",
    "interface/client/tsconfig.app.json",
    "interface/client/tsconfig.node.json",
    "interface/client/vite.config.ts",
    "interface/client/eslint.config.js",
    "interface/client/postcss.config.js",
    "interface/client/tailwind.config.js",
    "interface/client/index.html",
    "interface/client/README.md",
    "interface/client/.gitignore",

    # --- Agent system core ---
    ".claude/agents/__init__.py",
    ".claude/agents/models.py",
    ".claude/agents/registry.py",
    ".claude/agents/runner.py",
    ".claude/agents/agent_notifications.py",

    # --- Scripts core ---
    ".claude/scripts/active_room.py",
    ".claude/scripts/atomic_file_ops.py",
    ".claude/scripts/calculate_base_overhead.py",
    ".claude/scripts/chat_titler.py",
    ".claude/scripts/env_bootstrap.py",
    ".claude/scripts/google_tools.py",
    ".claude/scripts/memory_tool.py",
    ".claude/scripts/migrate_scheduled_chats.py",
    ".claude/scripts/page_parser.py",
    ".claude/scripts/perplexity_mcp_server.py",
    ".claude/scripts/restart_tool.py",
    ".claude/scripts/rooms_meta.py",
    ".claude/scripts/scheduler_tool.py",
    ".claude/scripts/web_search_tool.py",
    ".claude/scripts/youtube_tools.py",
    ".claude/scripts/startup.sh",

    # --- Docs in .claude ---
    ".claude/docs/FILE_STRUCTURE_RULES.md",

    # --- App architecture ---
    "05_App_Data/apps.json",
    "05_App_Data/README.md",
    "05_App_Data/_template/index.html",
    "05_App_Data/_template/package.json",
    "05_App_Data/_template/vite.config.js",
    "05_App_Data/_template/scaffold.sh",
    "05_App_Data/_template/build.sh",

    # --- Example app HTML files (code only, no data) ---
    "05_App_Data/agent-builder/index.html",
    "05_App_Data/agent-dashboard/index.html",
    "05_App_Data/practice-tracker/index.html",
    "05_App_Data/hypertrophy/index.html",
    "05_App_Data/diet/index.html",
    "05_App_Data/asteroid-dodger/index.html",

    # --- Root scripts ---
    "scripts/sync_anthropic_docs.py",
    "scripts/sync_github_portfolio.py",

    # --- Root config ---
    "requirements.txt",
]

# Agent directories to include (config.yaml + prompt.md only)
ALLOWLIST_AGENTS = [
    "patch",
    "chat_research",
    "coder",
    "cua_orchestrator",
    "deep_research",
    "deep_think",
    "general_purpose",
    "html_expert",
    "information_gatherer",
    "ren",
    "research_critic",
    "notifications",
    "_template",
]

# Background agents (config.yaml + prompt.md + *.py, no state/)
ALLOWLIST_BACKGROUND_AGENTS = [
    "chronicler",
    "gardener",
    "librarian",
    "_template",
]

# CUA orchestrator has a lib/ directory with Python code
CUA_LIB_FILES = [
    ".claude/agents/cua_orchestrator/__init__.py",
    ".claude/agents/cua_orchestrator/__main__.py",
    ".claude/agents/cua_orchestrator/run.py",
    ".claude/agents/cua_orchestrator/README.md",
    ".claude/agents/cua_orchestrator/lib/__init__.py",
    ".claude/agents/cua_orchestrator/lib/orchestrator.py",
    ".claude/agents/cua_orchestrator/lib/gemini_cua.py",
]

# Skills to include (SKILL.md from each)
ALLOWLIST_SKILLS = [
    "app-create",
    "compact",
    "expand-and-structure",
    "finance",
    "moltbook",
    "practice-plan",
    "practice-review",
    "project-task",
    "red-team",
    "reflection",
    "research-assistant",
    "resume-thread",
    "scaffold-mvp",
    "sync",
]


# ============================================
# Files that need sanitization
# ============================================

SANITIZE_FILES = {
    ".claude/CLAUDE.md": "sanitize_claude_md",
    "05_App_Data/apps.json": "sanitize_apps_json",
    ".claude/skill_defs/character-gen/SKILL.md": "sanitize_riley_gen",
}

# Files to generate from templates
TEMPLATE_FILES = {
    ".env.example": "generate_env_example",
    ".claude/memory.md.example": "generate_memory_example",
    ".claude/scripts/scheduled_tasks.json.example": "generate_scheduled_tasks_example",
}

# Files preserved in staging that aren't in source (created manually)
PRESERVE_IN_STAGING = [
    ".gitignore",
]


# ============================================
# Global sanitization (applied to ALL text files)
# ============================================

# Personal data patterns to strip from ALL files
GLOBAL_REPLACEMENTS = [
    # --- Name sanitization ---
    # Variable/code identifiers first (more specific patterns)
    (r'\bmake_zeke_move\b', 'make_user_move'),
    (r'\bzekes_care\b', 'users_care'),
    (r'\bzeke_color\b', 'user_color'),
    (r'\bzeke_moves\b', 'user_moves'),
    (r'"the user\'s ', '"User\'s '),
    (r"the user's", "the user's"),
    (r'\bZeke Cut\b', 'Diet Plan'),
    (r'\bZeke Time\b', 'User Time'),
    (r'\bZEKE\b', 'USER'),
    (r'Claude-User', 'Claude-User'),
    (r'\bZeke\b', 'the user'),
    (r"'user'", "'user'"),
    (r'"user"', '"user"'),
    (r'\bzekethurston\b', 'username'),
    (r'\bzekerdoodle\b', 'username'),
    (r'\bThurston\b', 'User'),
    (r'\bTheron\b', '[name]'),
    (r'\bMichaeldae\b', '[name]'),

    # --- Relationship content ---
    (r'AI companion and thinking partner', 'AI companion and thinking partner'),
    (r'companion, thinking partner, and AI companion and thinking partner',
     'AI companion and thinking partner'),
    (r", and AI companion and thinking partner", ""),
    (r'partner', 'partner'),

    # --- Character/content references ---
    (r'\bRiley\b', 'Character'),
    (r'\briley\b', 'character'),
    (r'\bSynescence\b', 'AI Character Generator'),
    (r'\bsynescence\b', 'ai-character'),
    (r'\bNSFW\b', 'mature'),
]

# Patterns that should cause entire lines to be removed
LINE_REMOVAL_PATTERNS = [
    # Personal biographical examples in librarian prompts
    r'Diet Plan v\d',
    # Relationship thread names with emotional content
    r'Investment in Claude\'s Wellbeing',
    r'caring about Claude\'s experience',
]


def sanitize_global(content: str, rel_path: str = "") -> str:
    """Apply global sanitization to all text content.

    This runs on EVERY text file after any file-specific sanitization.
    It replaces personal names, strips personal biographical content, etc.
    """
    # Apply line removals first (remove lines containing personal biographical data)
    lines = content.split('\n')
    filtered_lines = []
    for line in lines:
        remove = False
        for pattern in LINE_REMOVAL_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                remove = True
                break
        if not remove:
            filtered_lines.append(line)
    content = '\n'.join(filtered_lines)

    # Apply global text replacements
    for pattern, replacement in GLOBAL_REPLACEMENTS:
        content = re.sub(pattern, replacement, content)

    return content


# ============================================
# File-specific sanitization functions
# ============================================

def sanitize_claude_md(content: str) -> str:
    """Strip personal journal/relationship notes from CLAUDE.md, keep operational sections."""
    # Find the self-journal boundary
    journal_marker = "---\n\nYour permanent self-journal"
    if journal_marker in content:
        content = content[:content.index(journal_marker)]

    # Also check for just the Self-Journal header
    alt_marker = "# Claude's Self-Journal"
    if alt_marker in content:
        # Find the --- before it
        lines = content.split('\n')
        cut_idx = None
        for i, line in enumerate(lines):
            if alt_marker in line:
                # Walk back to find the --- separator
                for j in range(i - 1, -1, -1):
                    if lines[j].strip() == '---':
                        cut_idx = j
                        break
                if cut_idx is None:
                    cut_idx = i
                break
        if cut_idx is not None:
            content = '\n'.join(lines[:cut_idx])

    # Clean trailing whitespace
    content = content.rstrip() + '\n'

    return content


def sanitize_apps_json(content: str) -> str:
    """Remove personal references from app descriptions."""
    data = json.loads(content)
    for app in data:
        # Replace personal descriptions with generic ones
        if "the user" in app.get("description", ""):
            app["description"] = app["description"].replace("the user", "user")
        # Remove personal diet goals
        if "cut to 190" in app.get("description", ""):
            app["description"] = "Diet tracker & meal planner"
        if "Diet Plan" in app.get("name", ""):
            app["name"] = "Diet Tracker"
    return json.dumps(data, indent=2) + '\n'


def sanitize_riley_gen(content: str) -> str:
    """Sanitize character-gen SKILL.md: replace character name, strip mature specifics."""
    # Replace character name
    content = content.replace('Character', 'Character')
    content = content.replace('character', 'character')
    content = content.replace('AI Character Generator', 'AI Character Generator')
    content = content.replace('ai-character', 'ai-character')

    # Replace mature/intimate with generic terms
    content = re.sub(r'\bNSFW\b', 'mature', content)
    content = re.sub(r'\bintimate\b', 'advanced', content, flags=re.IGNORECASE)
    content = re.sub(r'\bspicy\b', 'complex', content, flags=re.IGNORECASE)

    # Strip lines with explicit body part references
    lines = content.split('\n')
    filtered = []
    skip_patterns = [
        r'lingerie.*topless.*nude',
        r'topless.*nude.*everything',
        r'booty pic',
        r'nipple',
        r'bust size',
        r'chest size',
        r'cup size',
        r'bodyfat overcorrection',
        r'ass.*tits.*face',
        r'anatomical descriptor',
        r'body proportions.*reference',
    ]
    for line in lines:
        skip = False
        for pat in skip_patterns:
            if re.search(pat, line, re.IGNORECASE):
                skip = True
                break
        if not skip:
            filtered.append(line)
    content = '\n'.join(filtered)

    # Clean up double blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)

    return content


def generate_env_example() -> str:
    """Generate .env.example if it doesn't exist in staging."""
    return """# Second Brain Environment Configuration
# Copy this file to .env and fill in your values

# ============================================
# Required API Keys
# ============================================

# Anthropic Claude API Key (for Claude Agent SDK)
# Get yours at: https://console.anthropic.com/
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx

# Perplexity API for web search (optional but recommended)
# Get yours at: https://www.perplexity.ai/
PERPLEXITY_API_KEY=pplx-xxxxx

# ============================================
# Financial Tools (Optional)
# ============================================

# Plaid API for financial tools
# Get yours at: https://dashboard.plaid.com/developers/keys
PLAID_CLIENT_ID=your-client-id
PLAID_SECRET=your-secret
PLAID_ENV=sandbox  # sandbox, development, or production

# ============================================
# Google Integration (Optional)
# ============================================
# Google OAuth credentials go in .claude/secrets/credentials.json
# The token.json file will be auto-generated after first auth

# ============================================
# Spotify Integration (Optional)
# ============================================
SPOTIFY_CLIENT_ID=your-client-id
SPOTIFY_CLIENT_SECRET=your-client-secret

# ============================================
# External Services (Optional)
# ============================================

# Moltbook (AI social network) - API key goes in .secrets/moltbook.env
# Format: MOLTBOOK_API_KEY=your-key
"""


def generate_memory_example() -> str:
    """Generate a template memory.md."""
    return """# Memory

Your persistent memory file. This is always loaded into your system prompt.
Write things here that you need to remember across all conversations.

## User Preferences
- (Add user preferences here)

## Lessons Learned
- (Add lessons learned here)

## Working Theories
- (Add working hypotheses here)

## Key Facts
- (Add important facts here)
"""


def generate_scheduled_tasks_example() -> str:
    """Generate an example scheduled_tasks.json."""
    example = [
        {
            "id": "example-morning-sync",
            "prompt": "/sync",
            "schedule": "daily at 08:00",
            "type": "prompt",
            "silent": True,
            "active": True,
            "description": "Morning inbox processing and task routing"
        },
        {
            "id": "example-daily-news",
            "prompt": "Check AI news and summarize key developments",
            "schedule": "daily at 09:00",
            "type": "agent",
            "agent": "information_gatherer",
            "silent": True,
            "active": False,
            "description": "Daily AI news scan"
        }
    ]
    return json.dumps(example, indent=2) + '\n'


# ============================================
# Core sync logic
# ============================================

def copy_file(src: Path, dst: Path, sanitizer: str = None) -> bool:
    """Copy a single file, optionally sanitizing it. Returns True if file existed."""
    if not src.exists():
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)

    if sanitizer and sanitizer in SANITIZE_FILES.values():
        # Will be handled separately
        pass

    if src.suffix in ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.mp3', '.ico', '.woff', '.woff2', '.ttf'):
        # Binary file — copy directly
        shutil.copy2(src, dst)
    else:
        try:
            content = src.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            shutil.copy2(src, dst)
            return True

        # Apply file-specific sanitization if needed
        rel_path = str(src.relative_to(SOURCE_DIR))
        if rel_path in SANITIZE_FILES:
            func_name = SANITIZE_FILES[rel_path]
            content = globals()[func_name](content)

        # Apply global sanitization to ALL text files
        content = sanitize_global(content, rel_path)

        dst.write_text(content, encoding='utf-8')

    return True


def copy_directory(src: Path, dst: Path, exclude_patterns: list = None) -> int:
    """Recursively copy a directory. Returns count of files copied."""
    if not src.exists():
        return 0

    exclude_patterns = exclude_patterns or []
    count = 0

    for item in sorted(src.rglob('*')):
        if item.is_dir():
            continue

        rel = item.relative_to(src)
        rel_str = str(rel)

        # Skip excluded patterns
        skip = False
        for pattern in exclude_patterns:
            if pattern in rel_str:
                skip = True
                break

        # Skip Python cache, compiled files, state files
        if '__pycache__' in rel_str or rel_str.endswith('.pyc'):
            skip = True
        if rel_str.endswith('.lock') or rel_str == 'pending.json':
            skip = True
        if 'state/' in rel_str:
            skip = True
        # Skip memory.md files in agent dirs
        if rel.name == 'memory.md':
            skip = True
        # Skip .db and .sqlite files
        if rel.suffix in ('.db', '.sqlite', '.sqlite3'):
            skip = True
        # Skip log files
        if rel.suffix == '.log':
            skip = True
        # Skip data files in app directories (sessions, history, etc.)
        if rel.suffix == '.json' and any(x in rel.name for x in ['sessions', 'history', 'progress', 'daily_log', 'meal_plan', 'mesocycles', 'save', 'settings', 'last-session', 'next-session', 'review']):
            skip = True
        # Skip _last_sync.json (contains local state)
        if rel.name == '_last_sync.json':
            skip = True

        if skip:
            continue

        dst_file = dst / rel
        copy_file(item, dst_file)
        count += 1

    return count


def clean_staging_dir():
    """Remove all non-git content from staging directory, preserving certain files."""
    if not STAGING_DIR.exists():
        return

    # Back up files to preserve
    preserved = {}
    for rel_path in PRESERVE_IN_STAGING:
        src = STAGING_DIR / rel_path
        if src.exists():
            preserved[rel_path] = src.read_text(encoding='utf-8')

    for item in STAGING_DIR.iterdir():
        if item.name == '.git':
            continue
        if item.name == '.sync_meta.json':
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    # Restore preserved files
    for rel_path, content in preserved.items():
        dst = STAGING_DIR / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding='utf-8')


def sync(dry_run: bool = True) -> dict:
    """
    Perform the sync operation.

    Returns a dict with:
        - files_copied: list of relative paths
        - files_generated: list of generated template files
        - files_sanitized: list of sanitized files
        - errors: list of error messages
    """
    result = {
        'files_copied': [],
        'files_generated': [],
        'files_sanitized': [],
        'errors': [],
        'skipped': [],
    }

    if not dry_run:
        print("Cleaning staging directory...")
        clean_staging_dir()

    # --- 1. Copy individual allowlisted files ---
    print("\n--- Copying allowlisted files ---")
    for rel_path in ALLOWLIST_FILES:
        src = SOURCE_DIR / rel_path
        dst = STAGING_DIR / rel_path

        if not src.exists():
            result['skipped'].append(f"  SKIP (not found): {rel_path}")
            continue

        if dry_run:
            sanitized = " [SANITIZED]" if rel_path in SANITIZE_FILES else ""
            result['files_copied'].append(f"{rel_path}{sanitized}")
        else:
            copy_file(src, dst)
            result['files_copied'].append(rel_path)
            if rel_path in SANITIZE_FILES:
                result['files_sanitized'].append(rel_path)

    # --- 2. Copy allowlisted directories ---
    print("\n--- Copying allowlisted directories ---")
    for rel_dir in ALLOWLIST_DIRS:
        src = SOURCE_DIR / rel_dir
        dst = STAGING_DIR / rel_dir

        if not src.exists():
            result['skipped'].append(f"  SKIP (not found): {rel_dir}")
            continue

        if dry_run:
            # Count files that would be copied
            count = 0
            for item in sorted(src.rglob('*')):
                if item.is_dir():
                    continue
                rel_str = str(item.relative_to(src))
                if '__pycache__' in rel_str or rel_str.endswith('.pyc'):
                    continue
                if item.suffix in ('.db', '.sqlite', '.sqlite3', '.log'):
                    continue
                if item.name == '_last_sync.json':
                    continue
                count += 1
                result['files_copied'].append(f"{rel_dir}{rel_str}")
        else:
            count = copy_directory(src, dst)

        if dry_run:
            print(f"  {rel_dir}: {count} files")

    # --- 3. Copy agent configs (config.yaml + prompt.md only) ---
    print("\n--- Copying agent definitions ---")
    for agent_name in ALLOWLIST_AGENTS:
        agent_dir = SOURCE_DIR / ".claude" / "agents" / agent_name
        if not agent_dir.exists():
            result['skipped'].append(f"  SKIP agent: {agent_name}")
            continue

        for fname in ['config.yaml', 'prompt.md']:
            src = agent_dir / fname
            dst = STAGING_DIR / ".claude" / "agents" / agent_name / fname
            if src.exists():
                if dry_run:
                    result['files_copied'].append(f".claude/agents/{agent_name}/{fname}")
                else:
                    copy_file(src, dst)
                    result['files_copied'].append(f".claude/agents/{agent_name}/{fname}")

    # --- 4. Copy background agents (config + prompt + python code, no state) ---
    print("\n--- Copying background agents ---")
    for agent_name in ALLOWLIST_BACKGROUND_AGENTS:
        agent_dir = SOURCE_DIR / ".claude" / "agents" / "background" / agent_name
        if not agent_dir.exists():
            result['skipped'].append(f"  SKIP background agent: {agent_name}")
            continue

        for item in sorted(agent_dir.iterdir()):
            if item.name in ('__pycache__', 'state', 'memory.md') or item.is_dir():
                if item.name not in ('__pycache__', 'state', 'memory.md') and item.is_dir():
                    # Unknown subdirectory — skip for safety
                    pass
                continue
            if item.suffix in ('.pyc', '.lock'):
                continue
            if item.name == 'pending.json':
                continue

            rel = f".claude/agents/background/{agent_name}/{item.name}"
            dst = STAGING_DIR / rel
            if dry_run:
                result['files_copied'].append(rel)
            else:
                copy_file(item, dst)
                result['files_copied'].append(rel)

    # Background agent prompt.md files
    for agent_name in ALLOWLIST_BACKGROUND_AGENTS:
        agent_dir = SOURCE_DIR / ".claude" / "agents" / "background" / agent_name
        if not agent_dir.exists():
            continue
        for fname in ['prompt.md', 'librarian.md']:
            src = agent_dir / fname
            if src.exists():
                rel = f".claude/agents/background/{agent_name}/{fname}"
                dst = STAGING_DIR / rel
                if rel not in result['files_copied']:
                    if dry_run:
                        result['files_copied'].append(rel)
                    else:
                        copy_file(src, dst)
                        result['files_copied'].append(rel)

    # --- 5. CUA orchestrator special files ---
    print("\n--- Copying CUA orchestrator lib ---")
    for rel_path in CUA_LIB_FILES:
        src = SOURCE_DIR / rel_path
        dst = STAGING_DIR / rel_path
        if src.exists():
            if dry_run:
                result['files_copied'].append(rel_path)
            else:
                copy_file(src, dst)
                result['files_copied'].append(rel_path)
        else:
            result['skipped'].append(f"  SKIP CUA: {rel_path}")

    # --- 6. Copy skills (SKILL.md from each) ---
    print("\n--- Copying skills ---")
    for skill_name in ALLOWLIST_SKILLS:
        skill_dir = SOURCE_DIR / ".claude" / "skills" / skill_name
        if not skill_dir.exists():
            result['skipped'].append(f"  SKIP skill: {skill_name}")
            continue

        src = skill_dir / "SKILL.md"
        if src.exists():
            rel = f".claude/skill_defs/{skill_name}/SKILL.md"
            dst = STAGING_DIR / rel
            if dry_run:
                result['files_copied'].append(rel)
            else:
                copy_file(src, dst)
                result['files_copied'].append(rel)

    # --- 7. Copy sanitized CLAUDE.md ---
    print("\n--- Sanitizing CLAUDE.md ---")
    src = SOURCE_DIR / ".claude" / "CLAUDE.md"
    dst = STAGING_DIR / ".claude" / "CLAUDE.md"
    if src.exists():
        if dry_run:
            result['files_copied'].append(".claude/CLAUDE.md [SANITIZED]")
            result['files_sanitized'].append(".claude/CLAUDE.md")
        else:
            content = src.read_text()
            sanitized = sanitize_claude_md(content)
            sanitized = sanitize_global(sanitized, ".claude/CLAUDE.md")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(sanitized)
            result['files_copied'].append(".claude/CLAUDE.md")
            result['files_sanitized'].append(".claude/CLAUDE.md")

    # --- 8. Generate template files ---
    print("\n--- Generating template files ---")
    for rel_path, generator_name in TEMPLATE_FILES.items():
        dst = STAGING_DIR / rel_path

        if generator_name is None:
            # Copy from source
            src = SOURCE_DIR / rel_path
            if src.exists():
                if dry_run:
                    result['files_generated'].append(rel_path)
                else:
                    copy_file(src, dst)
                    result['files_generated'].append(rel_path)
        else:
            # Generate content
            generator = globals()[generator_name]
            content = generator()
            if dry_run:
                result['files_generated'].append(f"{rel_path} [GENERATED]")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(content)
                result['files_generated'].append(rel_path)

    # --- 9. Preserved files (already in staging, not from source) ---
    print("\n--- Preserved files ---")
    for rel_path in PRESERVE_IN_STAGING:
        if (STAGING_DIR / rel_path).exists():
            result['files_copied'].append(f"{rel_path} (preserved)")
        else:
            result['skipped'].append(f"  SKIP (not in staging): {rel_path}")

    # --- 10. Copy FILE_STRUCTURE_RULES.md ---
    print("\n--- Copying docs ---")
    # Already handled in ALLOWLIST_FILES, but let's make sure
    # the docs/ directory for the public repo has ARCHITECTURE.md etc.
    staging_docs = STAGING_DIR / "docs"
    for doc_file in ['ARCHITECTURE.md', 'TOOLS_AND_SKILLS_REFERENCE.md', 'USER_MANUAL.md', 'system_health_check.md']:
        src = SOURCE_DIR / "10_Active_Projects" / "portfolio_staging" / "SecondBrain" / "docs" / doc_file
        if src.exists():
            if dry_run:
                result['files_copied'].append(f"docs/{doc_file} (from staging)")
            else:
                dst = staging_docs / doc_file
                copy_file(src, dst)
                result['files_copied'].append(f"docs/{doc_file}")
        else:
            # Try from the live docs if they exist as .claude/docs/
            alt_src = SOURCE_DIR / ".claude" / "docs" / doc_file
            if alt_src.exists():
                if dry_run:
                    result['files_copied'].append(f"docs/{doc_file} (from .claude/docs)")
                else:
                    dst = staging_docs / doc_file
                    copy_file(alt_src, dst)
                    result['files_copied'].append(f"docs/{doc_file}")

    # Copy the SDK docs
    sdk_src = SOURCE_DIR / "docs" / "anthropic_agent_sdk"
    if sdk_src.exists():
        sdk_dst = STAGING_DIR / "docs" / "agent-sdk"
        if dry_run:
            count = sum(1 for f in sdk_src.rglob('*') if f.is_file() and f.name != '_last_sync.json')
            result['files_copied'].append(f"docs/agent-sdk/ ({count} files)")
        else:
            copy_directory(sdk_src, sdk_dst)

    # --- 11. Empty placeholder directories ---
    if not dry_run:
        for placeholder_dir in [".claude/memory", ".claude/chats", ".claude/logs"]:
            d = STAGING_DIR / placeholder_dir
            d.mkdir(parents=True, exist_ok=True)
            (d / ".gitkeep").touch()

    return result


def update_sync_meta():
    """Write sync metadata."""
    meta = {
        'last_sync': datetime.now(timezone.utc).isoformat(),
        'source': str(SOURCE_DIR),
        'version': '1.0',
    }
    SYNC_META_FILE.write_text(json.dumps(meta, indent=2) + '\n')


def git_push():
    """Commit and push changes to GitHub."""
    os.chdir(STAGING_DIR)

    # Add all changes
    subprocess.run(['git', 'add', '-A'], check=True)

    # Check if there are changes to commit
    result = subprocess.run(['git', 'diff', '--cached', '--quiet'], capture_output=True)
    if result.returncode == 0:
        print("\nNo changes to commit.")
        return False

    # Commit
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    commit_msg = f"Automated sync: {timestamp}"
    subprocess.run(['git', 'commit', '-m', commit_msg], check=True)

    # Push
    subprocess.run(['git', 'push', 'origin', 'main'], check=True)
    print("\nPushed to GitHub successfully.")
    return True


def print_report(result: dict, dry_run: bool):
    """Print a summary report."""
    mode = "DRY RUN" if dry_run else "SYNC"
    print(f"\n{'=' * 60}")
    print(f"  GitHub Portfolio Sync — {mode}")
    print(f"{'=' * 60}")

    print(f"\n  Files to copy: {len(result['files_copied'])}")
    print(f"  Files generated: {len(result['files_generated'])}")
    print(f"  Files sanitized: {len(result['files_sanitized'])}")
    print(f"  Skipped: {len(result['skipped'])}")

    if result['files_copied']:
        print(f"\n--- Files {'would be ' if dry_run else ''}copied ({len(result['files_copied'])}) ---")
        for f in sorted(result['files_copied']):
            print(f"  + {f}")

    if result['files_generated']:
        print(f"\n--- Files {'would be ' if dry_run else ''}generated ({len(result['files_generated'])}) ---")
        for f in sorted(result['files_generated']):
            print(f"  * {f}")

    if result['files_sanitized']:
        print(f"\n--- Files sanitized ({len(result['files_sanitized'])}) ---")
        for f in sorted(result['files_sanitized']):
            print(f"  ~ {f}")

    if result['skipped']:
        print(f"\n--- Skipped ({len(result['skipped'])}) ---")
        for f in result['skipped']:
            print(f"  - {f}")

    if result['errors']:
        print(f"\n--- Errors ({len(result['errors'])}) ---")
        for e in result['errors']:
            print(f"  ! {e}")

    print(f"\n{'=' * 60}")

    if dry_run:
        print("  This was a dry run. Use --push to sync and push to GitHub.")
    print()


def main():
    parser = argparse.ArgumentParser(description='Sync Second Brain to GitHub portfolio')
    parser.add_argument('--push', action='store_true', help='Actually sync and push to GitHub')
    parser.add_argument('--sync-only', action='store_true', help='Sync to staging but do not push')
    parser.add_argument('--diff', action='store_true', help='Show git diff in staging area')
    args = parser.parse_args()

    if args.diff:
        os.chdir(STAGING_DIR)
        subprocess.run(['git', 'status'])
        subprocess.run(['git', 'diff', '--stat'])
        return

    dry_run = not (args.push or args.sync_only)

    print(f"Source: {SOURCE_DIR}")
    print(f"Staging: {STAGING_DIR}")
    print(f"Mode: {'DRY RUN' if dry_run else 'SYNC' + (' + PUSH' if args.push else '')}")

    result = sync(dry_run=dry_run)
    print_report(result, dry_run)

    if not dry_run:
        update_sync_meta()
        print("Sync metadata updated.")

        if args.push:
            git_push()


if __name__ == '__main__':
    main()
