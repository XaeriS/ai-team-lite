"""Lessons learned across projects (L23).

When a problem took real effort to solve (an environment quirk, a library gotcha, a fix that needed several tries),
Fred records one short line. The most recent lessons go into the instructions of new work, so the team does not repeat
the same mistake on every project. Kept in the app's data folder; the user can see and delete them in Settings.
Nothing personal or secret is kept: secret-shaped text is refused and the user's home folder is replaced by "~".
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from . import paths

MAX_LESSONS = 100
IN_BRIEF = 12
_lock = threading.Lock()


def _file() -> Path:
    return paths.data_dir() / "lessons.json"


def load() -> list[dict]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    _file().write_text(json.dumps(items, indent=1), encoding="utf-8")


def _clean(text: str) -> str:
    t = " ".join(str(text or "").split())[:240]
    home = str(Path.home())
    if home and len(home) > 3:
        t = t.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return t


def add(text: str, project: str = "") -> dict:
    from .updates import SECRET_RE
    t = _clean(text)
    if len(t) < 8:
        raise ValueError("A lesson needs one clear sentence.")
    if SECRET_RE.search(t):
        raise ValueError("That looks like it contains a secret: lessons must never hold keys or passwords.")
    norm = re.sub(r"\W+", " ", t.lower()).strip()
    with _lock:
        items = load()
        for it in items:
            if re.sub(r"\W+", " ", it["text"].lower()).strip() == norm:
                it["time"] = time.time()   # already known: just mark it as still useful
                _save(items)
                return it
        item = {"id": uuid.uuid4().hex[:8], "text": t, "project": project, "time": time.time()}
        items = (items + [item])[-MAX_LESSONS:]
        _save(items)
    return item


def delete(lesson_id: str) -> bool:
    with _lock:
        items = load()
        kept = [i for i in items if i["id"] != lesson_id]
        _save(kept)
    return len(kept) != len(items)


def note() -> str:
    """The recent lessons as a short block for the team's instructions ('' when there are none)."""
    items = sorted(load(), key=lambda i: i.get("time", 0))[-IN_BRIEF:]
    if not items:
        return ""
    return ("\n## Lessons from earlier projects on this PC (apply them; they were learned the hard way)\n"
            + "\n".join(f"- {i['text']}" for i in items) + "\n")
