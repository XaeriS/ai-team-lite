"""Minimal GitHub support with a personal access token: check it, create a repository, push a project."""
from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.parse
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
    """'owner/repo' for an https or ssh address on exactly github.com (no password, no port), else ''."""
    import re
    u = (url or "").strip()
    m = re.fullmatch(r"git@github\.com:(.+)", u, re.I)       # the short ssh form
    if m:
        path = m.group(1)
    else:
        try:
            s = urllib.parse.urlsplit(u)
            ok = (s.scheme.lower(), s.netloc.lower()) in (("https", "github.com"), ("ssh", "git@github.com"))
        except ValueError:
            return ""
        if not ok or s.query or s.fragment:
            return ""
        path = s.path.lstrip("/")
    m = re.fullmatch(r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?", path)
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
    cfg = list(paths.GIT_SAFE)
    for c in extra_config or []:
        cfg += ["-c", c]
    r = subprocess.run([exe, *cfg, *args], cwd=cwd, capture_output=True, text=True, timeout=180, env=paths.git_env(),
                       creationflags=0x08000000 if paths.IS_WINDOWS else 0)
    if r.returncode != 0:
        raise GitHubError((r.stderr or r.stdout).strip()[-600:])
    return r.stdout.strip()


def _same_repo(a: str, b: str) -> bool:
    return repo_full_name(a).lower() == repo_full_name(b).lower() != ""


def _check_origin(folder: Path, clone_url: str) -> None:
    """M-2: files and the token go only to the recorded repository, never to an address found in the folder's .git."""
    remotes = _git(["remote"], folder).split()
    if "origin" not in remotes:
        _git(["remote", "add", "origin", clone_url], folder)
        return
    # fetch address, push address(es) and every address of the remote: all must be the recorded repository
    seen = [_git(["remote", "get-url", "origin"], folder)]
    for extra in (["--push", "--all"], ["--all"]):
        seen += _git(["remote", "get-url", *extra, "origin"], folder).splitlines()
    now = next((u for u in seen if not _same_repo(u, clone_url)), "")
    if now:
        raise GitHubError(f"This folder's Git points to {now}, not to the saved repository {clone_url}. "
                          "Nothing was sent. Fix or remove the remote called origin first.")


def origin_url(folder: Path) -> str:
    """Where this folder's Git would really send files ('' when there is no origin yet)."""
    try:
        return _git(["remote", "get-url", "origin"], folder) if (folder / ".git").exists() else ""
    except GitHubError:
        return ""


def push(folder: Path, message: str, clone_url: str) -> str:
    info = check()
    login, uid = info["login"], info["id"]
    ident = [f"user.name={info['name']}", f"user.email={uid}+{login}@users.noreply.github.com"]
    if not (folder / ".git").exists():
        _git(["init", "-b", "main"], folder)
    _check_origin(folder, clone_url)
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
    _check_origin(folder, clone_url)
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


def needs_workflow_scope() -> bool:
    """L52c: GitHub refuses a push that adds .github/workflows unless a classic token has the "workflow" scope.
    Fine-grained tokens do not list scopes: then we cannot know, and the push says so if it fails."""
    scopes = [x.strip() for x in str(check().get("scopes") or "").split(",") if x.strip()]
    return bool(scopes) and "workflow" not in scopes


def create_release(repo_full: str, tag: str, title: str, notes: str, asset: Path, target: str = "main") -> str:
    """L52d: publish a release with the installer attached. Returns the release page."""
    tok = token()
    rel, _ = _request("POST", f"{API}/repos/{repo_full}/releases", tok,
                      {"tag_name": tag, "name": title, "body": notes, "target_commitish": target})
    upload = str(rel.get("upload_url") or "").split("{")[0]
    if not upload:
        raise GitHubError("GitHub did not return where to upload the installer.")
    data = asset.read_bytes()
    req = urllib.request.Request(f"{upload}?name={urllib.parse.quote(asset.name)}", data=data, method="POST", headers={
        "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "Content-Type": "application/octet-stream",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "AI-Team-Lite-App"})
    try:
        with urllib.request.urlopen(req, timeout=600):
            pass
    except urllib.error.HTTPError as e:
        raise GitHubError(f"GitHub refused the installer upload ({e.code}). The release page exists: "
                          f"{rel.get('html_url', '')}") from None
    except urllib.error.URLError as e:
        raise GitHubError(f"Could not upload the installer: {e.reason}") from None
    return str(rel.get("html_url") or "")
