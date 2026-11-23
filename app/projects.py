"""Projects: one folder per project inside the workspace. App bookkeeping lives in <project>/.aiteam/."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import paths, safefile, settings

log = logging.getLogger(__name__)

SETUP_SLUG = "setup"
META = ".aiteam"
GITIGNORE = ".venv/\n__pycache__/\n*.pyc\n.aiteam/\n.env\n"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "project"


def valid_slug(name: str) -> bool:
    """A project id: lower-case letters, digits and dashes. A folder in the projects folder whose name is not one
    (capitals, spaces...) is reached through the list of outside folders instead (L58)."""
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name or ""))


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def project_dir(slug: str) -> Path:
    if slug == SETUP_SLUG:
        d = paths.data_dir() / "setup"
        d.mkdir(parents=True, exist_ok=True)
        return d
    if not valid_slug(slug):
        raise ValueError("Invalid project id")
    ext = external().get(slug)
    return Path(ext) if ext else settings.workspace() / slug


# ---------------------------------------------------------------------------- existing folders
def _external_file() -> Path:
    return paths.data_dir() / "external-projects.json"


def external() -> dict[str, str]:
    """Projects that live in a folder the user already had (slug -> absolute path)."""
    try:
        return json.loads(_external_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_external(data: dict) -> None:
    _external_file().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _unique_slug(name: str, own: Path | None = None) -> str:
    """A free project id from a name. `own` is the folder being added: on Windows "VLC-Multiplayer" and
    "vlc-multiplayer" are the same folder, which must not count as taken (L58)."""
    base = slugify(name)
    slug, n = base, 2
    while slug in external() or ((settings.workspace() / slug).exists()
                                 and not (own is not None and _same_folder(settings.workspace() / slug, own))):
        slug, n = f"{base}-{n}"[:40], n + 1
    return slug


PROJECT_DOCS = ("REQUIREMENTS.md", "ARCHITECTURE.md", "PLAN.md", "STATUS.md")
_SKIP_DIRS = {".git", ".aiteam", ".venv", "venv", "node_modules", "__pycache__", "target", "dist", "build", ".idea"}


def scan_folder(path: Path) -> dict:
    """A quick, free (no AI) look at an existing folder before Fred starts: which of the project documents already
    exist (any capitalisation, in the folder or a docs/ subfolder), whether it is a Git repository, and its size."""
    found, missing = {}, []
    for doc in PROJECT_DOCS:
        hit = None
        for base in (path, path / "docs", path / "doc"):
            if base.is_dir():
                hit = next((f for f in base.iterdir() if f.is_file() and f.name.lower() == doc.lower()), None)
                if hit:
                    break
        if hit:
            found[doc] = hit.relative_to(path).as_posix()
        else:
            missing.append(doc)
    files = 0
    for _root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        files += len(names)
        if files > 20000:
            break
    return {"found": found, "missing": missing, "git": (path / ".git").is_dir(), "files": files,
            "readme": next((f.name for f in path.iterdir() if f.is_file() and f.name.lower().startswith("readme")), "")}


def create_from_folder(folder: str, name: str = "") -> dict:
    """Start where we left off: register an existing folder as a project (nothing is copied or changed
    except a hidden .aiteam folder for the chat history)."""
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise ValueError("That folder does not exist.")
    path = path.resolve()
    if path in (Path.home().resolve(), Path(path.anchor)) or len(path.parts) < 2:
        raise ValueError("Choose a project folder, not your whole drive or home folder.")
    repair()
    known = slug_for_folder(path)
    if known:
        # Already one of your projects (item 65): never register it a second time - that used to give it a new
        # name, overwrite its record and leave an "Unknown project". Reopen it and let Fred re-read everything.
        return update(known, hidden=False, scan=scan_folder(path), reopened_at=time.time()) | {"reopened": True,
                                                                                               "adopted": True}
    ws = settings.workspace().resolve()
    if path.parent == ws and valid_slug(path.name) and not (path / META / "project.json").exists():
        slug, old = path.name, {}   # already in the projects folder: it becomes an ordinary project
    elif (path / META / "project.json").exists():  # a project from an earlier install / another PC: adopt it
        try:
            old = json.loads((path / META / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        prev = str(old.get("slug") or "")
        # keep its own name when it is free, so everything stored under that name still matches
        free = valid_slug(prev) and prev not in external() and \
            (not (ws / prev).exists() or _same_folder(ws / prev, path))
        slug = prev if free else _unique_slug(old.get("name") or path.name, path)
    else:
        old, slug = {}, _unique_slug(name or path.name, path)
    if not (path.parent == ws and path.name == slug):   # compare the names as text: Windows ignores capitals (L58)
        reg = external()
        reg[slug] = str(path)
        _save_external(reg)
    (path / META).mkdir(exist_ok=True)
    project = {"slug": slug, "name": " ".join((name or old.get("name") or path.name).split())[:60], "kind": "project",
               "phase": "discover", "created": time.time(), "updated": time.time(), "session_id": None,
               "cost_usd": 0.0, "plan_approved": False, "architecture_approved": False, "github_repo": "",
               "existing_folder": True, "source": str(path)}
    if old:  # an AI Team Lite project from before (another PC, or moved): continue exactly where it was
        keep = ("phase", "github_repo",
                "cost_usd", "created", "iterations", "released", "priority", "after_default")
        project.update({k: old[k] for k in keep if k in old})
        project["adopted"] = True
    project["scan"] = scan_folder(path)
    _state_file(slug).unlink(missing_ok=True)
    save(project)
    gi = path / ".gitignore"
    if gi.exists() and ".aiteam" not in gi.read_text(encoding="utf-8", errors="ignore"):
        with open(gi, "a", encoding="utf-8") as f:
            f.write("\n.aiteam/\n")
    exclude_meta(path)
    return project


def exclude_meta(folder: Path) -> None:
    """Keep AI Team Lite's own .aiteam folder out of Git without touching the user's files."""
    if not (folder / ".git").is_dir():
        return
    ex = folder / ".git" / "info" / "exclude"
    try:
        text = ex.read_text(encoding="utf-8", errors="ignore") if ex.exists() else ""
        if ".aiteam" not in text:
            ex.parent.mkdir(parents=True, exist_ok=True)
            with open(ex, "a", encoding="utf-8") as f:
                f.write("\n.aiteam/\n")
    except OSError:
        pass


def _rmtree(folder: Path) -> None:
    """Delete a folder, including read-only files (Git makes its object files read-only on Windows)."""
    def retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(folder, onexc=retry)
    else:  # pragma: no cover
        shutil.rmtree(folder, onerror=retry)


# ---------------------------------------------------------------------------- hide / delete
def architecture_approved(project: dict) -> bool:
    """Projects from before 0.6.0 that already had an approved plan count as approved."""
    return bool(project.get("architecture_approved", bool(project.get("plan_approved_at"))))


def set_hidden(slug: str, hidden: bool) -> dict:
    return update(slug, hidden=hidden)


def delete(slug: str, mode: str) -> str:
    """mode: "remove" = forget the project but keep its files (existing folders only);
    "all" = delete the project folder and everything in it."""
    folder = project_dir(slug)
    reg = external()
    if mode in ("remove", "all"):
        try:   # REQ-22: take back the file-protection rights and profiles before the project goes
            from . import sandbox
            sandbox.cleanup(folder)
        except Exception:  # noqa: BLE001  (deleting a project must not fail on this)
            logging.exception("sandbox cleanup failed for %s", folder)
    if mode == "remove":
        if slug not in reg:
            raise ValueError("Only projects from an existing folder can be removed while keeping the files.")
        # The .aiteam folder (chat, plan, usage history) stays, so opening the folder again later brings it all back.
        reg.pop(slug)
        _save_external(reg)
        return (f"Removed from AI Team Lite. Your files in {folder} were kept; open that folder again with "
                "\"Continue an existing folder\" to bring the project back with its history.")
    if mode == "all":
        if folder.resolve() in (Path.home().resolve(), settings.workspace().resolve()) or len(folder.resolve().parts) < 3:
            raise ValueError("Refusing to delete this folder.")
        if folder.exists():
            _rmtree(folder)
        _state_file(slug).unlink(missing_ok=True)
        if slug in reg:
            reg.pop(slug)
            _save_external(reg)
        if folder.exists():
            raise ValueError(f"Some files could not be deleted (are they open?): {folder}")
        return f"Deleted {folder} and everything in it."
    raise ValueError("Unknown delete mode")


def meta_file(slug: str) -> Path:
    return project_dir(slug) / META / "project.json"


def history_file(slug: str) -> Path:
    return project_dir(slug) / META / "events.jsonl"


# M-1: these are kept in the app's own data folder, never believed from a file inside the project folder
GUARDED = ("plan_approved", "plan_approved_at", "architecture_approved", "launch")
MIGRATED = GUARDED[:3]   # not "launch": an old project asks to save its Launch command again


def _state_file(slug: str) -> Path:
    d = paths.data_dir() / "project-state"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{slug}.json"


def _read_state(slug: str) -> dict:
    s = safefile.read_json(_state_file(slug))
    return s if isinstance(s, dict) else {}


def migrate_state() -> list[str]:
    """One time, when the app starts after the upgrade: projects already in the app's own list (projects folder and
    registered outside folders) that have no record yet keep the approval their old file holds.
    Never run when a folder is adopted or loaded, so a copied folder still starts unapproved."""
    marker = _state_file("x").parent / ".migrated"
    if marker.exists():
        return []
    done = []
    for p in list_projects():
        slug = p["slug"]
        if _state_file(slug).exists():
            continue
        old = safefile.read_json(meta_file(slug))
        if not isinstance(old, dict):
            continue
        state = {k: old[k] for k in MIGRATED if k in old}
        if state:
            safefile.write_json(_state_file(slug), state)
            done.append(slug)
    marker.write_text("1", encoding="utf-8")
    return done


def load(slug: str) -> dict | None:
    if slug == SETUP_SLUG:
        default = {"slug": SETUP_SLUG, "name": "Setup with Fred", "phase": "setup", "kind": "setup"}
        p = {**default, **(safefile.read_json(meta_file(slug)) or {})}
    else:
        p = safefile.read_json(meta_file(slug))   # L34: never open while a save swaps the file
    if not isinstance(p, dict):
        return p
    for k in GUARDED:
        p.pop(k, None)
    p.update({"plan_approved": False, "architecture_approved": False})
    p.update({k: v for k, v in _read_state(slug).items() if k in GUARDED})
    return p


def save(project: dict) -> None:
    """L34: writers take turns and Windows' brief "Access is denied" is retried; if the file still cannot be saved,
    the work carries on (the error is logged) instead of stopping the team's turn."""
    try:
        state = {k: project[k] for k in GUARDED if k in project}
        safefile.write_json(_state_file(project["slug"]), state)
        safefile.write_json(meta_file(project["slug"]), {k: v for k, v in project.items() if k not in GUARDED})
    except OSError as e:
        log.warning("could not save %s: %s", project.get("slug"), e)


