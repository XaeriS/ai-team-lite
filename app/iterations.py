"""Iteration-based delivery: the plan is split into iterations (one feature each). Nothing is built before
the user approves the plan; then each iteration is built, tested, and committed to Git only when its tests
pass, before the next one starts."""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from . import paths, policy, projects, settings, usage

STATUSES = ("planned", "building", "done")
AFTER = ("continue", "pause")  # when an iteration is finished: go on to the next one, or wait for the user's release


def load(slug: str) -> list[dict]:
    return list((projects.load(slug) or {}).get("iterations") or [])


def _save(slug: str, its: list[dict]) -> None:
    projects.update(slug, iterations=its)


def normalize(raw) -> list[dict]:
    """Validate the iterations Fred proposes: [{name, goal, requirements}]"""
    if not isinstance(raw, list) or not raw:
        raise ValueError("The plan must be split into iterations (at least one).")
    out = []
    for i, it in enumerate(raw, 1):
        if not isinstance(it, dict) or not str(it.get("name", "")).strip():
            raise ValueError(f"Iteration {i} needs a name.")
        reqs = it.get("requirements") or []
        if isinstance(reqs, str):
            reqs = [r.strip() for r in reqs.replace(";", ",").split(",") if r.strip()]
        kind = "cleanup" if it.get("kind") == "cleanup" or re.search(r"clean-?up", str(it["name"]), re.I) else "feature"
        out.append({"name": str(it["name"]).strip()[:120], "goal": str(it.get("goal", "")).strip()[:600],
                    "requirements": [str(r)[:40] for r in reqs][:20], "kind": kind,
                    # existing folder: work that was finished before AI Team Lite, shown as done (nothing to rebuild)
                    "already_done": bool(it.get("already_done")) and kind == "feature"})
    return out


CLEANUP_ITERATION = {"name": "Clean-up before the version", "kind": "cleanup", "requirements": [],
                     "goal": "remove dead code and duplication, refactor where needed, re-run every test"}


def propose(slug: str, raw) -> list[dict]:
    """Keep finished iterations, replace the rest with the new proposal (numbering continues)."""
    new = normalize(raw)
    if settings.load().get("version_cleanup", True) and not any(i["kind"] == "cleanup" for i in new):
        new.append(dict(CLEANUP_ITERATION))
    done = [it for it in load(slug) if it.get("status") == "done"]
    default = "pause" if settings.load().get("iteration_pause") else "continue"
    flags = [it.pop("already_done", False) for it in new]   # finished before AI Team Lite (existing folder)
    before = [it for it, f in zip(new, flags) if f]
    todo = [it for it, f in zip(new, flags) if not f]
    its = done + [{"n": len(done) + i, "status": "done", "existing": True, "after": "continue", **it}
                  for i, it in enumerate(before, 1)]
    its += [{"n": len(its) + i, "status": "planned", "after": default, **it} for i, it in enumerate(todo, 1)]
    _save(slug, its)
    return its


def set_after(slug: str, changes: dict) -> list[dict]:
    """changes: {n: "continue" | "pause"}"""
    its = load(slug)
    for it in its:
        v = changes.get(it["n"], changes.get(str(it["n"])))
        if v in AFTER:
            it["after"] = v
    _save(slug, its)
    p = projects.load(slug) or {}
    # switching the finished iteration's gate back to "continue" releases the next one
    w = p.get("waiting_release")
    if w and any(it["n"] == w - 1 and it.get("after") == "continue" for it in its):
        projects.update(slug, waiting_release=None)
    return its


def remove_cleanup(slug: str) -> list[dict]:
    """The user unticked the clean-up iteration on the plan card."""
    its = [it for it in load(slug) if not (it.get("kind") == "cleanup" and it.get("status") == "planned"
                                           and it.get("name") == CLEANUP_ITERATION["name"])]
    for i, it in enumerate(its, 1):
        it["n"] = i
    _save(slug, its)
    return its


