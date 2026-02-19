#!/usr/bin/env python3
"""
Calculate the base token overhead for the context window indicator.

This script measures the approximate token count of:
- CLAUDE.md (system instructions)
- Skills (from .claude/skill_defs/)
- Memory budget (assumed 4k)
- MCP tool definitions (estimated)

The result is stored as JSON for the UI to consume.
"""

import os
import json
from pathlib import Path

# Constants
CHARS_PER_TOKEN = 4  # Conservative estimate (~4 chars = 1 token)
MEMORY_BUDGET = 4000  # Assumed memory injection budget
MCP_TOOLS_OVERHEAD = 5000  # Estimated overhead for MCP tool schemas

# Paths
SECOND_BRAIN = Path(__file__).parent.parent.parent
CLAUDE_DIR = SECOND_BRAIN / ".claude"
SKILLS_DIR = CLAUDE_DIR / "skills"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"
OUTPUT_FILE = CLAUDE_DIR / "context_overhead.json"


def count_tokens(text: str) -> int:
    """Estimate token count from text."""
    return len(text) // CHARS_PER_TOKEN


def calculate_overhead() -> dict:
    """Calculate total base overhead."""
    breakdown = {}

    # CLAUDE.md
    if CLAUDE_MD.exists():
        content = CLAUDE_MD.read_text()
        breakdown["claude_md"] = {
            "chars": len(content),
            "tokens": count_tokens(content)
        }
    else:
        breakdown["claude_md"] = {"chars": 0, "tokens": 0}

    # Skills
    skills_chars = 0
    skills_count = 0
    if SKILLS_DIR.exists():
        for skill_file in SKILLS_DIR.glob("*.md"):
            content = skill_file.read_text()
            skills_chars += len(content)
            skills_count += 1

    breakdown["skills"] = {
        "count": skills_count,
        "chars": skills_chars,
        "tokens": count_tokens("x" * skills_chars)  # Token count from skill content
    }

    # Memory budget (assumed constant)
    breakdown["memory_budget"] = {
        "tokens": MEMORY_BUDGET,
        "note": "Assumed 4k budget for semantic memory injection"
    }

    # MCP tools (estimated)
    breakdown["mcp_tools"] = {
        "tokens": MCP_TOOLS_OVERHEAD,
        "note": "Estimated overhead for MCP tool schemas"
    }

    # Total
    # Note: Skills are loaded on-demand, so we include a reduced portion
    # Assuming on average 1-2 skills are active per conversation
    avg_active_skills_ratio = 0.3
    effective_skills_tokens = int(count_tokens(str(skills_chars)) * avg_active_skills_ratio)

    total = (
        breakdown["claude_md"]["tokens"] +
        effective_skills_tokens +
        breakdown["memory_budget"]["tokens"] +
        breakdown["mcp_tools"]["tokens"]
    )

    return {
        "total_tokens": total,
        "percentage_of_200k": round((total / 200000) * 100, 1),
        "breakdown": breakdown,
        "notes": [
            "Skills overhead is estimated at 30% of total (assuming 1-2 active skills)",
            "Memory budget is assumed constant at 4k tokens",
            "MCP tools overhead is estimated",
            "Actual overhead may vary based on active skills and memory content"
        ]
    }


def main():
    """Calculate and save overhead."""
    result = calculate_overhead()

    # Save to file
    OUTPUT_FILE.write_text(json.dumps(result, indent=2))

    print(f"Base overhead calculated: {result['total_tokens']:,} tokens ({result['percentage_of_200k']}% of 200k)")
    print(f"Saved to: {OUTPUT_FILE}")
    print(f"\nBreakdown:")
    for key, value in result["breakdown"].items():
        if isinstance(value, dict) and "tokens" in value:
            print(f"  {key}: {value['tokens']:,} tokens")


if __name__ == "__main__":
    main()
