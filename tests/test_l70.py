"""L70: the whole-codebase clean-up comes after every code change of a version, just before the GitHub push."""
import asyncio
import time
from types import SimpleNamespace

from app import engine as engine_mod, iterations, projects, settings
from app.events import EventHub
from fakeclaude import FakeClient
from test_checkpoints import _project


def _with_cleanup(slug, folder, done=True):
    settings.save({"version_cleanup": True})   # the tests switch it off by default
    its = iterations.load(slug)
    its.append({"n": 4, "name": "Clean-up before the version", "kind": "cleanup", "goal": "", "requirements": [],
                "status": "planned", "after": "continue", "tasks": []})
    if done:
        (folder / "app" / "totals.py").write_text("def total(xs):\n    return sum(xs or [])\n")
        iterations._git(folder, "add", "-A")
        iterations._git(folder, "commit", "-m", "Iteration 4: clean-up")
        its[-1].update(status="done", finished=time.time(),
                       commit=iterations._git(folder, "rev-parse", "--short", "HEAD").stdout.strip())
    projects.update(slug, iterations=its)


def test_push_waits_until_the_cleanup_is_done(isolated):
    slug, folder = _project("Not yet")
    _with_cleanup(slug, folder, done=False)
    assert "is not done yet" in iterations.cleanup_before_push(slug)


def test_push_allowed_right_after_the_cleanup_and_after_document_changes(isolated):
    slug, folder = _project("Clean")
    _with_cleanup(slug, folder)
    assert iterations.cleanup_before_push(slug) == ""
    (folder / "STATUS.md").write_text("Ready.\n")
    (folder / "reports").mkdir(exist_ok=True)
    (folder / "reports" / "QUALITY.md").write_text("ok\n")
    assert iterations.cleanup_before_push(slug) == ""                 # documents and reports may change after it


def test_code_changed_after_the_cleanup(isolated):
    slug, folder = _project("Changed")
    _with_cleanup(slug, folder)
    (folder / "app" / "export.py").write_text("def export():\n    return 'csv'\n")      # not even committed yet
    msg = iterations.cleanup_before_push(slug)
    assert "Code changed after the clean-up of iteration 4" in msg and "app/export.py" in msg
    iterations._git(folder, "add", "-A"); iterations._git(folder, "commit", "-m", "late fix")
    assert "app/export.py" in iterations.cleanup_before_push(slug)


def test_no_rule_when_the_cleanup_is_switched_off_or_absent(isolated):
    slug, folder = _project("Off")
    assert iterations.cleanup_before_push(slug) == ""                 # plan without a clean-up iteration
    _with_cleanup(slug, folder, done=False)
    settings.save({"version_cleanup": False})
    assert iterations.cleanup_before_push(slug) == ""


def test_early_push_refused_without_a_card_unless_the_user_asked(isolated, monkeypatch):
    slug, folder = _project("Early")
    _with_cleanup(slug, folder, done=False)
    projects.update(slug, github_clone="https://example.invalid/x.git", github_repo="me/x")
    eng = engine_mod.Engine(EventHub(projects.history_file), client_factory=lambda o: FakeClient(o, None))
    try:
        s = eng.session(slug)
        s.build_options()
        cards = []

        async def ask(kind, payload):
            cards.append(payload)
            return {"approved": False}
        monkeypatch.setattr(s, "ask_user", ask)
        ctx = SimpleNamespace(agent_id=None)
        r = asyncio.run(s._can_use_tool("mcp__aiteam__github_push", {"message": "v1"}, ctx))
        assert type(r).__name__ == "PermissionResultDeny" and "not done yet" in r.message and not cards
        asyncio.run(s._can_use_tool("mcp__aiteam__github_push", {"message": "v1", "user_asked_now": True}, ctx))
        assert len(cards) == 1                                        # the user asked: their card as usual
    finally:
        eng.shutdown()
