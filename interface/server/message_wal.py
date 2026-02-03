"""
Write-Ahead Log (WAL) for Message Persistence

This module provides crash-safe message storage by writing messages to disk
BEFORE processing begins. This ensures:

1. User messages are never lost, even if server crashes mid-processing
2. Partial Claude responses can be recovered after restart
3. In-flight messages can be re-sent after reconnection

WAL Structure:
- pending_messages.json: User messages awaiting completion
- streaming_responses.json: In-progress Claude responses (checkpointed periodically)
"""

import os
import json
import time
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class PendingMessage:
    """A message that has been received but not fully processed."""
    msg_id: str
    session_id: str  # Chat session ID (may be 'new' initially)
    content: str
    timestamp: float  # When received (seconds since epoch)
    status: str = 'received'  # received, processing, complete, failed
    ack_sent: bool = False  # Whether we ACK'd receipt to client
    chat_id: Optional[str] = None  # Resolved chat ID after processing starts
    error: Optional[str] = None  # Error message if failed


@dataclass
class StreamingResponse:
    """A Claude response that is being streamed."""
    session_id: str
    chat_id: str
    msg_id: str  # ID of the user message that triggered this
    content_segments: List[str] = field(default_factory=list)  # Accumulated text segments
    last_checkpoint: float = 0  # Last time we saved to disk
    started_at: float = 0
    tool_in_progress: Optional[str] = None  # Name of tool currently running


