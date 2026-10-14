"""How Fred connects to Claude: with an Anthropic API key, or with the user's own Claude subscription.

Subscription sign-in is done entirely by Claude Code's own `claude auth login` (Anthropic's browser flow).
AI Team Lite never sees or stores the login: Claude Code keeps it in its own config folder.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import paths, secrets_store, settings

NO_WINDOW = 0x08000000 if paths.IS_WINDOWS else 0
NEW_CONSOLE = 0x00000010 if paths.IS_WINDOWS else 0


def cli_path() -> Path | None:
    try:
        import claude_agent_sdk
    except Exception:
        return None
    exe = Path(claude_agent_sdk.__file__).parent / "_bundled" / ("claude.exe" if paths.IS_WINDOWS else "claude")
    return exe if exe.exists() else None


def claude_config_dir() -> Path:
    d = paths.data_dir() / "claude"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cli_env(mode: str) -> dict[str, str]:
    """Environment for Claude Code. In subscription mode any API key (even one set globally on the PC) is
    blanked so Claude Code uses the subscription sign-in."""
    env = {"PATH": paths.tool_path(), "CLAUDE_CONFIG_DIR": str(claude_config_dir()),
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "PYTHONIOENCODING": "utf-8"}
    if mode == "subscription":
        env["ANTHROPIC_API_KEY"] = ""
        env["ANTHROPIC_AUTH_TOKEN"] = ""
    else:
        key = secrets_store.get_secret("anthropic_api_key")
        if key:
            env["ANTHROPIC_API_KEY"] = key
    return env


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess | None:
    exe = cli_path()
    if not exe:
        return None
    env = {**os.environ, **cli_env("subscription")}
    try:
        return subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=timeout, env=env,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None


def subscription_status() -> dict:
    """{'signed_in': bool, 'method': str}. Uses `claude auth status --json`."""
    r = _run(["auth", "status", "--json"])
    if r is None:
        return {"signed_in": False, "method": "", "error": "Claude engine not found"}
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        return {"signed_in": False, "method": "", "error": (r.stderr or r.stdout)[-300:]}
    method = str(data.get("authMethod") or "")
    return {"signed_in": bool(data.get("loggedIn")) and method not in ("api_key", "apiKey"), "method": method}


def start_subscription_login() -> int:
    """Open Claude Code's own sign-in in a visible window (it opens the browser). Returns the process id."""
    exe = cli_path()
    if not exe:
        raise RuntimeError("The Claude engine is missing from the installation. Reinstall AI Team Lite.")
    env = {**os.environ, **cli_env("subscription")}
    proc = subprocess.Popen([str(exe), "auth", "login", "--claudeai"], env=env, creationflags=NEW_CONSOLE,
                            stdin=None if paths.IS_WINDOWS else subprocess.DEVNULL)
    return proc.pid


def subscription_logout() -> bool:
    r = _run(["auth", "logout"])
    return bool(r and r.returncode == 0)


def mode() -> str:
    return settings.load().get("auth_mode") or "api_key"


def ready() -> bool:
    """Can Fred think right now?"""
    if mode() == "subscription":
        return bool(settings.load().get("subscription_signed_in"))
    return secrets_store.has("anthropic_api_key")
