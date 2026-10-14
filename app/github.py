"""Minimal GitHub support with a personal access token: check it, create a repository, push a project."""
from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from . import paths, secrets_store

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


def _request(method: str, url: str, token: str, body: dict | None = None) -> tuple[dict, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "AI-Team-Lite-App"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("message", "")
        except Exception:
            msg = ""
        raise GitHubError(f"GitHub said {e.code}: {msg or e.reason}") from None
    except urllib.error.URLError as e:
        raise GitHubError(f"Could not reach GitHub: {e.reason}") from None


def token() -> str:
    t = secrets_store.get_secret("github_token")
    if not t:
        raise GitHubError("GitHub is not connected yet (no token saved).")
    return t


def check() -> dict:
    user, headers = _request("GET", f"{API}/user", token())
    scopes = headers.get("X-OAuth-Scopes") or headers.get("x-oauth-scopes") or ""
    return {"login": user.get("login"), "name": user.get("name") or user.get("login"),
            "id": user.get("id"), "scopes": scopes}


def create_repo(name: str, private: bool = True, description: str = "") -> dict:
    repo, _ = _request("POST", f"{API}/user/repos", token(),
                       {"name": name, "private": private, "description": description[:300], "auto_init": False})
    return {"full_name": repo["full_name"], "url": repo["html_url"], "clone_url": repo["clone_url"]}


def repo_full_name(url: str) -> str:
    import re
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", (url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def create_issue(repo_full: str, title: str, body: str) -> dict:
    issue, _ = _request("POST", f"{API}/repos/{repo_full}/issues", token(),
                        {"title": title[:200] or "Feedback", "body": body[:8000], "labels": ["feedback"]})
    return {"number": issue.get("number"), "url": issue.get("html_url")}


def list_issues(repo_full: str, since_number: int = 0) -> list[dict]:
    """Open issues labelled 'feedback' (the owner imports these into the feature queue)."""
    items, _ = _request("GET", f"{API}/repos/{repo_full}/issues?state=all&labels=feedback&per_page=100", token())
    out = []
    for it in items if isinstance(items, list) else []:
        if it.get("number", 0) > since_number and "pull_request" not in it:
            out.append({"number": it["number"], "title": it.get("title", ""), "body": it.get("body", "") or "",
                        "author": (it.get("user") or {}).get("login", ""), "url": it.get("html_url", ""),
                        "state": it.get("state", "open")})
    return out


def _git(args: list[str], cwd: Path, extra_config: list[str] | None = None) -> str:
    exe = paths.git_exe()
    if not exe:
        raise GitHubError("Git is not available.")
    cfg = []
    for c in extra_config or []:
        cfg += ["-c", c]
    r = subprocess.run([exe, *cfg, *args], cwd=cwd, capture_output=True, text=True, timeout=180,
                       creationflags=0x08000000 if paths.IS_WINDOWS else 0)
    if r.returncode != 0:
        raise GitHubError((r.stderr or r.stdout).strip()[-600:])
    return r.stdout.strip()


def push(folder: Path, message: str, clone_url: str) -> str:
    info = check()
    login, uid = info["login"], info["id"]
    ident = [f"user.name={info['name']}", f"user.email={uid}+{login}@users.noreply.github.com"]
    if not (folder / ".git").exists():
        _git(["init", "-b", "main"], folder)
    remotes = _git(["remote"], folder).split()
    if "origin" not in remotes:
        _git(["remote", "add", "origin", clone_url], folder)
    _git(["add", "-A"], folder)
    if _git(["status", "--porcelain"], folder):
        _git(["commit", "-m", message or "Update"], folder, ident)
    basic = base64.b64encode(f"{login}:{token()}".encode()).decode()
    _git(["push", "-u", "origin", "HEAD:main"], folder, [f"http.extraHeader=Authorization: Basic {basic}"])
    return _git(["log", "-1", "--format=%h %s"], folder)


def repo_info(full_name: str) -> dict:
    """{full_name, url, clone_url, can_push, private, empty} for an existing repository."""
    repo, _ = _request("GET", f"{API}/repos/{full_name}", token())
    perms = repo.get("permissions") or {}
    return {"full_name": repo.get("full_name", full_name), "url": repo.get("html_url", ""),
            "clone_url": repo.get("clone_url", ""), "can_push": bool(perms.get("push") or perms.get("admin")),
            "private": bool(repo.get("private")), "empty": not repo.get("pushed_at") or repo.get("size", 0) == 0}


def publish(folder: Path, message: str, clone_url: str, tag: str = "") -> str:
    """Publish the files in `folder` as the next version on the repository's main branch: it builds on what is
    already there (so every update is a normal step forward, never rejected), and tags the version."""
    info = check()
    login, uid = info["login"], info["id"]
    ident = [f"user.name={info['name']}", f"user.email={uid}+{login}@users.noreply.github.com"]
    basic = base64.b64encode(f"{login}:{token()}".encode()).decode()
    auth = [f"http.extraHeader=Authorization: Basic {basic}"]
    if not (folder / ".git").exists():
        _git(["init", "-b", "main"], folder)
    if "origin" not in _git(["remote"], folder).split():
        _git(["remote", "add", "origin", clone_url], folder)
    try:  # build on the latest published version, if there is one
        _git(["fetch", "--depth", "1", "origin", "main"], folder, auth)
        _git(["reset", "--soft", "FETCH_HEAD"], folder)
    except GitHubError:
        pass  # empty repository: this is the first version
    _git(["add", "-A"], folder)
    if _git(["status", "--porcelain"], folder):
        _git(["commit", "-m", message or "Update"], folder, ident)
    _git(["push", "origin", "HEAD:main"], folder, auth)
    if tag:
        try:
            _git(["tag", "-a", tag, "-m", message or tag], folder, ident)
            _git(["push", "origin", tag], folder, auth)
        except GitHubError:
            pass  # the tag already exists (same version published again): the code itself is up to date
    return _git(["log", "-1", "--format=%h %s"], folder)
