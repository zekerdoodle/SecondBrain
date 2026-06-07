"""
Second Brain Interface Server

FastAPI server providing:
- WebSocket chat interface to the assistant
- File management API
- Chat history management
- Scheduled task execution
"""

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, UploadFile, File as FastAPIFile, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
import os
import logging
import json
import asyncio
import re
import sys
import uuid
import signal
import time
import base64
import hashlib
import html
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set, Tuple
from contextlib import asynccontextmanager
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_startup_env_file(env_path):
    """Load repo-root .env into the backend process; .env wins over shell env."""
    loaded_keys = []
    if not env_path.is_file():
        return loaded_keys, None
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                os.environ[key] = value
                loaded_keys.append(key)
    except Exception as exc:
        return loaded_keys, exc
    return loaded_keys, None


STARTUP_ENV_FILE = REPO_ROOT / ".env"
STARTUP_ENV_KEYS, STARTUP_ENV_ERROR = _load_startup_env_file(STARTUP_ENV_FILE)

from claude_wrapper import ClaudeWrapper, ChatManager, ConversationState
from notifications import should_notify, send_notification, NotificationDecision
from message_wal import init_wal, get_wal, MessageWAL
from tool_serializers import serialize_tool_call, format_tool_for_history
from tool_output_artifacts import (
    DEFAULT_DISPLAY_LIMIT_CHARS,
    compact_tool_output_for_display,
    maybe_write_raw_tool_output_artifact,
    with_truncation_flags,
)
from process_registry import register_process, deregister_by_pid, clear_registry
import running_agents
from mention_parser import parse_mentions
from anthropic_cache_proxy import router as anthropic_cache_proxy_router
from slash_commands import (
    SLASH_COMMANDS,
    dispatch_slash_command,
    get_command_menu,
    parse_slash_input,
)

# Salon (group chat) imports — see salon_manager.py / convener.py / salon_dispatcher.py
import salon_manager as _salon_manager_mod
import salon_dispatcher as _salon_dispatcher_mod


# --- Client Session Tracking (for notifications) ---

@dataclass
class ClientSession:
    """Tracks a connected WebSocket client's visibility state."""
    websocket: WebSocket
    is_active: bool = True  # User is actively viewing (visible + focused)
    current_chat_id: Optional[str] = None  # Which chat they're viewing
    last_heartbeat: float = field(default_factory=time.time)

    def update_visibility(self, is_active: bool, chat_id: Optional[str] = None):
        """Update visibility state."""
        self.is_active = is_active
        if chat_id is not None:
            self.current_chat_id = chat_id
        self.last_heartbeat = time.time()

    def is_stale(self, timeout_seconds: float = 90) -> bool:
        """Check if heartbeat is stale (no updates in timeout period)."""
        return time.time() - self.last_heartbeat > timeout_seconds

# Logging - output to both console and rotating file
from logging.handlers import RotatingFileHandler

SERVER_DIR = Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            SERVER_DIR / "server.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=3,              # Keep 3 rotated files (40 MB max total)
        )
    ]
)
logger = logging.getLogger("server")

# Secret used by Codex MCP bridge subprocesses to hand ping launches back to
# this long-lived server process without exposing an unauthenticated invoke
# endpoint. Codex stdio MCP does not reliably inherit dynamic backend env, so a
# local 0600 token file is the stable handoff source once it exists. That keeps
# unrelated imports of main.py from rotating the relay token out from under the
# live backend process.
INTERNAL_AGENT_INVOKE_TOKEN_FILE = REPO_ROOT / ".claude" / ".secrets" / "internal_agent_invoke_token"


def _read_internal_agent_invoke_token_file(token_file: Path = INTERNAL_AGENT_INVOKE_TOKEN_FILE) -> Optional[str]:
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def _load_internal_agent_invoke_token(token_file: Path = INTERNAL_AGENT_INVOKE_TOKEN_FILE) -> str:
    return (
        _read_internal_agent_invoke_token_file(token_file)
        or os.environ.get("SECOND_BRAIN_INTERNAL_AGENT_TOKEN")
        or uuid.uuid4().hex
    )


INTERNAL_AGENT_INVOKE_TOKEN = _load_internal_agent_invoke_token()
os.environ["SECOND_BRAIN_INTERNAL_AGENT_TOKEN"] = INTERNAL_AGENT_INVOKE_TOKEN


def _write_internal_agent_invoke_token_file(token: str) -> None:
    try:
        secret_dir = INTERNAL_AGENT_INVOKE_TOKEN_FILE.parent
        secret_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(secret_dir, 0o700)
        except OSError:
            pass
        tmp_path = INTERNAL_AGENT_INVOKE_TOKEN_FILE.with_suffix(".tmp")
        tmp_path.write_text(token + "\n", encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, INTERNAL_AGENT_INVOKE_TOKEN_FILE)
        os.chmod(INTERNAL_AGENT_INVOKE_TOKEN_FILE, 0o600)
    except Exception as exc:
        logger.error(
            "Failed to write internal agent invoke token file %s: %s",
            INTERNAL_AGENT_INVOKE_TOKEN_FILE,
            exc,
        )


_write_internal_agent_invoke_token_file(INTERNAL_AGENT_INVOKE_TOKEN)
# Child MCP/Codex processes that do inherit this value can compare it to
# os.getpid() to know whether they are running in the durable backend loop or in
# a caller-owned process that must relay ping launches back here.
os.environ["SECOND_BRAIN_BACKEND_PID"] = str(os.getpid())

if STARTUP_ENV_ERROR:
    logger.warning("Failed to load startup env file %s: %s", STARTUP_ENV_FILE, STARTUP_ENV_ERROR)
elif STARTUP_ENV_KEYS:
    logger.info(
        "Loaded startup env file %s: %d key(s): %s",
        STARTUP_ENV_FILE,
        len(STARTUP_ENV_KEYS),
        ", ".join(sorted(STARTUP_ENV_KEYS)),
    )
else:
    logger.info("No startup env file found at %s", STARTUP_ENV_FILE)

app = FastAPI(title="Second Brain API")

app.include_router(anthropic_cache_proxy_router)

# Static files - serve built React app
CLIENT_BUILD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../client/dist"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
UI_CONFIG_FILE = os.path.join(ROOT_DIR, ".claude", "ui_config.json")
PREFERENCES_FILE = os.path.join(ROOT_DIR, ".claude", "user_preferences.json")
CHATS_DIR = os.path.join(ROOT_DIR, ".claude", "chats")
WAL_DIR = os.path.join(ROOT_DIR, ".claude", "wal")
CHAT_IMAGES_DIR = os.path.join(ROOT_DIR, ".claude", "chat_images")
SERVER_STATE_FILE = os.path.join(ROOT_DIR, ".claude", "server_state.json")
RESTART_CONTINUATION_FILE = os.path.join(ROOT_DIR, ".claude", "restart_continuation.json")
os.makedirs(CHATS_DIR, exist_ok=True)
os.makedirs(WAL_DIR, exist_ok=True)
os.makedirs(CHAT_IMAGES_DIR, exist_ok=True)

# Initialize Write-Ahead Log for message persistence
message_wal = init_wal(WAL_DIR)


def load_ui_config():
    """Load UI visibility config from ui_config.json"""
    defaults = {
        "exclude_dirs": {'.git', 'node_modules', '__pycache__', 'venv', '.vite', 'interface', 'site-packages', 'chat_search', 'docs'},
        "exclude_files": {'.DS_Store'},
        "exclude_patterns": []
    }
    if not os.path.exists(UI_CONFIG_FILE):
        return defaults
    try:
        with open(UI_CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
        return {
            "exclude_dirs": set(cfg.get("exclude_dirs", [])),
            "exclude_files": set(cfg.get("exclude_files", [])),
            "exclude_patterns": [re.compile(p) for p in cfg.get("exclude_patterns", [])]
        }
    except Exception as e:
        logging.warning(f"Failed to load ui_config.json: {e}, using defaults")
        return defaults


# Initialize chat manager
chat_manager = ChatManager(CHATS_DIR)


# --- Server State Management (for restart continuity) ---

def save_server_state():
    """Save active session state before shutdown."""
    # Build a map of actively processing sessions -> agent names
    processing_agents = {}
    for sid in active_processing_sessions:
        agent = "character"  # Default
        try:
            stored = chat_manager.load_chat(sid)
            if stored and stored.get("agent"):
                agent = stored["agent"]
        except Exception:
            pass
        processing_agents[sid] = agent

    state = {
        "shutdown_time": datetime.now().isoformat(),
        "active_sessions": list(active_conversations.keys()),
        "active_processing": processing_agents,
        "had_active_websockets": len(client_sessions) > 0
    }
    try:
        with open(SERVER_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        logger.info(f"Saved server state: {len(state['active_sessions'])} active sessions, {len(processing_agents)} processing")
    except Exception as e:
        logger.error(f"Failed to save server state: {e}")


def load_server_state() -> Optional[Dict]:
    """Load previous server state if it exists."""
    if not os.path.exists(SERVER_STATE_FILE):
        return None
    try:
        with open(SERVER_STATE_FILE, 'r') as f:
            state = json.load(f)
        # Clear the state file after reading
        os.remove(SERVER_STATE_FILE)
        return state
    except Exception as e:
        logger.error(f"Failed to load server state: {e}")
        return None


def save_continuation_on_shutdown():
    """Save restart continuation for active chats and agent invocations.

    A fresh marker from restart_server may already exist. The shutdown handler
    still merges the backend-owned running_agents snapshot into that marker so
    child-process MCP tool calls cannot accidentally save only the triggering
    chat and lose concurrently running agent work.
    """
    try:
        running_invocations = running_agents.snapshot_all_sync()
    except Exception:
        running_invocations = []
    non_chat_running = [e for e in running_invocations if e.get("kind") != "chat"]

    all_active = {}
    for sid in active_processing_sessions:
        agent = "character"
        try:
            stored = chat_manager.load_chat(sid)
            if stored and stored.get("agent"):
                agent = stored["agent"]
        except Exception:
            pass
        all_active[sid] = agent

    if not all_active and not non_chat_running:
        logger.info("Shutdown: no active processing sessions or agent invocations, no continuation needed")
        return

    try:
        SCRIPTS_DIR_LOCAL = os.path.join(ROOT_DIR, ".claude", "scripts")
        if SCRIPTS_DIR_LOCAL not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR_LOCAL)
        import restart_tool as rt

        resumable_running = rt.filter_resumable_agent_invocations(running_invocations)
        if not all_active and not resumable_running:
            logger.info("Shutdown: no active processing sessions or resumable agent invocations, no continuation needed")
            return

        if os.path.exists(RESTART_CONTINUATION_FILE):
            try:
                file_age = time.time() - os.path.getmtime(RESTART_CONTINUATION_FILE)
                if file_age < 120:
                    with open(RESTART_CONTINUATION_FILE, "r") as f:
                        existing = json.load(f)

                    existing_sessions = existing.setdefault("sessions", [])
                    existing_session_ids = {s.get("session_id") for s in existing_sessions}
                    for sid, agent in all_active.items():
                        if sid in existing_session_ids:
                            continue
                        msg_count = 0
                        chat_file = os.path.join(CHATS_DIR, f"{sid}.json")
                        if os.path.exists(chat_file):
                            try:
                                with open(chat_file) as f:
                                    msg_count = len(json.load(f).get("messages", []))
                            except Exception:
                                pass
                        existing_sessions.append({
                            "session_id": sid,
                            "agent": agent,
                            "role": "bystander",
                            "message_count": msg_count,
                        })

                    existing_invocations = existing.setdefault("agent_invocations", [])
                    existing_invocations[:] = rt.filter_resumable_agent_invocations(existing_invocations)
                    seen = {
                        (e.get("agent"), e.get("kind"), e.get("conversation_id"), e.get("started_at"))
                        for e in existing_invocations
                    }
                    for entry in resumable_running:
                        key = (entry.get("agent"), entry.get("kind"), entry.get("conversation_id"), entry.get("started_at"))
                        if key in seen:
                            continue
                        seen.add(key)
                        existing_invocations.append({
                            "id": entry.get("id"),
                            "agent": entry.get("agent"),
                            "kind": entry.get("kind"),
                            "started_at": entry.get("started_at"),
                            "task_summary": entry.get("task_summary"),
                            "source_chat_id": entry.get("source_chat_id"),
                            "conversation_id": entry.get("conversation_id"),
                            "salon_id": entry.get("salon_id"),
                            "scheduled_task_id": entry.get("scheduled_task_id"),
                            "caller_agent": entry.get("caller_agent"),
                        })
                    with open(RESTART_CONTINUATION_FILE, "w") as f:
                        json.dump(existing, f, indent=2)
                    logger.info(
                        f"Shutdown: merged continuation marker with {len(all_active)} active session(s) "
                        f"and {len(resumable_running)} resumable agent invocation(s)"
                    )
                    return
                logger.info(f"Shutdown: continuation file is stale ({file_age:.0f}s old), overwriting")
            except Exception as merge_error:
                logger.warning(f"Shutdown: could not merge fresh continuation marker, overwriting: {merge_error}")

        first_session = next(iter(all_active), None)
        rt.save_continuation_state(
            session_id=first_session,
            reason="Server shutdown with active sessions/agents (signal handler)",
            source="shutdown_handler",
            all_active_sessions=all_active,
            running_invocations=resumable_running,
        )
        logger.info(
            f"Shutdown: saved continuation state for {len(all_active)} active session(s) "
            f"and {len(resumable_running)} resumable agent invocation(s)"
        )
    except Exception as e:
        logger.warning(f"Shutdown: could not save continuation state: {e}")


def setup_signal_handlers():
    """Setup graceful shutdown handlers.

    Save state immediately in the signal handler, then let uvicorn handle
    the actual shutdown (uvicorn also catches SIGTERM and triggers the FastAPI
    shutdown event). We do NOT call sys.exit() because that conflicts with
    uvicorn's graceful shutdown sequence — instead we re-raise the signal
    with default handling so uvicorn shuts down cleanly.
    """
    def handle_shutdown(signum, frame):
        logger.info(f"Received signal {signum}, saving state before shutdown...")
        # Flush logs immediately so we see this even if process dies
        for handler in logger.handlers:
            handler.flush()
        for handler in logging.getLogger().handlers:
            handler.flush()
        save_server_state()
        save_continuation_on_shutdown()
        # Flush again after continuation save
        for handler in logger.handlers:
            handler.flush()
        for handler in logging.getLogger().handlers:
            handler.flush()
        try:
            deregister_by_pid()
        except Exception:
            pass
        # Re-raise with default handler so uvicorn shuts down cleanly
        # (sys.exit() conflicts with uvicorn's shutdown sequence)
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)


# Track if server was restarted with active sessions
server_restart_info: Optional[Dict] = None

# Track pending restart continuation (for auto-resuming conversations)
restart_continuation: Optional[Dict] = None

def load_restart_continuation() -> Optional[Dict]:
    """Load restart continuation marker if it exists.

    Supports both legacy single-session format and new multi-session format.
    Returns normalized dict with 'sessions' list, 'reason', 'source', etc.
    """
    if not os.path.exists(RESTART_CONTINUATION_FILE):
        return None
    try:
        with open(RESTART_CONTINUATION_FILE, 'r') as f:
            continuation = json.load(f)
        # NOTE: We intentionally do NOT delete the file here. The wakeup task
        # deletes it after successfully resuming sessions. If the wakeup fails
        # (e.g. no stable WebSocket), the file persists so the next restart
        # can retry. See restart_continuation_wakeup().

        # Normalize legacy format (single session_id) to new format (sessions list)
        if "sessions" not in continuation:
            continuation["sessions"] = [{
                "session_id": continuation.get("session_id"),
                "agent": continuation.get("source", "character"),
                "role": "trigger",
                "message_count": continuation.get("message_count", 0),
            }]
            if "source" not in continuation:
                continuation["source"] = "character"

        session_count = len(continuation.get("sessions", []))
        marker_id = continuation.get("continuation_id", "legacy")
        logger.info(
            f"Loaded restart continuation: {session_count} session(s) to resume, "
            f"source={continuation.get('source')}, reason={continuation.get('reason')}, "
            f"marker_id={marker_id}"
        )
        return continuation
    except Exception as e:
        logger.error(f"Failed to load restart continuation: {e}")
        return None


def _restart_continuation_marker_matches(current: Dict[str, Any], loaded: Dict[str, Any]) -> bool:
    """Return True only when the on-disk marker is the one this task loaded.

    Restart continuation can be reentrant: a continuation turn may request another
    restart before this wakeup task reaches its cleanup block. In that case the
    file on disk is newer work for the next process, not the marker we should
    delete.
    """
    current_id = current.get("continuation_id")
    loaded_id = loaded.get("continuation_id")
    if current_id or loaded_id:
        return bool(current_id and loaded_id and current_id == loaded_id)

    # Legacy markers did not carry an explicit id. Compare the fields that were
    # persisted before startup normalization; do not require `sessions` because
    # load_restart_continuation() synthesizes it for old single-session markers.
    for field in ("restart_time", "reason", "source", "session_id"):
        if current.get(field) != loaded.get(field):
            return False

    if "sessions" in current and "sessions" in loaded:
        return current.get("sessions") == loaded.get("sessions")

    return True


# Import Scheduler Tool and Room utilities
SCRIPTS_DIR = os.path.join(ROOT_DIR, ".claude", "scripts")
if os.path.exists(SCRIPTS_DIR):
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        import scheduler_tool
    except ImportError:
        logger.warning("Could not import scheduler_tool")
        scheduler_tool = None
    try:
        import rooms_meta
        import active_room
    except ImportError:
        logger.warning("Could not import rooms_meta or active_room")
        rooms_meta = None
        active_room = None
else:
    logger.warning("Scripts dir not found")
    scheduler_tool = None
    rooms_meta = None
    active_room = None


def strip_tool_markers(content: str) -> str:
    """
    Remove legacy tool call markers from content.

    Legacy runtime output can include tool status as markdown:
    - *Running: `tool_name`...*
    - *Result:* ```...```

    For scheduled tasks, we want clean output without these internal markers.
    Regular interactive chat shows tool status in the UI status bar instead.
    """

    # Pattern for tool running markers: *Running: `tool_name`...*
    # Matches lines like: "\n\n*Running: `mcp__brain__google_list`...*\n\n"
    content = re.sub(r'\n*\*Running:\s*`[^`]+`\.\.\.?\*\n*', '', content)

    # Pattern for result markers with code blocks: *Result:* ```...```
    # The code block content can contain backticks, so use lazy match until closing ```
    content = re.sub(r'\*Result:\*\s*```[\s\S]*?```\s*', '', content)

    # Also handle simpler result markers without code blocks
    # Matches: *Result:* followed by content until next newline
    content = re.sub(r'\*Result:\*[^\n]*\n?', '', content)

    # Clean up excessive newlines that might remain
    content = re.sub(r'\n{3,}', '\n\n', content)

    # Strip leading/trailing whitespace but preserve internal structure
    return content.strip()


# Chat Titler background task
async def _run_titler_background(
    chat_id: str,
    messages: list,
    current_title: str = None,
    is_retitle: bool = False
):
    """Run the Titler agent in the background and push update via WebSocket."""
    try:
        scripts_dir = os.path.join(ROOT_DIR, ".claude", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from chat_titler import generate_title

        logger.info(f"Titler: Starting for chat {chat_id} (retitle={is_retitle})")
        result = await generate_title(messages, current_title, is_retitle)

        new_title = result.get("title", "Untitled Chat")
        should_update = result.get("should_update", True)

        if not should_update:
            logger.info(f"Titler: Keeping existing title for {chat_id}")
            return

        # Update chat file with new title
        existing = chat_manager.load_chat(chat_id)
        if existing:
            existing["title"] = new_title
            chat_manager.save_chat(chat_id, existing)
            logger.info(f"Titler: Updated title to '{new_title}' for {chat_id}")

        # Push title update to all connected clients
        await broadcast_to_all_clients({
            "type": "chat_title_update",
            "session_id": chat_id,
            "title": new_title,
            "confidence": result.get("confidence", 0.5)
        })

    except Exception as e:
        logger.error(f"Titler: Background task failed: {e}")


# --- Background Processing ---

# Track active bg processing tasks to prevent overlapping runs
_bg_processing_active: Set[str] = set()


def _format_conversation_history(messages: List[Dict[str, Any]]) -> str:
    """Format entire conversation history into readable text for background processing."""
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        if not content:
            continue
        # Skip form data entries
        if msg.get("formData"):
            continue
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _get_bg_config(agent_name: str) -> Optional[Dict[str, Any]]:
    """Get background processing config for an agent, with defaults applied."""
    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from registry import get_registry

    registry = get_registry()
    config = registry.get(agent_name)
    if not config:
        return None

    # Apply defaults
    bg = config.background_processing or {}
    return {
        "enabled": bg.get("enabled", True),
        "trigger_exchanges": bg.get("trigger_exchanges", 3),
        "idle_timeout_minutes": bg.get("idle_timeout_minutes", 30),
        "prompt": config.background_prompt or "",
    }


def _maybe_trigger_background_processing(
    chat_id: str,
    conv: ConversationState,
    agent_name: Optional[str],
):
    """Check if background processing should fire after an exchange."""
    if not agent_name:
        return

    bg_config = _get_bg_config(agent_name)
    if not bg_config or not bg_config["enabled"] or not bg_config["prompt"]:
        return

    trigger_exchanges = bg_config["trigger_exchanges"]
    exchanges_since_last = conv.exchange_count - conv.last_bg_exchange

    if exchanges_since_last >= trigger_exchanges:
        # Prevent overlapping runs for the same chat
        if chat_id in _bg_processing_active:
            logger.info(f"BG_PROCESSING: Already running for {chat_id}, skipping")
            return

        logger.info(
            f"BG_PROCESSING: Triggering for {agent_name} in chat {chat_id} "
            f"(exchange {conv.exchange_count}, last bg at {conv.last_bg_exchange})"
        )
        conv.last_bg_exchange = conv.exchange_count
        asyncio.create_task(_run_background_processing(
            chat_id=chat_id,
            agent_name=agent_name,
            messages=list(conv.messages),  # Snapshot current messages
            bg_prompt=bg_config["prompt"],
        ))


async def _run_background_processing(
    chat_id: str,
    agent_name: str,
    messages: List[Dict[str, Any]],
    bg_prompt: str,
):
    """Run background processing for an agent — full history + bg prompt via invoke_agent."""
    _bg_processing_active.add(chat_id)
    try:
        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
        if str(agents_dir) not in sys.path:
            sys.path.insert(0, str(agents_dir))
        from runner import invoke_agent

        # Format the entire conversation history
        history = _format_conversation_history(messages)
        if not history.strip():
            logger.info(f"BG_PROCESSING: No meaningful history for {chat_id}, skipping")
            return

        # Build the combined prompt
        combined_prompt = (
            f"[CONVERSATION HISTORY]\n{history}\n\n"
            f"[BACKGROUND PROCESSING INSTRUCTIONS]\n{bg_prompt}"
        )

        logger.info(
            f"BG_PROCESSING: Starting for {agent_name} in chat {chat_id} "
            f"({len(messages)} messages, {len(combined_prompt)} chars)"
        )

        result = await invoke_agent(
            name=agent_name,
            prompt=combined_prompt,
            mode="trust",
            is_background_processing=True,
        )

        status = result.get("status") if isinstance(result, dict) else getattr(result, "status", "unknown")
        logger.info(
            f"BG_PROCESSING: Completed for {agent_name} in chat {chat_id} "
            f"(status={status if result else 'no_result'})"
        )

    except Exception as e:
        logger.error(f"BG_PROCESSING: Failed for {agent_name} in chat {chat_id}: {e}")
    finally:
        _bg_processing_active.discard(chat_id)


async def _background_processing_idle_watcher():
    """Periodic task that checks for idle conversations and triggers background processing."""
    logger.info("BG_PROCESSING: Idle watcher started (30 min interval)")
    while True:
        try:
            await asyncio.sleep(1800)  # 30 minutes

            now = time.time()
            for chat_id, conv in list(active_conversations.items()):
                # Skip if no exchanges yet
                if conv.exchange_count == 0:
                    continue
                # Skip if already processed since last exchange
                if conv.last_bg_exchange >= conv.exchange_count:
                    continue
                # Skip if no last_exchange_time set
                if conv.last_exchange_time == 0:
                    continue
                # Check if idle long enough (30 min default)
                idle_seconds = now - conv.last_exchange_time
                if idle_seconds < 1800:
                    continue

                # Look up agent name from stored chat data
                stored = chat_manager.load_chat(chat_id)
                if not stored:
                    continue
                agent_name = stored.get("agent")
                if not agent_name:
                    continue

                bg_config = _get_bg_config(agent_name)
                if not bg_config or not bg_config["enabled"] or not bg_config["prompt"]:
                    continue

                idle_timeout = bg_config["idle_timeout_minutes"] * 60
                if idle_seconds < idle_timeout:
                    continue

                # Prevent overlapping runs
                if chat_id in _bg_processing_active:
                    continue

                logger.info(
                    f"BG_PROCESSING: Idle trigger for {agent_name} in chat {chat_id} "
                    f"(idle {idle_seconds:.0f}s, threshold {idle_timeout}s)"
                )
                conv.last_bg_exchange = conv.exchange_count
                asyncio.create_task(_run_background_processing(
                    chat_id=chat_id,
                    agent_name=agent_name,
                    messages=list(conv.messages),
                    bg_prompt=bg_config["prompt"],
                ))

        except asyncio.CancelledError:
            logger.info("BG_PROCESSING: Idle watcher cancelled")
            break
        except Exception as e:
            logger.error(f"BG_PROCESSING: Idle watcher error: {e}")


# --- Salon background processing ---
#
# Same idea as 1:1 chat bg processing, but per-agent within a salon. When an
# agent has been quiet in a salon for their idle threshold, fire their bg
# hook with the salon history rendered as a conversation (their messages =
# "assistant", everyone else's = "user" prefixed with the sender's name).
#
# The "session ended from their POV" moment is when they hit idle in a salon.
# Character's idea — confirmed in the spec.

# Per-(salon_id, agent, frontier) overlap guard. Durable correctness lives in
# each salon JSON's salon_bg_processing marker, not in this process-local set.
_salon_bg_inflight: Set[str] = set()


def _salon_bg_inflight_key(salon_id: str, agent_name: str, frontier_key: str) -> str:
    return f"{salon_id}:{agent_name}:{frontier_key}"


def _format_salon_history_for_bg(
    messages: List[Dict[str, Any]],
    target_agent: str,
) -> List[Dict[str, Any]]:
    """Translate salon messages into chat-style messages for bg processing.

    The target agent's own messages become role="assistant"; everyone else's
    become role="user" with a "[from <name>]" prefix so the agent can tell who
    said what without changing the role schema bg processing expects.
    """
    out: List[Dict[str, Any]] = []
    for msg in messages or []:
        sender = msg.get("from") or "unknown"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if sender == target_agent:
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": "user", "content": f"[from {sender}] {content}"})
    return out


async def _run_salon_background_processing(
    salon_id: str,
    salon_title: str,
    agent_name: str,
    frontier_key: str,
    messages: List[Dict[str, Any]],
    bg_prompt: str,
) -> None:
    """Run bg processing for one agent against a salon's history."""
    key = _salon_bg_inflight_key(salon_id, agent_name, frontier_key)
    _salon_bg_inflight.add(key)
    finish_status = "failed"
    result_status: Optional[str] = None
    error: Optional[str] = None
    try:
        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
        if str(agents_dir) not in sys.path:
            sys.path.insert(0, str(agents_dir))
        from runner import invoke_agent

        translated = _format_salon_history_for_bg(messages, agent_name)
        history = _format_conversation_history(translated)
        if not history.strip():
            finish_status = "skipped_empty_history"
            logger.info(f"SALON_BG: No meaningful history for {agent_name} in salon {salon_id}, skipping")
            return

        combined_prompt = (
            f"[SALON: \"{salon_title}\" (salon_id={salon_id})]\n"
            f"[CONTEXT: You were a participant in this salon. The conversation "
            f"has gone idle — this is your natural 'session ended' moment.]\n\n"
            f"[CONVERSATION HISTORY]\n{history}\n\n"
            f"[BACKGROUND PROCESSING INSTRUCTIONS]\n{bg_prompt}"
        )

        logger.info(
            f"SALON_BG: Starting for {agent_name} in salon {salon_id} "
            f"frontier={frontier_key} "
            f"({len(messages)} messages, {len(combined_prompt)} chars)"
        )
        result = await invoke_agent(
            name=agent_name,
            prompt=combined_prompt,
            mode="trust",
            is_background_processing=True,
        )
        result_status = result.get("status") if isinstance(result, dict) else getattr(result, "status", "unknown")
        finish_status = "completed"
        logger.info(
            f"SALON_BG: Completed for {agent_name} in salon {salon_id} "
            f"frontier={frontier_key} (status={result_status})"
        )
    except Exception as e:
        error = str(e)
        logger.error(f"SALON_BG: Failed for {agent_name} in salon {salon_id} frontier={frontier_key}: {e}")
    finally:
        try:
            mgr = _salon_manager_mod.get_manager()
            if not mgr.finish_salon_bg_processing(
                salon_id=salon_id,
                agent_name=agent_name,
                frontier_key=frontier_key,
                status=finish_status,
                result_status=result_status,
                error=error,
            ):
                logger.info(
                    f"SALON_BG: Finish marker skipped for {agent_name} in salon {salon_id} "
                    f"frontier={frontier_key} (frontier changed or claim missing)"
                )
        except Exception as finish_error:
            logger.error(
                f"SALON_BG: Failed to finish marker for {agent_name} in salon {salon_id} "
                f"frontier={frontier_key}: {finish_error}"
            )
        _salon_bg_inflight.discard(key)


async def _scan_salon_background_processing_once(now: Optional[float] = None) -> int:
    """Scan salons once and schedule eligible durable bg-processing work."""
    now = time.time() if now is None else now
    scheduled = 0

    try:
        mgr = _salon_manager_mod.get_manager()
    except Exception:
        return 0

    for summary in mgr.list_all(limit=10_000):
        salon_id = summary.get("salon_id")
        if not salon_id:
            continue
        # Don't bg-process locked salons (active dispatch in flight)
        if summary.get("locked"):
            continue

        salon = mgr.load(salon_id)
        if not salon:
            continue

        if not mgr.has_salon_bg_processing_schema(salon):
            backfilled = mgr.backfill_salon_bg_processing_if_missing(salon_id)
            logger.info(
                f"SALON_BG: Backfilled {backfilled} pre-durable frontier(s) "
                f"for salon {salon_id}"
            )
            salon = mgr.load(salon_id)
            if not salon:
                continue

        messages = salon.get("messages") or []
        if not messages:
            continue

        participants = list(salon.get("participants") or [])
        title = salon.get("title") or "(untitled salon)"

        for agent_name in participants:
            if agent_name == "user":
                continue

            frontier = mgr.latest_agent_frontier(messages, agent_name)
            if not frontier:
                # Agent has never spoken in this salon, or the latest legacy
                # message lacks enough data for a stable frontier.
                continue

            frontier_created_at = frontier.get("frontier_created_at")
            if frontier_created_at is None:
                logger.warning(
                    f"SALON_BG: Skipping {agent_name} in salon {salon_id}: "
                    f"frontier {frontier.get('frontier_key')} has no usable created_at"
                )
                continue

            bg_config = _get_bg_config(agent_name)
            if not bg_config or not bg_config["enabled"] or not bg_config["prompt"]:
                continue

            idle_seconds = now - float(frontier_created_at)
            idle_threshold = bg_config["idle_timeout_minutes"] * 60
            if idle_seconds < idle_threshold:
                continue

            frontier_key = frontier["frontier_key"]
            key = _salon_bg_inflight_key(salon_id, agent_name, frontier_key)
            if key in _salon_bg_inflight:
                continue

            claimed = mgr.claim_salon_bg_processing(
                salon_id=salon_id,
                agent_name=agent_name,
                expected_frontier_key=frontier_key,
                expected_frontier_message_id=frontier.get("frontier_message_id"),
                expected_frontier_created_at=frontier_created_at,
                expected_frontier_message_index=frontier.get("frontier_message_index"),
            )
            if not claimed:
                continue

            logger.info(
                f"SALON_BG: Idle trigger — {agent_name} in salon "
                f"{salon_id} frontier={frontier_key} (idle {idle_seconds:.0f}s, "
                f"threshold {idle_threshold}s)"
            )
            asyncio.create_task(_run_salon_background_processing(
                salon_id=salon_id,
                salon_title=title,
                agent_name=agent_name,
                frontier_key=frontier_key,
                messages=list(messages),
                bg_prompt=bg_config["prompt"],
            ))
            scheduled += 1

    return scheduled


async def _salon_background_processing_watcher():
    """Periodic task: fire each agent's bg hook when they hit idle in a salon.

    For each salon, for each participating agent (not zeke), durable salon JSON
    records whether that agent's latest own-message frontier has already been
    claimed. Process-local state is only an overlap guard while a task runs.
    """
    logger.info("SALON_BG: Idle watcher started (5 min interval)")
    while True:
        try:
            await asyncio.sleep(300)  # 5 min — fine-grained enough; cheap
            await _scan_salon_background_processing_once()
        except asyncio.CancelledError:
            logger.info("SALON_BG: Idle watcher cancelled")
            break
        except Exception as e:
            logger.error(f"SALON_BG: Idle watcher error: {e}")


# --- Pydantic Models ---

class FileRequest(BaseModel):
    path: str
    content: Optional[str] = None


class RenameRequest(BaseModel):
    path: str
    new_name: str


class MoveRequest(BaseModel):
    source: str       # relative path of file/folder to move
    destination: str  # relative path of destination directory

class AppBridgeWriteRequest(BaseModel):
    path: str
    data: str


class EditMessageRequest(BaseModel):
    session_id: str
    message_id: str
    new_content: str


class RegenerateRequest(BaseModel):
    session_id: str
    message_id: str


DEFAULT_HELPER_SETTINGS = {
    "titler": {"paused": False},
    "contextual_memory": {"mode": "auto", "manual_query": ""},
}


def _normalize_helper_settings(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the persisted per-chat helper settings shape."""
    raw = value if isinstance(value, dict) else {}
    raw_titler = raw.get("titler") if isinstance(raw.get("titler"), dict) else {}
    raw_memory = raw.get("contextual_memory")
    if not isinstance(raw_memory, dict):
        raw_memory = raw.get("contextualMemory") if isinstance(raw.get("contextualMemory"), dict) else {}

    mode = raw_memory.get("mode", "auto")
    if mode not in {"auto", "off", "manual"}:
        mode = "auto"

    manual_query = raw_memory.get("manual_query")
    if manual_query is None:
        manual_query = raw_memory.get("manualQuery", "")

    return {
        "titler": {
            "paused": bool(raw_titler.get("paused", raw.get("titler_paused", False))),
        },
        "contextual_memory": {
            "mode": mode,
            "manual_query": str(manual_query or ""),
        },
    }


def _merge_helper_settings(
    base: Optional[Dict[str, Any]],
    updates: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge a partial helper-settings payload onto an existing chat value."""
    merged = _normalize_helper_settings(base)
    if not isinstance(updates, dict):
        return merged

    titler = updates.get("titler")
    if isinstance(titler, dict) and "paused" in titler:
        merged["titler"]["paused"] = bool(titler.get("paused"))
    elif "titler_paused" in updates:
        merged["titler"]["paused"] = bool(updates.get("titler_paused"))

    memory = updates.get("contextual_memory")
    if not isinstance(memory, dict):
        memory = updates.get("contextualMemory") if isinstance(updates.get("contextualMemory"), dict) else None
    if isinstance(memory, dict):
        if "mode" in memory and memory.get("mode") in {"auto", "off", "manual"}:
            merged["contextual_memory"]["mode"] = memory["mode"]
        if "manual_query" in memory or "manualQuery" in memory:
            manual_query = memory.get("manual_query", memory.get("manualQuery", ""))
            merged["contextual_memory"]["manual_query"] = str(manual_query or "")

    return _normalize_helper_settings(merged)


def _extract_helper_settings_payload(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    payload = data.get("helper_settings")
    if payload is None:
        payload = data.get("helperSettings")
    return payload if isinstance(payload, dict) else None


def _chat_titler_paused(helper_settings: Optional[Dict[str, Any]]) -> bool:
    return _normalize_helper_settings(helper_settings)["titler"]["paused"]


class ChatUpdateRequest(BaseModel):
    title: Optional[str] = None
    messages: Optional[List[Dict[str, Any]]] = None
    helper_settings: Optional[Dict[str, Any]] = None


class UIConfigUpdateRequest(BaseModel):
    exclude_dirs: Optional[List[str]] = None
    exclude_files: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    default_editor_file: Optional[str] = None


class UserPreferencesUpdate(BaseModel):
    theme: Optional[Dict[str, Any]] = None
    typography: Optional[Dict[str, Any]] = None


# --- User Preferences API (synced across devices) ---

@app.get("/api/preferences")
def get_preferences():
    """Get user preferences (theme + typography), synced across all devices."""
    if not os.path.exists(PREFERENCES_FILE):
        return {"theme": None, "typography": None}
    try:
        with open(PREFERENCES_FILE, 'r') as f:
            data = json.load(f)
        return {
            "theme": data.get("theme"),
            "typography": data.get("typography"),
        }
    except Exception as e:
        logger.error(f"Failed to load user preferences: {e}")
        raise HTTPException(status_code=500, detail="Failed to load preferences")


@app.patch("/api/preferences")
def update_preferences(req: UserPreferencesUpdate):
    """Update user preferences (partial update). Syncs across all devices."""
    existing = {}
    if os.path.exists(PREFERENCES_FILE):
        try:
            with open(PREFERENCES_FILE, 'r') as f:
                existing = json.load(f)
        except Exception:
            pass

    if req.theme is not None:
        existing["theme"] = req.theme
    if req.typography is not None:
        existing["typography"] = req.typography

    try:
        os.makedirs(os.path.dirname(PREFERENCES_FILE), exist_ok=True)
        with open(PREFERENCES_FILE, 'w') as f:
            json.dump(existing, f, indent=2)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to save user preferences: {e}")
        raise HTTPException(status_code=500, detail="Failed to save preferences")


# --- UI Config API ---

@app.get("/api/slash-commands")
def get_slash_commands():
    """Return the registry of available slash commands for client-side autocomplete."""
    return {"commands": get_command_menu()}


@app.get("/api/ui-config")
def get_ui_config():
    """Get UI visibility configuration."""
    if not os.path.exists(UI_CONFIG_FILE):
        return {
            "exclude_dirs": [".git", "node_modules", "__pycache__", "venv", ".vite", "interface", "site-packages", "chat_search", "docs"],
            "exclude_files": [".DS_Store"],
            "exclude_patterns": ["^\\..*", ".*\\.pyc$"],
            "default_editor_file": ""
        }
    try:
        with open(UI_CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
        return {
            "exclude_dirs": cfg.get("exclude_dirs", []),
            "exclude_files": cfg.get("exclude_files", []),
            "exclude_patterns": cfg.get("exclude_patterns", []),
            "default_editor_file": cfg.get("default_editor_file", "")
        }
    except Exception as e:
        logger.error(f"Failed to load ui_config.json: {e}")
        raise HTTPException(status_code=500, detail="Failed to load UI config")


@app.patch("/api/ui-config")
def update_ui_config(req: UIConfigUpdateRequest):
    """Update UI visibility configuration."""
    # Load existing config
    existing = {}
    if os.path.exists(UI_CONFIG_FILE):
        try:
            with open(UI_CONFIG_FILE, 'r') as f:
                existing = json.load(f)
        except Exception:
            pass

    # Update only provided fields
    if req.exclude_dirs is not None:
        existing["exclude_dirs"] = req.exclude_dirs
    if req.exclude_files is not None:
        existing["exclude_files"] = req.exclude_files
    if req.exclude_patterns is not None:
        existing["exclude_patterns"] = req.exclude_patterns
    if req.default_editor_file is not None:
        existing["default_editor_file"] = req.default_editor_file

    # Preserve metadata fields
    existing["_comment"] = "UI visibility configuration for Second Brain file explorer"
    existing["_usage"] = {
        "exclude_dirs": "Exact directory names to hide (matched at any level)",
        "exclude_files": "Exact file names to hide (matched at any level)",
        "exclude_patterns": "Regex patterns applied to full relative paths",
        "default_editor_file": "File path to open by default when editor loads (relative to root)"
    }

    # Save
    try:
        os.makedirs(os.path.dirname(UI_CONFIG_FILE), exist_ok=True)
        with open(UI_CONFIG_FILE, 'w') as f:
            json.dump(existing, f, indent=2)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to save ui_config.json: {e}")
        raise HTTPException(status_code=500, detail="Failed to save UI config")


# --- Server Restart API ---

@app.post("/api/restart")
def restart_server_endpoint(rebuild: bool = False, reason: str = None):
    """Fail closed: direct UI/API restarts are disabled by restart canon."""
    raise HTTPException(
        status_code=410,
        detail=(
            "Direct UI/API restarts are disabled. Backend restarts are managed "
            "through Patch's safe MCP restart path."
        ),
    )


# --- File API ---


def _file_etag(file_path: str) -> str:
    """Generate an ETag from file modification time and size."""
    stat = os.stat(file_path)
    return f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'


def _file_response_with_etag(target_path: str, request: Request) -> Response:
    """Return a FileResponse with ETag/Cache-Control, or 304 if unchanged."""
    etag = _file_etag(target_path)
    cache_headers = {
        "Cache-Control": "no-cache, must-revalidate",
        "ETag": etag,
    }
    if_none_match = request.headers.get("if-none-match")
    if if_none_match == etag:
        return Response(status_code=304, headers=cache_headers)
    return FileResponse(target_path, headers=cache_headers)


@app.get("/api/files")
def list_files(path: str = ""):
    target_dir = os.path.join(ROOT_DIR, path)
    if not os.path.abspath(target_dir).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")

    cfg = load_ui_config()
    files = []

    for root, dirs, filenames in os.walk(target_dir):
        dirs[:] = [d for d in dirs if d not in cfg["exclude_dirs"]]

        for filename in filenames:
            if filename in cfg["exclude_files"]:
                continue
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, ROOT_DIR)
            if any(p.search(rel_path) for p in cfg["exclude_patterns"]):
                continue
            files.append(rel_path)

    return {"files": sorted(files)}


@app.get("/api/file/{file_path:path}")
def read_file(file_path: str, request: Request):
    target_path = os.path.join(ROOT_DIR, file_path)
    if not os.path.abspath(target_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404)
    # Serve binary files (images, PDFs, etc.) directly instead of trying UTF-8 decode
    import mimetypes
    mime_type, _ = mimetypes.guess_type(target_path)
    if mime_type and not mime_type.startswith("text/") and mime_type not in (
        "application/json", "application/xml", "application/javascript",
        "application/x-yaml", "application/toml",
    ):
        return _file_response_with_etag(target_path, request)
    try:
        with open(target_path, 'r', encoding='utf-8') as f:
            return {"content": f.read()}
    except UnicodeDecodeError:
        # Fallback: serve as binary if UTF-8 decode fails
        return _file_response_with_etag(target_path, request)


@app.get("/api/raw/{file_path:path}")
def raw_file(file_path: str, request: Request):
    """Serve a file as-is (binary-safe) for images, PDFs, etc."""
    target_path = os.path.join(ROOT_DIR, file_path)
    if not os.path.abspath(target_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404)
    return _file_response_with_etag(target_path, request)


@app.post("/api/file/{file_path:path}")
def save_file(file_path: str, req: FileRequest):
    target_path = os.path.join(ROOT_DIR, file_path)
    if not os.path.abspath(target_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(req.content or "")
    return {"status": "ok"}


@app.post("/api/upload/{dir_path:path}")
async def upload_files(dir_path: str, files: List[UploadFile] = FastAPIFile(...)):
    """Upload one or more files to a directory."""
    target_dir = os.path.join(ROOT_DIR, dir_path) if dir_path else ROOT_DIR
    if not os.path.abspath(target_dir).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")
    os.makedirs(target_dir, exist_ok=True)

    uploaded = []
    for file in files:
        filename = os.path.basename(file.filename or "upload")
        target_path = os.path.join(target_dir, filename)
        if not os.path.abspath(target_path).startswith(ROOT_DIR):
            raise HTTPException(status_code=403, detail="Access denied")
        content = await file.read()
        with open(target_path, 'wb') as f:
            f.write(content)
        uploaded.append(os.path.relpath(target_path, ROOT_DIR))

    return {"status": "ok", "paths": uploaded}


# ========== Chat Image Upload ==========
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_SIZE = 25 * 1024 * 1024  # 25MB

# Magic byte signatures for server-side image type validation
_IMAGE_MAGIC_BYTES = {
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    "image/webp": [b"RIFF"],  # Full check: RIFF????WEBP
}

def _validate_image_magic(content: bytes, claimed_type: str) -> bool:
    """Validate that file content magic bytes match the claimed MIME type."""
    signatures = _IMAGE_MAGIC_BYTES.get(claimed_type)
    if not signatures:
        return False
    for sig in signatures:
        if content[:len(sig)] == sig:
            # Extra check for WebP: bytes 8-12 must be "WEBP"
            if claimed_type == "image/webp" and content[8:12] != b"WEBP":
                return False
            return True
    return False

def _apply_exif_orientation(content: bytes, content_type: str) -> bytes:
    """Rotate image pixels according to EXIF Orientation tag, then strip the tag.

    Phone cameras capture sensor-native (landscape) and set EXIF Orientation
    instead of rotating pixels. If we ignore the tag, images render sideways
    for any consumer that doesn't honor EXIF (e.g. model vision input, some
    browser paths). Rotating pixels + stripping the tag makes the stored
    bytes the source of truth.

    - Skips GIF entirely to preserve animation.
    - No-op when the image has no Orientation tag or Orientation=1 (normal).
    - Falls back to original bytes on any decode/encode error.
    """
    if content_type not in ("image/jpeg", "image/png", "image/webp"):
        return content
    try:
        img = Image.open(BytesIO(content))
        orientation = img.getexif().get(0x0112)  # 0x0112 == Orientation
        if not orientation or orientation == 1:
            return content  # No rotation needed — don't re-encode and lose quality.
        transposed = ImageOps.exif_transpose(img)
        if transposed is None:
            return content
        out = BytesIO()
        if content_type == "image/jpeg":
            # Preserve color profile & high quality; re-encode without EXIF.
            transposed.save(out, format="JPEG", quality=95, optimize=True)
        elif content_type == "image/png":
            transposed.save(out, format="PNG", optimize=True)
        else:  # image/webp
            transposed.save(out, format="WEBP", quality=95)
        return out.getvalue()
    except Exception as e:
        logger.warning(f"[images] EXIF transpose failed ({e}); keeping original bytes.")
        return content


@app.post("/api/chat/images")
async def upload_chat_images(files: List[UploadFile] = FastAPIFile(...)):
    """Upload images for use in chat messages. Returns image IDs and URLs."""
    uploaded = []
    for file in files:
        # Validate content type
        content_type = file.content_type or ""
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported image type: {content_type}. Allowed: jpg, png, gif, webp")

        content = await file.read()
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=400, detail=f"Image too large: {len(content)} bytes. Max: {MAX_IMAGE_SIZE}")

        # Server-side magic byte validation — prevents MIME spoofing (e.g. HTML disguised as image/png)
        if not _validate_image_magic(content, content_type):
            raise HTTPException(status_code=400, detail=f"File content does not match claimed type: {content_type}")

        # Normalize EXIF orientation: rotate pixels, strip tag. Fixes the
        # "portrait photos show up sideways" bug for all consumers (browsers,
        # model vision, downstream agents). Hash is computed AFTER so dedupe
        # works on the canonical (rotated) bytes.
        content = _apply_exif_orientation(content, content_type)

        # Generate unique filename using content hash
        content_hash = hashlib.sha256(content).hexdigest()[:12]
        ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }.get(content_type, ".bin")
        filename = f"{content_hash}{ext}"

        # Save to chat_images directory
        target_path = os.path.join(CHAT_IMAGES_DIR, filename)
        with open(target_path, 'wb') as f:
            f.write(content)

        # Return image info
        image_url = f"/api/chat/images/{filename}"
        uploaded.append({
            "id": content_hash,
            "filename": filename,
            "url": image_url,
            "type": content_type,
            "size": len(content),
            "originalName": file.filename or "image",
        })

    return {"status": "ok", "images": uploaded}


@app.get("/api/chat/images/{filename}")
async def serve_chat_image(filename: str):
    """Serve a chat image."""
    # Sanitize filename to prevent path traversal
    safe_filename = os.path.basename(filename)
    target_path = os.path.join(CHAT_IMAGES_DIR, safe_filename)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Image not found")
    # Chat images are content-hashed and immutable — cache aggressively
    return FileResponse(target_path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.post("/api/chat/images/gc")
async def chat_image_gc(delete: bool = False, min_age_days: float = 7.0):
    """Run garbage collection on orphaned chat images.

    Args:
        delete: If True, actually delete orphans. If False, dry run (default).
        min_age_days: Only delete orphans older than this many days (default: 7).
    """
    try:
        # Import the GC module
        import importlib.util
        gc_script = os.path.join(ROOT_DIR, ".claude", "scripts", "chat_image_gc.py")
        spec = importlib.util.spec_from_file_location("chat_image_gc", gc_script)
        gc_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gc_module)

        result = gc_module.run_gc(
            delete=delete,
            min_age_days=min_age_days,
            as_json=True,
        )
        return result
    except Exception as e:
        logger.error(f"Chat image GC error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/file/{file_path:path}")
def delete_file(file_path: str):
    target_path = os.path.join(ROOT_DIR, file_path)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="File not found")

    if not os.path.abspath(target_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        if os.path.isdir(target_path):
            import shutil
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/rename")
def rename_file(req: RenameRequest):
    old_path = os.path.join(ROOT_DIR, req.path)
    parent_dir = os.path.dirname(old_path)

    if '/' in req.new_name or '\\' in req.new_name:
        new_path = os.path.join(ROOT_DIR, req.new_name)
    else:
        new_path = os.path.join(parent_dir, req.new_name)

    # Authorization/bounds check FIRST — prevents info disclosure via 404 on traversal attempts
    if not os.path.abspath(old_path).startswith(ROOT_DIR) or \
       not os.path.abspath(new_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="File not found")

    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="Destination already exists")

    os.rename(old_path, new_path)
    return {"status": "ok"}


@app.post("/api/move")
def move_file(req: MoveRequest):
    """Move a file or folder to a new directory."""
    import shutil

    src_path = os.path.join(ROOT_DIR, req.source)
    dest_dir = os.path.join(ROOT_DIR, req.destination) if req.destination else ROOT_DIR

    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="Source not found")

    # Security: ensure both paths are within ROOT_DIR
    if not os.path.abspath(src_path).startswith(ROOT_DIR) or \
       not os.path.abspath(dest_dir).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")

    # Can't move into itself or its own children
    if os.path.isdir(src_path):
        abs_src = os.path.abspath(src_path)
        abs_dest = os.path.abspath(dest_dir)
        if abs_dest == abs_src or abs_dest.startswith(abs_src + os.sep):
            raise HTTPException(status_code=400, detail="Cannot move a folder into itself")

    item_name = os.path.basename(src_path)
    final_dest = os.path.join(dest_dir, item_name)

    if os.path.exists(final_dest):
        raise HTTPException(status_code=400, detail=f"'{item_name}' already exists in the destination")

    # Ensure destination directory exists
    os.makedirs(dest_dir, exist_ok=True)

    shutil.move(src_path, final_dest)
    return {"status": "ok", "new_path": os.path.relpath(final_dest, ROOT_DIR)}


