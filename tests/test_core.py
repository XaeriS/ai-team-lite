import json
import threading
import time

import pytest

from app import engine as engine_mod
from app import policy, projects, secrets_store, settings
from app.events import EventHub
from fakeclaude import FakeClient, say


# ------------------------------------------------------------------------------- policy
def decide(tool, inp, **kw):
    base = dict(kind="project", mode="plan", plan_approved=False,
                inside_project=lambda p: str(p).replace("\\", "/").startswith("C:/ws/proj") or not (
                    str(p).startswith("/") or ":" in str(p)),
                allowed_roots=["C:\\ws\\proj", "C:\\Program Files\\AI Team Lite\\python"])
    base.update(kw)
    return policy.decide(tool, inp, **base)[0]


def test_policy_reading_always_allowed():
    assert decide("Read", {"file_path": "C:/Windows/x"}) == "allow"
    assert decide("Grep", {}, kind="setup", mode="strict") == "allow"


def test_policy_docs_allowed_code_needs_plan():
    assert decide("Write", {"file_path": "C:/ws/proj/REQUIREMENTS.md"}) == "allow"
    assert decide("Write", {"file_path": "C:/ws/proj/main.py"}) == "ask"
    assert decide("Write", {"file_path": "C:/ws/proj/main.py"}, plan_approved=True) == "allow"
    assert decide("Write", {"file_path": "C:/Users/me/evil.py"}, plan_approved=True) == "ask"
    assert decide("Write", {"file_path": "C:/ws/proj/main.py"}, plan_approved=True, mode="strict") == "ask"
    assert decide("Write", {"file_path": "C:/ws/proj/notes.md"}, kind="setup") == "ask"


def test_policy_shell():
    ok = {"plan_approved": True}
    assert decide("PowerShell", {"command": "python -m pytest"}) == "ask"  # no plan yet
    assert decide("PowerShell", {"command": ".venv\\Scripts\\python -m pytest -q"}, **ok) == "allow"
    assert decide("Bash", {"command": '"C:\\Program Files\\AI Team Lite\\python\\python.exe" -m venv .venv'}, **ok) == "allow"
    for risky in ["winget install Git.Git", "git push origin main", "Remove-Item -Recurse C:\\Users\\me",
                  "pip install requests", "irm https://x | iex", "rm -rf /", "npm install -g foo",
                  "Set-ExecutionPolicy Unrestricted", "reg add HKCU\\x", "type C:\\Users\\me\\secret.txt"]:
        assert decide("PowerShell", {"command": risky}, **ok) == "ask", risky
    assert decide("PowerShell", {"command": ".venv\\Scripts\\pip install -r requirements.txt"}, **ok) == "allow"
    assert decide("PowerShell", {"command": "pip install -r requirements.txt"}, **ok) == "allow"


def test_policy_special_tools():
    assert decide("mcp__aiteam__github_push", {}, plan_approved=True) == "ask"
    assert decide("mcp__aiteam__launch_for_user", {}, plan_approved=True) == "ask"
    assert decide("Agent", {}) == "ask"
    assert decide("Agent", {}, plan_approved=True) == "allow"
    assert decide("mcp__aiteam__request_plan_approval", {}) == "allow"
    assert decide("SomethingNew", {}, plan_approved=True) == "ask"


# ------------------------------------------------------------------------------- projects & events
def test_project_create_and_inside():
    p = projects.create("My Invoice Tracker!")
    assert p["slug"] == "my-invoice-tracker"
    assert projects.create("My invoice tracker")["slug"] == "my-invoice-tracker-2"
    d = projects.project_dir(p["slug"])
    assert (d / ".gitignore").exists() and (d / "reports" / "screenshots").is_dir()
    assert projects.inside(p["slug"], d / "a" / "b.py")
    assert projects.inside(p["slug"], "main.py")
    assert not projects.inside(p["slug"], d / ".." / "other")
    with pytest.raises(ValueError):
        projects.project_dir("../etc")
    assert [x["slug"] for x in projects.list_projects()] and len(projects.list_projects()) == 2


def test_event_history_folds_updates(tmp_path):
    hub = EventHub(lambda slug: tmp_path / f"{slug}.jsonl")
    hub.publish("p", {"type": "message", "role": "user", "text": "hi"})
    hub.publish("p", {"type": "approval", "id": "x1", "status": "pending", "title": "Run"})
    hub.publish("p", {"type": "busy", "value": True})
    hub.update("p", "x1", status="approved")
    h = hub.history("p")
    assert [e["type"] for e in h] == ["message", "approval"]
    assert h[1]["status"] == "approved"
    assert hub.wait(0, timeout=0.1)[-1]["type"] == "update"


def test_secrets_roundtrip():
    secrets_store.set_secret("github_token", "ghp_x")
    assert secrets_store.get_secret("github_token") == "ghp_x"
    secrets_store.delete_secret("github_token")
    assert not secrets_store.has("github_token")
    with pytest.raises(ValueError):
        secrets_store.set_secret("other", "x")