def update(slug: str, **changes) -> dict:
    with safefile.lock_for(meta_file(slug)):   # L34: read, change and write as one step, so no change is lost
        p = load(slug)
        if p is None:
            if slug != SETUP_SLUG:  # deleted or removed project: never re-create it
                return {}
            p = {"slug": slug}
        p.update(changes)
        save(p)
        return p


def slug_for_folder(path: Path) -> str | None:
    """The project that already lives in this folder, if any."""
    path = Path(path).resolve()
    ws = settings.workspace().resolve()
    if path.parent == ws and valid_slug(path.name) and (path / META / "project.json").exists() \
            and path.name not in external():
        return path.name
    for slug, other in external().items():
        try:
            if Path(other).resolve() == path:
                return slug
        except OSError:
            continue
    return None


def repair() -> list[str]:
    """Undo the damage of the old "open a folder that is already a project" bug (item 65): a project in the
    projects folder whose record was rewritten with another name, plus a duplicate entry pointing at the same
    folder. Safe to run any time; returns what it fixed."""
    fixed = []
    ws = settings.workspace()
    reg = external()
    changed = False
    if ws.exists():
        for d in ws.iterdir():
            f = d / META / "project.json"
            if not f.exists() or d.name in reg or not valid_slug(d.name):
                continue
            for slug, other in list(reg.items()):      # duplicate entry for a folder that is a normal project
                try:
                    same = Path(other).resolve() == d.resolve()
                except OSError:
                    same = False
                if same:
                    del reg[slug]
                    changed = True
                    fixed.append(f"removed duplicate {slug}")
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if p.get("slug") != d.name:
                fixed.append(f"{p.get('slug')} -> {d.name}")
                p["slug"] = d.name
                p.pop("adopted", None)
                f.write_text(json.dumps(p, indent=2), encoding="utf-8")
    if ws.exists():
        for d in ws.iterdir():                         # L58: a folder whose name is not a project id (capitals...)
            f = d / META / "project.json"
            if valid_slug(d.name) or not f.exists():
                continue
            if any(_same_folder(Path(other), d) for other in reg.values()):
                continue
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                p = None
            prev = str((p or {}).get("slug") or "")
            free = valid_slug(prev) and prev not in reg and \
                (not (ws / prev).exists() or _same_folder(ws / prev, d))
            slug = prev if free else _unique_slug(d.name, d)
            reg[slug] = str(d)
            changed = True
            fixed.append(f"{d.name} registered as {slug}")
            if p is not None and p.get("slug") != slug:
                p["slug"] = slug
                f.write_text(json.dumps(p, indent=2), encoding="utf-8")
    seen = {}
    for slug, other in list(reg.items()):              # two entries for the same outside folder
        key = str(Path(other)).lower()
        if key in seen:
            del reg[slug]
            changed = True
            fixed.append(f"removed duplicate {slug}")
            continue
        seen[key] = slug
        f = Path(other) / META / "project.json"
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            if p.get("slug") != slug:
                p["slug"] = slug
                f.write_text(json.dumps(p, indent=2), encoding="utf-8")
                fixed.append(f"record of {slug} fixed")
        except (OSError, ValueError):
            pass
    if changed:
        _save_external(reg)
    return fixed


