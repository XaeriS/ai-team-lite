"""0.10.0: in-app feedback -> feature queue (50), requested-features page (51), owner/friend sharing (49),
secret scan before publishing (53)."""
import json
from pathlib import Path

import http.client

from app import features, projects, secrets_store, settings, updates
from app.events import EventHub
from app.server import App, serve
from app import engine as engine_mod
from fakeclaude import FakeClient


class Client:
    def __init__(self, app, port):
        self.app, self.port = app, port

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body) if body is not None else None
        c.request(method, path, data, {"Content-Type": "application/json", "Host": "127.0.0.1", "X-Token": self.app.token})
        r = c.getresponse()
        raw = r.read()
        return r.status, (json.loads(raw) if raw else {})


def _app():
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, _ = serve(app)
    return app, httpd, Client(app, httpd.server_address[1])


# ------------------------------------------------------------------ feature queue (50, 51)
def test_feedback_recorded_locally(isolated):
    r = features.submit_feedback("Add dark mode", "please")
    assert r["shared"] is False and r["channel"] == "local"
    items = features.load()
    assert len(items) == 1 and items[0]["title"] == "Add dark mode" and items[0]["source"] == "feedback"
    assert items[0]["status"] == "new"


def test_status_flow_and_reason(isolated):
    it = features.add("Faster builds")
    features.set_status(it["id"], "approved")
    assert features.load()[0]["status"] == "approved"
    features.set_status(it["id"], "rejected", "out of scope for now")
    row = features.load()[0]
    assert row["status"] == "rejected" and row["reason"] == "out of scope for now"
    counts = features.summary()["counts"]
    assert counts["rejected"] == 1 and counts["new"] == 0


def test_feedback_endpoint_and_features_page(isolated):
    app, httpd, cli = _app()
    try:
        st, r = cli.call("POST", "/api/features/feedback", {"title": "Export to PDF", "body": "nice to have"})
        assert st == 200 and r["local"]["title"] == "Export to PDF"
        st, d = cli.call("GET", "/api/features")
        assert st == 200 and d["is_owner"] is False  # no sharing set up -> not owner
        assert any(i["title"] == "Export to PDF" for i in d["items"])
        # a non-owner may still set status when no repo is configured (solo use)
        fid = d["items"][0]["id"]
        st, r = cli.call("POST", "/api/features/status", {"id": fid, "status": "approved"})
        assert st == 200 and r["item"]["status"] == "approved"
    finally:
        app.engine.shutdown(); httpd.shutdown()


def test_feedback_title_required(isolated):
    app, httpd, cli = _app()
    try:
        st, r = cli.call("POST", "/api/features/feedback", {"title": "   ", "body": "x"})
        assert st == 400
    finally:
        app.engine.shutdown(); httpd.shutdown()


# ------------------------------------------------------------------ owner / friend sharing (49)
def test_not_owner_without_token(isolated):
    assert updates.is_owner() is False
    settings.save({"is_owner": True})  # flag alone is not enough; the push token must exist
    assert updates.is_owner() is False


def test_set_up_sharing_marks_owner(isolated, monkeypatch):
    secrets_store.set_secret("github_token", "ghp_" + "a" * 36)
    monkeypatch.setattr(updates.github, "create_repo",
                        lambda name, private, desc: {"url": "https://github.com/me/" + name,
                                                     "clone_url": "https://github.com/me/" + name + ".git"})
    monkeypatch.setattr(updates, "push_version", lambda: "https://github.com/me/ai-team")
    repo = updates.set_up_sharing("ai-team", private=False)
    assert repo["url"].endswith("/ai-team")
    assert settings.load().get("is_owner") is True
    assert secrets_store.has("app_repo_token")
    assert updates.is_owner() is True


def test_update_status_endpoint(isolated, monkeypatch):
    app, httpd, cli = _app()
    try:
        monkeypatch.setattr(updates, "_raw_version", lambda repo: "9.9.9")
        settings.save({"app_repo": "https://github.com/me/ai-team"})
        st, d = cli.call("GET", "/api/app/update-status")
        assert st == 200 and d["available"] is True and d["sharing"] is True and d["is_owner"] is False
    finally:
        app.engine.shutdown(); httpd.shutdown()


# ------------------------------------------------------------------ secret scan (53)
def test_scan_flags_real_key_not_placeholder(isolated, tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    # placeholder text and short fakes must NOT trip the scan
    (root / "ui.html").write_text('<input placeholder="sk-ant-…"> ghp_demo_token_123 sk-ant-should-not-be-used',
                                  encoding="utf-8")
    updates.scan_for_secrets(root)  # no raise
    # a realistic key must trip it
    (root / "leak.py").write_text("KEY = 'sk-ant-api03-" + "A" * 80 + "'", encoding="utf-8")
    raised = False
    try:
        updates.scan_for_secrets(root)
    except RuntimeError:
        raised = True
    assert raised


def test_stage_source_excludes_bugs_and_binaries(isolated):
    dst = Path(settings.workspace()) / "stage10"
    updates.stage_source(dst)
    assert (dst / "app" / "__init__.py").exists()
    assert not (dst / "BUGS.md").exists()      # the owner's notes are never published
    assert not (dst / "python").exists()
    assert "BUGS.md" not in updates.SOURCE_FILES
