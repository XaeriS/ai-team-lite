"""0.11.0: model descriptions + full lists (54), Ollama catalog (55), gateway + brain modes (56), code map installs
Graphify (52), existing folder continuation (53), portable token ledger by model/project/iteration (57)."""
import json
import shutil
import tempfile
import pytest
import time
from pathlib import Path

import http.client

from app import brains, iterations, projects, settings, tooling, updates, usage
from app import engine as engine_mod
from app.events import EventHub
from app.server import App, serve, _scan_text, EXISTING_KICKOFF
from fakeclaude import FakeClient


def _app():
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, _ = serve(app)
    port = httpd.server_address[1]

    def call(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Content-Type": "application/json", "Host": "127.0.0.1", "X-Token": app.token})
        r = c.getresponse()
        raw = r.read()
        return r.status, (json.loads(raw) if raw else {})
    return app, httpd, call


# ------------------------------------------------------------------ (54) Gemini list
def test_gemini_notes_and_order(isolated, monkeypatch):
    names = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-pro", "gemini-2.0-flash-001",
             "gemini-3-pro-preview", "gemini-2.0-flash"]
    monkeypatch.setattr(brains, "list_models", lambda p: list(names))
    rows = brains.list_models_tiered("gemini")
    assert len(rows) == len(names)                         # every model, not two
    assert rows[0]["model"] == "gemini-flash-latest" and rows[0]["recommended"]
    by = {r["model"]: r["note"] for r in rows}
    assert "Always points to the newest" in by["gemini-flash-latest"]
    assert "Early preview" in by["gemini-3-pro-preview"]
    assert "Fixed snapshot" in by["gemini-2.0-flash-001"]
    assert "Older generation" in by["gemini-2.0-flash"]
    assert "Older generation" not in by["gemini-2.5-flash"]  # a 3.0 PREVIEW does not make stable 2.5 "older"
    assert "Free tier" in by["gemini-2.5-flash"] and "Paid" in by["gemini-2.5-pro"]


def test_gemini_filters_non_text_models(isolated, monkeypatch):
    monkeypatch.setattr(brains, "_http", lambda *a, **k: {"data": [{"id": "models/gemini-2.5-flash"},
                        {"id": "models/gemini-embedding-001"}, {"id": "models/gemini-2.5-flash-preview-tts"},
                        {"id": "models/gemini-2.5-flash-image"}, {"id": "models/gemini-2.0-flash-live-001"}]})
    monkeypatch.setattr(brains, "_key", lambda p: "k")
    assert brains.list_models("gemini") == ["gemini-2.5-flash"]


# ------------------------------------------------------------------ (55) Ollama catalog
def test_ollama_catalog_fit_installed_recommended(isolated, monkeypatch):
    monkeypatch.setattr(tooling, "memory_gb", lambda: 16.0)
    monkeypatch.setattr(tooling, "gpu_memory_gb", lambda: 0.0)
    monkeypatch.setattr(tooling, "ollama_status", lambda: {"state": "running"})
    monkeypatch.setattr(brains, "_ollama_installed", lambda: {"qwen2.5-coder:7b", "phi4-mini:latest", "mymodel:latest"})
    cat = brains.ollama_catalog()
    rows = {r["model"]: r for r in cat["models"]}
    assert len(cat["models"]) >= 25
    assert rows["qwen2.5-coder:7b"]["installed"] and not rows["qwen2.5-coder:14b"]["installed"]  # exact, not by family
    assert rows["phi4-mini"]["installed"]                     # "name" == "name:latest"
    assert rows["mymodel"]["installed"]                       # installed but unknown models are listed too
    assert rows["qwen2.5-coder:14b"]["ram_ok"] and not rows["qwen3-coder:30b"]["ram_ok"]
    assert rows["qwen2.5-coder:14b"]["recommended"] and cat["recommended"] == "qwen2.5-coder:14b"
    assert all(r["note"] for r in cat["models"])


