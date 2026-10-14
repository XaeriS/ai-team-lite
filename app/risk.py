"""Plain-English "what this does" and "risks" for every approval card (bug #7).

This is the app's own check, so a risk level is always shown even if Fred forgets to explain. Fred's own
explanation and (optionally) a free brain's explanation are shown next to it.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import policy

HIGH, MEDIUM, LOW = "high", "medium", "low"

# (pattern, what it does, risk, why)
COMMANDS = [
    (r"\b(Remove-Item|rm|del|rmdir|rd)\b", "Deletes files or folders", HIGH, "deleted files cannot be recovered"),
    (r"\bgit\s+push\b", "Uploads the project to GitHub", MEDIUM, "the code becomes visible on GitHub (private repos stay private)"),
    (r"\bgit\s+(reset|clean|checkout\s+--)\b", "Throws away changes in the project", HIGH, "uncommitted work can be lost"),
    (r"\bgit\s+commit\b", "Saves a snapshot of the project in its history (Git)", LOW, "safe: it can be undone"),
    (r"\bgit\s+(init|add|status|log|diff)\b", "Prepares or inspects the project's history (Git)", LOW, "read-only or easily undone"),
    (r"\b(winget|choco|scoop|msiexec)\b", "Installs software on this PC", HIGH, "changes the whole PC, not just the project"),
    (r"\bnpm\s+(i|install)\s+(-g|--global)\b", "Installs a JavaScript tool for the whole PC", HIGH, "changes the whole PC"),
    (r"\bpip\s+install\b|-m\s+pip\s+install\b", "Installs Python packages", MEDIUM, "downloads code from the internet"),
    (r"\b-m\s+venv\b", "Creates the project's private Python environment (.venv)", LOW, "only inside the project"),
    (r"\b(pytest|unittest)\b", "Runs the automatic tests", LOW, "only reads and runs the project's code"),
    (r"\b(curl|wget|iwr|Invoke-WebRequest|irm|Invoke-RestMethod)\b", "Downloads something from the internet", MEDIUM, "content from outside the PC"),
    (r"\b(reg(\.exe)?\s+(add|delete)|Set-ItemProperty|New-ItemProperty|setx)\b", "Changes Windows settings", HIGH, "affects the whole PC"),
    (r"\bSet-ExecutionPolicy\b", "Changes Windows' script security setting", HIGH, "weakens a security protection"),
    (r"\b(shutdown|Restart-Computer|Stop-Computer)\b", "Restarts or shuts down the PC", HIGH, "unsaved work in other programs is lost"),
    (r"\b(Stop-Process|taskkill|kill)\b", "Stops a running program", MEDIUM, "unsaved work in that program is lost"),
    (r"\b(mkdir|New-Item|md)\b", "Creates a folder or file", LOW, "only adds something"),
    (r"\b(Copy-Item|cp|copy|Move-Item|mv|move|Rename-Item|ren)\b", "Copies, moves or renames files", MEDIUM, "files may end up in a different place"),
    (r"(\.bat|\.cmd|\.exe|python(\.exe)?|py)\b", "Runs a program", MEDIUM, "it runs with your user rights"),
    (r"\b(dir|ls|Get-ChildItem|type|cat|Get-Content|echo|Write-Output)\b", "Lists or shows files", LOW, "read-only"),
]


GENERIC = {"Runs a program", "Lists or shows files"}


def _rel(path: str, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def explain(tool: str, inp: dict, root: Path, inside_project) -> dict:
    """Return {"what": str, "risk": low|medium|high, "risk_reason": str}."""
    if tool in ("Bash", "PowerShell"):
        cmd = str(inp.get("command", ""))
        whats, risk, reasons = [], LOW, []
        order = {LOW: 0, MEDIUM: 1, HIGH: 2}
        for pattern, what, r, why in COMMANDS:
            if what in GENERIC and whats:  # "runs a program" adds nothing once something specific matched
                continue
            if re.search(pattern, cmd, re.I) and what not in whats:
                whats.append(what)
                reasons.append(why)
                risk = r if order[r] > order[risk] else risk
        if policy.is_risky(cmd):
            risk = HIGH if risk != HIGH else risk
        outside = policy._abs_paths_outside(cmd, [str(root)])
        if outside:
            risk = HIGH
            reasons.insert(0, f"it touches files outside the project ({outside[0]})")
        if not whats:
            whats, reasons = ["Runs a command"], ["the app could not recognise this command: read it carefully"]
            risk = MEDIUM if risk == LOW else risk
        why = "; ".join(reasons[:2])
        return {"what": "; ".join(whats[:3]) + ".", "risk": risk, "risk_reason": why[:1].upper() + why[1:] + "."}
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        rel = _rel(path, root)
        inside = inside_project(path)
        exists = Path(path).exists()
        if not inside:
            return {"what": f"{'Changes' if exists else 'Creates'} {path} (outside the project folder).", "risk": HIGH,
                    "risk_reason": "It changes a file that does not belong to this project."}
        if tool == "Write" and exists:
            lines = sum(1 for _ in open(path, encoding="utf-8", errors="ignore")) if Path(path).is_file() else 0
            return {"what": f"Replaces the whole file {rel} ({lines} lines today) with a new version.", "risk": MEDIUM,
                    "risk_reason": "The old content is overwritten (recoverable from Git if the project is saved there)."}
        if tool == "Write":
            return {"what": f"Creates a new file {rel}.", "risk": LOW, "risk_reason": "It only adds a file inside the project."}
        return {"what": f"Edits part of {rel}.", "risk": LOW, "risk_reason": "A small change inside the project; easy to undo."}
    if tool in ("Agent", "Task"):
        return {"what": "Starts a worker agent with the brief shown below.", "risk": LOW,
                "risk_reason": "The worker's own actions still follow the approval rules."}
    if tool == "mcp__aiteam__github_create_repo":
        private = inp.get("private", True)
        return {"what": f"Creates a {'private' if private else 'PUBLIC'} repository on your GitHub account.",
                "risk": LOW if private else MEDIUM,
                "risk_reason": "Private: only you can see it." if private else "Public: anyone on the internet can see the code."}
    if tool == "mcp__aiteam__github_push":
        return {"what": "Uploads the current project files to its GitHub repository.", "risk": MEDIUM,
                "risk_reason": "Make sure no passwords or private data are in the files."}
    if tool == "mcp__aiteam__launch_for_user":
        return {"what": f"Starts your app ({inp.get('command', '')}) so you can try it.", "risk": LOW,
                "risk_reason": "It runs the project's own program with your user rights."}
    if tool == "mcp__aiteam__take_screenshot":
        return {"what": "Starts the app briefly to take a picture of it, then closes it.", "risk": LOW,
                "risk_reason": "Runs the project's own program for a few seconds."}
    return {"what": f"Uses the tool '{tool}'.", "risk": MEDIUM, "risk_reason": "The app does not know this action; check the details."}
