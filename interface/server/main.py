"""
Second Brain Interface Server

FastAPI server providing:
- WebSocket chat interface to Claude
- File management API
- Chat history management
- Scheduled task execution
"""

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set
from contextlib import asynccontextmanager
from collections import defaultdict

from claude_wrapper import ClaudeWrapper, ChatManager, ConversationState
from notifications import should_notify, send_notification, NotificationDecision
from message_wal import init_wal, get_wal, MessageWAL


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

# Logging - output to both console and file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/home/debian/second_brain/interface/server/server.log')
    ]
)
logger = logging.getLogger("server")

app = FastAPI(title="Second Brain API")

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
CHATS_DIR = os.path.join(ROOT_DIR, ".claude", "chats")
WAL_DIR = os.path.join(ROOT_DIR, ".claude", "wal")
SERVER_STATE_FILE = os.path.join(ROOT_DIR, ".claude", "server_state.json")
RESTART_CONTINUATION_FILE = os.path.join(ROOT_DIR, ".claude", "restart_continuation.json")
os.makedirs(CHATS_DIR, exist_ok=True)
os.makedirs(WAL_DIR, exist_ok=True)

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
    state = {
        "shutdown_time": datetime.now().isoformat(),
        "active_sessions": list(active_conversations.keys()),
        "had_active_websockets": len(client_sessions) > 0
    }
    try:
        with open(SERVER_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        logger.info(f"Saved server state: {len(state['active_sessions'])} active sessions")
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


def setup_signal_handlers():
    """Setup graceful shutdown handlers."""
    def handle_shutdown(signum, frame):
        logger.info(f"Received signal {signum}, saving state before shutdown...")
        save_server_state()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)


# Track if server was restarted with active sessions
server_restart_info: Optional[Dict] = None

# Track pending restart continuation (for auto-resuming conversations)
restart_continuation: Optional[Dict] = None


def load_restart_continuation() -> Optional[Dict]:
    """Load restart continuation marker if it exists."""
    if not os.path.exists(RESTART_CONTINUATION_FILE):
        return None
    try:
        with open(RESTART_CONTINUATION_FILE, 'r') as f:
            continuation = json.load(f)
        # Remove the file after reading
        os.remove(RESTART_CONTINUATION_FILE)
        logger.info(f"Loaded restart continuation for session {continuation.get('session_id')}")
        return continuation
    except Exception as e:
        logger.error(f"Failed to load restart continuation: {e}")
        return None

# Import Scheduler Tool
SCRIPTS_DIR = os.path.join(ROOT_DIR, ".claude", "scripts")
if os.path.exists(SCRIPTS_DIR):
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        import scheduler_tool
    except ImportError:
        logger.warning("Could not import scheduler_tool")
        scheduler_tool = None
else:
    logger.warning("Scripts dir not found")
    scheduler_tool = None


# Long-Term Memory Librarian background task
async def _run_librarian_background():
    """Run the Librarian agent in the background."""
    try:
        ltm_scripts = os.path.join(ROOT_DIR, ".claude", "scripts", "ltm")
        if ltm_scripts not in sys.path:
            sys.path.insert(0, ltm_scripts)
        from librarian_agent import run_librarian_cycle

        logger.info("LTM: Starting background Librarian run")
        result = await run_librarian_cycle()
        logger.info(f"LTM: Librarian completed - {result.get('status')}, "
                   f"created {result.get('memories_created', 0)} memories")
    except Exception as e:
        logger.error(f"LTM: Background Librarian failed: {e}")


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
        for ws in client_sessions:
            try:
                await ws.send_json({
                    "type": "chat_title_update",
                    "session_id": chat_id,
                    "title": new_title,
                    "confidence": result.get("confidence", 0.5)
                })
            except:
                pass

    except Exception as e:
        logger.error(f"Titler: Background task failed: {e}")


# --- Pydantic Models ---

class FileRequest(BaseModel):
    path: str
    content: Optional[str] = None


class RenameRequest(BaseModel):
    path: str
    new_name: str


class EditMessageRequest(BaseModel):
    session_id: str
    message_id: str
    new_content: str


class RegenerateRequest(BaseModel):
    session_id: str
    message_id: str


class ChatUpdateRequest(BaseModel):
    title: Optional[str] = None
    messages: Optional[List[Dict[str, Any]]] = None


class UIConfigUpdateRequest(BaseModel):
    exclude_dirs: Optional[List[str]] = None
    exclude_files: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    default_editor_file: Optional[str] = None


# --- UI Config API ---

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


# --- File API ---

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
def read_file(file_path: str):
    target_path = os.path.join(ROOT_DIR, file_path)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404)
    with open(target_path, 'r', encoding='utf-8') as f:
        return {"content": f.read()}


