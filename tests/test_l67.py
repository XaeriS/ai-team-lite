"""L67: "JSON message exceeded maximum buffer size of 1048576 bytes" stopped a new project (the team read a big
saved web page). The limit is raised, and if a message is still too big the work restarts by itself."""
import json
import time

from app import auth, engine as engine_mod, engine_const, projects
from app.events import EventHub
from fakeclaude import FakeClient, say


def _wait(cond, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_limit_raised(isolated):
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        p = projects.create("Big pages")
        assert eng.session(p["slug"]).build_options().max_buffer_size == engine_const.MAX_MESSAGE_MB * 1024 * 1024 >= 50 * 2**20
    finally:
        eng.shutdown()


def test_oversize_message_recovers_by_itself(isolated, monkeypatch):
    monkeypatch.setattr(auth, "ready", lambda: True)
    prompts = []

    class Oversize(FakeClient):
        async def query(self, prompt):
            prompts.append(prompt)
            await super().query(prompt)

        async def receive_response(self):
            if len(prompts) == 1:
                raise RuntimeError("Failed to decode JSON: JSON message exceeded maximum buffer size of 52428800 bytes...")
            async for m in super().receive_response():
                yield m

    async def script(i, prompt, c):
        yield say("Reading it in parts now.")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: Oversize(o, script))
    try:
        p = projects.create("Printer queue")
        eng.send_message(p["slug"], "Read the saved Bambu Lab page in inputs")
        assert _wait(lambda: len(prompts) >= 2 and not eng.is_busy(p["slug"]))
        assert "too big for the app" in prompts[1] and "read big files in parts" in prompts[1]
        events = [json.loads(x) for x in projects.history_file(p["slug"]).read_text().splitlines()]
        assert any(e["type"] == "notice" and "too big to read in one go" in e.get("text", "") for e in events)
        assert not any(e["type"] == "error" for e in events)                 # no red "Something went wrong"
        assert any(e["type"] == "message" and "in parts" in e.get("text", "") for e in events)
    finally:
        eng.shutdown()
