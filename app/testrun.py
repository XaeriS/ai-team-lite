"""Running a project's tests for the team (L21, L22).

- The model gets a compact result: totals plus only the failing tests and their error lines. The full log is saved in
  reports/test-logs/ for the user and IT. Long passing logs no longer fill the AI's context.
- While building, only the tests related to the changed files run (pytest); the full suite still runs before every
  commit (commit_iteration), so nothing is saved without all tests passing.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from . import paths

LOG_DIR = Path("reports") / "test-logs"
KEEP_LOGS = 20
SKIP_DIRS = {".git", ".aiteam", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist", ".pytest_cache"}
NO_WINDOW = 0x08000000 if paths.IS_WINDOWS else 0


def run(command: str, cwd: Path, timeout: int = 300) -> tuple[bool, str]:
    """Run a test command; returns (passed, full output)."""
    env = {**os.environ, "PATH": paths.tool_path(), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run(command, cwd=str(cwd), shell=True, capture_output=True, text=True, timeout=timeout,
                           env=env, creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return False, f"Tests timed out after {timeout} s."


def save_log(folder: Path, label: str, command: str, output: str) -> str:
    """Keep the full output for people (the AI only gets the summary). Returns the path relative to the project."""
    d = folder / LOG_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:30] + ".log"
        (d / name).write_text(f"$ {command}\n\n{output}", encoding="utf-8")
        for old in sorted(d.glob("*.log"))[:-KEEP_LOGS]:
            old.unlink(missing_ok=True)
        return (LOG_DIR / name).as_posix()
    except OSError:
        return ""


_TOTALS = re.compile(r"(\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|warnings?|deselected)(?:, )?)+ in [\d.]+s")
_FAILED = re.compile(r"^(FAILED|ERROR) \S+.*$", re.M)
_ELINE = re.compile(r"^E +.+$", re.M)
_WHERE = re.compile(r"^[\w./\\-]+\.py:\d+: \w+.*$", re.M)


def summarize(output: str, passed: bool, log: str = "", limit: int = 2500) -> str:
    """Totals plus only what failed, in a few lines."""
    lines = (output or "").strip().splitlines()
    total_line = next((ln.strip(" =") for ln in reversed(lines) if _TOTALS.search(ln)), "")
    parts = [("Tests passed." if passed else "Tests FAILED.") + (f" {total_line}" if total_line else "")]
    if not passed:
        failed = [m.group(0) for m in _FAILED.finditer(output or "")]
        if failed:
            more = f"\n… and {len(failed) - 15} more" if len(failed) > 15 else ""
            parts.append("Failing:\n" + "\n".join(failed[:15]) + more)
            where = [m.group(0) for m in _WHERE.finditer(output)][:10]
            errors = [m.group(0) for m in _ELINE.finditer(output)][:25]
            if where:
                parts.append("Where:\n" + "\n".join(where))
            if errors:
                parts.append("Errors:\n" + "\n".join(errors))
        else:  # not pytest, or it stopped before running tests: the end of the output says what happened
            parts.append("\n".join(lines[-30:]))
    elif not total_line:
        parts.append("\n".join(lines[-3:]))
    if log:
        parts.append(f"Full log: {log}")
    text = "\n\n".join(x for x in parts if x)
    return text if len(text) <= limit else text[: limit - 60] + f"\n… (cut here; full log: {log or 'not saved'})"


# ---------------------------------------------------------------------------- L22: only the related tests
def _changed_files(folder: Path, since: float | None) -> list[str]:
    exe = paths.git_exe()
    if exe and (folder / ".git").exists():
        try:
            r = subprocess.run([exe, "status", "--porcelain", "--untracked-files=all"], cwd=str(folder),
                               capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
            files = [line[3:].strip().strip('"').split(" -> ")[-1] for line in r.stdout.splitlines() if len(line) > 3]
            return [f for f in files if f.endswith(".py")]
        except (OSError, subprocess.SubprocessError):
            pass
    out = []
    for f in folder.rglob("*.py"):
        if SKIP_DIRS & set(f.relative_to(folder).parts):
            continue
        try:
            if since is None or f.stat().st_mtime >= since:
                out.append(f.relative_to(folder).as_posix())
        except OSError:
            pass
    return out


def _test_files(folder: Path) -> list[Path]:
    out = []
    for f in folder.rglob("*.py"):
        rel = f.relative_to(folder)
        if SKIP_DIRS & set(rel.parts):
            continue
        if f.name.startswith("test_") or f.name.endswith("_test.py"):
            out.append(f)
    return sorted(out)


def affected(folder: Path, command: str, since: float | None = None) -> tuple[str, str]:
    """(command to run, plain note). Adds only the test files related to what changed; falls back to all tests."""
    if "pytest" not in command:
        return command, "all tests (only pytest projects can pick the related tests)"
    changed = _changed_files(folder, since)
    if not changed:
        return command, "all tests (no changed files found)"
    tests = _test_files(folder)
    pick: list[str] = []
    stems = {Path(c).stem for c in changed if not (Path(c).name.startswith("test_") or Path(c).name.endswith("_test.py"))}
    for t in tests:
        rel = t.relative_to(folder).as_posix()
        if rel in changed:
            pick.append(rel)
            continue
        try:
            body = t.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(re.search(rf"\b(?:import|from)\s+[\w.]*\b{re.escape(s)}\b", body) or s in t.stem for s in stems):
            pick.append(rel)
    if not pick:
        return command, "all tests (no test is related to the changed files)"
    try:
        words = shlex.split(command, posix=not paths.IS_WINDOWS)
    except ValueError:
        words = command.split()
    base = [w for w in words if not (folder / w.strip('"')).exists()]   # drop paths the command already names
    quoted = [f'"{p}"' if " " in p else p for p in pick]
    return " ".join(base + quoted), f"{len(pick)} related test file(s) of {len(tests)}"
