"""L69: the plan comes before the diagram, and the user decides whether the diagrammer starts.
(L68's decisions are tested in test_v14.py; the card in ui_v162.py.)"""
import asyncio

import pytest

from app import engine as engine_mod, projects, tools_common
from app.events import EventHub
from fakeclaude import FakeClient
from app.tools_common import PLAN_KICKOFF


@pytest.fixture()
def planning(isolated, monkeypatch):
    from app import tools_planning   # diagrams available and switched on
    monkeypatch.setattr(tools_planning, "diagram_hint", lambda when: " First ask the diagrammer (subagent_type 'diagrammer')")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    p = projects.create("Printer queue")
    projects.update(p["slug"], phase="plan", architecture_approved=True)
    s = eng.session(p["slug"])
    s.build_options()
    asked = []

    def run(choice):
        async def fake_ask(kind, payload):
            asked.append((kind, payload))
            return {"approved": True} if kind == "plan" else {"value": choice}
        monkeypatch.setattr(s, "ask_user", fake_ask)
        args = {"title": "Plan", "plan_markdown": "# Plan", "iterations": [{"name": "Queue", "goal": "g", "requirements": ["REQ-1"]}]}
        return asyncio.run(s.tool_handlers["request_plan_approval"](args))["content"][0]["text"]
    yield s, asked, run
    eng.shutdown()


def test_no_diagram_before_the_plan():
    assert "diagrammer" not in PLAN_KICKOFF
    assert tools_common.diagram_hint("iteration") is not None


def test_draw_it_after_the_plan(planning):
    s, asked, run = planning
    out = run("Draw it")
    assert [k for k, _ in asked] == ["plan", "choice"]
    assert asked[1][1]["title"] == "Draw the overview diagram before building starts?" and asked[1][1]["options"] == ["Draw it", "Skip"]
    assert "APPROVED the plan" in out and "diagrammer" in out


def test_skip_goes_straight_to_building(planning):
    s, asked, run = planning
    out = run("Skip")
    assert "APPROVED the plan" in out and "diagrammer" not in out


def test_not_asked_when_the_overview_exists(planning, monkeypatch):
    s, asked, run = planning
    projects.update(s.slug, diagrams=[{"id": "abc", "topic": "architecture", "title": "Overview"}])
    out = run("Draw it")
    assert [k for k, _ in asked] == ["plan"] and "diagrammer" not in out


def test_architecture_approval_no_longer_starts_the_diagrammer(isolated, monkeypatch):
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        p = projects.create("Arch first")
        projects.update(p["slug"], phase="architecture")
        s = eng.session(p["slug"])
        s.build_options()

        async def yes(kind, payload):
            return {"approved": True}
        monkeypatch.setattr(s, "ask_user", yes)
        args = {"title": "Arch", "architecture_markdown": "# Architecture", "decisions": [
            {"topic": t, "decision": "d", "reason": "r"} for t in ("Data", "Storage", "Security", "Hosting")]}
        out = asyncio.run(s.tool_handlers["request_architecture_approval"](args))["content"][0]["text"]
        assert "APPROVED the architecture" in out, out
        assert s.pending_kickoff == PLAN_KICKOFF and "diagrammer" not in s.pending_kickoff
    finally:
        eng.shutdown()
