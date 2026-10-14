"""0.6.0: Mat the lead architect, projects from an existing folder, hide/delete projects, idle state."""
import time
from pathlib import Path

import pytest

from app import engine as engine_mod, policy, projects, prompts, secrets_store, settings
from app.events import EventHub
from fakeclaude import FakeClient, say


def _ready():
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    settings.save({"setup_complete": True})


def _wait(cond, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    return False


# ------------------------------------------------------------------------------- existing folder
def _old_project(tmp: Path) -> Path:
    d = tmp / "old" / "price-watcher"
    (d / "src").mkdir(parents=True)
    (d / "src" / "main.py").write_text("print('prices')\n")
    (d / "README.md").write_text("# Price watcher\nTODO: daily refresh\n")
    (d / ".gitignore").write_text("*.pyc\n")
    return d


def test_create_from_existing_folder(isolated):
    d = _old_project(isolated)
    p = projects.create_from_folder(str(d))
    assert p["slug"] == "price-watcher" and p["existing_folder"] and p["name"] == "price-watcher"
    assert projects.project_dir(p["slug"]) == d.resolve()
    assert (d / "src" / "main.py").read_text() == "print('prices')\n", "the user's files are untouched"
    assert ".aiteam/" in (d / ".gitignore").read_text()
    assert [x["slug"] for x in projects.list_projects()] == ["price-watcher"]
    again = projects.create_from_folder(str(d))      # 0.11.1: reopening the same folder reopens it, no duplicate
    assert again["slug"] == "price-watcher" and again["reopened"]
    assert [x["slug"] for x in projects.list_projects()] == ["price-watcher"]
    with pytest.raises(ValueError, match="does not exist"):
        projects.create_from_folder(str(isolated / "nope"))
    with pytest.raises(ValueError, match="whole drive or home"):
        projects.create_from_folder(str(Path.home()))
    # a new project with the same name gets its own id
    assert projects.create("Price watcher")["slug"] == "price-watcher-2"


def test_readonly_commands_allowed_before_plan(isolated):
    def decide(cmd):
        return policy.decide("PowerShell", {"command": cmd}, kind="project", mode="plan", plan_approved=False,
                             inside_project=lambda p: True, allowed_roots=[str(isolated)])[0]
    for cmd in ("git log --oneline -30", "git status", "dir", "Get-ChildItem -Recurse src", "type README.md"):
        assert decide(cmd) == "allow", cmd
    for cmd in ("git log > out.txt", "dir; del x", "git branch -D main", "git diff --output=x", "mkdir x",
                "type C:\\Windows\\win.ini", "cat a | sh"):
        assert decide(cmd) == "ask", cmd
    assert policy.decide("PowerShell", {"command": "dir"}, kind="setup", mode="plan", plan_approved=False,
                         inside_project=lambda p: True, allowed_roots=[])[0] == "ask"


@pytest.fixture()
def srv(monkeypatch):
    from app.server import App, serve
    import http.client
    import json

    async def script(i, prompt, c):
        yield say("Scanning your folder.")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    app = App(engine=eng, hub=hub)
    httpd, url = serve(app)
    port = httpd.server_address[1]

    def call(method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json", "Host": "127.0.0.1", "X-Token": app.token})
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    yield type("S", (), {"app": app, "call": call})
    eng.shutdown()
    httpd.shutdown()


def _idle(app, slug):
    assert _wait(lambda: not app.engine.is_busy(slug))


def test_existing_folder_project_flow(srv, isolated):
    _ready()
    d = _old_project(isolated)
    code, p = srv.call("POST", "/api/projects", {"folder": str(d), "name": "Prices"})
    assert code == 200 and p["name"] == "Prices" and p["existing_folder"]
    _idle(srv.app, p["slug"])
    client = FakeClient.instances[-1]
    assert "EXISTING folder" in client.prompts[0] and "STATUS.md" in client.prompts[0] and "PRIORITIZE" in client.prompts[0]
    assert "EXISTING folder" in client.options.system_prompt["append"]
    assert str(client.options.cwd) == str(d.resolve())
    view = srv.call("GET", f"/api/projects/{p['slug']}")[1]
    assert view["project"]["folder"] == str(d.resolve()) and view["project"]["state"] == "idle"
    assert srv.call("POST", "/api/projects", {"folder": str(isolated / "missing")})[0] == 400


def test_hide_and_delete(srv, isolated):
    _ready()
    d = _old_project(isolated)
    ext = srv.call("POST", "/api/projects", {"folder": str(d)})[1]
    new = srv.call("POST", "/api/projects", {"name": "Throwaway"})[1]
    for x in (ext, new):
        _idle(srv.app, x["slug"])
    # hide / unhide
    assert srv.call("POST", f"/api/projects/{new['slug']}/hide", {})[0] == 200
    st = srv.call("GET", "/api/state")[1]
    assert next(x for x in st["projects"] if x["slug"] == new["slug"])["hidden"] is True
    assert new["slug"] not in [r["slug"] for r in srv.call("GET", "/api/work")[1]["rows"]]
    srv.call("POST", f"/api/projects/{new['slug']}/unhide", {})
    assert not next(x for x in srv.call("GET", "/api/state")[1]["projects"] if x["slug"] == new["slug"])["hidden"]
    # remove keeps the files (existing folder only)
    assert srv.call("POST", f"/api/projects/{new['slug']}/delete", {"mode": "remove"})[0] == 400
    code, r = srv.call("POST", f"/api/projects/{ext['slug']}/delete", {"mode": "remove"})
    assert code == 200 and "kept" in r["message"]
    # 0.11.1: the history stays in the folder so opening it again brings the project back
    assert (d / "src" / "main.py").exists() and (d / ".aiteam").exists()
    assert ext["slug"] not in [x["slug"] for x in srv.call("GET", "/api/state")[1]["projects"]]
    # delete everything needs the name typed
    folder = projects.project_dir(new["slug"])
    assert srv.call("POST", f"/api/projects/{new['slug']}/delete", {"mode": "all", "confirm": "nope"})[0] == 400
    code, r = srv.call("POST", f"/api/projects/{new['slug']}/delete", {"mode": "all", "confirm": "Throwaway"})
    assert code == 200 and not folder.exists()
    srv.app.hub.publish(new["slug"], {"type": "notice", "text": "late event"})
    projects.update(new["slug"], updated=1)
    assert not folder.exists(), "a late event must not re-create a deleted project"
    assert srv.call("GET", "/api/state")[1]["projects"] == []


def test_delete_refused_while_working(isolated):
    _ready()
    p = projects.create("Busy")

    async def script(i, prompt, c):
        ok, res, msgs, _ = await c.use_tool("PowerShell", {"command": "mkdir build"})
        for m in msgs:
            yield m

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(p["slug"], "go")
    assert _wait(lambda: eng.project_state(p["slug"]) == "needs you")
    eng.stop(p["slug"])
    assert _wait(lambda: eng.project_state(p["slug"]) == "idle")
    eng.forget(p["slug"])
    assert p["slug"] not in eng.sessions
    eng.shutdown()


# ------------------------------------------------------------------------------- idle state
def test_project_state_idle_working(isolated):
    _ready()
    p = projects.create("Idle")
    gate = {"go": False}

    async def script(i, prompt, c):
        while not gate["go"]:
            await c.options.can_use_tool.__self__.sleep(0.02)
        yield say("done")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    assert eng.project_state(p["slug"]) == "idle"
    eng.send_message(p["slug"], "go")
    assert _wait(lambda: eng.project_state(p["slug"]) == "working")
    gate["go"] = True
    assert _wait(lambda: eng.project_state(p["slug"]) == "idle")
    projects.update(p["slug"], paused=True)
    assert eng.project_state(p["slug"]) == "paused"
    eng.shutdown()


# ------------------------------------------------------------------------------- Mat the lead architect
DECISIONS = [{"topic": "Data refresh", "decision": "Download prices daily at 7:00 and on start",
              "reason": "prices change once a day"},
             {"topic": "Database", "decision": "SQLite, encrypted at rest; key in Windows Credential Manager",
              "reason": "the data includes account numbers"}]


def test_mat_architecture_flow(isolated):
    _ready()
    p = projects.create("Prices")
    slug = p["slug"]
    out = {"personas": []}

    async def script(i, prompt, c):
        sess = c.options.can_use_tool.__self__
        system = c.options.system_prompt["append"]
        who = "Mat" if system.startswith("You are Mat") else "Fred"
        out["personas"].append((who, prompt[:40]))
        names = set(sess.tool_handlers)
        if prompt == "requirements done":
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__request_plan_approval",
                                                {"title": "P", "plan_markdown": "x", "iterations": [{"name": "A"}]})
            out["early_plan"] = res
            out["fred_tools"] = names
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__handoff_to_architect", {"summary": "A price watcher", "challenge_skipped_because": "test"})
            out["handoff"] = res
            return
        if who == "Mat" and prompt.startswith("[App] Fred has handed"):
            out["mat_tools"] = names
            out["mat_resume"] = c.options.resume
            yield say("I'm Mat, the lead architect. Should prices refresh daily or hourly? I recommend daily.")
            return
        if who == "Mat" and prompt == "daily, and encrypt it":
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__request_architecture_approval", {
                "title": "Architecture: Prices", "architecture_markdown": "# Architecture\n\n## Overview\nDesktop app.",
                "decisions": []})
            out["no_decisions"] = res
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__request_architecture_approval", {
                "title": "Architecture: Prices", "architecture_markdown": "# Architecture\n\n## Overview\nDesktop app.",
                "decisions": DECISIONS})
            out["first_answer"] = res
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__request_architecture_approval", {
                "title": "Architecture: Prices v2", "architecture_markdown": "# Architecture\n\n## Overview\nDesktop app.",
                "decisions": DECISIONS})
            out["approved"] = res
            return
        if who == "Fred" and prompt.startswith("[App] You are Fred again. The user APPROVED"):
            ok, res, msgs, _ = await c.use_tool("mcp__aiteam__request_plan_approval",
                                                {"title": "P", "plan_markdown": "x", "iterations": [{"name": "A"}]})
            out["plan"] = res
            return
        yield say("ok")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    sess = eng.session(slug)
    assert sess.persona == "Fred"
    eng.send_message(slug, "requirements done")
    assert _wait(lambda: any(e["type"] == "message" and e.get("speaker") == "Mat" for e in hub.history(slug)))
    assert _wait(lambda: not eng.is_busy(slug))
    assert projects.load(slug)["phase"] == "architecture" and sess.persona == "Mat"
    assert "not been approved" in out["early_plan"]
    assert "handoff_to_architect" in out["fred_tools"] and "request_architecture_approval" not in out["fred_tools"]
    assert out["mat_tools"] == {"check_environment", "ask_choice", "request_architecture_approval",
                                "return_to_fred", "ask_council"}
    assert out["mat_resume"] == "sess-1", "Mat continues the same conversation"
    handoff = [e for e in hub.history(slug) if e["type"] == "handoff"]
    assert handoff[0]["from"] == "Fred" and handoff[0]["to"] == "Mat" and handoff[0]["summary"] == "A price watcher"
    team = {r["name"]: r["kind"] for r in sess.team_snapshot()}
    assert team["Mat"] == "architect" and team["Fred"] == "lead"

    eng.send_message(slug, "daily, and encrypt it")
    assert _wait(lambda: any(e["type"] == "architecture" and e.get("status") == "pending" for e in hub.history(slug)))
    card = next(e for e in hub.history(slug) if e["type"] == "architecture")
    assert [d["topic"] for d in card["decisions"]] == ["Data refresh", "Database"]
    arch = (projects.project_dir(slug) / "ARCHITECTURE.md").read_text()
    assert "| Database | SQLite, encrypted at rest" in arch and "## Overview" in arch
    eng.respond(card["id"], {"approved": False, "feedback": "use hourly"})
    assert _wait(lambda: len([e for e in hub.history(slug) if e["type"] == "architecture"]) == 2)
    card2 = [e for e in hub.history(slug) if e["type"] == "architecture"][-1]
    assert projects.load(slug)["architecture_approved"] is False
    eng.respond(card2["id"], {"approved": True})
    assert _wait(lambda: any(e["type"] == "plan" and e.get("status") == "pending" for e in hub.history(slug)))
    p2 = projects.load(slug)
    assert p2["architecture_approved"] is True and p2["phase"] == "plan" and sess.persona == "Fred"
    plan = next(e for e in hub.history(slug) if e["type"] == "plan")
    eng.respond(plan["id"], {"approved": False, "feedback": "later"})
    assert _wait(lambda: not eng.is_busy(slug))
    eng.shutdown()
    assert "decisions" in out["no_decisions"]
    assert "did NOT approve" in out["first_answer"] and "use hourly" in out["first_answer"]
    assert "APPROVED the architecture" in out["approved"]
    statuses = [e["status"] for e in hub.history(slug) if e["type"] == "architecture"]
    assert statuses == ["changes requested", "approved"]
    assert [w for w, _ in out["personas"]] == ["Fred", "Mat", "Mat", "Fred"]
    assert [(e["from"], e["to"]) for e in hub.history(slug) if e["type"] == "handoff"] == [("Fred", "Mat"), ("Mat", "Fred")]


