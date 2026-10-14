"""Publish AI Team Lite's own source to a GitHub repo to share it, and check that repo for a newer version so the
app can offer to update (item 46). The update is only ever offered when no work is in progress.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path

from . import __version__, github, paths, settings

SOURCE_DIRS = ["app", "installer", "tests"]   # what is pushed; big binaries (python/, git/) are never included
SOURCE_FILES = ["README.md", "requirements.txt"]  # BUGS.md is intentionally NOT published (the owner's notes)
GITIGNORE = "__pycache__/\n*.pyc\n.venv/\npython/\ngit/\nbuild/\nBUGS.md\nnode/\narchify/\n"


def _version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


# A real key is a prefix followed by a long run of token characters. Requiring that run (24+) means the scan
# flags genuine secrets without tripping on placeholders ("sk-ant-…") or the short fake keys used in tests.
SECRET_RE = re.compile(r"(?:sk-ant-|AIza|xai-|ghp_|github_pat_|gho_)[A-Za-z0-9_-]{24,}")


def share_config() -> dict:
    try:
        return json.loads((paths.install_dir() / "app" / "share.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_owner() -> bool:
    """The owner is the machine that set up sharing: it has the app-repo push token saved."""
    from . import secrets_store
    return bool(settings.load().get("is_owner") and secrets_store.has("app_repo_token"))


def is_friend_install() -> bool:
    """A friend received the app with a share.json baked in (pointing at the owner's repo) and cannot publish.
    Everyone else - the author, whether or not they've set up sharing yet - manages their own feature queue."""
    return bool(share_config().get("app_repo")) and not is_owner()


def can_manage_features() -> bool:
    return not is_friend_install()


def source_root() -> Path:
    return paths.install_dir()


def stage_source(dst: Path) -> Path:
    """Copy the shareable source into dst (skips the bundled Python and Git, caches, build output)."""
    root = source_root()
    dst.mkdir(parents=True, exist_ok=True)
    for d in SOURCE_DIRS:
        src = root / d
        if src.is_dir():
            shutil.copytree(src, dst / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "node_modules"))
    for fn in SOURCE_FILES:
        if (root / fn).is_file():
            shutil.copy2(root / fn, dst / fn)
    (dst / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (dst / "VERSION").write_text(__version__ + "\n", encoding="utf-8")
    scan_for_secrets(dst)
    return dst


def scan_for_secrets(root: Path) -> None:
    """Refuse to publish if a secret-shaped string or the owner's GitHub login leaks into the files."""
    from . import secrets_store
    hits = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() in (".png", ".ico", ".jpg", ".gif", ".webp", ".exe", ".dll"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = SECRET_RE.search(text)
        if m:
            hits.append(f"{p.name}: looks like it contains a secret ({m.group(0)[:8]}...)")
        # a real saved token appearing verbatim is the worst case
        for name in secrets_store.KNOWN:
            val = secrets_store.get_secret(name)
            if val and len(val) > 8 and val in text:
                hits.append(f"{p.name}: contains your saved {name}")
    if hits:
        raise RuntimeError("Not publishing - something private was found:\n- " + "\n- ".join(sorted(set(hits))[:10]))


def set_up_sharing(repo_name: str, private: bool = False) -> dict:
    """One-time: create the repo, push the current version, and mark THIS machine as the owner. The push token is
    saved locally (never shipped); a small share.json with only the public repo URL ships to friends."""
    from . import secrets_store
    name = re.sub(r"[^A-Za-z0-9._-]", "-", repo_name)[:90] or "ai-team-lite"
    repo = github.create_repo(name, private, "AI Team Lite - an AI software team you talk to")
    secrets_store.set_secret("app_repo_token", secrets_store.get_secret("github_token") or "")
    settings.save({"app_repo": repo["url"], "app_repo_clone": repo["clone_url"], "is_owner": True})
    push_version()
    return repo


def link_existing(repo: str) -> dict:
    """One-time, owner only: use a repository that already exists (e.g. XaeriS/ai-team-lite) for sharing. Checks that
    the saved GitHub token can write to it, marks this PC as the owner and publishes the current version."""
    from . import secrets_store
    repo = (repo or "").strip()
    full = github.repo_full_name(repo) if "github.com" in repo else repo.strip("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,39}/[A-Za-z0-9_.-]{1,100}", full):
        raise RuntimeError("Write it as owner/name, e.g. XaeriS/ai-team-lite (or paste its GitHub address).")
    info = github.repo_info(full)
    if not info["can_push"]:
        raise RuntimeError(f"Your GitHub connection cannot write to {full}. Use a token with write access to "
                           "this repository (Contents: read and write), then try again.")
    secrets_store.set_secret("app_repo_token", secrets_store.get_secret("github_token") or "")
    settings.save({"app_repo": info["url"], "app_repo_clone": info["clone_url"], "is_owner": True,
                   "published_version": ""})
    push_version()
    return info


def push_version() -> str:
    """Owner only: push the current source to the shared repo (used by set-up and by the auto-update on a new
    version). Writes share.json first so friends learn the repo URL from the code they receive."""
    if not is_owner():
        raise RuntimeError("Only the owner machine can publish updates.")
    repo_url = settings.load().get("app_repo") or ""
    clone = settings.load().get("app_repo_clone") or ""
    if not clone:
        raise RuntimeError("Sharing is not set up yet.")
    tmp = Path(tempfile.mkdtemp())
    try:
        stage_source(tmp)
        (tmp / "app").mkdir(exist_ok=True)
        (tmp / "app" / "share.json").write_text(json.dumps({"app_repo": repo_url}, indent=2), encoding="utf-8")
        github.publish(tmp, f"AI Team Lite {__version__}", clone, tag=f"v{__version__}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    settings.save({"published_version": __version__})
    return repo_url


def auto_push_if_owner_updated() -> bool:
    """When the owner installs a newer version, push it to the shared repo automatically. Returns True if pushed."""
    if not is_owner():
        return False
    if settings.load().get("published_version") == __version__:
        return False
    try:
        push_version()
        return True
    except Exception:  # noqa: BLE001
        return False


def _raw_version(repo_url: str) -> str | None:
    """Read VERSION from the repo's default branch via GitHub's raw host (public repos)."""
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url.strip())
    if not m:
        return None
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{branch}/VERSION"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return r.read().decode("utf-8", "replace").strip().splitlines()[0]
        except Exception:  # noqa: BLE001
            continue
    return None


def check(repo_url: str | None = None) -> dict:
    """Compare the repo's VERSION with the running version. {current, latest, available, repo}."""
    repo = repo_url or settings.load().get("app_repo") or share_config().get("app_repo") or ""
    latest = _raw_version(repo) if repo else None
    available = bool(latest) and _version_tuple(latest) > _version_tuple(__version__)
    return {"current": __version__, "latest": latest, "available": available, "repo": repo}
