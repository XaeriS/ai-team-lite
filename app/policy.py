"""Which agent actions run on their own and which need your approval.

Rules (in order):
1. Reading and searching never asks, wherever it looks (L65).
2. Setup conversation, or approval mode "strict": everything else asks.
3. Writing documents (*.md) inside the project never asks: that is Fred keeping the requirements and plan.
4. Before you approve a plan, any change asks (nothing is built before the plan is approved).
5. After you approve a plan (mode "plan"), the team works without asking inside the safe places: the project
   folder, the Windows temp folder and AI Team Lite's own folder (L63, L65). Only what is risky for the PC asks:
   installing software or new packages, changing Windows (settings, registry, services, scheduled tasks,
   firewall, Defender), changing files elsewhere on the PC, running a program from elsewhere, running as
   administrator, and publishing to GitHub. See app/shellcheck.py.
"""
from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote

from . import shellcheck

ALWAYS_ALLOW = {
    "Read", "Glob", "Grep", "LS", "TodoWrite", "TodoRead", "NotebookRead", "WebSearch", "WebFetch",
    "BashOutput", "ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool",
    "mcp__aiteam__check_environment", "mcp__aiteam__request_secret", "mcp__aiteam__ask_folder",
    "mcp__aiteam__save_setting", "mcp__aiteam__request_plan_approval", "mcp__aiteam__github_check",
    "mcp__aiteam__finish_setup", "mcp__aiteam__show_image", "mcp__aiteam__ask_choice",
    "mcp__aiteam__record_test_result",
    "mcp__aiteam__start_iteration", "mcp__aiteam__commit_iteration",  # they check the approved plan themselves
    "mcp__aiteam__handoff_to_architect", "mcp__aiteam__request_architecture_approval", "mcp__aiteam__return_to_fred",
    "mcp__aiteam__complete_task", "mcp__aiteam__record_red_tests", "mcp__aiteam__record_prior_art",
    "mcp__aiteam__ask_council", "mcp__aiteam__record_change_request",
    "mcp__aiteam__run_security_test",
    "mcp__aiteam__record_diagram", "mcp__aiteam__record_problem", "mcp__aiteam__record_challenge_review",
    "mcp__aiteam__record_questions", "mcp__aiteam__connect_phone", "mcp__aiteam__propose_ideas",
    "mcp__aiteam__run_tests", "mcp__aiteam__record_lesson", "mcp__aiteam__quality_check",   # run_tests checks itself that the command only runs the project's tests
    "mcp__aiteam__record_finding", "mcp__aiteam__close_finding", "mcp__aiteam__clean_leftovers",   # L61: bookkeeping
}
# helpers that only read, search the web or draw diagrams: they may start before a plan is approved
EARLY_WORKERS = {"scout", "challenger", "diagrammer"}
AFTER_PLAN = {"mcp__aiteam__take_screenshot", "Agent", "Task", "KillShell",
              "KillBash", "TaskStop", "mcp__aiteam__launch_for_user",   # L65: starting the project is project work
              "mcp__aiteam__package_app", "mcp__aiteam__add_ci"}       # L52b-c: inside the project