def test_mat_returns_to_fred_and_legacy_projects(isolated):
    _ready()
    p = projects.create("Back")
    projects.update(p["slug"], phase="architecture")
    seen = []

    async def script(i, prompt, c):
        system = c.options.system_prompt["append"]
        seen.append("Mat" if system.startswith("You are Mat") else "Fred")
        if prompt == "actually I want more features":
            await c.use_tool("mcp__aiteam__return_to_fred", {"reason": "the user wants more features"})
            return
        yield say("ok")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(p["slug"], "actually I want more features")
    assert _wait(lambda: len(seen) == 2 and not eng.is_busy(p["slug"]))
    eng.shutdown()
    assert seen == ["Mat", "Fred"]
    assert projects.load(p["slug"])["phase"] == "discover"
    assert "wants more features" in FakeClient.instances[-1].prompts[0]
    # a project from before 0.6.0 that already had an approved plan does not need an architecture approval
    assert projects.architecture_approved({"plan_approved_at": 1.0})
    assert not projects.architecture_approved({"plan_approved_at": 1.0, "architecture_approved": False})
    assert not projects.architecture_approved({})
    assert "Phase 1b" in prompts.PROJECT and "{existing}" in prompts.ARCHITECT


def test_existing_folder_details(isolated):
    import os
    import stat
    import subprocess
    from app import paths
    # inside the projects folder: becomes an ordinary project (no duplicate in the list)
    ws = settings.workspace()
    (ws / "notes-app").mkdir(parents=True)
    p = projects.create_from_folder(str(ws / "notes-app"))
    assert p["slug"] == "notes-app" and "notes-app" not in projects.external()
    assert [x["slug"] for x in projects.list_projects()] == ["notes-app"]
    # Git repository without .gitignore: .aiteam is excluded through .git/info/exclude, and a deleted
    # project with read-only Git files is removed completely
    d = _old_project(isolated)
    (d / ".gitignore").unlink()
    subprocess.run([paths.git_exe(), "init", "-q"], cwd=d, check=True)
    q = projects.create_from_folder(str(d))
    assert ".aiteam/" in (d / ".git" / "info" / "exclude").read_text() and not (d / ".gitignore").exists()
    ro = d / "locked.txt"
    ro.write_text("x")
    os.chmod(ro, stat.S_IREAD)
    projects.delete(q["slug"], "all")
    assert not d.exists()


