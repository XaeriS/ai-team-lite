"""REQ-22: the setting that turns the sandbox on or off. Written BEFORE the code.
Key: "sandbox_enabled" (bool, default True), saved by POST /api/settings, shown in /api/state settings,
read by app.sandbox.enabled()."""
import http.client
import json

from app import projects, settings
from app import engine as engine_mod
from app.events import EventHub
from app.server import App, serve
from fakeclaude import FakeClient


def test_req22_sandbox_setting_defaults_to_on():
    assert settings.load()["sandbox_enabled"] is True
    from app import sandbox
    assert sandbox.enabled() is True


def test_req22_enabled_follows_the_setting():
    from app import sandbox
    settings.save({"sandbox_enabled": False})
    assert sandbox.enabled() is False
    settings.save({"sandbox_enabled": True})
    assert sandbox.enabled() is True


def test_req22_sandbox_setting_saved_by_route():
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, _ = serve(app)
    port = httpd.server_address[1]

    def call(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Content-Type": "application/json", "Host": "127.0.0.1", "X-Token": app.token})
        r = c.getresponse(); raw = r.read()
        return r.status, (json.loads(raw) if raw else {})
    try:
        assert call("GET", "/api/state")[1]["settings"]["sandbox_enabled"] is True
        assert call("POST", "/api/settings", {"sandbox_enabled": False})[0] == 200
        assert call("GET", "/api/state")[1]["settings"]["sandbox_enabled"] is False
        assert settings.load()["sandbox_enabled"] is False
        assert call("POST", "/api/settings", {"sandbox_enabled": "no"})[0] == 200   # not a yes/no: ignored
        assert settings.load()["sandbox_enabled"] is False
    finally:
        app.engine.shutdown(); httpd.shutdown()
