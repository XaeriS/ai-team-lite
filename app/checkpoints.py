"""Checkpoints per iteration (L48): what an iteration changed, in plain words, and going back to the state of an
earlier iteration. Every finished iteration is already a Git commit; going back adds a new commit that puts the files
back as they were, so nothing is ever deleted from the history and going back can itself be undone."""
from __future__ import annotations

import time
from pathlib import Path

from . import iterations, projectmap, projects, safefile, tracking

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"   # Git's name for "no files at all"
MAX_DIFF_LINES = 400
GROUPS = [("app", "The app itself"), ("tests", "Tests (the proof that it works)"), ("docs", "Documents"),
          ("data", "Settings and data files")]
CHANGE_WORDS = {"A": "new", "M": "changed", "D": "removed", "R": "renamed", "C": "copied", "T": "changed"}


class CheckpointError(ValueError):
    """A plain-words reason shown to the user."""


def _group(path: str) -> str:
    p = path.lower()
    name = p.rsplit("/", 1)[-1]
    if p.startswith(("tests/", "test/")) or "/tests/" in p or name.startswith("test_") or name.endswith("_test.py"):
        return "tests"
    if name.endswith((".md", ".txt", ".pdf", ".docx")) and name != "requirements.txt" or p.startswith(("reports/", "docs/")):
        return "docs"
    if name.endswith((".json", ".csv", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".xlsx", ".db", ".sqlite")) \
            or name in ("requirements.txt", ".gitignore"):
        return "data"
    return "app"


def _iteration(slug: str, n: int) -> tuple[list[dict], dict]:
    its = iterations.load(slug)
    it = next((x for x in its if x["n"] == n), None)
    if not it or it.get("status") != "done" or not it.get("commit"):
        raise CheckpointError(f"Iteration {n} is not finished yet, so there is nothing to show or go back to.")
    return its, it


