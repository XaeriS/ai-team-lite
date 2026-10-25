"""0.14.0: Fred pushes back like a business analyst (item 79)."""
import asyncio

from app import challenge, iterations, policy, projects, settings, tools, tracking
from app import engine as engine_mod
from app.events import EventHub
from fakeclaude import FakeClient

FINDINGS = [
    {"kind": "objection", "title": "A spreadsheet template may be enough", "detail": "d", "recommendation": "Try it",
     "requirements": ["req-1"]},
    {"kind": "contradiction", "title": "Anyone edits vs managers approve", "recommendation": "Managers only",
     "requirements": ["REQ-2", "REQ-3"]},
    {"kind": "assumption", "title": "One currency only"},
    {"kind": "clarification", "title": "Negative amounts?"},
    {"kind": "weird", "title": "Unknown kind becomes an objection"},
    {"kind": "objection", "title": ""},  # dropped: no title
]


def _session(eng, name="Expenses"):
    p = projects.create(name)
    s = eng.session(p["slug"])
    s.build_options()
    return p["slug"], s


def _call(s, name, args):
    return asyncio.run(s.tool_handlers[name](args))["content"][0]["text"]


def test_problem_card_merges_and_reports_missing(isolated):
    p = projects.create("P")
    prob = challenge.save_problem(p["slug"], {"who": "Accountants", "how_often": "monthly"})
    assert challenge.missing(prob) == ["What it costs today", "How it is done now", "What success looks like"]
    prob = challenge.save_problem(p["slug"], {"who": "", "cost_today": "2 days per close", "how_now": "Excel",
                                              "success": "close in 1 day", "statement": "Month-end is slow."})
    assert prob["who"] == "Accountants" and challenge.missing(prob) == []      # empty value never erases


def test_findings_normalized_and_decisions_applied(isolated):
    f = challenge.normalize(FINDINGS)
    assert len(f) == 5 and f[4]["kind"] == "objection" and f[0]["requirements"] == ["REQ-1"]
    ans = {f[0]["id"]: {"decision": "keep", "reason": "my boss wants an app"},
           f[1]["id"]: {"decision": "change"}, f[2]["id"]: {"decision": "correct", "reason": "dollars and euros"},
           f[3]["id"]: {"decision": "answer", "reason": "treat as refund"}, f[4]["id"]: {"decision": "confirm"}}
    d = challenge.apply_answers(f, ans)
    assert d[4]["decision"] is None                       # 'confirm' is not allowed for an objection
    assert [x["title"] for x in challenge.undecided(d)] == ["Unknown kind becomes an objection"]
    todo = challenge.instructions_for_fred(d)
    assert "OVERRULED" in todo and "my boss wants an app" in todo and "Do not raise it again" in todo
    assert "CHANGE the requirements (REQ-2, REQ-3)" in todo and "Use your recommendation: Managers only" in todo
    assert "Assumption WRONG" in todo and "dollars and euros" in todo and "When... then..." in todo
    assert "keep it as it is" in challenge.decisions_markdown(d)


def test_level_setting(isolated):
    assert challenge.level() == "normal" and "NORMAL" in challenge.level_text()
    settings.save({"challenge_level": "firm"})
    assert "FIRM" in challenge.level_text() and "recommend NOT building" in challenge.level_text()
    settings.save({"challenge_level": "silly"})
    assert challenge.level() == "normal"


def test_fred_prompt_and_challenger_agent(isolated):
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        settings.save({"challenge_level": "gentle"})
        p = projects.create("Prompted")
        opts = eng.session(p["slug"]).build_options()
        system = opts.system_prompt["append"]
        assert "Phase 0 - Understand the problem first" in system and "GENTLE" in system
        assert "second copy of the truth" in system and "When ..., then ..." in system
        assert "challenger" in opts.agents and opts.agents["challenger"].tools == ["Read", "Glob", "Grep"]
        assert "have NOT seen the conversation" in opts.agents["challenger"].prompt
        names = set(eng.session(p["slug"]).tool_handlers)
        assert {"record_problem", "record_challenge_review", "record_diagram"} <= names
    finally:
        eng.shutdown()