def test_ollama_add_offers_download(isolated, monkeypatch):
    app, httpd, call = _app()
    try:
        monkeypatch.setattr(brains, "ollama_installed_names", lambda: {"llama3.2:3b"})
        st, r = call("POST", "/api/brains/add", {"provider": "ollama", "model": "qwen3:8b"})
        assert st == 200 and r["needs_download"] and r["size_gb"] == 5.2 and not brains.all_brains()
    finally:
        app.engine.shutdown(); httpd.shutdown()


def test_key_for_one_provider_never_saved_to_another(isolated):
    app, httpd, call = _app()
    try:
        st, _ = call("POST", "/api/brains/models", {"provider": "ollama", "key": "AIza-should-not-matter"})
        assert st == 200  # used to fail with "Unknown secret: None"
    finally:
        app.engine.shutdown(); httpd.shutdown()


# ------------------------------------------------------------------ (56) modes + gateway
# ------------------------------------------------------------------ (52) code map
# ------------------------------------------------------------------ (53) existing folder = continuation
def test_scan_finds_docs_anywhere(isolated, tmp_path):
    f = tmp_path / "old-app"
    (f / "docs").mkdir(parents=True)
    (f / "docs" / "requirements.md").write_text("# reqs")
    (f / "Status.MD").write_text("# status")
    (f / "main.py").write_text("print(1)")
    s = projects.scan_folder(f)
    assert s["found"] == {"REQUIREMENTS.md": "docs/requirements.md", "STATUS.md": "Status.MD"}
    assert s["missing"] == ["ARCHITECTURE.md", "PLAN.md"] and s["files"] == 3
    text = _scan_text(s)
    assert "docs/requirements.md" in text and "missing: ARCHITECTURE.md, PLAN.md" in text
    assert "REBUILD" in EXISTING_KICKOFF and "ask_choice" in EXISTING_KICKOFF and "already_done" in EXISTING_KICKOFF


def test_adopted_project_continues(isolated, tmp_path):
    p = projects.create("Was here")
    projects.update(p["slug"], phase="execute", plan_approved=True, architecture_approved=True,
                    iterations=[{"n": 1, "name": "A", "status": "done"}])
    src = projects.project_dir(p["slug"])
    moved = tmp_path / "moved-project"
    shutil.copytree(src, moved)
    q = projects.create_from_folder(str(moved), "Moved")
    assert q["adopted"] and q["phase"] == "execute" and q["plan_approved"] and q["iterations"][0]["status"] == "done"


def test_already_done_iterations(isolated):
    p = projects.create("Cont")
    its = iterations.propose(p["slug"], [{"name": "Login (existing)", "already_done": True},
                                         {"name": "Reports"}, {"name": "Export"}])
    assert [i["status"] for i in its[:3]] == ["done", "planned", "planned"]
    assert its[0]["existing"] and [i["n"] for i in its] == list(range(1, len(its) + 1))
    assert iterations.approve(p["slug"])["name"] == "Reports"   # nothing finished is rebuilt


# ------------------------------------------------------------------ (57) portable token ledger
def test_ledger_by_iteration_and_model_travels(isolated, tmp_path):
    p = projects.create("Ledger")
    slug = p["slug"]
    usage.record_tokens(slug, "Fred", {"input_tokens": 100, "output_tokens": 10}, model="claude-sonnet-5-5")
    projects.update(slug, iterations=[{"n": 1, "name": "First", "status": "building", "started": time.time()}])
    usage.record_tokens(slug, "Developer", {"input_tokens": 1000, "output_tokens": 500,
                                            "cache_read_input_tokens": 300}, model="claude-opus-5-5")
    ledger = projects.project_dir(slug) / ".aiteam" / "usage.jsonl"
    rows = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert len(rows) == 2 and rows[1]["iteration"] == 1 and rows[0]["iteration"] is None
    from app import analytics
    d = analytics.report(slug)
    assert d["total"]["tokens"] == 1610
    it1 = next(g for g in d["iterations"] if g["n"] == 1)
    assert it1["name"] == "First" and it1["tokens"] == 1500 and it1["cost"] > 0
    # the app's own log is lost (new PC) and the folder is moved: the project still knows its history
    (Path(usage._log_file())).unlink()
    moved = tmp_path / "elsewhere"
    shutil.copytree(projects.project_dir(slug), moved)
    q = projects.create_from_folder(str(moved), "Ledger moved")
    assert analytics.report(q["slug"])["total"]["tokens"] == 1610


