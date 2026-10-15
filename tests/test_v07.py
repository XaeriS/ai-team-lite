"""0.7.0: Claude models as brains + model choice, usage/cost, pipeline gates and master plan, launch button."""
import asyncio
import time

import pytest

from app import brains, engine as engine_mod, iterations, models, projects, secrets_store, settings, usage
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


CTX = type("Ctx", (), {"agent_id": None, "title": None, "description": None})()


# ------------------------------------------------------------------------------- models (items 8-10)
def test_engine_applies_models(isolated):
    _ready()
    p = projects.create("Models")
    projects.update(p["slug"], plan_approved=True, architecture_approved=True)
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    sess = eng.session(p["slug"])

    def allow(inp):
        return asyncio.run_coroutine_threadsafe(sess.can_use_tool("Agent", inp, CTX), eng.loop).result(5).updated_input

    assert allow({"subagent_type": "developer", "prompt": "x", "model": "haiku"})["model"] == "haiku"
    assert allow({"subagent_type": "reviewer", "prompt": "x"})["model"] == "haiku", "default for the role"
    assert allow({"subagent_type": "developer", "prompt": "x", "model": "claude-opus-5-5"})["model"] == "opus"
    settings.save({"model_auto": False, "role_models": {"Developer": "opus"}})  # old settings are ignored in Lite
    assert allow({"subagent_type": "developer", "prompt": "x"})["model"] == "sonnet"
    opts = sess.build_options()
    assert opts.model == "claude-sonnet-5-5" and opts.agents["developer"].model == "sonnet"
    projects.update(p["slug"], phase="architecture")
    assert sess.build_options().model == "claude-opus-5-5", "Mat starts on Opus"
    eng.shutdown()
# ------------------------------------------------------------------------------- usage and cost (13-15)
class Info:
    def __init__(self, **kw):
        self.__dict__.update({"status": "allowed", "resets_at": None, "rate_limit_type": "five_hour",
                              "utilization": None, **kw})


def test_plan_percent_is_kept(isolated):
    reset = time.time() + 3600
    usage.record_limit(Info(status="allowed_warning", utilization=0.9, resets_at=reset))
    e = usage.record_limit(Info(status="allowed_warning", resets_at=reset))  # still warning, no percentage
    assert e["percent"] == 90.0 and usage.plan()["five_hour"]["percent"] == 90.0
    assert e["percent_at"] <= time.time()
    e = usage.record_limit(Info(status="allowed_warning"))  # no reset time either
    assert e["percent"] == 90.0 and e["resets_at"] == pytest.approx(reset)
    # 0.11.1 (item 61): "back to normal" after a warning means usage went down - the old 90% is not kept
    e = usage.record_limit(Info(status="allowed", resets_at=reset))
    assert e["percent"] is None and e["below_warning"]
    e = usage.record_limit(Info(status="rejected", resets_at=reset))
    assert e["percent"] == 100.0
    usage.record_limit(Info(rate_limit_type="seven_day", status="rejected"))
    assert usage.plan()["seven_day"]["percent"] == 100.0
    usage.record_limit(Info(utilization=0.3, resets_at=reset + 18000))  # a new window
    assert usage.plan()["five_hour"]["percent"] == 30.0


