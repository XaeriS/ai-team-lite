"""H-4: reading never asks, but it must not reach the app's secrets or the Claude sign-in; the web screenshot
must not read local files or take a browser flag."""
import subprocess
from pathlib import Path

import pytest

from app import auth, policy, winhelpers


def _decide(tool, inp):
    return policy.decide(tool, inp, kind="project", mode="plan", plan_approved=True,
                         inside_project=lambda p: str(p).replace("\\", "/").startswith("C:/ws/proj"),
                         allowed_roots=["C:/ws/proj"])[0]


def _private_paths(isolated):
    return [str(isolated / "secrets.json"),                                  # AITEAM_SECRETS_FILE (conftest)
            str(Path.home() / ".ai-team-lite" / "secrets.json"),
            str(auth.claude_config_dir() / ".credentials.json"),
            str(auth.claude_config_dir())]


@pytest.mark.parametrize("tool,key", [("Read", "file_path"), ("Glob", "path"), ("Grep", "path")])
def test_policy_does_not_allow_reading_secrets_or_signin(isolated, tool, key):
    for p in _private_paths(isolated):
        assert _decide(tool, {key: p, "pattern": "*"}) != "allow", f"{tool} {p}"


def test_policy_still_allows_reading_project_files(isolated):
    assert _decide("Read", {"file_path": "C:/ws/proj/main.py"}) == "allow"
    assert _decide("Grep", {"path": "C:/ws/proj", "pattern": "x"}) == "allow"


@pytest.fixture()
def browser(monkeypatch):
    calls = []
    monkeypatch.setattr(winhelpers, "find_headless_browser", lambda: "browser.exe")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or subprocess.CompletedProcess(a[0], 0))
    return calls


@pytest.mark.parametrize("target", ["file:///C:/Users/x/.ai-team-lite/secrets.json", "FILE:///C:/Windows/win.ini",
                                    "--renderer-cmd-prefix=calc", "--gpu-launcher=calc", "-x"])
def test_screenshot_url_rejects_file_urls_and_flags(browser, isolated, target):
    with pytest.raises(Exception):
        winhelpers.screenshot_url(target, isolated / "o.png")
    assert browser == [], "the browser was started with a dangerous target"


def test_screenshot_url_still_takes_a_local_web_page(browser, isolated):
    try:
        winhelpers.screenshot_url("http://127.0.0.1:8000/", isolated / "o.png")
    except RuntimeError:
        pass   # the fake browser makes no picture; only the start matters
    assert browser and browser[0][-1] == "http://127.0.0.1:8000/"


@pytest.mark.parametrize("target", ["ftp://x/y", "//evil/share/x", "C:/Windows/win.ini", "about:blank", "chrome://settings",
                                    "http://a/\tfile:///C:/x", "http://a/\n--gpu-launcher=calc", "https://a/\x00b", " \tfile:///x"])
def test_screenshot_url_accepts_only_clean_http_addresses(browser, isolated, target):
    with pytest.raises(Exception):
        winhelpers.screenshot_url(target, isolated / "o.png")
    assert browser == []


def test_policy_resolves_dots_before_checking_private_paths(isolated):
    base = str(auth.claude_config_dir())
    for p in (base + "/x/../.credentials.json", base + "/./.credentials.json", base.replace("\\", "/") + "//.credentials.json",
              str(Path.home() / ".ai-team-lite" / "sub" / ".." / "secrets.json")):
        assert _decide("Read", {"file_path": p}) != "allow", p
    assert _decide("Glob", {"pattern": base.replace("\\", "/") + "/**"}) != "allow"
    assert _decide("Grep", {"pattern": "x", "path": str(Path.home() / ".ai-team-lite" / "." / "secrets.json")}) != "allow"


def test_fetch_and_web_screenshot_with_a_key_in_the_address_ask(isolated):
    from app import secrets_store
    secrets_store.set_secret("anthropic_api_key", "sk-ant-api03-ABCDEFGHIJKLMNOP")
    assert _decide("WebFetch", {"url": "http://x/?k=sk-ant-api03-ABCDEFGHIJKLMNOP"}) != "allow"
    assert _decide("WebFetch", {"url": "http://x/?k=sk-ant-api03"}) != "allow"          # first 12 characters
    assert _decide("mcp__aiteam__take_screenshot", {"kind": "web", "target": "http://x/?k=sk-ant-api03-ABC"}) != "allow"
    assert _decide("mcp__aiteam__take_screenshot", {"kind": "web", "target": "http://127.0.0.1:8000/"}) == "allow"