def test_ledger_backfills_earlier_history(isolated):
    p = projects.create("Old history")
    slug = p["slug"]
    t0 = time.time()
    with open(usage._log_file(), "a") as f:   # rows written by an older version (no iteration, no ledger)
        f.write(json.dumps({"t": t0 - 50, "day": time.strftime("%Y-%m-%d"), "project": slug, "who": "Developer",
                            "model": "claude-sonnet-5-5", "in": 10, "out": 5, "cache_read": 0, "cache_write": 0,
                            "cost": 0}) + "\n")
    projects.update(slug, iterations=[{"n": 1, "name": "Old", "status": "done", "started": t0 - 100, "finished": t0 - 10}])
    usage.record_tokens(slug, "Fred", {"input_tokens": 1, "output_tokens": 1}, model="claude-haiku-5-5")
    rows = usage.project_rows(slug)
    assert len(rows) == 2 and rows[0]["iteration"] == 1   # matched to the iteration running at the time


def test_8gb_pc_reported_as_7_8(isolated):
    assert brains._fits(7.8, 8) and brains.recommended_local(7.8) == "qwen2.5-coder:7b"
    assert brains.recommended_local(15.8) == "qwen2.5-coder:14b" and not brains._fits(5.0, 8)


# ------------------------------------------------------------------ 0.11.1
class _Info:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_back_to_normal_clears_stale_percent(isolated):
    t = time.time() + 3600
    usage.record_limit(_Info(rate_limit_type="seven_day", status="allowed_warning", utilization=0.98, resets_at=t))
    assert usage.plan()["seven_day"]["percent"] == 98.0
    usage.record_limit(_Info(rate_limit_type="seven_day", status="allowed", utilization=None, resets_at=t))
    v = usage.plan()["seven_day"]
    assert v["percent"] is None and v["below_warning"]        # no longer stuck at 98%
    assert "five_hour" in usage.plan()                         # the 5-hour window is always shown


def test_reopen_folder_and_repair(isolated):
    p = projects.create("accounting formatting")
    q = projects.create_from_folder(str(projects.project_dir(p["slug"])))
    assert q["slug"] == p["slug"] and q["reopened"] and not projects.external()
    # the damage left by 0.11.0: record renamed + duplicate entry -> repaired
    f = projects.project_dir(p["slug"]) / ".aiteam" / "project.json"
    j = json.loads(f.read_text()); j["slug"] = "accounting-formatting-2"; f.write_text(json.dumps(j))
    projects._save_external({"accounting-formatting-2": str(projects.project_dir(p["slug"]))})
    assert projects.repair()
    assert [x["slug"] for x in projects.list_projects()] == ["accounting-formatting"] and not projects.external()


def test_broken_project_listed_reloaded_removed(isolated, tmp_path):
    src = tmp_path / "my-app"; src.mkdir(); (src / "main.py").write_text("x")
    p = projects.create_from_folder(str(src))
    moved = tmp_path / "my-app-moved"; src.rename(moved)
    b = projects.broken()
    assert b and b[0]["slug"] == p["slug"] and "not found" in b[0]["reason"]
    q = projects.reload_broken(p["slug"], str(moved))            # point it at its new folder
    assert q["slug"] == p["slug"] and projects.project_dir(q["slug"]) == moved.resolve() and not projects.broken()
    moved.rename(src)
    projects.forget_broken(p["slug"])
    assert not projects.broken() and not projects.list_projects() and (src / "main.py").exists()
