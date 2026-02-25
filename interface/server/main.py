"""
Second Brain Interface Server

FastAPI server providing:
- WebSocket chat interface to Claude
- File management API
- Chat history management
- Scheduled task execution
"""

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, UploadFile, File as FastAPIFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse, HTMLResponse, RedirectResponse
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
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Set, Tuple
from contextlib import asynccontextmanager
from collections import defaultdict

from claude_wrapper import ClaudeWrapper, ChatManager, ConversationState
from notifications import should_notify, send_notification, NotificationDecision
from message_wal import init_wal, get_wal, MessageWAL
from tool_serializers import serialize_tool_call, format_tool_for_history
from process_registry import register_process, deregister_by_pid, clear_registry


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
        agent = "ren"  # Default
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
    """Save restart continuation for any actively processing sessions.

    Called during graceful shutdown (SIGTERM/SIGINT). Only writes the continuation
    file if one doesn't already exist — this avoids overwriting a more detailed
    continuation saved by the MCP restart_server tool or the Settings UI endpoint.
    """
    if os.path.exists(RESTART_CONTINUATION_FILE):
        logger.info("Shutdown: continuation file already exists (restart tool or API saved it), skipping")
        return

    if not active_processing_sessions:
        logger.info("Shutdown: no active processing sessions, no continuation needed")
        return

    try:
        SCRIPTS_DIR_LOCAL = os.path.join(ROOT_DIR, ".claude", "scripts")
        if SCRIPTS_DIR_LOCAL not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR_LOCAL)
        import restart_tool as rt

        # Build map of actively processing sessions -> agent names
        all_active = {}
        for sid in active_processing_sessions:
            agent = "ren"
            try:
                stored = chat_manager.load_chat(sid)
                if stored and stored.get("agent"):
                    agent = stored["agent"]
            except Exception:
                pass
            all_active[sid] = agent

        # Pick the first active session as the nominal trigger
        first_session = next(iter(all_active))
        rt.save_continuation_state(
            session_id=first_session,
            reason="Server shutdown with active sessions (signal handler)",
            source="shutdown_handler",
            all_active_sessions=all_active
        )
        logger.info(f"Shutdown: saved continuation state for {len(all_active)} active session(s)")
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
        # Remove the file after reading
        os.remove(RESTART_CONTINUATION_FILE)

        # Normalize legacy format (single session_id) to new format (sessions list)
        if "sessions" not in continuation:
            continuation["sessions"] = [{
                "session_id": continuation.get("session_id"),
                "agent": continuation.get("source", "ren"),
                "role": "trigger",
                "message_count": continuation.get("message_count", 0),
            }]
            if "source" not in continuation:
                continuation["source"] = "ren"

        session_count = len(continuation.get("sessions", []))
        logger.info(
            f"Loaded restart continuation: {session_count} session(s) to resume, "
            f"source={continuation.get('source')}, reason={continuation.get('reason')}"
        )
        return continuation
    except Exception as e:
        logger.error(f"Failed to load restart continuation: {e}")
        return None


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
    Remove Claude Code tool call markers from content.

    The Agent SDK's Claude Code preset outputs tool status as markdown:
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
        for ws in list(client_sessions):
            try:
                await ws.send_json({
                    "type": "chat_title_update",
                    "session_id": chat_id,
                    "title": new_title,
                    "confidence": result.get("confidence", 0.5)
                })
            except Exception:
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


# --- Server Restart API ---