def test_usage_per_brain_and_cost(isolated):
    usage.record_tokens("calc", "Developer", {"input_tokens": 1_000_000, "output_tokens": 100_000}, model="claude-sonnet-5-5")
    usage.record_tokens("calc", "Reviewer", {"input_tokens": 1_000_000}, model="claude-haiku-5-5")
    usage.record_tokens("calc", "Ollama · qwen", {"input_tokens": 5000}, model="ollama:qwen3:8b")
    usage.record_tokens("other", "Fred", {"input_tokens": 2_000_000}, model="")  # before 0.7.0: Sonnet
    from app import analytics
    s = analytics.report(None, "all")
    per = {x["slug"]: x for x in s["projects"]}
    agents = {x["name"]: x for x in s["split"]["by_agent"]}
    assert agents["Developer"]["cost"] == pytest.approx(2 + 1) and agents["Reviewer"]["cost"] == pytest.approx(0.10)
    assert agents["Ollama · qwen"]["cost"] == 0 and agents["Fred"]["cost"] == pytest.approx(4)   # no model: Sonnet
    assert per["calc"]["cost"] == pytest.approx(3.10) and s["total"]["cost"] == pytest.approx(7.10)
    settings.save({"prices": {"claude-haiku-5-5": {"in": 1, "out": 5, "cache_read": 0.1, "cache_write": 1.25}}})
    assert {x["slug"]: x for x in analytics.report(None)["projects"]}["calc"]["cost"] == pytest.approx(4.0)
    assert models.price_for("mystery-model") is None


def test_cost_estimate_from_iterations(isolated):
    p = projects.create("Est")
    slug = p["slug"]
    now = time.time()
    projects.update(slug, plan_approved=True, iterations=[
        {"n": 1, "name": "A", "status": "done", "started": now - 100, "finished": now - 50},
        {"n": 2, "name": "B", "status": "planned"}, {"n": 3, "name": "C", "status": "planned"}])
    usage._log_file().write_text("")
    import json
    with open(usage._log_file(), "a") as f:
        f.write(json.dumps({"t": now - 70, "day": time.strftime("%Y-%m-%d"), "project": slug, "who": "Developer",
                            "model": "claude-sonnet-5-5", "in": 500_000, "out": 0, "cache_read": 0, "cache_write": 0,
                            "cost": 0}) + "\n")
    e = iterations.cost_estimate(slug)
    assert e["so_far"] == 1.0 and e["per_iteration"] == 1.0 and e["remaining_iterations"] == 2
    assert e["remaining"]["mid"] == 2.0 and e["remaining"]["low"] < 2 < e["remaining"]["high"]
    assert e["basis"] == "this project"


# ------------------------------------------------------------------------------- iteration gates (18)
ITS = [{"name": "A"}, {"name": "B"}, {"name": "C"}]


def test_release_gates(isolated):
    settings.save({"user_name": "Fred"})
    p = projects.create("Gates")
    slug = p["slug"]
    its = iterations.propose(slug, ITS)
    assert [i["after"] for i in its] == ["continue"] * 3
    iterations.set_after(slug, {1: "pause", "2": "continue", 3: "bogus"})
    projects.update(slug, plan_approved=True)
    assert iterations.approve(slug)["n"] == 1 and projects.load(slug)["released"] == [1]
    iterations.start(slug, 1)
    folder = projects.project_dir(slug)
    (folder / "run.bat").write_text("python main.py\n")
    r = iterations.commit(slug, 1, "done", 'python -c "print(1)"')
    assert r["ok"]
    assert projects.load(slug)["waiting_release"] == 2
    assert projects.load(slug)["launch"] == {"command": "run.bat", "url": ""}
    with pytest.raises(ValueError, match="waiting for the user's release"):
        iterations.start(slug, 2)
    with pytest.raises(ValueError, match="not waiting"):
        iterations.release(slug, 1)
    iterations.release(slug, 2)
    assert projects.load(slug)["waiting_release"] is None
    assert iterations.start(slug, 2)["status"] == "building"
    # switching a finished gate back to continue releases the next one
    p2 = projects.create("Gates2")
    iterations.propose(p2["slug"], ITS)
    projects.update(p2["slug"], plan_approved=True, released=[], waiting_release=2,
                    iterations=[{**i, "status": "done" if i["n"] == 1 else "planned", "after": "pause" if i["n"] == 1 else "continue"}
                                for i in iterations.load(p2["slug"])])
    iterations.set_after(p2["slug"], {1: "continue"})
    assert projects.load(p2["slug"])["waiting_release"] is None and iterations.gate_open(p2["slug"], 2)


