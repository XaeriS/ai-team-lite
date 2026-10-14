import base64
import http.client
import json
import time
import urllib.request

import pytest

from app import engine as engine_mod
from app import projects, secrets_store, settings
from app.events import EventHub
from app.server import App, serve
from fakeclaude import FakeClient, say


async def hello_script(i, prompt, c):
    yield say(f"Fred heard: {prompt[:40]}")


@pytest.fixture()
def srv(monkeypatch):
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, hello_script))
    app = App(engine=eng, hub=hub)
    httpd, url = serve(app)
    port = httpd.server_address[1]
    monkeypatch.setattr(engine_mod, "validate_anthropic_key",
                        lambda k: (k == "sk-ant-good", "Anthropic refused this key."))

    def call(method, path, body=None, token=True, host="127.0.0.1"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Content-Type": "application/json", "Host": host}
        if token:
            headers["X-Token"] = app.token
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        data = r.read()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data
    yield type("S", (), {"app": app, "call": call, "port": port, "url": url})
    eng.shutdown()
    httpd.shutdown()


def wait_idle(app, slug):
    for _ in range(200):
        if not app.engine.is_busy(slug):
            return
        time.sleep(0.05)


def test_security(srv):
    assert srv.call("GET", "/api/state", token=False)[0] == 403
    assert srv.call("GET", "/api/state", host="evil.com")[0] == 403
    assert srv.call("GET", "/", token=False)[0] == 403
    with urllib.request.urlopen(srv.url) as r:
        page = r.read().decode()
    assert srv.app.token in page and "__TOKEN__" not in page
    assert srv.call("GET", "/files/../../etc/passwd")[0] == 404


def test_onboarding_then_setup_kickoff(srv):
    s = srv.call("GET", "/api/state")[1]
    assert s["has_api_key"] is False and s["setup_complete"] is False
    assert srv.call("POST", "/api/onboarding/key", {"key": "hello"})[0] == 400
    code, body = srv.call("POST", "/api/onboarding/key", {"key": "sk-ant-bad"})
    assert code == 400 and "refused" in body["error"]
    assert srv.call("POST", "/api/onboarding/key", {"key": "sk-ant-good"})[0] == 200
    assert secrets_store.get_secret("anthropic_api_key") == "sk-ant-good"
    time.sleep(0.2)
    wait_idle(srv.app, "setup")
    view = srv.call("GET", "/api/projects/setup")[1]
    assert [e["text"][:10] for e in view["history"] if e["type"] == "message"] == ["Fred heard"]
    assert "[App]" in FakeClient.instances[-1].prompts[0]
    # projects cannot be created before setup is finished
    assert srv.call("POST", "/api/projects", {"name": "X"})[0] == 400


def test_project_lifecycle(srv):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-good")
    settings.save({"setup_complete": True})
    code, p = srv.call("POST", "/api/projects", {"name": "Budget helper"})
    assert code == 200 and p["slug"] == "budget-helper"
    wait_idle(srv.app, p["slug"])
    assert srv.call("POST", f"/api/projects/{p['slug']}/message", {"text": "  "})[0] == 400
    assert srv.call("POST", f"/api/projects/{p['slug']}/message", {"text": "I need a budget app"})[0] == 200
    wait_idle(srv.app, p["slug"])
    view = srv.call("GET", f"/api/projects/{p['slug']}")[1]
    msgs = [(e["role"], e["text"]) for e in view["history"] if e["type"] == "message"]
    assert msgs[-2] == ("user", "I need a budget app") and msgs[-1][0] == "fred"
    # attachments land in inputs/ and names are sanitized
    data = base64.b64encode(b"a,b\n1,2\n").decode()
    code, r = srv.call("POST", f"/api/projects/{p['slug']}/attach", {"name": "../../bad name?.csv", "data_b64": data})
    assert code == 200 and r["path"] == "inputs/bad name_.csv"
    assert (projects.project_dir(p["slug"]) / "inputs" / "bad name_.csv").read_bytes() == b"a,b\n1,2\n"
    # screenshots are served, other files are not
    shot = projects.project_dir(p["slug"]) / "reports" / "screenshots" / "a.png"
    shot.write_bytes(b"\x89PNG fake")
    assert srv.call("GET", f"/files/{p['slug']}/reports/screenshots/a.png")[0] == 200
    assert srv.call("GET", f"/files/{p['slug']}/.aiteam/project.json")[0] == 404
    state = srv.call("GET", "/api/state")[1]
    assert [x["slug"] for x in state["projects"]] == ["budget-helper"]
    assert srv.call("POST", "/api/respond", {"id": "nope", "approved": True})[0] == 409
    assert srv.call("GET", "/api/projects/unknown-one")[0] == 404


def test_settings_and_stream(srv):
    s = srv.call("POST", "/api/settings", {"approval_mode": "strict", "role_models": {"Developer": "opus"}})[1]
    assert s["settings"]["approval_mode"] == "strict" and "role_models" not in s["settings"], "Lite: no model per role"
    s = srv.call("POST", "/api/settings", {"approval_mode": "yolo"})[1]
    assert s["settings"]["approval_mode"] == "strict"
    srv.app.hub.publish("x", {"type": "notice", "text": "ping"})
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    conn.request("GET", f"/api/stream?token={srv.app.token}&since=0", headers={"Host": "127.0.0.1"})
    r = conn.getresponse()
    assert r.status == 200
    buf = b""
    while b"ping" not in buf:
        buf += r.fp.readline()
    assert b'"type": "notice"' in buf


def test_subscription_signin_flow(srv, monkeypatch):
    from app import auth
    started = []
    monkeypatch.setattr(auth, "start_subscription_login", lambda: started.append(1) or 4242)
    state = {"signed_in": False}
    monkeypatch.setattr(auth, "subscription_status", lambda: {"signed_in": state["signed_in"], "method": "claude.ai"})
    monkeypatch.setattr(auth, "subscription_logout", lambda: True)
    assert srv.call("GET", "/api/state")[1]["has_api_key"] is False
    assert srv.call("POST", "/api/auth/subscription/start", {})[1]["pid"] == 4242 and started
    assert srv.call("POST", "/api/auth/subscription/check", {})[1]["signed_in"] is False
    assert srv.call("GET", "/api/state")[1]["has_api_key"] is False
    state["signed_in"] = True
    assert srv.call("POST", "/api/auth/subscription/check", {})[1]["signed_in"] is True
    s = srv.call("GET", "/api/state")[1]
    assert s["has_api_key"] is True and s["auth_mode"] == "subscription"
    wait_idle(srv.app, "setup")
    view = srv.call("GET", "/api/projects/setup")[1]
    assert any(e["type"] == "message" and e["role"] == "fred" for e in view["history"]), "setup should start"
    assert FakeClient.instances[-1].options.env["ANTHROPIC_API_KEY"] == ""
    s = srv.call("POST", "/api/auth/subscription/logout", {})[1]
    assert s["has_api_key"] is False and s["auth_mode"] == "api_key"  # unset mode falls back to API key


def test_delete_attachment(srv):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-good")
    settings.save({"setup_complete": True})
    code, p = srv.call("POST", "/api/projects", {"name": "Files"})
    wait_idle(srv.app, p["slug"])
    data = base64.b64encode(b"x,y\n").decode()
    path = srv.call("POST", f"/api/projects/{p['slug']}/attach", {"name": "bank.csv", "data_b64": data})[1]["path"]
    f = projects.project_dir(p["slug"]) / path
    assert f.exists()
    # only files under inputs/ can be deleted
    for bad in ["../secrets.json", "REQUIREMENTS.md", "inputs/../../x", ".aiteam/project.json"]:
        assert srv.call("POST", f"/api/projects/{p['slug']}/delete-attachment", {"path": bad})[0] == 400, bad
    # not sent yet: just removed
    assert srv.call("POST", f"/api/projects/{p['slug']}/delete-attachment", {"path": path})[1]["existed"] is True
    assert not f.exists() and not srv.app.engine.notes.get(p["slug"])
    # sent: recorded in the chat and Fred is told on the next message
    srv.call("POST", f"/api/projects/{p['slug']}/attach", {"name": "bank.csv", "data_b64": data})
    srv.call("POST", f"/api/projects/{p['slug']}/delete-attachment", {"path": path, "sent": True})
    view = srv.call("GET", f"/api/projects/{p['slug']}")[1]
    assert any(e["type"] == "attachment_deleted" and e["path"] == path for e in view["history"])
    srv.call("POST", f"/api/projects/{p['slug']}/message", {"text": "next"})
    wait_idle(srv.app, p["slug"])
    prompt = FakeClient.instances[-1].prompts[-1]
    assert "deleted the attached file inputs/bank.csv" in prompt and prompt.endswith("next")
    assert not srv.app.engine.notes.get(p["slug"])