# ------------------------------------------------------------------------------- workers in the background (0.6.1)
RECEIPT = ("Async agent launched successfully. (This tool result is internal metadata - never quote or paste any part "
           "of it.)\nagentId: a6775f4ddb68dc391 (internal ID - do not mention to user.)\nThe agent is working in the "
           "background. You will be notified automatically when it completes.")


def test_workers_forced_to_foreground(isolated):
    import asyncio
    from claude_agent_sdk import PermissionResultAllow
    _ready()
    p = projects.create("Fg")
    projects.update(p["slug"], plan_approved=True, architecture_approved=True)
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    sess = eng.session(p["slug"])
    ctx = type("Ctx", (), {"agent_id": None, "title": None, "description": None})()
    r = asyncio.run_coroutine_threadsafe(sess.can_use_tool(
        "Agent", {"subagent_type": "developer", "prompt": "x", "run_in_background": True}, ctx), eng.loop).result(5)
    assert isinstance(r, PermissionResultAllow) and r.updated_input["run_in_background"] is False
    r = asyncio.run_coroutine_threadsafe(sess.can_use_tool("Read", {"file_path": "a"}, ctx), eng.loop).result(5)
    assert r.updated_input is None
    eng.shutdown()
    assert "never run_in_background" in prompts.PROJECT