def broken() -> list[dict]:
    """Projects AI Team Lite knows about but cannot open (item 66), so they can still be reloaded or removed."""
    out = []
    for slug, other in external().items():
        folder = Path(other)
        f = folder / META / "project.json"
        if not folder.exists():
            reason = "Its folder was not found (moved, renamed, deleted, or on a drive that is not connected)."
        elif not f.exists():
            reason = "Its AI Team Lite record (.aiteam folder) is missing."
        else:
            try:
                json.loads(f.read_text(encoding="utf-8"))
                continue
            except (OSError, ValueError):
                reason = "Its AI Team Lite record is damaged."
        out.append({"slug": slug, "name": folder.name or slug, "path": str(folder), "reason": reason,
                    "folder_exists": folder.exists()})
    ws = settings.workspace()
    for d in ws.iterdir() if ws.exists() else []:
        if d.name in external() or not valid_slug(d.name) or not (d / META / "project.json").exists():
            continue
        if load(d.name) is None:
            out.append({"slug": d.name, "name": d.name, "path": str(d), "reason": "Its AI Team Lite record is damaged.",
                        "folder_exists": True})
    return out


def forget_broken(slug: str) -> None:
    """Remove a project that cannot be opened from AI Team Lite's list. Nothing on disk is touched."""
    reg = external()
    if slug in reg:
        reg.pop(slug)
        _save_external(reg)
        return
    f = settings.workspace() / slug / META / "project.json"
    if f.exists():  # damaged record inside the projects folder: set it aside so the folder is ignored
        f.replace(f.with_suffix(".damaged"))


