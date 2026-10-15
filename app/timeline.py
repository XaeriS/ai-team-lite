"""Where the time goes (L14) and which phase a token or a minute belongs to (L13).

Every turn of the lead, every worker's run and every wait for the user is written as one span
{t0, t1, kind, who, stage, iteration} to time-log.jsonl in the app's data folder, and copied into the project's
own folder (.aiteam/time.jsonl) so it travels with the project, like the token ledger.

Stages are the steps a non-technical person recognises: problem, requirements, devil's advocate, architecture,
plan, build, test, review, diagrams... They are worked out from who is working and the project's phase.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import paths

_lock = threading.Lock()
_open: dict[tuple[str, str], tuple[str, float]] = {}   # (project, key) -> (who, start) for a worker running now

STAGES = [  # (key, plain label), in the order a project goes through them
    ("setup", "Setup"),
    ("problem", "Problem"),
    ("requirements", "Requirements"),
    ("challenge", "Devil's advocate"),
    ("prior_art", "Existing solutions"),
    ("architecture", "Architecture"),
    ("plan", "Plan"),
    ("build", "Build"),
    ("test", "Test"),
    ("review", "Review"),
    ("diagrams", "Diagrams"),
    ("council", "Council"),
    ("coordination", "Fred coordinating"),
    ("your_review", "Your review"),
    ("other", "Other"),
]
STAGE_LABELS = dict(STAGES)
STAGE_ORDER = {k: i for i, (k, _) in enumerate(STAGES)}
WORKER_STAGE = {"Developer": "build", "Tester": "test", "Reviewer": "review", "Diagrammer": "diagrams",
                "Challenger": "challenge", "Scout": "prior_art"}
PHASE_STAGE = {"architecture": "architecture", "plan": "plan", "execute": "coordination", "review": "your_review"}
AGENT_GROUPS = {"Fred": "Fred", "Mat": "Mat", "Developer": "Developer", "Tester": "Tester", "Reviewer": "Reviewer",
                "Diagrammer": "Diagrammer", "Challenger": "Devil's advocate", "Scout": "Scout"}


def stage_for(who: str, phase: str = "", problem_done: bool | None = None, project: str = "") -> str:
    """The stage a token or a minute belongs to, from who did the work and the project's phase."""
    who = who or ""
    if project == "setup":
        return "setup"
    if who in WORKER_STAGE:
        return WORKER_STAGE[who]
    if who.startswith("Council"):
        return "council"
    if who == "Mat":
        return "architecture"
    if phase == "discover" or not phase:
        if problem_done is None:
            return "requirements"
        return "requirements" if problem_done else "problem"
    return PHASE_STAGE.get(phase, "other")


def agent_group(who: str) -> str:
    """Agent type for the summaries: council brains are grouped as 'Council'."""
    if (who or "").startswith("Council"):
        return "Council"
    return AGENT_GROUPS.get(who or "", who or "Fred")


def project_stage(project: str, who: str) -> dict:
    """The stage, phase and iteration of a project right now (recorded with each token row and time span)."""
    try:
        from . import challenge, iterations, projects
        if project == projects.SETUP_SLUG:
            return {"stage": "setup", "phase": "setup", "iteration": None}
        p = projects.load(project) or {}
        cur = iterations.current(project)
        phase = p.get("phase", "")
        done = not challenge.missing(p.get("problem") or {}) if phase in ("discover", "") else None
        return {"stage": stage_for(who, phase, done, project), "phase": phase, "iteration": cur["n"] if cur else None}
    except Exception:  # noqa: BLE001
        return {"stage": stage_for(who), "phase": "", "iteration": None}


# ---------------------------------------------------------------------------- recording
def _log_file() -> Path:
    return paths.data_dir() / "time-log.jsonl"


def _ledger(project: str) -> Path | None:
    if not project or project in ("setup", "?"):
        return None
    try:
        from . import projects
        if not projects.load(project):
            return None
        d = projects.project_dir(project) / projects.META
        d.mkdir(parents=True, exist_ok=True)
        return d / "time.jsonl"
    except Exception:  # noqa: BLE001
        return None


def record(project: str, kind: str, who: str, t0: float, t1: float) -> None:
    """kind: "lead" (a turn of Fred or Mat), "worker" (a worker's run) or "wait" (waiting for the user)."""
    if not project or t1 - t0 < 0.5:
        return
    row = {"t0": round(t0, 2), "t1": round(t1, 2), "project": project, "kind": kind, "who": who,
           **project_stage(project, who)}
    line = json.dumps(row) + "\n"
    with _lock:
        try:
            with open(_log_file(), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
        ledger = _ledger(project)
        if ledger is not None:
            try:
                with open(ledger, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass


def start(project: str, key: str, who: str) -> None:
    with _lock:
        _open[(project, key)] = (who, time.time())


def stop(project: str, key: str) -> None:
    with _lock:
        item = _open.pop((project, key), None)
    if item:
        record(project, "worker", item[0], item[1], time.time())


def rows(project: str | None = None) -> list[dict]:
    """Time spans of one project (its own ledger when it has one) or of all projects."""
    path = None
    if project:
        try:
            from . import projects
            p = projects.project_dir(project) / projects.META / "time.jsonl"
            path = p if p.exists() else None
        except Exception:  # noqa: BLE001
            path = None
    try:
        lines = (path or _log_file()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if project and r.get("project") != project:
            continue
        out.append(r)
    # a worker running right now counts up to now
    now = time.time()
    with _lock:
        for (proj, _key), (who, t0) in _open.items():
            if project is None or proj == project:
                out.append({"t0": t0, "t1": now, "project": proj, "kind": "worker", "who": who, "live": True,
                            **project_stage(proj, who)})
    return out


def _overlap(a0: float, a1: float, spans: list[tuple[float, float]]) -> float:
    """How much of [a0, a1] is covered by the (possibly overlapping) spans."""
    cut = sorted((max(a0, s0), min(a1, s1)) for s0, s1 in spans if s1 > a0 and s0 < a1)
    total, end = 0.0, a0
    for s0, s1 in cut:
        s0 = max(s0, end)
        if s1 > s0:
            total += s1 - s0
            end = s1
    return total


def time_by_stage(project: str | None = None, iteration: int | None = None) -> dict:
    """Seconds per stage, working time and waiting-for-you time kept apart (L14). The lead's own time is his turn
    minus the time his workers worked and the time he waited for you inside that turn."""
    rs = rows(project)
    if iteration is not None:
        rs = [r for r in rs if r.get("iteration") == iteration]
    nested = [(r["t0"], r["t1"]) for r in rs if r.get("kind") in ("worker", "wait")]
    work: dict[str, float] = {}
    wait: dict[str, float] = {}
    for r in rs:
        d = max(0.0, r["t1"] - r["t0"])
        stage = r.get("stage") or stage_for(r.get("who", ""), r.get("phase", ""))
        if r.get("kind") == "wait":
            wait[stage] = wait.get(stage, 0.0) + d
            continue
        if r.get("kind") == "lead":
            d = max(0.0, d - _overlap(r["t0"], r["t1"], nested))
        work[stage] = work.get(stage, 0.0) + d
    stages = sorted(set(work) | set(wait), key=lambda k: STAGE_ORDER.get(k, 99))
    out = [{"stage": k, "label": STAGE_LABELS.get(k, k), "work": round(work.get(k, 0.0)), "wait": round(wait.get(k, 0.0))}
           for k in stages]
    return {"stages": out, "work": round(sum(work.values())), "wait": round(sum(wait.values()))}
