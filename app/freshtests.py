"""L64: after an iteration passes its full test run and is saved, the test results show that run, not the history.

The owner (2026-11-17): "make sure the previous test are deleted and replaced by the new ones, when the new
iteration is tested and validated". Cyber Assistant kept 235 results for 181 tests, 52 of them old failures fixed
long ago, plus 20 logs and 40 screenshots. Earlier versions stay recoverable through the project's checkpoints.
"""
from __future__ import annotations

import json
from pathlib import Path

from .testrun import LOG_DIR
from .tools_common import load_tests, tests_file

SHOTS = Path("reports") / "screenshots"


def refresh(folder: Path, n: int, started: float | None) -> dict:
    """Keep one result per test (the latest), drop failures the passing run has made stale and loose screenshots
    from before this iteration, keep only the newest test log. Returns what was removed (for Fred and the tests)."""
    started = started or 0.0
    results = load_tests(folder)
    latest: dict[str, dict] = {}
    for r in results:   # oldest first: a later result for the same test replaces the earlier one
        name = r.get("name") or ""
        if not name:
            continue
        latest.pop(name, None)
        latest[name] = r
    kept, dropped = [], []
    for name, r in latest.items():
        status = r.get("status")
        if status == "failed":                                   # the full run just passed: this one is out of date
            dropped.append(name)
            continue
        if status == "screenshot" and (r.get("time") or 0) < started:   # a loose picture from an earlier iteration
            dropped.append(name)
            continue
        r = {**r, "confirmed_in": n}
        if (r.get("time") or 0) >= started and "iteration" not in r:
            r["iteration"] = n
        kept.append(r)
    removed_results = len(results) - len(kept)
    if removed_results or kept != results:
        f = tests_file(folder)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(kept, indent=1), encoding="utf-8")

    used = {str(r.get("screenshot") or "") for r in kept}
    shots = 0
    d = folder / SHOTS
    for png in sorted(d.glob("*.png")) if d.is_dir() else []:
        rel = png.relative_to(folder).as_posix()
        try:
            old = png.stat().st_mtime < started
        except OSError:
            continue
        if old and rel not in used:
            png.unlink(missing_ok=True)
            shots += 1

    logs = 0
    d = folder / LOG_DIR
    all_logs = sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
    for old in all_logs[:-1]:   # the newest log is the run that just validated the iteration
        old.unlink(missing_ok=True)
        logs += 1
    return {"results": removed_results, "screenshots": shots, "logs": logs, "kept": len(kept)}
