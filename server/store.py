"""Atomic, thread-safe JSON storage.

Writes go to a temp file in the same directory and are renamed over the target,
so a crash (or Android killing the process) can never leave a truncated
config.json behind.
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def read_json(path: Path, default: Any = None) -> Any:
    with _lock_for(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return default
        except (json.JSONDecodeError, OSError):
            # Corrupt file: keep a copy for the user instead of silently losing it.
            try:
                backup = path.with_suffix(path.suffix + ".corrupt")
                os.replace(path, backup)
            except OSError:
                pass
            return default


def write_json(path: Path, data: Any, *, secure: bool = False) -> None:
    from .paths import secure_file

    with _lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if secure:
            secure_file(path)