def normalize_tasks(raw) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("Split the iteration into small tasks: tasks = [{title, files, test}].")
    out = []
    for t in raw[:40]:
        t = t if isinstance(t, dict) else {"title": str(t)}
        if not str(t.get("title", "")).strip():
            raise ValueError("Each task needs a title.")
        files = t.get("files") or []
        if isinstance(files, str):
            files = [x.strip() for x in files.split(",") if x.strip()]
        out.append({"title": str(t["title"]).strip()[:160], "files": [str(x)[:200] for x in files][:20],
                    "test": str(t.get("test", "")).strip()[:200], "done": False})
    return out


def complete_task(slug: str, n: int, task: int | str, note: str = "") -> dict:
    its = load(slug)
    it = next((x for x in its if x["n"] == n), None)
    if not it or it.get("status") != "building":
        raise ValueError(f"Iteration {n} is not being built.")
    tasks = it.get("tasks") or []
    idx = None
    if isinstance(task, int) or str(task).isdigit():
        idx = int(task) - 1
    else:
        idx = next((i for i, t in enumerate(tasks) if t["title"].lower() == str(task).lower()), None)
    if idx is None or not 0 <= idx < len(tasks):
        raise ValueError(f"There is no task {task} in iteration {n} (tasks are numbered from 1).")
    tasks[idx].update(done=True, note=note.strip()[:300], finished=time.time())
    _save(slug, its)
    return tasks[idx]


def record_red(slug: str, n: int, test_command: str) -> dict:
    """Tests-first (item 25): run the new tests before the code exists; they must fail."""
    its = load(slug)
    it = next((x for x in its if x["n"] == n), None)
    if not it or it.get("status") != "building":
        raise ValueError(f"Iteration {n} is not being built (start it first).")
    folder = projects.project_dir(slug)
    cmd = test_command.strip()
    if not cmd or policy.is_risky(cmd) or policy._abs_paths_outside(cmd, [str(folder), str(paths.bundled_python().parent)]):
        raise ValueError("test_command is missing or not allowed (it must only run the project's tests).")
    ok, output = run_tests(cmd, folder)
    it["red"] = {"time": time.time(), "failed": not ok, "command": cmd, "output": output[-1500:]}
    _save(slug, its)
    return it["red"]


def commit_checks(slug: str, n: int, light_cleanup: str = "") -> list[str]:
    """Problems that block the commit before the tests are even run (items 25, 27, 29)."""
    it = next((x for x in load(slug) if x["n"] == n), None)
    if not it:
        return [f"There is no iteration {n}."]
    s = settings.load()
    problems = []
    if s.get("tests_first", True) and it.get("kind") != "cleanup":
        red = it.get("red") or {}
        if not red:
            problems.append("Tests first: no failing-first test run was recorded for this iteration. Have the Tester "
                            "write the tests from the requirements and call record_red_tests BEFORE building.")
        elif not red.get("failed"):
            problems.append("Tests first: the recorded test run PASSED before the code was written, so those tests do not "
                            "check the new work. Write tests for this iteration's acceptance criteria and record them again.")
    open_tasks = [f"{i}. {t['title']}" for i, t in enumerate(it.get("tasks") or [], 1) if not t.get("done")]
    if open_tasks:
        problems.append("Tasks not finished: " + "; ".join(open_tasks) + ". Finish them (complete_task) first.")
    if s.get("light_cleanup", True) and not light_cleanup.strip():
        problems.append('Light clean-up: run the quick clean-up of the files changed in this iteration, then pass '
                        'light_cleanup="done" (or the reason it was skipped).')
    return problems


def approve(slug: str) -> dict | None:
    """The plan was approved: the approval itself releases the first iteration still to do."""
    its = load(slug)
    first = next((it for it in its if it["status"] != "done"), None)
    projects.update(slug, released=[first["n"]] if first else [], waiting_release=None)
    return first


def release(slug: str, n: int) -> dict:
    p = projects.load(slug) or {}
    it = next((x for x in load(slug) if x["n"] == n), None)
    if not it or it["status"] != "planned":
        raise ValueError(f"Iteration {n} is not waiting to start.")
    if not p.get("plan_approved"):
        raise ValueError("The plan has not been approved yet.")
    projects.update(slug, released=sorted(set((p.get("released") or []) + [n])), waiting_release=None)
    return it


