"""REQ-29: one message draft per project, saved in the project's .aiteam/draft.json (written before the code exists)."""
import base64
import json

from app import projects
from fakeclaude import FakeClient
import test_server


srv = test_server.srv  # fixture: the test server with a fake Claude, reused

def _project(name):
    return projects.create(name)["slug"]


def _draft_file(slug):
    return projects.project_dir(slug) / ".aiteam" / "draft.json"


def _attach(srv, slug, name="notes.csv"):
    data = base64.b64encode(b"a,b\n1,2\n").decode()
    code, r = srv.call("POST", f"/api/projects/{slug}/attach", {"name": name, "data_b64": data})
    assert code == 200
    return r["path"]


def test_req29_project_without_draft_returns_empty(srv):
    slug = _project("Alpha")
    code, d = srv.call("GET", f"/api/projects/{slug}/draft")
    assert code == 200
    assert d["text"] == "" and d["attachments"] == []


def test_req29_saved_draft_comes_back_with_text_and_attachments(srv):
    slug = _project("Alpha")
    path = _attach(srv, slug)
    code, _ = srv.call("PUT", f"/api/projects/{slug}/draft", {"text": "hello A", "attachments": [path]})
    assert code == 200
    code, d = srv.call("GET", f"/api/projects/{slug}/draft")
    assert code == 200 and d["text"] == "hello A" and d["attachments"] == [path]
    assert not d.get("dropped")


def test_req29_two_projects_keep_separate_drafts(srv):
    a, b = _project("Alpha"), _project("Beta")
    srv.call("PUT", f"/api/projects/{a}/draft", {"text": "hello A", "attachments": []})
    assert srv.call("GET", f"/api/projects/{b}/draft")[1]["text"] == ""
    srv.call("PUT", f"/api/projects/{b}/draft", {"text": "hello B", "attachments": []})
    assert srv.call("GET", f"/api/projects/{a}/draft")[1]["text"] == "hello A"
    assert srv.call("GET", f"/api/projects/{b}/draft")[1]["text"] == "hello B"


def test_req29_draft_is_stored_in_project_aiteam_folder(srv):
    slug = _project("Alpha")
    srv.call("PUT", f"/api/projects/{slug}/draft", {"text": "keep me", "attachments": []})
    f = _draft_file(slug)
    assert f.is_file()
    assert json.loads(f.read_text(encoding="utf-8"))["text"] == "keep me"


def test_req29_empty_draft_clears_it(srv):
    slug = _project("Alpha")
    srv.call("PUT", f"/api/projects/{slug}/draft", {"text": "temp", "attachments": []})
    code, _ = srv.call("PUT", f"/api/projects/{slug}/draft", {"text": "", "attachments": []})
    assert code == 200
    f = _draft_file(slug)
    assert (not f.exists()) or f.read_text(encoding="utf-8").strip() in ("", "{}") or \
        json.loads(f.read_text(encoding="utf-8")).get("text", "") == ""
    d = srv.call("GET", f"/api/projects/{slug}/draft")[1]
    assert d["text"] == "" and d["attachments"] == []


def test_req29_attachment_whose_file_is_gone_is_dropped_with_name(srv):
    slug = _project("Alpha")
    keep = _attach(srv, slug, "keep.csv")
    gone = _attach(srv, slug, "gone.csv")
    srv.call("PUT", f"/api/projects/{slug}/draft", {"text": "x", "attachments": [keep, gone]})
    (projects.project_dir(slug) / gone).unlink()
    code, d = srv.call("GET", f"/api/projects/{slug}/draft")
    assert code == 200
    assert d["attachments"] == [keep]
    assert any("gone.csv" in n for n in d["dropped"])
    assert d["text"] == "x"


