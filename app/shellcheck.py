"""L65: what a shell command really does to the PC, so approvals are asked only for what is risky for the machine.

The owner (2026-11-17): "I only want to receive a request for approvals when you are doing something risky for my
machine, not when you are working on the project content itself." Measured: 351 cards in 7 days, about 9 in 10
not risky. The old check read the whole command as text, so a test file being written that mentions C:\\Windows
looked like a command touching C:\\Windows.

Here the command is split into what runs and the text it feeds in (here-documents). Text written into a file is
content, not an action. Text fed to an interpreter (python - <<EOF) is code: it only counts when it deletes or
moves things and names a folder outside the safe places.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
INTERPRETER = re.compile(r"(?i)(^|[\s|;&(/\\])(python(3)?(\.exe)?|py(\.exe)?|node(\.exe)?|pwsh|powershell(\.exe)?|"
                         r"bash|sh|ruby|perl)\b(?!\S*\.(py|js|ps1|sh)\b)")
# commands that change files or folders (anything else here only reads)
MUTATING = re.compile(
    r"""(?ix)
    (^|[\s;&|(])(rm|rmdir|del|erase|rd|mv|move|cp|copy|xcopy|robocopy|mkdir|md|touch|ln|chmod|chown|icacls|takeown|
       tee|truncate|unzip|tar|7z|Remove-Item|ri|Move-Item|mi|Copy-Item|cpi|New-Item|ni|Rename-Item|rni|ren|rename|
       Set-Content|sc|Add-Content|ac|Out-File|Expand-Archive|Compress-Archive)\b
    | (?<![<>=!])>{1,2}(?!&|\s*/dev/null|\s*nul\b|\s*\$null)   # redirection into a file
    | \bsed\s+(-[a-z]*i|--in-place) | \bperl\s+-[a-z]*i
    | \bgit\s+(checkout|reset|clean|rm|mv|stash|restore|apply|am|pull|merge|rebase|add|commit|init|tag|config|switch)\b
    | \bgit\s+branch\s+(-[dDmMcC]|--delete|--move|--copy) | --output\b
    """)
DESTRUCTIVE_CODE = re.compile(   # real file operations in a script (not text operations such as str.replace)
    r"(?i)(\bos\.(remove|unlink|rmdir|removedirs|rename|replace|system)\b|\bshutil\.(rmtree|move|copy\w*)\b|"
    r"\.(unlink|rmdir|write_text|write_bytes|mkdir)\(|\bopen\([^)]*['\"][wax]\+?b?['\"]|\bsubprocess\.|"
    r"\b(Remove-Item|Move-Item|Copy-Item|Set-Content|Out-File|New-Item)\b|\b(unlinkSync|rmSync|writeFileSync|renameSync|"
    r"copyFileSync|mkdirSync)\b|^\s*(rm|del|mv|cp|rmdir|rd)\s)")
RUN_PROGRAM = re.compile(r"""(?ix)(?:^|[;&|(]\s*|\bstart\s+(?:""\s+)?|&\s*)\s*["']?((?:[a-z]:[\\/]|/[a-z]/)[^"'\n;&|]*?\.(exe|msi|bat|cmd|ps1|vbs|com|scr))\b""")
RUNS_CODE = re.compile(r"(?i)(^|[\s;&|(\"'])(python3?|py|node|npm|npx|pip3?|pytest|uv|poetry|java|dotnet|cargo|go|sh|bash|"
                       r"pwsh|powershell|cmd|iex|Invoke-Expression)(\.exe)?\b"
                       r"|\.(bat|cmd|exe|ps1)\b")
# reading is never risky for the PC, but private files elsewhere (keys, passwords, browser data) still ask
PRIVATE = re.compile(r"(?i)([\\/]\.(ssh|aws|azure|gnupg|kube|docker)\b|id_(rsa|ed25519|ecdsa)|\.(pem|pfx|p12|kdbx|key)\b|"
                     r"Login Data|Cookies|[\\/](Google[\\/]Chrome|Microsoft[\\/]Edge|Mozilla[\\/]Firefox)[\\/]|"
                     r"secret|password|passwd|credential|token|wallet|[\\/]\.env\b)")
HOME_PATH = re.compile(r"""(?i)(~|\$HOME|%USERPROFILE%|\$env:USERPROFILE)[\\/][^\s"';&|]*""")
PARENT_PATH = re.compile(r"""(?:^|[\s"'=>(])(\.\.[\\/][^\s"';&|]*)""")


@dataclass
class Parts:
    visible: str                                       # what runs (here-document bodies removed)
    file_bodies: list[str] = field(default_factory=list)   # text written into files: content, not actions
    code_bodies: list[str] = field(default_factory=list)   # text fed to an interpreter: code that runs


def split(command: str) -> Parts:
    """Separate the command from the here-document text it feeds in."""
    lines = command.split("\n")
    out, files, code = [], [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        tags = [(m.group(2), line[:m.start()], line[m.end():]) for m in HEREDOC.finditer(line)]
        i += 1
        for tag, before, after in tags:
            body = []
            while i < len(lines) and lines[i].strip() != tag:
                body.append(lines[i])
                i += 1
            i += 1   # the closing tag
            text = "\n".join(body)
            if INTERPRETER.search(before.split("|")[-1]) or re.search(r"\|\s*(python|py|node|pwsh|powershell|bash|sh)\b",
                                                                       after, re.I):
                code.append(text)    # python - <<EOF: code that runs
            else:
                files.append(text)   # cat > f <<EOF, git commit -F - <<EOF...: text, not actions
    return Parts("\n".join(out), files, code)


def safe_roots(project: Path | str, install_dir: Path | str | None = None) -> list[str]:
    """The places where the team may do anything: the project, AI Team Lite's own folder and the temp folder."""
    roots = [str(project)]
    if install_dir:
        roots.append(str(install_dir))
    temps = {tempfile.gettempdir(), os.environ.get("TEMP", ""), os.environ.get("TMP", ""), "/tmp"}
    local = os.environ.get("LOCALAPPDATA")
    if local:
        temps.add(str(Path(local) / "Temp"))
    return roots + sorted(t for t in temps if t)


@dataclass
class Verdict:
    ask: bool
    reason: str
    what: str = ""


def judge(command: str, roots: list[str], *, plan_approved: bool, is_risky, outside_paths) -> Verdict:
    """Decide for one shell command. `is_risky(text)` and `outside_paths(text, roots)` come from policy.py."""
    parts = split(command)
    visible = parts.visible
    if is_risky(visible):
        return Verdict(True, "risky for your PC", "installs software, changes Windows or publishes online")
    for body in parts.code_bodies:
        if is_risky(body):
            return Verdict(True, "risky for your PC", "the script it runs installs software or changes Windows")
    mutating = bool(MUTATING.search(visible)) or bool(parts.code_bodies) or bool(
        re.search(r"(?i)\b(python|py|node)(\.exe)?\s+-c\b", visible))
    private = [p for p in outside_paths(visible, roots) if PRIVATE.search(p)]
    private += [m.group(0) for m in HOME_PATH.finditer(visible) if PRIVATE.search(m.group(0))]
    if private:
        return Verdict(True, "reads private files outside the project", private[0])
    if not plan_approved:   # nothing is built before the plan is approved: changes and running code still ask
        if mutating or RUNS_CODE.search(visible):
            return Verdict(True, "no approved plan yet", "")
        return Verdict(False, "only reads")
    for m in RUN_PROGRAM.finditer(visible):
        prog = m.group(1)
        if outside_paths(f'"{prog}"', roots):
            return Verdict(True, "runs a program from outside the project", prog)
    if mutating:
        outside = outside_paths(visible, roots)
        parent = [p for p in PARENT_PATH.findall(visible)]
        if outside or parent:
            return Verdict(True, "changes files outside the project", (outside + parent)[0])
        for body in parts.code_bodies:
            for line in body.splitlines():   # a folder named in a line that deletes, moves or writes
                if DESTRUCTIVE_CODE.search(line):
                    hit = outside_paths(line, roots)
                    if hit:
                        return Verdict(True, "the script it runs changes files outside the project", hit[0])
    return Verdict(False, "project work" if mutating else "only reads")