def gate_open(slug: str, n: int) -> bool:
    """May iteration n start? The first after approval, or after an iteration set to continue, or released."""
    p = projects.load(slug) or {}
    if "released" not in p:  # plan approved before 0.7.0
        return True
    if n in (p.get("released") or []):
        return True
    prev = next((x for x in load(slug) if x["n"] == n - 1), None)
    return prev is not None and prev.get("status") == "done" and prev.get("after", "continue") == "continue"


def markdown(its: list[dict]) -> str:
    rows = ["| # | Iteration | Requirements | Status |", "|---|---|---|---|"]
    for it in its:
        tasks = it.get("tasks") or []
        rows.append(f"| {it['n']} | **{it['name']}**{' - ' + it['goal'] if it.get('goal') else ''} | "
                    f"{', '.join(it.get('requirements') or []) or '-'} | {it.get('status', 'planned')}"
                    f"{' (' + it['commit'] + ')' if it.get('commit') else ''} |")
        for t in tasks:
            rows.append(f"|  | {'[x]' if t.get('done') else '[ ]'} {t['title']} | {', '.join(t.get('files') or [])} | |")
    return "\n".join(rows)


def current(slug: str) -> dict | None:
    return next((it for it in load(slug) if it.get("status") == "building"), None)


def progress(slug: str) -> dict:
    its = load(slug)
    cur = current(slug) or next((it for it in its if it.get("status") == "planned"), None)
    return {"total": len(its), "done": sum(1 for it in its if it.get("status") == "done"),
            "current": cur["n"] if cur else None, "current_name": cur["name"] if cur else "",
            "building": bool(current(slug))}


def refresh_plan_doc(slug: str) -> None:
    """Keep the Iterations table in PLAN.md in step with reality (status and commit ids)."""
    f = projects.project_dir(slug) / "PLAN.md"
    if not f.exists():
        return
    text = f.read_text(encoding="utf-8")
    head = text.split("\n## Iterations\n")[0].rstrip()
    f.write_text(head + "\n\n## Iterations\n" + markdown(load(slug)) + "\n", encoding="utf-8")


def start(slug: str, n: int, tasks=None) -> dict:
    p = projects.load(slug) or {}
    if not p.get("plan_approved"):
        raise ValueError("The plan has not been approved by the user. Do not build anything yet.")
    its = load(slug)
    it = next((x for x in its if x["n"] == n), None)
    if not it:
        raise ValueError(f"There is no iteration {n} in the approved plan.")
    if it["status"] == "done":
        raise ValueError(f"Iteration {n} is already done and committed.")
    open_before = [x["n"] for x in its if x["n"] < n and x["status"] != "done"]
    if open_before:
        raise ValueError(f"Finish and commit iteration {open_before[0]} first (iterations are done in order).")
    busy = [x["n"] for x in its if x["status"] == "building" and x["n"] != n]
    if busy:
        raise ValueError(f"Iteration {busy[0]} is still being built; commit it first.")
    if it["status"] == "planned" and not gate_open(slug, n):
        projects.update(slug, waiting_release=n)
        raise ValueError(f"Iteration {n} is waiting for the user's release (they chose to pause after iteration "
                         f"{n - 1}). Stop here and tell the user in one line; they release it from the Pipeline screen.")
    it["status"], it["started"] = "building", time.time()
    if tasks is not None:
        it["tasks"] = normalize_tasks(tasks)
    _save(slug, its)
    refresh_plan_doc(slug)
    return it


def run_tests(command: str, cwd: Path, timeout: int = 300) -> tuple[bool, str]:
    env = {**os.environ, "PATH": paths.tool_path(), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run(command, cwd=str(cwd), shell=True, capture_output=True, text=True, timeout=timeout,
                           env=env, creationflags=0x08000000 if paths.IS_WINDOWS else 0)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-3000:]
    except subprocess.TimeoutExpired:
        return False, f"Tests timed out after {timeout} s."


