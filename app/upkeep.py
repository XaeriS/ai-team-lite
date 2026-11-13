"""Keeping a project tidy over time (from the independent review, L41-L43): documents that follow the code,
review findings tracked until they are closed, and leftovers removed at version time."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import projects, safefile

# ---------------------------------------------------------------------------- L41: documents follow the code


def stale_docs(folder: Path, started: float | None) -> list[str]:
    """Problems with the project's documents at the end of an iteration that began at `started`."""
    problems = []
    status = folder / "STATUS.md"
    if not status.exists():
        problems.append("STATUS.md is missing: write where the project stands (what works, how to run it, what is "
                        "unfinished).")
    elif started and status.stat().st_mtime < started:
        problems.append("STATUS.md was not updated in this iteration: bring it in line with what was just built "
                        "(what works now, what is unfinished).")
    if (folder / "run.bat").exists() and not (folder / "README.md").exists():
        problems.append("README.md is missing: the app can be started (run.bat), so write a short README in plain words "
                        "(what it does, how to start it, how to use it).")
    return problems


# ---------------------------------------------------------------------------- L42: findings tracked to closure
STATUSES = ("open", "fixed", "accepted")
SEVERITIES = ("high", "medium", "low")


def _findings_file(slug: str) -> Path:
    return projects.project_dir(slug) / ".aiteam" / "findings.json"


def findings(slug: str) -> list[dict]:
    data = safefile.read_json(_findings_file(slug), [])
    return data if isinstance(data, list) else []


def _key(title: str) -> str:
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in title).split())


def record_finding(slug: str, title: str, severity: str = "medium", where: str = "") -> tuple[dict, bool]:
    """Add a review finding, or return the one already known under the same title. Returns (finding, is_new)."""
    title = " ".join(str(title).split())[:200]
    if not title:
        raise ValueError("A finding needs a short title.")
    severity = severity.lower() if severity.lower() in SEVERITIES else "medium"
    with safefile.lock_for(_findings_file(slug)):
        items = findings(slug)
        known = next((f for f in items if _key(f["title"]) == _key(title)), None)
        if known:
            if known["status"] == "fixed":   # it came back: open again
                known.update(status="open", reason="", closed=None, severity=severity, where=where or known["where"])
                _save(slug, items)
            return known, False
        item = {"id": f"F-{len(items) + 1}", "title": title, "severity": severity, "where": str(where)[:300],
                "status": "open", "reason": "", "found": time.time(), "closed": None}
        items.append(item)
        _save(slug, items)
        return item, True


def close_finding(slug: str, finding_id: str, status: str, reason: str = "") -> dict:
    status = status.lower()
    if status not in ("fixed", "accepted"):
        raise ValueError('status must be "fixed" or "accepted".')
    if status == "accepted" and not reason.strip():
        raise ValueError("An accepted finding needs the reason it is accepted (the user's decision, in plain words).")
    with safefile.lock_for(_findings_file(slug)):
        items = findings(slug)
        item = next((f for f in items if f["id"].lower() == str(finding_id).strip().lower()), None)
        if not item:
            raise ValueError(f"There is no finding {finding_id}.")
        item.update(status=status, reason=reason.strip()[:300], closed=time.time())
        _save(slug, items)
        return item


def _save(slug: str, items: list[dict]) -> None:
    safefile.write_json(_findings_file(slug), items)
    lines = ["# Review findings", "", "> Kept by AI Team Lite: every finding of a review stays here until it is fixed, or "
             "accepted with a reason.", "", "| # | Finding | Severity | Where | Status |", "|---|---------|----------|-------|--------|"]
    for f in items:
        state = f["status"] + (f" ({f['reason']})" if f.get("reason") else "")
        lines.append(f"| {f['id']} | {f['title'].replace('|', '/')} | {f['severity']} | "
                     f"{(f.get('where') or '').replace('|', '/')} | {state} |")
    try:
        out = projects.project_dir(slug) / "reports" / "FINDINGS.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def findings_brief(slug: str) -> str:
    """What the reviewer must know before a new review."""
    items = findings(slug)
    if not items:
        return "No findings recorded yet."
    part = lambda st: "; ".join(f"{f['id']} {f['title']} [{f['severity']}]" + (f" (reason: {f['reason']})" if f.get("reason") else "")
                                for f in items if f["status"] == st) or "none"
    return f"Open: {part('open')}. Accepted by the user (do not report again): {part('accepted')}. Fixed: {part('fixed')}."


# ---------------------------------------------------------------------------- L43: leftovers
ACTIVE_ENVS = {".venv", "venv", "env"}
CACHE_DIRS = {"graphify-out": "an old analysis cache", ".pytest_cache": "a test cache", ".mypy_cache": "a checker cache",
              ".ruff_cache": "a checker cache"}
BACKUP_ENDINGS = (".bak", ".old", ".orig", ".tmp")
SKIP = {".git", ".aiteam", "node_modules"} | ACTIVE_ENVS


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def leftovers(folder: Path) -> list[dict]:
    """Things that are safe candidates to remove, each with a plain reason. Looks at the top two levels only."""
    found = []

    def look(directory: Path, depth: int) -> None:
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for e in entries:
            if e.name in SKIP:
                continue
            why = None
            if e.is_dir():
                if (e / "pyvenv.cfg").exists():
                    why = "an old Python environment (the project uses .venv)"
                elif e.name in CACHE_DIRS:
                    why = CACHE_DIRS[e.name]
                elif depth < 1 and not e.name.startswith("."):
                    look(e, depth + 1)
            elif e.name.lower().endswith(BACKUP_ENDINGS) or "backup" in e.stem.lower():
                why = "a backup or temporary copy"
            if why:
                found.append({"path": e.relative_to(folder).as_posix(), "why": why, "bytes": _size(e)})

    look(folder, 0)
    return found


def remove_leftovers(slug: str, paths: list[str]) -> list[str]:
    """Delete the given leftovers (only those leftovers() lists, only inside the project). Returns what was removed."""
    folder = projects.project_dir(slug)
    allowed = {x["path"] for x in leftovers(folder)}
    removed = []
    for rel in paths:
        target = folder / rel
        if rel not in allowed or not projects.inside(slug, target):
            continue
        try:
            shutil.rmtree(target) if target.is_dir() else target.unlink()
            removed.append(rel)
        except OSError:
            pass
    return removed