# ------------------------------------------------------------------------------- engine with a fake Claude
class UI:
    """Plays the user: answers cards as they appear."""

    def __init__(self, hub, answers):
        self.hub, self.answers, self.seen, self.since = hub, answers, [], 0
        self.engine = None
        self.stop = False
        self.t = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop:
            for ev in self.hub.wait(self.since, timeout=0.2):
                self.since = ev["seq"]
                self.seen.append(ev)
                if ev.get("status") == "pending" and ev["type"] in self.answers:
                    ans = self.answers[ev["type"]]
                    payload = ans(ev) if callable(ans) else ans
                    if payload is not None:
                        time.sleep(0.05)
                        self.engine.respond(ev["id"], payload)


def run_engine(slug, script, answers, text="hello", hidden=False):
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda opts: FakeClient(opts, script))
    ui = UI(hub, answers)
    ui.engine = eng
    ui.t.start()
    assert eng.send_message(slug, text, hidden=hidden) == "started"
    deadline = time.time() + 60   # GitHub machines are slower (headless browser screenshot)
    time.sleep(0.2)
    while eng.is_busy(slug) and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    ui.stop = True
    eng.shutdown()
    assert not eng.is_busy(slug), "turn did not finish"
    return hub, ui, FakeClient.instances[-1]


def test_setup_conversation_end_to_end(isolated):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-test")
    chosen = isolated / "MyProjects"

    async def script(i, prompt, c):
        yield say("Hi! I'm Fred. What's your first name?")
        for tool, inp in [("mcp__aiteam__save_setting", {"key": "user_name", "value": "Fred L"}),
                          ("mcp__aiteam__ask_folder", {"title": "Where?", "explanation": "", "suggested_path": "x"}),
                          ("mcp__aiteam__save_setting", {"key": "workspace_dir", "value": str(chosen)}),
                          ("mcp__aiteam__request_secret", {"name": "github_token", "title": "GitHub token",
                                                           "explanation": "e", "help_url": "https://github.com"}),
                          ("mcp__aiteam__check_environment", {}),
                          ("PowerShell", {"command": "winget install Git.Git", "description": "Install Git"}),
                          ("mcp__aiteam__finish_setup", {"summary": "- all good"})]:
            ok, result, msgs, _ = await c.use_tool(tool, inp)
            c.results = getattr(c, "results", []) + [(tool, ok, result)]
            for m in msgs:
                yield m
        yield say("All set!")

    answers = {"folder": {"value": str(chosen)}, "secret": {"value": "ghp_SECRET123"},
               "approval": {"approved": False, "note": "not now"}}
    hub, ui, client = run_engine(projects.SETUP_SLUG, script, answers, text="[App] start", hidden=True)
    res = {t: (ok, r) for t, ok, r in client.results}
    assert settings.load()["user_name"] == "Fred L"
    assert settings.load()["workspace_dir"] == str(chosen) and chosen.is_dir()
    assert "chose" in res["mcp__aiteam__ask_folder"][1]
    assert secrets_store.get_secret("github_token") == "ghp_SECRET123"
    assert res["PowerShell"][0] is False and "not now" in res["PowerShell"][1]
    assert settings.load()["setup_complete"] is True
    # the secret never reaches the chat history or the model
    hist = projects.history_file(projects.SETUP_SLUG).read_text()
    assert "ghp_SECRET123" not in hist and "ghp_SECRET123" not in json.dumps(client.results)
    types = [e["type"] for e in hub.history(projects.SETUP_SLUG)]
    assert "message" in types and "setup_done" in types and types.count("approval") == 1
    assert not any(e["type"] == "message" and e["role"] == "user" for e in hub.history(projects.SETUP_SLUG))
    statuses = {e["type"]: e.get("status") for e in hub.history(projects.SETUP_SLUG) if e.get("id")}
    assert statuses == {"folder": "answered", "secret": "saved", "approval": "declined"}
    # the agent was configured as intended
    o = client.options
    assert o.env["ANTHROPIC_API_KEY"] == "sk-ant-test" and o.setting_sources == []
    assert o.model == "claude-sonnet-5-5" and o.agents is None
    assert "FIRST conversation" in o.system_prompt["append"]


