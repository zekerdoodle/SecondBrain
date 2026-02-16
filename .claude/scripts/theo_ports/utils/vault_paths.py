"""
Vault Path Utilities (Ported)

For Second Brain, the canonical vault location is interface/server/vault.
This module provides path utilities that work regardless of the current working directory.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional, Union
import os
import shutil

from .theo_logger import cli_logger

logger = cli_logger

LEGACY_VAULT_PATH = Path("layer3_longterm") / "vault"
BACKUPS_SUBDIR = "backups"
CLONES_SUBDIR = "clones"
UPLOADS_SUBDIR = "uploads"
PathInput = Union[str, Path]


def _get_project_root() -> Path:
    """
    Determine the Second Brain project root from this module's location.

    This file is at: .claude/scripts/theo_ports/utils/vault_paths.py
    Project root is 5 levels up.
    """
    return Path(__file__).resolve().parent.parent.parent.parent.parent


def get_vault_root(config: Optional[dict] = None) -> Path:
    """
    Get the canonical vault root directory.

    Priority:
    1. THEO_VAULT_ROOT environment variable (for testing/overrides)
    2. Absolute path: {project_root}/interface/server/vault

    This ensures forms, submissions, and other vault data always go to the
    same location regardless of the process's working directory.
    """
    override = _resolve_env_override()
    if override is not None:
        return override

    # Use absolute path to canonical vault location
    # This is where all forms data actually lives
    project_root = _get_project_root()
    vault_path = project_root / "interface" / "server" / "vault"

    try:
        vault_path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    return vault_path

def get_backups_root(config: Optional[dict] = None) -> Path:
    vault_root = get_vault_root(config)
    vault_root = vault_root if vault_root.is_absolute() else (Path.cwd() / vault_root)
    backups_root = (vault_root / BACKUPS_SUBDIR).resolve()
    try:
        backups_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return backups_root

def get_clone_root(config: Optional[dict] = None) -> Path:
    vault_root = get_vault_root(config)
    clones_root = (vault_root / CLONES_SUBDIR).resolve()
    try:
        clones_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return clones_root

def get_uploads_root(room_id: Optional[str] = None, config: Optional[dict] = None) -> Path:
    vault_root = get_vault_root(config)
    vault_root = vault_root if vault_root.is_absolute() else (Path.cwd() / vault_root)

    uploads_root = (vault_root / UPLOADS_SUBDIR).resolve()
    try:
        uploads_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    if room_id is None:
        return uploads_root

    room_dir = (uploads_root / str(room_id)).resolve()
    try:
        room_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return room_dir

def _resolve_env_override() -> Optional[Path]:
    try:
        override = os.environ.get("THEO_VAULT_ROOT")
        if isinstance(override, str) and override.strip():
            root = Path(override.strip())
            root.mkdir(parents=True, exist_ok=True)
            return root
    except Exception:
        pass
    return None
