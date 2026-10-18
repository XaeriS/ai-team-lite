"""PROJECT_MAP.md: a short, automatically maintained index of the project's files (L20).

Workers start each task with a fresh context and used to spend many tokens listing, searching and reading files just
to find their way. The map gives them one line per file (what it is for) and the entry points, so they open the right
file first. It is built from the files themselves (docstrings, headings, first comments): no model call, and unchanged
files are read again only when they change.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

NAME = "PROJECT_MAP.md"
CACHE = Path(".aiteam") / "map-cache.json"
SKIP_DIRS = {".git", ".aiteam", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist", ".pytest_cache",
             ".archify", "test-logs", "screenshots", ".mypy_cache", ".idea", ".vscode"}
TEXT = {".py", ".md", ".txt", ".bat", ".cmd", ".ps1", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml", ".html",
        ".css", ".js", ".sql", ".csv"}
MAX_FILES = 300
ROLE = {"run.bat": "starts the app (double-click)", "requirements.txt": "Python packages the app needs",
        "README.md": "how to use the app", "REQUIREMENTS.md": "the agreed requirements (REQ-n)",
        "ARCHITECTURE.md": "how it is built (approved decisions)", "PLAN.md": "the plan and its iterations",
        "STATUS.md": "where the project stands"}


def _describe(f: Path) -> str:
    if f.name in ROLE:
        return ROLE[f.name]
    try:
        text = f.read_text(encoding="utf-8", errors="ignore")[:6000]
    except OSError:
        return ""
    if f.suffix == ".py":
        try:
            tree = ast.parse(text)
            doc = ast.get_docstring(tree)
            if doc:
                return " ".join(doc.strip().splitlines()[0].split())[:140]
            names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            if names:
                return "defines " + ", ".join(names[:6]) + (" …" if len(names) > 6 else "")
        except SyntaxError:
            pass
        m = re.search(r"^\s*#\s*(.+)$", text, re.M)
        return m.group(1).strip()[:140] if m else ""
    if f.suffix == ".md":
        m = re.search(r"^#+\s+(.+)$", text, re.M)
        return m.group(1).strip()[:140] if m else ""
    if f.suffix in (".bat", ".cmd", ".ps1"):
        m = re.search(r"^\s*(?:rem|::|#)\s*(.+)$", text, re.M | re.I)
        return m.group(1).strip()[:140] if m else "script"
    if f.suffix in (".csv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"):
        return "data / settings file"
    m = re.search(r"<title>([^<]+)</title>", text, re.I)
    return m.group(1).strip()[:140] if m else ""


def build(folder: Path) -> str:
    """The map's text. Uses a small cache so only new or changed files are read."""
    cache_file = folder / CACHE
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    rows, seen, total = [], {}, 0
    for f in sorted(folder.rglob("*")):
        rel = f.relative_to(folder)
        if not f.is_file() or SKIP_DIRS & set(rel.parts) or f.suffix.lower() not in TEXT or f.name.startswith(".") \
                or rel.as_posix() == NAME:
            continue
        total += 1
        if len(rows) >= MAX_FILES:
            continue
        key = rel.as_posix()
        try:
            stamp = f"{f.stat().st_mtime_ns}:{f.stat().st_size}"
        except OSError:
            continue
        old = cache.get(key) or {}
        desc = old.get("d", "") if old.get("s") == stamp else _describe(f)
        seen[key] = {"s": stamp, "d": desc}
        rows.append((key, desc))
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(seen), encoding="utf-8")
    except OSError:
        pass
    tests = [k for k, _ in rows if Path(k).name.startswith("test_") or Path(k).name.endswith("_test.py")]
    start = [k for k in ("run.bat", "run_app.bat", "main.py", "app.py") if any(r[0] == k for r in rows)]
    lines = ["# Project map", "",
             "> Maintained automatically by AI Team Lite after each iteration. Read this first to find the right file;",
             "> open only the files you need.", ""]
    if start:
        lines.append("**Start:** " + ", ".join(f"`{s}`" for s in start))
    if tests:
        dirs = sorted({str(Path(t).parent) for t in tests})
        lines.append(f"**Tests:** {len(tests)} file(s) in " + ", ".join(f"`{d}`" for d in dirs))
    lines += ["", "| File | What it is for |", "|------|----------------|"]
    lines += [f"| `{k}` | {d.replace('|', '/') or ''} |" for k, d in rows]
    if total > len(rows):
        lines += ["", f"… {total - len(rows)} more files not listed."]
    return "\n".join(lines) + "\n"


def refresh(folder: Path) -> bool:
    """Write PROJECT_MAP.md if it changed. Returns True when it was (re)written. Never raises."""
    try:
        if not folder.exists():
            return False
        text = build(folder)
        target = folder / NAME
        if target.exists() and target.read_text(encoding="utf-8") == text:
            return False
        target.write_text(text, encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001  (the map is a help, never a reason to fail)
        return False
