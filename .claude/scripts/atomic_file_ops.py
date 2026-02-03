"""
Atomic File Operations with Locking

Provides thread-safe and process-safe file operations with atomic writes and proper locking
to prevent corruption from concurrent access.

Ported from Theo (2026-01-24) with full fcntl locking support for Linux.
"""

import json
import fcntl
import time
import tempfile
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class AtomicFileOperations:
    """Thread-safe and process-safe file operations."""

    @staticmethod
    @contextmanager
    def file_lock(file_path: Path, mode: str = 'r', timeout: float = 10.0):
        """
        Context manager for file locking with timeout.

        Args:
            file_path: Path to file to lock
            mode: File open mode
            timeout: Max seconds to wait for lock
        """
        lock_file = file_path.with_suffix(f'{file_path.suffix}.lock')
        start_time = time.time()
        lock_fd = None

        while True:
            try:
                # Create lock file
                lock_fd = open(lock_file, 'w')
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

                # Open actual file
                file_fd = open(file_path, mode, encoding='utf-8')

                try:
                    yield file_fd
                finally:
                    file_fd.close()
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                    try:
                        lock_file.unlink()
                    except FileNotFoundError:
                        pass
                break

            except (IOError, OSError) as e:
                if lock_fd:
                    try:
                        lock_fd.close()
                    except:
                        pass

                # Check timeout
                if time.time() - start_time > timeout:
                    logger.error(f"Timeout waiting for file lock: {file_path}")
                    raise TimeoutError(f"Could not acquire lock for {file_path} within {timeout}s")

                # Wait and retry
                time.sleep(0.1)

    @staticmethod
    def load_json_safe(file_path: Path, default: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Safely load JSON file with locking and error handling.

        Args:
            file_path: Path to JSON file
            default: Default value if file doesn't exist or is corrupted

        Returns:
            Loaded JSON data or default value
        """
        if default is None:
            default = {}

        try:
            # Ensure file exists
            if not file_path.exists():
                logger.debug(f"File doesn't exist, returning default: {file_path}")
                return default.copy()

            # Load with file locking
            with AtomicFileOperations.file_lock(file_path, 'r') as f:
                data = json.load(f)
                logger.debug(f"Successfully loaded JSON: {file_path}")
                return data

        except json.JSONDecodeError as e:
            logger.warning(f"JSON decode error, returning default: {file_path} - {e}")
            return default.copy()
        except TimeoutError as e:
            logger.error(f"Lock timeout: {e}")
            return default.copy()
        except Exception as e:
            logger.error(f"Unexpected error loading {file_path}: {e}")
            return default.copy()

    @staticmethod
    def save_json_safe(file_path: Path, data: Dict[str, Any], create_parents: bool = True) -> bool:
        """
        Safely save JSON file with atomic writes and locking.

        Args:
            file_path: Path to JSON file
            data: Data to save
            create_parents: Whether to create parent directories

        Returns:
            True if successful, False otherwise
        """
        temp_file = None
        try:
            # Create parent directories if needed
            if create_parents:
                file_path.parent.mkdir(parents=True, exist_ok=True)

            # Create temporary file in same directory for atomic operation
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=file_path.parent,
                prefix=f'.{file_path.name}',
                suffix='.tmp',
                delete=False
            ) as f:
                temp_file = Path(f.name)
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            # Atomic move with file locking
            lock_file = file_path.with_suffix(f'{file_path.suffix}.lock')
            try:
                with open(lock_file, 'w') as lock_fd:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

                    # Atomic rename
                    temp_file.replace(file_path)
                    logger.debug(f"Successfully saved JSON atomically: {file_path}")

                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            finally:
                try:
                    lock_file.unlink()
                except FileNotFoundError:
                    pass

            return True

        except Exception as e:
            logger.error(f"Failed to save {file_path}: {e}")

            # Clean up temp file if it exists
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except:
                    pass

            return False


# Convenience functions for common operations
def load_json(file_path: Path, default: Optional[Dict] = None) -> Dict[str, Any]:
    """Load JSON file safely with locking."""
    return AtomicFileOperations.load_json_safe(file_path, default)


def save_json(file_path: Path, data: Dict[str, Any]) -> bool:
    """Save JSON file safely with atomic write and locking."""
    return AtomicFileOperations.save_json_safe(file_path, data)