def test_handoff_gate_problem_then_review_then_decisions(isolated):
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        slug, s = _session(eng)
        assert "Problem card is not complete" in _call(s, "handoff_to_architect", {"summary": "x"})
        assert "Still missing" in _call(s, "record_problem", {"who": "me"})
        assert "complete" in _call(s, "record_problem", {"how_often": "weekly", "cost_today": "1h", "how_now": "Excel",
                                                         "success": "1 click"})
        assert "Challenge review first" in _call(s, "handoff_to_architect", {"summary": "x"})
        shown = []

        async def partial(kind, card):
            shown.append((kind, card))
            f = card["findings"]
            return {"answers": {f[0]["id"]: {"decision": "keep", "reason": "I need it"}}}
        s.ask_user = partial
        out = _call(s, "record_challenge_review", {"findings": FINDINGS[:2]})
        assert shown[0][0] == "challenge" and shown[0][1]["level"] == "normal" and len(shown[0][1]["findings"]) == 2
        assert "Not decided yet" in out and "I need it" in out
        assert "not decided on every point" in _call(s, "handoff_to_architect", {"summary": "x"})
        report = (projects.project_dir(slug) / "reports" / "CHALLENGE.md").read_text(encoding="utf-8")
        assert "round 1" in report and "I need it" in report

        async def full(kind, card):
            return {"answers": {f["id"]: {"decision": "change"} for f in card["findings"]}}
        s.ask_user = full
        _call(s, "record_challenge_review", {"findings": FINDINGS[1:2]})
        assert (projects.load(slug)["challenge"]["round"]) == 2
        assert "Handed over to Mat" in _call(s, "handoff_to_architect", {"summary": "x"})
        # the user can always skip
        slug2, s2 = _session(eng, "Skip me")
        assert "Handed over" in _call(s2, "handoff_to_architect", {"summary": "x", "challenge_skipped_because": "user"})
        assert "No usable findings" in _call(s2, "record_challenge_review", {"findings": []})
    finally:
        eng.shutdown()


def test_existing_folder_is_not_gated(isolated):
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        slug, s = _session(eng, "Old work")
        projects.update(slug, existing_folder=True)
        assert "Handed over" in _call(s, "handoff_to_architect", {"summary": "x"})
    finally:
        eng.shutdown()


def test_early_helpers_need_no_approval():
    kw = dict(kind="project", mode="plan", plan_approved=False, inside_project=lambda p: True, allowed_roots=[])
    assert policy.decide("Agent", {"subagent_type": "challenger"}, **kw)[0] == "allow"
    assert policy.decide("Agent", {"subagent_type": "scout"}, **kw)[0] == "allow"
    assert policy.decide("Agent", {"subagent_type": "developer"}, **kw)[0] == "ask"
    assert policy.decide("mcp__aiteam__record_challenge_review", {}, **kw)[0] == "allow"
    assert policy.decide("mcp__aiteam__record_diagram", {}, **kw)[0] == "allow"
    assert policy.decide("Agent", {"subagent_type": "challenger"}, **{**kw, "mode": "strict"})[0] == "ask"


def test_requirements_proven_by_tests(isolated):
    p = projects.create("Proof")
    folder = projects.project_dir(p["slug"])
    (folder / "REQUIREMENTS.md").write_text("## Requirements\n- **REQ-1** Import\n- **REQ-2** Totals\n"
                                            "- **REQ-3** Export\n", encoding="utf-8")
    tools.add_test_result(folder, {"name": "import ok", "requirement": "REQ-1, req-2", "status": "passed"})
    tools.add_test_result(folder, {"name": "totals", "requirement": "REQ-2", "status": "failed"})
    rows = {r["id"]: r for r in tracking.traceability(p["slug"])}
    assert rows["REQ-1"]["proven"] and not rows["REQ-2"]["proven"] and rows["REQ-2"]["proof"]["failing"] == 1
    assert not rows["REQ-3"]["proven"] and rows["REQ-3"]["proof"] == {"passing": 0, "failing": 0}
    tools.add_test_result(folder, {"name": "totals", "requirement": "REQ-2", "status": "passed"})  # fixed later
    rows = {r["id"]: r for r in tracking.traceability(p["slug"])}
    assert rows["REQ-2"]["proven"]


def test_commit_flags_unproven_requirements(isolated, monkeypatch):
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        slug, s = _session(eng, "Commit proof")
        folder = projects.project_dir(slug)
        (folder / "REQUIREMENTS.md").write_text("- **REQ-1** A\n- **REQ-2** B\n", encoding="utf-8")
        projects.update(slug, plan_approved=True, phase="execute")
        iterations.propose(slug, iterations.normalize([{"name": "One", "requirements": ["REQ-1", "REQ-2"]}]))
        iterations.approve(slug)
        tools.add_test_result(folder, {"name": "a", "requirement": "REQ-1", "status": "passed"})
        monkeypatch.setattr(iterations, "commit_checks", lambda *a: [])

        def fake_commit(sl, n, summary, cmd, lc):
            its = iterations.load(sl)
            its[0].update(status="done", commit="abc123")
            iterations._save(sl, its)
            return {"ok": True, "commit": "abc123", "output": ""}
        monkeypatch.setattr(iterations, "commit", fake_commit)
        out = _call(s, "commit_iteration", {"n": 1, "summary": "s", "test_command": "x", "light_cleanup": "done"})
        assert "NOT YET PROVEN BY A TEST: REQ-2" in out and "REQ-1," not in out
    finally:
        eng.shutdown()

