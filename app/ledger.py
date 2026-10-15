"""Append-only JSON-lines logs shared by the token log (usage) and the time log (timeline).

Each log exists twice: once in the app's data folder (all projects) and once inside the project's own folder
(.aiteam/<name>), so a project keeps its history when it is moved to another folder or another PC.
"""
from __future__ import annotations

import json
from pathlib import Path


def project_file(project: str, name: str, create: bool = False) -> Path | None:
    """<project folder>/.aiteam/<name>, or None for the setup chat and unknown projects."""
    if not project or project in ("setup", "?"):
        return None
    try:
        from . import projects
        if not projects.load(project):
            return None
        d = projects.project_dir(project) / projects.META
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d / name
    except Exception:  # noqa: BLE001
        return None


def read(path: Path) -> list[dict]:
    """Every row of a JSON-lines file; unreadable lines are skipped, a missing file gives []."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def append(row: dict, *paths: Path | None) -> None:
    """Add one row to each file given (a folder that is read-only or offline is skipped)."""
    line = json.dumps(row) + "\n"
    for path in paths:
        if path is None:
            continue
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