def test_broken_endpoints_and_state(isolated, tmp_path):
    app, httpd, call = _app()
    try:
        src = tmp_path / "gone"; src.mkdir()
        p = projects.create_from_folder(str(src))
        shutil.rmtree(src)
        st, s = call("GET", "/api/state")
        assert [b["slug"] for b in s["broken"]] == [p["slug"]]
        st, r = call("POST", f"/api/broken/{p['slug']}/reload", {})
        assert st == 400 and "does not exist" in r["error"]
        st, r = call("POST", f"/api/broken/{p['slug']}/remove", {})
        assert st == 200 and not call("GET", "/api/state")[1]["broken"]
    finally:
        app.engine.shutdown(); httpd.shutdown()


def test_brain_test_timeout_is_clear(isolated, monkeypatch):
    def slow(*a, **k):
        raise brains.BrainError("timed out")
    monkeypatch.setattr(brains, "_http", slow)
    monkeypatch.setattr(brains, "_key", lambda p: "k")
    ok, msg = brains.test({"id": "x", "provider": "gemini", "model": "gemini-flash-latest"})
    assert not ok and "within 30 seconds" in msg


def test_recommendation_reason_and_best_per_task(isolated):
    b = brains.best_local(16)
    assert b["coding"] == "qwen2.5-coder:14b" and b["reasoning"] and b["writing"]
    assert "coding model" in brains.recommended_why(16) and "DeepSeek-R1" in brains.recommended_why(16)


def test_close_to_tray_setting_saved(isolated):
    app, httpd, call = _app()
    try:
        assert call("POST", "/api/settings", {"close_to_tray": True})[0] == 200
        assert call("GET", "/api/state")[1]["settings"]["close_to_tray"] is True
    finally:
        app.engine.shutdown(); httpd.shutdown()


# ------------------------------------------------------------------ 0.11.2
def _fake_claude(tmp_path, text):
    f = tmp_path / "claude"
    f.write_text("#!/bin/sh\ncat <<'EOF'\n" + text + "\nEOF\n")
    f.chmod(0o755)
    return f


def test_real_plan_figures_from_claude(isolated, tmp_path, monkeypatch):
    from app import auth
    usage._last_failed["t"] = 0
    settings.save({"auth_mode": "subscription"})
    monkeypatch.setattr(auth, "cli_path", lambda: _fake_claude(tmp_path, "Total cost: $0.00\n"
                        "Current session: 46% used · resets 7:30pm\nCurrent week (all models): 8% used · resets Fri 8pm"))
    t = time.time() + 99999
    usage.record_limit(_Info(rate_limit_type="seven_day", status="allowed_warning", utilization=0.98, resets_at=None))
    assert usage.fetch_plan()
    p = usage.plan()
    assert p["five_hour"]["percent"] == 46.0 and p["seven_day"]["percent"] == 8.0      # the 98% is replaced
    assert p["seven_day"]["reset_text"] == "Fri 8pm" and p["seven_day"]["source"] == "claude_usage"
    usage.record_limit(_Info(rate_limit_type="seven_day", status="allowed", utilization=None, resets_at=t))
    assert usage.plan()["seven_day"]["percent"] == 8.0   # a status report without % does not wipe the real figure


def test_no_figures_falls_back_and_old_percent_expires(isolated, tmp_path, monkeypatch):
    from app import auth
    usage._last_failed["t"] = 0
    settings.save({"auth_mode": "subscription"})
    monkeypatch.setattr(auth, "cli_path", lambda: _fake_claude(tmp_path, "Total cost: $0.00"))
    assert usage.fetch_plan() is None
    usage.record_limit(_Info(rate_limit_type="seven_day", status="allowed_warning", utilization=0.98, resets_at=None))
    data = json.loads(usage._plan_file().read_text())
    data["seven_day"]["percent_at"] = time.time() - 8 * 86400   # reported more than a week ago
    usage._plan_file().write_text(json.dumps(data))
    v = usage.plan()["seven_day"]
    assert v["percent"] is None and v["stale"]               # never shown past its own window