def test_plan_card_choices_applied(isolated):
    _ready()
    p = projects.create("Card")
    slug = p["slug"]
    projects.update(slug, architecture_approved=True)
    out = {}

    async def script(i, prompt, c):
        ok, res, msgs, _ = await c.use_tool("mcp__aiteam__request_plan_approval",
                                            {"title": "P", "plan_markdown": "x", "iterations": ITS})
        out["res"] = res
        yield say("ok")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(slug, "plan")
    assert _wait(lambda: any(e["type"] == "plan" and e.get("status") == "pending" for e in hub.history(slug)))
    card = next(e for e in hub.history(slug) if e["type"] == "plan")
    assert "estimate" in card and card["estimate"]["remaining_iterations"] == 3
    eng.respond(card["id"], {"approved": True, "after": {"1": "continue", "2": "pause"}})
    assert _wait(lambda: not eng.is_busy(slug))
    eng.shutdown()
    assert [i["after"] for i in iterations.load(slug)] == ["continue", "pause", "continue"]
    assert "after iteration(s) 2" in out["res"]
    assert "| 2 | **B** |" in (projects.project_dir(slug) / "PLAN.md").read_text()


# ------------------------------------------------------------------------------- master plan (19, 20)
def _approved(name, statuses, after=None):
    p = projects.create(name)
    its = iterations.propose(p["slug"], [{"name": f"{name} {i}"} for i in range(1, len(statuses) + 1)])
    for it, st in zip(its, statuses):
        it["status"] = st
        if after:
            it["after"] = after
    projects.update(p["slug"], plan_approved=True, architecture_approved=True, iterations=its, released=[])
    return p["slug"]


def test_waiting_for_user_frees_the_slot(isolated):
    _ready()
    settings.save({"max_parallel": 1})
    a, b = projects.create("Asker")["slug"], projects.create("Other")["slug"]
    seen = []

    async def script(i, prompt, c):
        sess = c.options.can_use_tool.__self__
        seen.append(sess.slug)
        if sess.slug == a:
            await c.use_tool("mcp__aiteam__ask_choice", {"question": "Blue or green?", "options": ["Blue", "Green"]})
        yield say("done")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(a, "go")
    assert _wait(lambda: eng.waiting_user(a))
    assert eng.running_count() == 0
    assert eng.send_message(b, "work please") == "started", "the other project does not wait behind a question"
    assert _wait(lambda: b in seen and not eng.is_busy(b))
    item = next(i for i, (_f, s, _k) in eng.pending.items() if s == a)
    eng.respond(item, {"value": "Blue"})
    assert _wait(lambda: not eng.is_busy(a))
    eng.shutdown()


def test_master_plan_continues_next_project(isolated):
    _ready()
    settings.save({"max_parallel": 1, "pipeline_auto": True})
    first = _approved("First", ["done", "planned"], after="pause")   # waits for release
    second = _approved("Second", ["done", "planned"])                # may continue
    third = _approved("Third", ["building"])
    prompts_seen = {}

    async def script(i, prompt, c):
        prompts_seen.setdefault(c.options.can_use_tool.__self__.slug, []).append(prompt)
        yield say("Continuing.")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    projects.update(first, waiting_release=2)
    settings.save({"pipeline_order": [first, second, third]})
    started = eng.pipeline_continue()
    assert started == [second], "the waiting project is skipped, slots are respected"
    assert _wait(lambda: not eng.is_busy(second))
    assert "start iteration 2 (Second 2)" in prompts_seen[second][0]
    # when Second's turn ends its slot frees and the master plan starts the next project by itself
    assert _wait(lambda: third in prompts_seen and not eng.is_busy(third))
    assert "Continue iteration 1" in prompts_seen[third][0]
    assert eng.pipeline_continue() == [], "never the same iteration twice in a row"
    # a lead question waiting for the user's reply is not overridden
    p4 = _approved("Fourth", ["planned"])
    projects.update(p4, released=[1])
    hub.publish(p4, {"type": "message", "role": "fred", "speaker": "Fred", "text": "Should totals include tax?"})
    assert p4 not in eng.pipeline_continue()
    # stopped by the user: left alone until they talk again
    p5 = _approved("Fifth", ["planned"])
    projects.update(p5, released=[1])
    eng.stop(p5)
    assert p5 not in eng.pipeline_continue()
    ov = eng.pipeline_overview()
    names = [r["name"] for r in ov["projects"]]
    assert names[:3] == ["First", "Second", "Third"]
    f = ov["projects"][0]
    assert f["state"] == "release" and f["iterations"][1]["status"] == "waiting for release"
    # release from the pipeline screen
    eng.release(first, 2)
    assert _wait(lambda: first in prompts_seen and not eng.is_busy(first))
    assert "released iteration 2" in prompts_seen[first][0]
    eng.shutdown()


