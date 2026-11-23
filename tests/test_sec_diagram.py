"""H-5: a diagram page (written by the AI) must not run with the app's own power: no access to the app token or
origin. The page is shown in a sandboxed frame and is served with a Content-Security-Policy that sandboxes it."""
import http.client
import re
from pathlib import Path

import pytest

from app import engine as engine_mod
from app import projects
from app.events import EventHub
from app.server import App, serve
from fakeclaude import FakeClient

UI = Path(__file__).resolve().parent.parent / "app" / "ui"


@pytest.fixture()
def diagram(isolated):
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, _ = serve(app)
    p = projects.create("Diag")
    f = projects.project_dir(p["slug"])
    (f / ".archify").mkdir()
    (f / ".archify" / "d.html").write_text("<html><script>parent.eval('TOKEN')</script></html>", encoding="utf-8")
    projects.update(p["slug"], diagrams=[{"id": "abcdef123456", "title": "T", "html": ".archify/d.html"}])

    def get():
        c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        c.request("GET", f"/diagram/{p['slug']}/abcdef123456", headers={"Host": "127.0.0.1", "X-Token": app.token})
        r = c.getresponse()
        r.read()
        return r
    yield get
    eng.shutdown()
    httpd.shutdown()


def test_diagram_response_is_sandboxed_by_csp(diagram):
    r = diagram()
    assert r.status == 200
    csp = r.getheader("Content-Security-Policy") or ""
    assert re.search(r"(^|;)\s*sandbox\b", csp), f"no 'sandbox' directive in CSP: {csp!r}"
    assert "allow-same-origin" not in csp


def test_diagram_iframe_has_sandbox_without_same_origin():
    html = (UI / "index.html").read_text(encoding="utf-8")
    tag = re.search(r"<iframe[^>]*id=\"graphFrame\"[^>]*>", html).group(0)
    m = re.search(r"\bsandbox(\s*=\s*\"([^\"]*)\")?", tag)
    assert m, f"the diagram iframe has no sandbox attribute: {tag}"
    assert "allow-same-origin" not in (m.group(2) or "")


def test_diagram_url_carries_no_token():
    js = (UI / "js" / "side.js").read_text(encoding="utf-8")
    line = next(l for l in js.splitlines() if "graphFrame').src" in l or 'graphFrame").src' in l)
    assert "token" not in line.lower(), f"token in the diagram URL: {line.strip()}"


def test_diagram_ticket_is_bound_to_its_diagram_and_single_use(isolated):
    import json
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, _ = serve(app)
    try:
        p = projects.create("Diag2")
        f = projects.project_dir(p["slug"])
        (f / ".archify").mkdir()
        (f / ".archify" / "d.html").write_text("<html></html>", encoding="utf-8")
        projects.update(p["slug"], diagrams=[{"id": "abcdef123456", "title": "T", "html": ".archify/d.html"},
                                             {"id": "fedcba654321", "title": "U", "html": ".archify/d.html"}])

        def call(path, token=True):
            c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
            c.request("GET", path, headers={"Host": "127.0.0.1", **({"X-Token": app.token} if token else {})})
            r = c.getresponse()
            return r.status, r.read()

        def ticket(i="abcdef123456", which="html"):
            _, b = call(f"/api/diagram-ticket?slug={p['slug']}&id={i}&which={which}")
            return json.loads(b)["url"]
        url = ticket()
        other = url.replace("abcdef123456", "fedcba654321")
        assert call(other, token=False)[0] != 200, "a ticket opened another diagram"
        url = ticket()
        assert call(url.replace("which=html", "which=compare"), token=False)[0] != 200, "a ticket opened another view"
        url = ticket()
        assert call(url, token=False)[0] == 200
        assert call(url, token=False)[0] != 200, "a ticket worked twice"
    finally:
        eng.shutdown()
        httpd.shutdown()
