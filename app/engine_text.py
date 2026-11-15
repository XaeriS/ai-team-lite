"""Plain helpers of the engine: how a tool call, an approval or an error is put into words, what starts a
project's app, and recognising what an error from Claude means (L46)."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
import urllib.error
import urllib.request

from . import engine_const as const, iterations, projects


def validate_anthropic_key(key: str) -> tuple[bool, str]:
    """Cheap check (lists models, costs nothing). Returns (ok, message)."""
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=1", headers={
        "x-api-key": key.strip(), "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True, "Key accepted."
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "Anthropic refused this key. Check you copied all of it (it starts with sk-ant-)."
        return False, f"Anthropic answered with error {e.code}. Try again in a minute."
    except urllib.error.URLError as e:
        return False, f"Could not reach Anthropic ({e.reason}). Check your internet connection."


def _short(value: Any, limit: int = 90) -> str:
    s = " ".join(str(value).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _file_label(path: str, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def describe_tool(name: str, inp: dict, root: Path) -> str:
    """One plain-English line for the activity feed."""
    f = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""
    if name == "Read":
        return f"reading {_file_label(f, root)}"
    if name == "Write":
        return f"writing {_file_label(f, root)}"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"editing {_file_label(f, root)}"
    if name in ("Glob", "Grep", "LS"):
        return "looking through the files"
    if name in ("Bash", "PowerShell"):
        return _short(inp.get("description") or f"running: {inp.get('command', '')}")
    if name == "WebSearch":
        return f"searching the web for “{_short(inp.get('query', ''), 60)}”"
    if name == "WebFetch":
        return f"reading {_short(inp.get('url', ''), 60)}"
    if name in const.AGENT_TOOLS:
        return f"briefing the {const.WORKER_LABELS.get(inp.get('subagent_type', ''), 'worker')}"
    if name == "TodoWrite":
        return "updating the to-do list"
    if name in const.QUIET_TOOLS:
        return "getting ready"
    if name == "mcp__aiteam__run_tests":
        return "running the full test suite" if str(inp.get("scope", "")).lower() == "all" else "running the related tests"
    if name.startswith("mcp__aiteam__"):
        return name.removeprefix("mcp__aiteam__").replace("_", " ")
    return name


APPROVAL_TITLES = {
    "Bash": "Run a command", "PowerShell": "Run a command", "Write": "Create or replace a file",
    "Edit": "Change a file", "MultiEdit": "Change a file", "NotebookEdit": "Change a notebook",
    "Agent": "Start a worker agent", "Task": "Start a worker agent",
    "mcp__aiteam__github_create_repo": "Create a GitHub repository",
    "mcp__aiteam__github_push": "Save the project to GitHub",
    "mcp__aiteam__launch_for_user": "Start your app for you to try",
    "mcp__aiteam__take_screenshot": "Start the app to take a screenshot",
}


def approval_card(name: str, inp: dict, ctx: Any, root: Path) -> dict:
    title = APPROVAL_TITLES.get(name) or getattr(ctx, "title", None) or name
    explanation = inp.get("explanation") or inp.get("description") or getattr(ctx, "description", None) or ""
    details = ""
    if name in ("Bash", "PowerShell"):
        details = str(inp.get("command", ""))
    elif name == "Write":
        details = f"{_file_label(inp.get('file_path', ''), root)}\n\n{str(inp.get('content', ''))[:1500]}"
    elif name in ("Edit", "MultiEdit"):
        edits = inp.get("edits") or [inp]
        parts = [f"{_file_label(inp.get('file_path', ''), root)}"]
        for e in edits[:3]:
            parts.append(f"- {_short(e.get('old_string', ''), 300)}\n+ {_short(e.get('new_string', ''), 300)}")
        details = "\n".join(parts)
    elif name in const.AGENT_TOOLS:
        explanation = explanation or inp.get("description", "")
        details = f"{const.WORKER_LABELS.get(inp.get('subagent_type', ''), inp.get('subagent_type', 'worker'))}: " \
                  f"{str(inp.get('prompt', ''))[:1500]}"
    else:
        details = json.dumps({k: v for k, v in inp.items() if k != "explanation"}, indent=2)[:1500]
    return {"title": title, "explanation": explanation, "details": details, "tool": name}


def _tail(path: str, limit: int = 4000) -> str:
    """The end of a background worker's output file (its last words are its report)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = []
    for line in text.splitlines():  # the file may be a JSON transcript: keep the assistant's text
        try:
            row = json.loads(line)
        except ValueError:
            lines.append(line)
            continue
        msg = row.get("message") if isinstance(row, dict) else None
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            c = msg.get("content")
            lines.append(c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict)))
    return "\n".join(x for x in lines if x.strip())[-limit:]


async def _quiet(coro) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001
        pass