# ------------------------------------------------------------------------------- launch (21) and server
@pytest.fixture()
def srv(monkeypatch):
    import http.client
    import json
    from app import winhelpers
    from app.server import App, serve

    launched = []
    monkeypatch.setattr(winhelpers, "launch_checked", lambda cmd, folder: launched.append((cmd, str(folder))) or 99)
    monkeypatch.setattr(winhelpers, "open_url", lambda url: launched.append(("url", url)))

    async def script(i, prompt, c):
        yield say("ok")

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
    yield type("S", (), {"app": app, "call": call, "launched": launched})
    eng.shutdown()
    httpd.shutdown()


def test_launch_and_pipeline_endpoints(srv, isolated):
    _ready()
    slug = _approved("Ready", ["planned", "planned"])
    assert srv.call("POST", f"/api/projects/{slug}/launch", {})[0] == 400, "nothing runnable yet"
    its = iterations.load(slug)
    its[0].update(status="done", commit="abc123")
    projects.update(slug, iterations=its, launch={"command": "run.bat", "url": "http://127.0.0.1:8000"})
    view = srv.call("GET", f"/api/projects/{slug}")[1]
    assert view["project"]["launch"]["version"] == "iteration 1: Ready 1" and "estimate" in view["project"]
    code, r = srv.call("POST", f"/api/projects/{slug}/launch", {})
    assert code == 200 and srv.launched[0] == ("run.bat", str(projects.project_dir(slug)))
    assert _wait(lambda: ("url", "http://127.0.0.1:8000") in srv.launched)
    assert srv.call("POST", f"/api/projects/{slug}/iteration-after", {"n": 2, "after": "later"})[0] == 400
    assert srv.call("POST", f"/api/projects/{slug}/iteration-after", {"n": 1, "after": "pause"})[0] == 200
    pipe = srv.call("GET", "/api/pipeline")[1]
    assert pipe["projects"][0]["iterations"][0]["after"] == "pause"
    other = _approved("Other", ["planned"])
    pipe = srv.call("POST", "/api/pipeline/order", {"order": [other, slug, "nope"]})[1]
    assert [p["slug"] for p in pipe["projects"]] == [other, slug]
    assert srv.call("POST", f"/api/projects/{slug}/release", {"n": 1})[0] == 400  # already done
    st = srv.call("GET", "/api/state")[1]
    assert next(p for p in st["projects"] if p["slug"] == slug)["launch"]["command"] == "run.bat"


def test_launch_reports_immediate_failure(tmp_path):
    import sys
    from app import winhelpers
    with pytest.raises(RuntimeError, match="error code 3"):
        winhelpers.launch_checked(f'"{sys.executable}" -c "import sys; sys.exit(3)"', tmp_path, wait=5)
    assert winhelpers.launch_checked(f'"{sys.executable}" -c "print(1)"', tmp_path, wait=2) > 0