def test_req29_unknown_project_gives_clear_error(srv):
    code, body = srv.call("GET", "/api/projects/no-such-project/draft")
    assert code == 404 and isinstance(body, dict) and body.get("error")
    code, body = srv.call("PUT", "/api/projects/no-such-project/draft", {"text": "x", "attachments": []})
    assert code == 404 and isinstance(body, dict) and body.get("error")


def test_req29_draft_needs_the_token(srv):
    slug = _project("Alpha")
    assert srv.call("GET", f"/api/projects/{slug}/draft", token=False)[0] == 403
    assert srv.call("PUT", f"/api/projects/{slug}/draft", {"text": "x", "attachments": []}, token=False)[0] == 403


def test_req29_draft_never_in_chat_history_and_never_sent_to_claude(srv):
    slug = _project("Alpha")
    before = sum(len(c.prompts) for c in FakeClient.instances)
    srv.call("PUT", f"/api/projects/{slug}/draft", {"text": "SECRET-DRAFT-TEXT", "attachments": []})
    srv.call("GET", f"/api/projects/{slug}/draft")
    hist = projects.history_file(slug)
    assert (not hist.exists()) or "SECRET-DRAFT-TEXT" not in hist.read_text(encoding="utf-8")
    view = srv.call("GET", f"/api/projects/{slug}")[1]
    assert "SECRET-DRAFT-TEXT" not in json.dumps(view["history"])
    assert not any("SECRET-DRAFT-TEXT" in p for c in FakeClient.instances for p in c.prompts)
    assert sum(len(c.prompts) for c in FakeClient.instances) == before


def _evil_paths():
    bs = chr(92)
    return ["../../x", "inputs/../../x", "C:" + bs + "Windows" + bs + "win.ini", "C:/Windows/win.ini", "/etc/passwd",
            "inputs" + bs + ".." + bs + ".." + bs + "x", bs * 2 + "server" + bs + "share" + bs + "f", "inputs/a:b", "notes.csv"]


def test_review_draft_attachment_paths_are_confined(srv):
    slug = _project("Alpha")
    good = _attach(srv, slug)
    outside = projects.project_dir(slug).parent / "secret-outside.txt"
    outside.write_text("x", encoding="utf-8")
    evil = _evil_paths() + [str(outside), "../secret-outside.txt"]
    (projects.project_dir(slug) / "notes.csv").write_text("x", encoding="utf-8")   # exists, but not in inputs/
    srv.call("PUT", f"/api/projects/{slug}/draft", {"text": "t", "attachments": [good] + evil})
    code, d = srv.call("GET", f"/api/projects/{slug}/draft")
    assert code == 200 and d["attachments"] == [good]
    assert d["dropped"] == []   # existence outside the project is never revealed


def test_review_draft_size_caps(srv):
    slug = _project("Alpha")
    url = f"/api/projects/{slug}/draft"
    assert srv.call("PUT", url, {"text": "a" * 100_001, "attachments": []})[0] == 400
    assert srv.call("PUT", url, {"text": "a", "attachments": ["inputs/a"] * 51})[0] == 400
    assert srv.call("PUT", url, {"text": "a", "attachments": ["inputs/" + "a" * 260]})[0] == 400
    assert srv.call("PUT", url, {"text": "a" * 100_000, "attachments": []})[0] == 200


def test_review_draft_body_must_be_json_object(srv):
    slug = _project("Alpha")
    for bad in ([1, 2], "text", 5):
        code, body = srv.call("PUT", f"/api/projects/{slug}/draft", bad)
        assert code == 400 and body.get("error")


def test_review_composer_js_keeps_text_on_failure_and_prunes_cache():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "ui" / "js"
    js = (root / "composer.js").read_text(encoding="utf-8")
    assert "draftPrune" in js and "draftPrune" in (root / "core.js").read_text(encoding="utf-8")
    send = js[js.index("async function send()"):js.index("function autosize")]
    assert send.index("await api(") < send.index("draftSave(slug)")   # cleared only after success
    assert "drafts[slug] = {text: t, attachments" in send            # restored on failure
