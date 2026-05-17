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


def _format_messages_for_titler(messages: List[Dict[str, Any]]) -> str:
    """Format messages for the Titler prompt.

    For long conversations we keep the FIRST user message (anchors topic) plus
    the LAST 4 messages (captures any topic drift) — at FULL fidelity, no
    per-message truncation. Pick fewer messages if needed; never chop content.
    """
    if not messages:
        return ""

    # Keep first user message as an anchor, plus most recent 4 messages.
    first_user = None
    for msg in messages:
        if msg.get("role") == "user":
            first_user = msg
            break

    recent = messages[-4:]
    selected: List[Dict[str, Any]] = []
    if first_user is not None and first_user not in recent:
        selected.append(first_user)
    selected.extend(recent)

    formatted = []
    for msg in selected:
        role = msg.get("role", "user")
        raw_content = msg.get("content", "")
        if isinstance(raw_content, list):
            raw_content = " ".join(
                b.get("text", "") for b in raw_content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        content = str(raw_content)
        formatted.append(f"**{role.title()}**: {content}")

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

    # Resolve model + reasoning effort from system_models config (with haiku
    # default). Re-read every call so agent-builder edits take effect without
    # a server restart.
    model = "haiku"
    effort = ""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _server_dir = _Path(__file__).resolve().parent.parent.parent / "interface" / "server"
        if str(_server_dir) not in _sys.path:
            _sys.path.insert(0, str(_server_dir))
        import system_models as _sm
        _cfg = _sm.get("chat_titler")
        model = _cfg.get("model") or "haiku"
        effort = _cfg.get("effort") or ""
    except Exception as e:
        logger.warning(f"chat_titler: system_models load failed ({e}); using haiku default")

    logger.info(f"Running Titler on {len(messages)} messages (retitle={is_retitle}, model={model}, prompt_len={len(prompt)})")

    import sys as _sys
    from pathlib import Path as _Path
    _root_dir = _Path(__file__).resolve().parents[2]
    _server_dir = _root_dir / "interface" / "server"
    if str(_server_dir) not in _sys.path:
        _sys.path.insert(0, str(_server_dir))
    from codex_backend import CodexRunOptions, run_codex

    # One retry on SDK subprocess failure — these are usually transient
    # (exit-code-1 crashes from the CLI, typically ~1-3% of calls).
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            codex_result = await run_codex(
                CodexRunOptions(
                    model=model,
                    cwd=str(_root_dir),
                    identity_instructions=TITLER_SYSTEM_PROMPT,
                    prompt=prompt,
                    tools=[],
                    timeout_seconds=120,
                    max_turns=2,
                    effort=effort or None,
                    output_schema=TITLER_OUTPUT_SCHEMA,
                )
            )
            result = codex_result.structured_output
            if not result and codex_result.response:
                try:
                    result = json.loads(codex_result.response)
                except Exception:
                    result = None

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

                if attempt > 0:
                    logger.info(f"Titler result (after retry): '{result['title']}' (confidence={result['confidence']}, update={result.get('should_update')})")
                else:
                    logger.info(f"Titler result: '{result['title']}' (confidence={result['confidence']}, update={result.get('should_update')})")
                return result

            logger.warning(f"No structured output from Titler (attempt {attempt + 1}/2, prompt_len={len(prompt)})")
            # Empty result isn't an exception, don't retry — fall through to fallback
            return {
                "title": _fallback_title(messages),
                "confidence": 0.3,
                "should_update": True,
                "reasoning": "Fallback - no structured output"
            }

        except Exception as e:
            last_error = e
            if attempt == 0:
                logger.warning(
                    f"Titler attempt 1/2 failed: {type(e).__name__}: {e}. Retrying..."
                )
                continue
            logger.error(
                f"Titler agent failed after retry: {type(e).__name__}: {e} (prompt_len={len(prompt)})",
                exc_info=True,
            )

    # Both attempts failed
    return {
        "title": _fallback_title(messages),
        "confidence": 0.2,
        "should_update": True,
        "error": str(last_error) if last_error else "unknown",
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
