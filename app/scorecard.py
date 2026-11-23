"""L49: the quality scorecard of a version, in plain words.

Before a version is saved the user sees more than "the tests pass": each requirement proven by a test, the code
check, the packages (fixed versions, known vulnerabilities), no secrets in the files, the review findings and the
documents. Each check gives a level: good, attention, problem, or unknown (could not be checked).
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

from . import iterations, paths, projects, safefile, settings, testrun, tools_common, tracking, upkeep

LEVELS = ("good", "unknown", "attention", "problem")   # from best to worst
OSV_URL = "https://api.osv.dev/v1/querybatch"
LONG_FILE = 500       # lines: a code file longer than this is worth splitting (L44, L46)
MAX_FILES = 2000
MAX_BYTES = 1_000_000
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".exe", ".dll", ".pyd", ".so", ".zip", ".pdf", ".db",
          ".sqlite", ".pyc", ".whl", ".gz", ".7z", ".xlsx", ".docx", ".pptx", ".mp3", ".mp4", ".wav", ".onnx", ".bin"}
Fetch = Callable[[str, dict], dict]


def _check(key: str, title: str, level: str, summary: str, details: list[str] | None = None) -> dict:
    return {"key": key, "title": title, "level": level, "summary": summary, "details": (details or [])[:30]}


def _worst(levels: list[str]) -> str:
    worst = max((LEVELS.index(lv) for lv in levels), default=0)
    return "attention" if LEVELS[worst] == "unknown" else LEVELS[worst]


def _plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _files(folder: Path) -> list[Path]:
    """The project's files that would be saved: Git's view when there is a repository (ignored files such as .env
    are never saved), else every file outside the usual tool folders."""
    exe = paths.git_exe()
    if exe and (folder / ".git").exists():
        try:
            r = subprocess.run([exe, *paths.GIT_SAFE, "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=str(folder),
                               capture_output=True, timeout=60, creationflags=testrun.NO_WINDOW,
                               env=paths.git_env())
            if r.returncode == 0:
                names = [n for n in r.stdout.decode("utf-8", "replace").split("\0") if n]
                out = [folder / n for n in names if not (testrun.SKIP_DIRS & set(Path(n).parts))]
                return [f for f in out if f.is_file()][:MAX_FILES]
        except (OSError, subprocess.SubprocessError):
            pass
    out = []
    for f in folder.rglob("*"):
        rel = f.relative_to(folder)
        if testrun.SKIP_DIRS & set(rel.parts) or f.name == ".env" or not f.is_file():
            continue
        out.append(f)
        if len(out) >= MAX_FILES:
            break
    return out


def _rel(f: Path, folder: Path) -> str:
    return f.relative_to(folder).as_posix()


# ---------------------------------------------------------------------------- the checks
def check_requirements(slug: str) -> dict:
    rows = [r for r in tracking.traceability(slug) if r["status"] != "dropped"]
    title = "Every requirement proven by a test"
    if not rows:
        return _check("requirements", title, "unknown", "No numbered requirements (REQ-1, REQ-2...) were found yet.")
    built = [r for r in rows if r["status"] in ("built", "tested")]
    unproven = [r for r in built if not r["proven"]]
    waiting = [r for r in rows if r["status"] not in ("built", "tested")]
    details = [f"{r['id']} is built but no passing test proves it yet: {r['text']}" for r in unproven]
    details += [f"{r['id']} is not built yet ({r['status']}): {r['text']}" for r in waiting]
    proven = len(built) - len(unproven)
    summary = f"{proven} of {_plural(len(built), 'built requirement')} proven by a test"
    if unproven:
        summary += "; not yet: " + ", ".join(r["id"] for r in unproven[:8])
    if waiting:
        summary += f" ({len(waiting)} not built yet)"
    if not built:
        return _check("requirements", title, "unknown", "Nothing is built yet, so nothing can be proven yet.", details)
    return _check("requirements", title, "attention" if unproven else "good", summary + ".", details)


def check_tests(slug: str, folder: Path) -> dict:
    title = "Tests"
    latest: dict[str, dict] = {}
    for t in tools_common.load_tests(folder):
        if t.get("status") in ("passed", "failed"):
            latest[t.get("name") or ""] = t
    failing = [n for n, t in latest.items() if t["status"] == "failed"]
    runs = [it for it in iterations.load(slug) if it.get("last_test")]
    last = max(runs, key=lambda it: it["last_test"].get("time", 0)) if runs else None
    details = [f"Failing: {n or 'a test without a name'}" for n in failing]
    if last and not last["last_test"].get("ok"):
        details.insert(0, f"The last full test run (iteration {last['n']}) failed.")
    if not latest and not last:
        return _check("tests", title, "attention", "No test results were recorded yet.")
    summary = f"{_plural(len(latest) - len(failing), 'test')} passing, {len(failing)} failing"
    if last:
        summary += "; last full run " + ("passed" if last["last_test"].get("ok") else "FAILED")
    level = "problem" if failing else ("attention" if last and not last["last_test"].get("ok") else "good")
    return _check("tests", title, level, summary + ".", details)


class _Collect:
    """A pyflakes reporter that keeps the messages instead of printing them."""

    def __init__(self, folder: Path):
        self.folder, self.syntax, self.flakes = folder, [], []

    def _name(self, filename: str) -> str:
        try:
            return Path(filename).relative_to(self.folder).as_posix()
        except ValueError:
            return Path(filename).name

    def unexpectedError(self, filename, msg):   # noqa: N802 (pyflakes' own method names)
        self.syntax.append(f"{self._name(filename)}: could not be read ({msg})")

    def syntaxError(self, filename, msg, lineno, offset, text):   # noqa: N802
        self.syntax.append(f"{self._name(filename)} line {lineno}: {msg}")

    def flake(self, message):
        self.flakes.append(f"{self._name(message.filename)} line {message.lineno}: "
                           f"{message.message % message.message_args}")


def check_code(folder: Path, files: list[Path]) -> dict:
    title = "Code check"
    py = [f for f in files if f.suffix == ".py"]
    if not py:
        return _check("code", title, "unknown", "No Python files to check yet.")
    try:
        from pyflakes import api as flakes_api
    except ImportError:
        return _check("code", title, "unknown", "The code checker is not installed with this version of AI Team Lite.")
    rep = _Collect(folder)
    long_files = []
    for f in py:
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            rep.unexpectedError(str(f), e)
            continue
        flakes_api.check(src, str(f), rep)
        lines = len(src.splitlines())
        if lines > LONG_FILE:
            long_files.append(f"{_rel(f, folder)} has {lines} lines (easier to maintain when split, below {LONG_FILE})")
    details = rep.syntax + rep.flakes[:20] + long_files[:10]
    if rep.syntax:
        return _check("code", title, "problem", f"{_plural(len(rep.syntax), 'file')} cannot be read by Python "
                      "(a syntax error): the app would stop there.", details)
    if rep.flakes:
        return _check("code", title, "attention", f"{_plural(len(rep.flakes), 'small issue')} in the code (unused "
                      "imports or names, names used but never defined).", details)
    return _check("code", title, "good", f"{_plural(len(py), 'Python file')} checked: no issues"
                  + (f"; {_plural(len(long_files), 'long file')}" if long_files else "") + ".", details)


_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;#]*)")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_requirements(folder: Path) -> list[dict]:
    """[{name, spec, pinned, version}] from requirements.txt (options, -r and -e lines are skipped)."""
    f = folder / "requirements.txt"
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.split(" #")[0].strip()
        if not line or line.startswith(("#", "-", "git+", "http")):
            continue
        m = _REQ.match(line)
        if not m:
            continue
        spec = m.group(3).strip().replace(" ", "")
        exact = re.fullmatch(r"===?([A-Za-z0-9.+!_-]+)", spec)
        pinned = bool(exact) and "*" not in spec
        out.append({"name": m.group(1), "spec": spec, "pinned": pinned, "version": exact.group(1) if exact else ""})
    return out


def installed_versions(folder: Path) -> dict[str, str]:
    """Package versions installed in the project's .venv (normalized name -> version)."""
    venv = folder / ".venv"
    sites = [venv / "Lib" / "site-packages"] + sorted((venv / "lib").glob("python*/site-packages"))
    out: dict[str, str] = {}
    for site in sites:
        if not site.is_dir():
            continue
        for meta in site.glob("*.dist-info/METADATA"):
            name = version = ""
            try:
                with meta.open(encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.startswith("Name:"):
                            name = line[5:].strip()
                        elif line.startswith("Version:"):
                            version = line[8:].strip()
                        if (name and version) or not line.strip():
                            break
            except OSError:
                continue
            if name and version:
                out[norm(name)] = version
    return out


def osv_fetch(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def check_packages(folder: Path, online: bool, fetch: Fetch | None = None) -> dict:
    title = "Packages the app uses"
    reqs = read_requirements(folder)
    if not reqs:
        return _check("packages", title, "good", "No extra packages are listed in requirements.txt.")
    loose = [r for r in reqs if not r["pinned"]]
    have = installed_versions(folder)
    queries, asked, details = [], [], []
    for r in reqs:
        version = have.get(norm(r["name"])) or r["version"]
        if version:
            queries.append({"package": {"ecosystem": "PyPI", "name": r["name"]}, "version": version})
            asked.append(f"{r['name']} {version}")
        if not r["pinned"]:
            details.append(f"{r['name']} has no fixed version ({r['spec'] or 'any version'}): a new release could change "
                           "the app without anyone noticing" + ("" if version else "; it is not installed yet either, so "
                                                                 "it could not be checked for known problems"))
    fixed = len(reqs) - len(loose)
    pinned_text = f"{fixed} of {_plural(len(reqs), 'package')} {'has' if fixed == 1 else 'have'} a fixed version"
    if not online:
        return _check("packages", title, "attention" if loose else "unknown",
                      pinned_text + "; known problems were not checked (switched off in Settings).", details)
    if not queries:
        return _check("packages", title, "attention" if loose else "unknown",
                      pinned_text + "; no versions to check for known problems.", details)
    try:
        answer = (fetch or osv_fetch)(OSV_URL, {"queries": queries})
        results = answer.get("results") or []
    except Exception:   # no internet, osv.dev down, unexpected answer: say so, never stop the version
        return _check("packages", title, "attention" if loose else "unknown",
                      pinned_text + "; known problems could not be checked today (no answer from osv.dev).", details)
    hits = []
    for label, res in zip(asked, results):
        ids = [v.get("id", "") for v in (res or {}).get("vulns") or [] if v.get("id")]
        if ids:
            hits.append(f"{label}: {_plural(len(ids), 'known security problem')} ({', '.join(ids[:5])}"
                        + (", ..." if len(ids) > 5 else "") + "): update to a fixed version")
    if hits:
        return _check("packages", title, "problem", f"{_plural(len(hits), 'package')} with known security problems; "
                      + pinned_text + ".", hits + details)
    return _check("packages", title, "attention" if loose else "good",
                  pinned_text + f"; no known security problems in {_plural(len(queries), 'package')}.", details)


def check_secrets(folder: Path, files: list[Path]) -> dict:
    from .updates import SECRET_RE
    hits = []
    for f in files:
        if f.suffix.lower() in BINARY or f.name == ".env":
            continue
        try:
            if f.stat().st_size > MAX_BYTES:
                continue
            m = SECRET_RE.search(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if m:
            hits.append(f"{_rel(f, folder)}: looks like a key or password ({m.group(0)[:8]}...): move it to a .env file "
                        "or Windows Credential Manager and change the key")
    title = "No secrets in the files"
    if hits:
        return _check("secrets", title, "problem", f"{_plural(len(hits), 'file')} {'seems' if len(hits) == 1 else 'seem'} to contain a key or password "
                      "that would be saved and shared with the code.", hits)
    return _check("secrets", title, "good", f"No keys or passwords found in {_plural(len(files), 'file')}.")


def check_findings(slug: str) -> dict:
    items = upkeep.findings(slug)
    title = "Review findings"
    if not items:
        return _check("findings", title, "good", "No review findings recorded.")
    open_ = [f for f in items if f["status"] == "open"]
    high = [f for f in open_ if f["severity"] == "high"]
    accepted = [f for f in items if f["status"] == "accepted"]
    fixed = [f for f in items if f["status"] == "fixed"]
    details = [f"{f['id']} open ({f['severity']}): {f['title']}" for f in open_]
    details += [f"{f['id']} accepted by you: {f['title']} (reason: {f.get('reason') or '-'})" for f in accepted]
    summary = f"{len(open_)} open, {len(fixed)} fixed, {len(accepted)} accepted by you"
    level = "problem" if high else ("attention" if open_ else "good")
    if high:
        summary = f"{_plural(len(high), 'serious finding')} still open; " + summary
    return _check("findings", title, level, summary + ".", details)


def check_documents(slug: str, folder: Path) -> dict:
    started = [it["started"] for it in iterations.load(slug) if it.get("started")]
    problems = upkeep.stale_docs(folder, max(started) if started else None)
    title = "Documents up to date"
    if problems:
        return _check("documents", title, "attention", "The documents do not follow the latest work yet.", problems)
    return _check("documents", title, "good", "STATUS.md (and the README, once the app runs) follow the latest work.")


# ---------------------------------------------------------------------------- the whole card
def _file(slug: str) -> Path:
    return projects.project_dir(slug) / ".aiteam" / "scorecard.json"


def headline(level: str, checks: list[dict]) -> str:
    n = sum(1 for c in checks if c["level"] == level)
    if level == "problem":
        return f"{_plural(n, 'problem')} to fix before this version is saved."
    if level == "attention":
        return f"Good to go, with {_plural(n + sum(1 for c in checks if c['level'] == 'unknown'), 'point')} to look at."
    return "Everything checked looks good."


def build(slug: str, online: bool | None = None, fetch: Fetch | None = None) -> dict:
    """Run every check, save the card (.aiteam/scorecard.json and reports/QUALITY.md) and return it."""
    if not projects.load(slug):
        raise KeyError(slug)
    folder = projects.project_dir(slug)
    if online is None:
        online = bool(settings.load().get("scorecard_online", True))
    files = _files(folder)
    checks = [check_requirements(slug), check_tests(slug, folder), check_code(folder, files),
              check_packages(folder, online, fetch), check_secrets(folder, files), check_findings(slug),
              check_documents(slug, folder)]
    level = _worst([c["level"] for c in checks])
    done = [it for it in iterations.load(slug) if it.get("status") == "done"]
    card = {"time": time.time(), "level": level, "headline": headline(level, checks), "checks": checks,
            "iteration": done[-1]["n"] if done else None, "commit": done[-1].get("commit", "") if done else ""}
    safefile.write_json(_file(slug), card)
    try:
        out = folder / "reports" / "QUALITY.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown(card), encoding="utf-8")
    except OSError:
        pass
    return card


def load(slug: str) -> dict | None:
    data = safefile.read_json(_file(slug), None)
    return data if isinstance(data, dict) and data.get("checks") else None


MARK = {"good": "OK", "attention": "To look at", "problem": "To fix", "unknown": "Not checked"}


def markdown(card: dict) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(card["time"]))
    lines = ["# Quality of this version", "",
             f"> Made by AI Team Lite on {when}" + (f" after iteration {card['iteration']} ({card['commit']})"
                                                    if card.get("iteration") else "") + ".", "",
             f"**{card['headline']}**", "", "| Check | Result | In short |", "|---|---|---|"]
    for c in card["checks"]:
        lines.append(f"| {c['title']} | {MARK[c['level']]} | {c['summary'].replace('|', '/')} |")
    for c in card["checks"]:
        if c["details"]:
            lines += ["", f"## {c['title']}", ""] + [f"- {d}" for d in c["details"]]
    return "\n".join(lines) + "\n"


def push_warning(slug: str) -> str:
    """For the GitHub approval card (owner's choice for L49: warn, do not block): the scorecard's red points."""
    card = load(slug)
    if not card:
        return "The quality check was not run for this version yet."
    bad = [c["title"] for c in card["checks"] if c["level"] == "problem"]
    if not bad:
        return ""
    done = [it for it in iterations.load(slug) if it.get("status") == "done"]
    old = " (checked before the latest iteration)" if done and done[-1]["n"] != card.get("iteration") else ""
    return f"{_plural(len(bad), 'point')} to fix{old}: {', '.join(bad)}. See the scorecard in the chat."


def brief(card: dict) -> str:
    """What Fred must do with the card."""
    todo = [c for c in card["checks"] if c["level"] in ("problem", "attention", "unknown")]
    if not todo:
        return "Quality check: everything checked looks good. Tell the user in one sentence."
    out = [f"Quality check: {card['headline']}"]
    for c in todo:
        out.append(f"- [{MARK[c['level']]}] {c['title']}: {c['summary']}")
        out += [f"    {d}" for d in c["details"][:6]]
    out.append("Fix every 'To fix' point (the Developer, then the Tester; record new findings with record_finding), "
               "then call quality_check again. For 'To look at' points, fix the easy ones and tell the user about the "
               "rest in plain words; a point the user decides to live with is fine, keep their reason (close_finding "
               "with \"accepted\" for findings). The user sees the full card in the chat and the Tests tab.")
    return "\n".join(out)