def reload_broken(slug: str, folder: str = "") -> dict:
    """Reload a project that cannot be opened, from its folder or from the folder it was moved to."""
    item = next((b for b in broken() if b["slug"] == slug), None)
    if not item:
        raise ValueError("This project opens normally.")
    target = Path(folder or item["path"]).expanduser()
    if not target.is_dir():
        raise ValueError("That folder does not exist. Choose the folder where the project is now.")
    f = target / META / "project.json"
    if f.exists():
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            f.replace(f.with_suffix(".damaged"))  # start a fresh record; the files and docs are re-read
    reg = external()
    if slug in reg:
        reg.pop(slug)
        _save_external(reg)
    return create_from_folder(str(target))


def _load_quietly(slug: str) -> dict | None:
    """One odd folder or record must never empty the whole list (L58)."""
    try:
        p = load(slug)
    except (OSError, ValueError) as e:
        log.warning("project %s skipped: %s", slug, e)
        return None
    if not isinstance(p, dict):
        return None
    if p.get("slug") != slug:   # the record names itself differently: the folder's id is the one that works
        p = {**p, "slug": slug}
    return p


def list_projects(include_hidden: bool = True) -> list[dict]:
    ws = settings.workspace()
    out = []
    try:
        names = list(ws.iterdir()) if ws.exists() else []
    except OSError as e:
        log.warning("projects folder not readable: %s", e)
        names = []
    for d in names:
        if valid_slug(d.name) and d.name not in external() and (d / META / "project.json").exists():
            p = _load_quietly(d.name)
            if p:
                out.append(p)
    for slug, path in external().items():
        if valid_slug(slug) and (Path(path) / META / "project.json").exists():
            p = _load_quietly(slug)
            if p:
                out.append(p)
    if not include_hidden:
        out = [p for p in out if not p.get("hidden")]
    return sorted(out, key=lambda p: -p.get("updated", 0))


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess | None:
    exe = paths.git_exe()
    if not exe:
        return None
    flags = 0x08000000 if paths.IS_WINDOWS else 0  # no console window
    return subprocess.run([exe, *paths.GIT_SAFE, *args], cwd=cwd, capture_output=True, text=True, timeout=60,
                          creationflags=flags, env=paths.git_env())


