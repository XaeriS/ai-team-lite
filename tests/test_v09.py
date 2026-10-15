"""0.9.0: brain test-before-add + tiers, usage in/out split + cache savings, context cap, changes diff, updates."""
import json
import time
from pathlib import Path

import pytest

from app import brains, engine as engine_mod, iterations, models, projects, secrets_store, settings, updates, usage
from app.events import EventHub
from fakeclaude import FakeClient, say


def _wait(cond, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    return False


# ------------------------------------------------------------------ brains (39-42)
def test_model_tier(isolated):
    assert brains.model_tier("gemini", "gemini-flash-latest") == "free"
    assert brains.model_tier("gemini", "gemini-2.5-pro") == "paid"
    assert brains.model_tier("ollama", "qwen3:8b") == "free"
    p = brains.summary()["providers"]
    assert p["gemini"]["how"] and "free" in p["gemini"]["free_hint"].lower()


def test_brain_not_added_when_test_fails(isolated, monkeypatch):
    import http.client
    from app.server import App, serve
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, _ = serve(app)
    port = httpd.server_address[1]
    monkeypatch.setattr(brains, "test_config", lambda prov, model: (False, "no credit"))

    def call(body):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/api/brains/add", json.dumps(body),
                  {"Content-Type": "application/json", "Host": "127.0.0.1", "X-Token": app.token})
        r = c.getresponse()
        return json.loads(r.read())
    r = call({"provider": "gemini", "model": "gemini-2.5-pro"})
    assert r["added"] is False and r["test_ok"] is False and not brains.all_brains()
    # add anyway -> saved but disabled
    r = call({"provider": "gemini", "model": "gemini-2.5-pro", "force": True})
    assert r["added"] is True and brains.all_brains()[0]["enabled"] is False
    eng.shutdown(); httpd.shutdown()


# ------------------------------------------------------------------ context cap (48)
def test_context_cap_resets_and_briefs(isolated):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    settings.save({"setup_complete": True, "cap_context": True})
    p = projects.create("Cap")
    slug = p["slug"]
    projects.update(slug, session_id="old-sess", plan_approved=True, architecture_approved=True)
    (projects.project_dir(slug) / "REQUIREMENTS.md").write_text("# Reqs\n- REQ-1 do a thing")
    iterations.propose(slug, [{"name": "A"}, {"name": "B"}])
    seen = {}

    async def script(i, prompt, c):
        seen["prompt"] = prompt
        seen["resume"] = c.options.resume
        yield say("ok")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    # a pipeline continuation ("start_iteration(") is a fresh point
    eng.send_message(slug, "[App] The approved plan continues: start iteration 1 (A) now with start_iteration(1).", hidden=True)
    assert _wait(lambda: "prompt" in seen and not eng.is_busy(slug))
    assert seen["resume"] is None, "fresh context: the old session is dropped"
    assert projects.load(slug)["session_id"] == "sess-1", "a new session id is stored"
    assert "REQUIREMENTS.md" in seen["prompt"] and "REQ-1" in seen["prompt"], "briefing re-sends the essentials"
    eng.shutdown()


def test_context_cap_off_keeps_session(isolated):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    settings.save({"setup_complete": True, "cap_context": False})
    p = projects.create("NoCap")
    slug = p["slug"]
    projects.update(slug, session_id="keep-me", plan_approved=True, architecture_approved=True)
    iterations.propose(slug, [{"name": "A"}])
    seen = {}

    async def script(i, prompt, c):
        seen["resume"] = c.options.resume
        yield say("ok")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(slug, "[App] start iteration 1 (A) with start_iteration(1).", hidden=True)
    assert _wait(lambda: "resume" in seen and not eng.is_busy(slug))
    assert seen["resume"] == "keep-me", "with the cap off, the long session continues"
    eng.shutdown()


# ------------------------------------------------------------------ changes diff (45)
# ------------------------------------------------------------------ updates (46)
def test_updates_version_compare(isolated, monkeypatch):
    monkeypatch.setattr(updates, "_raw_version", lambda repo: "9.9.9")
    st = updates.check("https://github.com/me/ai-team")
    assert st["available"] is True and st["latest"] == "9.9.9"
    monkeypatch.setattr(updates, "_raw_version", lambda repo: updates.__dict__ and __import__("app").__version__)
    assert updates.check("https://github.com/me/ai-team")["available"] is False
    # staging copies app source but never the bundled python/git
    dst = isolated / "stage"
    updates.stage_source(dst)
    assert (dst / "app" / "__init__.py").exists() and (dst / "VERSION").exists()
    assert not (dst / "python").exists()