ALWAYS_ASK = {"mcp__aiteam__github_create_repo", "mcp__aiteam__github_push", "mcp__aiteam__github_release"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

RISKY = re.compile(
    r"""(?ix)
    \b(winget|choco|scoop|msiexec|apt(-get)?|brew|sudo|runas|reg(\.exe)?\s+(add|delete)|setx|
       Set-ExecutionPolicy|Set-ItemProperty|New-ItemProperty|Remove-ItemProperty|shutdown|Restart-Computer|
       Stop-Computer|format(\.com)?\s|diskpart|bcdedit|schtasks|sc(\.exe)?\s+(create|delete|config)|
       Start-Process\s+.*-Verb\s+RunAs|Invoke-Expression|iex\b|
       netsh|New-NetFirewallRule|Set-NetFirewall\w*|Add-MpPreference|Set-MpPreference|Set-Service|New-Service|
       Register-ScheduledTask|Enable-WindowsOptionalFeature|Disable-WindowsOptionalFeature|dism(\.exe)?\s)
    | git\s+push | git\s+remote\s+(add|set-url) | gh\s+repo
    | npm\s+(i|install)\s+(-g|--global) | pip\s+install\s+.*--user
    | (curl|wget|iwr|Invoke-WebRequest|irm|Invoke-RestMethod)\b.*\|\s*(sh|bash|iex|powershell)
    | rm\s+-[a-z]*r[a-z]*f?\s+(/(?!tmp\b)|~|\.\.|[a-z]:) | Remove-Item\b.*-Recurse\b.*([a-z]:\\|\.\.|~|\$env:(?!TE?MP))
    | del\s+/[sq] | rmdir\s+/s | rd\s+/s
    """
)
# L65: a new package runs its own code on the PC when installed, so it asks; the packages already listed by the
# project (pip install -r requirements.txt, npm install / npm ci), the project itself (-e .) and pip itself do not.
PIP_INSTALL = re.compile(r"\bpip(3)?(\.exe)?\s+install\b(?!(\s+(-q|--quiet|-U|--upgrade|--no-input|--disable-pip-version-check))*"
                         r"\s+(-r\b|--requirement\b|-e\s+\.(\s|$)|\.(\s|$)|pip(\s|$)))", re.I)
NPM_INSTALL = re.compile(r"\bnpm\s+(i|install|add)\s+(?!-)[@\w]", re.I)


def is_risky(command: str) -> bool:
    """Risky for the PC: installing software or new packages, changing Windows, publishing."""
    return bool(RISKY.search(command) or PIP_INSTALL.search(command) or NPM_INSTALL.search(command))


ABS_PATH = re.compile(r"""(?i)(?<![\w/])([a-z]:[\\/][^\s"'|;&]*|/[a-z]/[^\s"'|;&]*)""")


QUOTED_PATH = re.compile(r"""(?i)(["'])((?:[a-z]:[\\/]|/[a-z]/)[^"']*)\1""")


def _abs_paths_outside(command: str, allowed_roots: list[str]) -> list[str]:
    roots = [r.replace("\\", "/").lower().rstrip("/") for r in allowed_roots if r]
    found = [m.group(2) for m in QUOTED_PATH.finditer(command)]
    rest = QUOTED_PATH.sub(" ", command)
    found += [m.group(1) for m in ABS_PATH.finditer(rest)]
    out = []
    base = roots[0] if roots else ""
    for tok in re.split(r"""[\s"'=]+""", rest):   # relative parts that climb out of the project folder ('..\x')
        t = tok.replace("\\", "/")
        if base and not re.match(r"(?i)^([a-z]:)?/", t) and "://" not in t and ".." in t.split("/"):
            full = posixpath.normpath(posixpath.join(base, t))
            if not any(full == r or full.startswith(r + "/") for r in roots):
                out.append(tok)
    for raw in found:
        p = raw.replace("\\", "/").lower()
        if re.match(r"^/[a-z]/", p):  # git-bash style /c/Users -> c:/users
            p = p[1] + ":" + p[2:]
        if not any(p == r or p.startswith(r + "/") for r in roots):
            out.append(raw)
    return out


# Read-only commands that may run without asking (e.g. to understand an existing folder). One command only:
# no chaining, pipes, redirection or sub-commands.
READONLY = re.compile(
    r"""(?ix)^\s*(
      git\s+(status|log|show|diff|shortlog|ls-files|describe|rev-parse|blame)\b(?!.*--output)(?!.*\s-o\b)
    | git\s+branch(\s+(-a|-r|-v|-vv|--list|--all))*\s*$
    | git\s+remote\s+-v\s*$
    | (dir|ls|tree|pwd|Get-ChildItem|gci|Get-Location|type|cat|Get-Content|gc|head|tail|wc|Measure-Object|
       Select-String|sls|Get-Item|Test-Path|Resolve-Path)\b
    )""")
CHAINING = re.compile(r"[;&|<>`]|\$\(|\$\{|\n")


_PY = r"""(?:"[^"\n]*[\\/]|'[^'\n]*[\\/]|(?:[\w.\-:]+[\\/])*)?(?:python3?|py)(?:\.exe)?["']?"""
TEST_RUNNER = re.compile(
    r"(?i)^\s*(?:" + _PY + r"\s+(?:-3(?:\.\d+)?\s+)?-m\s+(?:pytest|unittest)"
    r"|(?:(?:[\w.\-]+[\\/])*)pytest(?:\.exe)?"
    r"|(?:npm|yarn|pnpm)\s+(?:run\s+)?test)(?:\s+[^\s].*)?\s*$")
TEST_META = re.compile(r"""[;&|<>`\r\n]|\$\(|\$\{|%|(?:^|[\s"'=\\/])\.\.(?:[\\/\s"']|$)""")   # also '..' (leaving the project)


def is_test_command(command: str) -> bool:
    """Only a plain test-runner call (python -m pytest/unittest, pytest, npm/yarn/pnpm test): one command, no chaining."""
    c = command or ""
    return bool(c.strip()) and not TEST_META.search(c) and bool(TEST_RUNNER.match(c)) and not is_risky(c)


SAFE_START = re.compile(r"""(?ix)^\s*(?:\.?[\\/])?(?:run\.bat|run\.cmd|main\.py|app\.py|(?:\.venv[\\/]Scripts[\\/])?(?:python|pythonw|py)(?:\.exe)?\s+[\w./\\-]+\.py|npm\s+(?:run\s+)?start)(?:\s+[\w./\\=:-]+)*\s*$""")


def is_plain_app_start(command: str) -> bool:
    """A single clearly-the-project's-own start command (run.bat, python main.py, npm start)."""
    c = command or ""
    return bool(c.strip()) and not TEST_META.search(c) and bool(SAFE_START.match(c)) and not is_risky(c)


def is_readonly(command: str) -> bool:
    return bool(command.strip()) and not CHAINING.search(command) and bool(READONLY.match(command))


READ_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead"}


def _norm(p) -> str:
    """One spelling of a path: '..', '.', '//' and links resolved, so they cannot hide a private place."""
    raw = os.path.expandvars(str(p))
    try:
        raw = str(Path(raw).resolve())
    except (OSError, ValueError, RuntimeError):
        raw = os.path.normpath(raw)
    return raw.replace("\\", "/").rstrip("/").lower()


def _private_paths() -> list[str]:
    """H-4: what reading must never reach on its own: the saved-keys file (plain-text fallback) and Claude's sign-in."""
    from . import auth, secrets_store
    out = [auth.claude_config_dir(), Path.home() / ".ai-team-lite" / "secrets.json"]
    f = secrets_store._fallback_file()
    if f:
        out.append(f)
    return [_norm(p) for p in out]


def reads_private(tool: str, tool_input: dict) -> bool:
    if tool not in READ_TOOLS:
        return False
    private = _private_paths()
    for k in ("file_path", "path", "notebook_path"):
        v = tool_input.get(k)
        if v:
            n = _norm(v)
            if _hits(n, private):
                return True   # the file, a folder below it, or a folder that contains it
    pat = str(tool_input.get("pattern") or "")
    if pat and re.match(r"(?i)^([a-z]:)?[\\/]", pat):   # an absolute search pattern: the part before any wildcard
        head = re.split(r"[*?\[{]", pat, 1)[0]
        if head and _hits(_norm(head), private):
            return True
    if tool == "Grep" and pat:
        flat = pat.replace("\\\\", "/").replace("\\", "/").lower()
        if any(r in flat for r in private):
            return True
    return False


def _hits(n: str, private: list[str]) -> bool:
    return any(n == r or n.startswith(r + "/") or r.startswith(n + "/") for r in private)


def fetch_leaks_key(tool: str, tool_input: dict) -> bool:
    """H-4: a web address that contains one of the saved keys would send it to a stranger."""
    if tool == "WebFetch":
        url = str(tool_input.get("url") or "")
    elif tool == "mcp__aiteam__take_screenshot" and str(tool_input.get("kind") or "") == "web":
        url = str(tool_input.get("target") or "")
    else:
        return False
    from . import secrets_store
    seen = url + " " + unquote(url)
    for name in ("anthropic_api_key", "github_token"):
        key = secrets_store.get_secret(name)
        if key and len(key) >= 8 and (key in seen or key[:12] in seen):
            return True
    return False


def decide(tool: str, tool_input: dict, *, kind: str, mode: str, plan_approved: bool,
           inside_project, allowed_roots: list[str]) -> tuple[str, str]:
    """Return ("allow" | "ask", reason). `inside_project(path)` tells if a path is in the project."""
    if reads_private(tool, tool_input):
        return "ask", "this reads the app's saved keys or the Claude sign-in"
    if fetch_leaks_key(tool, tool_input):
        return "ask", "the web address contains a saved key"
    if tool in ALWAYS_ALLOW:
        return "allow", "safe"
    if tool in ALWAYS_ASK:
        return "ask", "always asks"
    if kind == "setup" or mode == "strict":
        return "ask", "approval needed for every action"
    if tool in WRITE_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if path and not inside_project(path) and not _abs_paths_outside(f'"{path}"', allowed_roots):
            return "allow", "a safe place (temp or AI Team Lite's folder)"   # L63
        if not path or not inside_project(path):
            return "ask", "outside the project folder"
        if "/.archify/" in path.replace("\\", "/"):
            return "allow", "diagram file"   # the diagram's own description, inside the project (item 78)
        if path.lower().endswith(".md"):
            return "allow", "project document"
        return ("allow", "covered by the approved plan") if plan_approved else ("ask", "no approved plan yet")
    if tool in ("Agent", "Task") and str(tool_input.get("subagent_type") or "").lower() in EARLY_WORKERS:
        return "allow", "read-only helper"
    if tool == "mcp__aiteam__take_screenshot" and str(tool_input.get("kind") or "") != "web":
        tgt = str(tool_input.get("target") or "")   # H-1: starts a program: only the project's own plain start command
        if plan_approved and is_plain_app_start(tgt) and not _abs_paths_outside(tgt, allowed_roots):
            return "allow", "starting the project's own app"
        return "ask", "starts a program that is not clearly the project's app"
    if tool in AFTER_PLAN:
        return ("allow", "covered by the approved plan") if plan_approved else ("ask", "no approved plan yet")
    if tool in SHELL_TOOLS:
        cmd = str(tool_input.get("command") or "")
        if "archify.mjs" in cmd and not CHAINING.search(cmd) and not _abs_paths_outside(cmd, allowed_roots):
            return "allow", "drawing a diagram"  # the bundled diagram program, writing inside the project (item 78)
        v = shellcheck.judge(cmd, allowed_roots, plan_approved=plan_approved, is_risky=is_risky,
                             outside_paths=_abs_paths_outside)
        return ("ask", v.reason) if v.ask else ("allow", v.reason)
    return "ask", "unknown action"
