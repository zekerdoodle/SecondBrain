"""
Chat Titler Agent

Generates intelligent chat titles from conversation context using Claude Haiku.
Uses OAuth authentication (same as main Claude Code agent).

Triggers:
1. First message: Generate initial title immediately
2. Every N exchanges: Re-evaluate and potentially update title

The Titler:
1. Analyzes conversation content and themes
2. Generates concise, descriptive titles
3. Only updates title if conversation has significantly evolved
"""

import json
import logging
import asyncio
import os
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger("chat_titler")

# Configuration
RETITLE_INTERVAL = 5  # Re-evaluate title every N exchanges
MIN_MESSAGES_FOR_RETITLE = 3  # Need at least this many messages to consider retitling

# System prompt for the Titler
TITLER_SYSTEM_PROMPT = """You are a chat title generator. Your job is to create concise, descriptive titles for conversations.

## GUIDELINES

1. **Be Concise**: Titles should be 3-8 words maximum
2. **Be Descriptive**: Capture the main topic or purpose of the conversation
3. **Be Specific**: Prefer "Debugging Python async bug" over "Code help"
4. **Skip Greetings**: Ignore "hi", "hello", focus on substantive content
5. **Use Present Tense**: "Building authentication system" not "Built auth system"
6. **No Emojis**: Keep titles clean and professional

## TITLE PATTERNS

Good titles:
- "Debugging WebSocket reconnection"
- "Setting up Kubernetes cluster"
- "Reviewing PR for auth module"
- "Planning Q2 roadmap"
- "Diet tracking spreadsheet setup"

Bad titles:
- "Help with code" (too vague)
- "Question about something" (meaningless)
- "Hi Claude" (just a greeting)
- "This is a conversation about building a system for..." (too long)

## FOR TITLE UPDATES

When asked to update an existing title:
- Only suggest a change if the conversation has CLEARLY shifted topics
- If it's just a continuation of the same theme, keep the original
- A longer conversation doesn't need a new title unless the topic changed
"""

# JSON Schema for structured output
TITLER_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "The generated title (3-8 words)"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0 that this title captures the conversation",
            "minimum": 0.0,
            "maximum": 1.0
        },
        "should_update": {
            "type": "boolean",
            "description": "For retitling: true if title should change, false to keep existing"
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of the title choice"
        }
    },
    "required": ["title", "confidence"]
}


def _format_messages_for_titler(messages: List[Dict[str, Any]], max_chars: int = 2000) -> str:
    """Format messages for the Titler prompt, truncating if needed."""
    formatted = []
    total_chars = 0

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")[:500]  # Truncate individual messages

        if total_chars + len(content) > max_chars:
            break

        formatted.append(f"**{role.title()}**: {content}")
        total_chars += len(content)

    return "\n\n".join(formatted)


async def generate_title(
    messages: List[Dict[str, Any]],
    current_title: Optional[str] = None,
    is_retitle: bool = False
) -> Dict[str, Any]:
    """
    Generate a title for a chat conversation.

    Args:
        messages: List of message dicts with role and content
        current_title: Existing title (for retitling)
        is_retitle: If True, this is a title update check

    Returns:
        Dict with title, confidence, should_update, reasoning
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    if not messages:
        return {
            "title": "New Chat",
            "confidence": 1.0,
            "should_update": False,
            "reasoning": "No messages to analyze"
        }

    # Build the prompt
    if is_retitle and current_title:
        prompt = f"""## Current Title
"{current_title}"

## Conversation ({len(messages)} messages)

{_format_messages_for_titler(messages)}

---

Should this title be updated? The conversation may have evolved since the title was set.
Only suggest a new title if the topic has significantly changed."""
    else:
        prompt = f"""## Conversation ({len(messages)} messages)

{_format_messages_for_titler(messages)}

---

