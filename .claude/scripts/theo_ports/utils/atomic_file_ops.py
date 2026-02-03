"""
Atomic File Operations (Windows Compatible Port)
"""
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional
import os
from .theo_logger import cli_logger

logger = cli_logger

class AtomicFileOperations:
    @staticmethod
    def load_json_safe(file_path: Path, default: Optional[Dict] = None) -> Dict[str, Any]:
        if default is None:
            default = {}
        try:
            if not file_path.exists():
                return default.copy()
            
            # Simple read on Windows 
            for _ in range(3):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except PermissionError:
                    time.sleep(0.1)
                    continue
                except Exception:
                    break
            return default.copy()
                
        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}")
            return default.copy()

    @staticmethod
    def save_json_safe(file_path: Path, data: Dict[str, Any], create_parents: bool = True) -> bool:
        try:
            if create_parents:
                file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic write via temp file + replace
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=file_path.parent, delete=False) as tf:
                json.dump(data, tf, indent=2, ensure_ascii=False, default=str)
                temp_path = Path(tf.name)
            
            # Retry loop for Windows file locking
            for _ in range(3):
                try:
                    os.replace(temp_path, file_path)
                    return True
                except PermissionError:
                    time.sleep(0.1)
                except OSError:
                    try:
                        if file_path.exists():
                            file_path.unlink()
                        os.rename(temp_path, file_path)
                        return True
                    except Exception:
                        time.sleep(0.1)

            logger.error(f"Failed to save {file_path} after retries")
            if temp_path.exists():
                os.unlink(temp_path)
            return False

        except Exception as e:
            logger.error(f"Save error {file_path}: {e}")
            return False

def load_json(file_path: Path, default: Optional[Dict] = None):
    return AtomicFileOperations.load_json_safe(file_path, default)

def save_json(file_path: Path, data: Dict[str, Any]):
    return AtomicFileOperations.save_json_safe(file_path, data)