def test_setup_extras_skips_what_is_installed(isolated, monkeypatch, capsys):
    from app import setup_extras, voice
    calls = []
    monkeypatch.setattr(voice, "model_ready", lambda size=None: True)
    monkeypatch.setattr(voice, "download_model", lambda size=None: calls.append("voice"))
    monkeypatch.setattr(tooling, "ollama_exe", lambda: "C:/ollama.exe")
    monkeypatch.setattr(tooling, "ollama_install", lambda p=None: calls.append("ollama"))
    monkeypatch.setattr(tooling, "ollama_status", lambda: {"state": "running"})
    monkeypatch.setattr(tooling, "memory_gb", lambda: 16.0)
    monkeypatch.setattr(brains, "ollama_installed_names", lambda: {"qwen2.5-coder:14b"})
    monkeypatch.setattr(tooling, "ollama_pull", lambda m, p=None: calls.append("pull " + m))
    assert setup_extras.main(["--voice", "--ollama", "--model"]) == 0
    out = capsys.readouterr().out
    assert calls == [] and out.count("nothing to download") == 3


def test_setup_extras_downloads_and_never_fails(isolated, monkeypatch, capsys):
    from app import setup_extras, voice
    calls = []
    monkeypatch.setattr(voice, "model_ready", lambda size=None: False)
    monkeypatch.setattr(voice, "packages_available", lambda: True)
    monkeypatch.setattr(voice, "download_model", lambda size=None: calls.append("voice"))
    monkeypatch.setattr(tooling, "ollama_exe", lambda: None)
    monkeypatch.setattr(tooling, "ollama_status", lambda: {"state": "not installed"})
    def boom(p=None):
        raise RuntimeError("no internet")
    monkeypatch.setattr(tooling, "ollama_install", boom)
    assert setup_extras.main(["--voice", "--ollama"]) == 0      # an error never fails the installation
    out = capsys.readouterr().out
    assert calls == ["voice"] and "could not finish now" in out and "offer it again" in out


def test_launch_for_existing_folder_with_several_start_files(isolated, tmp_path):
    app, httpd, call = _app()
    try:
        src = tmp_path / "accounting-formatting"; src.mkdir()
        for f in ("run.bat", "run_app.bat", "run_migrate.bat", "main.py"):
            (src / f).write_text("echo hi")
        p = projects.create_from_folder(str(src))
        info = engine_mod.launch_info(p["slug"])
        assert info and info["all_choices"] == ["run_app.bat", "run.bat"]      # migrate script is not a start file
        assert info["choices"]                                                  # several: ask once
        st, r = call("POST", f"/api/projects/{p['slug']}/launch", {})
        assert st == 200 and r["needs_choice"]
        st, r = call("POST", f"/api/projects/{p['slug']}/launch", {"command": "run.bat", "save_only": True})
        assert st == 200 and r["command"] == "run.bat" and not r["choices"]    # remembered
        st, r = call("POST", f"/api/projects/{p['slug']}/launch", {"command": "evil.exe"})
        assert st == 400
        q = projects.create("Brand new")
        (projects.project_dir(q["slug"]) / "run.bat").write_text("echo")
        assert engine_mod.launch_info(q["slug"]) is None                        # nothing built yet: no button
    finally:
        app.engine.shutdown(); httpd.shutdown()


# ------------------------------------------------------------------ 0.11.3
def test_ask_user_question_becomes_a_question_card(isolated):
    import asyncio
    from claude_agent_sdk import PermissionResultAllow
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        p = projects.create("Ask me")
        s = eng.session(p["slug"])
        shown = []

        async def fake_ask(kind, card):
            shown.append((kind, card))
            return {"value": "Wait for the reset"}
        s.ask_user = fake_ask
        q = {"questions": [{"question": "Start iteration 1 now?", "header": "Usage", "multiSelect": False,
                            "options": [{"label": "Start now", "description": "uses most of the 2% left"},
                                        {"label": "Wait for the reset", "description": "Friday 20:00"}]}]}
        r = asyncio.run(s._can_use_tool("AskUserQuestion", q, None))
        assert shown[0][0] == "choice" and shown[0][1]["options"] == ["Start now", "Wait for the reset"]
        assert shown[0][1]["hints"][1] == "Friday 20:00"
        assert isinstance(r, PermissionResultAllow)
        assert r.updated_input["answers"] == {"Start iteration 1 now?": "Wait for the reset"}
    finally:
        eng.shutdown()