@app.post("/api/restart")
def restart_server_endpoint(rebuild: bool = False, reason: str = None):
    """Restart the Second Brain server via the restart script.

    Args:
        rebuild: If true, rebuild frontend before restart. Default: quick restart.
        reason: Why the restart was triggered. Defaults to "Restart via Settings UI".
    """
    import subprocess as sp

    if rebuild:
        script = os.path.join(ROOT_DIR, "interface", "restart-server-full.sh")
    else:
        script = os.path.join(ROOT_DIR, "interface", "restart-server.sh")

    if not os.path.exists(script):
        raise HTTPException(status_code=500, detail=f"Restart script not found: {script}")

    log_file = os.path.join(ROOT_DIR, ".claude", "server_restart.log")
    restart_reason = reason or "Restart via Settings UI"

    # Save server state for restart continuity
    save_server_state()

    # Save multi-session continuation marker so ALL active sessions resume after restart
    # Source is "settings_ui" since this endpoint is called from the Settings modal
    try:
        SCRIPTS_DIR_LOCAL = os.path.join(ROOT_DIR, ".claude", "scripts")
        if SCRIPTS_DIR_LOCAL not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR_LOCAL)
        import restart_tool as rt

        # Build map of actively processing sessions -> agent names
        all_active = {}
        for sid in active_processing_sessions:
            agent = "ren"
            try:
                stored = chat_manager.load_chat(sid)
                if stored and stored.get("agent"):
                    agent = stored["agent"]
            except Exception:
                pass
            all_active[sid] = agent

        if all_active:
            # Pick the first active session as the "trigger" session
            # (Settings UI isn't a session itself, so we use the first active one)
            first_session = next(iter(all_active))
            rt.save_continuation_state(
                session_id=first_session,
                reason=restart_reason,
                source="settings_ui",
                all_active_sessions=all_active
            )
            logger.info(f"Saved continuation state for {len(all_active)} active session(s) before UI restart")
    except Exception as e:
        logger.warning(f"Could not save continuation state for UI restart: {e}")

    # Launch the restart script in a detached subprocess (same pattern as MCP tool).
    # sleep 1 gives the HTTP response time to flush before the server dies.
    sp.Popen(
        f"sleep 1 && bash {script} > {log_file} 2>&1",
        shell=True,
        start_new_session=True,
        stdout=sp.DEVNULL,
        stderr=sp.DEVNULL,
    )

    mode = "full (with frontend rebuild)" if rebuild else "quick (server only)"
    logger.info(f"Server restart initiated via API — mode: {mode}, reason: {restart_reason}")

    return {"status": "restarting", "mode": mode, "reason": restart_reason}


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
    if not os.path.abspath(target_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404)
    with open(target_path, 'r', encoding='utf-8') as f:
        return {"content": f.read()}