def test_background_receipt_is_not_a_report(isolated):
    from claude_agent_sdk import ToolResultBlock, UserMessage
    _ready()
    p = projects.create("Bg")
    slug = p["slug"]
    projects.update(slug, plan_approved=True, architecture_approved=True)
    gate = {"go": False}
    seen = {}

    async def script(i, prompt, c):
        if i == 0:
            ok, res, msgs, tid = await c.use_tool("ToolSearch", {"query": "select:Agent"})
            ok, res, msgs, tid = await c.use_tool("Agent", {"subagent_type": "developer", "description": "Iteration 1",
                                                            "prompt": "build"})
            yield UserMessage(content=[ToolResultBlock(tid, RECEIPT)])
            yield say("The developer is working; I'll continue once it reports back.")
            return
        seen["kickoff"] = prompt
        sess = c.options.can_use_tool.__self__
        seen["state_between"] = sess.engine.project_state(slug)
        while not gate["go"]:
            await sess.sleep(0.02)
        ok, res, msgs, tid = await c.use_tool("TaskOutput", {"task_id": "a6775f4ddb68dc391", "block": True})
        for m in msgs:
            yield m
        yield say("The developer finished: tests pass.")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(slug, "go")
    assert _wait(lambda: "kickoff" in seen)
    card = next(e for e in hub.history(slug) if e["type"] == "agent")
    assert card["status"] == "working" and "summary" not in card, "the receipt must not be shown as a report"
    dev = next(r for r in eng.session(slug).team_snapshot() if r["name"] == "Developer")
    assert dev["status"] == "working"
    assert seen["state_between"] == "working", "not idle while the worker is still busy"
    assert 'TaskOutput with task_id "a6775f4ddb68dc391"' in seen["kickoff"]
    acts = [e["text"] for e in hub.history(slug) if e["type"] == "activity"]
    assert not any("ToolSearch" in a for a in acts)
    fred = next(r for r in eng.session(slug).team_snapshot() if r["name"] == "Fred")
    assert "ToolSearch" not in fred.get("last", "")
    gate["go"] = True
    assert _wait(lambda: not eng.is_busy(slug))
    card = next(e for e in hub.history(slug) if e["type"] == "agent")
    assert card["status"] == "done" and card["summary"] == "ok"
    assert eng.project_state(slug) == "idle"
    dev = next(r for r in eng.session(slug).team_snapshot() if r["name"] == "Developer")
    assert dev["status"] == "idle" and dev["task"] == "Finished: Iteration 1"
    eng.shutdown()
