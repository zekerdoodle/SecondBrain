"""Formatting helpers for Working Memory prompt injection."""

from __future__ import annotations

from typing import Sequence
from datetime import datetime, timezone
import re

from .models import WorkingMemoryItem

_HEADER = (
    "Your active scratchpad. These are notes you have set for yourself during\n"
    "recent conversations. They auto-expire after a set number of exchanges\n"
    "unless pinned. Use them to track tasks, reminders, and context you need\n"
    "to carry across turns. Review and prune regularly."
)
_MAX_PREVIEW_CHARS = 160


def format_time_until(seconds: float) -> str:
    """Format seconds until deadline as human-readable countdown."""
    is_overdue = seconds < 0
    abs_seconds = abs(seconds)

    minutes = abs_seconds / 60
    hours = abs_seconds / 3600
    days = abs_seconds / 86400

    prefix = "T+" if is_overdue else "T-"

    if abs_seconds < 60:
        return f"{prefix}<1m"
    elif minutes < 60:
        return f"{prefix}{int(minutes)}m"
    elif hours < 24:
        return f"{prefix}{int(hours)}h"
    else:
        return f"{prefix}{int(days)}d"


def parse_duration(duration_str: str) -> int:
    """Parse duration string like '2h', '30m', '1d' to seconds."""
    if not duration_str:
        return 0

    duration_str = duration_str.strip().lower()
    match = re.match(r'^(\d+)\s*(m|h|d)$', duration_str)
    if not match:
        return 0

    value, unit = match.groups()
    value = int(value)

    if unit == 'm':
        return value * 60
    elif unit == 'h':
        return value * 3600
    elif unit == 'd':
        return value * 86400

    return 0


def is_due_soon(deadline_dt: datetime, remind_before: str = None) -> bool:
    """Check if deadline is approaching (within remind_before window)."""
    if not remind_before:
        return False

    remind_seconds = parse_duration(remind_before)
    if not remind_seconds:
        return False

    now = datetime.now(timezone.utc)
    seconds_until = (deadline_dt - now).total_seconds()

    return 0 <= seconds_until <= remind_seconds


def _preview(first_line: str) -> str:
    """Truncate long content for preview."""
    text = first_line.strip()
    if len(text) <= _MAX_PREVIEW_CHARS:
        return text
    return text[: _MAX_PREVIEW_CHARS - 1].rstrip() + "..."


def _format_item_header(item: WorkingMemoryItem, index: int) -> str:
    """Format the header line for a working memory item."""
    parts = []

    # Index
    parts.append(f"{index}.")

    # Pinned indicator
    if item.pinned:
        parts.append("[PINNED]")
        if item.pin_rank > 1:
            parts.append(f"[rank:{item.pin_rank}]")

    # Tag
    tag = item.tag or "note"
    parts.append(f"[{tag}]")

    # Deadline or TTL info
    if item.deadline_at:
        time_until = item.time_until_deadline
        if time_until is not None:
            countdown = format_time_until(time_until)

            if item.is_overdue:
                parts.append(f"OVERDUE {countdown}")
            elif is_due_soon(item.deadline_at, item.remind_before):
                parts.append(f"DUE SOON {countdown}")
            else:
                parts.append(countdown)

            if item.deadline_type == "hard":
                parts.append("[hard]")
    else:
        # Show TTL for non-deadline items
        parts.append(f"(age {item.age_exchanges}/{item.ttl_initial})")

    # Content preview
    content_parts = item.content.splitlines() or [""]
    preview = _preview(content_parts[0])
    parts.append(preview)

    return " ".join(parts).rstrip()


def format_working_memory_section(items: Sequence[WorkingMemoryItem]) -> str:
    """Return formatted Working Memory block for prompt injection."""
    if not items:
        return ""

    lines = [_HEADER]

    for index, item in enumerate(items, start=1):
        header = _format_item_header(item, index)
        lines.append(header)

        # Add continuation lines for multiline content
        parts = item.content.splitlines()
        if len(parts) > 1:
            for extra in parts[1:]:
                lines.append(f"   {extra.rstrip()}")

    return "\n".join(lines)