def _result_text(block: Any) -> str:
    c = block.content
    return c if isinstance(c, str) else " ".join(x.get("text", "") for x in (c or []) if isinstance(x, dict))


_NOT_A_START = re.compile(r"test|migrat|install|setup|build|clean|deploy|uninstall|lint|format|update", re.I)
_START_FIRST = ("run_app", "run", "start", "launch", "app")


def is_lost_conversation(e: Exception) -> bool:
    """Claude's engine could not find the conversation AI Team Lite asked it to continue (item 74)."""
    s = str(e).lower()
    return "no conversation found" in s or ("session" in s and "not found" in s)


def launch_candidates(slug: str) -> list[str]:
    """Start files in the project folder (run.bat, run_app.bat, start.cmd...), most likely first (item 71)."""
    try:
        folder = projects.project_dir(slug)
        files = [f.name for f in folder.iterdir() if f.is_file() and f.suffix.lower() in (".bat", ".cmd")
                 and not _NOT_A_START.search(f.stem)]
    except OSError:
        return []
    rank = lambda n: (_START_FIRST.index(n.rsplit(".", 1)[0].lower()) if n.rsplit(".", 1)[0].lower() in _START_FIRST
                      else len(_START_FIRST), n.lower())
    return sorted(files, key=rank)


def launch_info(slug: str, p: dict | None = None) -> dict | None:
    """How to start the app (request 21), or None while there is nothing runnable. It shows once something works:
    a finished iteration, a project in review, or an existing folder that already held a working app (item 71)."""
    p = p if p is not None else (projects.load(slug) or {})
    its = iterations.load(slug)
    works = any(it.get("status") == "done" for it in its) or p.get("phase") == "review" or bool(p.get("existing_folder"))
    cands = launch_candidates(slug)
    launch = p.get("launch")  # kept even if its file went missing: Launch then explains and offers a fix
    if not launch and cands:
        launch = {"command": cands[0], "url": ""}
    if not works or not launch:
        return None
    last = next((it for it in reversed(its) if it.get("status") == "done"), None)
    # several start files and the user never chose: ask once which one (the choice is remembered)
    choices = cands if len(cands) > 1 and not p.get("launch_chosen") else []
    return {**launch, "version": f"iteration {last['n']}: {last['name']}" if last else "", "choices": choices,
            "all_choices": cands}


def iterations_note(slug: str) -> str:
    its = iterations.load(slug)
    if not its:
        return "none planned yet"
    return "; ".join(f"{it['n']}. {it['name']} [{it.get('status', 'planned')}{' ' + it['commit'] if it.get('commit') else ''}]"
                     for it in its)


def interrupted_text(answer: dict) -> str:
    return ("The user has not answered this yet. Before that, they asked: \"" + str(answer.get("question", "")) +
            "\". Answer their question first. Then, if it is still needed, ask again (call the same tool again).")


_SIGNIN_LOST = re.compile(r"failed to authenticate|oauth (session|token)[^.]{0,60}(expired|refresh|revoked)|"
                          r"authentication_error|not logged in|please run /login|invalid (x-)?api[ -]key|"
                          r"\bauth login\b", re.I)
WINDOWS_CLOSED = {0x40010004, 0xC000013A}   # Windows ended the program: restart, update, sign-out, Ctrl+Break


def is_signin_lost(text: str) -> bool:
    """L37: Claude refused the sign-in (expired subscription sign-in, refused API key)."""
    return bool(_SIGNIN_LOST.search(text or ""))


def closed_by_windows(e: Exception) -> bool:
    """L37: Claude's engine was ended by Windows (exit code 1073807364 = 0x40010004), not by an error of its own."""
    code = getattr(e, "exit_code", None)
    if code is None:
        m = re.search(r"exit code:? (-?\d+)", str(e))
        code = int(m.group(1)) if m else None
    return code is not None and (code & 0xFFFFFFFF) in WINDOWS_CLOSED


def friendly_error(e: Exception) -> str:
    name = type(e).__name__
    text = str(e)
    if name == "CLINotFoundError":
        return "The Claude engine is missing from the installation. Reinstall AI Team Lite."
    if "Not logged in" in text or "auth login" in text:
        return "Fred is not signed in to your Claude subscription. Open Settings and sign in again."
    if "API key" in text or "401" in text:
        return f"Problem with the Anthropic API key: {text[:300]}"
    return f"Something went wrong ({name}): {text[:500]}"


__all__ = ["APPROVAL_TITLES", "WINDOWS_CLOSED", "_NOT_A_START", "_SIGNIN_LOST", "_START_FIRST", "_file_label", "_quiet", "_result_text", "_short", "_tail", "approval_card", "closed_by_windows", "describe_tool", "friendly_error", "interrupted_text", "is_lost_conversation", "is_signin_lost", "iterations_note", "launch_candidates", "launch_info", "validate_anthropic_key"]