def _git(folder: Path, *args: str) -> str:
    r = iterations._git(folder, *args)
    if r.returncode != 0:
        raise CheckpointError(f"Git could not do this: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def _base(folder: Path, its: list[dict], it: dict) -> str:
    """The state just before this iteration: the previous finished iteration, else the commit before it."""
    before = [x for x in its if x["n"] < it["n"] and x.get("status") == "done" and x.get("commit")]
    if before:
        return before[-1]["commit"]
    r = iterations._git(folder, "rev-parse", "--verify", "-q", f"{it['commit']}^")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else EMPTY_TREE


def _descriptions(folder: Path) -> dict:
    cache = safefile.read_json(folder / projectmap.CACHE, {})
    return {k: (v or {}).get("d", "") for k, v in cache.items()} if isinstance(cache, dict) else {}


def changes(slug: str, n: int) -> dict:
    """What iteration n changed: files by kind, with how much changed and what each file is for."""
    its, it = _iteration(slug, n)
    folder = projects.project_dir(slug)
    base = _base(folder, its, it)
    status = {}
    for line in _git(folder, "diff", "--name-status", "-M", base, it["commit"]).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status[parts[-1]] = (parts[0][:1], parts[1] if parts[0][:1] in "RC" and len(parts) == 3 else "")
    counts = {}
    for line in _git(folder, "diff", "--numstat", "-M", base, it["commit"]).splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            path = parts[2]
            if " => " in path:   # renamed: "old => new" or "dir/{old => new}"
                path = next((p for p in status if p.split("/")[-1] == path.split(" => ")[-1].rstrip("}").split("/")[-1]), path)
            counts[path] = (None if parts[0] == "-" else int(parts[0]), None if parts[1] == "-" else int(parts[1]))
    described = _descriptions(folder)
    groups = {k: [] for k, _ in GROUPS}
    for path in sorted(status):
        code, old = status[path]
        added, removed = counts.get(path, (None, None))
        groups[_group(path)].append({"path": path, "change": CHANGE_WORDS.get(code, "changed"), "from": old,
                                     "added": added, "removed": removed, "what": described.get(path, "")})
    reqs = tracking.parse_requirements(projects.read_doc(slug, "REQUIREMENTS.md"))
    later = [x for x in its if x["n"] > n and x.get("status") in ("done", "building")]
    return {"n": n, "name": it["name"], "goal": it.get("goal", ""), "summary": it.get("summary", ""),
            "commit": it["commit"], "finished": it.get("finished"),
            "requirements": [{"id": r, "title": reqs.get(r, "")} for r in it.get("requirements") or []],
            "groups": [{"key": k, "label": label, "files": groups[k]} for k, label in GROUPS if groups[k]],
            "files": len(status), "added": sum(a or 0 for a, _ in counts.values()),
            "removed": sum(r or 0 for _, r in counts.values()),
            "later": [{"n": x["n"], "name": x["name"]} for x in later], "unsaved": bool(_dirty(folder, own=False)),
            "can_go_back": bool(later) or bool(_dirty(folder, own=False)) or not _same_as(folder, it["commit"])}


def file_diff(slug: str, n: int, path: str) -> dict:
    """The changed lines of one file in iteration n (for people who want to see them)."""
    its, it = _iteration(slug, n)
    folder = projects.project_dir(slug)
    if not projects.inside(slug, folder / path):
        raise CheckpointError("That file is not in this project.")
    text = _git(folder, "diff", "-M", "--no-color", "-U2", _base(folder, its, it), it["commit"], "--", path)
    lines = [x for x in text.splitlines() if not x.startswith(("diff --git", "index ", "--- ", "+++ ", "new file", "deleted file",
                                                              "similarity", "rename "))]
    return {"path": path, "lines": lines[:MAX_DIFF_LINES], "more": max(0, len(lines) - MAX_DIFF_LINES),
            "binary": "Binary files" in text}


def _dirty(folder: Path, own: bool = True) -> str:
    """Unsaved changes; with own=False, changes to the documents the app maintains itself do not count."""
    lines = iterations._git(folder, "status", "--porcelain").stdout.splitlines()
    return "\n".join(x for x in lines if x.strip() and (own or x[3:].strip('"') not in ("PLAN.md", "PROJECT_MAP.md")))


OWN = (":(exclude)PLAN.md", ":(exclude)PROJECT_MAP.md")   # kept up to date by the app itself
KEEP = ":(exclude).gitignore"   # the newest list of ignored files stays, so a restored old one cannot expose them


def _same_as(folder: Path, commit: str) -> bool:
    """True when the saved files are already exactly those of `commit` (the ignore list aside)."""
    return iterations._git(folder, "diff", "--quiet", commit, "HEAD", "--", ".", KEEP, *OWN).returncode == 0


def _restore(folder: Path, commit: str) -> None:
    """Make the tracked files exactly as they were at `commit`. Files Git ignores (the Python environment, local data
    kept out of Git) are not touched."""
    _git(folder, "restore", f"--source={commit}", "--staged", "--worktree", "--", ".", KEEP)


def _save(slug: str, folder: Path, message: str) -> str:
    """Bring the plan document and the project map in line, then save everything as one new commit."""
    iterations.refresh_plan_doc(slug)
    projectmap.refresh(folder)
    _git(folder, "add", "-A")
    r = iterations._git(folder, "commit", "-m", message)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        raise CheckpointError(f"Git could not save this: {(r.stderr or r.stdout).strip()[-300:]}")
    return _git(folder, "rev-parse", "--short", "HEAD").strip()


def go_back(slug: str, n: int) -> dict:
    """Put the project back as it was when iteration n was finished. Work done since (even unsaved) is first kept in
    a safety copy; the later iterations become "planned" again. Returns what was undone and how to undo this."""
    its, it = _iteration(slug, n)
    folder = projects.project_dir(slug)
    projects.exclude_meta(folder)
    safety = None
    w = (projects.load(slug) or {}).get("went_back") or {}
    if w.get("to") == n and not _dirty(folder) and _git(folder, "rev-parse", "--short", "HEAD").strip() == w.get("commit"):
        raise CheckpointError(f"The project is already as it was at iteration {n}.")
    if _dirty(folder):   # work in progress that was never saved: keep it, so going back loses nothing
        _git(folder, "add", "-A")
        r = iterations._git(folder, "commit", "-m", f"Safety copy before going back to iteration {n}")
        if r.returncode != 0:
            raise CheckpointError(f"The unsaved work could not be kept safe, so nothing was changed: "
                                  f"{(r.stderr or r.stdout).strip()[-200:]}")
        safety = True
    before = _git(folder, "rev-parse", "--short", "HEAD").strip()
    if _same_as(folder, it["commit"]) and not any(x["n"] > n and x.get("status") != "planned" for x in its):
        raise CheckpointError(f"The project is already as it was at iteration {n}.")
    _restore(folder, it["commit"])
    p = projects.load(slug) or {}
    undone = []
    for x in its:
        if x["n"] > n and x.get("status") != "planned":
            undone.append({"n": x["n"], "name": x["name"]})
            for key in ("commit", "summary", "finished", "started", "last_test", "light_cleanup", "red"):
                x.pop(key, None)
            x["status"] = "planned"
            for t in x.get("tasks") or []:
                t.update(done=False)
                t.pop("note", None)
                t.pop("finished", None)
    projects.update(slug, iterations=its, waiting_release=None, phase="execute" if any(x["status"] != "done" for x in its)
                    else p.get("phase"))
    head = _save(slug, folder, f"Went back to iteration {n}: {it['name']}")
    projects.update(slug, went_back={"to": n, "from_commit": before, "commit": head, "time": time.time(),
                                     "iterations": p.get("iterations"), "phase": p.get("phase"),
                                     "waiting_release": p.get("waiting_release")})
    return {"n": n, "name": it["name"], "undone": undone, "kept_unsaved": bool(safety), "commit": head, "from_commit": before}


def undo_go_back(slug: str) -> dict:
    """Bring back everything as it was just before the last "go back"."""
    p = projects.load(slug) or {}
    w = p.get("went_back")
    if not w:
        raise CheckpointError("There is nothing to undo.")
    folder = projects.project_dir(slug)
    if _dirty(folder):
        _git(folder, "add", "-A")
        iterations._git(folder, "commit", "-m", "Safety copy before undoing the go-back")
    if _git(folder, "rev-parse", "--short", "HEAD").strip() != w["commit"] and \
            any(x.get("status") in ("done", "building") and x["n"] > w["to"] for x in iterations.load(slug)):
        raise CheckpointError("New work was built after going back, so it can no longer be undone in one click. "
                              "Ask Fred to bring back what you need.")
    _restore(folder, w["from_commit"])
    projects.update(slug, iterations=w.get("iterations") or [], phase=w.get("phase"), went_back=None,
                    waiting_release=w.get("waiting_release"))
    head = _save(slug, folder, f"Undid going back to iteration {w['to']}")
    return {"to": w["to"], "commit": head}