def test_fred_warning_uses_fresh_real_figures(isolated, tmp_path, monkeypatch):
    from app import auth
    settings.save({"auth_mode": "subscription"})
    usage.record_limit(_Info(rate_limit_type="seven_day", status="allowed_warning", utilization=0.98, resets_at=None))
    monkeypatch.setattr(auth, "cli_path", lambda: _fake_claude(tmp_path, "Current session: 46% used · resets 7:30pm\n"
                        "Current week (all models): 8% used · resets Fri 8pm"))
    usage._last_failed["t"] = 0
    assert usage.warning_note() == ""             # real figures (8%) replace the stale 98%: no warning
    monkeypatch.setattr(auth, "cli_path", lambda: _fake_claude(tmp_path, "Current week (all models): 91% used · resets Fri 8pm"))
    usage.fetch_plan(force=True)
    note = usage.warning_note()
    assert "91% used" in note and "read from Claude at" in note


def test_old_figure_from_before_the_window_is_dropped(isolated):
    reset = time.time() + 3 * 86400
    usage.record_limit(_Info(rate_limit_type="seven_day", status="allowed_warning", utilization=0.98, resets_at=None))
    data = json.loads(usage._plan_file().read_text())
    data["seven_day"]["percent_at"] = reset - 8 * 86400        # taken before this week began
    usage._plan_file().write_text(json.dumps(data))
    e = usage.record_limit(_Info(rate_limit_type="seven_day", status="allowed_warning", utilization=None, resets_at=reset))
    assert e["percent"] is None


# ------------------------------------------------------------------ 0.11.4
def test_lost_conversation_continues_in_a_fresh_one(isolated, monkeypatch):
    from fakeclaude import say
    from app import auth
    monkeypatch.setattr(auth, "ready", lambda: True)
    seen = []

    class LostOnResume(FakeClient):
        async def query(self, prompt):
            seen.append((self.options.resume, prompt))
            if self.options.resume == "gone-session":
                raise RuntimeError("Claude Code returned an error result: No conversation found with session ID: "
                                   "gone-session (exit code 1)")
            await super().query(prompt)

    async def script(i, prompt, c):
        yield say("Cleaning up now.")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: LostOnResume(o, script))
    try:
        p = projects.create("Lost chat")
        projects.update(p["slug"], session_id="gone-session")
        (projects.project_dir(p["slug"]) / "REQUIREMENTS.md").write_text("# Requirements\nREQ-1 format accounts")
        eng.send_message(p["slug"], "Please clean up the code")
        assert _wait_for(lambda: len(seen) >= 2 and not eng.is_busy(p["slug"]))
        assert seen[0][0] == "gone-session" and seen[1][0] is None        # retried in a fresh conversation
        assert "REQ-1 format accounts" in seen[1][1] and "Please clean up the code" in seen[1][1]  # caught up
        events = [json.loads(x) for x in projects.history_file(p["slug"]).read_text().splitlines()]
        assert any(e.get("type") == "notice" and "could not be found" in e.get("text", "") for e in events)
        assert not any(e.get("type") == "error" for e in events)
        assert projects.load(p["slug"])["session_id"] == "sess-1"         # the new conversation is remembered
    finally:
        eng.shutdown()