# --- App Bridge API (for HTML apps running in editor) ---

APP_DATA_DIR = os.path.join(ROOT_DIR, "05_App_Data")
os.makedirs(APP_DATA_DIR, exist_ok=True)


def validate_app_path(path: str) -> str:
    """Validate and resolve app data path. Returns absolute path if valid."""
    # Normalize the path and prevent directory traversal
    normalized = os.path.normpath(path)
    if normalized.startswith('..') or normalized.startswith('/'):
        raise HTTPException(status_code=403, detail="Invalid path: directory traversal not allowed")

    # Build absolute path within app data directory
    abs_path = os.path.abspath(os.path.join(APP_DATA_DIR, normalized))

    # Ensure path stays within APP_DATA_DIR
    if not abs_path.startswith(APP_DATA_DIR):
        raise HTTPException(status_code=403, detail="Access denied: path outside app data directory")

    return abs_path


@app.post("/api/app-bridge/write")
def app_bridge_write(req: AppBridgeWriteRequest):
    """Write data to app data directory. Used by HTML apps running in editor."""
    temp_path = None
    try:
        abs_path = validate_app_path(req.path)
        target_dir = os.path.dirname(abs_path)
        os.makedirs(target_dir, exist_ok=True)

        # Atomic write: write to temp file in same directory, then replace
        fd = tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=target_dir,
            prefix=f'.{os.path.basename(abs_path)}',
            suffix='.tmp',
            delete=False
        )
        temp_path = fd.name
        try:
            fd.write(req.data)
            fd.flush()
            os.fsync(fd.fileno())
            fd.close()
        except BaseException:
            fd.close()
            raise

        # Atomic rename (same filesystem, so this is guaranteed atomic)
        os.replace(temp_path, abs_path)
        temp_path = None  # Successfully moved, no cleanup needed
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"App bridge write error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp file if it still exists (write or replace failed)
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


@app.get("/api/app-bridge/read", response_class=PlainTextResponse)
def app_bridge_read(path: str):
    """Read data from app data directory. Used by HTML apps running in editor."""
    try:
        abs_path = validate_app_path(path)
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="File not found")
        with open(abs_path, 'r', encoding='utf-8') as f:
            return PlainTextResponse(f.read())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"App bridge read error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- App Bridge v2: askClaude, listFiles, deleteFile ---

class AskClaudeRequest(BaseModel):
    prompt: str
    system_hint: Optional[str] = None  # Optional system context for the request


@app.post("/api/app-bridge/ask-claude")
async def app_bridge_ask_claude(req: AskClaudeRequest):
    """
    Brain Bridge v2: Request-response Codex API for embedded apps.
    Uses the shared Codex CLI backend for consistent auth and infrastructure.
    """
    from codex_backend import CodexRunOptions, run_codex

    try:
        system_prompt = (
            "You are a helpful assistant embedded in a Second Brain app. "
            "Respond concisely and directly. When asked to return structured data (JSON, numbers, lists), "
            "return ONLY the requested format without markdown wrappers or explanations unless asked."
        )
        if req.system_hint:
            system_prompt += f"\n\nApp context: {req.system_hint}"

        result = await run_codex(
            CodexRunOptions(
                model="gpt-5.4-mini",
                cwd=str(ROOT_DIR),
                identity_instructions=system_prompt,
                prompt=req.prompt,
                tools=[],
                timeout_seconds=120,
                max_turns=1,
                ephemeral=True,
            )
        )
        result_text = result.response

        logger.info(f"App Bridge askClaude: prompt={req.prompt[:80]}... response_len={len(result_text)}")
        return {"response": result_text}

    except Exception as e:
        logger.error(f"App Bridge askClaude error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class AskAgentRequest(BaseModel):
    agent: str
    prompt: str


@app.post("/api/app-bridge/ask-agent")
async def app_bridge_ask_agent(req: AskAgentRequest):
    """
    Brain Bridge v2: Route app requests through a named agent.
    Agent runs with full system prompt and tool access.
    """
    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from runner import invoke_agent

    try:
        result = await asyncio.wait_for(
            invoke_agent(name=req.agent, prompt=req.prompt, mode="foreground"),
            timeout=120
        )
        response_text = result.response or result.transcript or ""
        logger.info(f"App Bridge askAgent: agent={req.agent} prompt={req.prompt[:80]}... response_len={len(response_text)}")
        return {"response": response_text}
    except asyncio.TimeoutError:
        logger.warning(f"App Bridge askAgent timed out: agent={req.agent}")
        raise HTTPException(status_code=504, detail=f"Agent '{req.agent}' timed out")
    except Exception as e:
        logger.error(f"App Bridge askAgent error: agent={req.agent} {e}")
        raise HTTPException(status_code=500, detail=str(e))


class InternalAgentInvokeRequest(BaseModel):
    agent: str
    prompt: str
    mode: str = "ping"
    source_chat_id: Optional[str] = None
    model_override: Optional[str] = None
    project: Optional[Any] = None
    project_output_contract: str = "agent_outputs"
    conversation_id: Optional[str] = None
    caller_agent: Optional[str] = None
    worktree_branch: Optional[str] = None
    worktree_slug: Optional[str] = None
    worktree_base_ref: Optional[str] = None
    worktree_path: Optional[str] = None



@app.get("/api/agent-activity")
async def get_agent_activity(upcoming_limit: int = 20):
    """Public read-only agent activity surface for the Settings UI."""
    running_entries = None
    running_error = None
    scheduled_entries = None
    scheduled_error = None

    try:
        # This public endpoint runs in the backend process, so list_all() is the
        # same backend-owned registry exposed internally to restart guards.
        running_entries = await running_agents.list_all()
    except Exception as e:
        logger.warning(f"Failed to read running_agents for UI activity: {e}")
        running_error = str(e)

    try:
        if not scheduler_tool or not hasattr(scheduler_tool, "list_upcoming_runs"):
            scheduled_error = "Scheduled task reader unavailable"
        else:
            limit = max(1, min(int(upcoming_limit), 50))
            scheduled_entries = await asyncio.to_thread(
                scheduler_tool.list_upcoming_runs,
                limit=limit,
                include_inactive=False,
            )
    except Exception as e:
        logger.warning(f"Failed to read upcoming scheduled runs for UI activity: {e}")
        scheduled_error = str(e)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "running_agents": {
            "entries": running_entries,
            "error": running_error,
            "source": "backend",
            "backend_pid": os.getpid(),
        },
        "upcoming_scheduled_runs": {
            "entries": scheduled_entries,
            "error": scheduled_error,
            "source": "scheduler_tool",
        },
    }


@app.get("/api/internal/running-agents")
async def internal_running_agents(request: Request, agent: Optional[str] = None, kind: Optional[str] = None):
    """Read the backend-owned running_agents registry for MCP subprocesses."""
    token = request.headers.get("X-Second-Brain-Internal-Token")
    if not token or token != INTERNAL_AGENT_INVOKE_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    entries = await running_agents.list_all(filter_agent=agent, filter_kind=kind)
    return {"entries": entries, "source": "backend", "backend_pid": os.getpid()}


@app.post("/api/internal/agent-invoke")
async def internal_agent_invoke(req: InternalAgentInvokeRequest, request: Request):
    """Launch ping invocations from the long-lived backend process.

    Codex MCP bridge subprocesses are tied to the caller agent's Codex lifetime;
    if they create detached ping tasks locally, those tasks can be stranded when
    the caller exits. This endpoint lets the bridge hand the launch to the
    backend event loop before returning the ping ack.
    """
    token = request.headers.get("X-Second-Brain-Internal-Token")
    if not token or token != INTERNAL_AGENT_INVOKE_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    if req.mode != "ping":
        raise HTTPException(status_code=400, detail="internal_agent_invoke only supports ping mode")

    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from runner import invoke_agent

    try:
        result = await invoke_agent(
            name=req.agent,
            prompt=req.prompt,
            mode=req.mode,
            source_chat_id=req.source_chat_id,
            model_override=req.model_override,
            project=req.project,
            project_output_contract=req.project_output_contract,
            conversation_id=req.conversation_id,
            caller_agent=req.caller_agent,
            worktree_branch=req.worktree_branch,
            worktree_slug=req.worktree_slug,
            worktree_base_ref=req.worktree_base_ref,
            worktree_path=req.worktree_path,
        )
        if hasattr(result, "__dict__"):
            return result.__dict__
        return result
    except Exception as e:
        logger.error("Internal ping launch failed for agent %s: %s", req.agent, e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class AppBridgeDeleteRequest(BaseModel):
    path: str


@app.get("/api/app-bridge/list")
def app_bridge_list_files(dirPath: str = ""):
    """Brain Bridge v2: List files in an app data subdirectory."""
    try:
        abs_path = validate_app_path(dirPath) if dirPath else APP_DATA_DIR
        if not os.path.isdir(abs_path):
            raise HTTPException(status_code=404, detail="Directory not found")

        files = []
        for entry in sorted(os.listdir(abs_path)):
            entry_path = os.path.join(abs_path, entry)
            rel_path = os.path.relpath(entry_path, APP_DATA_DIR)
            files.append({
                "name": entry,
                "path": rel_path,
                "isDir": os.path.isdir(entry_path),
                "size": os.path.getsize(entry_path) if os.path.isfile(entry_path) else None
            })
        return {"files": files}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"App bridge listFiles error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/app-bridge/stat")
def app_bridge_stat_file(path: str):
    """Brain Bridge v2: Get file mtime and size for change detection."""
    try:
        abs_path = validate_app_path(path)
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="File not found")
        st = os.stat(abs_path)
        return {"mtime": st.st_mtime, "size": st.st_size}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"App bridge stat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/app-bridge/delete")
def app_bridge_delete_file(req: AppBridgeDeleteRequest):
    """Brain Bridge v2: Delete a file within app data directory."""
    try:
        abs_path = validate_app_path(req.path)
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="File not found")
        if os.path.isdir(abs_path):
            raise HTTPException(status_code=400, detail="Cannot delete directories via this endpoint")
        os.remove(abs_path)
        logger.info(f"App bridge deleted: {req.path}")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"App bridge deleteFile error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- App Registry & Icons ---

@app.get("/api/apps")
def get_apps():
    """Return the apps registry from apps.json."""
    apps_json = os.path.join(APP_DATA_DIR, "apps.json")
    if os.path.exists(apps_json):
        with open(apps_json) as f:
            return json.load(f)
    return []