@app.post("/api/file/{file_path:path}")
def save_file(file_path: str, req: FileRequest):
    target_path = os.path.join(ROOT_DIR, file_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(req.content or "")
    return {"status": "ok"}


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

    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="File not found")

    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="Destination already exists")

    if not os.path.abspath(old_path).startswith(ROOT_DIR) or \
       not os.path.abspath(new_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")

    os.rename(old_path, new_path)
    return {"status": "ok"}


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
    return data


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

    chat_manager.save_chat(session_id, existing)
    return {"status": "ok"}


# --- Chat Search API ---

# Initialize chat search (lazy loaded)
_chat_searcher = None

def get_chat_searcher():
    """Get or create the chat searcher instance."""
    global _chat_searcher
    if _chat_searcher is None:
        import sys
        scripts_dir = os.path.join(ROOT_DIR, ".claude", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from chat_search.searcher import get_searcher
        _chat_searcher = get_searcher()
    return _chat_searcher


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

    Args:
        q: Search query
        date_from: ISO date string for start filter
        date_to: ISO date string for end filter
        roles: Comma-separated roles to filter (e.g., "user,assistant")
        exclude_system: Exclude system/scheduled chats
        semantic_only: If True, only return semantic results (for progressive loading)
        limit: Maximum results to return

    Returns:
        Search results with scores and match types
    """
    import time
    start_time = time.time()

    searcher = get_chat_searcher()

    # Build filters
    from chat_search.searcher import SearchFilters
    from datetime import datetime as dt

    filters = SearchFilters(
        exclude_system=exclude_system,
        roles=roles.split(",") if roles else None,
        date_from=dt.fromisoformat(date_from).timestamp() if date_from else None,
        date_to=dt.fromisoformat(date_to).timestamp() if date_to else None,
    )

    # Perform search
    if semantic_only:
        results = searcher.semantic_search(q, filters, k=limit)
    else:
        results = searcher.keyword_search(q, filters, limit=limit)

    query_time = (time.time() - start_time) * 1000

    return ChatSearchResponse(
        results=[
            ChatSearchResult(
                message_id=r.message_id,
                chat_id=r.chat_id,
                chat_title=r.chat_title,
                role=r.role,
                content_preview=r.content_preview,
                timestamp=r.timestamp,
                score=r.score,
                match_type=r.match_type
            )
            for r in results
        ],
        total_count=len(results),
        semantic_pending=not semantic_only,  # Semantic still pending if this was keyword-only
        query_time_ms=query_time
    )


@app.post("/api/chat/search/refresh")
def refresh_search_index():
    """Manually refresh the search index."""
    searcher = get_chat_searcher()
    searcher.refresh()
    return {"status": "ok", "message": "Index refreshed"}


# --- Context Overhead API ---

CONTEXT_OVERHEAD_FILE = os.path.join(ROOT_DIR, ".claude", "context_overhead.json")

@app.get("/api/context-overhead")
def get_context_overhead():
    """Get base token overhead for context window calculation.

    Returns the estimated overhead from system prompt, tools, memory, etc.
    Used by the frontend to show accurate context usage.
    """
    if not os.path.exists(CONTEXT_OVERHEAD_FILE):
        # Return default if file doesn't exist
        return {
            "total_tokens": 11000,
            "percentage_of_200k": 5.5,
            "breakdown": {},
            "notes": ["Default estimate - run calculate_base_overhead.py to update"]
        }

    try:
        with open(CONTEXT_OVERHEAD_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load context overhead: {e}")
        return {"total_tokens": 11000, "percentage_of_200k": 5.5, "error": str(e)}


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


# --- WebSocket Chat ---

# Track connected clients with their visibility state
client_sessions: Dict[WebSocket, ClientSession] = {}
active_conversations: Dict[str, ConversationState] = {}
# Track the currently processing session (for MCP tools to know which chat they're in)
current_processing_session: Optional[str] = None

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
    """
    if not session_id:
        return

    dead = set()
    for ws in session_clients.get(session_id, set()):
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

# --- Session Streaming State (for reconnect recovery) ---
# This is the SINGLE SOURCE OF TRUTH for what the client should display
@dataclass
class SessionStreamingState:
    """Tracks the current streaming state for a session - sent to clients on subscribe."""
    status: str = "idle"  # idle, thinking, tool_use
    status_text: str = ""
    tool_name: Optional[str] = None
    streaming_content: str = ""  # Accumulated content being streamed
    last_updated: float = field(default_factory=time.time)
    # Pending form request - sent to reconnecting clients who missed the broadcast
    pending_form: Optional[Dict[str, Any]] = None

# Map of session_id -> streaming state
session_streaming_states: Dict[str, SessionStreamingState] = {}

# Track active tool heartbeat tasks for cancellation
# Key: session_id, Value: asyncio.Task
tool_heartbeat_tasks: Dict[str, asyncio.Task] = {}


async def send_tool_heartbeat(session_id: str, tool_name: str):
    """Send periodic heartbeat events while a tool is running to keep UI active.

    This prevents the client-side timeout from resetting to idle during
    long-running tool executions (e.g., Claude Code, long bash commands).
    """
    heartbeat_interval = 10  # seconds
    try:
        while True:
            await asyncio.sleep(heartbeat_interval)
            # Check if session is still in tool_use state
            state = session_streaming_states.get(session_id)
            if not state or state.status != "tool_use":
                break
            # Send heartbeat to keep UI alive
            await broadcast_to_session(session_id, {
                "type": "status",
                "text": f"Running {tool_name}...",
                "heartbeat": True
            })
            logger.debug(f"Tool heartbeat sent for {session_id}: {tool_name}")
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


# Per-chat locks to serialize message processing and prevent race conditions
# This ensures concurrent messages to the same chat are processed sequentially
chat_processing_locks: Dict[str, asyncio.Lock] = {}

def get_chat_lock(chat_id: str) -> asyncio.Lock:
    """Get or create a lock for a specific chat ID."""
    if chat_id not in chat_processing_locks:
        chat_processing_locks[chat_id] = asyncio.Lock()
    return chat_processing_locks[chat_id]

# Track pending form requests (for forms_show tool)
# Key: session_id, Value: {"form_id": str, "prefill": dict}
pending_form_requests: Dict[str, Dict[str, Any]] = {}

# Track recently completed sessions with timestamps - for reconnect fallback
# Key: session_id, Value: timestamp when processing completed
# This helps direct reconnecting clients to the right session even if they have old localStorage
recently_completed_sessions: Dict[str, float] = {}
RECENTLY_COMPLETED_TTL = 30.0  # Keep for 30 seconds after completion


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    global server_restart_info, restart_continuation

    await websocket.accept()
    # Create client session for visibility tracking
    client_sessions[websocket] = ClientSession(websocket=websocket)

    # Notify client if server was restarted
    if server_restart_info:
        try:
            await websocket.send_json({
                "type": "server_restarted",
                "shutdown_time": server_restart_info.get("shutdown_time"),
                "active_sessions": server_restart_info.get("active_sessions", []),
                "message": "Server was restarted. Your previous session should be preserved."
            })
        except Exception as e:
            logger.warning(f"Could not send restart notification: {e}")

    # Check for restart continuation - this triggers auto-resume of conversation
    if restart_continuation:
        continuation = restart_continuation
        restart_continuation = None  # Clear so only first client gets it

        try:
            session_id = continuation.get("session_id")
            continuation_prompt = continuation.get("continuation_prompt", "Restart completed. Please continue.")
            reason = continuation.get("reason", "")

            logger.info(f"Auto-continuing session {session_id} after restart")

            # Notify client about the continuation
            await websocket.send_json({
                "type": "restart_continuation",
                "session_id": session_id,
                "reason": reason,
                "message": "Continuing conversation after restart..."
            })

            # Load existing chat to get context
            chat_data = chat_manager.load_chat(session_id)
            context_messages = chat_data.get("messages", []) if chat_data else []

            # The continuation prompt is injected as context, not as a visible message
            # This way Claude continues naturally without cluttering the chat
            continuation_message = (
                f"[SYSTEM NOTICE - NOT VISIBLE TO USER]\n"
                f"Server restart completed successfully. Reason: {reason}\n"
                f"{continuation_prompt}\n"
                f"Continue the conversation naturally - acknowledge the restart briefly and proceed."
            )

            # Use handle_message with forceNewSession to start fresh Claude session
            # but preserve chat history context
            await handle_message(websocket, {
                "sessionId": session_id,
                "message": continuation_message,
                "msgId": f"system-restart-{datetime.now().timestamp()}",
                "forceNewSession": True,
                "preserveChatId": session_id,
                "contextMessages": context_messages,
                "isSystemContinuation": True  # Flag to handle specially
            })

        except Exception as e:
            logger.error(f"Failed to auto-continue after restart: {e}")

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

                # Register streaming state IMMEDIATELY (before task runs)
                global current_processing_session
                current_processing_session = state_key
                os.environ["CURRENT_CHAT_ID"] = state_key  # Export for MCP tools (ping mode)
                session_streaming_states[state_key] = SessionStreamingState(
                    status="thinking",
                    status_text="Starting...",
                    streaming_content=""
                )
                register_client(websocket, state_key)
                logger.info(f"PRE-TASK: Registered streaming state for {state_key}")

                # IMMEDIATELY send session_init so client can update localStorage
                # This prevents losing the chat ID if user refreshes before background task sends it
                try:
                    await websocket.send_json({
                        "type": "session_init",
                        "id": state_key
                    })
                    logger.info(f"PRE-TASK: Sent immediate session_init for {state_key}")
                except Exception:
                    pass  # Client may have already disconnected

                # Now start the background task - state is already registered
                asyncio.create_task(handle_message(websocket, data))
            elif action == "edit":
                # For edits, we know the chat_id upfront
                chat_id = data.get("sessionId")
                if chat_id:
                    current_processing_session = chat_id
                    os.environ["CURRENT_CHAT_ID"] = chat_id  # Export for MCP tools (ping mode)
                    session_streaming_states[chat_id] = SessionStreamingState(
                        status="thinking",
                        status_text="Re-processing...",
                        streaming_content=""
                    )
                    register_client(websocket, chat_id)
                asyncio.create_task(handle_edit(websocket, data))
            elif action == "regenerate":
                # For regenerate, we know the chat_id upfront
                session_id = data.get("sessionId")
                if session_id:
                    current_processing_session = session_id
                    os.environ["CURRENT_CHAT_ID"] = session_id  # Export for MCP tools (ping mode)
                    session_streaming_states[session_id] = SessionStreamingState(
                        status="thinking",
                        status_text="Regenerating...",
                        streaming_content=""
                    )
                    register_client(websocket, session_id)
                asyncio.create_task(handle_regenerate(websocket, data))
            elif action == "interrupt":
                await handle_interrupt(websocket, data)
            elif action == "visibility_update":
                # Update client's visibility state
                session = client_sessions.get(websocket)
                if session:
                    is_active = data.get("isActive", False)
                    chat_id = data.get("chatId")
                    session.update_visibility(is_active=is_active, chat_id=chat_id)
                    logger.info(f"Visibility update: active={is_active}, chat={chat_id}")
            elif action == "subscribe":
                # Client wants full state for a session - THIS IS THE KEY FOR RECONNECT
                await handle_subscribe(websocket, data)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected - background tasks will continue processing")
    except Exception as e:
        logger.error(f"WebSocket Error: {e}")
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
        except:
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
    logger.info(f"SUBSCRIBE: Client requesting session {requested_session_id}")

    # Register this client for the session (for broadcast)
    if requested_session_id and requested_session_id != "new":
        register_client(websocket, requested_session_id)

    # CRITICAL: Check if there's ANY active streaming session
    # Client might have old/wrong session ID in localStorage - doesn't matter
    # If we're actively streaming, return that state
    active_session_id = None
    streaming_state = None

    if current_processing_session and current_processing_session in session_streaming_states:
        # There's an active stream - use it
        active_session_id = current_processing_session
        streaming_state = session_streaming_states[current_processing_session]
        logger.info(f"SUBSCRIBE: Found active stream for {active_session_id} (client requested {requested_session_id})")

    # Check for recently completed sessions if no active stream
    # This handles the case where processing finished between user refresh and reconnect
    recent_session_id = None
    if not active_session_id and recently_completed_sessions:
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

    # Register for the effective session (may differ from requested)
    if effective_session_id and effective_session_id != "new":
        register_client(websocket, effective_session_id)

    # Default state
    messages = []
    cumulative_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # Load messages from disk (source of truth)
    if effective_session_id and effective_session_id != "new":
        chat_data = chat_manager.load_chat(effective_session_id)
        if chat_data:
            messages = chat_data.get("messages", [])
            cumulative_usage = chat_data.get("cumulative_usage", cumulative_usage)
            logger.info(f"SUBSCRIBE: Loaded {len(messages)} messages from disk for {effective_session_id}")

    is_processing = streaming_state is not None

    # Build the state response
    state_response = {
        "type": "state",
        "sessionId": effective_session_id,  # Tell client the CORRECT session ID
        "messages": messages,
        "cumulative_usage": cumulative_usage,
        "isProcessing": is_processing,
        "status": streaming_state.status if streaming_state else "idle",
        "statusText": streaming_state.status_text if streaming_state else "",
        "toolName": streaming_state.tool_name if streaming_state else None,
        "streamingContent": streaming_state.streaming_content if streaming_state else ""
    }

    logger.info(f"SUBSCRIBE: Sending state for {effective_session_id} - isProcessing={is_processing}, status={state_response['status']}, streamingContent={len(state_response['streamingContent'])} chars")

    await websocket.send_json(state_response)

    # If there's a pending form, send it to this reconnecting client
    # This handles mobile clients who missed the initial form_request broadcast
    if streaming_state and streaming_state.pending_form:
        logger.info(f"SUBSCRIBE: Sending pending form to reconnecting client for {effective_session_id}")
        await websocket.send_json(streaming_state.pending_form)


async def handle_message(websocket: WebSocket, data: dict):
    """Handle a new message from the user."""
    global current_processing_session

    session_id = data.get("sessionId", "new")
    prompt = data.get("message", "")
    msg_id = data.get("msgId") or str(uuid.uuid4())
    force_new_session = data.get("forceNewSession", False)
    preserve_chat_id = data.get("preserveChatId")  # For edit/regenerate, keep same chat file
    context_messages = data.get("contextMessages", [])  # For edit: messages before edit point
    is_system_continuation = data.get("isSystemContinuation", False)  # For restart continuation

    if not prompt:
        return

    # ========== PER-CHAT LOCK: Serialize concurrent messages to same chat ==========
    # Determine lock key early - this prevents race conditions where concurrent messages
    # both load null sdkSessionId and start separate SDK sessions
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
    global current_processing_session

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

    # Track which session is currently being processed (for MCP tools like restart_server)
    # Priority: early_chat_id (pre-generated) > preserve_chat_id > session_id
    streaming_state_key = early_chat_id or preserve_chat_id or session_id
    current_processing_session = streaming_state_key
    os.environ["CURRENT_CHAT_ID"] = streaming_state_key  # Export for MCP tools (ping mode)
    logger.info(f"PROCESSING: current_processing_session={current_processing_session}, early_chat_id={early_chat_id}")

    # Streaming state was already initialized by websocket loop before task started
    # Just update it if needed (in case of any ID changes)
    if streaming_state_key not in session_streaming_states:
        session_streaming_states[streaming_state_key] = SessionStreamingState(
            status="thinking",
            status_text="Thinking...",
            streaming_content=""
        )
        logger.info(f"STREAMING_STATE: Late initialization for {streaming_state_key}")

    # Get or create conversation state
    # IMPORTANT: 'new' always creates a fresh state - don't reuse
    sdk_session_id_from_disk = None  # Track the actual SDK session ID for resumption

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
                # CRITICAL: Load the SDK session ID for proper resumption
                # Our chat ID is different from the SDK's session ID!
                sdk_session_id_from_disk = existing_chat.get("sdkSessionId")
                logger.info(f"Loaded existing chat: {len(conv.messages)} messages, "
                           f"{conv.cumulative_usage.get('total_tokens', 0)} cumulative tokens, "
                           f"sdkSessionId={sdk_session_id_from_disk}")

        if session_id != 'new':
            active_conversations[session_id] = conv
    else:
        conv = active_conversations[session_id]
        # CRITICAL: Even when reusing active conversation, load sdkSessionId from disk
        # The SDK session ID is stored on disk but not in the conv object
        chat_id_to_load = preserve_chat_id or session_id
        existing_chat = chat_manager.load_chat(chat_id_to_load)
        if existing_chat:
            sdk_session_id_from_disk = existing_chat.get("sdkSessionId")
            if sdk_session_id_from_disk:
                logger.info(f"Loaded sdkSessionId from disk for active conv: {sdk_session_id_from_disk}")

    # Determine effective session for Claude SDK
    # CRITICAL: Use the SDK session ID (not our chat ID) for resumption!
    # The SDK has its own session IDs that are different from our storage IDs.
    if force_new_session or session_id == "new":
        effective_session_id = "new"
        # For edits/regenerates with context_messages, inject full context
        if context_messages:
            context_str = "\n\n".join([
                f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
                for m in context_messages
            ])
            prompt = f"[Previous conversation for context - continue naturally]\n{context_str}\n\n[Current message]\n{prompt}"
            logger.info(f"MESSAGE: Injecting full context ({len(context_messages)} messages) for edit/regenerate")
    else:
        # Try to resume existing SDK session using the ACTUAL SDK session ID
        # If we don't have one stored, we'll need to start fresh with history injection
        if sdk_session_id_from_disk:
            effective_session_id = sdk_session_id_from_disk
            logger.info(f"MESSAGE: Resuming SDK session {sdk_session_id_from_disk} (chat ID: {session_id})")
        else:
            # No SDK session ID stored - need to start fresh with history
            # This happens for old chats that were saved before we started storing sdkSessionId
            effective_session_id = "new"
            if conv.messages:
                # Inject history since we can't resume
                history_str = "\n\n".join([
                    f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
                    for m in conv.messages[:-1]  # Exclude the message we're about to add
                ])
                if history_str:
                    prompt = f"[Previous conversation for context - continue naturally]\n{history_str}\n\n[Current message]\n{prompt}"
                    logger.info(f"MESSAGE: No SDK session ID stored, injecting history ({len(conv.messages)-1} messages)")
            logger.info(f"MESSAGE: No SDK session ID for chat {session_id}, starting fresh with history")

    # Add user message - use frontend's ID if provided, otherwise generate one
    # Skip for system continuations (restart) - those shouldn't appear in chat history
    if not is_system_continuation:
        user_msg_id = msg_id  # Use the same ID as in WAL

        # EARLY SAVE: Save user message immediately to prevent loss if connection drops
        # This handles the case where WebSocket dies during Claude's response
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
                conv.add_message("user", data.get("message", ""), user_msg_id)  # Store original, not context-wrapped

            title = existing.get("title") if existing else chat_manager.generate_title(data.get("message", ""))
            # Preserve the SDK session ID if we have it (for resumption after refresh)
            sdk_session_to_save = existing.get("sdkSessionId") if existing else sdk_session_id_from_disk
            chat_manager.save_chat(early_save_id, {
                "title": title,
                "sessionId": early_save_id,
                "sdkSessionId": sdk_session_to_save,  # Preserve for resumption
                "messages": conv.messages,
                "cumulative_usage": conv.cumulative_usage
            })
            logger.info(f"EARLY_SAVE: Saved user message to {early_save_id} (sdkSessionId={sdk_session_to_save})")

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
                    current_processing_session = early_save_id
                    os.environ["CURRENT_CHAT_ID"] = early_save_id  # Update env for MCP tools
                    streaming_state_key = early_save_id

            # BROADCAST: message_accepted to ALL clients viewing this session
            # This is the backend-authoritative architecture: server persists first,
            # then broadcasts to all clients. No client-side optimistic updates.
            await broadcast_to_session(early_save_id, {
                "type": "message_accepted",
                "sessionId": early_save_id,
                "message": {
                    "id": user_msg_id,
                    "role": "user",
                    "content": data.get("message", ""),
                    "timestamp": time.time(),
                    "status": "confirmed"
                }
            })
            logger.info(f"BROADCAST: message_accepted to session {early_save_id}")

        except Exception as e:
            logger.warning(f"EARLY_SAVE failed: {e}")

    # Broadcast status to let clients know we're starting
    # Use streaming_state_key which should be set correctly by now
    await broadcast_to_session(streaming_state_key, {"type": "status", "text": "Thinking..."})

    # Create wrapper and run
    claude = ClaudeWrapper(session_id=effective_session_id, cwd=ROOT_DIR)

    # Track the active wrapper for interrupt capability
    wrapper_key = current_processing_session or effective_session_id
    active_claude_wrappers[wrapper_key] = claude

    # Track message segments - each tool use creates a new segment
    current_segment = []  # Current text accumulator
    all_segments = []     # List of finalized text segments
    new_session_id = None
    current_tool_name = None
    had_error = False

    def finalize_segment():
        """Save current segment if it has content."""
        nonlocal current_segment
        if current_segment:
            text = "".join(current_segment).strip()
            if text:
                all_segments.append(text)
            current_segment = []

    # Inject pending agent notifications into the prompt (not system prompt)
    # This ensures notifications are visible even when resuming SDK sessions
    # where the system prompt may be cached from session creation
    try:
        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
        if str(agents_dir) not in sys.path:
            sys.path.insert(0, str(agents_dir))
        from agent_notifications import get_notification_queue

        queue = get_notification_queue()
        pending = queue.get_pending()

        if pending:
            notification_block = queue.format_for_injection(pending)
            # Prepend notifications to user's message so Claude sees them
            prompt = f"{notification_block}\n\n[User's message follows]\n{prompt}"
            # Mark as injected
            queue.mark_injected([n.id for n in pending])
            logger.info(f"Injected {len(pending)} agent notifications into user prompt")
    except Exception as e:
        logger.debug(f"Could not inject agent notifications into prompt: {e}")

    try:
        async for event in claude.run_prompt(prompt, conversation_history=conv.messages):
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
                            "id": preserve_chat_id
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
                            "id": effective_chat_id
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
                            session_streaming_states[actual_state_key] = SessionStreamingState(
                                status="thinking",
                                status_text="Processing..."
                            )
                            logger.info(f"STREAMING_STATE: Created for {actual_state_key}")
                    # Update current_processing_session to the actual ID
                    current_processing_session = actual_state_key
                    os.environ["CURRENT_CHAT_ID"] = actual_state_key  # Update env for MCP tools
                    logger.info(f"STREAMING_STATE: current_processing_session now {current_processing_session}")

                    # ========== CRITICAL: Save SDK session ID IMMEDIATELY ==========
                    # This prevents race condition where concurrent messages load null sdkSessionId
                    # and fall back to history injection mode (making Claude think it's a fresh conversation)
                    if new_session_id and not is_system_continuation:
                        save_chat_id = preserve_chat_id or early_save_id or new_session_id
                        try:
                            existing = chat_manager.load_chat(save_chat_id)
                            if existing:
                                existing["sdkSessionId"] = new_session_id
                                chat_manager.save_chat(save_chat_id, existing)
                                logger.info(f"SESSION_INIT: Saved sdkSessionId {new_session_id} to {save_chat_id}")
                            else:
                                # Chat doesn't exist yet - create minimal entry with SDK session ID
                                chat_manager.save_chat(save_chat_id, {
                                    "title": "Processing...",
                                    "sessionId": save_chat_id,
                                    "sdkSessionId": new_session_id,
                                    "messages": conv.messages,
                                    "cumulative_usage": conv.cumulative_usage
                                })
                                logger.info(f"SESSION_INIT: Created chat {save_chat_id} with sdkSessionId {new_session_id}")
                        except Exception as e:
                            logger.warning(f"SESSION_INIT: Failed to save sdkSessionId: {e}")

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
                    # ========== Update streaming state for reconnect recovery ==========
                    state_key = preserve_chat_id or new_session_id or streaming_state_key
                    if state_key in session_streaming_states:
                        session_streaming_states[state_key].streaming_content += text
                        session_streaming_states[state_key].status = "thinking"
                        session_streaming_states[state_key].status_text = ""
                    # BROADCAST to all clients viewing this session
                    await broadcast_to_session(state_key, {"type": "content_delta", "text": text})

            elif event_type == "content":
                # Complete text block - may come after deltas or instead of them
                text = event.get("text", "")
                if text and not current_segment:
                    # Only use if we didn't already stream it
                    current_segment.append(text)
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                await broadcast_to_session(state_key, {"type": "content", "text": text})

            elif event_type == "thinking_delta":
                # Extended thinking - can show as subtle status
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                await broadcast_to_session(state_key, {"type": "status", "text": "Thinking deeply..."})

            elif event_type == "thinking":
                # Complete thinking block
                pass  # Don't expose full thinking to UI

            elif event_type == "tool_start":
                # Finalize any content before tool starts
                finalize_segment()
                current_tool_name = event.get("name", "tool")
                # ========== WAL: Track tool in progress ==========
                if not is_system_continuation:
                    wal.set_tool_in_progress(new_session_id or effective_session_id, current_tool_name)
                    wal.new_segment(new_session_id or effective_session_id)  # New segment after tool
                # ========== Update streaming state for reconnect recovery ==========
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                if state_key in session_streaming_states:
                    session_streaming_states[state_key].status = "tool_use"
                    session_streaming_states[state_key].tool_name = current_tool_name
                    session_streaming_states[state_key].status_text = f"Running {current_tool_name}..."
                # ========== START HEARTBEAT for long-running tools ==========
                # This prevents client-side timeout during long tool executions
                start_tool_heartbeat(state_key, current_tool_name)
                # BROADCAST to all clients viewing this session
                await broadcast_to_session(state_key, {
                    "type": "status",
                    "text": f"Running {current_tool_name}..."
                })
                await broadcast_to_session(state_key, event)

            elif event_type == "tool_use":
                # Finalize any content before tool starts
                finalize_segment()
                current_tool_name = event.get("name", "tool")
                # ========== WAL: Track tool in progress ==========
                if not is_system_continuation:
                    wal.set_tool_in_progress(new_session_id or effective_session_id, current_tool_name)
                    wal.new_segment(new_session_id or effective_session_id)  # New segment after tool
                # ========== Update streaming state for reconnect recovery ==========
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                if state_key in session_streaming_states:
                    session_streaming_states[state_key].status = "tool_use"
                    session_streaming_states[state_key].tool_name = current_tool_name
                    session_streaming_states[state_key].status_text = f"Running {current_tool_name}..."

                # ========== Track forms_show args for later broadcast ==========
                # Tool name may be prefixed with mcp__brain__
                if current_tool_name and current_tool_name.endswith("forms_show"):
                    logger.info(f"FORMS_SHOW detected in tool_use, tracking args for state_key={state_key}")
                    try:
                        args_str = event.get("args", "{}")
                        args_data = json.loads(args_str) if isinstance(args_str, str) else args_str
                        pending_form_requests[state_key] = {
                            "form_id": args_data.get("form_id"),
                            "prefill": args_data.get("prefill", {})
                        }
                    except Exception:
                        pass

                # ========== START HEARTBEAT for long-running tools ==========
                # This prevents client-side timeout during long tool executions
                start_tool_heartbeat(state_key, current_tool_name)
                # BROADCAST to all clients viewing this session
                await broadcast_to_session(state_key, {
                    "type": "status",
                    "text": f"Running {current_tool_name}..."
                })
                await broadcast_to_session(state_key, {
                    "type": "tool_start",
                    "name": current_tool_name,
                    "args": event.get("args", "{}")
                })

            elif event_type == "tool_end":
                logger.info(f"TOOL_END event received: name={event.get('name')}, is_error={event.get('is_error')}")
                # ========== STOP HEARTBEAT - tool execution complete ==========
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                stop_tool_heartbeat(state_key)
                # ========== Update streaming state for reconnect recovery ==========
                if state_key in session_streaming_states:
                    session_streaming_states[state_key].status = "thinking"
                    session_streaming_states[state_key].tool_name = None
                    session_streaming_states[state_key].status_text = "Processing..."
                # BROADCAST to all clients viewing this session
                await broadcast_to_session(state_key, {"type": "status", "text": "Processing..."})
                await broadcast_to_session(state_key, event)

                # ========== Check for forms_show tool completion ==========
                # Broadcast form_request to UI when forms_show completes successfully
                # Tool name may be prefixed with mcp__brain__
                tool_name = event.get("name", "")
                is_error = event.get("is_error", False)
                if tool_name.endswith("forms_show") and not is_error and state_key in pending_form_requests:
                    try:
                        # Import forms_store to fetch form data
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

                                # Store in streaming state for reconnecting clients (e.g., mobile)
                                if state_key in session_streaming_states:
                                    session_streaming_states[state_key].pending_form = form_payload
                                    logger.info(f"Stored pending_form in streaming state for {state_key}")

                                await broadcast_to_session(state_key, form_payload)
                    except Exception as form_err:
                        logger.warning(f"Error broadcasting form_request: {form_err}")

                current_tool_name = None
                # ========== WAL: Clear tool in progress ==========
                if not is_system_continuation:
                    wal.set_tool_in_progress(new_session_id or effective_session_id, None)

            elif event_type == "error":
                had_error = True
                state_key = preserve_chat_id or new_session_id or streaming_state_key
                await broadcast_to_session(state_key, event)

            elif event_type == "result_meta":
                # Accumulate token usage for this conversation
                turn_usage = event.get("usage", {})
                turn_input = turn_usage.get("input_tokens", 0)
                turn_output = turn_usage.get("output_tokens", 0)
                cache_read = turn_usage.get("cache_read_input_tokens", 0)
                cache_creation = turn_usage.get("cache_creation_input_tokens", 0)

                # Get context info from the event (calculated by claude_wrapper)
                context_info = event.get("context", {})
                actual_context = context_info.get("actual_tokens", turn_input + cache_read + cache_creation)
                context_percent = context_info.get("percent_until_compaction", 0)

                # Track cumulative usage (including context info for persistence)
                conv.cumulative_usage["input_tokens"] = actual_context
                conv.cumulative_usage["output_tokens"] += turn_output
                conv.cumulative_usage["total_tokens"] = (
                    actual_context + conv.cumulative_usage["output_tokens"]
                )
                conv.cumulative_usage["actual_context"] = actual_context
                conv.cumulative_usage["context_percent"] = context_percent

                # BROADCAST event with context info to ALL clients
                cumulative_event = {
                    **event,  # Preserves 'context' field from claude_wrapper
                    "usage": {
                        "input_tokens": actual_context,
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

                # Check for stale session: is_error=True with no content means session expired
                if event.get("is_error") and not all_segments and not current_segment:
                    if effective_session_id != "new":
                        # Session was stale - retry with fresh session + history injection
                        logger.warning(f"SDK session {effective_session_id} expired, retrying with fresh session")
                        await broadcast_to_session(state_key, {"type": "status", "text": "Session expired, reconnecting..."})

                        # Build history-injected prompt - send full conversation
                        history_to_inject = conv.messages[:-1] if conv.messages else []  # Exclude the just-added user msg
                        retry_prompt = data.get("message", "")
                        if history_to_inject:
                            context_str = "\n\n".join([
                                f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
                                for m in history_to_inject
                            ])
                            retry_prompt = f"[Previous conversation for context - continue naturally]\n{context_str}\n\n[Current message]\n{retry_prompt}"
                            logger.info(f"MESSAGE: Injecting full conversation ({len(history_to_inject)} messages) for session recovery")

                        # Retry with fresh session
                        claude_retry = ClaudeWrapper(session_id="new", cwd=ROOT_DIR)
                        async for retry_event in claude_retry.run_prompt(retry_prompt, conversation_history=conv.messages):
                            retry_type = retry_event.get("type")
                            if retry_type == "session_init":
                                new_session_id = retry_event.get("id")
                                conv.session_id = new_session_id
                                active_conversations[new_session_id] = conv
                                await broadcast_to_session(state_key, retry_event)
                            elif retry_type == "content_delta":
                                text = retry_event.get("text", "")
                                if text:
                                    current_segment.append(text)
                                    await broadcast_to_session(state_key, {"type": "content_delta", "text": text})
                            elif retry_type == "content":
                                text = retry_event.get("text", "")
                                if text and not current_segment:
                                    current_segment.append(text)
                                await broadcast_to_session(state_key, {"type": "content", "text": text})
                            elif retry_type in ("tool_start", "tool_use", "tool_end"):
                                finalize_segment()
                                await broadcast_to_session(state_key, retry_event)
                            elif retry_type == "error":
                                had_error = True
                                await broadcast_to_session(state_key, retry_event)

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

    # Finalize any remaining content
    finalize_segment()
    logger.info(f"COMPLETE: {len(all_segments)} segments, had_error={had_error}")

    # Add each message segment as a separate assistant message
    for segment in all_segments:
        conv.add_message("assistant", segment)
        logger.info(f"SEGMENT: {segment[:50]}...")

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

    chat_manager.save_chat(chat_id_for_storage, {
        "title": title,
        "sessionId": chat_id_for_storage,
        "sdkSessionId": new_session_id,  # Store SDK session ID for proper resumption
        "messages": conv.messages,
        "cumulative_usage": conv.cumulative_usage
    })

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

    # Buffer exchange for long-term memory processing (Librarian)
    # Skip system continuations (restart messages) and error-only exchanges
    if not is_system_continuation and all_segments and not had_error:
        try:
            ltm_scripts = os.path.join(ROOT_DIR, ".claude", "scripts", "ltm")
            if ltm_scripts not in sys.path:
                sys.path.insert(0, ltm_scripts)
            from memory_throttle import add_exchange_to_buffer

            user_msg = data.get("message", "")
            assistant_msg = "\n\n".join(all_segments)

            exchange = {
                "user_message": user_msg,
                "assistant_message": assistant_msg,
                "session_id": chat_id_for_storage,
                "timestamp": datetime.now().isoformat()
            }
            should_run = add_exchange_to_buffer(exchange)
            logger.info(f"LTM: Buffered exchange, should_run_librarian={should_run}")

            # Optionally trigger Librarian if ready (async background task)
            if should_run:
                asyncio.create_task(_run_librarian_background())
        except Exception as e:
            logger.debug(f"LTM exchange buffering failed: {e}")

        # Trigger Chat Titler
        try:
            from chat_titler import should_retitle

            # Increment exchange count
            conv.exchange_count += 1
            exchange_count = conv.exchange_count

            # Get current title
            existing_chat = chat_manager.load_chat(chat_id_for_storage)
            current_title = existing_chat.get("title") if existing_chat else None

            # First exchange: always generate title
            # Every N exchanges: check if title should update
            if exchange_count == 1:
                logger.info(f"Titler: First exchange, generating initial title")
                asyncio.create_task(_run_titler_background(
                    chat_id_for_storage,
                    conv.messages,
                    None,
                    is_retitle=False
                ))
            elif should_retitle(exchange_count, current_title):
                logger.info(f"Titler: Exchange {exchange_count}, checking for title update")
                asyncio.create_task(_run_titler_background(
                    chat_id_for_storage,
                    conv.messages,
                    current_title,
                    is_retitle=True
                ))
        except Exception as e:
            logger.debug(f"Titler trigger failed: {e}")

    # ========== Clear streaming state - processing complete ==========
    # Note: current_processing_session is already declared global at top of handle_message
    state_key = chat_id_for_storage
    # Stop any running heartbeat for this session
    stop_tool_heartbeat(state_key)
    if state_key in session_streaming_states:
        del session_streaming_states[state_key]
        logger.info(f"STREAMING_STATE: Cleared for {state_key}")
    # Also clear current_processing_session but track it as recently completed
    if current_processing_session == state_key:
        # Add to recently completed sessions so reconnecting clients can find it
        recently_completed_sessions[state_key] = time.time()
        current_processing_session = None
        os.environ.pop("CURRENT_CHAT_ID", None)  # Clean up env var for MCP tools
        logger.info(f"STREAMING_STATE: Session {state_key} moved to recently_completed")
        # Clean up old entries
        now = time.time()
        expired = [sid for sid, ts in recently_completed_sessions.items() if now - ts > RECENTLY_COMPLETED_TTL]
        for sid in expired:
            del recently_completed_sessions[sid]

    # BROADCAST done event to ALL clients viewing this session
    logger.info(f"DONE: Broadcasting done event with sessionId={chat_id_for_storage}")
    await broadcast_to_session(chat_id_for_storage, {"type": "done", "sessionId": chat_id_for_storage})


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

    # BROADCAST truncation to ALL clients viewing this session
    # This ensures multi-device consistency during edits
    await broadcast_to_session(chat_id, {
        "type": "truncate",
        "messageId": message_id,
        "messages": context_messages,
        "sessionId": chat_id
    })
    logger.info(f"EDIT: Broadcasted truncation to session {chat_id}")

    # Send the edited message with a FRESH Claude session but WITH context
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

    # Find the assistant message to regenerate and the user message before it
    user_message = None
    context_messages = []

    for i, msg in enumerate(old_messages):
        if msg.get("id") == message_id:
            # This is the assistant message to regenerate
            # Context is everything before it, excluding the last user message
            # Find the user message that triggered this response
            for j in range(i - 1, -1, -1):
                if old_messages[j].get("role") == "user":
                    user_message = old_messages[j].get("content")
                    context_messages = old_messages[:j]  # Everything before that user message
                    break
            break

    if not user_message:
        await broadcast_to_session(session_id, {"type": "error", "text": "Could not find user message to regenerate from"})
        return

    logger.info(f"REGENERATE: Keeping {len(context_messages)} context messages")

    # Create new conversation state with context
    conv = ConversationState()
    conv.session_id = session_id
    conv.messages = context_messages.copy()
    active_conversations[session_id] = conv

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

    # Try the specific session ID first
    if session_id and session_id in active_claude_wrappers:
        wrapper = active_claude_wrappers[session_id]
    # Fall back to current processing session
    elif current_processing_session and current_processing_session in active_claude_wrappers:
        wrapper = active_claude_wrappers[current_processing_session]
    # Try any active wrapper as last resort
    elif active_claude_wrappers:
        wrapper = next(iter(active_claude_wrappers.values()))

    if wrapper:
        try:
            await wrapper.interrupt()
            interrupted = True
            logger.info(f"INTERRUPT: Successfully interrupted Claude session")
        except Exception as e:
            logger.error(f"INTERRUPT: Error interrupting: {e}")

    # BROADCAST interrupted status to ALL clients viewing this session
    effective_session_id = session_id or current_processing_session
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


# --- Scheduler ---

async def scheduler_loop():
    """Background task to check and execute scheduled tasks."""
    logger.info("Scheduler Loop Started")

    while True:
        try:
            await asyncio.sleep(60)  # Check every minute

            if not scheduler_tool:
                continue

            due_tasks = scheduler_tool.check_due_tasks()

            for task_info in due_tasks:
                # Handle both old format (string) and new format (dict with metadata)
                if isinstance(task_info, str):
                    prompt = task_info
                    is_silent = False
                    task_id = None
                    task_type = "prompt"
                    agent_name = None
                else:
                    prompt = task_info.get("prompt", "")
                    is_silent = task_info.get("silent", False)
                    task_id = task_info.get("id")
                    task_type = task_info.get("type", "prompt")
                    agent_name = task_info.get("agent")

                # Handle agent tasks separately
                if task_type == "agent" and agent_name:
                    logger.info(f"Executing Scheduled Agent Task: {agent_name} - {prompt[:80]}...")

                    try:
                        # Import agent runner
                        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
                        if str(agents_dir) not in sys.path:
                            sys.path.insert(0, str(agents_dir))
                        from runner import invoke_agent
                        from datetime import datetime

                        # Ensure agent_outputs directory exists
                        agent_outputs_dir = Path(ROOT_DIR) / "00_Inbox" / "agent_outputs"
                        agent_outputs_dir.mkdir(parents=True, exist_ok=True)

                        # Generate topic slug from prompt (first 30 chars, kebab-case)
                        topic_slug = prompt[:30].lower().strip()
                        topic_slug = '-'.join(topic_slug.split())
                        topic_slug = ''.join(c if c.isalnum() or c == '-' else '' for c in topic_slug)
                        topic_slug = topic_slug.strip('-')[:30] or 'task'

                        # Build routing instructions for scheduled agent output
                        today = datetime.now().strftime("%Y-%m-%d")
                        routing_instructions = f"""

SCHEDULED TASK CONTEXT:
You are running as a scheduled task, not a live invocation. Your output will be reviewed asynchronously by Primary Claude.

- Write your complete output to: 00_Inbox/agent_outputs/{today}_{agent_name}_{topic_slug}.md
- Include at the top of the file: the original task/question you were asked, so the reviewer has full context
- Your final reply to this prompt doesn't matter - all value should be in the artifact you create
"""
                        augmented_prompt = prompt + routing_instructions

                        # Invoke agent in scheduled mode (fire-and-forget, logged)
                        result = await invoke_agent(
                            name=agent_name,
                            prompt=augmented_prompt,
                            mode="scheduled",
                            source_chat_id=None  # No chat for scheduled tasks
                        )

                        logger.info(f"Scheduled agent task completed: {agent_name} - {result}")
                    except Exception as e:
                        logger.error(f"Scheduled agent task failed: {agent_name} - {e}")

                    continue  # Skip normal prompt handling for agent tasks

                logger.info(f"Executing Scheduled Task (silent={is_silent}): {prompt[:80]}...")

                # Create new session for scheduled task
                session_id = str(uuid.uuid4())
                claude = ClaudeWrapper(session_id="new", cwd=ROOT_DIR)

                assistant_content = []
                actual_session_id = session_id

                async for event in claude.run_prompt(prompt):
                    if event.get("type") == "session_init":
                        actual_session_id = event.get("id", session_id)
                    elif event.get("type") == "content":
                        assistant_content.append(event.get("text", ""))
                    elif event.get("type") == "tool_start":
                        tool_name = event.get("name", "tool")
                        assistant_content.append(f"\n\n*Running: `{tool_name}`...*\n")
                    elif event.get("type") == "tool_end":
                        output = event.get("output", "")
                        display_output = output[:500] + "..." if len(output) > 500 else output
                        assistant_content.append(f"\n*Result:* ```\n{display_output}\n```\n")
                    elif event.get("type") == "error":
                        assistant_content.append(f"\n\n**Error:** {event.get('text')}\n")

                # Generate title for scheduled tasks (emoji added at display time)
                prompt_preview = prompt.replace("👇 [SCHEDULED AUTOMATION] 👇\n", "")[:50]
                title = prompt_preview if prompt_preview else "Scheduled Task"

                # Save chat
                # Silent tasks are marked as system (hidden from main list)
                # Non-silent tasks appear in normal chat history
                chat_data = {
                    "title": title,
                    "sessionId": actual_session_id,
                    "is_system": is_silent,  # Only silent tasks are hidden
                    "scheduled": True,  # Flag for scheduled chats - emoji added at display time
                    "messages": [
                        {"id": str(uuid.uuid4()), "role": "system", "content": prompt},
                        {"id": str(uuid.uuid4()), "role": "assistant", "content": "".join(assistant_content)}
                    ]
                }

                chat_manager.save_chat(actual_session_id, chat_data)
                logger.info(f"Saved scheduled task result: {actual_session_id} (is_system={is_silent})")

                # Determine notification channels based on visibility
                decision = should_notify(
                    chat_id=actual_session_id,
                    is_silent=is_silent,
                    client_sessions=client_sessions
                )

                if decision.notify:
                    # Get message preview for notification
                    preview = "".join(assistant_content)[:200] if assistant_content else title

                    # Send WebSocket notification (toast/sound) to connected clients
                    if decision.use_toast:
                        await send_notification(
                            client_sessions=client_sessions,
                            chat_id=actual_session_id,
                            preview=preview,
                            critical=False,
                            play_sound=decision.play_sound
                        )

                        # Also send legacy scheduled_task_complete for backward compatibility
                        for ws in client_sessions:
                            try:
                                await ws.send_json({
                                    "type": "scheduled_task_complete",
                                    "session_id": actual_session_id,
                                    "title": title
                                })
                            except:
                                pass

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
            logger.error(f"Scheduler Error: {e}")


async def agent_notification_wakeup_loop():
    """Background task to check for stale agent notifications and trigger wake-ups.

    When a ping mode agent completes but the user hasn't sent a message for 15+ minutes,
    this loop automatically wakes Claude up with the notification as a hidden user message.
    Claude receives the standard prompt with conversation context and the notification content.
    """
    logger.info("Agent Notification Wake-up Loop Started")

    while True:
        try:
            await asyncio.sleep(60)  # Check every minute

            # Import notification queue
            try:
                agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
                if str(agents_dir) not in sys.path:
                    sys.path.insert(0, str(agents_dir))
                from agent_notifications import get_notification_queue
            except ImportError:
                continue

            queue = get_notification_queue()
            stale = queue.get_stale(threshold_minutes=15)

            if not stale:
                continue

            logger.info(f"Found {len(stale)} stale agent notifications, triggering wake-ups")

            for notification in stale:
                chat_id = notification.source_chat_id
                if not chat_id:
                    # No chat to wake up - mark as expired
                    queue.mark_expired([notification.id])
                    continue

                # Load existing chat to get conversation history for context
                existing_chat = chat_manager.load_chat(chat_id)
                if not existing_chat:
                    # Chat doesn't exist - mark notification as expired
                    queue.mark_expired([notification.id])
                    logger.warning(f"Chat {chat_id} not found for notification wake-up, marking expired")
                    continue

                # Build conversation history for semantic memory lookup
                conversation_history = existing_chat.get("messages", [])

                # CRITICAL: Get the SDK session ID for proper resumption
                # Our chat_id is NOT the same as Claude's SDK session ID!
                sdk_session_id = existing_chat.get("sdkSessionId")
                if not sdk_session_id:
                    logger.warning(f"Chat {chat_id} has no sdkSessionId - cannot resume, skipping wake-up")
                    queue.mark_expired([notification.id])
                    continue

                # Create the notification user message (will be hidden from UI)
                # Session resume handles conversation context automatically
                notification_prompt = f"""<agent-completion-notification>
Agent "{notification.agent}" has completed its task.

**Invoked at:** {notification.invoked_at.strftime('%Y-%m-%d %H:%M:%S')}
**Completed at:** {notification.completed_at.strftime('%Y-%m-%d %H:%M:%S')}

**Agent Response:**
{notification.agent_response}
</agent-completion-notification>

Please review the agent's response and take any necessary follow-up action. If there are results to report, summarize them for the user. If there are errors, explain what went wrong and suggest next steps."""

                # Mark notification as injected BEFORE running Claude
                # (since it's now the user message, not a system prompt injection)
                queue.mark_injected([notification.id])

                # Resume the existing session using the SDK session ID (not our chat ID!)
                logger.info(f"Wake-up: resuming SDK session {sdk_session_id} (chat_id: {chat_id})")
                claude = ClaudeWrapper(session_id=sdk_session_id, cwd=ROOT_DIR)

                streaming_content = []  # Collect streaming deltas
                final_content = []      # Collect final complete blocks
                actual_session_id = chat_id
                error_content = []

                # Notify clients that wake-up is starting (so they see streaming)
                await broadcast_to_session(chat_id, {
                    "type": "status",
                    "text": f"Agent {notification.agent} completed - processing...",
                    "isProcessing": True
                })

                try:
                    event_count = 0
                    async for event in claude.run_prompt(notification_prompt, conversation_history=conversation_history):
                        event_count += 1
                        event_type = event.get("type", "unknown")

                        if event_type == "session_init":
                            actual_session_id = event.get("id", chat_id)
                            logger.info(f"Wake-up session initialized: {actual_session_id}")
                        elif event_type == "content":
                            # Final complete text block - use this if available
                            text = event.get("text", "")
                            final_content.append(text)
                        elif event_type == "content_delta":
                            # Streaming delta - accumulate AND broadcast to clients
                            text = event.get("text", "")
                            streaming_content.append(text)
                            # Stream to clients viewing this chat
                            await broadcast_to_session(chat_id, {
                                "type": "content_delta",
                                "text": text
                            })
                        elif event_type == "thinking_delta":
                            # Broadcast thinking to clients
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
                            # Check if there's error info in the result
                            if event.get("is_error"):
                                err = event.get("error_text", "Unknown error")
                                error_content.append(err)
                                logger.error(f"Wake-up result error: {err}")

                    logger.info(f"Wake-up completed: {event_count} events, {len(streaming_content)} deltas, {len(final_content)} final blocks, {len(error_content)} errors")
                except Exception as e:
                    logger.error(f"Error running wake-up prompt for {notification.agent}: {e}", exc_info=True)
                    error_content.append(f"Error processing agent notification: {e}")

                # Use final content if available, otherwise use streaming content
                # (final blocks are the authoritative complete content)
                if final_content:
                    assistant_content = final_content
                else:
                    assistant_content = streaming_content

                # Append any errors
                if error_content:
                    assistant_content.append(f"\n\n**Errors:**\n" + "\n".join(error_content))

                # Build the final assistant response
                assistant_response = "".join(assistant_content).strip()

                # Only save messages if there's actual content
                if assistant_response:
                    # Add hidden user message (notification trigger - not shown in UI)
                    existing_chat["messages"].append({
                        "id": str(uuid.uuid4()),
                        "role": "user",
                        "content": notification_prompt,
                        "hidden": True,  # Hidden from UI but preserved for context
                        "timestamp": int(notification.completed_at.timestamp() * 1000)
                    })

                    # Add Claude's response
                    existing_chat["messages"].append({
                        "id": str(uuid.uuid4()),
                        "role": "assistant",
                        "content": assistant_response,
                        "timestamp": int(time.time() * 1000)
                    })

                    chat_manager.save_chat(chat_id, existing_chat)
                    logger.info(f"Triggered wake-up for agent notification: {notification.agent} -> chat {chat_id}")

                    # Send done event with updated messages
                    await broadcast_to_session(chat_id, {
                        "type": "done",
                        "sessionId": chat_id,
                        "messages": existing_chat["messages"]
                    })
                else:
                    logger.warning(f"Wake-up produced no content for {notification.agent} -> chat {chat_id}")
                    # Still send done to clear processing state
                    await broadcast_to_session(chat_id, {
                        "type": "done",
                        "sessionId": chat_id
                    })

                # Also send global notification (for users not viewing this chat)
                for ws in client_sessions:
                    try:
                        await ws.send_json({
                            "type": "agent_notification",
                            "agent": notification.agent,
                            "chat_id": chat_id,
                            "has_response": bool(assistant_response)
                        })
                    except:
                        pass

        except Exception as e:
            logger.error(f"Agent Notification Wake-up Error: {e}", exc_info=True)


@app.on_event("startup")
async def startup_event():
    global server_restart_info, restart_continuation

    # Setup signal handlers for graceful shutdown
    setup_signal_handlers()

    # ========== WAL Recovery: Check for unfinished work ==========
    wal = get_wal()
    recovery_state = wal.get_recovery_state()
    if recovery_state["has_recovery_work"]:
        logger.warning(f"WAL: Found unfinished work on startup!")
        logger.warning(f"WAL: {len(recovery_state['pending_messages'])} pending messages")
        logger.warning(f"WAL: {len(recovery_state['streaming_responses'])} incomplete responses")

        # Recover streaming responses to chat files
        for resp in recovery_state["streaming_responses"]:
            chat_id = resp.get("chat_id")
            segments = resp.get("content_segments", [])
            if chat_id and segments:
                # Load existing chat and append recovered content
                existing = chat_manager.load_chat(chat_id)
                if existing:
                    # Mark recovered content
                    recovered_content = "\n\n".join(s for s in segments if s.strip())
                    if recovered_content:
                        # Add a note about recovery
                        recovered_content += "\n\n[Response recovered after server restart - may be incomplete]"
                        existing["messages"].append({
                            "id": str(uuid.uuid4()),
                            "role": "assistant",
                            "content": recovered_content
                        })
                        chat_manager.save_chat(chat_id, existing)
                        logger.info(f"WAL: Recovered partial response for chat {chat_id}")

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
        logger.info(f"Restart continuation pending for session: {restart_continuation.get('session_id')}")

    asyncio.create_task(scheduler_loop())
    asyncio.create_task(agent_notification_wakeup_loop())

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

    messages = chat_data.get("messages", [])

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
             current_processing_session == session_id)
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


# Catch-all route for SPA - must be LAST
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("ws/"):
        raise HTTPException(status_code=404)

    file_path = os.path.join(CLIENT_BUILD_DIR, full_path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)

    index_path = os.path.join(CLIENT_BUILD_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)

    raise HTTPException(status_code=404, detail="Frontend not built. Run 'npm run build' in client/")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