def _wait_for(cond, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------------ 0.12.0
def _fake_github(monkeypatch, tmp_path):
    import subprocess
    from app import github
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    monkeypatch.setattr(github, "check", lambda: {"login": "XaeriS", "name": "XaeriS", "id": 1, "scopes": ""})
    monkeypatch.setattr(github, "token", lambda: "ghp_" + "x" * 36)
    return remote


def test_publish_builds_on_previous_versions(isolated, tmp_path, monkeypatch):
    import subprocess
    from app import github
    remote = _fake_github(monkeypatch, tmp_path)
    for version, files in (("0.11.4", {"a.py": "1", "old.py": "x"}), ("0.12.0", {"a.py": "2", "b.py": "new"})):
        d = tmp_path / ("stage-" + version); d.mkdir()
        for n, c in files.items():
            (d / n).write_text(c)
        github.publish(d, f"AI Team Lite {version}", str(remote), tag=f"v{version}")   # second one used to be rejected
    log = subprocess.run(["git", "--git-dir", str(remote), "log", "--oneline", "main"], capture_output=True, text=True).stdout
    tags = subprocess.run(["git", "--git-dir", str(remote), "tag"], capture_output=True, text=True).stdout.split()
    files = subprocess.run(["git", "--git-dir", str(remote), "ls-tree", "--name-only", "main"], capture_output=True, text=True).stdout.split()
    assert len(log.splitlines()) == 2 and tags == ["v0.11.4", "v0.12.0"]
    assert files == ["a.py", "b.py"]                       # a file removed in the new version is removed


def test_link_existing_repository(isolated, tmp_path, monkeypatch):
    from app import github, secrets_store
    remote = _fake_github(monkeypatch, tmp_path)
    secrets_store.set_secret("github_token", "ghp_" + "x" * 36)
    monkeypatch.setattr(github, "repo_info", lambda full: {"full_name": full, "url": "https://github.com/" + full,
                                                           "clone_url": str(remote), "can_push": full == "XaeriS/ai-team",
                                                           "private": False, "empty": True})
    published = []
    monkeypatch.setattr(updates, "push_version", lambda: published.append(1) or "ok")
    try:
        updates.link_existing("XaeriS/other")
        assert False, "should refuse a repo it cannot write to"
    except RuntimeError as e:
        assert "cannot write" in str(e)
    info = updates.link_existing("https://github.com/XaeriS/ai-team")
    assert info["full_name"] == "XaeriS/ai-team" and published and updates.is_owner()
    assert settings.load()["app_repo"] == "https://github.com/XaeriS/ai-team" and not updates.is_friend_install()
# ------------------------------------------------------------------ 0.13.0 Archify diagrams
ARCHIFY_SRC = Path("/home/claude/build/dl/archify-v3.1.0/archify")


def _archify_project(monkeypatch, versions=("base", "head")):
    import shutil, subprocess, pytest
    if not (ARCHIFY_SRC / "bin" / "archify.mjs").exists() or not shutil.which("node"):
        pytest.skip("Archify / Node not available here")
    monkeypatch.setenv("AITEAM_ARCHIFY", str(ARCHIFY_SRC))
    p = projects.create("Checkout")
    f = projects.project_dir(p["slug"])
    out = []
    for v in versions:
        d = f / ".archify" / f"architecture-checkout-{v}"; d.mkdir(parents=True)
        shutil.copy(ARCHIFY_SRC / "examples" / f"checkout-platform.{v}.architecture.json", d / "candidate.json")
        subprocess.run([tooling.node_exe(), str(ARCHIFY_SRC / "bin" / "archify.mjs"), "finalize", "architecture",
                        str(d / "candidate.json"), str(d / "checkout.html")], cwd=f, capture_output=True)
        out.append((f".archify/architecture-checkout-{v}/checkout.html", f".archify/architecture-checkout-{v}/candidate.json"))
    return p, out


def test_diagrams_recorded_and_compared(isolated, monkeypatch):
    from app import diagrams
    p, files = _archify_project(monkeypatch)
    a = diagrams.record(p["slug"], "How checkout works", "architecture", "architecture", *files[0], "Main parts?")
    b = diagrams.record(p["slug"], "How checkout works", "architecture", "architecture", *files[1], "Main parts?")
    assert not a["compare"] and b["compare"].endswith("-changes.html") and b["compare_with"] == a["id"]
    assert diagrams.path_of(p["slug"], b["id"], "compare").exists()
    assert [d["id"] for d in diagrams.items(p["slug"])] == [a["id"], b["id"]]


def test_diagram_refused_when_invalid_or_outside(isolated, monkeypatch):
    import pytest
    from app import diagrams
    p, files = _archify_project(monkeypatch, versions=("base",))
    f = projects.project_dir(p["slug"])
    (f / ".archify" / "fake.html").write_text("<html>not a diagram</html>")
    with pytest.raises(ValueError, match="check did not pass"):
        diagrams.record(p["slug"], "x", "architecture", "architecture", ".archify/fake.html", files[0][1])
    with pytest.raises(ValueError, match="not a file in the project"):
        diagrams.record(p["slug"], "x", "architecture", "architecture", "../../etc/passwd", files[0][1])
    with pytest.raises(ValueError, match="Unknown diagram type"):
        diagrams.record(p["slug"], "x", "architecture", "pie", *files[0])


def test_diagram_policy_exception_is_narrow(isolated):
    from app import policy
    proj, inst = r"C:\Users\Alex\Projects\recipes", r"C:\Users\Alex\AppData\Local\Programs\AI Team Lite"
    inside = lambda path: str(path).lower().startswith(proj.lower())
    dec = lambda tool, inp: policy.decide(tool, inp, kind="project", mode="plan", plan_approved=False,
                                          inside_project=inside, allowed_roots=[proj, inst])[0]
    assert dec("Write", {"file_path": proj + r"\.archify\architecture-x\candidate.json"}) == "allow"
    assert dec("Write", {"file_path": proj + r"\main.py"}) == "ask"                 # no plan yet: still asks
    run = f'"{inst}\\node\\node.exe" "{inst}\\archify\\bin\\archify.mjs" finalize architecture .archify\\a\\c.json .archify\\a\\x.html'
    assert dec("PowerShell", {"command": run}) == "allow"
    assert dec("PowerShell", {"command": run + "; Remove-Item -Recurse C:\\"}) == "ask"       # chaining never allowed
    assert dec("PowerShell", {"command": f'"{inst}\\node\\node.exe" archify.mjs render architecture a.json C:\\Users\\Other\\x.html'}) == "ask"


def test_diagram_endpoints(isolated, monkeypatch):
    from app import diagrams
    p, files = _archify_project(monkeypatch, versions=("base",))
    e = diagrams.record(p["slug"], "How checkout works", "architecture", "architecture", *files[0])
    app, httpd, call = _app()
    try:
        st, d = call("GET", f"/api/projects/{p['slug']}/diagrams")
        assert st == 200 and d["available"] and d["items"][0]["id"] == e["id"]
        c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        c.request("GET", f"/diagram/{p['slug']}/{e['id']}?token={app.token}", headers={"Host": "127.0.0.1"})
        r = c.getresponse(); body = r.read()
        assert r.status == 200 and b"<svg" in body
        st, _ = call("GET", f"/diagram/{p['slug']}/ffffffffff")
        assert st == 404
    finally:
        app.engine.shutdown(); httpd.shutdown()


def test_diagrammer_only_when_available_and_on(isolated, monkeypatch):
    from app import tools as tools_mod
    monkeypatch.setattr(tooling, "archify_ready", lambda: False)
    assert tools_mod.diagram_hint("architecture") == ""
    monkeypatch.setattr(tooling, "archify_ready", lambda: True)
    assert "diagrammer" in tools_mod.diagram_hint("architecture")
    settings.save({"skills": {"archify": False}})
    assert tools_mod.diagram_hint("iteration") == ""                 # switched off in Team & tools: no diagrams


def test_archify_selftest(monkeypatch):
    from app import tooling
    if not (ARCHIFY_SRC / "bin" / "archify.mjs").exists() or not shutil.which("node"):
        pytest.skip("Archify source not available")
    monkeypatch.setenv("AITEAM_ARCHIFY", str(ARCHIFY_SRC))
    tooling._archify_ok.clear()
    assert tooling.archify_works() is True and tooling.archify_ready() is True
    broken = Path(tempfile.mkdtemp()) / "archify"; (broken / "bin").mkdir(parents=True)
    (broken / "bin" / "archify.mjs").write_text("process.exit(3)")
    monkeypatch.setenv("AITEAM_ARCHIFY", str(broken))
    assert tooling.archify_works() is False and tooling.archify_ready() is False
    tooling._archify_ok.clear()