def create(name: str, has_ui: bool | None = None) -> dict:
    name = " ".join(name.split())[:60]
    if not name:
        raise ValueError("Give the project a name.")
    slug = _unique_slug(name)
    folder = settings.workspace() / slug
    (folder / META).mkdir(parents=True)
    (folder / "reports" / "screenshots").mkdir(parents=True)
    (folder / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    project = {"slug": slug, "name": name, "kind": "project", "phase": "discover", "created": time.time(),
               "updated": time.time(), "session_id": None, "cost_usd": 0.0, "plan_approved": False,
               "architecture_approved": False, "github_repo": ""}
    if has_ui is not None:
        project["has_ui"] = bool(has_ui)
    _state_file(slug).unlink(missing_ok=True)   # a left-over record under this name must not approve the new project
    save(project)
    try:
        _git(["init", "-b", "main"], folder)
    except Exception:
        pass
    return project


def read_doc(slug: str, name: str) -> str:
    f = project_dir(slug) / name
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        return ""


def inside(slug: str, target: str | Path) -> bool:
    """True when target is inside the project folder (used by the approval policy)."""
    root = project_dir(slug).resolve()
    try:
        p = Path(target)
        p = (p if p.is_absolute() else root / p).resolve()
        return p == root or root in p.parents
    except (OSError, ValueError):
        return False
