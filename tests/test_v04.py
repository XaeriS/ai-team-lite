"""0.4.0: brains + delegation, risk explanations, team view, limit auto-continue, work queue, tests view."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import brains, engine as engine_mod, projects, risk, secrets_store, settings, tools, usage
from app.events import EventHub
from fakeclaude import FakeClient, say


# ------------------------------------------------------------------------------- a stand-in brain (OpenAI-compatible)
class FakeBrain:
    def __init__(self):
        self.replies, self.requests = [], []
        brain = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path.endswith("/api/tags"):
                    return self._send(200, {"models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3.2:3b"}]})
                return self._send(200, {"data": [{"id": "m1"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                brain.requests.append(body)
                reply = brain.replies.pop(0) if brain.replies else "ready"
                if reply == "429":
                    return self._send(429, {"error": "quota"})
                self._send(200, {"choices": [{"message": {"content": reply}}],
                                 "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/"


@pytest.fixture()
def fake_brain(monkeypatch):
    fb = FakeBrain()
    monkeypatch.setitem(brains.PROVIDERS["ollama"], "base", fb.base)
    yield fb
    fb.httpd.shutdown()


def test_brain_quota_marks_exhausted(fake_brain):
    b = brains.add("ollama", "m")
    fake_brain.replies.append("429")
    with pytest.raises(brains.BrainError, match="out of quota"):
        brains.chat(b, "s", "x")
    assert not brains.usable(b), "an exhausted brain sits out the council"


def test_gemini_needs_key():
    b = brains.add("gemini", "gemini-2.5-flash")
    ok, msg = brains.test(b)
    assert not ok and "no API key" in msg
def test_risk_explanations(tmp_path):
    root = tmp_path
    inside = lambda p: str(p).startswith(str(root)) or not Path(p).is_absolute()
    e = risk.explain("PowerShell", {"command": ".venv\\Scripts\\python -m pytest -q"}, root, inside)
    assert e["risk"] in ("low", "medium") and "tests" in e["what"].lower()
    e = risk.explain("PowerShell", {"command": "Remove-Item -Recurse -Force build"}, root, inside)
    assert e["risk"] == "high" and "Deletes" in e["what"] and "cannot be recovered" in e["risk_reason"]
    e = risk.explain("PowerShell", {"command": "winget install Python.Python.3.12"}, root, inside)
    assert e["risk"] == "high" and "whole PC" in e["risk_reason"]
    e = risk.explain("Bash", {"command": "frobnicate --all"}, root, inside)
    assert e["risk"] == "medium" and "could not recognise" in e["risk_reason"]
    (root / "main.py").write_text("a\nb\n")
    e = risk.explain("Write", {"file_path": str(root / "main.py"), "content": "x"}, root, inside)
    assert e["risk"] == "medium" and "Replaces the whole file main.py (2 lines" in e["what"]
    e = risk.explain("Write", {"file_path": str(root / "new.py"), "content": "x"}, root, inside)
    assert e["risk"] == "low" and "Creates a new file new.py" in e["what"]
    e = risk.explain("mcp__aiteam__github_create_repo", {"private": False}, root, inside)
    assert "PUBLIC" in e["what"] and e["risk"] == "medium"


# ------------------------------------------------------------------------------- engine features
def _engine(script):
    hub = EventHub(projects.history_file)
    return hub, engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))


def _wait(cond, secs=10):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    return False


def _ready():
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    settings.save({"setup_complete": True})


def test_approval_card_has_explanation_and_team_updates(isolated):
    _ready()
    p = projects.create("Cards")
    seen = {}

    async def script(i, prompt, c):
        yield say("What this does: deletes the old build folder. Risk: high - files cannot be recovered.")
        ok, res, msgs, tid = await c.use_tool("Agent", {"subagent_type": "developer", "description": "Build v1", "prompt": "go"})
        seen["dev_working"] = [r for r in c.options.can_use_tool.__self__.team_snapshot() if r["name"] == "Developer"][0]["status"]
        ok, res, inner, _ = await c.use_tool("PowerShell", {"command": "Remove-Item -Recurse build"}, parent=tid)
        for m in inner:
            yield m
        yield msgs[0]

    hub, eng = _engine(script)
    eng.send_message(p["slug"], "go")
    assert _wait(lambda: eng.pending_for(p["slug"]))
    first = [e for e in hub.history(p["slug"]) if e["type"] == "approval"][-1]
    assert first["tool"] == "Agent" and first["risk"] == "low" and "deletes the old build" in first["says"]
    eng.respond(first["id"], {"approved": True})
    assert _wait(lambda: len([e for e in hub.history(p["slug"]) if e["type"] == "approval"]) == 2)
    card = [e for e in hub.history(p["slug"]) if e["type"] == "approval"][-1]
    assert card["risk"] == "high" and "Deletes" in card["what"] and card["who"] == "Developer"
    team = eng.session(p["slug"]).team_snapshot()
    dev = next(r for r in team if r["name"] == "Developer")
    assert dev["status"] == "question" and "approval" in dev["last"]
    eng.respond(card["id"], {"approved": False})
    assert _wait(lambda: not eng.is_busy(p["slug"]))
    eng.shutdown()
    assert seen["dev_working"] == "working"
    team = eng.session(p["slug"]).team_snapshot()
    dev = next(r for r in team if r["name"] == "Developer")
    assert dev["status"] == "idle" and dev["task"] == "Finished: Build v1" and len(dev["recent"]) >= 2
    assert next(r for r in team if r["name"] == "Fred")["status"] == "idle"


def test_worker_text_shown_on_card(isolated):
    _ready()
    p = projects.create("Says")

    async def script(i, prompt, c):
        yield say("What this does: installs requests. Risk: medium - downloads code.")
        ok, res, msgs, _ = await c.use_tool("PowerShell", {"command": ".venv\\Scripts\\pip install requests"})
        for m in msgs:
            yield m

    hub, eng = _engine(script)
    eng.send_message(p["slug"], "go")
    assert _wait(lambda: eng.pending_for(p["slug"]))
    card = [e for e in hub.history(p["slug"]) if e["type"] == "approval"][-1]
    assert "installs requests" in card["says"] and card["risk"] == "medium"
    eng.stop(p["slug"])
    _wait(lambda: not eng.is_busy(p["slug"]))
    eng.shutdown()


def test_limit_stops_and_auto_continues(isolated):
    _ready()
    p = projects.create("Limits")
    from claude_agent_sdk import ResultMessage

    async def script(i, prompt, c):
        if i == 0:
            yield say("Working on it")
            return
        yield say("Resuming where I stopped.")

    class LimitClient(FakeClient):
        async def receive_response(self):
            if len(self.prompts) == 1:
                yield say("Starting the build")
                yield ResultMessage(subtype="error", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1,
                                    session_id="s", result="Claude usage limit reached", api_error_status=429)
                return
            async for m in super().receive_response():
                yield m

    usage.record_limit(type("I", (), {"rate_limit_type": "five_hour", "status": "rejected", "utilization": 1.0,
                                      "resets_at": time.time() + 1})())  # resets in 1 s; high priority continues 10 s later
    projects.update(p["slug"], priority="high")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: LimitClient(o, script))
    eng.send_message(p["slug"], "build it")
    assert _wait(lambda: any(e["type"] == "limit" for e in hub.history(p["slug"])))
    card = next(e for e in hub.history(p["slug"]) if e["type"] == "limit")
    assert card["status"] == "waiting" and card["auto"] and not any(e["type"] == "error" for e in hub.history(p["slug"]))
    assert eng.work_overview()[0]["state"] == "limit"
    assert _wait(lambda: any(e["type"] == "message" and "Resuming" in e.get("text", "") for e in hub.history(p["slug"])), 25)
    card = next(e for e in hub.history(p["slug"]) if e["type"] == "limit")
    assert card["status"] == "continued" and "automatically" in card["note"]
    assert engine_mod.CONTINUE_TEXT in FakeClient.instances[-1].prompts[-1]
    eng.shutdown()


def test_limit_cancel_and_manual_continue(isolated):
    _ready()
    p = projects.create("Manual")
    hub, eng = _engine(lambda *a: None)
    eng.on_limit(p["slug"], {"type": "five_hour", "label": "x", "resets_at": time.time() + 3600})
    time.sleep(0.1)
    eng.cancel_auto_continue(p["slug"])
    card = next(e for e in hub.history(p["slug"]) if e["type"] == "limit")
    assert card["auto"] is False and "cancelled" in card["note"]
    assert p["slug"] in eng.limit_timers
    eng.shutdown()


def test_parallel_limit_priority_pause_resume(isolated):
    _ready()
    settings.save({"max_parallel": 1})
    a, b, c = projects.create("A"), projects.create("B"), projects.create("C")
    projects.update(c["slug"], priority="high")
    gate = {"open": False}

    async def script(i, prompt, cl):
        import asyncio
        while not gate["open"]:
            await asyncio.sleep(0.02)
        yield say(f"done: {prompt[:20]}")

    hub, eng = _engine(script)
    assert eng.send_message(a["slug"], "first") == "started"
    assert _wait(lambda: eng.is_busy(a["slug"]))
    assert eng.send_message(b["slug"], "second") == "waiting"
    assert eng.send_message(c["slug"], "third") == "waiting"
    rows = {r["slug"]: r["state"] for r in eng.work_overview()}
    assert rows == {a["slug"]: "working", b["slug"]: "waiting", c["slug"]: "waiting"}
    order = []
    orig = eng._start
    eng._start = lambda slug, text, hidden: (order.append(slug), orig(slug, text, hidden))
    gate["open"] = True
    assert _wait(lambda: len(order) >= 2 and not any(eng.is_busy(s["slug"]) for s in (a, b, c)), 15)
    assert order[:2] == [c["slug"], b["slug"]], "high priority starts first"
    # pause holds new messages; resume delivers them
    eng.pause(a["slug"])
    assert eng.send_message(a["slug"], "while paused") == "paused"
    assert {r["slug"]: r["state"] for r in eng.work_overview()}[a["slug"]] == "paused"
    assert eng.resume(a["slug"]) == "started"
    assert _wait(lambda: not eng.is_busy(a["slug"]))
    last_prompt = [cl for cl in FakeClient.instances if cl.options.cwd.endswith(a["slug"])][-1].prompts[-1]
    assert "resumed" in last_prompt and "while paused" in last_prompt
    eng.shutdown()


# ------------------------------------------------------------------------------- tests view
def test_test_results_and_screenshot_linking(isolated):
    p = projects.create("T")
    folder = projects.project_dir(p["slug"])
    tools.add_test_result(folder, {"name": "adds expense", "requirement": "REQ-1", "status": "passed", "details": ""})
    tools.add_test_result(folder, {"name": "adds expense", "screenshot": "reports/screenshots/a.png", "caption": "list"})
    tools.add_test_result(folder, {"name": "orphan shot", "screenshot": "reports/screenshots/b.png"})
    r = tools.load_tests(folder)
    assert r[0]["status"] == "passed" and r[0]["screenshot"] == "reports/screenshots/a.png" and r[0]["requirement"] == "REQ-1"
    assert r[1]["status"] == "screenshot" and len(r) == 2
