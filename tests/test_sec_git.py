"""M-2: the app's git calls must not run programs hidden in the project's .git, and a push must go only to the
repository the app created or recorded, and its card must say where it goes."""
import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import engine as engine_mod, github, iterations, paths, projects, scorecard, secrets_store, testrun
from app.events import EventHub
from fakeclaude import FakeClient

CLONE = "https://github.com/me/repo.git"
EVIL = "https://attacker.example/x.git"

needs_git = pytest.mark.skipif(not paths.git_exe(), reason="git is not installed")


def _repo(folder: Path, origin: str | None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    git = paths.git_exe()
    subprocess.run([git, "init", "-b", "main"], cwd=folder, capture_output=True, check=True)
    if origin:
        subprocess.run([git, "remote", "add", "origin", origin], cwd=folder, capture_output=True, check=True)
    (folder / "a.txt").write_text("x", encoding="utf-8")


@pytest.fixture()
def pushes(isolated, monkeypatch):
    """Real git for everything except the network: a push or fetch is recorded, not sent."""
    secrets_store.set_secret("github_token", "ghp_TESTTOKEN")
    monkeypatch.setattr(github, "check", lambda: {"login": "me", "name": "Me", "id": 1, "scopes": ""})
    real, sent = subprocess.run, []

    def run(cmd, *a, **k):
        if any(x in ("push", "fetch") for x in cmd[1:]):
            sent.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", run)
    return sent


# ------------------------------------------------------------------ the destination of a push
@needs_git
def test_push_is_refused_when_origin_is_not_the_recorded_repository(isolated, pushes):
    folder = isolated / "p"
    _repo(folder, EVIL)
    with pytest.raises(github.GitHubError):
        github.push(folder, "v1", CLONE)
    assert pushes == []                       # the token was never sent to the other address


@needs_git
def test_publish_is_refused_when_origin_is_not_the_recorded_repository(isolated, pushes):
    folder = isolated / "p"
    _repo(folder, EVIL)
    with pytest.raises(github.GitHubError):
        github.publish(folder, "v1", CLONE, tag="v1")
    assert pushes == []


@needs_git
def test_push_still_works_when_origin_is_the_recorded_repository(isolated, pushes):
    folder = isolated / "p"
    _repo(folder, CLONE)
    github.push(folder, "v1", CLONE)
    assert any("push" in c for c in pushes)


@needs_git
def test_push_still_works_for_a_folder_without_origin_yet(isolated, pushes):
    folder = isolated / "p"
    _repo(folder, None)
    github.push(folder, "v1", CLONE)
    assert any("push" in c for c in pushes)


def _session(monkeypatch, with_origin=None):
    p = projects.create("Pushy")
    slug = p["slug"]
    projects.update(slug, github_clone=CLONE, github_repo="https://github.com/me/repo")
    if with_origin:
        _repo(projects.project_dir(slug), with_origin)
    eng = engine_mod.Engine(EventHub(projects.history_file), client_factory=lambda o: FakeClient(o, None))
    s = eng.session(slug)
    s.build_options()
    cards = []

    async def ask(kind, payload):
        cards.append(payload)
        return {"approved": False}
    monkeypatch.setattr(s, "ask_user", ask)
    return eng, s, cards


def test_push_card_shows_where_the_files_go(isolated, monkeypatch):
    eng, s, cards = _session(monkeypatch)
    try:
        asyncio.run(s._can_use_tool("mcp__aiteam__github_push", {"message": "v1", "user_asked_now": True},
                                    SimpleNamespace(agent_id=None)))
        assert cards and "github.com/me/repo" in cards[-1]["what"]
    finally:
        eng.shutdown()


@needs_git
def test_push_card_does_not_hide_a_changed_origin(isolated, monkeypatch):
    """Either no card is shown (push refused), or the card names the address the files would really go to."""
    eng, s, cards = _session(monkeypatch, with_origin=EVIL)
    try:
        r = asyncio.run(s._can_use_tool("mcp__aiteam__github_push", {"message": "v1", "user_asked_now": True},
                                        SimpleNamespace(agent_id=None)))
        shown = cards[-1]["what"] if cards else ""
        assert "attacker.example" in shown or (not cards and type(r).__name__ == "PermissionResultDeny")
    finally:
        eng.shutdown()


# ------------------------------------------------------------------ git runs without hooks or config-run programs
@pytest.fixture()
def git_calls(isolated, monkeypatch):
    folder = isolated / "proj"
    (folder / ".git").mkdir(parents=True)
    monkeypatch.setattr(paths, "git_exe", lambda: "git")
    calls = []

    def run(cmd, *a, **k):
        calls.append((list(cmd), k.get("env")))
        out = b"" if not k.get("text") else ""
        return subprocess.CompletedProcess(cmd, 0, out, out)
    monkeypatch.setattr(subprocess, "run", run)
    return folder, calls


def _entry_points(folder):
    return {
        "iterations._git": lambda: iterations._git(folder, "status", "--porcelain"),
        "github._git": lambda: github._git(["status", "--porcelain"], folder),
        "projects._git": lambda: projects._git(["status", "--porcelain"], folder),
        "testrun._changed_files": lambda: testrun._changed_files(folder, None),
        "scorecard._files": lambda: scorecard._files(folder),
    }


@pytest.mark.parametrize("name", ["iterations._git", "github._git", "projects._git", "testrun._changed_files",
                                  "scorecard._files"])
def test_app_git_calls_run_with_hooks_and_fsmonitor_off(git_calls, name):
    folder, calls = git_calls
    _entry_points(folder)[name]()
    git = [c for c, _ in calls if c and str(c[0]).lower().endswith("git")]
    assert git, "no git call was made"
    for cmd in git:
        cfg = [cmd[i + 1] for i, x in enumerate(cmd[:-1]) if x == "-c"]
        assert any(c.lower().startswith("core.hookspath=") for c in cfg), f"hooks not switched off: {cmd}"
        assert "core.fsmonitor=false" in [c.lower() for c in cfg], f"fsmonitor not switched off: {cmd}"


@pytest.mark.parametrize("name", ["iterations._git", "github._git", "projects._git", "testrun._changed_files",
                                  "scorecard._files"])
def test_app_git_calls_ignore_the_system_wide_git_config(git_calls, name):
    folder, calls = git_calls
    _entry_points(folder)[name]()
    for cmd, env in [(c, e) for c, e in calls if c and str(c[0]).lower().endswith("git")]:
        assert env and env.get("GIT_CONFIG_NOSYSTEM") in ("1", "true"), f"system config still read: {cmd}"


# ------------------------------------------------------------------ the address is parsed, not pattern-matched
@pytest.mark.parametrize("url", [
    "https://github.com/Me/Repo.git", "https://github.com/me/repo", "https://github.com/me/repo/",
    "git@github.com:me/repo.git", "ssh://git@github.com/me/repo.git"])
def test_same_repo_accepts_the_real_address_in_any_normal_spelling(url):
    assert github._same_repo(url, CLONE)


@pytest.mark.parametrize("url", [
    "https://evil.example/github.com/me/repo", "http://github.com/me/repo.git",
    "https://user:pw@github.com/me/repo.git", "https://github.com.evil.example/me/repo.git",
    "https://evil.example/x@github.com/me/repo", "https://github.com/me/other.git", "file:///github.com/me/repo",
    "https://github.com:8443/me/repo.git", "https://github.com/me/repo/extra", ""])
def test_same_repo_refuses_lookalike_addresses(url):
    assert not github._same_repo(url, CLONE)
    assert github.repo_full_name(url) in ("", "me/other") or url.endswith("other.git")


@needs_git
def test_push_is_refused_when_only_the_push_address_is_different(isolated, pushes):
    folder = isolated / "p"
    _repo(folder, CLONE)
    subprocess.run([paths.git_exe(), "remote", "set-url", "--push", "origin", EVIL], cwd=folder,
                   capture_output=True, check=True)
    with pytest.raises(github.GitHubError):
        github.push(folder, "v1", CLONE)
    assert pushes == []


@needs_git
def test_push_is_refused_when_origin_has_a_second_push_address(isolated, pushes):
    folder = isolated / "p"
    _repo(folder, CLONE)
    subprocess.run([paths.git_exe(), "remote", "set-url", "--add", "--push", "origin", CLONE], cwd=folder,
                   capture_output=True, check=True)
    subprocess.run([paths.git_exe(), "remote", "set-url", "--add", "--push", "origin", EVIL], cwd=folder,
                   capture_output=True, check=True)
    with pytest.raises(github.GitHubError):
        github.publish(folder, "v1", CLONE)
    assert pushes == []


def test_git_safe_switches_off_programs_started_by_config():
    cfg = " ".join(paths.GIT_SAFE)
    for want in ("core.sshCommand=ssh", "credential.helper=", "core.askpass=", "http.proxy=",
                 "protocol.ext.allow=never"):
        assert want in cfg
