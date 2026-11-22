"""REQ-30 (L72, CR-3): improvement ideas after each iteration. Written BEFORE the code (app/ideas.py,
propose_ideas tool, "ideas" card, POST /api/ideas/answer)."""
import asyncio
import http.client
import json
import time

from app import engine as engine_mod
from app import iterations, projects, secrets_store, settings, tools, usage
from app.events import EventHub
from fakeclaude import FakeClient, say

IDEAS = [
    {"title": "Export to Excel", "why": "Share totals with the boss", "effort": "small"},
    {"title": "Monthly e-mail summary", "why": "No need to open the app", "effort": "medium", "source_link": "https://x.test/a"},
    {"title": "Dark mode", "why": "Easier on the eyes", "effort": "small"},
    {"title": "Fourth idea", "why": "Too many", "effort": "large"},
]


def _wait(cond, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    return False


def _call(s, name, args):
    return asyncio.run(s.tool_handlers[name](args))["content"][0]["text"]


def _log(folder):
    f = folder / ".aiteam" / "ideas.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []


def _engine(script=None):
    hub = EventHub(projects.history_file)
    return hub, engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))


# ---- app/ideas.py ------------------------------------------------------------------------------
def test_req30_decision_saved_in_log_and_ideas_md(isolated):
    from app import ideas
    p = projects.create("Ideas log")
    folder = projects.project_dir(p["slug"])
    ideas.add_decision(folder, IDEAS[0], "yes", "boss asked")
    ideas.add_decision(folder, IDEAS[1], "no", "")
    rows = _log(folder)
    assert [(r["title"], r["decision"]) for r in rows] == [("Export to Excel", "yes"), ("Monthly e-mail summary", "no")]
    assert {"id", "title", "why", "effort", "source", "decision", "date", "reason"} <= set(rows[0])
    assert rows[0]["reason"] == "boss asked" and rows[0]["date"]
    md = (folder / "reports" / "IDEAS.md").read_text(encoding="utf-8")
    assert "Export to Excel" in md and "Monthly e-mail summary" in md and "boss asked" in md and "|" in md
    assert "yes" in md.lower() and "no" in md.lower()


def test_req30_effort_cannot_break_a_table_row(isolated):
    from app import ideas
    p = projects.create("Ideas effort")
    folder = projects.project_dir(p["slug"])
    ideas.add_decision(folder, {"title": "T", "why": "w", "effort": "a\rb|c\nd"}, "yes", "")
    md = (folder / "reports" / "IDEAS.md").read_text(encoding="utf-8")
    row = md.split("\n")[-2]
    assert "\r" not in md and row.startswith("| T |") and "a b/c d" in row and row.count("|") == 7


def test_req30_decided_titles_ignore_case_spaces_punctuation(isolated):
    from app import ideas
    p = projects.create("Ideas norm")
    folder = projects.project_dir(p["slug"])
    assert not ideas.decided_titles(folder)
    ideas.add_decision(folder, IDEAS[0], "no", "")
    survivors = ideas.filter_new(folder, [{"title": "  EXPORT  to excel!! ", "why": "w", "effort": "s"},
                                          {"title": "Dark mode", "why": "w", "effort": "s"}], [])
    assert [i["title"] for i in survivors] == ["Dark mode"]


def test_req30_filter_new_caps_at_three_and_drops_known(isolated):
    from app import ideas
    p = projects.create("Ideas filter")
    folder = projects.project_dir(p["slug"])
    ideas.add_decision(folder, {"title": "Old idea", "why": "w", "effort": "s"}, "yes", "")
    got = ideas.filter_new(folder, IDEAS, [])
    assert len(got) == 3 and [i["title"] for i in got] == [i["title"] for i in IDEAS[:3]]
    got = ideas.filter_new(folder, [{"title": "Old idea"}] + IDEAS, ["The app must offer Dark Mode for night use."])
    titles = [i["title"] for i in got]
    assert "Old idea" not in titles and "Dark mode" not in titles and len(titles) == 3
    assert ideas.filter_new(folder, [], []) == []


# ---- the team tool -----------------------------------------------------------------------------
def test_req30_propose_ideas_is_a_listed_tool(isolated):
    from app import policy
    assert "propose_ideas" in policy.ALWAYS_ALLOW or "mcp__aiteam__propose_ideas" in policy.ALWAYS_ALLOW
    hub, eng = _engine()
    try:
        s = eng.session(projects.create("Tool list")["slug"])
        s.build_options()
        assert "propose_ideas" in s.tool_handlers
    finally:
        eng.shutdown()


def test_req30_empty_list_shows_no_card(isolated):
    hub, eng = _engine()
    try:
        p = projects.create("Nothing to say")
        s = eng.session(p["slug"])
        s.build_options()
        out = _call(s, "propose_ideas", {"ideas": []})
        assert "no idea" in out.lower()
        assert not [e for e in hub.history(p["slug"]) if e["type"] == "ideas"]
        assert eng.project_state(p["slug"]) != "needs you"
    finally:
        eng.shutdown()


def test_req30_decided_idea_is_refused_without_card(isolated):
    from app import ideas
    hub, eng = _engine()
    try:
        p = projects.create("Refuse repeat")
        s = eng.session(p["slug"])
        s.build_options()
        ideas.add_decision(projects.project_dir(p["slug"]), IDEAS[0], "no", "")
        out = _call(s, "propose_ideas", {"ideas": [{"title": "export to EXCEL", "why": "w", "effort": "s"}]})
        assert "already decided" in out.lower()
        assert not [e for e in hub.history(p["slug"]) if e["type"] == "ideas"]
    finally:
        eng.shutdown()