@app.get("/api/app-icon/{path:path}")
def get_app_icon(path: str):
    """Serve app icon images from 05_App_Data/."""
    full_path = validate_app_path(path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Icon not found")
    return FileResponse(full_path)


# --- Plaid Link Integration ---

class PlaidExchangeRequest(BaseModel):
    public_token: str

@app.post("/api/plaid/exchange")
async def plaid_exchange(request: PlaidExchangeRequest):
    """Exchange a Plaid public token for an access token.

    Called by the Plaid Link HTML app after the user completes bank login.
    """
    try:
        scripts_dir = os.path.join(ROOT_DIR, ".claude", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from theo_ports.financial_tools import connect_bank_account
        message, metadata = connect_bank_account(public_token=request.public_token)
        return {
            "success": metadata.get("success", False),
            "message": message,
            "item_id": metadata.get("item_id"),
        }
    except Exception as e:
        logger.error(f"Plaid exchange error: {e}")
        return {"success": False, "error": str(e)}


# --- Agent API ---

@app.get("/api/agents")
def list_agents(all: bool = False):
    """List agents available for chat. Pass all=true to include non-chattable agents."""
    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from registry import get_registry

    registry = get_registry()
    if all:
        agents = list(registry.get_all_configs().values()) + list(registry.get_all_background_configs().values())
    else:
        agents = registry.get_chattable_agents()
    return {"agents": [
        {
            "name": a.name,
            "display_name": a.display_name or " ".join(w.capitalize() for w in a.name.split("_")),
            "description": a.description,
            "model": a.model,
            "is_default": a.default,
            "color": a.color,
            "icon": a.icon,
            "chattable": a.chattable,
            "system_prompt_preset": a.system_prompt_preset,
        }
        for a in agents
    ]}


@app.get("/api/agents/{name}")
def get_agent_detail(name: str):
    """Get full agent detail including raw config and prompt."""
    import yaml as _yaml
    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from registry import get_registry

    registry = get_registry()
    agent = registry.get(name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    # Read raw config.yaml and prompt.md from disk
    agent_dir = agents_dir / name
    if not agent_dir.exists():
        # Check background agents
        agent_dir = agents_dir / "background" / name
    config_yaml = {}
    if (agent_dir / "config.yaml").exists():
        config_yaml = _yaml.safe_load((agent_dir / "config.yaml").read_text()) or {}
    prompt_content = ""
    prompt_path = agent_dir / "prompt.md"
    if prompt_path.exists():
        prompt_content = prompt_path.read_text()

    # Load background processing prompt
    bg_prompt_content = ""
    bg_prompt_path = agent_dir / "background_processing.md"
    if bg_prompt_path.exists():
        bg_prompt_content = bg_prompt_path.read_text()
    else:
        # Fall back to default template
        default_bg_path = agents_dir / "_default" / "background_processing.md"
        if default_bg_path.exists():
            bg_prompt_content = default_bg_path.read_text()

    return {"name": name, "config": config_yaml, "prompt": prompt_content, "background_prompt": bg_prompt_content}


# ---------------------------------------------------------------------------
# Mood selector endpoints
# ---------------------------------------------------------------------------
# Exposes the mood toolkit to the UI so the user can set an agent's mood directly
# when they don't want to decide themselves. Only surfaces for agents that
# already have `mcp__brain__set_mood` in their config `tools` list — the UI
# hides the selector entirely for other agents.

_MOOD_TOOL_NAME = "mcp__brain__set_mood"


def _agent_has_mood_tool(agent_config) -> bool:
    """Check if an agent has the mood tool enabled in its config."""
    return _MOOD_TOOL_NAME in (agent_config.tools or [])


async def broadcast_mood_changed(agent_name: str):
    """Tell every connected client that ``agent_name``'s mood changed.

    Called by both the POST /api/agents/{name}/set-mood endpoint (when the user
    sets via UI) and by the set_mood MCP tool (when the agent sets its own
    mood). The frontend MoodSelector listens for this and refetches.
    """
    try:
        await broadcast_to_all_clients({
            "type": "mood_updated",
            "agent": agent_name,
        })
    except Exception as e:
        logger.warning(f"Failed to broadcast mood update for {agent_name}: {e}")


def _read_mood_description(mood_path: Path) -> str:
    """Extract a short description from a mood .md file.

    Skips leading `# Heading` lines and blank lines, returns the first sentence
    (or up to ~140 chars) of the first body paragraph.
    """
    try:
        text = mood_path.read_text().strip()
    except Exception:
        return ""
    body_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if body_lines:
                break
            continue
        if stripped.startswith("#"):
            continue
        body_lines.append(stripped)
        # Grab enough for a one-line preview then stop
        if sum(len(l) for l in body_lines) > 200:
            break
    preview = " ".join(body_lines).strip()
    # Truncate to first sentence or 140 chars
    cutoff = 140
    if len(preview) > cutoff:
        # Try to cut at sentence boundary
        for marker in [". ", "! ", "? "]:
            idx = preview.rfind(marker, 0, cutoff)
            if idx > 40:
                return preview[: idx + 1]
        return preview[:cutoff].rstrip() + "…"
    return preview


@app.get("/api/agents/{name}/moods")
def get_agent_moods(name: str):
    """Return mood-selector data for an agent.

    Response:
        {
            "enabled": bool,           # whether the mood tool is in the agent's config
            "current": str | None,     # name of active preset if one matches, else "custom" / None
            "current_preview": str,    # short preview of active mood content (empty if none)
            "moods": [
                {"name": "cozy", "description": "Sunday morning energy..."},
                ...
            ]
        }
    """
    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from registry import get_registry

    registry = get_registry()
    agent = registry.get(name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    enabled = _agent_has_mood_tool(agent)
    if not enabled:
        return {"enabled": False, "current": None, "current_preview": "", "moods": []}

    # List preset mood files
    moods_dir = agents_dir / name / "moods"
    moods: List[Dict[str, str]] = []
    if moods_dir.exists():
        for md_file in sorted(moods_dir.glob("*.md")):
            if md_file.name.startswith("_"):
                continue
            moods.append({
                "name": md_file.stem,
                "description": _read_mood_description(md_file),
            })

    # Inspect current mood via working memory
    scripts_dir = Path(ROOT_DIR) / ".claude" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from working_memory import get_store

    store = get_store(agent_name=name)
    current_name: Optional[str] = None
    current_preview = ""
    for item in store.list_items():
        if item.tag == "mood":
            # Try to match content against a preset file
            for m in moods:
                preset_path = moods_dir / f"{m['name']}.md"
                try:
                    if preset_path.read_text().strip() == item.content.strip():
                        current_name = m["name"]
                        break
                except Exception:
                    continue
            if current_name is None:
                current_name = "custom"
            # Short preview for the button label
            first_line = item.content.splitlines()[0].lstrip("# ").strip()
            current_preview = first_line[:60]
            break

    return {
        "enabled": True,
        "current": current_name,
        "current_preview": current_preview,
        "moods": moods,
    }


class SetMoodRequest(BaseModel):
    preset: str  # preset name (e.g. "cozy") or "clear" to remove the active mood


@app.post("/api/agents/{name}/set-mood")
async def set_agent_mood(name: str, body: SetMoodRequest):
    """Set or clear an agent's mood on the user's behalf.

    Writes the mood to the agent's working memory as a pinned entry (same as the
    mood MCP tool) and adds a short TTL=1 attribution note so the agent knows
    the user set it and can still change it. The attribution note auto-expires
    after one exchange.
    """
    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from registry import get_registry

    registry = get_registry()
    agent = registry.get(name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    if not _agent_has_mood_tool(agent):
        raise HTTPException(status_code=400, detail=f"Agent '{name}' does not have the mood tool enabled")

    scripts_dir = Path(ROOT_DIR) / ".claude" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from working_memory import get_store

    store = get_store(agent_name=name)
    preset = (body.preset or "").strip().lower()

    # Clear any existing mood-tagged item (only one mood active at a time).
    def _clear_mood():
        items = store.list_items()
        mood_indices = [i + 1 for i, item in enumerate(items) if item.tag == "mood"]
        for idx in reversed(mood_indices):
            store.remove_item(idx)

    # Clear any stale attribution notes too — don't stack them.
    def _clear_attribution():
        items = store.list_items()
        attr_indices = [i + 1 for i, item in enumerate(items) if item.tag == "mood-set-by-zeke"]
        for idx in reversed(attr_indices):
            store.remove_item(idx)

    _clear_attribution()

    if preset in ("clear", "neutral", ""):
        _clear_mood()
        # Attribution note so the agent knows the user intentionally cleared it
        try:
            store.add_item(
                content="the user just cleared your mood via the UI — back to baseline. Change it anytime with set_mood if something fits better.",
                tag="mood-set-by-zeke",
                ttl=1,
                pinned=False,
            )
        except Exception as e:
            logger.warning(f"Could not add attribution note: {e}")
        await broadcast_mood_changed(name)
        return {"ok": True, "action": "cleared"}

    # Apply preset
    moods_dir = agents_dir / name / "moods"
    preset_file = moods_dir / f"{preset}.md"
    if not preset_file.exists():
        raise HTTPException(status_code=404, detail=f"Mood preset '{preset}' not found for agent '{name}'")

    try:
        mood_content = preset_file.read_text().strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read mood file: {e}")

    _clear_mood()
    try:
        store.add_item(
            content=mood_content,
            tag="mood",
            pinned=True,
            pin_rank=1,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not set mood: {e}")

    # TTL=1 attribution — decays after a single exchange so it doesn't clutter.
    try:
        store.add_item(
            content=f"the user set your mood to **{preset}** via the UI just now — change it anytime with set_mood if it doesn't fit.",
            tag="mood-set-by-zeke",
            ttl=1,
            pinned=False,
        )
    except Exception as e:
        logger.warning(f"Could not add attribution note: {e}")

    await broadcast_mood_changed(name)
    return {"ok": True, "action": "set", "preset": preset}


@app.get("/api/native-tools")
def list_native_tools():
    """List Codex-native tool labels exposed to agents (Agent Builder source of truth).

    Reads from .claude/agents/native_tools.py — the single place to edit when a
    new Codex capability or mapped compatibility label should become available
    in the builder. Frontend falls back to a local copy if this endpoint fails.
    """
    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    try:
        from native_tools import NATIVE_TOOL_GROUPS
        return {"groups": NATIVE_TOOL_GROUPS}
    except Exception as e:
        logger.error(f"Failed to load native tools: {e}")
        return {"groups": []}


@app.get("/api/system-models")
def get_system_models():
    """Return the current system_models config (convener, salon_titler, chat_titler).

    Always includes all known system models — missing keys fall back to
    defaults defined in `interface/server/system_models.py`.
    """
    try:
        import system_models
        return {"system_models": system_models.load()}
    except Exception as e:
        logger.error(f"Failed to load system_models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class _SystemModelsReq(BaseModel):
    system_models: Dict[str, Dict[str, Any]]


@app.post("/api/system-models")
def save_system_models(req: _SystemModelsReq):
    """Save the system_models config. Validates + atomic-writes to JSON."""
    try:
        import system_models as _sm
        merged = _sm.save(req.system_models or {})
        return {"system_models": merged, "ok": True}
    except Exception as e:
        logger.error(f"Failed to save system_models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tools/categories")
def list_tool_categories():
    """List available MCP tool categories for the Agent Builder."""
    try:
        from mcp_tools.constants import TOOL_CATEGORIES
        # Hide internal categories and parent "memory" (subcategories cover it fully)
        hidden = {"gardener", "memory"}
        return {"categories": [
            {"name": cat, "tools": tools}
            for cat, tools in TOOL_CATEGORIES.items()
            if cat not in hidden
        ]}
    except ImportError:
        return {"categories": []}


@app.get("/api/skills")
def list_skills():
    """List all available skills for the Agent Builder skill selector."""
    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    try:
        from skill_injector import get_registry
        registry = get_registry()
        return {"skills": [
            {"name": entry.name, "description": entry.description}
            for entry in sorted(registry.values(), key=lambda e: e.name)
        ]}
    except Exception as e:
        logger.error(f"Failed to list skills: {e}")
        return {"skills": []}


class AgentCreateRequest(BaseModel):
    name: str
    config: dict
    prompt: str = ""
    background_prompt: str = ""


@app.post("/api/agents")
def create_agent(req: AgentCreateRequest):
    """Create a new agent from the Agent Builder."""
    import yaml as _yaml
    import re as _re

    # Validate name
    name = req.name.strip().lower()
    if not _re.match(r'^[a-z][a-z0-9_]*$', name):
        raise HTTPException(status_code=400, detail="Name must be lowercase letters, numbers, and underscores, starting with a letter")
    reserved = {"character", "background", "_template", "notifications", "__pycache__"}
    if name in reserved:
        raise HTTPException(status_code=400, detail=f"Name '{name}' is reserved")

    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    agent_dir = agents_dir / name
    if agent_dir.exists():
        raise HTTPException(status_code=409, detail=f"Agent '{name}' already exists")

    # Create directory and files
    agent_dir.mkdir(parents=True)
    config = req.config.copy()
    config["name"] = name
    (agent_dir / "config.yaml").write_text(_yaml.dump(config, default_flow_style=False, sort_keys=False))
    (agent_dir / "prompt.md").write_text(req.prompt)

    # Save background processing prompt if provided (and different from default)
    if req.background_prompt.strip():
        default_bg_path = agents_dir / "_default" / "background_processing.md"
        default_bg = default_bg_path.read_text() if default_bg_path.exists() else ""
        if req.background_prompt.strip() != default_bg.strip():
            (agent_dir / "background_processing.md").write_text(req.background_prompt)

    # Reload registry
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from registry import get_registry
    get_registry().reload()

    return {"status": "created", "name": name, "restart_required": False}


@app.put("/api/agents/{name}")
def update_agent(name: str, req: AgentCreateRequest):
    """Update an existing agent."""
    import yaml as _yaml

    agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
    agent_dir = agents_dir / name
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    config = req.config.copy()
    config["name"] = name
    (agent_dir / "config.yaml").write_text(_yaml.dump(config, default_flow_style=False, sort_keys=False))
    (agent_dir / "prompt.md").write_text(req.prompt)

    # Save background processing prompt
    if req.background_prompt is not None:
        bg_prompt_text = req.background_prompt.strip()
        bg_prompt_path = agent_dir / "background_processing.md"
        default_bg_path = agents_dir / "_default" / "background_processing.md"
        default_bg = default_bg_path.read_text().strip() if default_bg_path.exists() else ""

        if bg_prompt_text and bg_prompt_text != default_bg:
            # Custom prompt — save it
            bg_prompt_path.write_text(req.background_prompt)
        elif bg_prompt_path.exists() and (not bg_prompt_text or bg_prompt_text == default_bg):
            # Reverted to default or cleared — remove custom file so default is used
            bg_prompt_path.unlink()

    # Reload registry
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from registry import get_registry
    get_registry().reload()

    return {"status": "updated", "name": name}


# --- Chat API ---

@app.get("/api/chat/history")
def list_chat_history(include_system: bool = False):
    """List chat history. System chats (scheduled tasks, automations) hidden by default."""
    chats = chat_manager.list_chats()
    if not include_system:
        chats = [c for c in chats if not c.get("is_system", False)]
    return {"chats": chats}


@app.get("/api/chat/history/{session_id}")
def get_chat_history(session_id: str):
    data = chat_manager.load_chat(session_id)
    if data is None:
        raise HTTPException(status_code=404)

    # REST callers are UI-facing too. Return a normalized display_messages
    # array so stale/truncated on-disk display snapshots cannot hide visible
    # flat messages during fallback loads or restart continuation recovery.
    response_data = dict(data)
    response_data["display_messages"] = _messages_for_display(data, session_id)
    response_data["helper_settings"] = _normalize_helper_settings(data.get("helper_settings"))
    return response_data


@app.post("/api/chat/history/{session_id}")
def save_chat_history(session_id: str, data: dict):
    chat_manager.save_chat(session_id, data)
    return {"status": "ok"}


@app.delete("/api/chat/history/{session_id}")
def delete_chat_history(session_id: str):
    if chat_manager.delete_chat(session_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Chat not found")


@app.patch("/api/chat/history/{session_id}")
def update_chat(session_id: str, req: ChatUpdateRequest):
    """Update chat title or messages."""
    existing = chat_manager.load_chat(session_id)
    if existing is None:
        raise HTTPException(status_code=404)

    if req.title is not None:
        existing["title"] = req.title
    if req.messages is not None:
        existing["messages"] = req.messages
    if req.helper_settings is not None:
        existing["helper_settings"] = _merge_helper_settings(existing.get("helper_settings"), req.helper_settings)

    chat_manager.save_chat(session_id, existing)
    return {"status": "ok"}


# --- Room API (Room = Chat conversation with metadata) ---

class RoomMetaResponse(BaseModel):
    room: str
    title: Optional[str] = None
    updated_at: Optional[float] = None
    room_type: str = "standard"


class ActiveRoomPayload(BaseModel):
    room: Optional[str] = None
    context: Optional[str] = None


class SetActiveRoomRequest(BaseModel):
    room_id: Optional[str] = None
    name: Optional[str] = None  # Alias for room_id (Theo compatibility)
    context: Optional[str] = None


@app.get("/api/rooms")
def list_rooms(include_system: bool = False):
    """List all rooms with metadata, sorted by most recent.

    Returns both the room list and metadata dict for fast frontend rendering.
    """
    # Get list from chat_manager (existing chat files)
    chats = chat_manager.list_chats()
    if not include_system:
        chats = [c for c in chats if not c.get("is_system", False)]

    # Get metadata from rooms_meta (if available)
    meta = {}
    if rooms_meta:
        meta = rooms_meta.load()

    # Build rooms list with IDs
    rooms = [c.get("id") for c in chats if c.get("id")]

    return {"rooms": rooms, "meta": meta, "chats": chats}


@app.get("/api/rooms/active")
def get_active_room_endpoint():
    """Get the currently active room."""
    if active_room:
        payload = active_room.get_active_payload()
        return {
            "room": payload.get("room"),
            "context": payload.get("context"),
            "updated_at": payload.get("updated_at")
        }
    return {"room": None, "context": None, "updated_at": None}


@app.post("/api/rooms/active")
def set_active_room_endpoint(req: SetActiveRoomRequest):
    """Set the active room/context."""
    if not active_room:
        raise HTTPException(status_code=500, detail="Active room module not available")

    room_id = req.room_id or req.name
    context = req.context

    # Handle context-only updates
    if context and not room_id:
        ok, msg = active_room.set_active_context(context)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return {"ok": True, "context": context}

    # Require room_id for room updates
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id or name is required")

    ok, msg = active_room.set_active_room(room_id, context=context)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    return {"ok": True, "room": room_id, "context": context}


@app.get("/api/rooms/{room_id}/meta")
def get_room_meta(room_id: str):
    """Get metadata for a specific room."""
    if rooms_meta:
        meta = rooms_meta.get_room_meta(room_id)
        return {"room": room_id, **meta}
    return {"room": room_id, "title": None, "updated_at": None, "room_type": "standard"}


@app.post("/api/rooms/{room_id}/title")
def set_room_title(room_id: str, payload: dict):
    """Set the title for a room."""
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    # Update rooms_meta
    if rooms_meta:
        rooms_meta.set_title(room_id, title)

    # Also update the chat file
    existing = chat_manager.load_chat(room_id)
    if existing:
        existing["title"] = title
        chat_manager.save_chat(room_id, existing)

    return {"ok": True}


@app.get("/api/rooms/{room_id}/history")
def get_room_history(room_id: str):
    """Get message history for a room.

    This is an alias for /api/chat/history/{session_id} for Theo compatibility.
    """
    data = chat_manager.load_chat(room_id)
    if data is None:
        raise HTTPException(status_code=404)
    return data


# ---------------------------------------------------------------------------
# Salon (group chat) HTTP API
# ---------------------------------------------------------------------------
#
# Salons are persistent N-way conversations between the user and multiple agents,
# with routing handled by the silent Convener. See salon_manager.py and
# salon_dispatcher.py for the runtime; these endpoints are the the user-side
# surface (UI). Agents have their own MCP tools (mcp_tools/salons/).


class _CreateSalonReq(BaseModel):
    title: Optional[str] = ""  # empty = let salon_titler auto-name after first exchange
    participants: List[str]
    opening_message: Optional[str] = None


class _PostToSalonReq(BaseModel):
    content: str


class _AddSalonParticipantReq(BaseModel):
    participant: str


class _PromoteChatReq(BaseModel):
    chat_id: str
    title: Optional[str] = None
    participant: str  # agent to add (the one being added to make it multi-party)


@app.get("/api/salons")
def list_salons_endpoint(participant: Optional[str] = None, limit: int = 100):
    """List salons. By default returns all salons in the system.

    Pass ?participant=zeke to filter to a specific participant's salons.
    """
    mgr = _salon_manager_mod.get_manager()
    if participant:
        return {"salons": mgr.list_for_participant(participant, limit=limit)}
    return {"salons": mgr.list_all(limit=limit)}


@app.get("/api/salons/{salon_id}")
def get_salon_endpoint(salon_id: str):
    """Return the full salon JSON (messages, participants, hints, state)."""
    mgr = _salon_manager_mod.get_manager()
    data = mgr.load(salon_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Salon not found")
    return data


@app.post("/api/salons")
async def create_salon_endpoint(req: _CreateSalonReq):
    """Create a new salon (the user-side). Creator is recorded as 'user'."""
    title = (req.title or "").strip() or "(untitled salon)"
    participants = [p.strip() for p in (req.participants or []) if p.strip()]
    if not participants:
        raise HTTPException(status_code=400, detail="participants is required")

    mgr = _salon_manager_mod.get_manager()
    salon_id = mgr.create(
        title=title,
        participants=participants,
        creator="user",
        opening_message=(req.opening_message or "").strip() or None,
    )

    # Fire dispatcher (it'll broadcast salon_created and run the convener)
    try:
        from salon_events import publish
        publish("salon_created", {
            "salon_id": salon_id,
            "title": title,
            "participants": participants,
            "creator": "user",
            "had_opening_message": bool(req.opening_message),
        })
        if req.opening_message:
            # The opening message is from "user" — the convener should pick
            # this up via salon_created → dispatch_salon_loop. No separate
            # message_posted needed.
            pass
    except Exception as e:
        logger.warning(f"Salon create: event publish failed: {e}")

    return {"salon_id": salon_id, "ok": True}


@app.post("/api/salons/{salon_id}/messages")
async def post_to_salon_endpoint(salon_id: str, req: _PostToSalonReq):
    """the user posts to a salon. Triggers the convener loop."""
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    mgr = _salon_manager_mod.get_manager()
    salon = mgr.load(salon_id)
    if salon is None:
        raise HTTPException(status_code=404, detail="Salon not found")

    # Make sure zeke is a participant — otherwise the agents don't know
    # he can post here. (UI promotion path adds him explicitly; this is a
    # safety net.)
    if "user" not in (salon.get("participants") or []):
        mgr.add_participant(salon_id, "user")

    msg_id = mgr.append_message(
        salon_id=salon_id,
        from_participant="user",
        content=content,
    )

    try:
        from salon_events import publish
        publish("salon_message_posted", {
            "salon_id": salon_id,
            "message_id": msg_id,
            "from": "user",
        })
    except Exception as e:
        logger.warning(f"Salon post: event publish failed: {e}")

    return {"message_id": msg_id, "ok": True}


@app.post("/api/salons/{salon_id}/participants")
async def add_salon_participant_endpoint(salon_id: str, req: _AddSalonParticipantReq):
    """Add a participant (agent name or 'user') to an existing salon."""
    participant = (req.participant or "").strip()
    if not participant:
        raise HTTPException(status_code=400, detail="participant is required")

    mgr = _salon_manager_mod.get_manager()
    if not mgr.exists(salon_id):
        raise HTTPException(status_code=404, detail="Salon not found")

    added = mgr.add_participant(salon_id, participant)
    if added:
        try:
            from salon_events import publish
            publish("salon_participant_added", {
                "salon_id": salon_id,
                "added_by": "user",
                "participant": participant,
            })
        except Exception as e:
            logger.warning(f"Salon add participant: event publish failed: {e}")

    return {"added": added, "ok": True}


@app.post("/api/salons/{salon_id}/title")
def set_salon_title_endpoint(salon_id: str, payload: dict):
    """Update a salon's title."""
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    mgr = _salon_manager_mod.get_manager()
    if not mgr.update_title(salon_id, title):
        raise HTTPException(status_code=404, detail="Salon not found")
    return {"ok": True}


@app.delete("/api/salons/{salon_id}")
def delete_salon_endpoint(salon_id: str):
    """Delete a salon. No participant check."""
    mgr = _salon_manager_mod.get_manager()
    if not mgr.delete(salon_id):
        raise HTTPException(status_code=404, detail="Salon not found")
    return {"ok": True}


@app.post("/api/salons/promote-chat")
async def promote_chat_to_salon_endpoint(req: _PromoteChatReq):
    """Promote a 1:1 chat to a salon by adding a second agent.

    Loads the chat's message history, copies it into a new salon (rendered
    as 'from' messages: user → 'user', assistant → original agent), adds
    the new participant, and fires the convener loop.
    """
    chat_id = (req.chat_id or "").strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    new_participant = (req.participant or "").strip()
    if not new_participant:
        raise HTTPException(status_code=400, detail="participant is required")

    chat = chat_manager.load_chat(chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Determine the original agent. Look at the chat's `agent` field, falling
    # back to the assistant role on the first assistant message.
    chat_agent = (chat.get("agent") or "").strip() or None
    if not chat_agent:
        for msg in chat.get("messages") or []:
            if msg.get("role") == "assistant":
                chat_agent = msg.get("agent") or None
                if chat_agent:
                    break
    if not chat_agent:
        raise HTTPException(
            status_code=400,
            detail="Could not determine the chat's agent — chat has no agent field",
        )

    title = (req.title or chat.get("title") or "(promoted chat)").strip()

    participants = ["user", chat_agent]
    if new_participant not in participants:
        participants.append(new_participant)

    mgr = _salon_manager_mod.get_manager()
    salon_id = mgr.create(
        title=title,
        participants=participants,
        creator="user",
        opening_message=None,
    )

    # Copy chat messages → salon messages with role-translation.
    for msg in chat.get("messages") or []:
        role = msg.get("role")
        content = msg.get("content")
        if not content or not isinstance(content, str) or not content.strip():
            continue
        if role == "user":
            from_participant = "user"
        elif role == "assistant":
            from_participant = msg.get("agent") or chat_agent
        else:
            # system / tool / etc. — skip for now
            continue
        try:
            mgr.append_message(
                salon_id=salon_id,
                from_participant=from_participant,
                content=content.strip(),
            )
        except Exception as e:
            logger.warning(f"Promote chat: failed to copy message: {e}")

    # Fire the dispatcher to bring the new participant in.
    try:
        from salon_events import publish
        publish("salon_created", {
            "salon_id": salon_id,
            "title": title,
            "participants": participants,
            "creator": "user",
            "had_opening_message": False,
        })
    except Exception as e:
        logger.warning(f"Salon promote: event publish failed: {e}")

    return {"salon_id": salon_id, "ok": True}


# --- Chat Search API ---

# Initialize chat search using Qwen3 embedding index (lazy loaded)
_chat_index = None

def _get_chat_index():
    """Get or create the Qwen3 chat embedding index."""
    global _chat_index
    if _chat_index is None:
        import sys
        scripts_dir = os.path.join(ROOT_DIR, ".claude", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from contextual_memory.chat_embedding_index import load_index
        _chat_index = load_index()
        if _chat_index is None:
            logger.warning("Chat embedding index not found — search will return empty results")
    return _chat_index


def _highlight_matches(text: str, query: str, max_length: int = 200) -> str:
    """Create a preview with highlighted search terms using <mark> tags."""
    import html as html_mod
    import re as re_mod

    # Tokenize query (simple word extraction)
    query_tokens = set(re_mod.findall(r'[a-z0-9_]+', query.lower()))
    if not query_tokens:
        escaped = html_mod.escape(text[:max_length])
        return escaped + ("..." if len(text) > max_length else "")

    # Find the best snippet containing query terms
    words = text.split()
    best_start = 0
    best_score = 0

    for i in range(len(words)):
        window = words[i:i + 30]
        window_text = " ".join(window).lower()
        score = sum(1 for t in query_tokens if t in window_text)
        if score > best_score:
            best_score = score
            best_start = i

    # Extract snippet
    snippet_words = words[best_start:best_start + 30]
    snippet = " ".join(snippet_words)

    if len(snippet) > max_length:
        snippet = snippet[:max_length].rsplit(" ", 1)[0] + "..."

    # Escape HTML first, then wrap matches in <mark> tags
    snippet = html_mod.escape(snippet)
    for token in query_tokens:
        escaped_token = html_mod.escape(token)
        pattern = re_mod.compile(f'({re_mod.escape(escaped_token)})', re_mod.IGNORECASE)
        snippet = pattern.sub(r'<mark>\1</mark>', snippet)

    return snippet


class ChatSearchResult(BaseModel):
    message_id: str
    chat_id: str
    chat_title: str
    role: str
    content_preview: str
    timestamp: float
    score: float
    match_type: str


class ChatSearchResponse(BaseModel):
    results: List[ChatSearchResult]
    total_count: int
    semantic_pending: bool
    query_time_ms: float


@app.get("/api/chat/search", response_model=ChatSearchResponse)
def search_chats(
    q: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    roles: Optional[str] = None,
    exclude_system: bool = True,
    semantic_only: bool = False,
    limit: int = 20
):
    """
    Search chat history with keyword and/or semantic search.
    Uses the Qwen3 embedding index for both keyword and semantic search.
    """
    import time as time_mod
    start_time = time_mod.time()

    index = _get_chat_index()
    if index is None:
        return ChatSearchResponse(results=[], total_count=0, semantic_pending=False, query_time_ms=0)

    from contextual_memory.chat_embedding_index import keyword_search, search as semantic_search

    # Build date range filter
    date_range = None
    if date_from or date_to:
        from datetime import datetime as dt
        date_range = {}
        if date_from:
            date_range["start"] = dt.fromisoformat(date_from).timestamp()
        if date_to:
            date_range["end"] = dt.fromisoformat(date_to).timestamp()

    # Role filtering: we'll filter results after the search since the Qwen3 index
    # doesn't have a built-in role filter
    role_set = set(roles.split(",")) if roles else None

    if semantic_only:
        # Phase 2: Semantic search using Qwen3 embeddings
        # This requires the embedding model to be loaded — may fail on first call
        try:
            raw_results = semantic_search(index, q, k=limit * 2, date_range=date_range)
            match_type = "semantic"
        except Exception as e:
            logger.warning(f"Semantic search failed (model may not be loaded): {e}")
            return ChatSearchResponse(results=[], total_count=0, semantic_pending=False, query_time_ms=0)
    else:
        # Phase 1: Fast keyword search (no model needed)
        raw_results = keyword_search(index, q, k=limit * 2, date_range=date_range)
        match_type = "keyword"

    # Convert to API response format
    results = []
    for meta, score in raw_results:
        # Role filter
        if role_set and meta.role not in role_set:
            continue

        # Exclude system/scheduled chats (agent-only chats without user messages)
        # We keep all results for now since the index already filters short messages

        # Get full text for highlighting if available
        if index.texts and meta.doc_idx < len(index.texts):
            full_text = index.texts[meta.doc_idx]
        else:
            full_text = meta.content_preview

        preview = _highlight_matches(full_text, q)

        results.append(ChatSearchResult(
            message_id=meta.message_id,
            chat_id=meta.chat_id,
            chat_title=meta.chat_title,
            role=meta.role,
            content_preview=preview,
            timestamp=meta.timestamp or 0.0,
            score=score,
            match_type=match_type,
        ))

        if len(results) >= limit:
            break

    query_time = (time_mod.time() - start_time) * 1000

    return ChatSearchResponse(
        results=results,
        total_count=len(results),
        semantic_pending=not semantic_only,  # Semantic still pending if this was keyword-only
        query_time_ms=query_time,
    )


@app.post("/api/chat/search/refresh")
def refresh_search_index():
    """Reload the chat embedding index from disk."""
    global _chat_index
    from contextual_memory.chat_embedding_index import load_index
    _chat_index = load_index()
    msg_count = len(_chat_index.metadata) if _chat_index else 0
    return {"status": "ok", "message": f"Index reloaded: {msg_count} messages"}




# --- Push Notifications ---

from push_service import (
    get_vapid_public_key,
    add_subscription,
    remove_subscription,
    PushSubscription,
    send_push_notification
)
from datetime import datetime as dt


class PushSubscriptionRequest(BaseModel):
    endpoint: str
    keys: dict


@app.get("/api/push/vapid-public-key")
def get_push_vapid_key():
    """Get VAPID public key for push subscription."""
    public_key = get_vapid_public_key()
    if not public_key:
        raise HTTPException(status_code=500, detail="VAPID keys not configured")
    return {"publicKey": public_key}


@app.post("/api/push/subscribe")
def subscribe_push(request: PushSubscriptionRequest):
    """Register a push subscription."""
    subscription = PushSubscription(
        endpoint=request.endpoint,
        keys=request.keys,
        created_at=dt.now().isoformat()
    )
    success = add_subscription(subscription)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save subscription")
    logger.info(f"Push subscription added: {request.endpoint[:50]}...")
    return {"status": "ok"}


@app.post("/api/push/unsubscribe")
def unsubscribe_push(request: PushSubscriptionRequest):
    """Remove a push subscription."""
    success = remove_subscription(request.endpoint)
    logger.info(f"Push subscription removed: {request.endpoint[:50]}...")
    return {"status": "ok", "removed": success}


# --- Chess Game API ---

class ChessMoveRequest(BaseModel):
    move: str  # Move in UCI format (e.g., "e2e4") or SAN (e.g., "e4")


@app.get("/api/chess/game")
def get_chess_game():
    """Get current chess game state."""
    from mcp_tools.chess.chess import get_current_game
    game = get_current_game()
    if not game:
        return {"active": False}
    return {"active": True, "game": game}


@app.post("/api/chess/move")
async def make_chess_move(request: ChessMoveRequest):
    """Make a move for the user (the user)."""
    from mcp_tools.chess.chess import make_user_move

    result = make_user_move(request.move)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    game_state = result.get("game_state", {})

    # Broadcast to all connected clients
    await broadcast_to_all_clients({
        "type": "chess_update",
        "game": game_state
    })

    # Return context for Claude if it's now Claude's turn
    response = {
        "success": True,
        "game": game_state,
        "status": result.get("status", {})
    }

    # If it's Claude's turn and game isn't over, include a prompt
    if not game_state.get("game_over"):
        fen_parts = game_state.get("fen", "").split()
        current_turn = "white" if len(fen_parts) > 1 and "w" in fen_parts[1] else "black"
        if current_turn == game_state.get("claude_color"):
            response["claude_prompt"] = f"Current position: {game_state['fen']}. Your turn."

    return response


@app.delete("/api/chess/game")
def cancel_chess_game():
    """Cancel the current chess game."""
    from mcp_tools.chess.chess import delete_game
    delete_game()
    return {"status": "ok"}


# --- WebSocket Chat ---

# Track connected clients with their visibility state
client_sessions: Dict[WebSocket, ClientSession] = {}
active_conversations: Dict[str, ConversationState] = {}
# Track all currently processing sessions (supports concurrent chats).
# Maps chat_id -> running_agents entry_id. Phase 2 migration: the value used
# to be the start-time float; it's now the running_agents handle so that
# membership / iteration semantics (the only thing every reader uses) keep
# working while the same in-flight set is also visible via running_agents().
active_processing_sessions: Dict[str, str] = {}


async def _record_chat_session_started(
    state_key: str,
    agent_name: Optional[str],
    first_message: str,
) -> str:
    """Register a chat-WS session with running_agents and remember the entry id
    under ``state_key`` in ``active_processing_sessions``. Returns the entry id
    so the caller can hand it on if needed."""
    entry_id = await running_agents.register(
        agent=agent_name or "character",
        kind="chat",
        task_summary=first_message or "",
        source_chat_id=state_key,
    )
    active_processing_sessions[state_key] = entry_id
    return entry_id


async def _record_chat_session_ended(state_key: str) -> None:
    """Tear down both halves: pop from ``active_processing_sessions`` and
    unregister the running_agents entry. Idempotent — if ``state_key`` is not
    present, no-op."""
    entry_id = active_processing_sessions.pop(state_key, None)
    if entry_id:
        await running_agents.unregister(entry_id)


async def _record_chat_session_rekey(old_key: str, new_key: str) -> None:
    """Mid-flight state-key swap (used when a "new" chat gets its real id, or
    when a session migrates onto its preserved chat id). Keeps the same
    running_agents entry but moves the dict key and refreshes ``source_chat_id``
    on the entry so the registry view matches the new key."""
    entry_id = active_processing_sessions.pop(old_key, None)
    if entry_id is None:
        return
    active_processing_sessions[new_key] = entry_id
    await running_agents.update(entry_id, source_chat_id=new_key)

# --- Session-Scoped Client Registry (for broadcast) ---
# Track all WebSocket connections per session for multi-device sync
session_clients: Dict[str, Set[WebSocket]] = defaultdict(set)


def _find_chat_with_message(msg_id: str) -> Optional[str]:
    """
    Search recent chats for a message with this ID.

    This is used to prevent duplicate chat file creation when:
    - Client reconnects with stale session ID
    - Message was already saved but client didn't get session_init

    Returns the chat file's session ID if found, None otherwise.
    """
    if not msg_id:
        return None

    chats_dir = Path(ROOT_DIR) / ".claude" / "chats"
    if not chats_dir.exists():
        return None

    # Search only the 20 most recent chats for performance
    try:
        chat_files = sorted(
            chats_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )[:20]
    except Exception:
        return None

    for chat_file in chat_files:
        try:
            data = json.loads(chat_file.read_text())
            for msg in data.get("messages", []):
                if msg.get("id") == msg_id:
                    return chat_file.stem  # Return filename without .json
        except Exception:
            continue

    return None


async def broadcast_to_session(session_id: str, message: dict):
    """Broadcast a message to ALL clients viewing this session.

    This is the core of the backend-authoritative architecture:
    when the server has new state, it pushes to all connected clients.

    Uses a snapshot (copy) of the client set to avoid 'Set changed size
    during iteration' errors when a WebSocket disconnects mid-broadcast.
    """
    if not session_id:
        return

    clients = session_clients.get(session_id)
    if not clients:
        return

    # Inject sessionId so clients can filter by chat (multi-chat concurrent streaming)
    if "sessionId" not in message:
        message = {**message, "sessionId": session_id}

    # Auto-inject turnId from streaming state so clients can detect stale events
    # from cancelled edits/regenerates. This covers ALL broadcast events automatically.
    if "turnId" not in message:
        ss = session_streaming_states.get(session_id)
        if ss and ss.turn_id:
            message = {**message, "turnId": ss.turn_id}

    dead = set()
    for ws in list(clients):  # snapshot to avoid concurrent modification
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)

    # Clean up dead connections
    if dead:
        session_clients[session_id] -= dead
        # Also clean up from client_sessions
        for ws in dead:
            client_sessions.pop(ws, None)


async def broadcast_to_all_clients(message: dict):
    """Broadcast a message to ALL connected WebSocket clients with dead connection cleanup.

    Collects dead connections during iteration, then removes them from both
    client_sessions and session_clients dictionaries to prevent memory leaks.
    """
    dead = set()
    for ws in list(client_sessions):
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)

    # Clean up dead connections from both tracking dicts
    if dead:
        for ws in dead:
            client_sessions.pop(ws, None)
        for sid, clients in list(session_clients.items()):
            clients -= dead
            if not clients:
                del session_clients[sid]


async def broadcast_chat_created(chat_id: str, title: str, agent: str = None,
                                  is_system: bool = False, scheduled: bool = False):
    """Broadcast chat_created to ALL connected clients for history list updates."""
    await broadcast_to_all_clients({
        "type": "chat_created",
        "chat": {"id": chat_id, "title": title, "updated": time.time(),
                 "is_system": is_system, "scheduled": scheduled, "agent": agent}
    })


async def _dispatch_mention_agents(
    session_id: str,
    chat_id: str,
    agent_names: List[str],
    context_messages: List[dict],
    trigger_text: str,
    trigger_role: str,
    primary_agent: str,
):
    """
    Dispatch @mentioned agents in parallel. Their responses appear directly
    in the chat timeline — like a group chat, not funneled through the primary agent.

    Each agent runs independently. Messages are appended in completion order.
    Max 3 agents per invocation. 4-hour timeout per agent.
    """
    async def _run_single_mention(agent_name: str):
        # Broadcast typing indicator
        await broadcast_to_session(session_id, {
            "type": "agent_typing",
            "agent": agent_name,
            "sessionId": session_id
        })

        # Build context from recent messages
        context_parts = []
        for msg in context_messages[-10:]:
            role = msg.get("role", "user")
            agent = msg.get("agent")
            content = msg.get("content", "")
            if not content or not content.strip():
                continue
            if agent:
                context_parts.append(f"[@{agent}]: {content}")
            elif role == "user":
                context_parts.append(f"[the user]: {content}")
            elif role == "assistant":
                context_parts.append(f"[{primary_agent}]: {content}")

        context_str = "\n".join(context_parts[-15:])  # Last 15 non-empty messages

        prompt = f"""You were @mentioned in a conversation between the user and {primary_agent}.
Here's the recent context:

{context_str}

Your response will appear directly in the chat timeline as a message from you.
Be conversational and concise — this is a group chat, not a report.
Use tools only if the request clearly requires action (e.g., "fix this bug", "look this up")."""

        # Import and run the agent
        try:
            agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
            if str(agents_dir) not in sys.path:
                sys.path.insert(0, str(agents_dir))
            from runner import invoke_agent

            try:
                result = await asyncio.wait_for(
                    invoke_agent(name=agent_name, prompt=prompt, mode="foreground", is_visible=True),
                    timeout=14400
                )
                response_text = result.response or result.transcript or ""
                if not response_text.strip():
                    response_text = f"*@{agent_name} had nothing to say*"
            except asyncio.TimeoutError:
                response_text = f"*@{agent_name} timed out after 4 hours*"
                logger.warning(f"Mention agent {agent_name} timed out")
            except Exception as e:
                response_text = f"*@{agent_name} encountered an error*"
                logger.error(f"Mention agent {agent_name} failed: {e}", exc_info=True)
        except Exception as e:
            response_text = f"*@{agent_name} could not be loaded*"
            logger.error(f"Failed to import agent runner for mention: {e}")

        # Guard: verify chat still exists before persisting
        if chat_id not in active_conversations and not chat_manager.load_chat(chat_id):
            logger.warning(f"Mention agent {agent_name}: chat {chat_id} gone, discarding result")
            return

        # Build the agent message
        agent_msg_id = str(uuid.uuid4())
        agent_msg = {
            "id": agent_msg_id,
            "role": "assistant",
            "agent": agent_name,
            "content": response_text,
            "timestamp": time.time(),
            "status": "complete"
        }

        # Persist under lock to prevent race conditions
        async with get_chat_lock(chat_id):
            conv = active_conversations.get(chat_id)
            if conv:
                conv.messages.append(agent_msg)

            # Also update the saved chat on disk
            existing = chat_manager.load_chat(chat_id)
            if existing:
                existing["messages"] = conv.messages if conv else existing.get("messages", []) + [agent_msg]
                # Also append to display_messages if present
                display_msg_entry = {
                    "id": agent_msg_id,
                    "role": "assistant",
                    "agent": agent_name,
                    "content": response_text,
                    "status": "complete",
                    "blocks": [{
                        "id": str(uuid.uuid4()),
                        "type": "text",
                        "content": response_text,
                        "status": "complete"
                    }]
                }
                if "display_messages" in existing:
                    existing["display_messages"].append(display_msg_entry)
                chat_manager.save_chat(chat_id, existing)

        # Broadcast the agent message to all clients viewing this session
        display_msg = {
            "id": agent_msg_id,
            "role": "assistant",
            "agent": agent_name,
            "blocks": [{
                "id": str(uuid.uuid4()),
                "type": "text",
                "content": response_text,
                "status": "complete"
            }],
            "status": "complete"
        }
        await broadcast_to_session(session_id, {
            "type": "agent_message",
            "sessionId": session_id,
            "message": display_msg
        })
        logger.info(f"Mention agent @{agent_name} responded in chat {chat_id} ({len(response_text)} chars)")

    # Run all mentioned agents in parallel (capped at 3)
    tasks = [asyncio.create_task(_run_single_mention(name)) for name in agent_names[:3]]
    # Don't await here — let them run independently. Errors are handled inside each task.
    for task in tasks:
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() and t.exception() else None)


def _get_valid_agent_names() -> set:
    """Get the set of valid agent names from the registry."""
    try:
        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
        if str(agents_dir) not in sys.path:
            sys.path.insert(0, str(agents_dir))
        from registry import get_registry
        registry = get_registry()
        return set(registry.list_all())
    except Exception as e:
        logger.warning(f"Failed to get agent names for mention parsing: {e}")
        return set()


def register_client(ws: WebSocket, session_id: str):
    """Register a client WebSocket for a specific session.

    Called when client subscribes to a session. Removes from any
    previous session first (client can only view one chat at a time).
    """
    # Remove from all other sessions
    for sid, clients in list(session_clients.items()):
        clients.discard(ws)
        # Clean up empty sets
        if not clients:
            del session_clients[sid]

    # Add to new session
    session_clients[session_id].add(ws)

# Track active ClaudeWrapper instances for interrupt capability
active_claude_wrappers: Dict[str, ClaudeWrapper] = {}

# Path to pending restart config file — written by the restart_server MCP tool,
# read by the streaming loop after a clean save.
_PENDING_RESTART_FILE = os.path.join(ROOT_DIR, ".claude", "pending_restart.json")
_PENDING_RESTART_WAIT_SECONDS = 30.0
_PENDING_RESTART_POLL_SECONDS = 0.25
_PENDING_RESTART_MTIME_GRACE_SECONDS = 2.0
_RESTART_FAIL_CLOSED_TEXT = "restart_server cannot safely restart from this invocation context"
_RESTART_NO_MARKER_TEXT = "No pending restart or continuation marker was written"


def _restart_tool_result_allows_finalizer(tool_output: Any, is_error: bool) -> bool:
    """Return True only when restart_server produced a restart marker contract.

    The stdio MCP bridge can surface a tool's error-shaped text as a normal
    Codex tool_end event. Do not let the streaming finalizer infer a valid
    restart from the known fail-closed restart_server response.
    """
    if is_error:
        return False
    text = str(tool_output or "")
    return not (
        _RESTART_FAIL_CLOSED_TEXT in text
        or _RESTART_NO_MARKER_TEXT in text
    )


async def _load_fresh_pending_restart_config(trigger_time: float) -> Dict[str, Any]:
    """Wait for restart_server's pending config from this tool invocation.

    Codex can emit the restart_server tool_end event before the MCP tool's file
    write is visible to the streaming finalizer. Ignore stale configs from older
    failed restarts and wait briefly for the fresh config before giving up.
    """
    deadline = time.time() + _PENDING_RESTART_WAIT_SECONDS
    last_error = "pending_restart.json not found"
    while time.time() < deadline:
        if os.path.exists(_PENDING_RESTART_FILE):
            try:
                file_mtime = os.path.getmtime(_PENDING_RESTART_FILE)
                file_age = time.time() - file_mtime
                mtime_before_trigger = trigger_time - file_mtime
                if file_mtime + _PENDING_RESTART_MTIME_GRACE_SECONDS >= trigger_time:
                    with open(_PENDING_RESTART_FILE, 'r') as f:
                        config = json.load(f)
                    if config.get("restart_script"):
                        logger.info(
                            "RESTART: accepted pending_restart.json "
                            f"(mtime={file_mtime:.6f}, trigger_time={trigger_time:.6f}, "
                            f"mtime_before_trigger={mtime_before_trigger:.3f}s, "
                            f"file_age={file_age:.3f}s, "
                            f"grace={_PENDING_RESTART_MTIME_GRACE_SECONDS:.3f}s)"
                        )
                        return config
                    last_error = (
                        "pending_restart.json missing restart_script "
                        f"(mtime={file_mtime:.6f}, trigger_time={trigger_time:.6f}, "
                        f"mtime_before_trigger={mtime_before_trigger:.3f}s, "
                        f"file_age={file_age:.3f}s, "
                        f"grace={_PENDING_RESTART_MTIME_GRACE_SECONDS:.3f}s)"
                    )
                else:
                    last_error = (
                        "stale pending_restart.json "
                        f"(mtime={file_mtime:.6f}, trigger_time={trigger_time:.6f}, "
                        f"mtime_before_trigger={mtime_before_trigger:.3f}s, "
                        f"file_age={file_age:.3f}s, "
                        f"grace={_PENDING_RESTART_MTIME_GRACE_SECONDS:.3f}s)"
                    )
            except Exception as e:
                last_error = str(e)
        else:
            last_error = "pending_restart.json not found"
        await asyncio.sleep(_PENDING_RESTART_POLL_SECONDS)
    logger.error(f"RESTART: pending restart config unavailable after wait ({last_error})")
    return {}

# --- Session Streaming State (Block Model) ---
# This is the SINGLE SOURCE OF TRUTH for what the client should display during streaming.
# Uses a block-based model where each assistant message contains ordered content blocks.

def _gen_block_id() -> str:
    return f"blk_{uuid.uuid4().hex[:12]}"

@dataclass
class ContentBlock:
    """A single content block within an assistant message."""
    id: str = field(default_factory=_gen_block_id)
    type: str = "text"  # "thinking", "text", "tool_use", "tool_result"
    content: str = ""
    status: str = "in_progress"  # "in_progress", "complete"
    # Tool fields
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    is_error: bool = False
    raw_output: Optional[Dict[str, Any]] = None
    # Thinking fields
    started_at: Optional[float] = None
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "type": self.type, "content": self.content, "status": self.status}
        if self.type == "tool_use":
            d["tool_name"] = self.tool_name
            d["tool_call_id"] = self.tool_call_id
            if self.tool_input is not None:
                d["tool_input"] = self.tool_input
        elif self.type == "tool_result":
            d["tool_call_id"] = self.tool_call_id
            d["is_error"] = self.is_error
            if self.raw_output:
                d["raw_output"] = self.raw_output
        elif self.type == "thinking":
            if self.started_at is not None:
                d["started_at"] = self.started_at
            if self.duration_ms is not None:
                d["duration_ms"] = self.duration_ms
        return d


@dataclass
class SessionStreamingState:
    """Server-authoritative streaming state for a session.

    The `messages` list contains TurnMessage dicts with a `blocks` array.
    This is the SINGLE SOURCE OF TRUTH for what the client should display.
    """
    status: str = "idle"  # "idle", "streaming"
    messages: List[Dict[str, Any]] = field(default_factory=list)
    pending_forms: List[Dict[str, Any]] = field(default_factory=list)
    todos: Optional[list] = None
    seq: int = 0
    last_updated: float = field(default_factory=time.time)
    # Turn tracking: unique ID per edit/regenerate/message to prevent stale event processing
    turn_id: Optional[str] = None

    # Internal tracking (not sent to clients)
    _current_blocks: List[ContentBlock] = field(default_factory=list)
    _current_msg_id: Optional[str] = None
    # Whether this SS was initialized with full message history from disk.
    # If True, serialized SS messages are the complete display_messages (no merge needed).
    # If False (late init / empty init), SS only has current-turn messages and needs
    # previous display_messages prepended from disk.
    _has_full_history: bool = False

    def _next_seq(self) -> int:
        self.seq += 1
        self.last_updated = time.time()
        return self.seq

    def get_or_create_assistant_message(self) -> Tuple[str, bool]:
        """Get the current streaming assistant message ID, or create one.
        Returns (message_id, is_new)."""
        if self._current_msg_id:
            return self._current_msg_id, False
        msg_id = f"msg-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        self._current_msg_id = msg_id
        self._current_blocks = []
        msg = {
            "id": msg_id,
            "role": "assistant",
            "blocks": self._current_blocks,
            "status": "streaming",
            "created_at": time.time()
        }
        self.messages.append(msg)
        return msg_id, True

    def get_or_create_block(self, block_type: str) -> Tuple[ContentBlock, bool]:
        """Get the trailing in_progress block of the given type, or create a new one.
        Returns (block, is_new)."""
        if self._current_blocks:
            last = self._current_blocks[-1]
            if last.type == block_type and last.status == "in_progress":
                return last, False
        block = ContentBlock(type=block_type)
        if block_type == "thinking":
            block.started_at = time.time()
        self._current_blocks.append(block)
        return block, True

    def complete_trailing_blocks(self) -> List[dict]:
        """Complete any in_progress text/thinking blocks. Returns events to broadcast."""
        events = []
        for block in self._current_blocks:
            if block.status == "in_progress" and block.type in ("text", "thinking"):
                block.status = "complete"
                meta = {}
                if block.type == "thinking" and block.started_at:
                    block.duration_ms = int((time.time() - block.started_at) * 1000)
                    meta["duration_ms"] = block.duration_ms
                events.append({
                    "type": "block_end",
                    "seq": self._next_seq(),
                    "message_id": self._current_msg_id,
                    "block_id": block.id,
                    "metadata": meta if meta else None
                })
        return events

    def finalize_turn(self) -> List[dict]:
        """Complete all blocks, mark message complete, go idle. Returns events."""
        events = self.complete_trailing_blocks()
        if self._current_msg_id:
            # Find the message and mark it complete
            for msg in self.messages:
                if msg.get("id") == self._current_msg_id:
                    msg["status"] = "complete"
                    break
            events.append({
                "type": "message_end",
                "seq": self._next_seq(),
                "message_id": self._current_msg_id
            })
        self.status = "idle"
        events.append({
            "type": "session_status",
            "seq": self._next_seq(),
            "status": "idle"
        })
        self._current_msg_id = None
        self._current_blocks = []
        return events

    def snapshot(self) -> dict:
        """Build a full state snapshot for client reconnect/subscribe."""
        serialized_messages = []
        for msg in self.messages:
            if msg.get("blocks") is not None:
                # Assistant message with blocks
                serialized = {
                    "id": msg["id"],
                    "role": msg["role"],
                    "status": msg.get("status", "complete"),
                    "blocks": [b.to_dict() if isinstance(b, ContentBlock) else b for b in msg["blocks"]],
                }
                if "created_at" in msg:
                    serialized["created_at"] = msg["created_at"]
                if "reactions" in msg:
                    serialized["reactions"] = msg["reactions"]
            else:
                # User message or legacy message (pass through as-is)
                serialized = msg
            serialized_messages.append(serialized)

        return {
            "type": "state",
            "seq": self.seq,
            "status": self.status,
            "messages": serialized_messages,
            "isProcessing": self.status != "idle",
            "pending_form": None,  # Legacy compat (client uses form_request events)
            "todos": self.todos,
        }

# Map of session_id -> streaming state
session_streaming_states: Dict[str, SessionStreamingState] = {}

# Track active edit/regenerate tasks for cancellation on re-edit
# Maps chat_id -> asyncio.Task so we can cancel stale tasks
active_edit_tasks: Dict[str, asyncio.Task] = {}

# Track active tool heartbeat tasks for cancellation
# Key: session_id, Value: asyncio.Task
tool_heartbeat_tasks: Dict[str, asyncio.Task] = {}


async def send_tool_heartbeat(session_id: str, tool_name: str):
    """Send periodic heartbeat events while a tool is running to keep UI active.

    This prevents the client-side timeout from resetting to idle during
    long-running tool executions (e.g., coding tools, long bash commands).
    """
    heartbeat_interval = 10  # seconds
    try:
        while True:
            await asyncio.sleep(heartbeat_interval)
            # Check if session is still streaming (tool in progress)
            state = session_streaming_states.get(session_id)
            if not state or state.status != "streaming":
                break
            # Check if there are any in-progress tool_use blocks
            has_active_tool = any(
                b.type == "tool_use" and b.status == "in_progress"
                for b in state._current_blocks
            )
            if not has_active_tool:
                break
            # Send heartbeat to keep UI alive
            active_tool_names = [
                b.tool_name or "tool" for b in state._current_blocks
                if b.type == "tool_use" and b.status == "in_progress"
            ]
            heartbeat_text = f"Running {', '.join(active_tool_names or [tool_name])}..."
            await broadcast_to_session(session_id, {
                "type": "status",
                "text": heartbeat_text,
                "heartbeat": True
            })
            logger.debug(f"Tool heartbeat sent for {session_id}: {heartbeat_text}")
    except asyncio.CancelledError:
        logger.debug(f"Tool heartbeat task cancelled for {session_id}")
    except Exception as e:
        logger.warning(f"Tool heartbeat error for {session_id}: {e}")


def start_tool_heartbeat(session_id: str, tool_name: str):
    """Start a heartbeat task for a tool execution."""
    # Cancel any existing heartbeat for this session
    stop_tool_heartbeat(session_id)
    # Start new heartbeat task
    task = asyncio.create_task(send_tool_heartbeat(session_id, tool_name))
    tool_heartbeat_tasks[session_id] = task
    logger.debug(f"Started tool heartbeat for {session_id}: {tool_name}")


def stop_tool_heartbeat(session_id: str):
    """Stop the heartbeat task for a session."""
    task = tool_heartbeat_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()
        logger.debug(f"Stopped tool heartbeat for {session_id}")


# --- History injection for fresh runtime sessions ---
# Every runtime session starts fresh (no --resume). Conversation context is injected
# into the prompt so the assistant knows what was discussed. This eliminates "session
# expired" errors and ensures a single consistent prompt format across all paths
# (normal messages, edits, regenerates, scheduled tasks, wake-ups).
def _collect_pending_reactions(messages: List[Dict[str, Any]], display_messages: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Collect reactions on assistant messages since the last user message.

    Checks both messages and display_messages arrays since they may use different
    IDs. Reactions might only exist in one array depending on which ID format
    the frontend sent.

    Walks messages backwards, stopping at the previous user message.
    Returns formatted reaction descriptions like: '👍 to "Here's the fix..."'
    """
    lines = []

    def _extract_reactions(msg_list: List[Dict[str, Any]]) -> None:
        for msg in reversed(msg_list):
            if msg.get("role") == "user":
                break
            if msg.get("role") == "assistant" and msg.get("reactions"):
                # Get a preview of the message content
                content = msg.get("content", "")
                if not content and msg.get("blocks"):
                    # Try extracting text from blocks — skip thinking blocks
                    # so the preview shows the actual visible response
                    for block in msg["blocks"]:
                        block_type = block.get("type", "") if isinstance(block, dict) else getattr(block, "type", "")
                        if block_type == "thinking":
                            continue
                        block_content = block.get("content", "") if isinstance(block, dict) else getattr(block, "content", "")
                        if block_content:
                            content = block_content
                            break
                preview = content[:60].replace("\n", " ").strip()
                if len(content) > 60:
                    preview += "..."

                for emoji, reactors in msg["reactions"].items():
                    if "user" in reactors:
                        line = f'{emoji} to "{preview}"'
                        if line not in lines:  # Deduplicate across arrays
                            lines.append(line)

    # Check both arrays — IDs may differ between messages and display_messages
    _extract_reactions(messages)
    if display_messages:
        _extract_reactions(display_messages)

    return lines



def _display_text_for_match(msg: Dict[str, Any]) -> str:
    """Extract user-visible text for matching flat and display messages."""
    content = msg.get("content")
    if isinstance(content, str) and content:
        return content

    block_text = []
    for block in msg.get("blocks") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("content")
            if isinstance(text, str) and text:
                block_text.append(text)
    return "".join(block_text)


def _display_match_key(msg: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    role = msg.get("role")
    if role not in {"user", "assistant", "notice"} and not msg.get("formData"):
        return None
    text = _display_text_for_match(msg).strip()
    if not text and not msg.get("formData"):
        return None
    return (role or "", text)


def _display_normalized_text(text: str) -> str:
    return " ".join(text.split())


def _display_block_represents_message(block_msg: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    if not block_msg.get("blocks") or candidate.get("blocks"):
        return False
    if block_msg.get("role") != candidate.get("role"):
        return False
    if block_msg.get("id") and block_msg.get("id") == candidate.get("id"):
        return False

    candidate_text = _display_normalized_text(_display_text_for_match(candidate).strip())
    if not candidate_text:
        return False

    block_text = _display_normalized_text(_display_text_for_match(block_msg).strip())
    return bool(block_text and candidate_text in block_text)


def _display_message_represented_by_blocks(candidate: Dict[str, Any], block_messages: List[Dict[str, Any]]) -> bool:
    return any(_display_block_represents_message(block_msg, candidate) for block_msg in block_messages)


def _display_sort_time(msg: Dict[str, Any]) -> float:
    for key in ("timestamp", "created_at"):
        value = msg.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    msg_id = str(msg.get("id") or "")
    match = re.match(r"msg-(\d{13})-", msg_id)
    if match:
        return int(match.group(1)) / 1000.0
    return float("inf")


def _dedupe_display_messages_by_id(display_messages: List[Dict[str, Any]], session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Remove duplicate display messages before persistence."""
    seen_ids: Set[str] = set()
    seen_keys: Set[Tuple[str, str]] = set()
    deduped: List[Dict[str, Any]] = []
    removed = 0
    for msg in display_messages:
        msg_id = msg.get("id")
        key = _display_match_key(msg)
        if msg_id and msg_id in seen_ids:
            removed += 1
            continue
        if key is not None and key in seen_keys:
            removed += 1
            continue
        deduped.append(msg)
        if msg_id:
            seen_ids.add(msg_id)
        if key is not None:
            seen_keys.add(key)

    if removed:
        label = f" for {session_id}" if session_id else ""
        logger.warning(f"DISPLAY_MSGS: Removed {removed} duplicate message(s){label} before save")
    return deduped


def _display_message_already_present(messages: List[Dict[str, Any]], candidate: Dict[str, Any]) -> bool:
    candidate_id = candidate.get("id")
    candidate_key = _display_match_key(candidate)
    for msg in messages:
        if candidate_id and msg.get("id") == candidate_id:
            return True
        if candidate_key is not None and _display_match_key(msg) == candidate_key:
            return True
    return False


def _display_messages_for_save(
    flat_messages: List[Dict[str, Any]],
    display_messages: List[Dict[str, Any]],
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Finalize UI messages for persistence without hiding visible flat history."""
    merged = _messages_for_display(
        {"messages": flat_messages, "display_messages": display_messages},
        session_id,
    )
    return _dedupe_display_messages_by_id(merged, session_id)


def _messages_for_display(chat_data: Dict[str, Any], session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return UI-facing messages without letting stale display_messages hide flat history.

    display_messages is richer than flat messages, so keep it when available.
    If it is stale/truncated, recover visible flat messages that are not already
    represented by id, role/content, or a block-model assistant turn. Tool/system
    internals stay out of the UI.
    """
    flat_messages = list(chat_data.get("messages") or [])
    display_messages = list(chat_data.get("display_messages") or [])
    if not display_messages:
        return flat_messages
    if not flat_messages:
        return _dedupe_display_messages_by_id(display_messages, session_id)

    flat_index_by_id: Dict[str, int] = {}
    flat_index_by_key: Dict[Tuple[str, str], int] = {}
    visible_flat_messages: List[Tuple[int, Dict[str, Any]]] = []
    for idx, msg in enumerate(flat_messages):
        if msg.get("role") in {"tool_call", "system"}:
            continue
        msg_id = msg.get("id")
        if msg_id and msg_id not in flat_index_by_id:
            flat_index_by_id[msg_id] = idx
        key = _display_match_key(msg)
        if key is not None and key not in flat_index_by_key:
            flat_index_by_key[key] = idx
        if key is not None:
            visible_flat_messages.append((idx, msg))

    block_messages = [msg for msg in display_messages if msg.get("blocks")]

    seen_ids: Set[str] = set()
    seen_keys: Set[Tuple[str, str]] = set()
    merged: List[Dict[str, Any]] = []
    deduped = 0
    for msg in display_messages:
        msg_id = msg.get("id")
        key = _display_match_key(msg)
        if msg_id and msg_id in seen_ids:
            deduped += 1
            continue
        if key is not None and key in seen_keys:
            deduped += 1
            continue
        if _display_message_represented_by_blocks(msg, block_messages):
            deduped += 1
            continue
        merged.append(msg)
        if msg_id:
            seen_ids.add(msg_id)
        if key is not None:
            seen_keys.add(key)

    recovered = 0
    for msg in flat_messages:
        role = msg.get("role")
        if role in {"tool_call", "system"}:
            continue
        msg_id = msg.get("id")
        if msg_id and msg_id in seen_ids:
            continue
        key = _display_match_key(msg)
        if key is None:
            continue
        if key in seen_keys:
            continue
        if _display_message_represented_by_blocks(msg, merged):
            continue

        merged.append(msg)
        recovered += 1
        if msg_id:
            seen_ids.add(msg_id)
        seen_keys.add(key)

    def _display_order(msg: Dict[str, Any], original_idx: int) -> Tuple[int, float, int]:
        msg_id = msg.get("id")
        if msg_id and msg_id in flat_index_by_id:
            return (0, float(flat_index_by_id[msg_id]), original_idx)
        key = _display_match_key(msg)
        if key is not None and key in flat_index_by_key:
            return (0, float(flat_index_by_key[key]), original_idx)
        if msg.get("blocks"):
            represented_indices = [
                idx for idx, flat_msg in visible_flat_messages
                if _display_block_represents_message(msg, flat_msg)
            ]
            if represented_indices:
                return (0, float(min(represented_indices)), original_idx)
        msg_time = _display_sort_time(msg)
        if msg_time != float("inf"):
            return (1, msg_time, original_idx)
        return (2, float(original_idx), original_idx)

    merged = [
        msg for _, msg in sorted(
            enumerate(merged),
            key=lambda item: _display_order(item[1], item[0]),
        )
    ]

    if recovered or deduped:
        label = f" for {session_id}" if session_id else ""
        if recovered:
            logger.warning(
                f"DISPLAY_MSGS: Recovered {recovered} visible flat message(s){label} "
                "missing from stale display_messages"
            )
        if deduped:
            logger.warning(f"DISPLAY_MSGS: Removed {deduped} duplicate display message(s){label}")
    return merged

def _build_history_context(messages: List[Dict[str, Any]], current_message: str) -> str:
    """Build a prompt with conversation history prepended.

    Args:
        messages: Prior messages from chat storage (excluding the current message).
        current_message: The new user message to append.

    Returns:
        A single prompt string with history context + current message.
        If no prior messages, returns just the current message.

    Note: No message limit — all history is included. Use compact_conversation
    tool to manage context window size in long conversations.
    """
    if not messages:
        return current_message

    parts = []
    for m in messages:
        role = m.get("role", "user")

        # Tool call entries — format as compact one-liners
        if role == "tool_call":
            parts.append(format_tool_for_history(m))
            continue

        # Compacted history summary — inject as-is
        if role == "compacted":
            parts.append(m.get("content", ""))
            continue

        content = m.get("content", "")
        if not content:
            continue
        if role == "user":
            # Add timestamp to user messages so agents see when each message was sent
            created_at = m.get("created_at")
            if created_at:
                try:
                    ts = datetime.fromtimestamp(created_at).strftime("%A, %-m/%-d/%Y at %-I:%M%p")
                    parts.append(f"User: [{ts}] {content}")
                except (OSError, ValueError):
                    parts.append(f"User: {content}")
            else:
                parts.append(f"User: {content}")
        elif role == "assistant":
            # Messages from @mentioned agents have an "agent" field
            agent = m.get("agent")
            if agent:
                parts.append(f"[@{agent}]: {content}")
            else:
                parts.append(f"Assistant: {content}")
        elif role == "system":
            parts.append(f"System: {content}")

    if not parts:
        return current_message

    history = "\n\n".join(parts)
    return (
        f"<chat-history>\n{history}\n</chat-history>\n\n"
        f"<current-message>\n{current_message}\n</current-message>"
    )


# Serialize prompt-type scheduled tasks so only one ClaudeWrapper runs at a time.
# Agent-type tasks bypass this lock because they use the agent runner which manages
# its own concurrency.  This prevents the "conversation not found" errors that
# occur when two prompt tasks race to create SDK sessions simultaneously.
scheduled_prompt_lock = asyncio.Lock()

# Timeout limits to prevent hung tasks from blocking all scheduled work.
# Agent tasks can be long-running (tool use, web fetches), so 15 min is generous.
# Notification batches process wake-up events and should complete faster.
SCHEDULED_TASK_TIMEOUT = 900   # 15 minutes
NOTIFICATION_BATCH_TIMEOUT = 14400  # 4 hours; agent-thread ping wakes may do real follow-up work
PING_COMPLETION_BUFFER_SECONDS = 30.0

# Per-chat locks to serialize message processing and prevent race conditions
# This ensures concurrent messages to the same chat are processed sequentially
chat_processing_locks: Dict[str, asyncio.Lock] = {}
chat_lock_last_used: Dict[str, float] = {}  # chat_id -> timestamp of last use
CHAT_LOCK_MAX_AGE = 3600  # Remove locks unused for 1 hour

def get_chat_lock(chat_id: str) -> asyncio.Lock:
    """Get or create a lock for a specific chat ID."""
    if chat_id not in chat_processing_locks:
        chat_processing_locks[chat_id] = asyncio.Lock()
    chat_lock_last_used[chat_id] = time.time()
    return chat_processing_locks[chat_id]

def _cleanup_chat_locks():
    """Remove chat locks that haven't been used recently. Called periodically."""
    cutoff = time.time() - CHAT_LOCK_MAX_AGE
    stale = [
        chat_id for chat_id, last_used in chat_lock_last_used.items()
        if last_used < cutoff
        and chat_id in chat_processing_locks
        and not chat_processing_locks[chat_id].locked()
    ]
    for chat_id in stale:
        chat_processing_locks.pop(chat_id, None)
        chat_lock_last_used.pop(chat_id, None)
    if stale:
        logger.info(f"Cleaned up {len(stale)} stale chat locks")


# Chat image GC state - runs once daily
_last_image_gc_time: float = 0.0
_IMAGE_GC_INTERVAL = 86400  # 24 hours between GC runs
_IMAGE_GC_MIN_AGE_DAYS = 7  # Only delete orphans older than 7 days


async def _maybe_run_image_gc():
    """Run chat image garbage collection if enough time has passed (once daily)."""
    global _last_image_gc_time
    now = time.time()

    if now - _last_image_gc_time < _IMAGE_GC_INTERVAL:
        return

    _last_image_gc_time = now

    try:
        # Run GC in a thread to avoid blocking the event loop (file I/O)
        import importlib.util
        gc_script = os.path.join(ROOT_DIR, ".claude", "scripts", "chat_image_gc.py")

        if not os.path.exists(gc_script):
            return

        spec = importlib.util.spec_from_file_location("chat_image_gc", gc_script)
        gc_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gc_module)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: gc_module.run_gc(
                delete=True,
                min_age_days=_IMAGE_GC_MIN_AGE_DAYS,
                as_json=True,
            )
        )

        if result.get("deleted", 0) > 0:
            logger.info(
                f"Image GC: deleted {result['deleted']} orphaned images, "
                f"freed {result.get('freed_bytes_human', '0 B')}"
            )
        else:
            orphans = result.get("orphan_count", 0)
            if orphans > 0:
                logger.info(f"Image GC: {orphans} orphans found but none old enough to delete")
            else:
                logger.debug("Image GC: no orphaned images found")
    except Exception as e:
        logger.error(f"Image GC error: {e}")


# Track pending form requests (for forms_show tool)
# Key: session_id, Value: {"form_id": str, "prefill": dict}
pending_form_requests: Dict[str, Dict[str, Any]] = {}

# Track recently completed sessions with timestamps - for reconnect fallback
# Key: session_id, Value: timestamp when processing completed
# This helps direct reconnecting clients to the right session even if they have old localStorage
recently_completed_sessions: Dict[str, float] = {}
RECENTLY_COMPLETED_TTL = 120.0  # Keep past the 30s ping buffer plus scheduler jitter


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    global server_restart_info, restart_continuation

    await websocket.accept()
    # Create client session for visibility tracking
    client_sessions[websocket] = ClientSession(websocket=websocket)

    # Notify client if server was restarted
    if server_restart_info:
        info = server_restart_info
        server_restart_info = None  # Clear so subsequent connections don't trigger reload loops
        try:
            await websocket.send_json({
                "type": "server_restarted",
                "shutdown_time": info.get("shutdown_time"),
                "active_sessions": info.get("active_sessions", []),
                "active_processing": info.get("active_processing", {}),
                "message": "Server was restarted. Your previous session should be preserved."
            })
        except Exception as e:
            logger.warning(f"Could not send restart notification: {e}")

    # NOTE: Restart continuation is now handled by the restart_continuation_wakeup_loop()
    # background task, which waits for WebSocket connections and then wakes ALL sessions.
    # See startup_event() for where it's launched.

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action", "message")

            if action == "message":
                # CRITICAL: Set up streaming state BEFORE starting background task
                # This prevents race condition where user refreshes before task registers its state
                session_id = data.get("sessionId", "new")
                preserve_chat_id = data.get("preserveChatId")
                msg_id = data.get("msgId")

                # FIX: Validate session ID exists on disk, otherwise check for message
                if session_id != "new" and not preserve_chat_id:
                    existing_chat = chat_manager.load_chat(session_id)
                    if not existing_chat:
                        # Session ID doesn't exist on disk - check if message already saved elsewhere
                        logger.warning(f"Session {session_id} not found on disk, checking for message {msg_id}")
                        existing_chat_id = _find_chat_with_message(msg_id)
                        if existing_chat_id:
                            logger.info(f"Found message {msg_id} in existing chat {existing_chat_id}")
                            session_id = existing_chat_id
                            preserve_chat_id = existing_chat_id
                        else:
                            # No existing chat found - treat as new
                            session_id = "new"

                # FIX: Check for duplicate message before generating new chat ID
                if session_id == "new" and not preserve_chat_id:
                    # Before generating new UUID, check if this message already exists
                    existing_chat_id = _find_chat_with_message(msg_id)
                    if existing_chat_id:
                        logger.info(f"Found message {msg_id} in existing chat {existing_chat_id}, reusing")
                        early_chat_id = existing_chat_id
                    else:
                        early_chat_id = str(uuid.uuid4())
                    data["_early_chat_id"] = early_chat_id  # Pass to background task
                    state_key = early_chat_id
                else:
                    state_key = preserve_chat_id or session_id

                # Load existing messages from disk for the streaming state snapshot
                _init_messages = []
                _existing_chat_data = None
                if session_id != "new" and not preserve_chat_id and existing_chat:
                    # Reuse already-loaded existing_chat from validation above
                    _existing_chat_data = existing_chat
                elif state_key:
                    # Load from disk using the resolved state_key
                    _existing_chat_data = chat_manager.load_chat(state_key)
                if _existing_chat_data:
                    # Prefer display_messages (preserves blocks/thinking) over flat messages
                    _init_messages = list(_messages_for_display(_existing_chat_data, state_key))

                # Determine agent for this chat (needed for both running_agents
                # registration and the session_init send below).
                ws_agent = None
                if session_id == "new":
                    ws_agent = data.get("agent")  # Only accept agent on new chats
                else:
                    stored = chat_manager.load_chat(session_id)
                    ws_agent = stored.get("agent") if stored else None

                # Register streaming state IMMEDIATELY (before task runs)
                turn_id = str(uuid.uuid4())
                await _record_chat_session_started(state_key, ws_agent, data.get("message", ""))
                session_streaming_states[state_key] = SessionStreamingState(
                    status="streaming",
                    messages=_init_messages,
                    _has_full_history=bool(_init_messages),
                    turn_id=turn_id,
                )
                data["turnId"] = turn_id
                register_client(websocket, state_key)
                logger.info(f"PRE-TASK: Registered streaming state for {state_key}")

                # IMMEDIATELY send session_init so client can update localStorage
                # This prevents losing the chat ID if user refreshes before background task sends it
                try:
                    await websocket.send_json({
                        "type": "session_init",
                        "id": state_key,
                        "agent": ws_agent,
                        "turnId": turn_id
                    })
                    logger.info(f"PRE-TASK: Sent immediate session_init for {state_key}, agent={ws_agent}, turnId={turn_id}")
                except Exception:
                    pass  # Client may have already disconnected

                # Now start the background task - state is already registered
                asyncio.create_task(handle_message(websocket, data))
            elif action == "edit":
                # For edits, we know the chat_id upfront
                chat_id = data.get("sessionId")
                turn_id = str(uuid.uuid4())
                if chat_id:
                    # Cancel any previous edit/regenerate task for this chat
                    prev_task = active_edit_tasks.pop(chat_id, None)
                    if prev_task and not prev_task.done():
                        prev_task.cancel()
                        logger.info(f"EDIT: Cancelled previous edit/regenerate task for {chat_id}")
                        # Also interrupt the active Claude wrapper to stop SDK processing
                        prev_wrapper = active_claude_wrappers.get(chat_id)
                        if prev_wrapper:
                            try:
                                await prev_wrapper.interrupt()
                                logger.info(f"EDIT: Interrupted previous Claude wrapper for {chat_id}")
                            except Exception as e:
                                logger.warning(f"EDIT: Failed to interrupt previous wrapper: {e}")
                    _edit_chat = chat_manager.load_chat(chat_id)
                    _edit_agent = _edit_chat.get("agent") if _edit_chat else None
                    await _record_chat_session_started(chat_id, _edit_agent, data.get("message", ""))
                    session_streaming_states[chat_id] = SessionStreamingState(
                        status="streaming",
                        turn_id=turn_id,
                    )
                    register_client(websocket, chat_id)
                data["turnId"] = turn_id
                task = asyncio.create_task(handle_edit(websocket, data))
                if chat_id:
                    active_edit_tasks[chat_id] = task
            elif action == "regenerate":
                # For regenerate, we know the chat_id upfront
                session_id = data.get("sessionId")
                turn_id = str(uuid.uuid4())
                if session_id:
                    # Cancel any previous edit/regenerate task for this chat
                    prev_task = active_edit_tasks.pop(session_id, None)
                    if prev_task and not prev_task.done():
                        prev_task.cancel()
                        logger.info(f"REGENERATE: Cancelled previous edit/regenerate task for {session_id}")
                        prev_wrapper = active_claude_wrappers.get(session_id)
                        if prev_wrapper:
                            try:
                                await prev_wrapper.interrupt()
                            except Exception:
                                pass
                    _regen_chat = chat_manager.load_chat(session_id)
                    _regen_agent = _regen_chat.get("agent") if _regen_chat else None
                    await _record_chat_session_started(session_id, _regen_agent, "(regenerate)")
                    session_streaming_states[session_id] = SessionStreamingState(
                        status="streaming",
                        turn_id=turn_id,
                    )
                    register_client(websocket, session_id)
                data["turnId"] = turn_id
                task = asyncio.create_task(handle_regenerate(websocket, data))
                if session_id:
                    active_edit_tasks[session_id] = task
            elif action == "interrupt":
                await handle_interrupt(websocket, data)
            elif action == "inject":
                # Mid-stream message injection - send while Claude is working
                await handle_inject(websocket, data)
            elif action == "visibility_update":
                # Update client's visibility state
                session = client_sessions.get(websocket)
                if session:
                    is_active = data.get("isActive", False)
                    chat_id = data.get("chatId")
                    session.update_visibility(is_active=is_active, chat_id=chat_id)
                    logger.info(f"Visibility update: active={is_active}, chat={chat_id}")

                    # Update active room tracking if user is focused on a specific chat
                    if is_active and chat_id and active_room:
                        try:
                            active_room.set_active_room(chat_id, context="chat")
                        except Exception:
                            pass
            elif action == "reaction":
                await handle_reaction(websocket, data)
            elif action == "subscribe":
                # Client wants full state for a session - THIS IS THE KEY FOR RECONNECT
                await handle_subscribe(websocket, data)
            elif action == "slash_command":
                await handle_slash_command_ws(websocket, data)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected - background tasks will continue processing")
    except Exception as e:
        logger.error(f"WebSocket Error: {e}")
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass
    finally:
        client_sessions.pop(websocket, None)
        # Also remove from session_clients
        for sid, clients in list(session_clients.items()):
            clients.discard(websocket)
            if not clients:
                del session_clients[sid]


async def handle_subscribe(websocket: WebSocket, data: dict):
    """
    Handle client subscription to a session.
    THIS IS THE KEY FOR RECONNECT RECOVERY.

    SERVER IS THE SOURCE OF TRUTH - if there's an active stream, we return it
    regardless of what session ID the client thinks it has.
    """
    requested_session_id = data.get("sessionId", "new")
    intent = data.get("intent")  # "new_chat" = user explicitly wants a new chat
    logger.info(f"SUBSCRIBE: Client requesting session {requested_session_id}, intent={intent}")

    # For intentional new chat: unregister from all sessions, return empty state
    if intent == "new_chat":
        for sid, clients in list(session_clients.items()):
            clients.discard(websocket)
            if not clients:
                del session_clients[sid]
        logger.info(f"SUBSCRIBE: New chat requested (intent=new_chat), unregistered from all sessions")
        await websocket.send_json({
            "type": "state",
            "seq": 0,
            "sessionId": "new",
            "messages": [],
            "cumulative_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "isProcessing": False,
            "status": "idle",
            "agent": None,
            "helper_settings": _normalize_helper_settings(None),
            "pending_form": None,
            "todos": None,
        })
        return

    # NOTE: We intentionally do NOT register_client here yet.
    # Registration happens AFTER sending the state snapshot to prevent a race condition:
    # if we register first, broadcast events can arrive at the client before the state
    # snapshot, causing content to be lost when the state response overwrites everything.

    # Check if the REQUESTED session has an active stream (per-session, not global)
    # Also check if any other session is actively streaming (for reconnect recovery)
    active_session_id = None
    streaming_state = None

    # First: check if the requested session itself is actively streaming
    if requested_session_id and requested_session_id in session_streaming_states:
        # Safety check: if streaming state exists but no active wrapper, it's orphaned/stale.
        # The wrapper is deleted when the streaming loop ends, so if it's gone,
        # processing is definitely done (completed or crashed). Clean up stale state.
        if requested_session_id not in active_claude_wrappers:
            logger.warning(f"SUBSCRIBE: Cleaning up orphaned streaming state for {requested_session_id} (no active wrapper)")
            del session_streaming_states[requested_session_id]
            await _record_chat_session_ended(requested_session_id)
            stop_tool_heartbeat(requested_session_id)
        else:
            active_session_id = requested_session_id
            streaming_state = session_streaming_states[requested_session_id]
            logger.info(f"SUBSCRIBE: Requested session {requested_session_id} is actively streaming")
    elif requested_session_id == "new" or not requested_session_id:
        # Client doesn't know its session - check if there's exactly ONE active stream
        # (If multiple concurrent streams, we can't guess which one the client wants)
        active_streams = {sid: state for sid, state in session_streaming_states.items()
                         if sid in active_processing_sessions}
        if len(active_streams) == 1:
            active_session_id = next(iter(active_streams))
            streaming_state = active_streams[active_session_id]
            logger.info(f"SUBSCRIBE: Single active stream found: {active_session_id} (client requested {requested_session_id})")
        elif len(active_streams) > 1:
            logger.info(f"SUBSCRIBE: {len(active_streams)} concurrent streams active, cannot auto-redirect client")

    # Check for recently completed sessions if no active stream
    # This handles the case where processing finished between user refresh and reconnect
    # ONLY redirect when the client has lost its session ID (requesting "new" or empty).
    # If the client explicitly requests a specific session, respect it — don't hijack
    # to some other recently-completed chat.
    recent_session_id = None
    client_has_no_session = not requested_session_id or requested_session_id == "new"
    if not active_session_id and client_has_no_session and recently_completed_sessions:
        # Clean up expired entries first
        now = time.time()
        expired = [sid for sid, ts in recently_completed_sessions.items() if now - ts > RECENTLY_COMPLETED_TTL]
        for sid in expired:
            del recently_completed_sessions[sid]

        # Find the most recent session
        if recently_completed_sessions:
            recent_session_id = max(recently_completed_sessions.keys(), key=lambda k: recently_completed_sessions[k])
            logger.info(f"SUBSCRIBE: Found recently completed session {recent_session_id} (client requested {requested_session_id})")

    # Determine which session to return data for
    # Priority: active stream > recently completed > requested session
    effective_session_id = active_session_id or recent_session_id or requested_session_id

    # NOTE: register_client is called AFTER sending state snapshot (see below)

    # Default state
    messages = []
    cumulative_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # Load state — prefer block model snapshot when actively streaming
    chat_agent = None
    if effective_session_id and effective_session_id != "new":
        if streaming_state:
            # Active streaming: use block model snapshot (authoritative)
            state_response = streaming_state.snapshot()
            state_response["sessionId"] = effective_session_id
            # Load metadata from disk (agent, cumulative_usage)
            chat_data = chat_manager.load_chat(effective_session_id)
            if chat_data:
                cumulative_usage = chat_data.get("cumulative_usage", cumulative_usage)
                chat_agent = chat_data.get("agent")
                state_response["helper_settings"] = _normalize_helper_settings(chat_data.get("helper_settings"))
            else:
                state_response["helper_settings"] = _normalize_helper_settings(None)
            state_response["cumulative_usage"] = cumulative_usage
            state_response["agent"] = chat_agent
            logger.info(f"SUBSCRIBE: Using block model snapshot for {effective_session_id} - {len(state_response['messages'])} messages, status={state_response['status']}")
        else:
            # No active streaming — load from disk (source of truth)
            chat_data = chat_manager.load_chat(effective_session_id)
            if chat_data:
                # Prefer display_messages (has blocks, thinking) over flat messages
                messages = _messages_for_display(chat_data, effective_session_id)
                cumulative_usage = chat_data.get("cumulative_usage", cumulative_usage)
                chat_agent = chat_data.get("agent")
                logger.info(f"SUBSCRIBE: Loaded {len(messages)} messages from disk for {effective_session_id} (display_messages={'display_messages' in chat_data})")

                # Safety net: ensure form messages from conv.messages aren't
                # missing from display_messages (forms can be lost during
                # display_messages rebuild across turns).
                conv_msgs = chat_data.get("messages", [])
                form_msgs_from_conv = [m for m in conv_msgs if m.get("formData")]
                if form_msgs_from_conv:
                    existing_form_ids = {
                        m.get("formData", {}).get("formId")
                        for m in messages if m.get("formData")
                    }
                    for fm in form_msgs_from_conv:
                        fm_id = fm.get("formData", {}).get("formId")
                        if fm_id and fm_id not in existing_form_ids:
                            # Insert before the last assistant message
                            insert_idx = len(messages)
                            for i in range(len(messages) - 1, -1, -1):
                                if messages[i].get("role") == "assistant":
                                    insert_idx = i
                                    break
                            messages.insert(insert_idx, fm)
                            existing_form_ids.add(fm_id)
                            logger.info(f"SUBSCRIBE: Restored missing form '{fm_id}' into display messages")

            state_response = {
                "type": "state",
                "seq": 0,
                "sessionId": effective_session_id,
                "messages": messages,
                "isProcessing": False,
                "status": "idle",
                "agent": chat_agent,
                "helper_settings": _normalize_helper_settings(chat_data.get("helper_settings") if chat_data else None),
                "cumulative_usage": cumulative_usage,
                "pending_form": None,
                "todos": None,
            }
    else:
        state_response = {
            "type": "state",
            "seq": 0,
            "sessionId": effective_session_id,
            "messages": messages,
            "isProcessing": False,
            "status": "idle",
            "agent": None,
            "helper_settings": _normalize_helper_settings(None),
            "cumulative_usage": cumulative_usage,
            "pending_form": None,
            "todos": None,
        }

    await websocket.send_json(state_response)

    # NOW register for broadcasts — AFTER state snapshot is sent.
    # This prevents the race condition where broadcast events arrive before the snapshot,
    # causing content to be lost when the state response overwrites accumulated deltas.
    if effective_session_id and effective_session_id != "new":
        register_client(websocket, effective_session_id)

    # If there are pending forms, send them all to this reconnecting client
    # This handles mobile clients who missed the initial form_request broadcast
    if streaming_state and streaming_state.pending_forms:
        logger.info(f"SUBSCRIBE: Sending {len(streaming_state.pending_forms)} pending form(s) to reconnecting client for {effective_session_id}")
        for pf in streaming_state.pending_forms:
            await websocket.send_json(pf)


async def handle_message(websocket: WebSocket, data: dict):
    """Handle a new message from the user."""

    session_id = data.get("sessionId", "new")
    prompt = data.get("message", "")
    msg_id = data.get("msgId") or str(uuid.uuid4())
    force_new_session = data.get("forceNewSession", False)  # Legacy: always fresh now, kept for caller compat
    preserve_chat_id = data.get("preserveChatId")  # For edit/regenerate, keep same chat file
    context_messages = data.get("contextMessages", [])  # For edit: messages before edit point
    is_system_continuation = data.get("isSystemContinuation", False)  # For restart continuation

    if not prompt:
        return

    # ========== PER-CHAT LOCK: Serialize concurrent messages to same chat ==========
    # Determine lock key early - prevents race conditions with concurrent messages
    early_chat_id = data.get("_early_chat_id")
    lock_key = early_chat_id or preserve_chat_id or (session_id if session_id != "new" else msg_id)
    chat_lock = get_chat_lock(lock_key)
    logger.info(f"LOCK: Acquiring lock for {lock_key}")

    async with chat_lock:
        logger.info(f"LOCK: Acquired lock for {lock_key}")
        await _handle_message_inner(websocket, data, session_id, prompt, msg_id,
                                     force_new_session, preserve_chat_id, context_messages,
                                     is_system_continuation)
        logger.info(f"LOCK: Releasing lock for {lock_key}")


async def _handle_message_inner(websocket: WebSocket, data: dict, session_id: str,
                                 prompt: str, msg_id: str, force_new_session: bool,
                                 preserve_chat_id: Optional[str], context_messages: list,
                                 is_system_continuation: bool):
    """Inner message handler - runs while holding the per-chat lock."""

    # ========== WRITE-AHEAD LOG: Step 1 - Write before processing ==========
    # This is CRITICAL: Write the message to the WAL BEFORE any other processing
    # If the server crashes after this point, the message can be recovered
    wal = get_wal()
    if not is_system_continuation:
        wal.write_message(msg_id, session_id, prompt)
        logger.info(f"WAL: Message {msg_id} written to WAL before processing")

    # Immediately acknowledge message receipt so frontend knows it arrived
    # This happens AFTER the WAL write to ensure durability
    try:
        await websocket.send_json({
            "type": "message_received",
            "msgId": msg_id,
            "sessionId": session_id,
            "timestamp": time.time()  # Server timestamp for confirmation
        })
    except Exception:
        pass  # Client may have disconnected - processing continues

    # Mark ACK sent in WAL
    if not is_system_continuation:
        wal.ack_message(msg_id)

    # Add timestamp to prompt so Claude knows the current time
    # Note: We add this to 'prompt' but history stores original via data.get("message")
    # Format: "Monday, 1/26/2026 at 8:59AM" - includes day of week so Claude doesn't have to calculate it
    timestamp = datetime.now().strftime("%A, %-m/%-d/%Y at %-I:%M%p")
    prompt = f"[{timestamp}] {prompt}"

    # Use pre-generated chat ID if available (set by websocket loop before starting background task)
    # This ensures consistency between the state registered before task start and the task itself
    early_chat_id = data.get("_early_chat_id")

    incoming_helper_settings = _extract_helper_settings_payload(data)
    settings_chat_id = early_chat_id or preserve_chat_id or (session_id if session_id != "new" else None)
    settings_source = chat_manager.load_chat(settings_chat_id) if settings_chat_id else None
    helper_settings = _merge_helper_settings(
        settings_source.get("helper_settings") if settings_source else None,
        incoming_helper_settings,
    )

    # Track which session is currently being processed
    # Priority: early_chat_id (pre-generated) > preserve_chat_id > session_id
    streaming_state_key = early_chat_id or preserve_chat_id or session_id
    # The WS loop already registered most chats at the message-receive step.
    # Defensive register for paths that reach here without going through the
    # WS pre-task hook (restart continuation, system continuation). The agent
    # name is resolved a few lines later in this function; the WS-loop case
    # has the correct agent already on its entry, so we skip if registered.
    if streaming_state_key not in active_processing_sessions:
        await _record_chat_session_started(streaming_state_key, data.get("agent"), data.get("message", ""))
    logger.info(f"PROCESSING: active session={streaming_state_key}, early_chat_id={early_chat_id}")

    # Streaming state was already initialized by websocket loop before task started
    # Just update it if needed (in case of any ID changes)
    if streaming_state_key not in session_streaming_states:
        session_streaming_states[streaming_state_key] = SessionStreamingState(
            status="streaming",
        )
        logger.info(f"STREAMING_STATE: Late initialization for {streaming_state_key}")

    # Get or create conversation state
    # IMPORTANT: 'new' always creates a fresh state - don't reuse
    if session_id == 'new' or session_id not in active_conversations:
        conv = ConversationState()
        conv.session_id = session_id

        # Load existing chat data from disk if available
        # This handles: continuations (restart/edit), resuming after server restart, or continuing a saved session
        chat_id_to_load = preserve_chat_id or (session_id if session_id != 'new' else None)
        if chat_id_to_load:
            existing_chat = chat_manager.load_chat(chat_id_to_load)
            if existing_chat:
                if existing_chat.get("messages"):
                    conv.messages = existing_chat["messages"].copy()
                # Load cumulative usage - this is key for the token tracker
                if existing_chat.get("cumulative_usage"):
                    conv.cumulative_usage = existing_chat["cumulative_usage"].copy()
                logger.info(f"Loaded existing chat: {len(conv.messages)} messages, "
                           f"{conv.cumulative_usage.get('total_tokens', 0)} cumulative tokens")

        if session_id != 'new':
            active_conversations[session_id] = conv
    else:
        conv = active_conversations[session_id]

    # Always start a fresh SDK session — never resume.
    # Conversation history is injected into the prompt via _build_history_context().
    effective_session_id = "new"

    if context_messages:
        # Edit/regenerate: use the explicit context_messages (messages before edit point)
        prompt = _build_history_context(context_messages, prompt)
        logger.info(f"MESSAGE: Injecting edit/regenerate context ({len(context_messages)} messages)")
    elif conv.messages:
        # Continuing a conversation: inject prior messages as context
        # Exclude the current user message (not yet added to conv.messages for new,
        # or will be the last element for existing chats)
        prior_messages = conv.messages
        prompt = _build_history_context(prior_messages, prompt)
        logger.info(f"MESSAGE: Injecting conversation history ({len(prior_messages)} messages)")

    # Prepend pending reactions (added since last user message) to the prompt
    # Check both messages and display_messages — they use different ID formats,
    # so reactions from the frontend may only exist in display_messages
    display_messages_for_reactions = None
    reaction_chat_data = chat_manager.load_chat(session_id) if session_id != 'new' else None
    if reaction_chat_data:
        display_messages_for_reactions = reaction_chat_data.get("display_messages")
    reaction_lines = _collect_pending_reactions(conv.messages, display_messages_for_reactions)
    if reaction_lines:
        reaction_block = "User reacted " + ", ".join(reaction_lines) + " to previous messages.\n\n---\n\n"
        prompt = reaction_block + prompt
        logger.info(f"MESSAGE: Prepended {len(reaction_lines)} reaction(s) to prompt")

    # Extract agent name early — needed by EARLY_SAVE below (before full agent routing)
    # For existing chats, the client doesn't send agent — load from stored chat data
    agent_name = data.get("agent")
    if not agent_name:
        stored_chat_id_for_agent = early_chat_id or preserve_chat_id or (session_id if session_id != "new" else None)
        if stored_chat_id_for_agent:
            stored_for_agent = chat_manager.load_chat(stored_chat_id_for_agent)
            if stored_for_agent:
                agent_name = stored_for_agent.get("agent")
                if agent_name:
                    logger.info(f"AGENT: Loaded agent '{agent_name}' from stored chat {stored_chat_id_for_agent}")

    user_agent_content = data.get("message", "")
    user_display_content = data.get("displayMessage") or user_agent_content
    user_display_segments = data.get("displaySegments") if isinstance(data.get("displaySegments"), list) else None
    user_reply_references = data.get("replyReferences") if isinstance(data.get("replyReferences"), list) else None

    # Add user message - use frontend's ID if provided, otherwise generate one
    # Skip for system continuations (restart) - those shouldn't appear in chat history
    if not is_system_continuation:
        user_msg_id = msg_id  # Use the same ID as in WAL

        # EARLY SAVE: Save user message immediately to prevent loss if connection drops
        # This handles the case where WebSocket dies during the assistant response
        # Use the pre-generated early_chat_id if available (was set before background task started)
        early_save_id = early_chat_id or preserve_chat_id or (session_id if session_id != "new" else None)

        # For NEW sessions without a pre-generated ID, generate one now
        if not early_save_id:
            early_save_id = str(uuid.uuid4())
            logger.info(f"EARLY_SAVE: Generated new chat ID for new session: {early_save_id}")
        else:
            logger.info(f"EARLY_SAVE: Using pre-generated chat ID: {early_save_id}")

        try:
            existing = chat_manager.load_chat(early_save_id)

            # FIX: Check for duplicate message BEFORE adding to conversation
            # This prevents the same message from being saved multiple times
            skip_message_add = False
            if existing:
                existing_msg_ids = {m.get("id") for m in existing.get("messages", [])}
                if user_msg_id in existing_msg_ids:
                    logger.warning(f"EARLY_SAVE: Duplicate message {user_msg_id} already in {early_save_id}, skipping add")
                    skip_message_add = True
                    # Reload conv.messages from existing to ensure we have the right state
                    conv.messages = existing.get("messages", []).copy()
                    if existing.get("cumulative_usage"):
                        conv.cumulative_usage = existing["cumulative_usage"].copy()

            # Only add message if it's not a duplicate
            if not skip_message_add:
                msg_images = data.get("images")
                conv.add_message("user", user_agent_content, user_msg_id, images=msg_images)  # Store agent-facing context

                display_user_msg = dict(conv.messages[-1])
                display_user_msg["content"] = user_display_content
                if user_display_content != user_agent_content:
                    display_user_msg["agentContent"] = user_agent_content
                    display_user_msg["displayContent"] = user_display_content
                if user_display_segments is not None:
                    display_user_msg["displaySegments"] = user_display_segments
                if user_reply_references is not None:
                    display_user_msg["replyReferences"] = user_reply_references

                # Also add clean user message to streaming state for display_messages persistence.
                # The flat messages array remains agent-facing; display_messages remains UI-facing.
                _ss_for_user_msg = session_streaming_states.get(streaming_state_key)
                if _ss_for_user_msg:
                    if _display_message_already_present(_ss_for_user_msg.messages, display_user_msg):
                        logger.warning(
                            f"DISPLAY_MSGS: Skipped duplicate user message {user_msg_id} "
                            f"in streaming state for {streaming_state_key}"
                        )
                    else:
                        _ss_for_user_msg.messages.append(display_user_msg)

            # If this is a form submission, mark the corresponding form message as submitted
            user_msg_text = data.get("message", "")
            if user_msg_text.startswith("[FORM_SUBMISSION:"):
                import re
                form_match = re.match(r'\[FORM_SUBMISSION:\s*(\S+)\]', user_msg_text)
                if form_match:
                    submitted_form_id = form_match.group(1)
                    for msg in conv.messages:
                        if msg.get("formData", {}).get("formId") == submitted_form_id:
                            msg["formData"]["status"] = "submitted"
                            logger.info(f"Marked form {submitted_form_id} as submitted in conv.messages")
                            break
                    # Also update in SS messages (source for display_messages rebuild)
                    _ss_for_form = session_streaming_states.get(streaming_state_key)
                    if _ss_for_form:
                        for msg in _ss_for_form.messages:
                            if msg.get("formData", {}).get("formId") == submitted_form_id:
                                msg["formData"]["status"] = "submitted"
                                logger.info(f"Marked form {submitted_form_id} as submitted in SS messages")
                                break

            title = existing.get("title") if existing else chat_manager.generate_title(user_display_content)
            early_save_data = {
                "title": title,
                "sessionId": early_save_id,
                "messages": conv.messages,
                "cumulative_usage": conv.cumulative_usage,
                "helper_settings": helper_settings
            }
            if agent_name:
                early_save_data["agent"] = agent_name
            # Preserve display_messages from existing chat and append the clean
            # user message immediately so recovery never falls back to raw agent context.
            if not skip_message_add:
                existing_display_messages = list(existing.get("display_messages") or []) if existing else []
                if not _display_message_already_present(existing_display_messages, display_user_msg):
                    existing_display_messages.append(display_user_msg)
                early_save_data["display_messages"] = existing_display_messages
            elif existing and existing.get("display_messages"):
                early_save_data["display_messages"] = existing["display_messages"]
            chat_manager.save_chat(early_save_id, early_save_data)
            logger.info(f"EARLY_SAVE: Saved user message to {early_save_id}, agent={agent_name}")

            # Update WAL with resolved chat ID
            wal.start_processing(msg_id, early_save_id)

            # Store the early_save_id so we can use it later if session_id is 'new'
            if session_id == 'new':
                preserve_chat_id = early_save_id
                # With pre-generated IDs, streaming_state_key should already equal early_save_id
                # Just update in case they differ
                if streaming_state_key != early_save_id:
                    if streaming_state_key in session_streaming_states:
                        session_streaming_states[early_save_id] = session_streaming_states.pop(streaming_state_key)
                        logger.info(f"EARLY_SAVE: Migrated streaming state from {streaming_state_key} to {early_save_id}")
                    # Migrate active session tracking (running_agents entry id
                    # follows the dict key; running_agents.update refreshes the
                    # source_chat_id field on the live entry).
                    await _record_chat_session_rekey(streaming_state_key, early_save_id)
                    streaming_state_key = early_save_id

            # BROADCAST: message_accepted to ALL clients viewing this session
            # This is the backend-authoritative architecture: server persists first,
            # then broadcasts to all clients. No client-side optimistic updates.
            accepted_msg = {
                "id": user_msg_id,
                "role": "user",
                "content": user_display_content,
                "timestamp": time.time(),
                "status": "confirmed"
            }
            if user_display_content != user_agent_content:
                accepted_msg["displayContent"] = user_display_content
                accepted_msg["agentContent"] = user_agent_content
            if user_display_segments is not None:
                accepted_msg["displaySegments"] = user_display_segments
            if user_reply_references is not None:
                accepted_msg["replyReferences"] = user_reply_references
            if data.get("images"):
                accepted_msg["images"] = data["images"]
            await broadcast_to_session(early_save_id, {
                "type": "message_accepted",
                "sessionId": early_save_id,
                "message": accepted_msg
            })
            logger.info(f"BROADCAST: message_accepted to session {early_save_id}")

            # The canonical user message was already added to streaming state
            # immediately after conv.add_message(). The accepted_msg broadcast is
            # only for clients; appending it here duplicates display_messages on save.

            # BROADCAST: chat_created to ALL clients so history list updates in real-time
            if session_id == 'new' and not skip_message_add:
                await broadcast_chat_created(early_save_id, title, agent_name)
                logger.info(f"BROADCAST: chat_created for new chat {early_save_id}")

            # ========== @MENTION DISPATCH: Scan user message for @agent mentions ==========
            user_msg_text = user_display_content
            valid_agents = _get_valid_agent_names()
            # Don't let users @mention the primary agent of this chat (they're already responding)
            if agent_name and agent_name in valid_agents:
                valid_agents.discard(agent_name)
            mentions = parse_mentions(user_msg_text, valid_agents)
            if mentions:
                logger.info(f"MENTION: User message mentions agents: {mentions}")
                asyncio.create_task(_dispatch_mention_agents(
                    session_id=early_save_id,
                    chat_id=early_save_id,
                    agent_names=mentions[:3],
                    context_messages=conv.messages.copy(),
                    trigger_text=user_msg_text,
                    trigger_role="user",
                    primary_agent=agent_name or "character",
                ))

            # ========== CHAT TITLER: Fire early, in parallel with main agent ==========
            # Titler is a fast Haiku call (~1-2s); main agent often runs 10-60s with tools.
            # Firing here (instead of end-of-turn) means the title populates the UI while
            # the agent is still streaming, rather than arriving after everything is done.
            # The titler broadcasts its own `chat_title_update` WebSocket message when done,
            # so it updates the UI independently of the main turn lifecycle.
            if not skip_message_add:
                try:
                    # Increment exchange count even when the titler is paused so unpausing
                    # resumes the normal cadence without a catch-up title run.
                    conv.exchange_count += 1
                    exchange_count = conv.exchange_count

                    if _chat_titler_paused(helper_settings):
                        logger.info(f"Titler: paused for chat {early_save_id}; skipping title trigger")
                    else:
                        from chat_titler import should_retitle

                        # Get current title (existing is already loaded above)
                        current_title = existing.get("title") if existing else None

                        # First exchange: always generate title based on user intent.
                        # Every N exchanges: check if title should update.
                        if exchange_count == 1:
                            logger.info(f"Titler: First exchange, generating initial title (early-fire)")
                            asyncio.create_task(_run_titler_background(
                                early_save_id,
                                list(conv.messages),
                                None,
                                is_retitle=False
                            ))
                        elif should_retitle(exchange_count, current_title):
                            logger.info(f"Titler: Exchange {exchange_count}, checking for title update (early-fire)")
                            asyncio.create_task(_run_titler_background(
                                early_save_id,
                                list(conv.messages),
                                current_title,
                                is_retitle=True
                            ))
                except Exception as e:
                    logger.debug(f"Titler trigger failed: {e}")

        except Exception as e:
            logger.warning(f"EARLY_SAVE failed: {e}")

    # Broadcast status to let clients know we're starting (client handles fun phrase display)
    # Use streaming_state_key which should be set correctly by now
    await broadcast_to_session(streaming_state_key, {"type": "status", "text": ""})

    # ========== AGENT ROUTING: Determine target agent ==========
    agent_name = data.get("agent")  # Set by WS handler for new chats; propagated for existing chats
    agent_config = None

    if not agent_name:
        # Check stored chat data for existing conversations
        stored_chat_id = early_chat_id or preserve_chat_id or (session_id if session_id != "new" else None)
        if stored_chat_id:
            stored = chat_manager.load_chat(stored_chat_id)
            if stored:
                agent_name = stored.get("agent")

    # Look up agent config from registry — ALL agents go through this path
    try:
        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
        if str(agents_dir) not in sys.path:
            sys.path.insert(0, str(agents_dir))
        from registry import get_registry
        registry = get_registry()

        if not agent_name:
            # No agent specified — use the default agent
            default_config = registry.get_default_agent()
            if default_config:
                agent_config = default_config
                agent_name = default_config.name
        else:
            agent_config = registry.get(agent_name)
            if agent_config and not agent_config.chattable:
                logger.warning(f"Agent '{agent_name}' is not chattable, falling back to default")
                default_config = registry.get_default_agent()
                if default_config:
                    agent_config = default_config
                    agent_name = default_config.name
                else:
                    agent_config = None
                    agent_name = None
    except Exception as e:
        logger.warning(f"Failed to load agent config for '{agent_name}': {e}")
        agent_config = None
        agent_name = None

    # Create wrapper and run
    chat_id_for_wrapper = early_chat_id or preserve_chat_id or (session_id if session_id != "new" else None)
    claude = ClaudeWrapper(
        session_id=effective_session_id,
        cwd=ROOT_DIR,
        chat_id=chat_id_for_wrapper,
        chat_messages=conv.messages,
        restart_consumer="main_streaming_finalizer",
    )

    # Track the active wrapper for interrupt capability
    wrapper_key = streaming_state_key or effective_session_id
    active_claude_wrappers[wrapper_key] = claude

    # Track message segments - each tool use creates a new segment
    current_segment = []  # Current text accumulator
    all_segments = []     # List of finalized text segments
    new_session_id = None
    current_tool_name = None
    had_error = False
    restart_after_save = False  # Set when restart_server tool completes — halts stream
    restart_trigger_time = 0.0
    # Tool call history tracking
    # pending_tool_calls: stash tool_use args until tool_end pairs them
    # completed_tool_calls: list of (segment_index, serialized_tool_call) for interleaving
    pending_tool_calls: Dict[str, dict] = {}  # tool_id -> {name, args}
    completed_tool_calls: list = []  # [(segment_index, tool_call_dict), ...]

    # Form messages to persist (appended when forms_show broadcasts successfully)
    # Each entry: (segment_index, form_message_dict)
    completed_form_messages: list = []

    def finalize_segment():
        """Save current segment if it has content (for disk persistence path).
        Live streaming state is managed by the block model, not here."""
        nonlocal current_segment
        if current_segment:
            text = "".join(current_segment).strip()
            if text:
                all_segments.append(text)
            current_segment = []

    claimed_notification_ids: List[str] = []

    # Inject pending agent notifications into the prompt (not system prompt)
    # This ensures notifications are visible even when resuming SDK sessions
    # where the system prompt may be cached from session creation
    try:
        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
        if str(agents_dir) not in sys.path:
            sys.path.insert(0, str(agents_dir))
        from agent_notifications import get_notification_queue

        queue = get_notification_queue()
        pending_for_chat = queue.get_pending(chat_id=streaming_state_key)
        claimed = queue.claim_by_ids([n.id for n in pending_for_chat])

        if claimed:
            claimed_notification_ids = [n.id for n in claimed]
            notification_block = queue.format_for_working_memory(claimed)
            # User sent during the ping buffer: deliver the completed replies as
            # working-memory-shaped context on this wake, then mark delivered
            # only after the turn is saved and broadcast.
            prompt = f"{notification_block}\n\n[User's message follows]\n{prompt}"
            logger.info(f"Injected {len(claimed)} agent notifications into user prompt as working memory")
    except Exception as e:
        logger.debug(f"Could not inject agent notifications into prompt: {e}")

    # ========== IMAGE HANDLING: Build structured content blocks if images present ==========
    image_refs = data.get("images", [])
    if image_refs:
        # Convert the text prompt + images into structured content blocks
        # This is the Anthropic API format for multimodal messages
        content_blocks = [{"type": "text", "text": prompt}]

        for img_ref in image_refs:
            try:
                img_filename = img_ref.get("filename", "")
                img_type = img_ref.get("type", "image/png")
                img_path = os.path.join(CHAT_IMAGES_DIR, os.path.basename(img_filename))

                if os.path.exists(img_path):
                    with open(img_path, "rb") as f:
                        img_data = base64.standard_b64encode(f.read()).decode("utf-8")

                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img_type,
                            "data": img_data,
                        }
                    })
                    logger.info(f"IMAGE: Added {img_filename} ({img_type}) as content block")
                else:
                    logger.warning(f"IMAGE: File not found: {img_path}")
            except Exception as e:
                logger.warning(f"IMAGE: Failed to process image {img_ref}: {e}")

        # Use structured content blocks instead of plain text prompt
        prompt_for_sdk = content_blocks
        logger.info(f"IMAGE: Sending {len(content_blocks)} content blocks ({len(image_refs)} images)")
    else:
        prompt_for_sdk = prompt

    try:
        # All agents route through run_chat()
        if not agent_config:
            logger.error("No agent config available — this should not happen after Phase 3")
            # Emergency fallback: try to get default from registry
            try:
                from registry import get_registry
                agent_config = get_registry().get_default_agent()
            except Exception:
                pass

        if agent_config:
            prompt_gen = claude.run_chat(
                prompt_for_sdk,
                agent_config=agent_config,
                conversation_history=conv.messages,
                session_id=chat_id_for_wrapper,
                helper_settings=helper_settings,
            )
        else:
            raise RuntimeError("No agent config available and no default agent found")

        async for event in prompt_gen:
            event_type = event.get("type")
            logger.info(f"EVENT: {event_type}")

            if event_type == "session_init":
                new_session_id = event.get("id")
                logger.info(f"SESSION_INIT: is_system_continuation={is_system_continuation}, preserve_chat_id={preserve_chat_id}")
                if new_session_id:
                    # For system continuations (restart), keep the original session ID
                    # so client stays on the same chat
                    if is_system_continuation and preserve_chat_id:
                        logger.info(f"RESTART CONTINUATION: Keeping original session {preserve_chat_id} (SDK gave {new_session_id})")
                        conv.session_id = preserve_chat_id
                        active_conversations[preserve_chat_id] = conv
                        # Register client and BROADCAST session_init with the ORIGINAL ID
                        register_client(websocket, preserve_chat_id)
                        await broadcast_to_session(preserve_chat_id, {
                            "type": "session_init",
                            "id": preserve_chat_id,
                            "agent": agent_name
                        })
                    else:
                        # CRITICAL: Use preserve_chat_id if available (from EARLY_SAVE)
                        # This ensures client and server use the same ID for state tracking
                        effective_chat_id = preserve_chat_id or new_session_id
                        conv.session_id = effective_chat_id
                        active_conversations[effective_chat_id] = conv
                        # Register client and BROADCAST the effective chat ID
                        register_client(websocket, effective_chat_id)
                        await broadcast_to_session(effective_chat_id, {
                            "type": "session_init",
                            "id": effective_chat_id,
                            "agent": agent_name
                        })

                    # ========== WAL: Start tracking streaming response ==========
                    streaming_chat_id = preserve_chat_id or new_session_id
                    if not is_system_continuation:
                        wal.start_streaming(new_session_id, streaming_chat_id, msg_id)

                    # ========== CRITICAL: Migrate streaming state to actual session ID ==========
                    # The state was initialized with streaming_state_key (possibly 'new')
                    # Now we have the real chat ID - migrate the state
                    actual_state_key = preserve_chat_id or new_session_id
                    if streaming_state_key != actual_state_key:
                        if streaming_state_key in session_streaming_states:
                            session_streaming_states[actual_state_key] = session_streaming_states.pop(streaming_state_key)
                            logger.info(f"STREAMING_STATE: Migrated from {streaming_state_key} to {actual_state_key}")
                        else:
                            # Fallback: old state was lost, create fresh
                            _fallback_msgs = []
                            _fb_chat = chat_manager.load_chat(actual_state_key)
                            if _fb_chat:
                                _fallback_msgs = list(_messages_for_display(_fb_chat, actual_state_key))
                            session_streaming_states[actual_state_key] = SessionStreamingState(
                                status="streaming",
                                messages=_fallback_msgs,
                                _has_full_history=bool(_fallback_msgs),
                            )
                            logger.info(f"STREAMING_STATE: Created for {actual_state_key}")
                    # Update active session tracking to the actual ID — keep
                    # the same running_agents entry, just rekey + update.
                    await _record_chat_session_rekey(streaming_state_key, actual_state_key)
                    logger.info(f"STREAMING_STATE: active session now {actual_state_key}")

            elif event_type == "content_delta":
                # Streaming text delta - broadcast to ALL clients viewing this session
                text = event.get("text", "")
                if text:
                    current_segment.append(text)
                    # Also track in conv for restart continuity
                    conv.pending_response = all_segments + ["".join(current_segment)]
                    # ========== WAL: Checkpoint streaming content ==========
                    if not is_system_continuation:
                        wal.append_content(new_session_id or effective_session_id, text)
                    # ========== Block model: create/append to text block ==========
                    state_key = preserve_chat_id or new_session_id or streaming_state_key
                    ss = session_streaming_states.get(state_key)
                    if ss:
                        msg_id_blk, msg_is_new = ss.get_or_create_assistant_message()
                        events_to_broadcast = []
                        if msg_is_new:
                            events_to_broadcast.append({
                                "type": "message_start",
                                "seq": ss._next_seq(),
                                "sessionId": state_key,
                                "message_id": msg_id_blk,
                                "role": "assistant"
                            })
                        # Complete any in-progress thinking blocks (thinking → text transition)
                        # This ensures the thinking timer stops when text generation begins.
                        for blk in ss._current_blocks:
                            if blk.type == "thinking" and blk.status == "in_progress":
                                blk.status = "complete"
                                meta = {}
                                if blk.started_at:
                                    blk.duration_ms = int((time.time() - blk.started_at) * 1000)
                                    meta["duration_ms"] = blk.duration_ms
                                events_to_broadcast.append({
                                    "type": "block_end",
                                    "seq": ss._next_seq(),
                                    "sessionId": state_key,
                                    "message_id": msg_id_blk,
                                    "block_id": blk.id,
                                    "metadata": meta if meta else None
                                })
                        block, block_is_new = ss.get_or_create_block("text")
                        if block_is_new:
                            events_to_broadcast.append({
                                "type": "block_start",
                                "seq": ss._next_seq(),
                                "sessionId": state_key,
                                "message_id": msg_id_blk,
                                "block": block.to_dict()
                            })
                        block.content += text
                        events_to_broadcast.append({
                            "type": "block_delta",
                            "seq": ss._next_seq(),
                            "sessionId": state_key,
                            "message_id": msg_id_blk,
                            "block_id": block.id,
                            "delta": text
                        })
                        for evt in events_to_broadcast:
                            await broadcast_to_session(state_key, evt)

            elif event_type == "thinking_delta":
                text = event.get("text", "")
                if text:
                    state_key = preserve_chat_id or new_session_id or streaming_state_key
                    ss = session_streaming_states.get(state_key)
                    if ss:
                        msg_id_blk, msg_is_new = ss.get_or_create_assistant_message()
                        events_to_broadcast = []
                        if msg_is_new:
                            events_to_broadcast.append({
                                "type": "message_start",
                                "seq": ss._next_seq(),
                                "sessionId": state_key,
                                "message_id": msg_id_blk,
                                "role": "assistant"
                            })
                        block, block_is_new = ss.get_or_create_block("thinking")
                        if block_is_new:
                            events_to_broadcast.append({
                                "type": "block_start",
                                "seq": ss._next_seq(),
                                "sessionId": state_key,
                                "message_id": msg_id_blk,
                                "block": block.to_dict()
                            })
                        block.content += text
                        events_to_broadcast.append({
                            "type": "block_delta",
                            "seq": ss._next_seq(),
                            "sessionId": state_key,
                            "message_id": msg_id_blk,
                            "block_id": block.id,
                            "delta": text
                        })
                        for evt in events_to_broadcast:
                            await broadcast_to_session(state_key, evt)

            elif event_type == "tool_start":
                # Finalize any content segment before tool starts (for disk persistence)
                finalize_segment()
                current_tool_name = event.get("name", "tool")
                tool_id = event.get("id")
                # ========== Defensive stash from tool_start ==========
                if tool_id and tool_id not in pending_tool_calls:
                    pending_tool_calls[tool_id] = {"name": current_tool_name, "args": "{}"}
                # ========== WAL: Track tool in progress ==========
                if not is_system_continuation:
                    wal.set_tool_in_progress(new_session_id or effective_session_id, current_tool_name)
                    wal.new_segment(new_session_id or effective_session_id)
                # ========== Block model: complete text blocks, create tool_use block ==========
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                ss = session_streaming_states.get(state_key)
                if ss:
                    msg_id_blk, msg_is_new = ss.get_or_create_assistant_message()
                    events_to_broadcast = []
                    if msg_is_new:
                        events_to_broadcast.append({
                            "type": "message_start",
                            "seq": ss._next_seq(),
                            "sessionId": state_key,
                            "message_id": msg_id_blk,
                            "role": "assistant"
                        })
                    # Complete any open text/thinking blocks
                    events_to_broadcast.extend(ss.complete_trailing_blocks())
                    # Note: do NOT complete other in_progress tool_use blocks here —
                    # multiple tools can run in parallel. Each tool_end event will
                    # complete its own block by matching tool_call_id.
                    # Create tool_use block
                    block = ContentBlock(
                        type="tool_use",
                        tool_name=current_tool_name,
                        tool_call_id=tool_id,
                        status="in_progress",
                        started_at=time.time()
                    )
                    ss._current_blocks.append(block)
                    events_to_broadcast.append({
                        "type": "block_start",
                        "seq": ss._next_seq(),
                        "sessionId": state_key,
                        "message_id": msg_id_blk,
                        "block": block.to_dict()
                    })
                    for evt in events_to_broadcast:
                        await broadcast_to_session(state_key, evt)
                # ========== START HEARTBEAT for long-running tools ==========
                start_tool_heartbeat(state_key, current_tool_name)

            elif event_type == "tool_use":
                # tool_use gives us full args — update existing block or create if tool_start missed
                finalize_segment()
                current_tool_name = event.get("name", "tool")
                tool_id = event.get("id")
                tool_args = event.get("args", "{}")
                # ========== WAL: Track tool in progress ==========
                if not is_system_continuation:
                    wal.set_tool_in_progress(new_session_id or effective_session_id, current_tool_name)
                    wal.new_segment(new_session_id or effective_session_id)
                state_key = preserve_chat_id or new_session_id or streaming_state_key

                # ========== Track forms_show args for later broadcast ==========
                if current_tool_name and current_tool_name.endswith("forms_show"):
                    logger.info(f"FORMS_SHOW detected in tool_use, tracking args for state_key={state_key}")
                    try:
                        args_str = tool_args
                        args_data = json.loads(args_str) if isinstance(args_str, str) else args_str
                        pending_form_requests[state_key] = {
                            "form_id": args_data.get("form_id"),
                            "prefill": args_data.get("prefill", {})
                        }
                    except Exception:
                        pass

                # ========== Stash tool call for history serialization (disk save path) ==========
                if tool_id:
                    pending_tool_calls[tool_id] = {"name": current_tool_name, "args": tool_args}

                # ========== TodoWrite: Broadcast todo state to UI ==========
                if current_tool_name == "TodoWrite":
                    try:
                        todo_args = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
                        todo_list = todo_args.get("todos", [])
                        if todo_list and state_key in session_streaming_states:
                            session_streaming_states[state_key].todos = todo_list
                        await broadcast_to_session(state_key, {
                            "type": "todo_update",
                            "todos": todo_list
                        })
                        logger.info(f"TODO_UPDATE: Broadcast {len(todo_list)} todos to session {state_key}")
                    except Exception as e:
                        logger.warning(f"TODO_UPDATE: Failed to parse TodoWrite args: {e}")

                # ========== set_theme: Broadcast theme update to ALL connected clients ==========
                if current_tool_name and current_tool_name.endswith("set_theme"):
                    try:
                        theme_args = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
                        # Read updated preferences from disk (the tool writes them)
                        # We broadcast to ALL clients, not just the session
                        theme_payload = {}
                        if theme_args.get("accent_color"):
                            from mcp_tools.utilities.set_theme import resolve_color
                            color, hover = resolve_color(
                                theme_args["accent_color"],
                                theme_args.get("accent_hover")
                            )
                            theme_payload["accentColor"] = color
                            theme_payload["accentHover"] = hover
                        if theme_args.get("mode"):
                            theme_payload["mode"] = theme_args["mode"]

                        if theme_payload:
                            # Broadcast to ALL connected clients (theme is global)
                            await broadcast_to_all_clients({
                                "type": "theme_update",
                                "theme": theme_payload
                            })
                            logger.info(f"THEME_UPDATE: Broadcast to {len(client_sessions)} clients: {theme_payload}")
                    except Exception as e:
                        logger.warning(f"THEME_UPDATE: Failed to broadcast: {e}")

                # ========== leave_on_desk: Broadcast file open to ALL connected clients ==========
                if current_tool_name and current_tool_name.endswith("leave_on_desk"):
                    try:
                        desk_args = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
                        file_path = desk_args.get("file_path", "")
                        reason = desk_args.get("reason", "")
                        if file_path:
                            await broadcast_to_all_clients({
                                "type": "leave_on_desk",
                                "file_path": file_path,
                                "reason": reason,
                            })
                            logger.info(f"LEAVE_ON_DESK: Broadcast to {len(client_sessions)} clients: {file_path}")
                    except Exception as e:
                        logger.warning(f"LEAVE_ON_DESK: Failed to broadcast: {e}")

                # ========== Block model: update existing tool_use block with args ==========
                ss = session_streaming_states.get(state_key)
                if ss and tool_id:
                    # Find existing tool_use block (created by tool_start) and update with args
                    found = False
                    for block in reversed(ss._current_blocks):
                        if block.type == "tool_use" and block.tool_call_id == tool_id:
                            try:
                                block.tool_input = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
                            except Exception:
                                block.tool_input = {"raw": tool_args}
                            await broadcast_to_session(state_key, {
                                "type": "block_update",
                                "seq": ss._next_seq(),
                                "sessionId": state_key,
                                "message_id": ss._current_msg_id,
                                "block_id": block.id,
                                "block": block.to_dict()
                            })
                            found = True
                            break
                    if not found:
                        # Fallback: tool_start didn't fire — create the block now
                        msg_id_blk, msg_is_new = ss.get_or_create_assistant_message()
                        events_to_broadcast = []
                        if msg_is_new:
                            events_to_broadcast.append({
                                "type": "message_start",
                                "seq": ss._next_seq(),
                                "sessionId": state_key,
                                "message_id": msg_id_blk,
                                "role": "assistant"
                            })
                        events_to_broadcast.extend(ss.complete_trailing_blocks())
                        try:
                            parsed_input = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
                        except Exception:
                            parsed_input = {"raw": tool_args}
                        block = ContentBlock(
                            type="tool_use",
                            tool_name=current_tool_name,
                            tool_call_id=tool_id,
                            tool_input=parsed_input,
                            status="in_progress",
                            started_at=time.time()
                        )
                        ss._current_blocks.append(block)
                        events_to_broadcast.append({
                            "type": "block_start",
                            "seq": ss._next_seq(),
                            "sessionId": state_key,
                            "message_id": msg_id_blk,
                            "block": block.to_dict()
                        })
                        for evt in events_to_broadcast:
                            await broadcast_to_session(state_key, evt)

                # ========== START HEARTBEAT for long-running tools ==========
                start_tool_heartbeat(state_key, current_tool_name)

            elif event_type == "tool_end":
                logger.info(f"TOOL_END event received: name={event.get('name')}, id={event.get('id')}, is_error={event.get('is_error')}")
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                tool_end_id = event.get("id")
                tool_name = event.get("name", "")
                tool_end_output = event.get("output", "")
                tool_output_raw = str(tool_end_output or "")
                is_error = event.get("is_error", False)
                raw_output_artifact = None
                try:
                    raw_output_artifact = maybe_write_raw_tool_output_artifact(
                        chat_id=state_key or effective_session_id or new_session_id or "unknown-chat",
                        tool_call_id=tool_end_id or f"tool-{int(time.time() * 1000)}",
                        tool_name=tool_name or "tool",
                        output=tool_output_raw,
                        is_error=bool(is_error),
                    )
                    if raw_output_artifact:
                        raw_output_artifact = with_truncation_flags(
                            raw_output_artifact,
                            display_truncated=len(tool_output_raw) > DEFAULT_DISPLAY_LIMIT_CHARS,
                            history_truncated=len(tool_output_raw) > 500,
                        )
                except Exception as artifact_err:
                    logger.warning(f"Tool output artifact write failed: {artifact_err}")
                    raw_output_artifact = None

                # ========== Block model: complete tool_use block, add tool_result ==========
                ss = session_streaming_states.get(state_key)
                if ss:
                    events_to_broadcast = []
                    # Complete the tool_use block
                    for block in ss._current_blocks:
                        if block.type == "tool_use" and block.tool_call_id == tool_end_id:
                            block.status = "complete"
                            if block.started_at:
                                block.duration_ms = int((time.time() - block.started_at) * 1000)
                            events_to_broadcast.append({
                                "type": "block_end",
                                "seq": ss._next_seq(),
                                "sessionId": state_key,
                                "message_id": ss._current_msg_id,
                                "block_id": block.id,
                                "metadata": {"duration_ms": block.duration_ms} if block.duration_ms else None
                            })
                            break
                    # Add tool_result block (created already complete)
                    result_block = ContentBlock(
                        type="tool_result",
                        tool_call_id=tool_end_id,
                        content=compact_tool_output_for_display(tool_output_raw, raw_output_artifact),
                        is_error=is_error,
                        raw_output=raw_output_artifact,
                        status="complete"
                    )
                    ss._current_blocks.append(result_block)
                    events_to_broadcast.append({
                        "type": "block_start",
                        "seq": ss._next_seq(),
                        "sessionId": state_key,
                        "message_id": ss._current_msg_id,
                        "block": result_block.to_dict()
                    })
                    for evt in events_to_broadcast:
                        await broadcast_to_session(state_key, evt)
                    # Check if all tool_use blocks are complete — stop heartbeat if so
                    has_active = any(
                        b.type == "tool_use" and b.status == "in_progress"
                        for b in ss._current_blocks
                    )
                    if not has_active:
                        stop_tool_heartbeat(state_key)
                else:
                    stop_tool_heartbeat(state_key)

                # ========== Check for forms_show tool completion ==========
                if tool_name.endswith("forms_show") and not is_error and state_key in pending_form_requests:
                    try:
                        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.claude/scripts"))
                        if scripts_dir not in sys.path:
                            sys.path.insert(0, scripts_dir)
                        from theo_ports.utils.forms_store import get_form

                        pending = pending_form_requests.pop(state_key, {})
                        form_id = pending.get("form_id")
                        prefill = pending.get("prefill", {})

                        if form_id:
                            form = get_form(form_id)
                            if form:
                                form_payload = {
                                    "type": "form_request",
                                    "formId": form_id,
                                    "title": form.get("title", form_id),
                                    "description": form.get("description", ""),
                                    "fields": form.get("fields", []),
                                    "prefill": prefill,
                                    "version": form.get("version", 1)
                                }
                                logger.info(f"Broadcasting form_request for '{form_id}' to session {state_key}")

                                # Store in streaming state for reconnecting clients
                                # Append to list so multiple forms can coexist
                                if state_key in session_streaming_states:
                                    session_streaming_states[state_key].pending_forms.append(form_payload)

                                await broadcast_to_session(state_key, form_payload)

                                # Stash for persistence into conv.messages at turn end
                                form_msg = {
                                    "id": f"form-{form_id}-{int(time.time() * 1000)}",
                                    "role": "assistant",
                                    "content": "",
                                    "formData": {
                                        "formId": form_id,
                                        "title": form.get("title", form_id),
                                        "description": form.get("description", ""),
                                        "fields": form.get("fields", []),
                                        "prefill": prefill,
                                        "status": "pending"
                                    }
                                }
                                completed_form_messages.append((len(all_segments), form_msg))
                                logger.info(f"Stashed form message for persistence: {form_id}")
                    except Exception as form_err:
                        logger.warning(f"Error broadcasting form_request: {form_err}")

                # ========== Check for chess tool completion ==========
                if tool_name.endswith("chess") and not is_error:
                    try:
                        chess_update_file = os.path.join(ROOT_DIR, ".claude", "chess", "pending_update.json")
                        if os.path.exists(chess_update_file):
                            with open(chess_update_file, 'r') as f:
                                chess_game = json.load(f)
                            os.remove(chess_update_file)
                            logger.info(f"Broadcasting chess_update to all clients")
                            await broadcast_to_all_clients({
                                "type": "chess_update",
                                "game": chess_game
                            })
                    except Exception as chess_err:
                        logger.warning(f"Error broadcasting chess_update: {chess_err}")

                # ========== Serialize tool call for disk persistence history ==========
                tool_end_name = tool_name
                tool_end_error = is_error
                stashed = pending_tool_calls.pop(tool_end_id, None) if tool_end_id else None
                if stashed:
                    try:
                        tc = serialize_tool_call(
                            tool_name=stashed["name"],
                            args_raw=stashed["args"],
                            output=tool_output_raw,
                            is_error=tool_end_error,
                            tool_id=tool_end_id,
                            raw_output=raw_output_artifact,
                        )
                        tc["timestamp"] = int(time.time())
                        completed_tool_calls.append((len(all_segments), tc))
                    except Exception as ser_err:
                        logger.warning(f"Tool serialization error: {ser_err}")
                elif tool_end_name:
                    try:
                        tc = serialize_tool_call(
                            tool_name=tool_end_name,
                            args_raw={},
                            output=tool_output_raw,
                            is_error=tool_end_error,
                            tool_id=tool_end_id,
                            raw_output=raw_output_artifact,
                        )
                        tc["timestamp"] = int(time.time())
                        completed_tool_calls.append((len(all_segments), tc))
                        logger.info(f"Tool call recorded without args (streaming-only): {tool_end_name}")
                    except Exception as ser_err:
                        logger.warning(f"Tool serialization fallback error: {ser_err}")

                current_tool_name = None
                # ========== WAL: Clear tool in progress ==========
                if not is_system_continuation:
                    wal.set_tool_in_progress(new_session_id or effective_session_id, None)

                # ========== RESTART: Halt streaming after restart_server tool ==========
                # When the restart_server tool completes, we break out of the streaming
                # loop immediately. This prevents the model from generating more text
                # (which would be lost when the server dies). The normal finalization
                # code below will do a clean save of display_messages + conv.messages,
                # and THEN spawn the restart subprocess.
                _restart_tool_name = tool_name or ""
                if _restart_tool_name.startswith("mcp__brain__"):
                    _restart_tool_name = _restart_tool_name[len("mcp__brain__"):]
                if _restart_tool_name == "restart_server":
                    if _restart_tool_result_allows_finalizer(tool_end_output, is_error):
                        logger.info("RESTART: restart_server tool completed — halting stream for clean save")
                        restart_after_save = True
                        restart_trigger_time = time.time()
                        break
                    logger.warning(
                        "RESTART: restart_server tool_end did not establish a restart "
                        "marker contract; continuing stream (is_error=%s)",
                        is_error,
                    )

            elif event_type == "error":
                had_error = True
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                await broadcast_to_session(state_key, event)

            elif event_type == "result_meta":
                # Accumulate token usage for this conversation
                # Note: SDK reports cumulative usage across all API calls in an
                # agentic loop, so these numbers reflect total billing tokens,
                # NOT actual context window size.
                turn_usage = event.get("usage", {})
                turn_input = turn_usage.get("input_tokens", 0)
                turn_output = turn_usage.get("output_tokens", 0)
                cache_read = turn_usage.get("cache_read_input_tokens", 0)
                cache_creation = turn_usage.get("cache_creation_input_tokens", 0)

                total_input = turn_input + cache_read + cache_creation
                conv.cumulative_usage["input_tokens"] = total_input
                conv.cumulative_usage["output_tokens"] += turn_output
                conv.cumulative_usage["total_tokens"] = (
                    total_input + conv.cumulative_usage["output_tokens"]
                )

                # Broadcast to clients
                cumulative_event = {
                    **event,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": conv.cumulative_usage["output_tokens"],
                        "total_tokens": conv.cumulative_usage["total_tokens"],
                        "cache_read_input_tokens": cache_read,
                    }
                }
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                await broadcast_to_session(state_key, cumulative_event)

                # Update session ID if changed
                if event.get("session_id"):
                    new_session_id = event.get("session_id")
                    conv.session_id = new_session_id

    except WebSocketDisconnect as e:
        # Client disconnected mid-response - PROCESSING CONTINUES
        # With background task architecture, this rarely happens (broadcasts catch exceptions)
        # But if it does, we just log it and continue - response will still be saved
        logger.info(f"WebSocket disconnected mid-response (code={e.code}): {e.reason or 'no reason'} - continuing processing")
        # DON'T set had_error = True - processing should complete normally
        # ========== WAL: Force checkpoint on disconnect ==========
        if not is_system_continuation:
            wal.append_content(new_session_id or effective_session_id, "", force_checkpoint=True)
    except Exception as e:
        error_msg = str(e) or type(e).__name__
        logger.error(f"Error processing Claude response: {error_msg}")
        had_error = True
        # ========== WAL: Mark message as failed ==========
        if not is_system_continuation:
            wal.fail_message(msg_id, error_msg)
        try:
            await websocket.send_json({"type": "error", "text": error_msg})
        except Exception:
            # WebSocket may be closed - that's ok, we logged the error
            pass
    finally:
        # Clean up the active wrapper immediately
        if wrapper_key in active_claude_wrappers:
            del active_claude_wrappers[wrapper_key]
            logger.info(f"Cleaned up wrapper for {wrapper_key}")

    # Finalize any remaining content (for disk persistence)
    finalize_segment()
    logger.info(f"COMPLETE: {len(all_segments)} segments, had_error={had_error}")

    # ========== Block model: finalize turn — complete blocks, mark message done ==========
    _final_state_key = preserve_chat_id or new_session_id or streaming_state_key
    ss = session_streaming_states.get(_final_state_key)
    if ss:
        try:
            finalize_events = ss.finalize_turn()
            for evt in finalize_events:
                evt["sessionId"] = _final_state_key
                await broadcast_to_session(_final_state_key, evt)
        except Exception as e:
            logger.error(f"finalize_turn() failed (will still clean up streaming state): {e}", exc_info=True)

    # Add message segments interleaved with tool calls and form messages to conv.messages
    # Tool calls and forms are tagged with the segment index they occurred AFTER,
    # so a tool at segment_index=0 means it ran after segment 0.
    tc_by_seg = defaultdict(list)
    for seg_idx, tc in completed_tool_calls:
        tc_by_seg[seg_idx].append(tc)

    form_by_seg = defaultdict(list)
    for seg_idx, fm in completed_form_messages:
        form_by_seg[seg_idx].append(fm)

    for i, segment in enumerate(all_segments):
        # First, insert any tool calls that ran before this segment
        # (tool calls at index i ran between segment i and segment i+1,
        #  but they're captured BEFORE the next segment is finalized)
        for tc in tc_by_seg.get(i, []):
            conv.messages.append(tc)
        # Insert any form messages that were broadcast during this segment's tools
        for fm in form_by_seg.get(i, []):
            conv.messages.append(fm)
        conv.add_message("assistant", segment)
        logger.info(f"SEGMENT: {segment[:50]}...")

    # Any tool calls/forms after the last segment (e.g., tool ran but no text followed)
    for tc in tc_by_seg.get(len(all_segments), []):
        conv.messages.append(tc)
    for fm in form_by_seg.get(len(all_segments), []):
        conv.messages.append(fm)

    if completed_tool_calls:
        logger.info(f"TOOL_HISTORY: Saved {len(completed_tool_calls)} tool calls to chat history")
    if completed_form_messages:
        logger.info(f"FORM_HISTORY: Saved {len(completed_form_messages)} form messages to chat history")

    # Determine chat ID for storage
    # Priority: preserve_chat_id (for edits) > new_session_id (from SDK) > generate new UUID
    logger.info(f"SAVE: preserve_chat_id={preserve_chat_id}, new_session_id={new_session_id}, session_id={session_id}")

    if preserve_chat_id:
        chat_id_for_storage = preserve_chat_id
    elif new_session_id and new_session_id != "new":
        chat_id_for_storage = new_session_id
    elif session_id and session_id != "new":
        chat_id_for_storage = session_id
    else:
        # Generate a new UUID if we don't have a valid session ID
        chat_id_for_storage = str(uuid.uuid4())
        logger.info(f"SAVE: Generated new chat ID: {chat_id_for_storage}")

    logger.info(f"SAVE: chat_id_for_storage={chat_id_for_storage}")

    # Always save (we now always have a valid ID)
    existing = chat_manager.load_chat(chat_id_for_storage)
    if existing is None or not existing.get("title"):
        # For edits, use original title; for new chats, generate from prompt
        original_prompt = data.get("message", prompt)  # Use original, not context-wrapped
        title = chat_manager.generate_title(original_prompt)
    else:
        title = existing.get("title", "Untitled")

    final_save_data = {
        "title": title,
        "sessionId": chat_id_for_storage,
        "messages": conv.messages,
        "cumulative_usage": conv.cumulative_usage,
        "helper_settings": helper_settings
    }
    if agent_name:
        final_save_data["agent"] = agent_name

    # Save display_messages (block-structured) for UI persistence.
    # This preserves thinking blocks, tool call metadata, and per-block rendering
    # across page reloads and server restarts.
    #
    # IMPORTANT: SS uses a block model (1 assistant message per turn with multiple
    # blocks for thinking/text/tool_use/tool_result), while conv.messages uses a flat
    # model (multiple assistant messages per turn, one per tool cycle). These counts
    # CANNOT be compared — SS will always have fewer messages than conv.messages for
    # conversations with tool use. Instead, we use the _has_full_history flag to decide.
    _save_ss_key = preserve_chat_id or new_session_id or streaming_state_key
    _save_ss = session_streaming_states.get(_save_ss_key)
    if _save_ss and _save_ss.messages:
        # Serialize SS messages (they have block data for thinking, tool_use, etc.)
        serialized_ss = []
        for _dm in _save_ss.messages:
            if _dm.get("blocks") is not None:
                _ser_blocks = [
                    b.to_dict() if isinstance(b, ContentBlock) else b
                    for b in _dm["blocks"]
                ]
                serialized_ss.append({
                    "id": _dm["id"],
                    "role": _dm["role"],
                    "content": "",
                    "status": "complete",
                    "blocks": _ser_blocks,
                })
            else:
                # User messages and other non-block messages pass through as-is
                serialized_ss.append(_dm)

        if _save_ss._has_full_history:
            # SS was initialized with full display_messages from disk + current turn.
            # Use it directly — it IS the complete history.
            display_msgs = serialized_ss
            logger.info(f"DISPLAY_MSGS: Using full SS history ({len(serialized_ss)} msgs, "
                        f"{sum(1 for m in serialized_ss if m.get('blocks'))} with blocks)")
        else:
            # SS is partial (late init / restart continuation) — only has current turn.
            # Prepend previous display_messages from disk to get full history.
            old_chat_for_merge = chat_manager.load_chat(chat_id_for_storage)
            old_display = _messages_for_display(old_chat_for_merge, chat_id_for_storage) if old_chat_for_merge else []
            display_msgs = old_display + serialized_ss
            logger.info(f"DISPLAY_MSGS: Merged {len(old_display)} old + {len(serialized_ss)} new SS msgs "
                        f"({sum(1 for m in display_msgs if m.get('blocks'))} with blocks)")

        # Inject form messages — they're persisted in conv.messages but not
        # tracked in SS, so they'd be lost from display_messages on reload.
        # Always cross-check conv.messages for ALL form messages (not just
        # current turn's completed_form_messages) to prevent forms from being
        # lost across subsequent turns.
        all_form_msgs_from_conv = [
            m for m in conv.messages if m.get("formData")
        ]
        if all_form_msgs_from_conv:
            existing_form_ids = {
                m.get("formData", {}).get("formId")
                for m in display_msgs if m.get("formData")
            }
            injected_count = 0
            for fm in all_form_msgs_from_conv:
                fm_id = fm.get("formData", {}).get("formId")
                if fm_id and fm_id not in existing_form_ids:
                    # Insert before the final assistant message so the form
                    # appears in roughly the right position (it was shown
                    # during tool execution, before the final text response).
                    insert_idx = len(display_msgs)
                    for i in range(len(display_msgs) - 1, -1, -1):
                        if display_msgs[i].get("role") == "assistant":
                            insert_idx = i
                            break
                    display_msgs.insert(insert_idx, fm)
                    existing_form_ids.add(fm_id)
                    injected_count += 1
            if injected_count:
                logger.info(f"DISPLAY_MSGS: Injected {injected_count} form messages into display_messages (from conv.messages)")

        # Preserve reactions from old display_messages — the rebuild above
        # creates fresh entries from streaming state, losing any reactions
        # the user added to previous messages
        old_chat = chat_manager.load_chat(chat_id_for_storage)
        if old_chat and old_chat.get("display_messages"):
            old_reactions = {}
            for old_msg in old_chat["display_messages"]:
                if old_msg.get("reactions"):
                    old_reactions[old_msg.get("id")] = old_msg["reactions"]
            if old_reactions:
                for dm in display_msgs:
                    msg_id = dm.get("id")
                    if msg_id and msg_id in old_reactions:
                        dm["reactions"] = old_reactions[msg_id]
                logger.info(f"SAVE: Preserved {len(old_reactions)} message reaction(s)")

        final_save_data["display_messages"] = _display_messages_for_save(
            conv.messages,
            display_msgs,
            chat_id_for_storage,
        )

    chat_manager.save_chat(chat_id_for_storage, final_save_data)

    # ========== @MENTION DISPATCH: Scan assistant response for @agent mentions ==========
    if all_segments:
        final_text = "".join(all_segments)
        valid_agents = _get_valid_agent_names()
        # Don't let the assistant @mention itself
        if agent_name and agent_name in valid_agents:
            valid_agents.discard(agent_name)
        mentions = parse_mentions(final_text, valid_agents)
        if mentions:
            logger.info(f"MENTION: Assistant response mentions agents: {mentions}")
            asyncio.create_task(_dispatch_mention_agents(
                session_id=chat_id_for_storage,
                chat_id=chat_id_for_storage,
                agent_names=mentions[:3],
                context_messages=conv.messages.copy(),
                trigger_text=final_text,
                trigger_role="assistant",
                primary_agent=agent_name or "character",
            ))

    # ========== WAL: Clean up - message fully processed ==========
    if not is_system_continuation:
        wal.complete_message(msg_id)
        wal.complete_streaming(new_session_id or effective_session_id)
        logger.info(f"WAL: Cleaned up WAL entries for message {msg_id}")

    # Update conv's session_id to match storage ID
    conv.session_id = chat_id_for_storage
    active_conversations[chat_id_for_storage] = conv

    # Advance working memory TTL after each completed exchange
    try:
        scripts_dir = os.path.join(ROOT_DIR, ".claude", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from working_memory import get_store
        store = get_store()
        if store.advance_exchange():
            logger.info("Working memory: advanced exchange, some items may have expired")
    except Exception as e:
        logger.debug(f"Working memory advance_exchange failed: {e}")

    # Chat Titler now fires at turn-start (see EARLY_SAVE block above) so the title
    # populates the UI in parallel with the agent's response, not after it.

    # ========== Background processing trigger ==========
    if not is_system_continuation and all_segments and not had_error:
        conv.last_exchange_time = time.time()
        try:
            _maybe_trigger_background_processing(
                chat_id=chat_id_for_storage,
                conv=conv,
                agent_name=agent_name,
            )
        except Exception as e:
            logger.debug(f"BG_PROCESSING: Trigger check failed: {e}")

    # ========== Clear streaming state - processing complete ==========
    state_key = chat_id_for_storage
    # Stop any running heartbeat for this session
    stop_tool_heartbeat(state_key)
    # Capture turn_id before clearing (needed for done event below)
    _done_turn_id = None
    if state_key in session_streaming_states:
        _done_turn_id = session_streaming_states[state_key].turn_id
        del session_streaming_states[state_key]
        logger.info(f"STREAMING_STATE: Cleared for {state_key}")
    # Clean up active edit task tracking
    active_edit_tasks.pop(state_key, None)
    # Remove from active processing and track as recently completed
    if state_key in active_processing_sessions:
        await _record_chat_session_ended(state_key)
        recently_completed_sessions[state_key] = time.time()
        logger.info(f"STREAMING_STATE: Session {state_key} moved to recently_completed")
        # Clean up old entries
        now = time.time()
        expired = [sid for sid, ts in recently_completed_sessions.items() if now - ts > RECENTLY_COMPLETED_TTL]
        for sid in expired:
            del recently_completed_sessions[sid]

    # BROADCAST done event to ALL clients viewing this session
    # Include messages in done event for instant sync (skip client fetch)
    # For very long chats, omit messages and let the client fall back to API fetch
    logger.info(f"DONE: Broadcasting done event with sessionId={chat_id_for_storage}")
    done_payload = {"type": "done", "sessionId": chat_id_for_storage}
    if _done_turn_id:
        done_payload["turnId"] = _done_turn_id
    if len(conv.messages) <= 500:
        done_payload["messages"] = conv.messages
    await broadcast_to_session(chat_id_for_storage, done_payload)

    if claimed_notification_ids:
        try:
            agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
            if str(agents_dir) not in sys.path:
                sys.path.insert(0, str(agents_dir))
            from agent_notifications import get_notification_queue
            queue = get_notification_queue()
            if had_error:
                queue.release_delivery(claimed_notification_ids)
                logger.warning(
                    f"Released {len(claimed_notification_ids)} inline notification(s) "
                    "because the user turn ended with an error"
                )
            else:
                queue.mark_delivered(claimed_notification_ids)
                logger.info(
                    f"Marked {len(claimed_notification_ids)} inline notification(s) delivered"
                )
        except Exception as e:
            logger.error(f"Failed to update inline notification delivery state: {e}")

    # ========== RESTART: Spawn restart subprocess AFTER clean save ==========
    # The restart_server tool wrote config to .claude/pending_restart.json. Now that
    # all state is cleanly saved to disk (display_messages with block model,
    # conv.messages with interleaved segments/tool calls, WAL cleaned up),
    # we can safely kill the server.
    if restart_after_save:
        try:
            import subprocess as _restart_sp
            restart_config = await _load_fresh_pending_restart_config(restart_trigger_time)
            restart_script = restart_config.get("restart_script", "")
            log_file = restart_config.get("log_file", "/tmp/restart.log")
            if restart_script:
                try:
                    os.remove(_PENDING_RESTART_FILE)
                except FileNotFoundError:
                    pass
                logger.info(f"RESTART: Spawning restart subprocess (script={restart_script})")
                _restart_sp.Popen(
                    f"sleep 1 && bash {restart_script} > {log_file} 2>&1",
                    shell=True,
                    start_new_session=True,
                    stdout=_restart_sp.DEVNULL,
                    stderr=_restart_sp.DEVNULL,
                )
            else:
                logger.error("RESTART: No fresh restart_script in pending_restart.json — cannot spawn subprocess")
        except Exception as e:
            logger.error(f"RESTART: Failed to spawn restart subprocess: {e}")


async def handle_edit(websocket: WebSocket, data: dict):
    """Handle editing a previous message."""
    chat_id = data.get("sessionId")  # This is our chat storage ID
    message_id = data.get("messageId")
    new_content = data.get("content", "")

    logger.info(f"EDIT: chat_id={chat_id}, message_id={message_id}")

    if not chat_id or not message_id or not new_content:
        # Error before we have a session - send directly
        await websocket.send_json({"type": "error", "text": "Missing required fields for edit"})
        return

    # Load existing conversation to get context before the edit point
    chat_data = chat_manager.load_chat(chat_id)
    old_messages = chat_data.get("messages", []) if chat_data else []

    # Find the edit point and keep messages BEFORE it
    context_messages = []
    for msg in old_messages:
        if msg.get("id") == message_id:
            break  # Stop before the edited message
        context_messages.append(msg)

    logger.info(f"EDIT: Keeping {len(context_messages)} context messages before edit point")

    # Create new conversation state with the context
    conv = ConversationState()
    conv.session_id = chat_id
    conv.messages = context_messages.copy()  # Messages before the edit
    active_conversations[chat_id] = conv

    # Seed streaming state with context messages so subscribe/snapshot
    # returns the full conversation history if user switches tabs during streaming.
    # Without this, the streaming state (initialized empty by the websocket loop)
    # would only contain the edited user message + assistant response.
    ss = session_streaming_states.get(chat_id)
    if ss is not None:
        ss.messages = list(context_messages)
        # Mark as full history: the context messages + new response IS the complete
        # conversation after edit. Don't merge with pre-edit display_messages from disk.
        ss._has_full_history = True

    # BROADCAST truncation to ALL clients viewing this session
    # This ensures multi-device consistency during edits
    await broadcast_to_session(chat_id, {
        "type": "truncate",
        "messageId": message_id,
        "messages": context_messages,
        "sessionId": chat_id
    })
    logger.info(f"EDIT: Broadcasted truncation to session {chat_id}")

    # Propagate agent from stored chat
    stored_agent = chat_data.get("agent") if chat_data else None
    if stored_agent:
        data["agent"] = stored_agent

    # Send the edited message with a fresh assistant session but WITH context
    data["message"] = new_content
    data["sessionId"] = chat_id
    data["forceNewSession"] = True
    data["preserveChatId"] = chat_id
    data["contextMessages"] = context_messages  # Pass context to handle_message
    await handle_message(websocket, data)


async def handle_regenerate(websocket: WebSocket, data: dict):
    """Handle regenerating the last assistant message."""
    session_id = data.get("sessionId")
    message_id = data.get("messageId")  # The assistant message to regenerate

    if not session_id or not message_id:
        # Error before we have context - send directly
        await websocket.send_json({"type": "error", "text": "Missing required fields for regenerate"})
        return

    # Load from disk to get consistent state
    chat_data = chat_manager.load_chat(session_id)
    if not chat_data:
        # Broadcast error to all clients viewing this session
        await broadcast_to_session(session_id, {"type": "error", "text": "Session not found"})
        return

    old_messages = chat_data.get("messages", [])
    # display_messages may have different IDs than messages (e.g. msg-timestamp-hash
    # vs UUIDs) since they're built from streaming state. The client renders
    # display_messages, so the messageId it sends comes from there.
    display_msgs = chat_data.get("display_messages", [])

    # Find the assistant message to regenerate and the user message before it.
    # Search display_messages first (client IDs come from there), fall back to messages.
    user_message = None
    user_message_id = None
    context_messages = []

    search_arrays = [(display_msgs, "display_messages"), (old_messages, "messages")]
    for search_arr, arr_name in search_arrays:
        if user_message:
            break
        for i, msg in enumerate(search_arr):
            if msg.get("id") == message_id:
                # Found the assistant message — walk backward to find the user message
                for j in range(i - 1, -1, -1):
                    if search_arr[j].get("role") == "user":
                        user_message = search_arr[j].get("content")
                        user_message_id = search_arr[j].get("id")
                        logger.info(f"REGENERATE: Found user message in {arr_name} (id={user_message_id})")
                        break
                break

    if not user_message:
        await broadcast_to_session(session_id, {"type": "error", "text": "Could not find user message to regenerate from"})
        return

    # Build context from messages (API format) using the user message ID.
    # User message IDs are consistent across both arrays.
    for i, msg in enumerate(old_messages):
        if msg.get("id") == user_message_id:
            context_messages = old_messages[:i]
            break
    else:
        # Fallback: if user_message_id not found in messages (shouldn't happen),
        # use content matching as last resort
        for i, msg in enumerate(old_messages):
            if msg.get("role") == "user" and msg.get("content") == user_message:
                context_messages = old_messages[:i]
                break

    logger.info(f"REGENERATE: Keeping {len(context_messages)} context messages")

    # Create new conversation state with context
    conv = ConversationState()
    conv.session_id = session_id
    conv.messages = context_messages.copy()
    active_conversations[session_id] = conv

    # Seed streaming state with context messages so subscribe/snapshot
    # returns the full conversation history if user switches tabs during streaming.
    ss = session_streaming_states.get(session_id)
    if ss is not None:
        ss.messages = list(context_messages)
        # Mark as full history: context + new response IS the complete conversation
        # after regeneration. Don't merge with pre-regenerate display_messages from disk.
        ss._has_full_history = True

    # BROADCAST truncation to ALL clients viewing this session
    # Show context + the user message we're regenerating from
    display_messages = context_messages + [{"id": str(uuid.uuid4()), "role": "user", "content": user_message}]
    await broadcast_to_session(session_id, {
        "type": "truncate",
        "messageId": message_id,
        "messages": display_messages,
        "sessionId": session_id
    })
    logger.info(f"REGENERATE: Broadcasted truncation to session {session_id}")

    # Propagate agent from stored chat
    stored_agent = chat_data.get("agent") if chat_data else None
    if stored_agent:
        data["agent"] = stored_agent

    # Re-send the user message with context
    data["message"] = user_message
    data["sessionId"] = session_id
    data["forceNewSession"] = True
    data["preserveChatId"] = session_id
    data["contextMessages"] = context_messages
    await handle_message(websocket, data)


async def handle_interrupt(websocket: WebSocket, data: dict):
    """Handle interrupt/stop request for an active generation."""
    session_id = data.get("sessionId")

    logger.info(f"INTERRUPT: Received interrupt request for session {session_id}")

    # Try to find and interrupt the active wrapper
    interrupted = False
    wrapper = None

    # Try the specific session ID first (preferred - always use explicit ID)
    if session_id and session_id in active_claude_wrappers:
        wrapper = active_claude_wrappers[session_id]
    # Fallback: if only one active wrapper, use it (backward compat for clients without session_id)
    elif len(active_claude_wrappers) == 1:
        wrapper = next(iter(active_claude_wrappers.values()))

    if wrapper:
        try:
            await wrapper.interrupt()
            interrupted = True
            logger.info(f"INTERRUPT: Successfully interrupted assistant session")
        except Exception as e:
            logger.error(f"INTERRUPT: Error interrupting: {e}")
    elif session_id:
        # No active wrapper but streaming state exists — orphaned/stale state.
        # Clean it up so the client stops showing "processing" on refresh.
        cleaned = False
        if session_id in session_streaming_states:
            del session_streaming_states[session_id]
            cleaned = True
        if session_id in active_processing_sessions:
            await _record_chat_session_ended(session_id)
            recently_completed_sessions[session_id] = time.time()
            cleaned = True
        stop_tool_heartbeat(session_id)
        if cleaned:
            logger.info(f"INTERRUPT: Cleaned up orphaned streaming state for {session_id}")
            interrupted = True  # Tell client the interrupt "worked"

    # BROADCAST interrupted status to ALL clients viewing this session
    effective_session_id = session_id
    if effective_session_id:
        await broadcast_to_session(effective_session_id, {
            "type": "interrupted",
            "success": interrupted,
            "sessionId": effective_session_id
        })
    else:
        # Fallback to direct send if no session ID
        await websocket.send_json({
            "type": "interrupted",
            "success": interrupted,
            "sessionId": session_id
        })


async def handle_reaction(websocket: WebSocket, data: dict):
    """
    Handle emoji reaction toggle from a user.

    Adds/removes a reaction on a message, persists to disk, updates
    in-memory streaming state if active, and broadcasts to all clients.
    """
    session_id = data.get("sessionId")
    message_id = data.get("messageId")
    emoji = data.get("emoji", "")
    remove = data.get("remove", False)
    reactor = "user"

    if not session_id or not message_id or not emoji:
        logger.warning(f"REACTION: Missing required fields: session={session_id}, msg={message_id}, emoji={emoji}")
        return

    # Load chat from disk (ChatManager.save_chat uses FileLock for atomicity)
    chat_data = chat_manager.load_chat(session_id)
    if not chat_data:
        logger.warning(f"REACTION: Chat {session_id} not found")
        return

    def _toggle_reaction(msg: dict) -> bool:
        """Toggle reaction on a single message dict. Returns True if found."""
        if msg.get("id") != message_id:
            return False
        reactions = msg.setdefault("reactions", {})
        if remove:
            reactors = reactions.get(emoji, [])
            if reactor in reactors:
                reactors.remove(reactor)
            if not reactors:
                reactions.pop(emoji, None)
        else:
            reactors = reactions.setdefault(emoji, [])
            if reactor not in reactors:
                reactors.append(reactor)
        # Clean up empty reactions dict
        if not reactions:
            msg.pop("reactions", None)
        return True

    # Update in both messages and display_messages arrays
    found = False
    for msg in chat_data.get("messages", []):
        if _toggle_reaction(msg):
            found = True
            break
    for msg in chat_data.get("display_messages", []):
        if _toggle_reaction(msg):
            found = True
            break

    if not found:
        logger.warning(f"REACTION: Message {message_id} not found in chat {session_id}")
        return

    # Persist
    chat_manager.save_chat(session_id, chat_data)

    # Update in-memory conversation state (used by _collect_pending_reactions)
    conv = active_conversations.get(session_id)
    if conv:
        for msg in conv.messages:
            _toggle_reaction(msg)

    # Update in-memory streaming state if active
    ss = session_streaming_states.get(session_id)
    if ss:
        for msg in ss.messages:
            _toggle_reaction(msg)

    # Get final reactions for the message (from the authoritative disk copy)
    final_reactions = None
    for msg in chat_data.get("display_messages", chat_data.get("messages", [])):
        if msg.get("id") == message_id:
            final_reactions = msg.get("reactions")
            break

    # Broadcast to all clients viewing this session
    await broadcast_to_session(session_id, {
        "type": "reaction_update",
        "sessionId": session_id,
        "messageId": message_id,
        "reactions": final_reactions or {},
    })

    logger.info(f"REACTION: {'Removed' if remove else 'Added'} {emoji} on {message_id} in {session_id}")


async def handle_slash_command_ws(websocket: WebSocket, data: dict):
    """
    Handle a /slash command from the user.

    Two paths:
      1. Quick-pick / explicit: client sends {command: "compact", args: {...}}
      2. Raw text: client sends {raw: "/compact strip_tools 3"} which is parsed
         server-side.

    The command runs server-side directly (no agent invocation), then we:
      - Append a "notice" message to display_messages so the user sees a chip
        in chat (and it persists across reloads).
      - Broadcast the result to all clients viewing this session.
    """
    session_id = data.get("sessionId")
    command = data.get("command")
    args = data.get("args") or {}
    raw = data.get("raw")

    if not session_id:
        await websocket.send_json({
            "type": "slash_command_result",
            "ok": False,
            "title": "No session",
            "text": "Slash commands require an active chat. Send a message first.",
        })
        return

    # If only raw was provided, parse it
    positional = None
    if raw and not command:
        parsed = parse_slash_input(raw)
        if not parsed:
            await websocket.send_json({
                "type": "slash_command_result",
                "ok": False,
                "title": "Invalid command",
                "text": f"Could not parse: {raw}",
                "sessionId": session_id,
            })
            return
        command = parsed["command"]
        positional = parsed["positional"]

    if not command:
        await websocket.send_json({
            "type": "slash_command_result",
            "ok": False,
            "title": "No command",
            "text": "No command specified.",
            "sessionId": session_id,
        })
        return

    logger.info(f"SLASH: /{command} session={session_id} args={args} positional={positional}")

    # If the conversation isn't loaded into memory yet (e.g. user opened the
    # chat and immediately ran /compact without sending a message), boot it.
    if session_id not in active_conversations:
        existing = chat_manager.load_chat(session_id) if chat_manager else None
        if existing:
            try:
                conv = ConversationState()
                # Use compact `messages` (agent-context) — matches what compaction modifies
                conv.messages = list(existing.get("messages") or existing.get("display_messages", []))
                # Restore cumulative usage if present
                cu = existing.get("cumulative_usage")
                if cu and hasattr(conv, "cumulative_usage"):
                    conv.cumulative_usage = cu
                active_conversations[session_id] = conv
                logger.info(f"SLASH: Lazy-loaded conversation {session_id} for /{command}")
            except Exception as e:
                logger.error(f"SLASH: Failed to lazy-load conversation {session_id}: {e}")

    result = await dispatch_slash_command(
        session_id=session_id,
        command=command,
        args=args if args else None,
        positional=positional,
    )

    # NOTE: We deliberately do NOT persist slash command results to display_messages.
    # These are ephemeral confirmations (like a toast), not conversation content.
    # The client appends them transiently and auto-fades them after a few seconds.
    if "id" not in result:
        result["id"] = str(uuid.uuid4())

    # Broadcast result to all clients viewing this session
    payload = {
        "type": "slash_command_result",
        "sessionId": session_id,
        **result,
    }
    await broadcast_to_session(session_id, payload)


async def handle_inject(websocket: WebSocket, data: dict):
    """
    Handle mid-stream message injection.

    This allows sending new user messages WHILE Claude is working.
    The message is injected into the active prompt stream and Claude
    sees it at the next processing point.

    This is different from queuing - injection happens immediately
    within the same conversation turn.
    """
    session_id = data.get("sessionId")
    content = data.get("message", "")
    display_content = data.get("displayMessage") or content
    display_segments = data.get("displaySegments") if isinstance(data.get("displaySegments"), list) else None
    reply_references = data.get("replyReferences") if isinstance(data.get("replyReferences"), list) else None
    msg_id = data.get("msgId") or str(uuid.uuid4())

    if not content:
        await websocket.send_json({
            "type": "inject_failed",
            "error": "Empty message",
            "msgId": msg_id
        })
        return

    logger.info(f"INJECT: Received injection request for session {session_id}: {content[:50]}...")

    # Find the active Claude wrapper to inject into
    wrapper = None
    effective_session_id = session_id

    # Try the specific session ID first (preferred)
    if session_id and session_id in active_claude_wrappers:
        wrapper = active_claude_wrappers[session_id]
    # Fallback: if only one active wrapper, use it (backward compat)
    elif len(active_claude_wrappers) == 1:
        effective_session_id = next(iter(active_claude_wrappers.keys()))
        wrapper = active_claude_wrappers[effective_session_id]

    if not wrapper:
        logger.warning(f"INJECT: No active Claude wrapper found for injection")
        await websocket.send_json({
            "type": "inject_failed",
            "error": "No active conversation to inject into",
            "msgId": msg_id
        })
        return

    # Check if wrapper has an injection queue
    injection_queue = wrapper.get_injection_queue()
    if not injection_queue:
        logger.warning(f"INJECT: Wrapper has no injection queue (not in streaming mode)")
        await websocket.send_json({
            "type": "inject_failed",
            "error": "Conversation not in streaming mode",
            "msgId": msg_id
        })
        return

    # Add timestamp to injection message
    timestamp = datetime.now().strftime("%A, %-m/%-d/%Y at %-I:%M%p")
    timestamped_content = f"[{timestamp}] [INJECTED MESSAGE] {content}"

    # Inject the message
    success = await injection_queue.inject(timestamped_content, msg_id)

    if success:
        logger.info(f"INJECT: Successfully injected message into session {effective_session_id}")

        # NOTE: Don't save to disk here — the SDK's conv.messages already includes
        # the injected message at the correct position. The final save at turn completion
        # (final_save_data["messages"] = conv.messages) will persist it. Saving here
        # could cause duplicates if the disk's messages array already has it from conv.messages.

        # Add injected user message to streaming state so reconnects/re-subscribes
        # show a consistent state (without this, the streaming snapshot was missing
        # injected messages, causing state drift on visibility changes / reconnects)
        _inject_ss = session_streaming_states.get(effective_session_id)
        if _inject_ss:
            injected_msg = {
                "id": msg_id,
                "role": "user",
                "content": display_content,
                "injected": True,
                "timestamp": time.time()
            }
            if display_content != content:
                injected_msg["displayContent"] = display_content
                injected_msg["agentContent"] = content
            if display_segments is not None:
                injected_msg["displaySegments"] = display_segments
            if reply_references is not None:
                injected_msg["replyReferences"] = reply_references
            # Insert AFTER the last assistant message (the one currently streaming),
            # not at the end of the array. This ensures display_messages on disk
            # has the correct conversation order when saved at turn completion.
            insert_idx = len(_inject_ss.messages)
            for i in range(len(_inject_ss.messages) - 1, -1, -1):
                if _inject_ss.messages[i].get("role") == "assistant":
                    insert_idx = i + 1
                    break
            _inject_ss.messages.insert(insert_idx, injected_msg)
            logger.info(f"INJECT: Inserted injected message at position {insert_idx}/{len(_inject_ss.messages)} in streaming state for {effective_session_id}")

        # BROADCAST the injection to all clients
        await broadcast_to_session(effective_session_id, {
            "type": "message_injected",
            "sessionId": effective_session_id,
            "message": injected_msg
        })

        # Also send direct acknowledgment to the sending client
        await websocket.send_json({
            "type": "inject_success",
            "msgId": msg_id,
            "sessionId": effective_session_id
        })
    else:
        logger.warning(f"INJECT: Failed to inject message")
        await websocket.send_json({
            "type": "inject_failed",
            "error": "Injection queue closed or unavailable",
            "msgId": msg_id
        })


# --- Scheduler helpers ---

async def _collect_structured_output(claude, prompt, agent_config=None):
    """Run a prompt and collect structured output with segment/tool tracking.

    Returns (all_segments, completed_tool_calls, actual_session_id) where:
    - all_segments: list of text strings between tool invocations
    - completed_tool_calls: list of (segment_index, serialized_tool_call_dict)
    - actual_session_id: the session ID from the SDK (or None if not received)
    """
    all_segments = []
    current_segment = []
    pending_tool_calls = {}  # tool_id -> {name, args}
    completed_tool_calls = []  # [(segment_index, tool_call_dict), ...]
    actual_session_id = None

    def finalize_segment():
        nonlocal current_segment
        if current_segment:
            text = "".join(current_segment).strip()
            if text:
                all_segments.append(text)
            current_segment = []

    # Resolve default agent config if not provided
    if not agent_config:
        try:
            agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
            if str(agents_dir) not in sys.path:
                sys.path.insert(0, str(agents_dir))
            from registry import get_registry
            agent_config = get_registry().get_default_agent()
        except Exception:
            pass

    if not agent_config:
        logger.error("No agent config available for _collect_structured_output")
        return [], [], None

    async for event in claude.run_chat(prompt, agent_config=agent_config):
        event_type = event.get("type")

        if event_type == "session_init":
            actual_session_id = event.get("id")

        elif event_type == "content_delta":
            current_segment.append(event.get("text", ""))

        elif event_type == "tool_start":
            finalize_segment()
            # Defensive stash from tool_start in case tool_use doesn't fire
            ts_tool_id = event.get("id")
            ts_tool_name = event.get("name", "tool")
            if ts_tool_id and ts_tool_id not in pending_tool_calls:
                pending_tool_calls[ts_tool_id] = {"name": ts_tool_name, "args": "{}"}

        elif event_type == "tool_use":
            finalize_segment()
            tool_id = event.get("id")
            tool_name = event.get("name", "tool")
            tool_args = event.get("args", "{}")
            if tool_id:
                # Overwrite any partial stash from tool_start with full args
                pending_tool_calls[tool_id] = {"name": tool_name, "args": tool_args}

        elif event_type == "tool_end":
            tool_end_id = event.get("id")
            tool_end_name = event.get("name", "")
            tool_end_output = event.get("output", "")
            tool_end_error = event.get("is_error", False)
            stashed = pending_tool_calls.pop(tool_end_id, None) if tool_end_id else None
            if stashed:
                try:
                    tc = serialize_tool_call(
                        tool_name=stashed["name"],
                        args_raw=stashed["args"],
                        output=tool_end_output,
                        is_error=tool_end_error,
                        tool_id=tool_end_id,
                    )
                    tc["timestamp"] = int(time.time())
                    completed_tool_calls.append((len(all_segments), tc))
                except Exception as ser_err:
                    logger.warning(f"Scheduled task tool serialization error: {ser_err}")
            elif tool_end_name:
                try:
                    tc = serialize_tool_call(
                        tool_name=tool_end_name,
                        args_raw={},
                        output=tool_end_output,
                        is_error=tool_end_error,
                        tool_id=tool_end_id,
                    )
                    tc["timestamp"] = int(time.time())
                    completed_tool_calls.append((len(all_segments), tc))
                except Exception:
                    pass

        elif event_type == "error":
            current_segment.append(f"\n\n**Error:** {event.get('text')}\n")

    # Finalize any trailing content
    finalize_segment()

    return all_segments, completed_tool_calls, actual_session_id


def _build_interleaved_messages(all_segments, completed_tool_calls):
    """Build interleaved assistant + tool_call message list from segments and tool calls.

    Returns a list of message dicts ready to be appended to a chat's messages.

    Tool calls are tagged with the segment count at the time they completed
    (i.e., len(all_segments) when tool_end fired). A tool at segment_index=1
    means segment 0 was already finalized, so the tool ran BETWEEN segment 0
    and segment 1. We insert tool calls BEFORE the segment at their index,
    matching the main handler's interleaving order.
    """
    # Group tool calls by the segment index they precede
    tc_by_seg = {}
    for seg_idx, tc in completed_tool_calls:
        tc_by_seg.setdefault(seg_idx, []).append(tc)

    messages = []
    for i, segment in enumerate(all_segments):
        # Tool calls at index i ran BEFORE this segment (between segment i-1 and i)
        for tc in tc_by_seg.get(i, []):
            if "id" not in tc:
                tc["id"] = str(uuid.uuid4())
            messages.append(tc)
        clean = strip_tool_markers(segment)
        if clean:
            messages.append({
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": clean
            })

    # Any tool calls after the last segment (e.g., tool ran but no text followed)
    for tc in tc_by_seg.get(len(all_segments), []):
        if "id" not in tc:
            tc["id"] = str(uuid.uuid4())
        messages.append(tc)

    return messages


# --- Scheduler ---


def _build_agent_display_messages(prompt: str, result, agent_name: str) -> list:
    """Build display_messages list from an agent result with proper blocks for UI rendering.

    If the agent result has blocks (ContentBlock-compatible dicts), creates an assistant
    message with a blocks array so the frontend renders tool pills, thinking sections, etc.
    Falls back to flat text content if no blocks are available.
    """
    user_msg = {
        "id": str(uuid.uuid4()),
        "role": "user",
        "content": f"[Scheduled Agent: {agent_name}] {prompt}",
        "status": "complete",
    }

    blocks = getattr(result, "blocks", None) if result.status == "success" else None

    if blocks:
        # Always populate content with text fallback for conversation history.
        # blocks carries the rich UI data; content is used by _build_history_context
        # when the user responds inline to a scheduled task chat.
        text = (result.transcript or result.response) or ""
        assistant_msg = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": text,
            "status": "complete",
            "blocks": blocks,
        }
    else:
        # Fallback to flat text
        text = (result.transcript or result.response) if result.status == "success" else f"Error: {result.error}"
        assistant_msg = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": text or "",
            "status": "complete",
        }

    return [user_msg, assistant_msg]


async def _execute_scheduled_task(task_info):
    """Execute a single scheduled task. Wraps the body in running_agents.track()
    so the firing shows up in running_agents() while it's in flight. The inner
    invoke_agent calls register their own entries (kind=invoke_foreground /
    invoke_trust) — two entries per scheduled-agent firing is intentional (see
    plan §2)."""
    if isinstance(task_info, dict):
        _t_prompt = task_info.get("prompt", "") or ""
        _t_id = task_info.get("id")
        _t_agent = task_info.get("agent")
    else:
        _t_prompt = task_info if isinstance(task_info, str) else ""
        _t_id = None
        _t_agent = None

    async with running_agents.track(
        agent=_t_agent or "system",
        kind="scheduled",
        task_summary=_t_prompt,
        scheduled_task_id=_t_id,
    ):
        await _execute_scheduled_task_body(task_info)


async def _execute_scheduled_task_body(task_info):
    """Execute a single scheduled task. Extracted from scheduler_loop to allow concurrent dispatch."""
    try:
        # Handle both old format (string) and new format (dict with metadata)
        if isinstance(task_info, str):
            prompt = task_info
            is_silent = False
            task_id = None
            task_type = "prompt"
            agent_name = None
            task_project = None
        else:
            prompt = task_info.get("prompt", "")
            is_silent = task_info.get("silent", False)
            task_id = task_info.get("id")
            task_type = task_info.get("type", "prompt")
            agent_name = task_info.get("agent")
            task_project = task_info.get("project")

        # Initialize variables used by the notification block
        actual_session_id: Optional[str] = None
        assistant_content: list = []
        title: str = "Scheduled Task"

        # Guard: agent tasks require agent_name
        if task_type == "agent" and not agent_name:
            logger.warning(f"Scheduled agent task has no agent name (task_id={task_id}). Skipping.")
            return

        # Handle agent tasks
        if task_type == "agent" and agent_name:
            agent_room_id = task_info.get("room_id") if isinstance(task_info, dict) else None
            logger.info(f"Executing Scheduled Agent Task: {agent_name} (silent={is_silent}, room={agent_room_id}) - {prompt[:80]}...")

            try:
                # Import agent runner
                agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
                if str(agents_dir) not in sys.path:
                    sys.path.insert(0, str(agents_dir))
                from runner import invoke_agent
                from datetime import datetime

                if is_silent:
                    # === SILENT AGENT TASKS ===
                    if agent_room_id:
                        # Room-targeted silent agent: run foreground to capture output
                        existing_chat = chat_manager.load_chat(agent_room_id)
                        if existing_chat:
                            existing_messages = existing_chat.get("messages", [])
                            history_context = ""
                            if existing_messages:
                                history_parts = []
                                for msg in existing_messages[-15:]:
                                    role = msg.get("role", "user")
                                    content = msg.get("content", "")
                                    if role == "user":
                                        history_parts.append(f"User: {content}")
                                    elif role == "assistant":
                                        history_parts.append(f"Assistant: {content}")
                                if history_parts:
                                    history_context = f"[ROOM CONTEXT - Previous conversation]\n{chr(10).join(history_parts)}\n\n"

                            routing_instructions = f"""

SCHEDULED TASK CONTEXT:
You are running as a scheduled task. Your output will be delivered directly to room '{agent_room_id}'.

- Your final response text will be appended to the room's conversation
- Be conversational and provide a complete response
"""
                            augmented_prompt = f"{history_context}{prompt}{routing_instructions}"

                            result = await invoke_agent(
                                name=agent_name,
                                prompt=augmented_prompt,
                                mode="foreground",
                                source_chat_id=agent_room_id,
                                project=task_project,
                                is_visible=False,
                            )

                            new_msgs = _build_agent_display_messages(prompt, result, agent_name)
                            if new_msgs:
                                existing_messages.extend(new_msgs)
                                existing_chat["messages"] = existing_messages
                                # Update display_messages for block-based UI rendering
                                dm = existing_chat.get("display_messages")
                                if dm is not None:
                                    dm.extend(new_msgs)
                                else:
                                    existing_chat["display_messages"] = list(existing_messages)
                                chat_manager.save_chat(agent_room_id, existing_chat)
                                if rooms_meta:
                                    rooms_meta.bump(agent_room_id)
                                logger.info(f"Delivered silent agent task output to room {agent_room_id}")
                        else:
                            logger.warning(f"Target room {agent_room_id} not found for agent task")

                    else:
                        # Default silent: fire-and-forget, agent writes to agent_outputs
                        agent_outputs_dir = Path(ROOT_DIR) / "00_Inbox" / "agent_outputs"
                        agent_outputs_dir.mkdir(parents=True, exist_ok=True)

                        topic_slug = prompt[:30].lower().strip()
                        topic_slug = '-'.join(topic_slug.split())
                        topic_slug = ''.join(c if c.isalnum() or c == '-' else '' for c in topic_slug)
                        topic_slug = topic_slug.strip('-')[:30] or 'task'

                        today = datetime.now().strftime("%Y-%m-%d")

                        # Build filename: include project slug when present
                        project_slug = ""
                        if task_project:
                            p = task_project if isinstance(task_project, str) else task_project[0]
                            project_slug = f"_{p}"
                        output_filename = f"{today}_{agent_name}{project_slug}_{topic_slug}.md"

                        routing_instructions = f"""

SCHEDULED TASK CONTEXT:
You are running as a scheduled task, not a live invocation. Your output will be reviewed asynchronously by Primary assistant.

- Write your complete output to: 00_Inbox/agent_outputs/{output_filename}
- Include at the top of the file: the original task/question you were asked, so the reviewer has full context
- Your final reply to this prompt doesn't matter - all value should be in the artifact you create
"""
                        augmented_prompt = prompt + routing_instructions

                        result = await invoke_agent(
                            name=agent_name,
                            prompt=augmented_prompt,
                            mode="scheduled",
                            source_chat_id=None,
                            project=task_project,
                            is_visible=False,
                        )

                    logger.info(f"Silent agent task completed: {agent_name}")
                    return  # Skip notification for silent agents

                else:
                    # === NON-SILENT AGENT TASKS: create visible chat + notify ===
                    if agent_room_id:
                        # Non-silent room-targeted: run foreground, append to room
                        existing_chat = chat_manager.load_chat(agent_room_id)
                        if existing_chat:
                            existing_messages = existing_chat.get("messages", [])
                            history_context = ""
                            if existing_messages:
                                history_parts = []
                                for msg in existing_messages[-15:]:
                                    role = msg.get("role", "user")
                                    content = msg.get("content", "")
                                    if role == "user":
                                        history_parts.append(f"User: {content}")
                                    elif role == "assistant":
                                        history_parts.append(f"Assistant: {content}")
                                if history_parts:
                                    history_context = f"[ROOM CONTEXT - Previous conversation]\n{chr(10).join(history_parts)}\n\n"

                            routing_instructions = f"""

SCHEDULED TASK CONTEXT:
You are running as a scheduled task. Your output will be shown to the user.

- Your final response text will be appended to the room's conversation
- Be conversational and provide a complete response
"""
                            augmented_prompt = f"{history_context}{prompt}{routing_instructions}"

                            result = await invoke_agent(
                                name=agent_name,
                                prompt=augmented_prompt,
                                mode="foreground",
                                source_chat_id=agent_room_id,
                                project=task_project,
                                is_visible=True,
                            )

                            new_msgs = _build_agent_display_messages(prompt, result, agent_name)
                            agent_output = (result.transcript or result.response) if result.status == "success" else f"Error: {result.error}"
                            if new_msgs:
                                existing_messages.extend(new_msgs)
                                existing_chat["messages"] = existing_messages
                                dm = existing_chat.get("display_messages")
                                if dm is not None:
                                    dm.extend(new_msgs)
                                else:
                                    existing_chat["display_messages"] = list(existing_messages)
                                chat_manager.save_chat(agent_room_id, existing_chat)
                                if rooms_meta:
                                    rooms_meta.bump(agent_room_id)

                            actual_session_id = agent_room_id
                            title = existing_chat.get("title", f"Agent: {agent_name}")
                            assistant_content = [agent_output] if agent_output else []
                            # Fall through to notification block
                        else:
                            logger.warning(f"Target room {agent_room_id} not found for agent task")
                            return

                    else:
                        # Non-silent, no room: run foreground, create new visible chat
                        routing_instructions = """

SCHEDULED TASK CONTEXT:
You are running as a scheduled task. Your output will be shown to the user in a chat.
- Provide a complete, conversational response
"""
                        augmented_prompt = prompt + routing_instructions

                        result = await invoke_agent(
                            name=agent_name,
                            prompt=augmented_prompt,
                            mode="foreground",
                            source_chat_id=None,
                            project=task_project,
                            is_visible=True,
                        )

                        display_msgs = _build_agent_display_messages(prompt, result, agent_name)
                        agent_output = (result.transcript or result.response) if result.status == "success" else f"Error: {result.error}"

                        session_id = str(uuid.uuid4())
                        actual_session_id = session_id

                        prompt_preview = prompt[:50]
                        title = f"{agent_name}: {prompt_preview}" if prompt_preview else f"Agent: {agent_name}"

                        chat_data = {
                            "title": title,
                            "sessionId": actual_session_id,
                            "is_system": False,
                            "scheduled": True,
                            "agent": agent_name,
                            "messages": display_msgs,
                            "display_messages": display_msgs,
                        }
                        chat_manager.save_chat(actual_session_id, chat_data)
                        await broadcast_chat_created(actual_session_id, title, agent_name, scheduled=True)
                        assistant_content = [agent_output] if agent_output else []
                        logger.info(f"Saved non-silent agent task result: {actual_session_id}")

                    # Fall through to notification block below

            except Exception as e:
                logger.error(f"Scheduled agent task failed: {agent_name} - {e}")
                return

        # === Prompt task handling (skip for agent tasks — they're handled above) ===
        # Prompt tasks use the legacy wrapper, which shares a single runtime session,
        # so we serialize them to prevent "conversation not found" races.
        if task_type != "agent":
          async with scheduled_prompt_lock:
            target_room_id = task_info.get("room_id") if isinstance(task_info, dict) else None
            logger.info(f"Executing Scheduled Task (silent={is_silent}, room={target_room_id}): {prompt[:80]}...")

            # === Room-targeted prompt task ===
            if target_room_id:
                existing_chat = chat_manager.load_chat(target_room_id)
                if existing_chat:
                    existing_messages = existing_chat.get("messages", [])
                    title = existing_chat.get("title", "Scheduled Task")

                    augmented_prompt = _build_history_context(existing_messages, prompt)

                    claude = ClaudeWrapper(session_id="new", cwd=ROOT_DIR, chat_id=target_room_id, chat_messages=existing_messages)
                    logger.info(f"Starting fresh runtime session for room {target_room_id}")

                    all_segments, completed_tool_calls, _ = await _collect_structured_output(claude, augmented_prompt)

                    raw_prompt = prompt.replace("\U0001f447 [SCHEDULED AUTOMATION] \U0001f447\n", "")
                    existing_messages.append({
                        "id": str(uuid.uuid4()),
                        "role": "system",
                        "content": f"[Scheduled Task] {raw_prompt}"
                    })
                    # Build interleaved assistant + tool_call messages
                    interleaved = _build_interleaved_messages(all_segments, completed_tool_calls)
                    existing_messages.extend(interleaved)

                    # Keep assistant_content for notification preview
                    assistant_content = [strip_tool_markers(s) for s in all_segments]

                    existing_chat["messages"] = existing_messages
                    chat_manager.save_chat(target_room_id, existing_chat)

                    try:
                        if rooms_meta:
                            rooms_meta.bump(target_room_id)
                    except Exception:
                        pass

                    actual_session_id = target_room_id
                    logger.info(f"Delivered scheduled task to room {target_room_id}")

                else:
                    logger.warning(f"Target room {target_room_id} not found, creating new chat")
                    target_room_id = None  # Fall through to normal handling

            # === Normal (non-room-targeted) prompt task ===
            if not target_room_id:
                session_id = str(uuid.uuid4())
                claude = ClaudeWrapper(session_id="new", cwd=ROOT_DIR)

                all_segments, completed_tool_calls, sdk_session_id = await _collect_structured_output(claude, prompt)
                actual_session_id = sdk_session_id or session_id

                prompt_preview = prompt.replace("\U0001f447 [SCHEDULED AUTOMATION] \U0001f447\n", "")[:50]
                title = prompt_preview if prompt_preview else "Scheduled Task"

                # Build interleaved assistant + tool_call messages
                interleaved = _build_interleaved_messages(all_segments, completed_tool_calls)

                chat_data = {
                    "title": title,
                    "sessionId": actual_session_id,
                    "is_system": is_silent,
                    "scheduled": True,
                    "messages": [
                        {"id": str(uuid.uuid4()), "role": "system", "content": prompt},
                        *interleaved
                    ]
                }

                # Keep assistant_content for notification preview
                assistant_content = [strip_tool_markers(s) for s in all_segments]

                chat_manager.save_chat(actual_session_id, chat_data)
                await broadcast_chat_created(actual_session_id, title, is_system=is_silent, scheduled=True)
                logger.info(f"Saved scheduled task result: {actual_session_id} (is_system={is_silent})")

        # Guard: skip notification if no session was created (defensive)
        if actual_session_id is None:
            logger.warning(f"Scheduled task produced no session_id (task_type={task_type}). Skipping notification.")
            return

        # Determine notification channels based on visibility
        decision = should_notify(
            chat_id=actual_session_id,
            is_silent=is_silent,
            client_sessions=client_sessions
        )

        if decision.notify:
            # Get message preview for notification
            # assistant_content is a list of clean text segments (tool markers already stripped)
            clean_preview = "\n\n".join(assistant_content) if assistant_content else ""
            preview = clean_preview[:200] if clean_preview else title

            # Send WebSocket notification (toast/sound) to connected clients
            if decision.use_toast:
                await send_notification(
                    client_sessions=client_sessions,
                    chat_id=actual_session_id,
                    preview=preview,
                    critical=False,
                    play_sound=decision.play_sound,
                    title=title
                )

                # Also send legacy scheduled_task_complete for backward compatibility
                await broadcast_to_all_clients({
                    "type": "scheduled_task_complete",
                    "session_id": actual_session_id,
                    "title": title
                })

            # Send push notification to mobile/offline clients
            if decision.use_push:
                await send_push_notification(
                    title="Claude sent you a message",
                    body=preview[:100],
                    chat_id=actual_session_id,
                    critical=False
                )

            logger.info(f"Notification sent: {decision.reason} (toast={decision.use_toast}, push={decision.use_push}, sound={decision.play_sound})")

    except Exception as e:
        logger.error(f"Scheduled task execution error: {e}")


async def _execute_scheduled_task_with_timeout(task_info):
    """Wrapper that enforces a timeout on scheduled task execution."""
    task_id = task_info.get("id") if isinstance(task_info, dict) else None
    task_desc = (task_info.get("prompt", "") if isinstance(task_info, dict) else str(task_info))[:80]
    try:
        async with asyncio.timeout(SCHEDULED_TASK_TIMEOUT):
            await _execute_scheduled_task(task_info)
    except TimeoutError:
        logger.error(f"Scheduled task timed out after {SCHEDULED_TASK_TIMEOUT}s (task_id={task_id}): {task_desc}")


async def scheduler_loop():
    """Background task to check and execute scheduled tasks.

    Dispatches each due task as a concurrent coroutine via asyncio.create_task()
    so that a slow task doesn't block other scheduled tasks from starting.
    """
    logger.info("Scheduler Loop Started")

    while True:
        try:
            await asyncio.sleep(60)  # Check every minute

            if not scheduler_tool:
                continue

            due_tasks = scheduler_tool.check_due_tasks()

            for task_info in due_tasks:
                asyncio.create_task(_execute_scheduled_task_with_timeout(task_info))

            # Periodic maintenance: clean up stale chat locks
            _cleanup_chat_locks()

            # Periodic maintenance: drop running_agents entries whose track()
            # context never unregistered (cancellation that swallowed
            # CancelledError, code path that escaped a finally). Flat ceiling
            # above the longest agent timeout; see running_agents
            # RUNNING_AGENTS_STALE_AFTER_SECONDS.
            try:
                stale_entries = await running_agents.sweep_stale()
                for entry in stale_entries:
                    logger.warning(
                        "running_agents: dropped stale entry "
                        f"id={entry.get('id')} agent={entry.get('agent')} "
                        f"kind={entry.get('kind')} elapsed="
                        f"{time.time() - entry.get('started_at', time.time()):.0f}s"
                    )
            except Exception as e:
                logger.warning(f"running_agents: periodic sweep failed: {e}")

            # Periodic maintenance: chat image garbage collection (once daily)
            await _maybe_run_image_gc()

            # Salon recall sweep: fire convener loops on any active salons
            # whose convener_recall_at deadline has passed. Cheap; gated by
            # per-salon locks so it can't pile up.
            try:
                await _salon_dispatcher_mod.check_salon_recalls()
            except Exception as e:
                logger.warning(f"Salon recall sweep error: {e}")

        except Exception as e:
            logger.error(f"Scheduler Error: {e}")


class _RestartContinuationNoopWebSocket:
    """Minimal websocket stand-in for headless restart continuation."""

    async def send_json(self, payload: Dict[str, Any]) -> None:
        logger.debug(
            f"Restart continuation: headless send_json ignored "
            f"(type={payload.get('type') if isinstance(payload, dict) else 'unknown'})"
        )


def _cleanup_restart_continuation_websocket(ws: _RestartContinuationNoopWebSocket) -> int:
    """Remove a synthetic restart transport from session broadcast registries."""
    removed = 0
    for sid, clients in list(session_clients.items()):
        if ws in clients:
            clients.discard(ws)
            removed += 1
        if not clients:
            del session_clients[sid]
    client_sessions.pop(ws, None)
    return removed


def _restart_agent_resume_prompt(reason: str, source: str, role_note: str = "") -> str:
    return (
        "[SYSTEM NOTICE - NOT VISIBLE TO USER]\n"
        "Backend restart completed successfully.\n"
        f"Restart reason: {reason}\n"
        f"Restart source: {source}.\n"
        f"{role_note}\n"
        "Continue where you left off using the existing thread/context. "
        "Do not restart the task from scratch unless the saved context requires it."
    )


def _resume_mode_for_running_kind(kind: Optional[str]) -> str:
    # Foreground callers died with the backend, so resume them as ping work when
    # possible: the thread continues and completion is still delivered to the
    # original chat/agent-thread target. Trust/scheduled work remains scheduled.
    if kind in {"invoke_trust", "scheduled", "background_processing"}:
        return "scheduled"
    return "ping"


async def _resume_agent_invocation_after_restart(entry: Dict[str, Any], reason: str, source: str) -> bool:
    agent = entry.get("agent")
    kind = entry.get("kind")
    conversation_id = entry.get("conversation_id")
    if not agent:
        logger.error(f"Restart continuation: running-agent entry missing agent: {entry}")
        return False
    if kind == "salon_agent":
        logger.warning(
            f"Restart continuation: salon agent '{agent}' cannot be safely resumed "
            "by the generic agent-thread path; salon dispatcher owns that lifecycle"
        )
        return True
    if not conversation_id:
        logger.error(
            f"Restart continuation: cannot resume agent '{agent}' kind={kind}; "
            "no durable conversation_id in running_agents entry"
        )
        return False

    try:
        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
        if str(agents_dir) not in sys.path:
            sys.path.insert(0, str(agents_dir))
        from runner import invoke_agent as _invoke_agent

        mode = _resume_mode_for_running_kind(kind)
        prompt = _restart_agent_resume_prompt(
            reason=reason,
            source=source,
            role_note=(
                f"You were running as agent '{agent}' in invocation kind "
                f"'{kind}' when the backend restarted."
            ),
        )
        result = await _invoke_agent(
            name=agent,
            prompt=prompt,
            mode=mode,
            source_chat_id=entry.get("source_chat_id"),
            conversation_id=conversation_id,
            caller_agent=entry.get("caller_agent") or "restart_continuation",
            scheduled_task_id=entry.get("scheduled_task_id"),
            is_background_processing=(kind == "background_processing"),
        )
        if isinstance(result, dict) and result.get("error"):
            logger.error(
                f"Restart continuation: failed to resume agent '{agent}' "
                f"thread {conversation_id}: {result.get('error')}"
            )
            return False
        logger.info(
            f"Restart continuation: resumed agent '{agent}' kind={kind} "
            f"mode={mode} thread={conversation_id}"
        )
        return True
    except Exception as e:
        logger.error(
            f"Restart continuation: exception resuming agent '{agent}' "
            f"thread {conversation_id}: {e}",
            exc_info=True,
        )
        return False


async def restart_continuation_wakeup():
    """Background task that runs once after server startup to resume ALL sessions
    that were active when a restart was triggered.

    Waits for a STABLE WebSocket connection (survives a brief delay without disconnecting),
    then triggers continuation for every session in the restart_continuation marker.
    """
    global restart_continuation

    if not restart_continuation:
        return

    continuation = restart_continuation
    restart_continuation = None  # Clear immediately so nothing else picks it up

    sessions = continuation.get("sessions", [])
    agent_invocations = continuation.get("agent_invocations", [])
    reason = continuation.get("reason", "Server restart")
    source = continuation.get("source", "unknown")
    continuation_prompt = continuation.get("continuation_prompt", "Restart completed. Please continue.")

    if not sessions and not agent_invocations:
        logger.warning("Restart continuation has no sessions or agent invocations to resume")
        return

    logger.info(
        f"Restart continuation: waiting for WebSocket connection to resume "
        f"{len(sessions)} chat session(s) and {len(agent_invocations)} agent invocation(s) "
        f"(source={source}, reason={reason})"
    )

    # Wait for a STABLE WebSocket connection (max 60 seconds).
    # The browser may connect/disconnect during page reload, so we need to
    # find a connection that persists through a brief stability check.
    ws = None
    max_wait_seconds = 60
    elapsed = 0.0
    transient_connects = 0
    while elapsed < max_wait_seconds:
        if client_sessions:
            # Found a connection — verify it survives a brief delay
            await asyncio.sleep(1.5)
            elapsed += 1.5
            ws = next(iter(client_sessions.keys()), None)
            if ws:
                # Connection survived the stability check — use it
                break
            else:
                # Connection disappeared (browser reload, etc.) — keep waiting
                transient_connects += 1
                logger.info(
                    f"Restart continuation: WebSocket connected then disconnected "
                    f"(transient #{transient_connects}), waiting for stable connection..."
                )
                ws = None
                continue
        await asyncio.sleep(0.5)
        elapsed += 0.5

    headless_continuation = False
    if not ws:
        logger.warning(
            f"Restart continuation: no stable WebSocket client within {max_wait_seconds}s "
            f"({transient_connects} transient connection(s) seen); continuing headlessly"
        )
        ws = _RestartContinuationNoopWebSocket()
        headless_continuation = True

    # Send a restart_continuation notification to the client for EACH session
    resumed_count = 0
    failed_sessions = []
    for session_info in sessions:
        session_id = session_info.get("session_id")
        agent = session_info.get("agent", "character")
        role = session_info.get("role", "trigger")

        if not session_id:
            continue

        try:
            # Notify the client about this session's continuation
            await ws.send_json({
                "type": "restart_continuation",
                "session_id": session_id,
                "agent": agent,
                "role": role,
                "reason": reason,
                "source": source,
                "message": f"Continuing conversation after restart (source: {source})..."
            })

            # Load existing chat to get context
            chat_data = chat_manager.load_chat(session_id)
            context_messages = chat_data.get("messages", []) if chat_data else []

            # Build the continuation message with restart metadata
            if role == "trigger":
                continuation_message = (
                    f"[SYSTEM NOTICE - NOT VISIBLE TO USER]\n"
                    f"Server restart completed successfully.\n"
                    f"Restart reason: {reason}\n"
                    f"Restart source: {source} (you triggered this restart)\n"
                    f"{continuation_prompt}\n"
                    f"Continue the conversation naturally - acknowledge the restart briefly and proceed."
                )
            else:
                continuation_message = (
                    f"[SYSTEM NOTICE - NOT VISIBLE TO USER]\n"
                    f"Server restart completed successfully.\n"
                    f"Restart reason: {reason}\n"
                    f"Restart source: {source} (another agent triggered this restart, not you)\n"
                    f"You were actively working when the server was restarted.\n"
                    f"{continuation_prompt}\n"
                    f"Continue the conversation naturally - acknowledge the restart briefly and proceed."
                )

            logger.info(f"Auto-continuing session {session_id} (agent={agent}, role={role}) after restart")

            # Use handle_message with forceNewSession to start a fresh assistant session
            # but preserve chat history context. Headless continuation may register
            # its synthetic websocket for broadcasts while streaming; always scrub
            # it after each attempt so it cannot remain in session_clients.
            try:
                await handle_message(ws, {
                    "sessionId": session_id,
                    "message": continuation_message,
                    "msgId": f"system-restart-{datetime.now().timestamp()}",
                    "forceNewSession": True,
                    "preserveChatId": session_id,
                    "contextMessages": context_messages,
                    "isSystemContinuation": True
                })
            finally:
                if headless_continuation:
                    removed = _cleanup_restart_continuation_websocket(ws)
                    if removed:
                        logger.info(
                            f"Restart continuation: cleaned synthetic websocket from "
                            f"{removed} session_clients registration(s) after {session_id}"
                        )

            resumed_count += 1

            # Brief delay between sessions to avoid overwhelming
            if len(sessions) > 1:
                await asyncio.sleep(1.0)

        except Exception as e:
            failed_sessions.append(session_id)
            logger.error(f"Failed to auto-continue session {session_id} after restart: {e}")

    resumed_agents = 0
    failed_agent_invocations = []
    for entry in agent_invocations:
        ok = await _resume_agent_invocation_after_restart(entry, reason, source)
        if ok:
            resumed_agents += 1
        else:
            failed_agent_invocations.append(entry.get("id") or entry.get("conversation_id") or entry.get("agent"))

    mode = "headless" if headless_continuation else "websocket"
    logger.info(
        f"Restart continuation complete via {mode}: resumed {resumed_count}/{len(sessions)} "
        f"chat session(s), {resumed_agents}/{len(agent_invocations)} agent invocation(s)"
    )

    if failed_sessions or failed_agent_invocations:
        logger.error(
            f"Restart continuation: leaving continuation file in place after "
            f"failed session(s): {failed_sessions}; failed agent invocation(s): "
            f"{failed_agent_invocations}"
        )
        return

    # Clean up the continuation file now that all sessions have been resumed,
    # but only if it is still the marker this wakeup task loaded. A nested
    # restart_server call can write the next marker before this older task exits.
    try:
        if os.path.exists(RESTART_CONTINUATION_FILE):
            with open(RESTART_CONTINUATION_FILE, 'r') as f:
                current_marker = json.load(f)
            if _restart_continuation_marker_matches(current_marker, continuation):
                os.remove(RESTART_CONTINUATION_FILE)
                logger.info(
                    "Restart continuation: cleaned up continuation file after successful resume "
                    f"(marker_id={continuation.get('continuation_id', 'legacy')})"
                )
            else:
                logger.warning(
                    "Restart continuation: preserving newer/different continuation marker during cleanup "
                    f"(loaded_marker_id={continuation.get('continuation_id', 'legacy')}, "
                    f"current_marker_id={current_marker.get('continuation_id', 'legacy')})"
                )
    except Exception as e:
        logger.warning(f"Restart continuation: failed to inspect/remove continuation file: {e}")


AGENT_THREAD_NOTIFICATION_PREFIX = "agent-thread:"


def _parse_agent_thread_notification_source(source_chat_id: Optional[str]) -> Optional[Tuple[str, str]]:
    """Return (caller_agent, conversation_id) for synthetic agent ping targets."""
    if not source_chat_id or not source_chat_id.startswith(AGENT_THREAD_NOTIFICATION_PREFIX):
        return None
    rest = source_chat_id[len(AGENT_THREAD_NOTIFICATION_PREFIX):]
    if ":" not in rest:
        return None
    caller_agent, conversation_id = rest.split(":", 1)
    if not caller_agent or not conversation_id:
        return None
    return caller_agent, conversation_id


def _notification_completed_ts(notification: Any) -> float:
    try:
        completed_at = notification.completed_at
        if isinstance(completed_at, datetime) and (
            completed_at.tzinfo is None or completed_at.utcoffset() is None
        ):
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        return completed_at.timestamp()
    except Exception:
        return time.time()


def _chat_notification_ready(chat_id: str, notification: Any) -> bool:
    if chat_id in active_processing_sessions:
        return False
    buffer_started = max(
        _notification_completed_ts(notification),
        recently_completed_sessions.get(chat_id, 0),
    )
    return time.time() - buffer_started >= PING_COMPLETION_BUFFER_SECONDS


def _agent_thread_caller_active(caller_agent: str, conversation_id: str) -> bool:
    try:
        for entry in running_agents.snapshot_all_sync():
            if entry.get("agent") == caller_agent and entry.get("conversation_id") == conversation_id:
                return True
    except Exception:
        return False
    return False


def _agent_thread_notification_ready(caller_agent: str, conversation_id: str, notification: Any) -> bool:
    if _agent_thread_caller_active(caller_agent, conversation_id):
        return False
    caller_completed_at = running_agents.recently_completed_at_sync(caller_agent, conversation_id) or 0
    buffer_started = max(_notification_completed_ts(notification), caller_completed_at)
    return time.time() - buffer_started >= PING_COMPLETION_BUFFER_SECONDS


async def agent_notification_wakeup_loop():
    """Background task to check for stale agent notifications and trigger wake-ups.

    When ping mode agents complete but the user hasn't sent a message within 30 seconds,
    this loop automatically wakes Claude up with the notifications as a hidden user message.

    Key design decisions for concurrency safety:
    - Eligibility is checked before claiming, so a wake loop never marks a
      notification in-flight while blocked behind the caller's active turn.
    - Claiming transitions pending -> delivering; delivered is written only
      after the caller-visible wake/injection has completed.
    - Batches all notifications for the same target into one delivery.
    - Holds chat_lock for chat delivery, preventing user messages from
      interleaving with the wake-up save.
    """
    logger.info("Agent Notification Wake-up Loop Started")

    while True:
        try:
            await asyncio.sleep(15)  # Check every 15 seconds

            # Import notification queue
            try:
                agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
                if str(agents_dir) not in sys.path:
                    sys.path.insert(0, str(agents_dir))
                from agent_notifications import get_notification_queue
            except ImportError:
                continue

            queue = get_notification_queue()

            pending = queue.get_pending()

            if not pending:
                continue

            # Group only notifications whose caller is idle and whose 30s buffer
            # has elapsed. User messages during the buffer use the inline
            # working-memory-shaped path instead.
            by_chat: dict[str, list] = defaultdict(list)
            by_agent_thread: dict[Tuple[str, str], list] = defaultdict(list)
            for notification in pending:
                source_id = notification.source_chat_id
                if not source_id:
                    logger.warning(f"Notification {notification.id} has no source_chat_id, skipping")
                    continue
                agent_target = _parse_agent_thread_notification_source(source_id)
                if agent_target:
                    caller_agent, conversation_id = agent_target
                    if _agent_thread_notification_ready(caller_agent, conversation_id, notification):
                        by_agent_thread[agent_target].append(notification)
                else:
                    if _chat_notification_ready(source_id, notification):
                        by_chat[source_id].append(notification)

            if not by_chat and not by_agent_thread:
                continue

            # Process each agent-thread batch first. These are silent scheduled
            # caller continuations, not UI chat wake-ups.
            for (caller_agent, conversation_id), notifications in by_agent_thread.items():
                try:
                    async with asyncio.timeout(NOTIFICATION_BATCH_TIMEOUT):
                        await _process_agent_thread_notification_batch(
                            caller_agent, conversation_id, notifications, queue
                        )
                except TimeoutError:
                    agent_names = [n.agent for n in notifications]
                    try:
                        queue.release_delivery([n.id for n in notifications])
                    except Exception:
                        pass
                    logger.error(
                        f"Agent-thread notification batch timed out after "
                        f"{NOTIFICATION_BATCH_TIMEOUT}s for {caller_agent} "
                        f"thread {conversation_id} (agents: {agent_names})"
                    )
                except Exception as e:
                    logger.error(
                        f"Error processing agent-thread notification batch for "
                        f"{caller_agent} thread {conversation_id}: {e}",
                        exc_info=True,
                    )

            # Process each chat's batch of notifications
            for chat_id, notifications in by_chat.items():
                try:
                    async with asyncio.timeout(NOTIFICATION_BATCH_TIMEOUT):
                        await _process_notification_batch(chat_id, notifications, queue)
                except TimeoutError:
                    agent_names = [n.agent for n in notifications]
                    try:
                        queue.release_delivery([n.id for n in notifications])
                    except Exception:
                        pass
                    logger.error(f"Notification batch timed out after {NOTIFICATION_BATCH_TIMEOUT}s for chat {chat_id} (agents: {agent_names})")
                except Exception as e:
                    logger.error(f"Error processing notification batch for chat {chat_id}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Agent Notification Wake-up Error: {e}", exc_info=True)


async def _process_agent_thread_notification_batch(
    caller_agent: str,
    conversation_id: str,
    notifications: list,
    queue,
) -> None:
    """Resume an agent caller for ping completions from silent/scheduled contexts."""
    agent_names = [n.agent for n in notifications]
    agent_names_str = ", ".join(agent_names)
    claimed = queue.claim_by_ids([n.id for n in notifications])
    if not claimed:
        logger.info(
            f"Agent-thread notification batch for {caller_agent} thread {conversation_id} "
            "had nothing left to claim"
        )
        return
    notifications = claimed
    claimed_ids = [n.id for n in claimed]

    try:
        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
        if str(agents_dir) not in sys.path:
            sys.path.insert(0, str(agents_dir))
        from registry import get_registry
        from runner import invoke_agent

        registry = get_registry()
        if not registry.get(caller_agent):
            logger.error(
                f"Agent-thread ping target '{caller_agent}' is not a registered agent; "
                f"cannot resume thread {conversation_id}"
            )
            queue.release_delivery(claimed_ids)
            return

        notification_parts = []
        for n in notifications:
            notification_parts.append(f'''<agent-completion agent="{n.agent}">
**Invoked at:** {n.invoked_at.strftime('%Y-%m-%d %H:%M:%S')}
**Completed at:** {n.completed_at.strftime('%Y-%m-%d %H:%M:%S')}

**Agent Response:**
{n.agent_response}
</agent-completion>''')

        count_str = (
            f"{len(notifications)} agent(s)"
            if len(notifications) > 1
            else f'Agent "{notifications[0].agent}"'
        )
        notification_prompt = f'''<agent-completion-notification count="{len(notifications)}">
{count_str} completed their task(s).

{chr(10).join(notification_parts)}
</agent-completion-notification>

You are being re-invoked because you requested ping mode from a silent or scheduled agent context. Continue the existing agent-to-agent thread, review the completed response(s), and take any necessary follow-up action. If no action is needed, finish briefly.'''

        result = await invoke_agent(
            name=caller_agent,
            prompt=notification_prompt,
            mode="foreground",
            source_chat_id=None,
            conversation_id=conversation_id,
            caller_agent="agent_notification_wakeup",
        )

        if isinstance(result, dict) and result.get("error"):
            logger.error(
                f"Failed to resume caller agent '{caller_agent}' for ping "
                f"thread {conversation_id}: {result.get('error')}"
            )
            queue.release_delivery(claimed_ids)
            return

        queue.mark_delivered(claimed_ids)
        logger.info(
            f"Completed agent-thread ping wake-up for {caller_agent} on "
            f"thread {conversation_id} ({agent_names_str})"
        )
    except Exception as e:
        queue.release_delivery(claimed_ids)
        logger.error(
            f"Agent-thread ping wake-up failed for {caller_agent} "
            f"thread {conversation_id} ({agent_names_str}): {e}",
            exc_info=True,
        )


async def _process_notification_batch(chat_id: str, notifications: list, queue) -> None:
    """Process a batch of notifications for a single chat_id in one Claude call.

    Holds the chat lock for the entire operation to prevent interleaving with
    user messages or other wake-up batches for the same chat.
    """
    agent_names = [n.agent for n in notifications]
    agent_names_str = ", ".join(agent_names)

    # Load existing chat to verify it exists before acquiring lock
    existing_chat = chat_manager.load_chat(chat_id)
    if not existing_chat:
        logger.warning(f"Chat {chat_id} not found for notification wake-up batch ({agent_names_str}), skipping")
        return

    # Acquire chat lock to prevent race conditions with concurrent user messages
    chat_lock = get_chat_lock(chat_id)
    async with chat_lock:
        if chat_id in active_processing_sessions:
            logger.info(f"Wake-up: chat {chat_id} became active before delivery; leaving notifications pending")
            return

        claimed = queue.claim_by_ids([n.id for n in notifications])
        if not claimed:
            logger.info(f"Wake-up: no notifications left to claim for chat {chat_id}")
            return
        notifications = claimed
        claimed_ids = [n.id for n in claimed]

        # Re-load chat inside the lock (may have changed since we checked)
        existing_chat = chat_manager.load_chat(chat_id)
        if not existing_chat:
            queue.release_delivery(claimed_ids)
            return

        # Build conversation history for context injection
        conversation_history = existing_chat.get("messages", [])

        # Build a combined notification prompt for all agents in this batch
        notification_parts = []
        for n in notifications:
            notification_parts.append(f"""<agent-completion agent="{n.agent}">
**Invoked at:** {n.invoked_at.strftime('%Y-%m-%d %H:%M:%S')}
**Completed at:** {n.completed_at.strftime('%Y-%m-%d %H:%M:%S')}

**Agent Response:**
{n.agent_response}
</agent-completion>""")

        count_str = f"{len(notifications)} agent(s)" if len(notifications) > 1 else f'Agent "{notifications[0].agent}"'
        notification_prompt_raw = f"""<agent-completion-notification count="{len(notifications)}">
{count_str} completed their task(s).

{chr(10).join(notification_parts)}
</agent-completion-notification>

Please review the agent response(s) and take any necessary follow-up action. If there are results to report, summarize them for the user. If there are errors, explain what went wrong and suggest next steps."""

        # Track active session for wake-up processing
        _wakeup_agent = existing_chat.get("agent") if existing_chat else None
        await _record_chat_session_started(chat_id, _wakeup_agent, count_str + " completed")

        # Start fresh session with conversation history injected
        notification_prompt = _build_history_context(conversation_history, notification_prompt_raw)
        logger.info(f"Wake-up: fresh SDK session with {len(conversation_history)} messages of history, {len(notifications)} notifications (chat_id: {chat_id}, agents: {agent_names_str})")
        claude = ClaudeWrapper(session_id="new", cwd=ROOT_DIR, chat_id=chat_id, chat_messages=conversation_history)

        # Register wrapper so interrupt/reconnect can find it
        active_claude_wrappers[chat_id] = claude

        # --- Segment/tool tracking (same approach as _collect_structured_output) ---
        all_segments = []
        current_segment = []
        pending_tool_calls_wakeup = {}  # tool_id -> {name, args}
        completed_tool_calls_wakeup = []  # [(segment_index, tool_call_dict), ...]
        actual_session_id = chat_id
        error_content = []

        def finalize_wakeup_segment():
            nonlocal current_segment
            if current_segment:
                text = "".join(current_segment).strip()
                if text:
                    all_segments.append(text)
                current_segment = []

        # Notify clients that wake-up is starting (so they see streaming)
        await broadcast_to_session(chat_id, {
            "type": "status",
            "text": f"{count_str} completed - processing..." if len(notifications) == 1 else f"{len(notifications)} agents completed - processing...",
            "isProcessing": True
        })

        # Resolve agent config for wake-up handling — use the chat's agent
        try:
            agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
            if str(agents_dir) not in sys.path:
                sys.path.insert(0, str(agents_dir))
            from registry import get_registry
            registry = get_registry()

            # Prefer the agent associated with this chat
            chat_agent_name = existing_chat.get("agent")
            wakeup_agent_config = registry.get(chat_agent_name) if chat_agent_name else None

            # Fall back to default agent, then first available
            if not wakeup_agent_config:
                wakeup_agent_config = registry.get_default_agent()
            if not wakeup_agent_config:
                all_configs = registry.get_all_configs()
                wakeup_agent_config = next(iter(all_configs.values()), None) if all_configs else None
        except Exception:
            wakeup_agent_config = None

        if not wakeup_agent_config:
            logger.error("No agent config available for wake-up handler")
            queue.release_delivery(claimed_ids)
            await _record_chat_session_ended(chat_id)
            return

        try:
            event_count = 0
            async for event in claude.run_chat(notification_prompt, agent_config=wakeup_agent_config, conversation_history=conversation_history):
                event_count += 1
                event_type = event.get("type", "unknown")

                if event_type == "session_init":
                    actual_session_id = event.get("id", chat_id)
                    logger.info(f"Wake-up session initialized: {actual_session_id}")

                elif event_type == "content_delta":
                    text = event.get("text", "")
                    if text:
                        current_segment.append(text)
                        await broadcast_to_session(chat_id, {
                            "type": "content_delta",
                            "text": text
                        })

                elif event_type == "tool_start":
                    logger.info(f"WAKEUP_TOOL: tool_start name={event.get('name')} id={event.get('id')} segments_so_far={len(all_segments)} current_seg_len={len(current_segment)}")
                    finalize_wakeup_segment()
                    # Also stash from tool_start in case tool_use doesn't fire
                    # (defensive: some SDK paths may only emit tool_start)
                    ts_tool_id = event.get("id")
                    ts_tool_name = event.get("name", "tool")
                    if ts_tool_id and ts_tool_id not in pending_tool_calls_wakeup:
                        pending_tool_calls_wakeup[ts_tool_id] = {"name": ts_tool_name, "args": "{}"}
                    await broadcast_to_session(chat_id, event)

                elif event_type == "tool_use":
                    logger.info(f"WAKEUP_TOOL: tool_use name={event.get('name')} id={event.get('id')} segments_so_far={len(all_segments)} current_seg_len={len(current_segment)}")
                    finalize_wakeup_segment()
                    tool_id = event.get("id")
                    tool_name = event.get("name", "tool")
                    tool_args = event.get("args", "{}")
                    if tool_id:
                        # Overwrite any partial stash from tool_start with full args
                        pending_tool_calls_wakeup[tool_id] = {"name": tool_name, "args": tool_args}
                    # Broadcast as tool_start for client
                    await broadcast_to_session(chat_id, {
                        "type": "tool_start",
                        "name": tool_name,
                        "id": tool_id,
                        "args": tool_args
                    })

                elif event_type == "tool_end":
                    tool_end_id = event.get("id")
                    tool_end_name = event.get("name", "")
                    tool_end_output = event.get("output", "")
                    tool_end_error = event.get("is_error", False)
                    logger.info(f"WAKEUP_TOOL: tool_end name={tool_end_name} id={tool_end_id} segments_so_far={len(all_segments)} pending_keys={list(pending_tool_calls_wakeup.keys())}")
                    stashed = pending_tool_calls_wakeup.pop(tool_end_id, None) if tool_end_id else None
                    if stashed:
                        try:
                            tc = serialize_tool_call(
                                tool_name=stashed["name"],
                                args_raw=stashed["args"],
                                output=tool_end_output,
                                is_error=tool_end_error,
                                tool_id=tool_end_id,
                            )
                            tc["timestamp"] = int(time.time())
                            completed_tool_calls_wakeup.append((len(all_segments), tc))
                            logger.info(f"WAKEUP_TOOL: Recorded tool_call '{stashed['name']}' at segment_idx={len(all_segments)}")
                        except Exception as ser_err:
                            logger.warning(f"Wake-up tool serialization error: {ser_err}")
                    elif tool_end_name:
                        try:
                            tc = serialize_tool_call(
                                tool_name=tool_end_name,
                                args_raw={},
                                output=tool_end_output,
                                is_error=tool_end_error,
                                tool_id=tool_end_id,
                            )
                            tc["timestamp"] = int(time.time())
                            completed_tool_calls_wakeup.append((len(all_segments), tc))
                            logger.info(f"WAKEUP_TOOL: Recorded tool_call (fallback) '{tool_end_name}' at segment_idx={len(all_segments)}")
                        except Exception:
                            pass
                    else:
                        logger.warning(f"WAKEUP_TOOL: tool_end with no stash and no name! id={tool_end_id}")
                    # Broadcast tool_end to client
                    await broadcast_to_session(chat_id, event)

                elif event_type == "thinking_delta":
                    await broadcast_to_session(chat_id, {
                        "type": "thinking_delta",
                        "text": event.get("text", "")
                    })

                elif event_type == "error":
                    error_text = event.get("text", "")
                    error_content.append(error_text)
                    logger.error(f"Wake-up error event: {error_text}")
                    await broadcast_to_session(chat_id, {"type": "error", "text": error_text})

                elif event_type == "result_meta":
                    if event.get("is_error"):
                        err = event.get("error_text", "Unknown error")
                        error_content.append(err)
                        logger.error(f"Wake-up result error: {err}")

            # Finalize any trailing content
            finalize_wakeup_segment()
            logger.info(f"Wake-up completed: {event_count} events, {len(all_segments)} segments, {len(completed_tool_calls_wakeup)} tool calls, {len(error_content)} errors")

        except Exception as e:
            logger.error(f"Error running wake-up prompt for {agent_names_str}: {e}", exc_info=True)
            error_content.append(f"Error processing agent notification: {e}")
            # Finalize any partial content on error
            finalize_wakeup_segment()
        finally:
            active_claude_wrappers.pop(chat_id, None)
            await _record_chat_session_ended(chat_id)

        if error_content:
            queue.release_delivery(claimed_ids)
            logger.warning(
                f"Wake-up failed for {agent_names_str} -> chat {chat_id}; "
                "leaving notification(s) pending for retry"
            )
            await broadcast_to_session(chat_id, {
                "type": "state",
                "seq": 0,
                "sessionId": chat_id,
                "messages": existing_chat.get("display_messages") or existing_chat.get("messages", []),
                "isProcessing": False,
                "status": "idle",
                "agent": existing_chat.get("agent"),
                "cumulative_usage": existing_chat.get("cumulative_usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
                "pending_form": None,
                "todos": None,
            })
            return

        # Build interleaved messages (same as scheduler)
        interleaved = _build_interleaved_messages(all_segments, completed_tool_calls_wakeup)

        # Build flat text for notification preview
        assistant_response = " ".join(strip_tool_markers(s) for s in all_segments).strip()

        if interleaved:
            # Add hidden user message (notification trigger)
            latest_completed = max(n.completed_at for n in notifications)
            hidden_user_msg = {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": notification_prompt_raw,
                "hidden": True,
                "timestamp": int(latest_completed.timestamp() * 1000)
            }
            existing_chat["messages"].append(hidden_user_msg)
            existing_chat["messages"].extend(interleaved)

            # Also update display_messages (used by subscribe and state broadcasts).
            # Without this, wake-up responses are invisible — subscribe returns the
            # stale display_messages which doesn't include the new messages.
            if "display_messages" in existing_chat:
                existing_chat["display_messages"].append(hidden_user_msg)
                existing_chat["display_messages"].extend(interleaved)

            chat_manager.save_chat(chat_id, existing_chat)

            # Sync in-memory state so the next user message doesn't
            # overwrite disk with stale ConversationState (the root
            # cause of wake-up responses not persisting to history).
            if chat_id in active_conversations:
                active_conversations[chat_id].messages = existing_chat["messages"].copy()

            queue.mark_delivered(claimed_ids)
            logger.info(f"Triggered wake-up for {len(notifications)} notification(s): {agent_names_str} -> chat {chat_id}")

            # Broadcast state event so the client syncs the new messages.
            # The old 'done' event didn't work because the client's done handler
            # explicitly does NOT sync data.messages (to preserve streaming blocks).
            # The 'state' handler properly replaces messages with server's state.
            chat_agent = existing_chat.get("agent")
            await broadcast_to_session(chat_id, {
                "type": "state",
                "seq": 0,
                "sessionId": chat_id,
                "messages": existing_chat.get("display_messages") or existing_chat["messages"],
                "isProcessing": False,
                "status": "idle",
                "agent": chat_agent,
                "cumulative_usage": existing_chat.get("cumulative_usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
                "pending_form": None,
                "todos": None,
            })
        else:
            # The correctness contract is that the caller model invocation saw
            # the completion once. A no-op model response still satisfies that;
            # do not keep replaying the same ping just because there is no
            # visible assistant text to persist.
            latest_completed = max(n.completed_at for n in notifications)
            hidden_user_msg = {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": notification_prompt_raw,
                "hidden": True,
                "timestamp": int(latest_completed.timestamp() * 1000)
            }
            existing_chat["messages"].append(hidden_user_msg)
            if "display_messages" in existing_chat:
                existing_chat["display_messages"].append(hidden_user_msg)

            chat_manager.save_chat(chat_id, existing_chat)

            if chat_id in active_conversations:
                active_conversations[chat_id].messages = existing_chat["messages"].copy()

            queue.mark_delivered(claimed_ids)
            logger.info(
                f"Triggered model-only wake-up for {len(notifications)} "
                f"notification(s): {agent_names_str} -> chat {chat_id}"
            )
            # Still send state to clear processing state
            await broadcast_to_session(chat_id, {
                "type": "state",
                "seq": 0,
                "sessionId": chat_id,
                "messages": existing_chat.get("display_messages") or existing_chat.get("messages", []),
                "isProcessing": False,
                "status": "idle",
                "agent": existing_chat.get("agent"),
                "cumulative_usage": existing_chat.get("cumulative_usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
                "pending_form": None,
                "todos": None,
            })

        # Send user-facing notifications (toast/push) so user knows there's a new message
        decision = should_notify(
            chat_id=chat_id,
            is_silent=False,
            client_sessions=client_sessions
        )
        if decision.notify:
            preview = assistant_response[:200] if assistant_response else f"{count_str} completed"
            wakeup_title = existing_chat.get("title", "") if isinstance(existing_chat, dict) else ""
            if decision.use_toast:
                await send_notification(
                    client_sessions=client_sessions,
                    chat_id=chat_id,
                    preview=preview,
                    critical=False,
                    play_sound=decision.play_sound,
                    title=wakeup_title
                )
            if decision.use_push:
                await send_push_notification(
                    title=f"{count_str} completed" if len(notifications) == 1 else f"{len(notifications)} agents completed",
                    body=preview[:100],
                    chat_id=chat_id,
                    critical=False
                )
            logger.info(f"Wake-up notification: {decision.reason} (toast={decision.use_toast}, push={decision.use_push})")


@app.on_event("startup")
async def startup_event():
    global server_restart_info, restart_continuation

    # Setup signal handlers for graceful shutdown
    setup_signal_handlers()

    # ========== Register desk broker for leave_on_desk broadcasts ==========
    from desk_broker import register_broadcast
    register_broadcast(broadcast_to_all_clients)

    # ========== WAL Recovery: Check for unfinished work ==========
    wal = get_wal()
    recovery_state = wal.get_recovery_state()
    if recovery_state["has_recovery_work"]:
        logger.warning(f"WAL: Found unfinished work on startup!")
        logger.warning(f"WAL: {len(recovery_state['pending_messages'])} pending messages")
        logger.warning(f"WAL: {len(recovery_state['streaming_responses'])} incomplete responses")

        # Clear stale streaming responses without writing them into chat files.
        #
        # This recovery path was built for true token streaming. Codex headless
        # emits item-level completed message blocks, and stale WAL entries can
        # contain whole completed assistant turns. Appending them on startup
        # duplicates prior replies in the visible chat after a server restart.
        for resp in recovery_state["streaming_responses"]:
            chat_id = resp.get("chat_id")
            segments = resp.get("content_segments", [])
            recovered_chars = sum(len(s or "") for s in segments)
            logger.warning(
                f"WAL: Discarding stale streaming response for chat {chat_id} "
                f"({recovered_chars} chars); not appending recovered content to chat"
            )

        # FIX BUG 3: Clear ALL stale WAL entries on server restart
        # Any 'processing' status entries are now stale because the server was restarted
        # and no processing is actually happening. Clear them to prevent false positives.
        wal.clear_stale_on_restart()
        logger.info("WAL: Cleared stale entries after server restart")
    else:
        # Even if no recovery work, clear old entries
        wal.clear_old_entries(max_age_hours=24)

    # Check for previous server state (restart continuity)
    previous_state = load_server_state()
    if previous_state:
        logger.info(f"Server was previously shutdown at {previous_state.get('shutdown_time')}")
        logger.info(f"Had {len(previous_state.get('active_sessions', []))} active sessions")
        server_restart_info = previous_state

    # Check for restart continuation (Claude-initiated restart)
    restart_continuation = load_restart_continuation()
    if restart_continuation:
        sessions = restart_continuation.get("sessions", [])
        logger.info(
            f"Restart continuation pending: {len(sessions)} session(s) to resume "
            f"(source={restart_continuation.get('source')}, reason={restart_continuation.get('reason')})"
        )

    # Clear stale entries and register primary_claude in process registry
    try:
        clear_registry()
        register_process("primary_claude", task="active")
        logger.info("Registered primary_claude in process registry")
    except Exception as e:
        logger.warning(f"Failed to register primary_claude in process registry: {e}")

    asyncio.create_task(scheduler_loop())
    asyncio.create_task(agent_notification_wakeup_loop())
    asyncio.create_task(_background_processing_idle_watcher())
    asyncio.create_task(_salon_background_processing_watcher())

    # Clear any stale locks on agent-to-agent conversations left by a crash
    # or an unclean restart. Server just came up — nothing's legitimately
    # in-flight yet, so anything with a lock is stale.
    try:
        from agent_conversation_manager import get_manager as _get_agent_conv_mgr
        # max_age_minutes=0 → clear ALL locks. Server just came up; nothing
        # legitimate can be in-flight, so any lock is stale by definition.
        # (Previously 5min, which left locks acquired right before shutdown
        # un-cleared and blocked the next dispatch.)
        cleared = _get_agent_conv_mgr().sweep_stale_locks(max_age_minutes=0)
        if cleared:
            logger.info(f"Agent conversations: cleared {cleared} stale lock(s) on startup")
    except Exception as e:
        logger.warning(f"Agent conversations: startup lock sweep failed: {e}")

    # Salon (group chat) startup: clear stale locks + wire the dispatcher
    # into the salon_events bus. Dispatcher uses broadcast_to_all_clients to
    # push salon updates (new messages, convener decisions, typing, state).
    try:
        # max_age_minutes=0 → clear ALL locks (see rationale above).
        cleared = _salon_manager_mod.get_manager().sweep_stale_locks(max_age_minutes=0)
        if cleared:
            logger.info(f"Salons: cleared {cleared} stale lock(s) on startup")
        _salon_dispatcher_mod.init_dispatcher(broadcast_to_all_clients)
    except Exception as e:
        logger.warning(f"Salons: startup wiring failed: {e}", exc_info=True)

    # running_agents: log + clear any stale entries on startup. The in-memory
    # dict is built from scratch at module import so it should always be empty;
    # a non-empty result would indicate a module-reload bug (worth surfacing).
    try:
        stale = await running_agents.list_all()
        if stale:
            logger.warning(
                f"running_agents: {len(stale)} stale entries on startup — clearing"
            )
            await running_agents.clear_all()
    except Exception as e:
        logger.warning(f"running_agents: startup sweep failed: {e}")

    # If there's a restart continuation, launch the wakeup task
    if restart_continuation:
        asyncio.create_task(restart_continuation_wakeup())

    # Mount static files if build exists
    if os.path.exists(CLIENT_BUILD_DIR):
        app.mount("/assets", StaticFiles(directory=os.path.join(CLIENT_BUILD_DIR, "assets")), name="assets")
        logger.info(f"Serving static files from {CLIENT_BUILD_DIR}")
    else:
        logger.warning(f"Client build not found at {CLIENT_BUILD_DIR}. Run 'npm run build' in client/")


@app.on_event("shutdown")
async def shutdown_event():
    """Save state on graceful shutdown."""
    logger.info("Server shutting down, saving state...")
    save_server_state()
    save_continuation_on_shutdown()

    # Deregister all processes owned by this server PID
    try:
        deregister_by_pid()
        logger.info("Deregistered all processes from process registry")
    except Exception as e:
        logger.warning(f"Failed to deregister from process registry: {e}")


# --- Message Sync API (for reconnection recovery) ---

class SyncRequest(BaseModel):
    session_id: str
    last_message_id: Optional[str] = None
    last_timestamp: Optional[float] = None


class SyncResponse(BaseModel):
    status: str
    session_id: str
    messages: List[Dict[str, Any]]
    has_pending: bool
    pending_status: Optional[str] = None


@app.post("/api/chat/sync", response_model=SyncResponse)
def sync_chat_state(req: SyncRequest):
    """
    Sync chat state after reconnection.

    Client sends their last known state, server returns the delta.
    This ensures client always has the server's authoritative state.
    """
    session_id = req.session_id

    # Load chat from disk (authoritative source)
    chat_data = chat_manager.load_chat(session_id)
    if not chat_data:
        return SyncResponse(
            status="not_found",
            session_id=session_id,
            messages=[],
            has_pending=False
        )

    # Prefer display_messages (has blocks/thinking) over flat messages
    messages = _messages_for_display(chat_data, session_id)

    # Check if there's a pending message in the WAL for this session
    wal = get_wal()
    pending = wal.get_pending_for_session(session_id)

    if pending:
        return SyncResponse(
            status="has_pending",
            session_id=session_id,
            messages=messages,
            has_pending=True,
            pending_status=pending.status
        )

    # If client provided a last_message_id, only return messages after that
    if req.last_message_id:
        found_idx = None
        for i, msg in enumerate(messages):
            if msg.get("id") == req.last_message_id:
                found_idx = i
                break
        if found_idx is not None:
            messages = messages[found_idx + 1:]

    return SyncResponse(
        status="ok",
        session_id=session_id,
        messages=messages,
        has_pending=False
    )


@app.get("/api/chat/pending/{session_id}")
def get_pending_message(session_id: str):
    """
    Check if there's a pending message for this session.

    FIX BUG 3: Only report as actively processing if:
    1. There's a WAL entry for this session
    2. The status is 'processing'
    3. The message is recent (within last 5 minutes)
    4. There's an active ClaudeWrapper processing this session

    Stale entries (from before server restart) should not cause
    the UI to show "processing" state.
    """
    wal = get_wal()
    pending = wal.get_pending_for_session(session_id)

    if pending:
        # Check if this is actually being processed right now
        # Look for an active ClaudeWrapper for this session
        is_actively_processing = (
            pending.status == 'processing' and
            (session_id in active_claude_wrappers or
             session_id in active_processing_sessions)
        )

        # If status is 'processing' but no active wrapper, it's stale
        # Mark it as such in the response
        effective_status = pending.status
        if pending.status == 'processing' and not is_actively_processing:
            # Check age - if older than 5 minutes, definitely stale
            age_seconds = time.time() - pending.timestamp
            if age_seconds > 300:  # 5 minutes
                effective_status = 'stale'
                logger.info(f"Pending message {pending.msg_id} marked as stale (age: {age_seconds:.0f}s)")

        return {
            "has_pending": True,
            "msg_id": pending.msg_id,
            "status": effective_status,
            "timestamp": pending.timestamp,
            "ack_sent": pending.ack_sent
        }

    return {"has_pending": False}


# --- Google OAuth Re-Authentication ---

@app.get("/api/auth/google/status")
def google_auth_status():
    """Check if Google OAuth token is valid."""
    from google_auth_web import get_auth_status
    return get_auth_status()


@app.get("/api/auth/google/login")
def google_auth_login():
    """Start the Google OAuth flow. Redirects user to Google consent screen."""
    from google_auth_web import create_authorization_url
    try:
        auth_url, state = create_authorization_url()
        return RedirectResponse(url=auth_url)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auth/google/callback")
def google_auth_callback(code: str = None, state: str = None, error: str = None):
    """OAuth callback from Google. Exchanges code for token."""
    from google_auth_web import handle_callback

    if error:
        return HTMLResponse(
            content="<html><body style='font-family:system-ui;max-width:500px;margin:80px auto;text-align:center'>"
                    f"<h1>Authentication Failed</h1><p>Error: {html.escape(str(error))}</p>"
                    "<p><a href='/api/auth/google/login'>Try again</a></p>"
                    "</body></html>",
            status_code=400,
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    try:
        handle_callback(state, code)
        return HTMLResponse(
            content="<html><body style='font-family:system-ui;max-width:500px;margin:80px auto;text-align:center'>"
                    "<h1>&#10004; Authentication Successful</h1>"
                    "<p>Google services have been re-authenticated. You can close this tab.</p>"
                    "</body></html>"
        )
    except ValueError as e:
        return HTMLResponse(
            content="<html><body style='font-family:system-ui;max-width:500px;margin:80px auto;text-align:center'>"
                    f"<h1>Authentication Failed</h1><p>{html.escape(str(e))}</p>"
                    "<p><a href='/api/auth/google/login'>Try again</a></p>"
                    "</body></html>",
            status_code=400,
        )
    except Exception as e:
        logger.error(f"Google OAuth callback error: {e}", exc_info=True)
        return HTMLResponse(
            content="<html><body style='font-family:system-ui;max-width:500px;margin:80px auto;text-align:center'>"
                    "<h1>Authentication Error</h1><p>An unexpected error occurred.</p>"
                    "<p><a href='/api/auth/google/login'>Try again</a></p>"
                    "</body></html>",
            status_code=500,
        )


# Serve files binary-safe at /file/ paths (short alias for /api/raw/)
# Used by HTML apps rendered in editor iframes to load images, etc.
@app.get("/file/{file_path:path}")
def serve_file(file_path: str, request: Request):
    target_path = os.path.join(ROOT_DIR, file_path)
    if not os.path.abspath(target_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    return _file_response_with_etag(target_path, request)


# Catch-all route for SPA - must be LAST
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("ws/"):
        raise HTTPException(status_code=404)

    file_path = os.path.abspath(os.path.join(CLIENT_BUILD_DIR, full_path))
    if not file_path.startswith(CLIENT_BUILD_DIR):
        raise HTTPException(status_code=403, detail="Path traversal blocked")
    if os.path.isfile(file_path):
        return FileResponse(file_path)

    index_path = os.path.join(CLIENT_BUILD_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)

    raise HTTPException(status_code=404, detail="Frontend not built. Run 'npm run build' in client/")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