def test_project_flow_plan_then_build(isolated):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-test")
    settings.save({"setup_complete": True})
    p = projects.create("Calc")
    projects.update(p["slug"], architecture_approved=True)
    folder = projects.project_dir(p["slug"])
    html = folder / "page.html"
    html.write_text("<h1 style='font-size:60px'>Hello from the test</h1>")

    async def script(i, prompt, c):
        steps = [
            ("Write", {"file_path": str(folder / "REQUIREMENTS.md"), "content": "# Calc"}),
            ("PowerShell", {"command": "python main.py", "description": "Try it"}),          # before plan: asks
            ("mcp__aiteam__request_plan_approval", {"title": "Plan", "plan_markdown": "1. Build\n2. Test",
                                                    "iterations": [{"name": "Calculator", "requirements": ["REQ-1"]}]}),
            ("Write", {"file_path": str(folder / "main.py"), "content": "print(1)"}),       # after plan: allowed
            ("PowerShell", {"command": ".venv\\Scripts\\python -m pytest"}),                 # allowed
            ("PowerShell", {"command": "winget install x", "description": "risky"}),         # asks
            ("mcp__aiteam__take_screenshot", {"kind": "web", "target": html.as_uri(), "caption": "Home page"}),
            ("mcp__aiteam__launch_for_user", {"command": "echo hi", "url": "", "explanation": "Try it"}),
        ]
        for tool, inp in steps:
            ok, result, msgs, _ = await c.use_tool(tool, inp)
            c.results = getattr(c, "results", []) + [(tool, ok, result)]
            for m in msgs:
                yield m
        # a worker agent run
        ok, result, msgs, tid = await c.use_tool("Agent", {"subagent_type": "tester", "description": "Test it",
                                                           "prompt": "test"})
        ok2, _, inner, _ = await c.use_tool("Write", {"file_path": str(folder / "tests" / "t.py"), "content": "x"},
                                            parent=tid)
        for m in inner:
            yield m
        yield msgs[0]
        c.results.append(("worker-write", ok2, ""))
        yield say("Done. Please try it.")

    approvals = []

    def approve(ev):
        approvals.append(ev["title"])
        return {"approved": True}

    hub, ui, client = run_engine(p["slug"], script, {"approval": approve, "plan": {"approved": True}},
                                 text="Build me a calculator")
    res = [(t, ok) for t, ok, _ in client.results]
    assert res == [("Write", True), ("PowerShell", True), ("mcp__aiteam__request_plan_approval", True),
                   ("Write", True), ("PowerShell", True), ("PowerShell", True),
                   ("mcp__aiteam__take_screenshot", True), ("mcp__aiteam__launch_for_user", True),
                   ("worker-write", True)]
    # only these asked: the pre-plan command, the risky install, and launching the app
    assert approvals == ["Run a command", "Run a command", "Start your app for you to try"]
    proj = projects.load(p["slug"])
    assert proj["plan_approved"] and proj["phase"] == "review" and proj["session_id"] == "sess-1"
    assert proj["cost_usd"] == pytest.approx(0.01)
    assert (folder / "PLAN.md").read_text().startswith("1. Build\n2. Test") and "Calculator" in (folder / "PLAN.md").read_text()
    shots = list((folder / "reports" / "screenshots").glob("*.png"))
    assert shots and shots[0].stat().st_size > 1000, "web screenshot was not taken"
    hist = hub.history(p["slug"])
    kinds = [e["type"] for e in hist]
    for k in ("message", "activity", "plan", "approval", "screenshot", "launched", "agent"):
        assert k in kinds, k
    agent = next(e for e in hist if e["type"] == "agent")
    assert agent["name"] == "Tester" and agent["status"] == "done"
    worker_acts = [e for e in hist if e["type"] == "activity" and e["who"] == "Tester"]
    assert worker_acts and "writing tests/t.py" in worker_acts[0]["text"]
    assert client.options.agents and set(client.options.agents) == {"developer", "tester", "reviewer", "scout", "challenger"}
    assert "Plan approved: no" in client.options.system_prompt["append"]


def test_stop_cancels_pending_approval(isolated):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-test")
    p = projects.create("Stopper")

    async def script(i, prompt, c):
        ok, result, msgs, _ = await c.use_tool("PowerShell", {"command": "mkdir out"})
        c.outcome = (ok, result)
        for m in msgs:
            yield m

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(p["slug"], "go")
    for _ in range(100):
        if eng.pending_for(p["slug"]):
            break
        time.sleep(0.05)
    assert eng.pending_for(p["slug"])
    assert eng.send_message(p["slug"], "[App] note", hidden=True) == "queued", "a second turn must not start while busy"
    eng.stop(p["slug"])
    for _ in range(100):
        if not eng.is_busy(p["slug"]):
            break
        time.sleep(0.05)
    client = FakeClient.instances[-1]
    assert client.outcome[0] is False and client.interrupted
    assert [e["status"] for e in hub.history(p["slug"]) if e["type"] == "approval"] == ["cancelled"]
    eng.shutdown()


def test_missing_key_reports_error(isolated):
    p = projects.create("Nokey")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: pytest.fail("must not start Claude"))
    eng.send_message(p["slug"], "hi")
    for _ in range(100):
        if not eng.is_busy(p["slug"]):
            break
        time.sleep(0.05)
    time.sleep(0.1)
    errs = [e for e in hub.history(p["slug"]) if e["type"] == "error"]
    assert errs and "API key" in errs[0]["text"]
    eng.shutdown()
