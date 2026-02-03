"""
Vault Path Utilities (Ported)
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

def get_vault_root(config: Optional[dict] = None) -> Path:
    override = _resolve_env_override()
    if override is not None:
        return override

    # Default to .agent/vault for Antigravity context??
    # No, keep Theo's "vault" relative behavior (cwd/vault).
    preferred = Path("vault")
    try:
        preferred.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return preferred

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