class MessageWAL:
    """
    Write-Ahead Log for message persistence.

    Ensures messages are durably stored before processing begins,
    and partial responses are checkpointed during streaming.
    """

    CHECKPOINT_INTERVAL = 5.0  # Seconds between response checkpoints

    def __init__(self, wal_dir: str):
        self.wal_dir = Path(wal_dir)
        self.wal_dir.mkdir(parents=True, exist_ok=True)

        self.pending_file = self.wal_dir / "pending_messages.json"
        self.streaming_file = self.wal_dir / "streaming_responses.json"

        self._lock = Lock()

        # In-memory state (synced to disk)
        self._pending: Dict[str, PendingMessage] = {}
        self._streaming: Dict[str, StreamingResponse] = {}

        # Load existing state on init
        self._load_state()

    def _load_state(self):
        """Load WAL state from disk."""
        try:
            if self.pending_file.exists():
                with open(self.pending_file, 'r') as f:
                    data = json.load(f)
                    for msg_id, msg_data in data.items():
                        self._pending[msg_id] = PendingMessage(**msg_data)
                logger.info(f"WAL: Loaded {len(self._pending)} pending messages from disk")
        except Exception as e:
            logger.error(f"WAL: Failed to load pending messages: {e}")

        try:
            if self.streaming_file.exists():
                with open(self.streaming_file, 'r') as f:
                    data = json.load(f)
                    for session_id, resp_data in data.items():
                        self._streaming[session_id] = StreamingResponse(**resp_data)
                logger.info(f"WAL: Loaded {len(self._streaming)} streaming responses from disk")
        except Exception as e:
            logger.error(f"WAL: Failed to load streaming responses: {e}")

    def _save_pending(self):
        """Persist pending messages to disk."""
        try:
            with open(self.pending_file, 'w') as f:
                data = {msg_id: asdict(msg) for msg_id, msg in self._pending.items()}
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"WAL: Failed to save pending messages: {e}")

    def _save_streaming(self):
        """Persist streaming responses to disk."""
        try:
            with open(self.streaming_file, 'w') as f:
                data = {session_id: asdict(resp) for session_id, resp in self._streaming.items()}
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"WAL: Failed to save streaming responses: {e}")

    # --- Pending Message Operations ---

    def write_message(self, msg_id: str, session_id: str, content: str) -> PendingMessage:
        """
        Write a user message to the WAL BEFORE processing.
        This is the critical write-ahead operation.
        """
        with self._lock:
            msg = PendingMessage(
                msg_id=msg_id,
                session_id=session_id,
                content=content,
                timestamp=time.time(),
                status='received'
            )
            self._pending[msg_id] = msg
            self._save_pending()
            logger.info(f"WAL: Written message {msg_id} to WAL")
            return msg

    def ack_message(self, msg_id: str):
        """Mark that we've ACK'd receipt to the client."""
        with self._lock:
            if msg_id in self._pending:
                self._pending[msg_id].ack_sent = True
                self._save_pending()

    def start_processing(self, msg_id: str, chat_id: str):
        """Mark that we've started processing this message."""
        with self._lock:
            if msg_id in self._pending:
                self._pending[msg_id].status = 'processing'
                self._pending[msg_id].chat_id = chat_id
                self._save_pending()
                logger.info(f"WAL: Message {msg_id} now processing in chat {chat_id}")

    def complete_message(self, msg_id: str):
        """Mark message as complete and remove from WAL."""
        with self._lock:
            if msg_id in self._pending:
                del self._pending[msg_id]
                self._save_pending()
                logger.info(f"WAL: Message {msg_id} completed, removed from WAL")

    def fail_message(self, msg_id: str, error: str):
        """Mark message as failed."""
        with self._lock:
            if msg_id in self._pending:
                self._pending[msg_id].status = 'failed'
                self._pending[msg_id].error = error
                self._save_pending()
                logger.warning(f"WAL: Message {msg_id} failed: {error}")

    def get_pending_messages(self) -> List[PendingMessage]:
        """Get all pending messages (for recovery on startup)."""
        with self._lock:
            return list(self._pending.values())

    def get_pending_for_session(self, session_id: str) -> Optional[PendingMessage]:
        """Get pending message for a specific session."""
        with self._lock:
            for msg in self._pending.values():
                if msg.session_id == session_id or msg.chat_id == session_id:
                    return msg
            return None

    # --- Streaming Response Operations ---

    def start_streaming(self, session_id: str, chat_id: str, msg_id: str):
        """Start tracking a streaming response."""
        with self._lock:
            self._streaming[session_id] = StreamingResponse(
                session_id=session_id,
                chat_id=chat_id,
                msg_id=msg_id,
                content_segments=[],
                last_checkpoint=time.time(),
                started_at=time.time()
            )
            self._save_streaming()
            logger.info(f"WAL: Started streaming for session {session_id}")

    def append_content(self, session_id: str, text: str, force_checkpoint: bool = False):
        """
        Append content to a streaming response.
        Checkpoints to disk periodically for crash recovery.
        """
        with self._lock:
            if session_id not in self._streaming:
                return

            resp = self._streaming[session_id]

            # Append to current segment or start new one
            if resp.content_segments:
                resp.content_segments[-1] += text
            else:
                resp.content_segments.append(text)

            # Checkpoint if enough time has passed
            now = time.time()
            if force_checkpoint or (now - resp.last_checkpoint >= self.CHECKPOINT_INTERVAL):
                resp.last_checkpoint = now
                self._save_streaming()
                logger.debug(f"WAL: Checkpointed streaming response for {session_id}")

    def new_segment(self, session_id: str):
        """Start a new content segment (e.g., after tool use)."""
        with self._lock:
            if session_id in self._streaming:
                self._streaming[session_id].content_segments.append("")

    def set_tool_in_progress(self, session_id: str, tool_name: Optional[str]):
        """Track which tool is currently running."""
        with self._lock:
            if session_id in self._streaming:
                self._streaming[session_id].tool_in_progress = tool_name
                self._save_streaming()

    def complete_streaming(self, session_id: str) -> Optional[StreamingResponse]:
        """Complete streaming and remove from WAL."""
        with self._lock:
            if session_id in self._streaming:
                resp = self._streaming.pop(session_id)
                self._save_streaming()
                logger.info(f"WAL: Completed streaming for session {session_id}")
                return resp
            return None

    def get_streaming(self, session_id: str) -> Optional[StreamingResponse]:
        """Get streaming response for a session."""
        with self._lock:
            return self._streaming.get(session_id)

    def get_all_streaming(self) -> Dict[str, StreamingResponse]:
        """Get all in-progress streaming responses (for recovery)."""
        with self._lock:
            return dict(self._streaming)

    # --- Recovery Operations ---

    def get_recovery_state(self) -> Dict[str, Any]:
        """
        Get full WAL state for recovery purposes.
        Called on server startup to identify unfinished work.
        """
        with self._lock:
            return {
                "pending_messages": [asdict(m) for m in self._pending.values()],
                "streaming_responses": [asdict(r) for r in self._streaming.values()],
                "has_recovery_work": len(self._pending) > 0 or len(self._streaming) > 0
            }

    def clear_old_entries(self, max_age_hours: float = 24):
        """Remove WAL entries older than max_age_hours."""
        cutoff = time.time() - (max_age_hours * 3600)

        with self._lock:
            # Clean pending messages
            old_pending = [
                msg_id for msg_id, msg in self._pending.items()
                if msg.timestamp < cutoff
            ]
            for msg_id in old_pending:
                del self._pending[msg_id]
                logger.info(f"WAL: Cleaned up old pending message {msg_id}")

            # Clean streaming responses
            old_streaming = [
                session_id for session_id, resp in self._streaming.items()
                if resp.started_at < cutoff
            ]
            for session_id in old_streaming:
                del self._streaming[session_id]
                logger.info(f"WAL: Cleaned up old streaming response {session_id}")

            if old_pending or old_streaming:
                self._save_pending()
                self._save_streaming()

    def clear_stale_on_restart(self):
        """
        Clear ALL 'processing' and 'received' status entries on server restart.

        This is critical for FIX BUG 3: When the server restarts, any entries marked
        as 'processing' or 'received' are stale - no actual processing is happening.
        The client needs to know the true state, which is that nothing is in progress.

        We keep 'failed' entries for debugging purposes, but clear everything else.
        """
        with self._lock:
            # Clear all pending messages that aren't in 'failed' state
            stale_pending = [
                msg_id for msg_id, msg in self._pending.items()
                if msg.status in ('received', 'processing')
            ]
            for msg_id in stale_pending:
                logger.info(f"WAL: Clearing stale pending message {msg_id} (status: {self._pending[msg_id].status})")
                del self._pending[msg_id]

            # Clear all streaming responses - they're all stale after restart
            stale_streaming = list(self._streaming.keys())
            for session_id in stale_streaming:
                logger.info(f"WAL: Clearing stale streaming response {session_id}")
                del self._streaming[session_id]

            if stale_pending or stale_streaming:
                self._save_pending()
                self._save_streaming()
                logger.info(f"WAL: Cleared {len(stale_pending)} pending messages and {len(stale_streaming)} streaming responses")


# Global WAL instance (initialized by server)
_wal_instance: Optional[MessageWAL] = None


def get_wal() -> MessageWAL:
    """Get the global WAL instance."""
    global _wal_instance
    if _wal_instance is None:
        raise RuntimeError("WAL not initialized. Call init_wal() first.")
    return _wal_instance


def init_wal(wal_dir: str) -> MessageWAL:
    """Initialize the global WAL instance."""
    global _wal_instance
    _wal_instance = MessageWAL(wal_dir)
    return _wal_instance
