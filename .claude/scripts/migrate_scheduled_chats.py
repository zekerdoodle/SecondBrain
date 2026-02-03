#!/usr/bin/env python3
"""
One-time migration: Add `scheduled: true` flag to existing scheduled chats.

Identifies scheduled chats by:
1. Title starting with '🕐 ' (old format)
2. Messages containing '[SCHEDULED AUTOMATION]'

Also strips the leading '🕐 ' from titles (emoji is now added at display time).
"""

import json
import os
import sys

def migrate_chats(chats_dir: str = None, dry_run: bool = False):
    if chats_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        chats_dir = os.path.join(script_dir, "..", "chats")

    chats_dir = os.path.abspath(chats_dir)

    if not os.path.exists(chats_dir):
        print(f"Chats directory not found: {chats_dir}")
        return

    stats = {"migrated": 0, "skipped": 0, "errors": []}

    chat_files = [f for f in os.listdir(chats_dir) if f.endswith('.json')]
    print(f"Scanning {len(chat_files)} chats...")

    for filename in chat_files:
        filepath = os.path.join(chats_dir, filename)

        try:
            with open(filepath, 'r') as f:
                data = json.load(f)

            # Already has scheduled flag
            if data.get("scheduled"):
                stats["skipped"] += 1
                continue

            title = data.get("title", "")
            messages = data.get("messages", [])

            # Check if this is a scheduled chat
            is_scheduled = False

            # Method 1: Title starts with clock emoji
            if title.startswith("🕐 "):
                is_scheduled = True

            # Method 2: Messages contain scheduled marker
            for msg in messages:
                content = msg.get("content", "")
                if "[SCHEDULED AUTOMATION]" in content or "👇 [SCHEDULED AUTOMATION] 👇" in content:
                    is_scheduled = True
                    break

            if not is_scheduled:
                stats["skipped"] += 1
                continue

            # Migrate this chat
            data["scheduled"] = True

            # Strip emoji prefix from title (now added at display time)
            if title.startswith("🕐 "):
                data["title"] = title[2:]  # Remove "🕐 " (emoji + space)

            if dry_run:
                print(f"[DRY RUN] Would migrate: {filename} - '{title}'")
            else:
                with open(filepath, 'w') as f:
                    json.dump(data, f, indent=2)
                print(f"Migrated: {filename} - '{title}' -> '{data['title']}'")

            stats["migrated"] += 1

        except Exception as e:
            print(f"Error processing {filename}: {e}")
            stats["errors"].append({"file": filename, "error": str(e)})

    print(f"\nDone: {stats['migrated']} migrated, {stats['skipped']} skipped, {len(stats['errors'])} errors")
    return stats


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("DRY RUN MODE - no changes will be made\n")
    migrate_chats(dry_run=dry_run)
