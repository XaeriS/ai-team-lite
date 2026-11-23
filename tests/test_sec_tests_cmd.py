"""H-2: run_tests / commit_iteration run only a plain test runner (python -m pytest, pytest, npm test ...), and
nothing before the plan is approved."""
import asyncio

import pytest

from app import engine as engine_mod, iterations, projects, secrets_store, settings, tools
from app.events import EventHub
from fakeclaude import FakeClient

BAD = ['python -c "import os; os.system(\'calc\')"', "pytest && curl http://x/?d=1", "pytest; calc",
       "pytest | powershell", "powershell -Command Get-Process", "cmd /c dir", "python -m pytest `calc`",
       "pytest $(calc)", "pytest\ncalc", "python x.py", "curl http://x"]
GOOD = ["python -m pytest -q", "pytest -q tests/test_a.py", ".venv\\Scripts\\python -m pytest -q", "npm test"]


@pytest.fixture()
def env(isolated, monkeypatch):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    settings.save({"setup_complete": True})
    p = projects.create("T")
    slug = p["slug"]
    projects.update(slug, plan_approved=True, architecture_approved=True, phase="execute")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    sess = eng.session(slug)
    _, chosen = tools.build_server(sess)
    ran = []
    monkeypatch.setattr(iterations, "run_tests",
                        lambda cmd, cwd, timeout=300, label="tests": (ran.append(cmd) or (True, "ok")))
    h = {t.name: t.handler for t in chosen}

    def call(name, args):
        return asyncio.run_coroutine_threadsafe(h[name](args), eng.loop).result(10)
    yield type("E", (), {"slug": slug, "call": call, "ran": ran})
    eng.shutdown()


@pytest.mark.parametrize("cmd", BAD)
def test_run_tests_refuses_anything_but_a_test_runner(env, cmd):
    out = env.call("run_tests", {"test_command": cmd, "scope": "all"})
    assert out.get("is_error"), f"accepted: {cmd!r}"
    assert env.ran == []


@pytest.mark.parametrize("cmd", GOOD)
def test_run_tests_still_runs_a_plain_test_runner(env, cmd):
    out = env.call("run_tests", {"test_command": cmd, "scope": "all"})
    assert not out.get("is_error"), out
    assert env.ran == [cmd]


def test_run_tests_refuses_before_the_plan_is_approved(env):
    projects.update(env.slug, plan_approved=False)
    out = env.call("run_tests", {"test_command": "python -m pytest -q", "scope": "all"})
    assert out.get("is_error") and env.ran == []


@pytest.mark.parametrize("cmd", BAD)
def test_commit_iteration_refuses_anything_but_a_test_runner(env, monkeypatch, cmd):
    monkeypatch.setattr(iterations, "load", lambda slug: [{"n": 1, "name": "x", "status": "building"}])
    monkeypatch.setattr(iterations, "_save", lambda *a, **k: None)
    with pytest.raises(ValueError):
        iterations.commit(env.slug, 1, "s", cmd)
    assert env.ran == []


def test_commit_iteration_refuses_before_the_plan_is_approved(env, monkeypatch):
    projects.update(env.slug, plan_approved=False)
    monkeypatch.setattr(iterations, "load", lambda slug: [{"n": 1, "name": "x", "status": "building"}])
    monkeypatch.setattr(iterations, "_save", lambda *a, **k: None)
    with pytest.raises(ValueError):
        iterations.commit(env.slug, 1, "s", "python -m pytest -q")
    assert env.ran == []


def test_boxed_test_run_that_cannot_start_is_refused_not_run_unboxed(isolated, monkeypatch):
    import subprocess
    from app import sandbox, testrun
    monkeypatch.setattr(sandbox, "enabled", lambda: True)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(sandbox, "shell_line", lambda c: c)
    monkeypatch.setattr(sandbox, "note_unprotected", lambda *a: None)

    def boom(*a, **k):
        raise OSError("no box")
    monkeypatch.setattr(sandbox, "start_process", boom)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ran outside the sandbox"))
    ok, out = testrun.run("python -m pytest", isolated)
    assert ok is False and "sandbox" in out.lower() and "Traceback" not in out