Generate a concise, descriptive title for this conversation."""

    logger.info(f"Running Titler on {len(messages)} messages (retitle={is_retitle})")

    try:
        result = None
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="haiku",  # Fast and cost-effective for simple tasks
                system_prompt=TITLER_SYSTEM_PROMPT,
                max_turns=2,  # Need 2 for structured output
                permission_mode="bypassPermissions",
                allowed_tools=[],
                output_format={
                    "type": "json_schema",
                    "schema": TITLER_OUTPUT_SCHEMA
                }
            )
        ):
            if isinstance(message, ResultMessage) and message.structured_output:
                result = message.structured_output

        if result:
            # Ensure all fields are present
            result.setdefault("title", "Untitled Chat")
            result.setdefault("confidence", 0.5)
            result.setdefault("should_update", not is_retitle)  # Default: update for new, keep for retitle
            result.setdefault("reasoning", "")

            # Sanitize title
            result["title"] = result["title"].strip()[:60]
            if not result["title"]:
                result["title"] = "Untitled Chat"

            logger.info(f"Titler result: '{result['title']}' (confidence={result['confidence']}, update={result.get('should_update')})")
            return result

        logger.warning("No structured output from Titler")
        return {
            "title": _fallback_title(messages),
            "confidence": 0.3,
            "should_update": True,
            "reasoning": "Fallback - no structured output"
        }

    except Exception as e:
        logger.error(f"Titler agent failed: {e}")
        return {
            "title": _fallback_title(messages),
            "confidence": 0.2,
            "should_update": True,
            "error": str(e)
        }


def _fallback_title(messages: List[Dict[str, Any]]) -> str:
    """Generate a fallback title using simple truncation (existing behavior)."""
    if not messages:
        return "New Chat"

    # Find first user message
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "").strip()
            content = content.replace('\n', ' ')
            content = ' '.join(content.split())

            for prefix in ['[CONTEXT:', '[SCHEDULED', '🕐 ', 'Hey ', 'Hi ', 'Hello ']:
                if content.startswith(prefix):
                    content = content[len(prefix):].strip()

            if len(content) > 50:
                content = content[:47] + "..."

            return content or "New Chat"

    return "New Chat"


def should_retitle(exchange_count: int, current_title: Optional[str] = None) -> bool:
    """Determine if we should attempt to retitle based on exchange count."""
    if not current_title:
        return True  # Always title new chats

    if exchange_count < MIN_MESSAGES_FOR_RETITLE:
        return False

    # Retitle every N exchanges
    return exchange_count % RETITLE_INTERVAL == 0


async def backfill_all_chats(chats_dir: str = None, batch_size: int = 5, delay_between_batches: float = 2.0):
    """
    Retitle all existing chats using the Titler agent.

    - Loads each chat JSON from .claude/chats/
    - Skips system chats (is_system=true) - keeps emoji prefix
    - Runs Titler on messages
    - Updates title in place
    - Rate limited: processes batch_size chats, then pauses
    - Prints progress to stdout

    Args:
        chats_dir: Path to chats directory (defaults to .claude/chats/)
        batch_size: Number of chats to process before pausing
        delay_between_batches: Seconds to wait between batches

    Returns:
        Dict with stats: updated, skipped, errors
    """
    if chats_dir is None:
        # Find the chats directory relative to this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        chats_dir = os.path.join(script_dir, "..", "chats")

    chats_dir = os.path.abspath(chats_dir)

    if not os.path.exists(chats_dir):
        print(f"Chats directory not found: {chats_dir}")
        return {"updated": 0, "skipped": 0, "errors": []}

    stats = {
        "updated": 0,
        "skipped": 0,
        "errors": []
    }

    # Get all chat files
    chat_files = [f for f in os.listdir(chats_dir) if f.endswith('.json')]
    total = len(chat_files)

    print(f"Found {total} chats to process")

    for i, filename in enumerate(chat_files):
        filepath = os.path.join(chats_dir, filename)

        try:
            with open(filepath, 'r') as f:
                chat_data = json.load(f)

            # Skip scheduled chats (both silent and non-silent) - they have special titles
            if chat_data.get("scheduled", False) or chat_data.get("is_system", False):
                print(f"[{i+1}/{total}] Skipping scheduled/system chat: {filename}")
                stats["skipped"] += 1
                continue

            messages = chat_data.get("messages", [])
            current_title = chat_data.get("title", "")

            # Skip empty chats
            if not messages:
                print(f"[{i+1}/{total}] Skipping empty chat: {filename}")
                stats["skipped"] += 1
                continue

            # Generate new title
            result = await generate_title(messages, current_title=None, is_retitle=False)
            new_title = result.get("title", current_title)

            # Update if different
            if new_title and new_title != current_title:
                chat_data["title"] = new_title
                with open(filepath, 'w') as f:
                    json.dump(chat_data, f, indent=2)
                print(f"[{i+1}/{total}] Updated: '{current_title}' -> '{new_title}'")
                stats["updated"] += 1
            else:
                print(f"[{i+1}/{total}] Kept: '{current_title}'")
                stats["skipped"] += 1

            # Rate limiting
            if (i + 1) % batch_size == 0 and i + 1 < total:
                print(f"Processed {i+1}/{total}, pausing for {delay_between_batches}s...")
                await asyncio.sleep(delay_between_batches)

        except Exception as e:
            print(f"[{i+1}/{total}] Error processing {filename}: {e}")
            stats["errors"].append({"file": filename, "error": str(e)})

    print(f"\nBackfill complete: {stats['updated']} updated, {stats['skipped']} skipped, {len(stats['errors'])} errors")
    return stats


# Synchronous wrapper for non-async contexts
def generate_title_sync(messages: List[Dict[str, Any]], current_title: Optional[str] = None) -> Dict[str, Any]:
    """Synchronous wrapper for generate_title."""
    return asyncio.run(generate_title(messages, current_title, is_retitle=bool(current_title)))


if __name__ == "__main__":
    # Run backfill when executed directly
    print("Running chat title backfill...")
    asyncio.run(backfill_all_chats())