def _git(folder: Path, *args: str) -> subprocess.CompletedProcess:
    exe = paths.git_exe()
    if not exe:
        raise RuntimeError("Git is not available.")
    name = settings.load().get("user_name") or "AI Team Lite"
    return subprocess.run([exe, "-c", f"user.name={name} (AI Team Lite)", "-c", "user.email=ai-team@localhost", *args],
                          cwd=str(folder), capture_output=True, text=True, timeout=120,
                          creationflags=0x08000000 if paths.IS_WINDOWS else 0)


def commit(slug: str, n: int, summary: str, test_command: str, light_cleanup: str = "") -> dict:
    """Run the tests; only if they pass, commit everything to Git and mark the iteration done.
    Returns {"ok", "output", "commit"}."""
    its = load(slug)
    it = next((x for x in its if x["n"] == n), None)
    if not it or it["status"] != "building":
        raise ValueError(f"Iteration {n} is not being built (start it with start_iteration first).")
    folder = projects.project_dir(slug)
    cmd = test_command.strip()
    if not cmd or policy.is_risky(cmd) or policy._abs_paths_outside(cmd, [str(folder), str(paths.bundled_python().parent)]):
        raise ValueError("test_command is missing or not allowed (it must only run the project's tests).")
    ok, output = run_tests(cmd, folder)
    it["last_test"] = {"ok": ok, "time": time.time()}
    if not ok:
        _save(slug, its)
        return {"ok": False, "output": output, "commit": None}
    if not (folder / ".git").exists():
        _git(folder, "init", "-b", "main")
    projects.exclude_meta(folder)  # never commit AI Team Lite's own chat history
    _git(folder, "add", "-A")
    message = f"Iteration {n}: {it['name']}\n\n{summary.strip()}\n\nRequirements: {', '.join(it.get('requirements') or []) or '-'}" \
              "\nTests: passed"
    r = _git(folder, "commit", "-m", message)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        raise RuntimeError(f"Git could not commit: {(r.stderr or r.stdout).strip()[-400:]}")
    head = _git(folder, "rev-parse", "--short", "HEAD").stdout.strip()
    it.update(status="done", commit=head, summary=summary.strip()[:600], finished=time.time(),
              light_cleanup=light_cleanup.strip()[:200])
    _save(slug, its)
    refresh_plan_doc(slug)
    p = projects.load(slug) or {}
    if not p.get("launch") and (folder / "run.bat").exists():
        projects.update(slug, launch={"command": "run.bat", "url": ""})
    nxt = next((x for x in its if x["status"] != "done"), None)
    if nxt and it.get("after") == "pause" and nxt["n"] not in (p.get("released") or []):
        projects.update(slug, waiting_release=nxt["n"])
    if all(x["status"] == "done" for x in its):
        projects.update(slug, phase="review")
    return {"ok": True, "output": output, "commit": head}


# ---------------------------------------------------------------------------- cost estimates (request 15)
def _windows(slug: str) -> list[tuple[float, float]]:
    return [(it["started"], it["finished"]) for it in load(slug)
            if it.get("status") == "done" and it.get("started") and it.get("finished")]
def cost_estimate(slug: str) -> dict:
    """Cost so far at the providers' prices, and an estimate for the iterations still to do (from the average of
    the finished iterations of this project, or of all projects when this one has none yet)."""
    data = usage.project_cost(slug, _windows(slug))
    per_it = [c for c in data["windows"] if c > 0]
    basis = "this project"
    if not per_it:
        for p in projects.list_projects():
            if p["slug"] != slug:
                per_it += [c for c in usage.project_cost(p["slug"], _windows(p["slug"]))["windows"] if c > 0]
        basis = "your other projects"
    remaining = sum(1 for it in load(slug) if it.get("status") != "done")
    avg = sum(per_it) / len(per_it) if per_it else None
    est = None
    if avg is not None and remaining:
        lo, hi = (min(per_it), max(per_it)) if len(per_it) > 1 else (avg * 0.6, avg * 1.6)
        est = {"low": round(lo * remaining, 2), "high": round(hi * remaining, 2), "mid": round(avg * remaining, 2)}
    return {"so_far": round(data["est_cost"], 2), "tokens": data["tokens"], "per_iteration": round(avg, 2) if avg else None,
            "remaining_iterations": remaining, "remaining": est, "basis": basis if per_it else ""}