@app.get("/api/raw/{file_path:path}")
def raw_file(file_path: str):
    """Serve a file as-is (binary-safe) for images, PDFs, etc."""
    target_path = os.path.join(ROOT_DIR, file_path)
    if not os.path.abspath(target_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404)
    # Cache images/static assets for 1 hour, revalidate after
    return FileResponse(target_path, headers={"Cache-Control": "public, max-age=3600, must-revalidate"})


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
    try:
        abs_path = validate_app_path(req.path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(req.data)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"App bridge write error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    Brain Bridge v2: Request-response Claude API for embedded apps.
    Uses the Agent SDK for consistent auth and infrastructure.
    """
    from claude_agent_sdk import query, ClaudeAgentOptions

    try:
        system_prompt = (
            "You are a helpful assistant embedded in a Second Brain app. "
            "Respond concisely and directly. When asked to return structured data (JSON, numbers, lists), "
            "return ONLY the requested format without markdown wrappers or explanations unless asked."
        )
        if req.system_hint:
            system_prompt += f"\n\nApp context: {req.system_hint}"

        options = ClaudeAgentOptions(
            model="sonnet",
            system_prompt=system_prompt,
            max_turns=1,
            permission_mode="bypassPermissions",
            setting_sources=[],
        )

        result_text = ""
        async for message in query(prompt=req.prompt, options=options):
            if hasattr(message, "result"):
                result_text = message.result

        logger.info(f"App Bridge askClaude: prompt={req.prompt[:80]}... response_len={len(result_text)}")
        return {"response": result_text}

    except Exception as e:
        logger.error(f"App Bridge askClaude error: {e}")
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
    return {"name": name, "config": config_yaml, "prompt": prompt_content}


@app.get("/api/tools/categories")
def list_tool_categories():
    """List available MCP tool categories for the Agent Builder."""
    try:
        from mcp_tools.constants import TOOL_CATEGORIES
        return {"categories": [
            {"name": cat, "tools": tools}
            for cat, tools in TOOL_CATEGORIES.items()
            if cat not in ("gardener",)  # Hide internal categories
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


@app.post("/api/agents")
def create_agent(req: AgentCreateRequest):
    """Create a new agent from the Agent Builder."""
    import yaml as _yaml
    import re as _re

    # Validate name
    name = req.name.strip().lower()
    if not _re.match(r'^[a-z][a-z0-9_]*$', name):
        raise HTTPException(status_code=400, detail="Name must be lowercase letters, numbers, and underscores, starting with a letter")
    reserved = {"ren", "background", "_template", "notifications", "__pycache__"}
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

    # Reload registry
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from registry import get_registry
    get_registry().reload()

    return {"status": "created", "name": name, "restart_required": True}


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
    for ws in list(client_sessions):
        try:
            await ws.send_json({
                "type": "chess_update",
                "game": game_state
            })
        except Exception:
            pass

    # Return context for Claude if it's now Claude's turn
    response = {
        "success": True,
        "game": game_state,
        "status": result.get("status", {})
    }

    # If it's Claude's turn and game isn't over, include a prompt
    if not game_state.get("game_over"):
        current_turn = "white" if "w" in game_state.get("fen", "").split()[1] else "black"
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
# Track all currently processing sessions (supports concurrent chats)
# Maps chat_id -> start_time for each active processing session
active_processing_sessions: Dict[str, float] = {}

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


async def broadcast_chat_created(chat_id: str, title: str, agent: str = None,
                                  is_system: bool = False, scheduled: bool = False):
    """Broadcast chat_created to ALL connected clients for history list updates."""
    msg = {
        "type": "chat_created",
        "chat": {"id": chat_id, "title": title, "updated": time.time(),
                 "is_system": is_system, "scheduled": scheduled, "agent": agent}
    }
    for ws_client in list(client_sessions):
        try:
            await ws_client.send_json(msg)
        except Exception:
            pass


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
    pending_form: Optional[Dict[str, Any]] = None
    todos: Optional[list] = None
    seq: int = 0
    last_updated: float = field(default_factory=time.time)

    # Internal tracking (not sent to clients)
    _current_blocks: List[ContentBlock] = field(default_factory=list)
    _current_msg_id: Optional[str] = None

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
            "pending_form": self.pending_form,
            "todos": self.todos,
        }

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


# --- History injection for fresh SDK sessions ---
# Every SDK session starts fresh (no --resume). Conversation context is injected
# into the prompt so Claude knows what was discussed. This eliminates "session
# expired" errors and ensures a single consistent prompt format across all paths
# (normal messages, edits, regenerates, scheduled tasks, wake-ups).
def _build_history_context(messages: List[Dict[str, Any]], current_message: str, limit: int = 50) -> str:
    """Build a prompt with conversation history prepended.

    Args:
        messages: Prior messages from chat storage (excluding the current message).
        current_message: The new user message to append.
        limit: Max number of prior messages to include (most recent).

    Returns:
        A single prompt string with history context + current message.
        If no prior messages, returns just the current message.
    """
    if not messages:
        return current_message

    recent = messages[-limit:]
    parts = []
    for m in recent:
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
            parts.append(f"Assistant: {content}")
        elif role == "system":
            parts.append(f"System: {content}")

    if not parts:
        return current_message

    history = "\n\n".join(parts)
    return f"[Previous conversation for context - continue naturally]\n{history}\n\n[Current message]\n{current_message}"


# Serialize prompt-type scheduled tasks so only one ClaudeWrapper runs at a time.
# Agent-type tasks bypass this lock because they use the agent runner which manages
# its own concurrency.  This prevents the "conversation not found" errors that
# occur when two prompt tasks race to create SDK sessions simultaneously.
scheduled_prompt_lock = asyncio.Lock()

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
                    _init_messages = list(_existing_chat_data.get("display_messages") or _existing_chat_data.get("messages", []))

                # Register streaming state IMMEDIATELY (before task runs)
                active_processing_sessions[state_key] = time.time()
                session_streaming_states[state_key] = SessionStreamingState(
                    status="streaming",
                    messages=_init_messages,
                )
                register_client(websocket, state_key)
                logger.info(f"PRE-TASK: Registered streaming state for {state_key}")

                # Determine agent for this chat
                ws_agent = None
                if session_id == "new":
                    ws_agent = data.get("agent")  # Only accept agent on new chats
                else:
                    stored = chat_manager.load_chat(session_id)
                    ws_agent = stored.get("agent") if stored else None

                # IMMEDIATELY send session_init so client can update localStorage
                # This prevents losing the chat ID if user refreshes before background task sends it
                try:
                    await websocket.send_json({
                        "type": "session_init",
                        "id": state_key,
                        "agent": ws_agent
                    })
                    logger.info(f"PRE-TASK: Sent immediate session_init for {state_key}, agent={ws_agent}")
                except Exception:
                    pass  # Client may have already disconnected

                # Now start the background task - state is already registered
                asyncio.create_task(handle_message(websocket, data))
            elif action == "edit":
                # For edits, we know the chat_id upfront
                chat_id = data.get("sessionId")
                if chat_id:
                    active_processing_sessions[chat_id] = time.time()
                    session_streaming_states[chat_id] = SessionStreamingState(
                        status="streaming",
                    )
                    register_client(websocket, chat_id)
                asyncio.create_task(handle_edit(websocket, data))
            elif action == "regenerate":
                # For regenerate, we know the chat_id upfront
                session_id = data.get("sessionId")
                if session_id:
                    active_processing_sessions[session_id] = time.time()
                    session_streaming_states[session_id] = SessionStreamingState(
                        status="streaming",
                    )
                    register_client(websocket, session_id)
                asyncio.create_task(handle_regenerate(websocket, data))
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
            elif action == "subscribe":
                # Client wants full state for a session - THIS IS THE KEY FOR RECONNECT
                await handle_subscribe(websocket, data)

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
            active_processing_sessions.pop(requested_session_id, None)
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
            state_response["cumulative_usage"] = cumulative_usage
            state_response["agent"] = chat_agent
            logger.info(f"SUBSCRIBE: Using block model snapshot for {effective_session_id} - {len(state_response['messages'])} messages, status={state_response['status']}")
        else:
            # No active streaming — load from disk (source of truth)
            chat_data = chat_manager.load_chat(effective_session_id)
            if chat_data:
                # Prefer display_messages (has blocks, thinking) over flat messages
                messages = chat_data.get("display_messages") or chat_data.get("messages", [])
                cumulative_usage = chat_data.get("cumulative_usage", cumulative_usage)
                chat_agent = chat_data.get("agent")
                logger.info(f"SUBSCRIBE: Loaded {len(messages)} messages from disk for {effective_session_id} (display_messages={'display_messages' in chat_data})")
            state_response = {
                "type": "state",
                "seq": 0,
                "sessionId": effective_session_id,
                "messages": messages,
                "isProcessing": False,
                "status": "idle",
                "agent": chat_agent,
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

    # If there's a pending form, send it to this reconnecting client
    # This handles mobile clients who missed the initial form_request broadcast
    if streaming_state and streaming_state.pending_form:
        logger.info(f"SUBSCRIBE: Sending pending form to reconnecting client for {effective_session_id}")
        await websocket.send_json(streaming_state.pending_form)


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

    # Track which session is currently being processed
    # Priority: early_chat_id (pre-generated) > preserve_chat_id > session_id
    streaming_state_key = early_chat_id or preserve_chat_id or session_id
    active_processing_sessions[streaming_state_key] = time.time()
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
                msg_images = data.get("images")
                conv.add_message("user", data.get("message", ""), user_msg_id, images=msg_images)  # Store original, not context-wrapped

                # Also add user message to streaming state for display_messages persistence
                # This ensures display_messages has the complete conversation (user + assistant w/ blocks)
                _ss_for_user_msg = session_streaming_states.get(streaming_state_key)
                if _ss_for_user_msg:
                    _ss_for_user_msg.messages.append(conv.messages[-1])

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

            title = existing.get("title") if existing else chat_manager.generate_title(data.get("message", ""))
            early_save_data = {
                "title": title,
                "sessionId": early_save_id,
                "messages": conv.messages,
                "cumulative_usage": conv.cumulative_usage
            }
            if agent_name:
                early_save_data["agent"] = agent_name
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
                    # Migrate active session tracking
                    active_processing_sessions.pop(streaming_state_key, None)
                    active_processing_sessions[early_save_id] = time.time()
                    streaming_state_key = early_save_id

            # BROADCAST: message_accepted to ALL clients viewing this session
            # This is the backend-authoritative architecture: server persists first,
            # then broadcasts to all clients. No client-side optimistic updates.
            accepted_msg = {
                "id": user_msg_id,
                "role": "user",
                "content": data.get("message", ""),
                "timestamp": time.time(),
                "status": "confirmed"
            }
            if data.get("images"):
                accepted_msg["images"] = data["images"]
            await broadcast_to_session(early_save_id, {
                "type": "message_accepted",
                "sessionId": early_save_id,
                "message": accepted_msg
            })
            logger.info(f"BROADCAST: message_accepted to session {early_save_id}")

            # Track the user message in streaming state for tab-switch recovery
            _ss = session_streaming_states.get(early_save_id) or session_streaming_states.get(streaming_state_key)
            if _ss:
                _ss.messages.append(accepted_msg)

            # BROADCAST: chat_created to ALL clients so history list updates in real-time
            if session_id == 'new' and not skip_message_add:
                await broadcast_chat_created(early_save_id, title, agent_name)
                logger.info(f"BROADCAST: chat_created for new chat {early_save_id}")

        except Exception as e:
            logger.warning(f"EARLY_SAVE failed: {e}")

    # Broadcast status to let clients know we're starting
    # Use streaming_state_key which should be set correctly by now
    await broadcast_to_session(streaming_state_key, {"type": "status", "text": "Thinking..."})

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
    claude = ClaudeWrapper(session_id=effective_session_id, cwd=ROOT_DIR, chat_id=chat_id_for_wrapper, chat_messages=conv.messages)

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

    # Inject pending agent notifications into the prompt (not system prompt)
    # This ensures notifications are visible even when resuming SDK sessions
    # where the system prompt may be cached from session creation
    try:
        agents_dir = Path(ROOT_DIR) / ".claude" / "agents"
        if str(agents_dir) not in sys.path:
            sys.path.insert(0, str(agents_dir))
        from agent_notifications import get_notification_queue

        queue = get_notification_queue()
        # Atomically claim pending notifications — prevents wake-up loop from
        # also grabbing the same notifications (read + mark in one lock)
        claimed = queue.claim_pending(chat_id=streaming_state_key)

        if claimed:
            notification_block = queue.format_for_injection(claimed)
            # Prepend notifications to user's message so Claude sees them
            prompt = f"{notification_block}\n\n[User's message follows]\n{prompt}"
            logger.info(f"Injected {len(claimed)} agent notifications into user prompt")
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
            prompt_gen = claude.run_chat(prompt_for_sdk, agent_config=agent_config, conversation_history=conv.messages)
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
                                _fallback_msgs = list(_fb_chat.get("display_messages") or _fb_chat.get("messages", []))
                            session_streaming_states[actual_state_key] = SessionStreamingState(
                                status="streaming",
                                messages=_fallback_msgs,
                            )
                            logger.info(f"STREAMING_STATE: Created for {actual_state_key}")
                    # Update active session tracking to the actual ID
                    active_processing_sessions.pop(streaming_state_key, None)
                    active_processing_sessions[actual_state_key] = time.time()
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
                is_error = event.get("is_error", False)

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
                        content=str(tool_end_output)[:2000] if tool_end_output else "",
                        is_error=is_error,
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
                                if state_key in session_streaming_states:
                                    session_streaming_states[state_key].pending_form = form_payload

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
                            for ws in list(client_sessions):
                                try:
                                    await ws.send_json({
                                        "type": "chess_update",
                                        "game": chess_game
                                    })
                                except Exception:
                                    pass
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
                            output=tool_end_output,
                            is_error=tool_end_error,
                            tool_id=tool_end_id,
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
                            output=tool_end_output,
                            is_error=tool_end_error,
                            tool_id=tool_end_id,
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
                if _restart_tool_name == "restart_server" and not is_error:
                    logger.info("RESTART: restart_server tool completed — halting stream for clean save")
                    restart_after_save = True
                    break

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
        "cumulative_usage": conv.cumulative_usage
    }
    if agent_name:
        final_save_data["agent"] = agent_name

    # Save display_messages (block-structured) for UI persistence.
    # This preserves thinking blocks, tool call metadata, and per-block rendering
    # across page reloads and server restarts.
    #
    # Strategy: SS messages have block-level detail (thinking, tool_use, etc.) but may
    # only cover the current turn (e.g. after restart). conv.messages has the complete
    # history but as flat text. Combine them: use SS where available, conv.messages for
    # older history that SS doesn't cover.
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

        n_ss = len(serialized_ss)
        # Count only user/assistant messages in conv.messages — tool_call and form
        # entries are blocks *within* SS assistant messages, not separate entries.
        # Comparing against len(conv.messages) directly causes n_conv > n_ss even
        # when SS has the full history, incorrectly triggering the merge branch.
        n_conv_meaningful = sum(1 for m in conv.messages if m.get("role") in ("user", "assistant"))

        if n_ss >= n_conv_meaningful:
            # SS has full history (normal flow with disk-init) - use it directly
            display_msgs = serialized_ss
        else:
            # SS is partial (e.g. restart continuation) - prepend older conv history
            conv_meaningful = [m for m in conv.messages if m.get("role") in ("user", "assistant")]
            display_msgs = conv_meaningful[:n_conv_meaningful - n_ss] + serialized_ss

        final_save_data["display_messages"] = display_msgs

    chat_manager.save_chat(chat_id_for_storage, final_save_data)

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

    # Trigger Chat Titler for ALL chats (not just Ren)
    # Skip system continuations, error-only exchanges, and empty responses
    if not is_system_continuation and all_segments and not had_error:
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
    state_key = chat_id_for_storage
    # Stop any running heartbeat for this session
    stop_tool_heartbeat(state_key)
    if state_key in session_streaming_states:
        del session_streaming_states[state_key]
        logger.info(f"STREAMING_STATE: Cleared for {state_key}")
    # Remove from active processing and track as recently completed
    if state_key in active_processing_sessions:
        active_processing_sessions.pop(state_key, None)
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
    if len(conv.messages) <= 500:
        done_payload["messages"] = conv.messages
    await broadcast_to_session(chat_id_for_storage, done_payload)

    # ========== RESTART: Spawn restart subprocess AFTER clean save ==========
    # The restart_server tool wrote config to .claude/pending_restart.json. Now that
    # all state is cleanly saved to disk (display_messages with block model,
    # conv.messages with interleaved segments/tool calls, WAL cleaned up),
    # we can safely kill the server.
    if restart_after_save:
        try:
            import subprocess as _restart_sp
            restart_config = {}
            if os.path.exists(_PENDING_RESTART_FILE):
                with open(_PENDING_RESTART_FILE, 'r') as f:
                    restart_config = json.load(f)
                os.remove(_PENDING_RESTART_FILE)
            restart_script = restart_config.get("restart_script", "")
            log_file = restart_config.get("log_file", "/tmp/restart.log")
            if restart_script:
                logger.info(f"RESTART: Spawning restart subprocess (script={restart_script})")
                _restart_sp.Popen(
                    f"sleep 1 && bash {restart_script} > {log_file} 2>&1",
                    shell=True,
                    start_new_session=True,
                    stdout=_restart_sp.DEVNULL,
                    stderr=_restart_sp.DEVNULL,
                )
            else:
                logger.error("RESTART: No restart_script in pending_restart.json — cannot spawn subprocess")
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
            logger.info(f"INTERRUPT: Successfully interrupted Claude session")
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
            active_processing_sessions.pop(session_id, None)
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
                "content": content,
                "injected": True,
                "timestamp": time.time()
            }
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
            "message": {
                "id": msg_id,
                "role": "user",
                "content": content,
                "injected": True,
                "timestamp": time.time()
            }
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
        assistant_msg = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": "",
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
- DO NOT write output files - respond directly
"""
                            augmented_prompt = f"{history_context}{prompt}{routing_instructions}"

                            result = await invoke_agent(
                                name=agent_name,
                                prompt=augmented_prompt,
                                mode="foreground",
                                source_chat_id=agent_room_id,
                                project=task_project
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
You are running as a scheduled task, not a live invocation. Your output will be reviewed asynchronously by Primary Claude.

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
                            project=task_project
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
- DO NOT write output files - respond directly
"""
                            augmented_prompt = f"{history_context}{prompt}{routing_instructions}"

                            result = await invoke_agent(
                                name=agent_name,
                                prompt=augmented_prompt,
                                mode="foreground",
                                source_chat_id=agent_room_id,
                                project=task_project
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
- DO NOT write output files - respond directly in your final output
"""
                        augmented_prompt = prompt + routing_instructions

                        result = await invoke_agent(
                            name=agent_name,
                            prompt=augmented_prompt,
                            mode="foreground",
                            source_chat_id=None,
                            project=task_project
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
        # Prompt tasks use ClaudeWrapper which shares a single SDK session,
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

                    augmented_prompt = _build_history_context(existing_messages[-20:], prompt)

                    claude = ClaudeWrapper(session_id="new", cwd=ROOT_DIR, chat_id=target_room_id, chat_messages=existing_messages)
                    logger.info(f"Starting fresh SDK session for room {target_room_id}")

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
                    play_sound=decision.play_sound
                )

                # Also send legacy scheduled_task_complete for backward compatibility
                for ws in list(client_sessions):
                    try:
                        await ws.send_json({
                            "type": "scheduled_task_complete",
                            "session_id": actual_session_id,
                            "title": title
                        })
                    except Exception:
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
        logger.error(f"Scheduled task execution error: {e}")


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
                asyncio.create_task(_execute_scheduled_task(task_info))

            # Periodic maintenance: clean up stale chat locks
            _cleanup_chat_locks()

        except Exception as e:
            logger.error(f"Scheduler Error: {e}")


async def restart_continuation_wakeup():
    """Background task that runs once after server startup to resume ALL sessions
    that were active when a restart was triggered.

    Waits for at least one WebSocket connection (so there's a client to send messages to),
    then triggers continuation for every session in the restart_continuation marker.
    """
    global restart_continuation

    if not restart_continuation:
        return

    continuation = restart_continuation
    restart_continuation = None  # Clear immediately so nothing else picks it up

    sessions = continuation.get("sessions", [])
    reason = continuation.get("reason", "Server restart")
    source = continuation.get("source", "unknown")
    continuation_prompt = continuation.get("continuation_prompt", "Restart completed. Please continue.")

    if not sessions:
        logger.warning("Restart continuation has no sessions to resume")
        return

    logger.info(
        f"Restart continuation: waiting for WebSocket connection to resume "
        f"{len(sessions)} session(s) (source={source}, reason={reason})"
    )

    # Wait for at least one WebSocket client to connect (max 30 seconds)
    for _ in range(60):
        if client_sessions:
            break
        await asyncio.sleep(0.5)
    else:
        logger.warning("Restart continuation: no WebSocket client connected within 30s, aborting")
        return

    # Brief additional delay to let the client fully initialize
    await asyncio.sleep(1.0)

    # Pick any connected WebSocket to use for sending messages
    # (all clients in this system share the same view)
    ws = next(iter(client_sessions.keys()), None)
    if not ws:
        logger.warning("Restart continuation: WebSocket client disconnected before we could resume")
        return

    # Send a restart_continuation notification to the client for EACH session
    for session_info in sessions:
        session_id = session_info.get("session_id")
        agent = session_info.get("agent", "ren")
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

            # Use handle_message with forceNewSession to start fresh Claude session
            # but preserve chat history context
            await handle_message(ws, {
                "sessionId": session_id,
                "message": continuation_message,
                "msgId": f"system-restart-{datetime.now().timestamp()}",
                "forceNewSession": True,
                "preserveChatId": session_id,
                "contextMessages": context_messages,
                "isSystemContinuation": True
            })

            # Brief delay between sessions to avoid overwhelming
            if len(sessions) > 1:
                await asyncio.sleep(1.0)

        except Exception as e:
            logger.error(f"Failed to auto-continue session {session_id} after restart: {e}")

    logger.info(f"Restart continuation complete: resumed {len(sessions)} session(s)")


async def agent_notification_wakeup_loop():
    """Background task to check for stale agent notifications and trigger wake-ups.

    When ping mode agents complete but the user hasn't sent a message within 30 seconds,
    this loop automatically wakes Claude up with the notifications as a hidden user message.

    Key design decisions for concurrency safety:
    - Uses claim_pending() to atomically transition notifications from "pending" to "injected"
      under a file lock, preventing double-delivery with the inline injection path (Path A).
    - Batches all notifications for the same chat_id into a single Claude call, so if 3 agents
      complete around the same time, Claude gets one combined prompt instead of 3 serial ones.
    - Holds chat_lock for the entire batch processing of a given chat, preventing user messages
      from interleaving with the wake-up save.
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

            # Atomically claim all stale notifications — this prevents Path A (inline
            # injection) and future loop iterations from grabbing the same ones.
            claimed = queue.claim_pending(threshold_seconds=30)

            if not claimed:
                continue

            logger.info(f"Claimed {len(claimed)} stale agent notifications for wake-up")

            # Group claimed notifications by target chat_id for batched delivery
            from collections import defaultdict
            by_chat: dict[str, list] = defaultdict(list)
            for notification in claimed:
                chat_id = notification.source_chat_id
                if not chat_id:
                    # No chat to wake up — already marked injected, just skip
                    logger.warning(f"Notification {notification.id} has no source_chat_id, skipping")
                    continue
                by_chat[chat_id].append(notification)

            # Process each chat's batch of notifications
            for chat_id, notifications in by_chat.items():
                try:
                    await _process_notification_batch(chat_id, notifications, queue)
                except Exception as e:
                    logger.error(f"Error processing notification batch for chat {chat_id}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Agent Notification Wake-up Error: {e}", exc_info=True)


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
        # Re-load chat inside the lock (may have changed since we checked)
        existing_chat = chat_manager.load_chat(chat_id)
        if not existing_chat:
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
        active_processing_sessions[chat_id] = time.time()

        # Start fresh session with conversation history injected
        notification_prompt = _build_history_context(conversation_history, notification_prompt_raw)
        logger.info(f"Wake-up: fresh SDK session with {len(conversation_history)} messages of history, {len(notifications)} notifications (chat_id: {chat_id}, agents: {agent_names_str})")
        claude = ClaudeWrapper(session_id="new", cwd=ROOT_DIR, chat_id=chat_id, chat_messages=conversation_history)

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
            active_processing_sessions.pop(chat_id, None)

        # Append error content to last segment if any
        if error_content:
            all_segments.append(f"\n\n**Errors:**\n" + "\n".join(error_content))

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
            logger.warning(f"Wake-up produced no content for {agent_names_str} -> chat {chat_id}")
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
            if decision.use_toast:
                await send_notification(
                    client_sessions=client_sessions,
                    chat_id=chat_id,
                    preview=preview,
                    critical=False,
                    play_sound=decision.play_sound
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
                    f"<h1>Authentication Failed</h1><p>Error: {error}</p>"
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
                    f"<h1>Authentication Failed</h1><p>{e}</p>"
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
def serve_file(file_path: str):
    target_path = os.path.join(ROOT_DIR, file_path)
    if not os.path.abspath(target_path).startswith(ROOT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target_path)


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