# ---- the card, answered row by row -------------------------------------------------------------
def _flow(name):
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    settings.save({"setup_complete": True})
    p = projects.create(name)
    out = {}

    async def script(i, prompt, c):
        ok, res, msgs, _ = await c.use_tool("mcp__aiteam__propose_ideas", {"ideas": IDEAS})
        out["result"] = res
        ok, res, msgs, _ = await c.use_tool("mcp__aiteam__propose_ideas", {"ideas": [IDEAS[1]]})
        out["again"] = res
        yield say("done")
    hub, eng = _engine(script)
    eng.send_message(p["slug"], "go")
    card = lambda: next((e for e in hub.history(p["slug"]) if e["type"] == "ideas"), None)
    assert _wait(lambda: card() and card().get("status") == "pending")
    return p, hub, eng, card, out


def _post(app, port, path, body):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json",
                 "Host": "127.0.0.1", "X-Token": app.token})
    r = conn.getresponse()
    r.read()
    return r.status


def test_req30_card_row_by_row_yes_asks_for_change_request_no_is_never_repeated(isolated):
    from app.server import App, serve
    p, hub, eng, card, out = _flow("Ideas flow")
    app = App(engine=eng, hub=hub)
    httpd, url = serve(app)
    port = httpd.server_address[1]
    try:
        c = card()
        assert [i["title"] for i in c["ideas"]] == [i["title"] for i in IDEAS[:3]], "at most 3 rows"
        assert all({"id", "title", "why", "effort"} <= set(i) for i in c["ideas"])
        assert eng.project_state(p["slug"]) == "needs you"
        row = lambda n, d, r="": _post(app, port, "/api/ideas/answer",
                                       {"id": c["id"], "idea": c["ideas"][n]["id"], "decision": d, "reason": r})
        assert row(0, "yes", "boss asked") == 200
        time.sleep(0.3)
        assert card()["status"] == "pending" and eng.project_state(p["slug"]) == "needs you", "rows left open"
        assert row(1, "no", "not now") == 200
        assert row(2, "no") == 200
        assert _wait(lambda: "result" in out and "again" in out)
        assert "record_change_request" in out["result"] and "Export to Excel" in out["result"]
        assert "already decided" in out["again"].lower(), "a No is never proposed again"
        rows = _log(projects.project_dir(p["slug"]))
        assert [(r["title"], r["decision"]) for r in rows] == [("Export to Excel", "yes"), ("Monthly e-mail summary", "no"),
                                                               ("Dark mode", "no")]
        assert rows[1]["reason"] == "not now"
        assert (projects.project_dir(p["slug"]) / "reports" / "IDEAS.md").exists()
        assert _wait(lambda: not eng.is_busy(p["slug"]))
        assert eng.project_state(p["slug"]) != "needs you"
    finally:
        eng.shutdown()
        httpd.shutdown()


def test_req30_answer_route_rejects_a_bad_decision(isolated):
    from app.server import App, serve
    p, hub, eng, card, out = _flow("Ideas bad")
    app = App(engine=eng, hub=hub)
    httpd, url = serve(app)
    try:
        c = card()
        assert _post(app, httpd.server_address[1], "/api/ideas/answer",
                     {"id": c["id"], "idea": c["ideas"][0]["id"], "decision": "maybe"}) == 400
        assert not _log(projects.project_dir(p["slug"]))
    finally:
        eng.shutdown()
        httpd.shutdown()


# ---- the trigger: commit_iteration ------------------------------------------------------------
ARGS = {"n": 1, "summary": "s", "test_command": "x", "light_cleanup": "done"}


def _commit_setup(name, monkeypatch, already_done=False):
    hub, eng = _engine()
    p = projects.create(name)
    s = eng.session(p["slug"])
    s.build_options()
    folder = projects.project_dir(p["slug"])
    projects.update(p["slug"], plan_approved=True, phase="execute")
    it = {"name": "One", "requirements": ["REQ-1"]}
    if already_done:
        it["already_done"] = True
    iterations.propose(p["slug"], iterations.normalize([it, {"name": "Two"}]))
    iterations.approve(p["slug"])
    tools.add_test_result(folder, {"name": "a", "requirement": "REQ-1", "status": "passed"})
    monkeypatch.setattr(iterations, "commit_checks", lambda *a: [])

    def fake_commit(sl, n, summary, cmd, lc):
        its = iterations.load(sl)
        its[0].update(status="done", commit="abc123")
        iterations._save(sl, its)
        return {"ok": True, "commit": "abc123", "output": ""}
    monkeypatch.setattr(iterations, "commit", fake_commit)
    return eng, s


def test_req30_commit_tells_fred_to_prepare_ideas(isolated, monkeypatch):
    monkeypatch.setattr(usage, "warning_note", lambda: "")
    eng, s = _commit_setup("Trigger", monkeypatch)
    try:
        out = _call(s, "commit_iteration", ARGS)
        assert "committed to Git" in out and "propose_ideas" in out and "idea" in out.lower()
    finally:
        eng.shutdown()


def test_req30_no_ideas_instruction_when_plan_usage_near_limit(isolated, monkeypatch):
    monkeypatch.setattr(usage, "warning_note", lambda: "[App] Plan usage is high: 5-hour: 90% used")
    eng, s = _commit_setup("Near limit", monkeypatch)
    try:
        out = _call(s, "commit_iteration", ARGS)
        assert "committed to Git" in out and "propose_ideas" not in out
    finally:
        eng.shutdown()


def test_req30_no_ideas_instruction_when_iteration_was_already_done(isolated, monkeypatch):
    monkeypatch.setattr(usage, "warning_note", lambda: "")
    eng, s = _commit_setup("Already done", monkeypatch, already_done=True)
    try:
        out = _call(s, "commit_iteration", ARGS)
        assert "propose_ideas" not in out
    finally:
        eng.shutdown()
