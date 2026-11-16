"""L49: the quality scorecard of a version, in plain words."""
import asyncio
import json

import pytest

from app import engine as engine_mod
from app import projects, scorecard, settings, tracking, upkeep
from app.events import EventHub
from app.tools_common import add_test_result
from fakeclaude import FakeClient
from test_checkpoints import _project
import test_server

srv = test_server.srv   # the running app fixture, for the endpoint test

FAKE_KEY = "sk-ant-" + "Q7x" * 12          # secret-shaped, but not a real key


def no_vulns(url, body):
    assert url == scorecard.OSV_URL
    return {"results": [{} for _ in body["queries"]]}


def _venv(folder, packages):
    site = folder / ".venv" / "lib" / "python3.12" / "site-packages"
    for name, version in packages.items():
        d = site / f"{name}-{version}.dist-info"
        d.mkdir(parents=True)
        (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\nLong text\n")


def _good(name="Invoices"):
    slug, folder = _project(name)
    tracking.snapshot(slug, "v1", git=False)
    tracking.mark_approved(slug, "requirements")
    add_test_result(folder, {"name": "imports invoices", "status": "passed", "requirement": "REQ-1"})
    add_test_result(folder, {"name": "monthly totals", "status": "passed", "requirement": "REQ-2"})
    (folder / "requirements.txt").write_text("requests==2.32.3\n")
    _venv(folder, {"requests": "2.32.3"})
    return slug, folder


def by_key(card):
    return {c["key"]: c for c in card["checks"]}


def test_a_good_version_is_all_green_and_saved(isolated):
    slug, folder = _good()
    card = scorecard.build(slug, online=True, fetch=no_vulns)
    checks = by_key(card)
    assert [c["key"] for c in card["checks"]] == ["requirements", "tests", "code", "packages", "secrets", "findings",
                                                   "documents"]
    assert {k: c["level"] for k, c in checks.items()} == dict.fromkeys(checks, "good"), checks
    assert card["level"] == "good" and card["headline"] == "Everything checked looks good."
    assert checks["requirements"]["summary"] == "2 of 2 built requirements proven by a test."
    assert "no known security problems in 1 package" in checks["packages"]["summary"]
    assert card["iteration"] == 3 and card["commit"]
    assert scorecard.load(slug)["headline"] == card["headline"]          # kept for the Tests tab
    report = (folder / "reports" / "QUALITY.md").read_text()
    assert "# Quality of this version" in report and "| Every requirement proven by a test | OK |" in report
    assert "after iteration 3" in report
    assert scorecard.brief(card).startswith("Quality check: everything checked looks good")


def test_each_problem_is_named_in_plain_words(isolated):
    slug, folder = _good("Expenses")
    add_test_result(folder, {"name": "monthly totals", "status": "failed", "requirement": "REQ-2"})   # REQ-2 not proven now
    (folder / "app" / "broken.py").write_text("def oops(:\n    pass\n")
    (folder / "app" / "unused.py").write_text("import os\n\nx = 1\n")
    (folder / "app" / "big.py").write_text("x = 1\n" * 600)
    (folder / "requirements.txt").write_text("requests==2.19.0\nflask>=2\n")
    (folder / "app" / "config.py").write_text(f'KEY = "{FAKE_KEY}"\n')
    (folder / ".env").write_text(f"KEY={FAKE_KEY}\n")                     # never saved (.gitignore): not reported
    upkeep.record_finding(slug, "SQL built from user text", "high", "app/db.py")
    upkeep.record_finding(slug, "Log file grows forever", "low")
    f = upkeep.record_finding(slug, "No HTTPS on the local page", "medium")[0]
    upkeep.close_finding(slug, f["id"], "accepted", "It only runs on this PC")
    (folder / "run.bat").write_text("python main.py\n")                     # the app runs, so a README is expected

    def vulns(url, body):
        names = [q["package"]["name"] for q in body["queries"]]
        assert names == ["requests"]                                         # flask has no known version: skipped
        assert body["queries"][0]["version"] == "2.32.3"                     # the installed one wins over the pin
        return {"results": [{"vulns": [{"id": "GHSA-1111"}, {"id": "PYSEC-2222"}]}]}

    card = scorecard.build(slug, online=True, fetch=vulns)
    c = by_key(card)
    assert card["level"] == "problem"
    assert card["headline"] == "5 problems to fix before this version is saved."
    assert c["requirements"]["level"] == "attention" and "not yet: REQ-2" in c["requirements"]["summary"]
    assert c["tests"]["level"] == "problem" and "1 failing" in c["tests"]["summary"]
    assert c["code"]["level"] == "problem" and any("app/broken.py line 1" in d for d in c["code"]["details"])
    assert any("'os' imported but unused" in d for d in c["code"]["details"])
    assert any("app/big.py has 600 lines" in d for d in c["code"]["details"])
    assert c["packages"]["level"] == "problem"
    assert any("requests 2.32.3: 2 known security problems (GHSA-1111, PYSEC-2222)" in d for d in c["packages"]["details"])
    assert any(d.startswith("flask has no fixed version (>=2)") for d in c["packages"]["details"])
    assert "1 of 2 packages has a fixed version" in c["packages"]["summary"]
    assert c["secrets"]["level"] == "problem"
    assert [d.split(":")[0] for d in c["secrets"]["details"]] == ["app/config.py"]
    assert c["findings"]["level"] == "problem" and "1 serious finding still open" in c["findings"]["summary"]
    assert any("accepted by you" in d and "It only runs on this PC" in d for d in c["findings"]["details"])
    assert c["documents"]["level"] == "attention" and "README.md is missing" in c["documents"]["details"][0]
    # the key itself never appears in what is saved or shown
    saved = (folder / ".aiteam" / "scorecard.json").read_text() + (folder / "reports" / "QUALITY.md").read_text()
    assert FAKE_KEY not in saved and FAKE_KEY[:8] + "..." in saved
    brief = scorecard.brief(card)
    assert "[To fix] Packages the app uses" in brief and "call quality_check again" in brief


def test_offline_or_switched_off_never_blocks(isolated):
    slug, folder = _good("Offline")

    def down(url, body):
        raise OSError("no internet")

    c = by_key(scorecard.build(slug, online=True, fetch=down))["packages"]
    assert c["level"] == "unknown" and "could not be checked today" in c["summary"]
    settings.save({"scorecard_online": False})
    card = scorecard.build(slug, fetch=lambda u, b: pytest.fail("must not ask osv.dev when switched off"))
    assert by_key(card)["packages"]["level"] == "unknown" and "switched off in Settings" in by_key(card)["packages"]["summary"]
    assert card["level"] == "attention" and card["headline"] == "Good to go, with 1 point to look at."


def test_nothing_built_yet(isolated):
    settings.save({"setup_complete": True})
    p = projects.create("Fresh")
    card = scorecard.build(p["slug"], online=False)
    c = by_key(card)
    assert c["requirements"]["level"] == "unknown" and c["tests"]["level"] == "attention"
    assert c["code"]["level"] == "unknown" and c["packages"]["level"] == "good"
    with pytest.raises(KeyError):
        scorecard.build("no-such-project")


def test_requirements_file_and_installed_versions(isolated, tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "# the app\nrequests==2.31.0\nFlask>=2\nnumpy\nuvicorn[standard]==0.30.1 ; python_version > '3.8'\n"
        "-r more.txt\n--index-url x\ngit+https://example.com/x.git\npandas == 2.2.2  # pinned\nfoo==1.*\n")
    reqs = {r["name"]: r for r in scorecard.read_requirements(tmp_path)}
    assert list(reqs) == ["requests", "Flask", "numpy", "uvicorn", "pandas", "foo"]
    assert reqs["requests"]["pinned"] and reqs["requests"]["version"] == "2.31.0"
    assert reqs["uvicorn"]["pinned"] and reqs["pandas"]["version"] == "2.2.2"
    assert not reqs["Flask"]["pinned"] and not reqs["numpy"]["pinned"] and not reqs["foo"]["pinned"]
    _venv(tmp_path, {"Typing_Extensions": "4.12.2"})
    assert scorecard.installed_versions(tmp_path) == {"typing-extensions": "4.12.2"}


def test_quality_check_tool_and_endpoint(isolated, monkeypatch):
    slug, folder = _good("Tooled")
    monkeypatch.setattr(scorecard, "osv_fetch", no_vulns)
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        s = eng.session(slug)
        s.build_options()
        assert "quality_check" in s.tool_handlers
        out = asyncio.run(s.tool_handlers["quality_check"]({}))["content"][0]["text"]
        assert out.startswith("Quality check: everything checked looks good")
        events = [json.loads(x) for x in projects.history_file(slug).read_text().splitlines()]
        card = next(e for e in events if e["type"] == "scorecard")
        assert card["level"] == "good" and len(card["checks"]) == 7
        assert "quality_check()" in s.build_options().system_prompt["append"]
    finally:
        eng.shutdown()


def test_scorecard_endpoint(srv):
    from app import scorecard as sc
    srv.call("POST", "/api/settings", {"scorecard_online": False})
    assert srv.call("GET", "/api/state")[1]["settings"]["scorecard_online"] is False
    settings.save({"setup_complete": True})
    p = projects.create("Served")
    assert srv.call("GET", f"/api/projects/{p['slug']}/scorecard")[1] == {"missing": True}
    sc.build(p["slug"])
    status, data = srv.call("GET", f"/api/projects/{p['slug']}/scorecard")
    assert status == 200 and len(data["checks"]) == 7
    assert srv.call("GET", "/api/projects/nope/scorecard")[0] == 404



def test_github_card_warns_but_does_not_block(isolated, monkeypatch):
    from types import SimpleNamespace
    from app import telegram
    slug, folder = _good("Pushed")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        s = eng.session(slug)
        s.build_options()
        seen = []

        async def fake_ask(kind, payload):
            seen.append(payload)
            return {"approved": True}

        monkeypatch.setattr(s, "ask_user", fake_ask)
        push = lambda: asyncio.run(s._can_use_tool("mcp__aiteam__github_push", {"message": "v1"}, SimpleNamespace(agent_id=None)))
        push()
        assert seen[-1]["quality"] == "The quality check was not run for this version yet."
        scorecard.build(slug, online=True, fetch=no_vulns)
        push()
        assert "quality" not in seen[-1]                       # all good: nothing added
        (folder / "app" / "config.py").write_text(f'KEY = "{FAKE_KEY}"\n')
        scorecard.build(slug, online=True, fetch=no_vulns)
        result = push()
        assert seen[-1]["quality"] == "1 point to fix: No secrets in the files. See the scorecard in the chat."
        assert type(result).__name__ == "PermissionResultAllow"   # the user still decides
        text, buttons = telegram.card_message("approval", seen[-1], "Pushed", False)
        assert "⚠ Quality check: 1 point to fix" in text and buttons
        asyncio.run(s._can_use_tool("Bash", {"command": "git status"}, SimpleNamespace(agent_id=None)))
        assert all("quality" not in p for p in seen[3:])        # only the GitHub card carries it
    finally:
        eng.shutdown()
