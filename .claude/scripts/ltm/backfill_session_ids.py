"""
Backfill source_session_id on existing atoms.

Strategy:
1. Build a timeline of chat sessions with their activity windows
2. For each atom without a source_session_id, find the chat session
   that was active when the atom was created
3. Match by timestamp proximity: the atom's created_at should fall
   within (or very close to) a chat's activity window
4. As a tiebreaker when multiple sessions overlap, scan message content
   for keywords from the atom

IMPORTANT: Run this with the server STOPPED to avoid singleton conflicts.
"""

import json
import os
import glob
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("backfill")

_CLAUDE_DIR = Path(__file__).parent.parent.parent  # .claude/
CHATS_DIR = _CLAUDE_DIR / "chats"
ATOMIC_FILE = _CLAUDE_DIR / "memory" / "atomic_memories.json"


def _extract_chat_timestamps(chat_data: dict) -> tuple:
    """Extract (earliest, latest) unix timestamps from a chat's messages."""
    messages = chat_data.get("messages", [])
    timestamps = []

    for m in messages:
        mid = m.get("id", "")
        # Timestamp-based message IDs (unix ms)
        if isinstance(mid, str) and mid.isdigit() and len(mid) >= 13:
            timestamps.append(int(mid) / 1000.0)

    # last_message_at is the most reliable latest timestamp
    lma = chat_data.get("last_message_at")
    if lma:
        timestamps.append(float(lma))

    if not timestamps:
        return None, None

    return min(timestamps), max(timestamps)


def _iso_to_unix(iso_str: str) -> float:
    """Convert ISO timestamp to unix timestamp."""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.timestamp()
    except Exception:
        return 0.0


def _extract_keywords(text: str, n: int = 5) -> set:
    """Extract significant keywords from text (lowercase, 4+ chars)."""
    words = set()
    for word in text.lower().split():
        # Strip common punctuation
        word = word.strip(".,!?;:'\"()[]{}").strip()
        if len(word) >= 4 and word.isalpha():
            words.add(word)
    # Return the longest words (most distinctive)
    sorted_words = sorted(words, key=len, reverse=True)
    return set(sorted_words[:n])


def build_chat_timeline():
    """Build a timeline of all chat sessions."""
    timeline = []

    if not CHATS_DIR.exists():
        logger.error(f"Chats directory not found: {CHATS_DIR}")
        return timeline

    chat_files = glob.glob(str(CHATS_DIR / "*.json"))
    logger.info(f"Scanning {len(chat_files)} chat files...")

    for f in chat_files:
        try:
            with open(f) as fh:
                data = json.load(fh)

            chat_id = os.path.splitext(os.path.basename(f))[0]
            earliest, latest = _extract_chat_timestamps(data)

            if earliest is None:
                continue

            # Collect all message content for keyword matching
            all_content = []
            for m in data.get("messages", []):
                content = m.get("content", "")
                if content and m.get("role") in ("user", "assistant"):
                    all_content.append(content[:500])  # Cap per message

            timeline.append({
                "chat_id": chat_id,
                "earliest": earliest,
                "latest": latest,
                "content_sample": " ".join(all_content)[:5000],
                "msg_count": len(data.get("messages", []))
            })
        except Exception as e:
            logger.debug(f"Skipping {f}: {e}")

    logger.info(f"Built timeline with {len(timeline)} chat sessions")
    return timeline


def find_matching_session(atom, timeline, grace_seconds=1800):
    """
    Find the chat session that most likely generated an atom.

    Args:
        atom: Atom dict with created_at
        timeline: List of chat session dicts
        grace_seconds: How much before/after the chat window to still match (default 30min)
            The Librarian runs on a 20-minute throttle, so atoms may be created
            up to ~20 minutes after the exchange happened.

    Returns:
        chat_id string or None
    """
    atom_ts = _iso_to_unix(atom.get("created_at", ""))
    if not atom_ts:
        return None

    candidates = []
    for session in timeline:
        # Check if atom creation time falls within (or near) the chat's activity window
        # Grace period accounts for Librarian processing delay
        if (session["earliest"] - grace_seconds) <= atom_ts <= (session["latest"] + grace_seconds):
            candidates.append(session)

    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]["chat_id"]

    # Multiple candidates — use keyword matching as tiebreaker
    atom_keywords = _extract_keywords(atom.get("content", ""))
    if not atom_keywords:
        # Can't disambiguate, pick the session closest in time
        candidates.sort(key=lambda s: abs(s["latest"] - atom_ts))
        return candidates[0]["chat_id"]

    best_match = None
    best_score = -1

    for session in candidates:
        session_text = session["content_sample"].lower()
        matches = sum(1 for kw in atom_keywords if kw in session_text)
        if matches > best_score:
            best_score = matches
            best_match = session["chat_id"]

    return best_match


def run_backfill(dry_run=True):
    """
    Run the backfill to populate source_session_id on existing atoms.

    Args:
        dry_run: If True, don't write changes, just report what would happen
    """
    # Load atoms
    if not ATOMIC_FILE.exists():
        logger.error(f"Atomic memories file not found: {ATOMIC_FILE}")
        return

    with open(ATOMIC_FILE) as f:
        data = json.load(f)

    atoms = data.get("memories", [])
    total = len(atoms)
    needs_backfill = [a for a in atoms if not a.get("source_session_id")]

    logger.info(f"Total atoms: {total}")
    logger.info(f"Atoms needing backfill: {len(needs_backfill)}")
    logger.info(f"Atoms already tagged: {total - len(needs_backfill)}")

    if not needs_backfill:
        logger.info("Nothing to backfill!")
        return

    # Build chat timeline
    timeline = build_chat_timeline()
    if not timeline:
        logger.error("No chat sessions found, cannot backfill")
        return

    # Match atoms to sessions
    matched = 0
    unmatched = 0

    for atom in needs_backfill:
        session_id = find_matching_session(atom, timeline)
        if session_id:
            atom["source_session_id"] = session_id
            matched += 1
        else:
            unmatched += 1

    logger.info(f"\nResults:")
    logger.info(f"  Matched: {matched}")
    logger.info(f"  Unmatched: {unmatched}")

    if dry_run:
        logger.info("\nDRY RUN — no changes written. Run with --apply to write.")
        # Show some examples
        examples = [a for a in needs_backfill if a.get("source_session_id")][:5]
        for a in examples:
            logger.info(f"  {a['id']}: {a['content'][:60]}... -> {a['source_session_id']}")
    else:
        # Write back
        with open(ATOMIC_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"\nWrote {matched} session IDs to {ATOMIC_FILE}")


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    run_backfill(dry_run=dry_run)
