"""Iteration-based delivery: plan by iterations, express approval, build -> test -> commit, in order."""
import subprocess
import sys
import time

import pytest

from app import engine as engine_mod, iterations, paths, projects, secrets_store, settings
from app.events import EventHub
from fakeclaude import FakeClient, say

TEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider tests'
ITS = [{"name": "Add expenses", "goal": "simplest version", "requirements": ["REQ-1"]},
       {"name": "Monthly totals", "requirements": "REQ-2, REQ-3"}]


def git_log(folder):
    r = subprocess.run([paths.git_exe(), "log", "--format=%an|%s"], cwd=folder, capture_output=True, text=True)
    return [line for line in r.stdout.splitlines() if line]


def make_project(name="Shop"):
    p = projects.create(name)
    projects.update(p["slug"], architecture_approved=True)
    folder = projects.project_dir(p["slug"])
    (folder / "tests").mkdir(exist_ok=True)
    (folder / "tests" / "test_app.py").write_text("from app_mod import total\n\ndef test_total():\n    assert total([1, 2]) == 3\n")
    return p, folder


def test_normalize_and_propose(isolated):
    with pytest.raises(ValueError, match="iterations"):
        iterations.normalize([])
    with pytest.raises(ValueError, match="name"):
        iterations.normalize([{"goal": "x"}])
    p, _ = make_project()
    its = iterations.propose(p["slug"], ITS)
    assert [(i["n"], i["status"]) for i in its] == [(1, "planned"), (2, "planned")]
    assert its[1]["requirements"] == ["REQ-2", "REQ-3"]
    assert "| 1 | **Add expenses** - simplest version | REQ-1 | planned |" in iterations.markdown(its)


def test_start_needs_approval_and_order(isolated):
    p, _ = make_project()
    iterations.propose(p["slug"], ITS)
    with pytest.raises(ValueError, match="not been approved"):
        iterations.start(p["slug"], 1)
    projects.update(p["slug"], plan_approved=True)
    with pytest.raises(ValueError, match="iteration 1 first"):
        iterations.start(p["slug"], 2)
    assert iterations.start(p["slug"], 1)["status"] == "building"
    with pytest.raises(ValueError, match="no iteration 9"):
        iterations.start(p["slug"], 9)


def test_commit_only_when_tests_pass(isolated):
    settings.save({"user_name": "Fred"})
    p, folder = make_project()
    iterations.propose(p["slug"], ITS)
    projects.update(p["slug"], plan_approved=True)
    iterations.start(p["slug"], 1)
    before = git_log(folder)
    (folder / "app_mod.py").write_text("def total(xs):\n    return 0\n")
    r = iterations.commit(p["slug"], 1, "Adds expenses", TEST_CMD)
    assert not r["ok"] and "assert" in r["output"]
    assert git_log(folder) == before, "failing work must not be committed"
    assert iterations.load(p["slug"])[0]["status"] == "building"
    (folder / "app_mod.py").write_text("def total(xs):\n    return sum(xs)\n")
    r = iterations.commit(p["slug"], 1, "Adds expenses", TEST_CMD)
    assert r["ok"] and r["commit"]
    log = git_log(folder)
    assert log[0] == "Fred (AI Team Lite)|Iteration 1: Add expenses"
    it = iterations.load(p["slug"])[0]
    assert it["status"] == "done" and it["commit"] == r["commit"]
    with pytest.raises(ValueError, match="not being built"):
        iterations.commit(p["slug"], 1, "again", TEST_CMD)
    with pytest.raises(ValueError, match="not allowed"):
        iterations.start(p["slug"], 2)
        iterations.commit(p["slug"], 2, "x", "git push origin main")
    # replanning keeps the finished iteration and numbers continue
    its = iterations.propose(p["slug"], [{"name": "Export to Excel"}])
    assert [(i["n"], i["name"], i["status"]) for i in its] == [(1, "Add expenses", "done"), (2, "Export to Excel", "planned")]


def _wait(cond, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    return False


def test_full_flow_with_fred(isolated):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    settings.save({"setup_complete": True, "iteration_pause": True})
    p, folder = make_project("Flow")
    out = {}

    async def script(i, prompt, c):
        if i == 0:
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__request_plan_approval", {"title": "Plan", "plan_markdown": "Goal"})
            out["no_iterations"] = res
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__request_plan_approval",
                                                {"title": "Plan", "plan_markdown": "Goal", "iterations": ITS})
            out["approval"] = res
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__start_iteration", {"n": 2})
            out["out_of_order"] = res
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__start_iteration", {"n": 1})
            (folder / "app_mod.py").write_text("def total(xs):\n    return 0\n")
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__commit_iteration", {"n": 1, "summary": "s", "test_command": TEST_CMD})
            out["failed_commit"] = res
            (folder / "app_mod.py").write_text("def total(xs):\n    return sum(xs)\n")
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__commit_iteration", {"n": 1, "summary": "Expenses work", "test_command": TEST_CMD})
            out["commit"] = res
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__start_iteration", {"n": 2})  # pause setting -> asks
            out["second"] = res
        yield say("ok")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(p["slug"], "go")
    assert _wait(lambda: any(e["type"] == "plan" and e.get("status") == "pending" for e in hub.history(p["slug"])))
    assert projects.load(p["slug"])["plan_approved"] is False, "nothing may be built before approval"
    plan = next(e for e in hub.history(p["slug"]) if e["type"] == "plan")
    assert [i["name"] for i in plan["iterations"]] == ["Add expenses", "Monthly totals"]
    assert [i["after"] for i in plan["iterations"]] == ["pause", "pause"], "default from the setting"
    eng.respond(plan["id"], {"approved": True})
    assert _wait(lambda: not eng.is_busy(p["slug"]))
    assert projects.load(p["slug"])["waiting_release"] == 2
    assert eng.project_state(p["slug"]) == "release"
    eng.shutdown()
    assert "split into iterations" in out["no_iterations"]
    assert "APPROVED" in out["approval"] and "start_iteration(1)" in out["approval"]
    assert "iteration 1 first" in out["out_of_order"]
    assert "NOT committed" in out["failed_commit"]
    assert "committed to Git" in out["commit"] and "waiting for their release" in out["commit"]
    assert "waiting for the user's release" in out["second"]
    events = [(e["n"], e["status"]) for e in hub.history(p["slug"]) if e["type"] == "iteration"]
    assert events == [(1, "building"), (1, "done")]
    assert git_log(folder)[0].endswith("Iteration 1: Add expenses")
    assert "Add expenses" in (folder / "PLAN.md").read_text()
    assert iterations.progress(p["slug"]) == {"total": 2, "done": 1, "current": 2, "current_name": "Monthly totals", "building": False}
