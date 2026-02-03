"""
Forms Store - Storage abstraction for form definitions and submissions.

Provides CRUD operations for forms:
- define_form: Register or update a form definition
- get_form: Retrieve a form by ID
- get_form_registry: Get all form definitions
- append_submission: Save a form submission
- list_submissions: Query past submissions
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .atomic_file_ops import load_json, save_json
from .theo_logger import cli_logger
from .vault_paths import get_vault_root

logger = cli_logger

# Storage paths relative to vault
FORMS_DIR = "forms"
REGISTRY_FILE = "registry.json"
SUBMISSIONS_FILE = "submissions.jsonl"


def _get_forms_dir() -> Path:
    """Get the forms storage directory, creating if needed."""
    vault = get_vault_root()
    forms_dir = vault / FORMS_DIR
    forms_dir.mkdir(parents=True, exist_ok=True)
    return forms_dir


def _get_registry_path() -> Path:
    """Get path to form definitions registry."""
    return _get_forms_dir() / REGISTRY_FILE


def _get_submissions_path() -> Path:
    """Get path to submissions JSONL file."""
    return _get_forms_dir() / SUBMISSIONS_FILE


def define_form(form: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Register or update a form definition.

    Args:
        form: Dict with keys:
            - form_id (required): Unique identifier
            - title (required): Display title
            - fields (required): List of field definitions
            - version (optional): Version number, defaults to 1
            - description (optional): Form description

    Returns:
        Tuple of (success: bool, message: str)
    """
    form_id = form.get("form_id")
    if not form_id:
        return False, "form_id is required"

    title = form.get("title")
    if not title:
        return False, "title is required"

    fields = form.get("fields")
    if not fields or not isinstance(fields, list):
        return False, "fields is required and must be a list"

    # Validate each field has required properties
    for i, field in enumerate(fields):
        if not isinstance(field, dict):
            return False, f"Field {i} must be a dict"
        if not field.get("id"):
            return False, f"Field {i} missing 'id'"
        if not field.get("type"):
            return False, f"Field {i} missing 'type'"
        if not field.get("label"):
            return False, f"Field {i} missing 'label'"

    try:
        registry_path = _get_registry_path()
        registry = load_json(registry_path, {})

        # Build form definition
        form_def = {
            "form_id": form_id,
            "title": title,
            "fields": fields,
            "version": form.get("version", 1),
            "description": form.get("description", ""),
            "created_at": registry.get(form_id, {}).get("created_at", time.time()),
            "updated_at": time.time(),
        }

        registry[form_id] = form_def

        if save_json(registry_path, registry):
            logger.info(f"forms_store: Defined form '{form_id}' v{form_def['version']}")
            return True, f"Form '{form_id}' registered"
        else:
            return False, "Failed to save registry"

    except Exception as e:
        logger.error(f"forms_store: define_form error: {e}")
        return False, str(e)


def get_form(form_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a form definition by ID.

    Args:
        form_id: The form identifier

    Returns:
        Form definition dict or None if not found
    """
    try:
        registry_path = _get_registry_path()
        registry = load_json(registry_path, {})
        return registry.get(form_id)
    except Exception as e:
        logger.error(f"forms_store: get_form error: {e}")
        return None


def get_form_registry() -> Dict[str, Dict[str, Any]]:
    """
    Get all form definitions.

    Returns:
        Dict mapping form_id to form definition
    """
    try:
        registry_path = _get_registry_path()
        return load_json(registry_path, {})
    except Exception as e:
        logger.error(f"forms_store: get_form_registry error: {e}")
        return {}


def append_submission(record: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Append a form submission to the submissions log.

    Args:
        record: Submission record with keys:
            - form_id (required): The form that was submitted
            - answers (required): Dict of field_id -> value
            - ts (optional): Timestamp, defaults to now
            - version (optional): Form version
            - room_id (optional): Session/room identifier

    Returns:
        Tuple of (success: bool, message: str)
    """
    form_id = record.get("form_id")
    if not form_id:
        return False, "form_id is required"

    answers = record.get("answers")
    if answers is None:
        return False, "answers is required"

    try:
        submissions_path = _get_submissions_path()

        # Build submission record
        submission = {
            "ts": record.get("ts", time.time()),
            "form_id": form_id,
            "version": record.get("version", 1),
            "answers": answers,
            "room_id": record.get("room_id"),
        }

        # Append to JSONL file
        with open(submissions_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(submission, ensure_ascii=False, default=str) + "\n")

        logger.info(f"forms_store: Saved submission for '{form_id}'")
        return True, "Submission saved"

    except Exception as e:
        logger.error(f"forms_store: append_submission error: {e}")
        return False, str(e)


def list_submissions(
    form_id: Optional[str] = None,
    limit: int = 20,
    after_ts: Optional[float] = None,
    room_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    List form submissions with optional filtering.

    Args:
        form_id: Filter by specific form (optional)
        limit: Maximum number of results (default 20)
        after_ts: Only include submissions after this timestamp (optional)
        room_id: Filter by room/session ID (optional)

    Returns:
        List of submission records, newest first
    """
    try:
        submissions_path = _get_submissions_path()

        if not submissions_path.exists():
            return []

        submissions = []
        with open(submissions_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)

                    # Apply filters
                    if form_id and record.get("form_id") != form_id:
                        continue
                    if after_ts and record.get("ts", 0) <= after_ts:
                        continue
                    if room_id and record.get("room_id") != room_id:
                        continue

                    submissions.append(record)
                except json.JSONDecodeError:
                    continue

        # Sort by timestamp descending (newest first)
        submissions.sort(key=lambda x: x.get("ts", 0), reverse=True)

        # Apply limit
        return submissions[:limit]

    except Exception as e:
        logger.error(f"forms_store: list_submissions error: {e}")
        return []


def delete_form(form_id: str) -> Tuple[bool, str]:
    """
    Delete a form definition.

    Note: This does not delete past submissions.

    Args:
        form_id: The form to delete

    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        registry_path = _get_registry_path()
        registry = load_json(registry_path, {})

        if form_id not in registry:
            return False, f"Form '{form_id}' not found"

        del registry[form_id]

        if save_json(registry_path, registry):
            logger.info(f"forms_store: Deleted form '{form_id}'")
            return True, f"Form '{form_id}' deleted"
        else:
            return False, "Failed to save registry"

    except Exception as e:
        logger.error(f"forms_store: delete_form error: {e}")
        return False, str(e)


__all__ = [
    "define_form",
    "get_form",
    "get_form_registry",
    "append_submission",
    "list_submissions",
    "delete_form",
]
