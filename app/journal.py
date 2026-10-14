"""Run journal and recovery (item 33).

While the app runs it keeps a small record of what each project is doing. A clean shutdown is marked; if the mark is
missing at the next start (closed during work, crash, Windows update, power cut), the projects that were busy are
listed as "interrupted" so the user can resume them, restart the step, or leave them paused.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import iterations, paths, projects

_lock = threading.Lock()


def _file() -> Path:
    return paths.data_dir() / "run-journal.json"


def _interrupted_file() -> Path:
    return paths.data_dir() / "interrupted.json"


def _read(f: Path) -> dict:
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(f: Path, data: dict) -> None:
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(f)


def start() -> list[dict]:
    """Call once when the app starts. Returns the projects interrupted by an unclean end of the previous run."""
    with _lock:
        prev = _read(_file())
        found = []
        if prev and not prev.get("clean", True):
            for slug, p in (prev.get("projects") or {}).items():
                if p.get("active") and projects.load(slug):
                    found.append({"slug": slug, **p, "app_started": prev.get("started")})
        if found:
            old = {x["slug"]: x for x in _read(_interrupted_file()).get("items", [])}
            old.update({x["slug"]: x for x in found})
            _write(_interrupted_file(), {"items": list(old.values())})
        _write(_file(), {"clean": False, "started": time.time(), "pid": os.getpid(), "projects": {}})
        return found


def record(slug: str, active: bool, **info) -> None:
    if slug == projects.SETUP_SLUG:
        return
    with _lock:
        data = _read(_file()) or {"clean": False, "started": time.time(), "projects": {}}
        cur = iterations.current(slug) or {}
        p = data.setdefault("projects", {}).setdefault(slug, {})
        p.update({"active": active, "time": time.time(), "name": (projects.load(slug) or {}).get("name", slug),
                  "iteration": cur.get("n"), "iteration_name": cur.get("name", ""),
                  "task": next((t["title"] for t in cur.get("tasks") or [] if not t.get("done")), ""), **info})
        _write(_file(), data)


def clean_exit() -> None:
    with _lock:
        data = _read(_file())
        data["clean"] = True
        data["ended"] = time.time()
        _write(_file(), data)


def interrupted() -> list[dict]:
    items = [x for x in _read(_interrupted_file()).get("items", []) if projects.load(x["slug"])]
    for x in items:
        x["changes"] = git_changes(projects.project_dir(x["slug"]))
    return items


def resolve(slug: str) -> None:
    with _lock:
        items = [x for x in _read(_interrupted_file()).get("items", []) if x["slug"] != slug]
        _write(_interrupted_file(), {"items": items})


def git_changes(folder: Path) -> list[str]:
    """Files changed since the last commit (kept out: AI Team Lite's own folder)."""
    if not (folder / ".git").exists() or not paths.git_exe():
        return []
    try:
        r = iterations._git(folder, "status", "--porcelain")
        return [line[3:] for line in r.stdout.splitlines() if line.strip() and ".aiteam/" not in line]
    except Exception:  # noqa: BLE001
        return []


def safety_stash(folder: Path) -> str:
    """Put uncommitted changes aside (recoverable with `git stash list` / `git stash pop`). Returns the stash name."""
    name = time.strftime("AI Team Lite safety stash %Y-%m-%d %H:%M")
    r = iterations._git(folder, "stash", "push", "-u", "-m", name)
    if r.returncode != 0 and "No local changes" not in (r.stdout + r.stderr):
        raise RuntimeError(f"Could not put the changes aside: {(r.stderr or r.stdout).strip()[-300:]}")
    return name
