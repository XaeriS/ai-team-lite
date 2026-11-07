"""Saving small JSON files safely from several threads (L34).

A file is written to a temporary copy with a unique name, then swapped in, so a reader never sees half a file. On
Windows the swap is refused ("Access is denied") while another thread or program (antivirus, the indexer) has the
file open for a moment, so it is retried for a short while; writers of the same file take turns.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

RETRY_SECONDS = 2.0
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def lock_for(path: Path) -> threading.RLock:
    """One lock per file, shared by every thread of the app."""
    key = os.path.normcase(os.path.abspath(path))
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def write_json(path: Path, data, indent: int = 2) -> None:
    """Write data as JSON to path, atomically. Raises OSError only if Windows still refuses after RETRY_SECONDS."""
    path = Path(path)
    text = json.dumps(data, indent=indent)
    with lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(text, encoding="utf-8")
        deadline = time.monotonic() + RETRY_SECONDS
        delay = 0.02
        try:
            while True:
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(delay)
                    delay = min(delay * 2, 0.25)
        finally:
            tmp.unlink(missing_ok=True)


def read_json(path: Path, default=None):
    """Read a JSON file while no writer is swapping it (on Windows an open reader blocks the swap). Returns default
    when the file is missing or unreadable."""
    path = Path(path)
    with lock_for(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
