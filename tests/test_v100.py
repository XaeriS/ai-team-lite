"""1.0.0: work never gets stuck silently (L7), the watchdog (L8), usage and time analytics (L6, L9, L13-L15),
"why Fred asked this" (L5), screenshots in the message box (L10), microphone diagnostics (L11), name (L12)."""
import asyncio
import json
import time

import pytest
from claude_agent_sdk import (AssistantMessage, ResultMessage, TaskNotificationMessage, TaskProgressMessage,
                              TextBlock, ToolResultBlock, UserMessage)

from app import engine as engine_mod, projects, prompts, secrets_store, settings, timeline, usage
from app.events import EventHub
from fakeclaude import FakeClient, say

RECEIPT = ("Async agent launched successfully. (This tool result is internal metadata - never quote or paste any part "
           "of it.)\nagentId: a6775f4ddb68dc391 (internal ID - do not mention to user.)\nThe agent is working in the "
           "background. You will be notified automatically when it completes.")


def _ready():
    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    settings.save({"setup_complete": True})


def _wait(cond, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.03)
    return False


def _put(eng, client, msg):
    """Hand a message to the fake engine from the test thread (like the real engine's reader would)."""
    eng.loop.call_soon_threadsafe(client.queue.put_nowait, msg)


def _bg_project(name="Bg"):
    _ready()
    p = projects.create(name)
    projects.update(p["slug"], plan_approved=True, architecture_approved=True, phase="execute")
    return p["slug"]


def _notification(tid, status="completed", summary="Agent finished"):
    return TaskNotificationMessage(subtype="task_notification", data={}, task_id="a6775f4ddb68dc391", status=status,
                                   output_file="", summary=summary, uuid="u1", session_id="sess-1", tool_use_id=tid)


# ------------------------------------------------------------------------------- L7
def test_background_worker_wakes_fred_without_taskoutput(isolated, monkeypatch):
    """The 2026-10-14 case: the engine put the Reviewer in the background, Fred ended his turn waiting, and nothing
    happened until the user typed. Now the app waits for the worker itself and wakes Fred with its report."""
    monkeypatch.setattr(engine_mod, "WAKE_GRACE", 0.2)
    slug = _bg_project()
    seen = {"prompts": []}

    async def script(i, prompt, c):
        seen["prompts"].append(prompt)
        if i == 0:
            ok, res, msgs, tid = await c.use_tool("Agent", {"subagent_type": "reviewer", "description": "Review it",
                                                            "prompt": "review"})
            seen["tid"], seen["client"] = tid, c
            yield UserMessage(content=[ToolResultBlock(tid, RECEIPT)])
            yield say("The Reviewer is working; I'll continue once it reports back.")
            return
        yield say("The Reviewer's report is in: all good. Committing.")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(slug, "go")
    assert _wait(lambda: "tid" in seen and not eng.is_busy(slug))
    sess = eng.session(slug)
    card = next(e for e in hub.history(slug) if e["type"] == "agent")
    assert card["status"] == "working" and "summary" not in card, "the receipt is never shown as a report"
    assert eng.project_state(slug) == "working", "not idle while the worker is still busy"
    assert not any("TaskOutput" in m for m in seen["prompts"]), "Fred is never told to use TaskOutput"
    assert _wait(lambda: sess.watch_task is not None and not sess.watch_task.done())
    # the worker keeps talking in the background, then finishes
    c, tid = seen["client"], seen["tid"]
    _put(eng, c, AssistantMessage(content=[TextBlock("Review done: REQ-1 to REQ-4 pass, no issues.")],
                                        model="claude-sonnet-5-5", parent_tool_use_id=tid,
                                        usage={"input_tokens": 900, "output_tokens": 100}, message_id="mbg1"))
    _put(eng, c, _notification(tid))
    assert _wait(lambda: len(seen["prompts"]) == 2), "Fred was woken up without the user typing"
    wake = seen["prompts"][1]
    assert "Reviewer has finished" in wake and "REQ-1 to REQ-4 pass" in wake
    assert "Do not call TaskOutput" in wake
    assert _wait(lambda: not eng.is_busy(slug))
    card = next(e for e in hub.history(slug) if e["type"] == "agent")
    assert card["status"] == "done" and "REQ-1" in card["summary"]
    rev = next(r for r in sess.team_snapshot() if r["name"] == "Reviewer")
    assert rev["status"] == "idle"
    assert eng.project_state(slug) == "idle"
    rows = usage.project_rows(slug)
    assert any(r["who"] == "Reviewer" and r["in"] == 900 for r in rows), "background tokens count for the Reviewer"
    eng.shutdown()


def test_engine_wakes_fred_itself_no_double_turn(isolated, monkeypatch):
    """When the engine does wake Fred on its own, the app shows that turn live and does not wake him twice."""
    monkeypatch.setattr(engine_mod, "WAKE_GRACE", 0.5)
    slug = _bg_project("Self")
    seen = {"prompts": []}

    async def script(i, prompt, c):
        seen["prompts"].append(prompt)
        ok, res, msgs, tid = await c.use_tool("Agent", {"subagent_type": "developer", "prompt": "build"})
        seen["tid"], seen["client"] = tid, c
        yield UserMessage(content=[ToolResultBlock(tid, RECEIPT)])
        yield say("Waiting for the developer.")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(slug, "go")
    assert _wait(lambda: "tid" in seen and not eng.is_busy(slug))
    c, tid = seen["client"], seen["tid"]
    _put(eng, c, _notification(tid))
    _put(eng, c, say("The developer finished. Moving on to the tests."))
    _put(eng, c, ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                                     session_id="sess-1", total_cost_usd=0.01))
    assert _wait(lambda: any(e["type"] == "message" and "Moving on" in e.get("text", "") for e in hub.history(slug)))
    time.sleep(1.0)
    assert len(seen["prompts"]) == 1, "no second wake-up: Fred already had the report"
    assert eng.project_state(slug) == "idle"
    eng.shutdown()


def test_progress_and_stop_background(isolated):
    slug = _bg_project("Stop")
    seen = {}

    async def script(i, prompt, c):
        ok, res, msgs, tid = await c.use_tool("Agent", {"subagent_type": "tester", "prompt": "test"})
        seen["tid"], seen["client"] = tid, c
        yield UserMessage(content=[ToolResultBlock(tid, RECEIPT)])
        yield say("Waiting.")

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(slug, "go")
    assert _wait(lambda: "tid" in seen and not eng.is_busy(slug))
    sess = eng.session(slug)
    _put(eng, seen["client"], TaskProgressMessage(
        subtype="task_progress", data={}, task_id="a6775f4ddb68dc391", description="t",
        usage={"total_tokens": 10, "tool_uses": 1, "duration_ms": 5}, uuid="u", session_id="s",
        tool_use_id=seen["tid"], last_tool_name="Bash"))
    assert _wait(lambda: "in the background" in (sess.team.get("Tester") or {}).get("last", "")
                 and (sess.team.get("Tester") or {}).get("last") != "working in the background")
    eng.stop(slug)
    assert _wait(lambda: eng.project_state(slug) == "idle")
    assert _wait(lambda: seen["client"].stopped_tasks == ["a6775f4ddb68dc391"]), "the background worker is stopped too"
    card = next(e for e in hub.history(slug) if e["type"] == "agent")
    assert card["status"] == "failed" and card["summary"] == "Stopped by you."
    eng.shutdown()


def test_prompt_never_asks_for_taskoutput():
    assert "never run_in_background" in prompts.PROJECT
    assert "there is no TaskOutput tool" in prompts.PROJECT
    assert "call TaskOutput" not in prompts.PROJECT


# ------------------------------------------------------------------------------- L8
def _busy_session(eng, slug):
    sess = eng.session(slug)
    sess.busy = True
    return sess


def test_watchdog_still_working_line_names_the_command(isolated):
    slug = _bg_project("Dog")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    sess = _busy_session(eng, slug)
    now = time.time()
    sess.last_activity = now - 200
    sess.open_tools["t1"] = {"who": "Reviewer", "line": "Running the full test suite", "t": now - 240}
    found = eng.watch_check(slug, now=now)
    assert found["state"] == "quiet"
    line = [e for e in hub.history(slug) if e["type"] == "watch"][-1]["text"]
    assert line == "Still working: the Reviewer is running the full test suite (4 min so far)."
    # a command still running is not "stuck" at 5 minutes
    found = eng.watch_check(slug, now=now + 200)
    assert found["state"] != "stuck"
    assert not [e for e in hub.history(slug) if e["type"] == "stuck"]
    eng.shutdown()


def test_watchdog_stuck_card_wake_and_restart(isolated):
    slug = _bg_project("Stuck")
    hub = EventHub(projects.history_file)
    prompts_seen = []

    async def script(i, prompt, c):
        prompts_seen.append(prompt)
        yield say("Checked: the Reviewer had hung, starting it again.")

    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    sess = _busy_session(eng, slug)
    now = time.time()
    sess.last_activity = now - 320
    found = eng.watch_check(slug, now=now)
    assert found["state"] == "stuck"
    card = [e for e in hub.history(slug) if e["type"] == "stuck"][-1]
    assert card["status"] == "open" and "Nothing has moved for 5 minutes" in card["text"]
    assert "wake him up" in card["text"]
    eng.watch_check(slug, now=now + 30)
    assert len([e for e in hub.history(slug) if e["type"] == "stuck"]) == 1, "one card per quiet spell"
    # "Wake Fred up" while he is between turns
    sess.busy = False
    sess.background["x"] = {"card": "c", "label": "Reviewer", "started": now - 400}
    assert eng.wake(slug) == "started"
    assert _wait(lambda: prompts_seen)
    assert "Wake Fred up" in prompts_seen[0] and "Still marked as working in the background: Reviewer" in prompts_seen[0]
    card = next(e for e in hub.history(slug) if e["type"] == "stuck")
    assert card["status"] == "resolved"
    eng.shutdown()


def test_watchdog_asks_fred_when_he_is_between_turns(isolated):
    slug = _bg_project("Between")
    hub = EventHub(projects.history_file)
    prompts_seen = []

    async def script(i, prompt, c):
        prompts_seen.append(prompt)
        yield say("Checking.")

    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    sess = eng.session(slug)
    sess.background["x"] = {"card": "c", "label": "Reviewer", "started": time.time() - 400}
    sess.last_activity = time.time() - 330
    eng.watch_check(slug)
    card = [e for e in hub.history(slug) if e["type"] == "stuck"][-1]
    assert "Fred was asked to check" in card["text"]
    assert _wait(lambda: prompts_seen) and prompts_seen[0].startswith("[App] Nothing has happened for 5 minutes")
    eng.shutdown()


def test_watchdog_quiet_when_waiting_for_user_or_idle(isolated):
    slug = _bg_project("Calm")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    sess = eng.session(slug)
    sess.last_activity = time.time() - 1000
    assert eng.watch_check(slug)["state"] == "idle"
    sess.busy = True
    eng.pending["q"] = (None, slug, "approval")
    assert eng.watch_check(slug)["state"] == "waiting", "waiting for the user's answer is not 'stuck'"
    eng.pending.clear()
    assert not [e for e in hub.history(slug) if e["type"] in ("stuck", "watch")]
    eng.shutdown()


def test_restart_step_from_stuck_card(isolated):
    slug = _bg_project("Restart")
    hub = EventHub(projects.history_file)
    seen = []

    async def script(i, prompt, c):
        seen.append(prompt)
        yield say("Starting the step again.")

    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.restart_step(slug)
    assert _wait(lambda: seen) and "restarted the work" in seen[0]
    assert not projects.load(slug).get("pipeline_hold")
    eng.shutdown()


# ------------------------------------------------------------------------------- L6, L9, L13, L14, L15
def _rows(slug, rows):
    from app import projects as P
    d = P.project_dir(slug) / P.META
    d.mkdir(parents=True, exist_ok=True)
    (d / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_stage_and_agent_groups():
    assert timeline.stage_for("Developer", "execute") == "build"
    assert timeline.stage_for("Tester") == "test" and timeline.stage_for("Reviewer") == "review"
    assert timeline.stage_for("Challenger", "discover") == "challenge"
    assert timeline.stage_for("Fred", "discover", problem_done=False) == "problem"
    assert timeline.stage_for("Fred", "discover", problem_done=True) == "requirements"
    assert timeline.stage_for("Mat", "architecture") == "architecture"
    assert timeline.stage_for("Fred", "execute") == "coordination"
    assert timeline.stage_for("Council · Gemini", "plan") == "council"
    assert timeline.agent_group("Council · Gemini") == "Council" and timeline.agent_group("Challenger") == "Devil's advocate"


def test_tokens_are_recorded_with_their_stage(isolated):
    _ready()
    p = projects.create("Stages")
    usage.record_tokens(p["slug"], "Fred", {"input_tokens": 100, "output_tokens": 10}, model="claude-sonnet-5-5")
    projects.update(p["slug"], problem={k: "x" for k in ("who", "how_often", "cost_today", "how_now", "success")})
    usage.record_tokens(p["slug"], "Fred", {"input_tokens": 100, "output_tokens": 10}, model="claude-sonnet-5-5")
    usage.record_tokens(p["slug"], "Tester", {"input_tokens": 100, "output_tokens": 10}, model="claude-sonnet-5-5")
    stages = [r["stage"] for r in usage.project_rows(p["slug"])]
    assert stages == ["problem", "requirements", "test"]


def test_report_per_iteration_agent_phase_and_periods(isolated):
    from datetime import date, timedelta
    from app import analytics
    _ready()
    p = projects.create("Report")
    slug = p["slug"]
    now = time.time()
    projects.update(slug, iterations=[{"n": 1, "name": "One", "status": "done", "started": now - 7200, "finished": now - 3600},
                                      {"n": 2, "name": "Two", "status": "building", "started": now - 600}])
    today = date.today()
    old = today - timedelta(days=40)
    base = {"model": "claude-sonnet-5-5", "cache_read": 0, "cache_write": 0, "cost": 0, "project": slug}
    _rows(slug, [
        {**base, "t": now - 40 * 86400, "day": old.isoformat(), "who": "Fred", "in": 1_000_000, "out": 0, "phase": "discover",
         "stage": "problem", "iteration": None},
        {**base, "t": now - 5000, "day": today.isoformat(), "who": "Developer", "in": 500_000, "out": 100_000,
         "phase": "execute", "stage": "build", "iteration": 1},
        {**base, "t": now - 120, "day": today.isoformat(), "who": "Tester", "in": 200_000, "out": 0, "phase": "execute",
         "stage": "test", "iteration": 2},
        {**base, "t": now - 60, "day": today.isoformat(), "who": "total", "in": 0, "out": 0, "cost": 1.5, "iteration": 2},
    ])
    r = analytics.report(slug, "month")
    # Sonnet 5.5: $2 per million in, $10 per million out
    assert r["total"]["tokens"] == 1_800_000 and abs(r["total"]["cost"] - (2.0 + 1.0 + 1.0 + 0.4)) < 1e-6
    assert r["done"] == 1 and r["planned"] == 2
    its = {it["n"]: it for it in r["iterations"]}
    assert its[0]["tokens"] == 1_000_000 and "Before the iterations" in its[0]["name"]
    assert its[1]["tokens"] == 600_000 and its[1]["by_agent"][0]["name"] == "Developer"
    assert its[2]["by_stage"][0]["label"] == "Test"
    labels = [x["label"] for x in r["periods"]]
    assert labels[0] == "This month" and len(labels) >= 2
    assert r["periods"][0]["tokens"] == 800_000
    assert {a["name"] for a in r["split"]["by_agent"]} == {"Fred", "Developer", "Tester"}
    assert r["activity"]["minutes"][-3] == 200_000 or sum(r["activity"]["minutes"]) == 200_000
    assert r["activity"]["hours"][-1] + r["activity"]["hours"][-2] == 800_000
    y = analytics.report(slug, "year")
    assert y["periods"][0]["label"] == str(today.year)
    a = analytics.report(None, "all")
    assert a["periods"][0]["label"] == "All time" and "projects" in a  # (this test wrote only the project ledger)


def test_activity_per_minute_and_no_token_only_stuck_warning():
    """L18: tokens per minute are shown, but whether work is stuck is left to the watchdog (it knows when a command
    is still running); the screen no longer warns "something may be stuck" from tokens alone."""
    import pathlib
    from app import analytics
    from unittest import mock
    now = 10_000_000.0
    rows = [{"t": now - 600, "in": 10, "out": 0, "cache_write": 0}]
    with mock.patch.object(analytics, "_rows", lambda slug: rows):
        a = analytics.activity("x", now=now)
    assert a["minutes"][49] == 10 and "flat_minutes" not in a
    ui = pathlib.Path(__file__).resolve().parent.parent / "app" / "ui"
    screen = "\n".join(f.read_text() for f in [ui / "app.css", *sorted((ui / "js").glob("*.js"))])
    assert "something may be stuck" not in screen and "flat_minutes" not in screen


def test_time_by_stage_keeps_waiting_apart(isolated):
    _ready()
    p = projects.create("Time")
    slug = p["slug"]
    projects.update(slug, phase="execute")
    t = time.time() - 3600
    timeline.record(slug, "lead", "Fred", t, t + 1000)          # a turn of 1000 s ...
    timeline.record(slug, "worker", "Developer", t + 100, t + 700)   # ... with a 600 s build inside
    timeline.record(slug, "wait", "Fred", t + 800, t + 900)          # ... and 100 s waiting for the user
    timeline.record(slug, "worker", "Tester", t + 2000, t + 2300)    # a background test after the turn
    res = timeline.time_by_stage(slug)
    st = {x["stage"]: x for x in res["stages"]}
    assert st["build"]["work"] == 600 and st["test"]["work"] == 300
    assert st["coordination"]["work"] == 300 and st["coordination"]["wait"] == 100
    assert res["work"] == 1200 and res["wait"] == 100
    assert (projects.project_dir(slug) / ".aiteam" / "time.jsonl").exists(), "the time ledger travels with the project"


def test_wait_for_an_answer_is_counted(isolated):
    slug = _bg_project("Wait")
    projects.update(slug, phase="discover")
    hub = EventHub(projects.history_file)

    async def script(i, prompt, c):
        yield say("Who uses the report?" if i == 0 else "Thanks.")

    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(slug, "hello")
    assert _wait(lambda: not eng.is_busy(slug) and eng.session(slug).turn_ended)
    eng.session(slug).turn_ended -= 120   # the user took two minutes to answer
    eng.send_message(slug, "The accounts team")
    assert _wait(lambda: [r for r in timeline.rows(slug) if r["kind"] == "wait"])
    waits = [r for r in timeline.rows(slug) if r["kind"] == "wait"]
    assert len(waits) == 1 and 115 < waits[0]["t1"] - waits[0]["t0"] < 130
    eng.shutdown()


def test_headroom_from_claude_and_warning(isolated, monkeypatch):
    from app import analytics
    data = {"five_hour": {"type": "five_hour", "label": "x", "status": "allowed_warning", "percent": 82.0,
                          "percent_at": time.time(), "source": "claude_usage", "reset_text": "7:30pm"},
            "seven_day": {"type": "seven_day", "label": "y", "status": "allowed", "percent": 10.0,
                          "percent_at": time.time(), "source": "claude_usage", "reset_text": "Fri 8pm"}}
    monkeypatch.setattr(usage, "plan", lambda: data)
    h = {x["kind"]: x for x in analytics.headroom()}
    assert h["five_hour"]["left"] == 18.0 and h["five_hour"]["from_claude"]
    w = analytics.headroom_warning()
    assert w.startswith("About 18% of your 5-hour limit is left (resets 7:30pm)")
    data["five_hour"]["percent"] = 50.0
    assert analytics.headroom_warning() == ""


def test_headroom_card_before_an_iteration(isolated, monkeypatch):
    from app import analytics, auth
    slug = _bg_project("Room")
    monkeypatch.setattr(auth, "mode", lambda: "subscription")
    monkeypatch.setattr(auth, "ready", lambda: True)
    monkeypatch.setattr(usage, "warning_note", lambda: "")
    monkeypatch.setattr(analytics, "headroom_warning", lambda: "About 10% of your 5-hour limit is left.")
    hub = EventHub(projects.history_file)

    async def script(i, prompt, c):
        yield say("Starting.")

    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(slug, "[App] The approved plan continues: start iteration 1 (x) now with start_iteration(1)", hidden=True)
    assert _wait(lambda: any(e["type"] == "headroom" for e in hub.history(slug)))
    eng.shutdown()


def test_analytics_endpoint(isolated):
    import urllib.request
    from app.server import App, serve
    slug = _bg_project("Api")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, url = serve(app)
    base = url.split("/?")[0]
    req = urllib.request.Request(f"{base}/api/analytics?project={slug}&unit=day", headers={"X-Token": app.token})
    data = json.loads(urllib.request.urlopen(req).read())
    assert data["project"]["slug"] == slug and data["unit"] == "day" and "iterations" in data and data["state"] == "idle"
    req = urllib.request.Request(f"{base}/api/projects/{slug}/activity", headers={"X-Token": app.token})
    act = json.loads(urllib.request.urlopen(req).read())
    assert len(act["minutes"]) == 60 and len(act["hours"]) == 24
    httpd.shutdown()
    eng.shutdown()


# ------------------------------------------------------------------------------- L5
def test_why_notes_and_questions_summary(isolated):
    from app import challenge
    _ready()
    p = projects.create("Why")
    f = challenge.normalize([{"kind": "assumption", "title": "Files are always CSV"},
                             {"kind": "objection", "title": "Excel does it", "why": "Tools that duplicate Excel go unused."}])
    assert f[0]["why"] == challenge.KIND_WHY["assumption"] and f[1]["why"] == "Tools that duplicate Excel go unused."
    assert set(challenge.PROBLEM_WHY) == {k for k, _ in challenge.PROBLEM_FIELDS}
    qs = challenge.normalize_questions([{"question": "Does Excel already do this?", "why": "Avoid duplicates",
                                         "changed": "We reuse the existing report"}, "Who maintains it?", {"x": 1}])
    assert len(qs) == 2 and qs[1]["question"] == "Who maintains it?"
    challenge.save_questions(p["slug"], "An app to retype invoices", "Invoices read automatically", qs)
    md = (projects.project_dir(p["slug"]) / "reports" / "QUESTIONS.md").read_text()
    assert "# The questions that changed your project" in md and "Invoices read automatically" in md
    assert "We reuse the existing report" in md and "Questions to ask on your next project" in md
    assert projects.load(p["slug"])["questions"]["real_need"] == "Invoices read automatically"


def test_record_questions_tool_and_prompts(isolated):
    from app import tools
    slug = _bg_project("Tool")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    sess = eng.session(slug)
    projects.update(slug, phase="discover")
    _, chosen = tools.build_server(sess)
    handler = {t.name: t.handler for t in chosen}["record_questions"]
    out = asyncio.run_coroutine_threadsafe(handler({"first_ask": "a", "real_need": "b", "questions": [
        {"question": "q1", "why": "w", "changed": "c"}]}), eng.loop).result(5)
    assert "QUESTIONS.md" in out["content"][0]["text"]
    assert any(e["type"] == "questions" and e["real_need"] == "b" for e in hub.history(slug))
    assert "record_questions" in prompts.PROJECT and "*Why I ask:*" in prompts.PROJECT
    assert '"why"' in prompts.CHALLENGER
    from app import policy
    assert policy.decide("mcp__aiteam__record_questions", {}, kind="project", mode="plan", plan_approved=False,
                         inside_project=lambda p: True, allowed_roots=[])[0] == "allow"
    eng.shutdown()


# ------------------------------------------------------------------------------- L11
def test_microphone_problems_are_explained():
    from app import voice
    assert voice.explain(voice.MicSilent("x"))[1] == "blocked"
    assert voice.explain(Exception("Error querying device -1"))[1] == "no_mic"
    assert voice.explain(Exception("Error opening InputStream: Device unavailable [PaErrorCode -9985]"))[1] == "busy"
    assert voice.explain(Exception("Unable to open file 'model.bin' in model"))[1] == "model"
    assert "Let desktop apps access your microphone" in voice.explain(voice.MicSilent("x"))[0]


def test_silent_microphone_says_windows_blocks_it(isolated):
    import numpy as np
    from app import voice
    events = []
    zeros = [np.zeros(4000, dtype="float32") for _ in range(8)]
    voice.Listener(events.append, transcribe=lambda a, f: "", source=iter(zeros)).start().thread.join(5)
    assert events[-1]["state"] == "error" and events[-1]["code"] == "blocked"
    assert any("no sound at all" in line for line in voice.recent_problems()), "written to the log for feedback"
    quiet = [np.full(4000, 0.004, dtype="float32") for _ in range(8)]
    events.clear()
    voice.Listener(events.append, transcribe=lambda a, f: "", source=iter(quiet)).start().thread.join(5)
    assert events[-1]["state"] == "cancelled" and events[-1]["code"] == "quiet"


def test_microphone_test_reports_level_and_device(isolated):
    import numpy as np
    from app import voice
    levels = []
    speech = [np.full(1600, 0.2 if i % 2 else 0.01, dtype="float32") for i in range(10)]
    res = voice.test_microphone(levels.append, seconds=0.05, source=iter(speech))
    assert res["ok"] and res["code"] == "ok" and levels
    res = voice.test_microphone(levels.append, seconds=5, source=iter([np.zeros(1600, dtype="float32")] * 3))
    assert not res["ok"] and res["code"] == "blocked"

    def broken():
        raise Exception("Error querying device -1")
        yield  # noqa
    res = voice.test_microphone(levels.append, source=broken())
    assert not res["ok"] and res["code"] == "no_mic"


# ------------------------------------------------------------------------------- L12
def test_name_can_be_changed_in_settings(isolated):
    import urllib.request
    from app.server import App, serve
    _ready()
    settings.save({"user_name": "Fred"})
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, url = serve(app)
    base = url.split("/?")[0]
    req = urllib.request.Request(f"{base}/api/settings", data=json.dumps({"user_name": "  Anne  "}).encode(),
                                 headers={"X-Token": app.token, "Content-Type": "application/json"})
    st = json.loads(urllib.request.urlopen(req).read())
    assert st["settings"]["user_name"] == "Anne"
    ui = engine_mod.Path(__file__).resolve().parent.parent / "app" / "ui"
    html = "\n".join(f.read_text() for f in [ui / "index.html", *sorted((ui / "js").glob("*.js"))])
    assert "'with ' + S.settings.user_name" not in html, "the name is not shown as part of the product name"
    httpd.shutdown()
    eng.shutdown()



# ------------------------------------------------------------------------------- L16: the screen in several files
def test_screen_files_are_served_with_their_types(isolated):
    import urllib.request
    from app.server import App, serve
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, url = serve(app)
    base = url.split("/?")[0]
    page = urllib.request.urlopen(url).read().decode()
    scripts = __import__("re").findall(r'src="(/ui/js/[\w.]+)"', page)
    assert len(scripts) >= 5 and '/ui/app.css' in page and 'const TOKEN = "' + app.token in page
    for src in scripts + ["/ui/app.css"]:
        r = urllib.request.urlopen(base + src)
        assert r.headers["Content-Type"].startswith("text/javascript" if src.endswith(".js") else "text/css")
    httpd.shutdown()
    eng.shutdown()


# ------------------------------------------------------------------------------- L17: approvals from the phone
class FakeTelegram:
    """Stands in for the Telegram Bot API: records what the app sends, returns scripted updates."""

    def __init__(self):
        self.sent, self.edits, self.answers, self.updates = [], [], [], []
        self.next_id = 100

    def __call__(self, token, method, params, timeout=15):
        if method == "getMe":
            return {"username": "my_ai_team_bot", "first_name": "My AI Team"}
        if method == "sendMessage":
            self.next_id += 1
            self.sent.append(params)
            return {"message_id": self.next_id}
        if method == "editMessageText":
            self.edits.append(params)
            return {}
        if method == "answerCallbackQuery":
            self.answers.append(params)
            return True
        if method == "getUpdates":
            out, self.updates = self.updates, []
            time.sleep(0.05)
            return out
        raise AssertionError(method)


def _phone(engine_hub=None, linked=True):
    from app import telegram
    hub = engine_hub or EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    fake = FakeTelegram()
    bridge = telegram.Bridge(eng, api=fake)
    eng.telegram = bridge
    secrets_store.set_secret("telegram_bot_token", "123:abc")
    if linked:
        settings.save({"telegram_chat_id": 4242})
    return eng, bridge, fake, hub


def _press(bridge, data, chat=4242, sender=4242):
    bridge.handle_update({"update_id": 1, "callback_query": {"id": "q1", "data": data, "from": {"id": sender},
                                                              "message": {"chat": {"id": chat}}}})


def test_phone_messages_and_buttons():
    from app import telegram
    text, buttons = telegram.card_message("approval", {"title": "Run a command", "who": "Developer",
                                                       "what": "Installs the test tools.", "risk": "medium",
                                                       "details": "pip install secret-thing"}, "Invoice tracker", False)
    assert "Invoice tracker" in text and "Developer wants to: Run a command" in text and "medium risk" in text
    assert "pip install" not in text, "the exact command never leaves the PC"
    assert [b["data"] for b in buttons[0]] == ["b0", "b1"]
    text, buttons = telegram.card_message("approval", {"title": "Install software", "risk": "high"}, "P", False)
    assert buttons == [] and "needs you at the PC" in text
    assert telegram.card_message("approval", {"title": "x", "risk": "high"}, "P", True)[1]
    assert telegram.card_message("secret", {"title": "Paste your key"}, "P", True)[1] == []
    assert telegram.answer_payload("approval", {}, "b0") == {"approved": True, "note": "", "via": "phone"}
    assert telegram.answer_payload("plan", {}, "b0")["approved"] is True
    assert telegram.answer_payload("choice", {"options": ["Yes", "No"]}, "b1")["value"] == "No"
    assert telegram.answer_payload("choice", {"options": ["Yes"]}, "b7") is None
    assert telegram.answer_payload("secret", {}, "b0") is None


def test_link_phone_with_a_code(isolated):
    eng, bridge, fake, hub = _phone(linked=False)
    info = bridge.begin_link()
    assert info["bot"] == "my_ai_team_bot" and len(info["code"]) == 6 and info["url"].endswith("my_ai_team_bot")
    bridge.handle_update({"update_id": 1, "message": {"chat": {"id": 99, "type": "group"}, "text": info["code"]}})
    assert bridge.chat_id() is None, "only a private chat can be linked"
    bridge.handle_update({"update_id": 2, "message": {"chat": {"id": 4242, "type": "private"}, "text": "nope"}})
    assert bridge.chat_id() is None
    bridge.handle_update({"update_id": 3, "message": {"chat": {"id": 4242, "type": "private"},
                                                      "text": f"/start {info['code']}"}})
    assert bridge.chat_id() == 4242 and bridge.link is None
    assert "linked" in fake.sent[-1]["text"]
    assert bridge.status()["connected"] and bridge.status()["bot"] == "my_ai_team_bot"
    bridge.stop()
    eng.shutdown()


def test_approve_from_the_phone(isolated):
    slug = _bg_project("Phone")
    eng, bridge, fake, hub = _phone()
    sess = eng.session(slug)
    fut = asyncio.run_coroutine_threadsafe(sess.ask_user("approval", {"title": "Run a command", "who": "Tester",
                                                                      "what": "Runs the tests.", "risk": "low"}),
                                           eng.loop)
    assert _wait(lambda: bridge.cards and fake.sent)
    item_id = next(iter(bridge.cards))
    msg = fake.sent[-1]
    assert msg["chat_id"] == 4242 and "Tester wants to: Run a command" in msg["text"]
    data = msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert data == f"c:{item_id}:b0"
    _press(bridge, data, chat=777, sender=777)   # a stranger who found the bot
    assert not fut.done() and not fake.answers, "other chats are ignored and get no answer"
    _press(bridge, data)
    assert fut.result(5)["approved"] is True
    assert fake.answers[-1]["text"] == "Sent to Fred." and "approved from your phone" in fake.edits[-1]["text"]
    card = next(e for e in hub.history(slug) if e["type"] == "approval")
    assert card["status"] == "approved" and card["via"] == "phone"
    _press(bridge, data)
    assert fake.answers[-1]["text"] == "This request is no longer waiting."
    bridge.stop()
    eng.shutdown()


def test_answered_at_the_pc_updates_the_phone(isolated):
    slug = _bg_project("Pc")
    eng, bridge, fake, hub = _phone()
    fut = asyncio.run_coroutine_threadsafe(eng.session(slug).ask_user("choice", {"title": "Desktop or web?",
                                                                                 "options": ["Desktop", "Web"]}), eng.loop)
    assert _wait(lambda: bridge.cards)
    item_id = next(iter(bridge.cards))
    assert [r[0]["text"] for r in fake.sent[-1]["reply_markup"]["inline_keyboard"]] == ["Desktop", "Web"]
    eng.respond(item_id, {"value": "Web"})
    assert fut.result(5)["value"] == "Web"
    assert _wait(lambda: fake.edits and "Answered at the PC" in fake.edits[-1]["text"])
    bridge.stop()
    eng.shutdown()


def test_stuck_alert_wakes_fred_from_the_phone(isolated, monkeypatch):
    slug = _bg_project("Alert")
    eng, bridge, fake, hub = _phone()
    woke = []
    monkeypatch.setattr(eng, "wake", lambda s: woke.append(s) or "started")
    sess = eng.session(slug)
    sess.busy = True
    sess.last_activity = time.time() - 400
    eng.watch_check(slug)
    assert _wait(lambda: any("Nothing has moved" in m["text"] for m in fake.sent))
    alert = next(m for m in fake.sent if "Nothing has moved" in m["text"])
    wake_btn = alert["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert wake_btn == f"s:{slug}:wake"
    _press(bridge, wake_btn)
    assert woke == [slug] and fake.answers[-1]["text"] == "Fred was woken up."
    bridge.stop()
    eng.shutdown()


def test_nothing_is_sent_without_a_linked_phone(isolated):
    slug = _bg_project("Quiet")
    eng, bridge, fake, hub = _phone(linked=False)
    fut = asyncio.run_coroutine_threadsafe(eng.session(slug).ask_user("approval", {"title": "x"}), eng.loop)
    time.sleep(0.3)
    assert not fake.sent and not bridge.cards
    eng.stop(slug)   # cancels the waiting card
    assert fut.result(5).get("cancelled")
    eng.shutdown()


def test_setup_asks_permission_and_links_the_phone(isolated):
    from app import tools
    assert "ASK PERMISSION FIRST" in prompts.SETUP and "Nothing is installed on the PC" in prompts.SETUP
    assert "connect_phone" in prompts.SETUP and "@BotFather" in prompts.SETUP
    assert "telegram_bot_token" in secrets_store.KNOWN
    eng, bridge, fake, hub = _phone(linked=False)
    sess = eng.session(projects.SETUP_SLUG)
    _, chosen = tools.build_server(sess)
    handler = {t.name: t.handler for t in chosen}["connect_phone"]
    fut = asyncio.run_coroutine_threadsafe(handler({}), eng.loop)
    assert _wait(lambda: any(e["type"] == "telegram" for e in hub.history(projects.SETUP_SLUG)))
    card = next(e for e in hub.history(projects.SETUP_SLUG) if e["type"] == "telegram")
    assert card["state"] == "linking" and card["bot"] == "my_ai_team_bot"
    bridge.handle_update({"update_id": 5, "message": {"chat": {"id": 4242, "type": "private"}, "text": card["code"]}})
    out = fut.result(10)["content"][0]["text"]
    assert out.startswith("Linked") and "High-risk approvals stay at the PC" in out
    card = next(e for e in hub.history(projects.SETUP_SLUG) if e["type"] == "telegram")
    assert card["state"] == "linked"
    bridge.stop()
    eng.shutdown()


def test_phone_settings_endpoints(isolated):
    import urllib.error
    import urllib.request
    from app import telegram
    from app.server import App, serve
    _ready()
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    fake = FakeTelegram()
    app.telegram.api = fake
    httpd, url = serve(app)
    base = url.split("/?")[0]

    def post(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(),
                                     headers={"X-Token": app.token, "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())
    assert post("/api/telegram/settings", {})["telegram"]["connected"] is False
    try:
        post("/api/telegram/test")
        raise AssertionError("must refuse without a phone")
    except urllib.error.HTTPError as e:
        assert e.code == 400
    secrets_store.set_secret(telegram.TOKEN_NAME, "123:abc")
    settings.save({"telegram_chat_id": 4242, "telegram_bot": "my_ai_team_bot"})
    assert post("/api/telegram/test")["telegram"]["connected"] and "Test from AI Team Lite" in fake.sent[-1]["text"]
    assert post("/api/telegram/settings", {"high_risk_on_phone": True})["telegram"]["high_risk_on_phone"] is True
    st = post("/api/telegram/disconnect")["telegram"]
    assert st["connected"] is False and not secrets_store.has(telegram.TOKEN_NAME)
    httpd.shutdown()
    eng.shutdown()



def test_publish_check_catches_a_telegram_key(isolated, tmp_path):
    from app import updates
    (tmp_path / "notes.txt").write_text("bot key 123456789:AAH" + "x" * 33)
    with pytest.raises(RuntimeError, match="looks like it contains a secret"):
        updates.scan_for_secrets(tmp_path)


# ------------------------------------------------------------------------------- 1.2.0
def test_screenshot_never_captures_the_whole_desktop(isolated, monkeypatch, tmp_path):
    """L19: no window found -> no picture (never all screens); the window is found in the whole process tree."""
    import inspect
    from app import paths as P, winhelpers
    src = inspect.getsource(winhelpers.screenshot_command)
    assert "ImageGrab" not in src and "all_screens" not in src
    monkeypatch.setattr(P, "IS_WINDOWS", False)
    with pytest.raises(winhelpers.NoWindow):
        winhelpers.screenshot_command("run.bat", tmp_path, tmp_path / "x.png")
    assert not (tmp_path / "x.png").exists()
    # run.bat (100) -> cmd (200) -> python (300) owns the window: found although it is two levels down
    monkeypatch.setattr(winhelpers, "_visible_windows", lambda: [(11, 999), (22, 300)])
    monkeypatch.setattr(winhelpers, "_descendants", lambda pid: {100, 200, 300})
    assert winhelpers.find_app_window(100, before={11}) == 22
    # nothing of ours, but exactly one new window appeared after the launch
    monkeypatch.setattr(winhelpers, "_descendants", lambda pid: {100})
    assert winhelpers.find_app_window(100, before={11}) == 22
    # several unknown new windows (a film starting on another screen...): no guessing
    monkeypatch.setattr(winhelpers, "_visible_windows", lambda: [(11, 999), (22, 300), (33, 400)])
    assert winhelpers.find_app_window(100, before={11}) is None


def test_short_test_output_and_full_log(isolated, tmp_path):
    from app import testrun
    out = ("============================= test session starts ==============================\n"
           + "".join(f"tests/test_a.py::test_ok_{i} PASSED\n" for i in range(200)) +
           "_________________________________ test_total __________________________________\n"
           "    def test_total():\n>       assert total([1, 2]) == 4\nE       assert 3 == 4\n"
           "E        +  where 3 = total([1, 2])\n\ntests/test_calc.py:8: AssertionError\n"
           "=========================== short test summary info ============================\n"
           "FAILED tests/test_calc.py::test_total - assert 3 == 4\n"
           "======================== 1 failed, 200 passed in 1.23s =========================\n")
    log = testrun.save_log(tmp_path, "iteration 2", "python -m pytest -q", out)
    s = testrun.summarize(out, False, log)
    assert s.startswith("Tests FAILED. 1 failed, 200 passed in 1.23s")
    assert "FAILED tests/test_calc.py::test_total" in s and "E       assert 3 == 4" in s
    assert "tests/test_calc.py:8: AssertionError" in s and "PASSED" not in s
    assert len(s) < 800 < len(out) and log.startswith("reports/test-logs/") and (tmp_path / log).read_text().endswith(out)
    ok = testrun.summarize("..........\n10 passed in 0.5s\n", True, "")
    assert ok == "Tests passed. 10 passed in 0.5s"
    crash = testrun.summarize("Traceback...\nModuleNotFoundError: No module named 'flask'\n", False, "")
    assert "No module named 'flask'" in crash
    for i in range(25):
        testrun.save_log(tmp_path, f"run {i}", "x", "y")
    assert len(list((tmp_path / "reports" / "test-logs").glob("*.log"))) <= testrun.KEEP_LOGS


def test_affected_tests_only_while_building(isolated, tmp_path):
    from app import testrun
    (tmp_path / "tests").mkdir()
    (tmp_path / "calc.py").write_text("def total(x): return sum(x)\n")
    (tmp_path / "report.py").write_text("def make(): pass\n")
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import total\n")
    (tmp_path / "tests" / "test_report.py").write_text("import report\n")
    (tmp_path / "tests" / "test_other.py").write_text("def test_x(): pass\n")
    old = time.time() - 3600
    for f in tmp_path.rglob("*.py"):
        __import__("os").utime(f, (old, old))
    since = time.time() - 60
    (tmp_path / "calc.py").write_text("def total(x): return sum(x) + 0\n")
    cmd, note = testrun.affected(tmp_path, "python -m pytest -q tests", since)
    assert cmd == "python -m pytest -q tests/test_calc.py" and note.startswith("1 related test file")
    assert testrun.affected(tmp_path, "python run_my_tests.py", since)[0] == "python run_my_tests.py"
    (tmp_path / "notes.py").write_text("x = 1\n")
    (tmp_path / "calc.py").touch()
    __import__("os").utime(tmp_path / "calc.py", (old, old))
    cmd, note = testrun.affected(tmp_path, "python -m pytest -q", since)
    assert cmd == "python -m pytest -q" and note.startswith("all tests"), "nothing related: run everything"


def test_commit_still_runs_the_full_suite(isolated, monkeypatch):
    from app import iterations, testrun
    _ready()
    p = projects.create("Full")
    slug = p["slug"]
    projects.update(slug, plan_approved=True, iterations=[{"n": 1, "name": "One", "status": "building", "red": {"failed": True}}])
    ran = []
    monkeypatch.setattr(testrun, "run", lambda cmd, cwd, timeout=300: (ran.append(cmd), (False, "E boom\nFAILED t.py::x\n1 failed in 0.1s"))[1])
    r = iterations.commit(slug, 1, "s", "python -m pytest -q")
    assert ran == ["python -m pytest -q"] and not r["ok"] and r["output"].startswith("Tests FAILED")


def test_project_map(isolated, tmp_path):
    from app import projectmap
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "calc.py").write_text('"""Adds up the invoice lines."""\ndef total(x): return sum(x)\n')
    (tmp_path / "app" / "util.py").write_text("def a(): pass\nclass B: pass\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("# checks the totals\nfrom app.calc import total\n")
    (tmp_path / "run.bat").write_text("@echo off\npython -m app\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("x")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    assert projectmap.refresh(tmp_path)
    m = (tmp_path / "PROJECT_MAP.md").read_text()
    assert "| `app/calc.py` | Adds up the invoice lines. |" in m and "defines a, B" in m
    assert "**Start:** `run.bat`" in m and "**Tests:** 1 file(s) in `tests`" in m
    assert ".venv" not in m and "logo.png" not in m
    assert not projectmap.refresh(tmp_path), "unchanged files: the map is not rewritten"
    (tmp_path / "app" / "calc.py").write_text('"""Adds up the lines and the taxes."""\n')
    assert projectmap.refresh(tmp_path) and "the taxes" in (tmp_path / "PROJECT_MAP.md").read_text()


def test_lessons(isolated):
    from app import lessons
    a = lessons.add("On this PC, run the tests with the bundled Python, not py.", "p1")
    b = lessons.add("on this pc run the tests with the bundled python not py", "p2")
    assert a["id"] == b["id"] and len(lessons.load()) == 1, "the same lesson is kept once"
    with pytest.raises(ValueError):
        lessons.add("The key is sk-ant-" + "a" * 30)
    with pytest.raises(ValueError):
        lessons.add("ok")
    home = str(__import__("pathlib").Path.home())
    c = lessons.add(f"Excel files under {home}\\Documents are locked while open.")
    assert home not in c["text"] and "~" in c["text"]
    assert "run the tests with the bundled Python" in lessons.note()
    assert lessons.delete(a["id"]) and all(i["id"] != a["id"] for i in lessons.load())


def test_lessons_reach_the_team_and_settings(isolated):
    import urllib.request
    from app import lessons, tools
    from app.server import App, serve
    slug = _bg_project("Learn")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    sess = eng.session(slug)
    _, chosen = tools.build_server(sess)
    handler = {t.name: t.handler for t in chosen}["record_lesson"]
    out = asyncio.run_coroutine_threadsafe(handler({"lesson": "Streamlit needs the --server.headless flag here."}),
                                           eng.loop).result(5)
    assert out["content"][0]["text"].startswith("Lesson kept")
    opts = sess.build_options()
    assert "Streamlit needs the --server.headless flag" in opts.system_prompt["append"]
    assert "Streamlit needs the --server.headless flag" in opts.agents["developer"].prompt
    assert (projects.project_dir(slug) / "PROJECT_MAP.md").exists(), "the map is ready when the team starts"
    app = App(engine=eng, hub=hub)
    httpd, url = serve(app)
    base = url.split("/?")[0]
    items = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/lessons",
                                                                      headers={"X-Token": app.token})).read())["items"]
    assert items[0]["text"].startswith("Streamlit")
    req = urllib.request.Request(base + "/api/lessons/delete", data=json.dumps({"id": items[0]["id"]}).encode(),
                                 headers={"X-Token": app.token, "Content-Type": "application/json"})
    assert json.loads(urllib.request.urlopen(req).read())["items"] == [] and lessons.load() == []
    httpd.shutdown()
    eng.shutdown()


def test_instructions_keep_the_fixed_part_first():
    """L24: what changes during a project sits at the end, so the long fixed part is re-used from Claude's cache."""
    a = prompts.render(prompts.PROJECT, name="Alpha", folder="C:/p/a", phase="discover", existing="", github="no",
                       architecture_approved="no", plan_approved="no", iterations="none", models="m", language="Python",
                       conventions="c", challenge="x")
    b = prompts.render(prompts.PROJECT, name="Beta", folder="C:/p/b", phase="execute", existing="", github="yes",
                       architecture_approved="yes", plan_approved="yes", iterations="1. A [done]", models="m",
                       language="Python", conventions="c", challenge="x")
    same = next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)
    assert same > 0.9 * len(a), "two different projects share over 90% of the instructions, from the start"
    assert a.rstrip().splitlines()[-3].startswith('Project: "Alpha"') or "## This project" in a[same - 200:]
    assert "run_tests" in prompts.PROJECT and "PROJECT_MAP.md" in prompts.DEVELOPER and "run_tests" in prompts.TESTER
    assert "record_lesson" in prompts.PROJECT and "PROJECT_MAP.md" in prompts.REVIEWER


# ---------------------------------------------------------------- L25: automatic restart after a plan limit
def _limit_info(kind, resets_at, status="rejected", utilization=None):
    return type("I", (), {"rate_limit_type": kind, "status": status, "utilization": utilization, "resets_at": resets_at})()


def test_l25_usage_reading_does_not_hide_the_limit_reached(isolated):
    """A fresh /usage reading used to replace the engine's 'limit reached' report, losing its reset time."""
    data = usage.plan()
    data["five_hour"] = {"type": "five_hour", "label": "x", "status": "allowed_warning", "percent": 97.0,
                         "percent_at": time.time(), "seen": time.time(), "source": "claude_usage",
                         "reset_text": "7:30pm", "resets_at": None}
    usage._plan_file().write_text(json.dumps(data), encoding="utf-8")
    when = time.time() + 1800
    entry = usage.record_limit(_limit_info("five_hour", when))
    stored = usage.plan()["five_hour"]
    assert entry["resets_at"] == when and stored["resets_at"] == when
    assert stored["status"] == "rejected" and stored["percent"] >= 100


def test_l25_reset_text_becomes_a_time():
    from datetime import datetime
    now = datetime(2026, 10, 14, 15, 0).timestamp()   # a Wednesday, 3pm
    at = lambda t: datetime.fromtimestamp(usage.reset_time(t, now))
    assert at("7:30pm") == datetime(2026, 10, 14, 19, 30)
    assert at("resets 9am") == datetime(2026, 10, 15, 9, 0)
    assert at("Fri 8pm") == datetime(2026, 10, 16, 20, 0)
    assert at("Oct 16 at 8pm (Europe/Paris)") == datetime(2026, 10, 16, 20, 0)
    assert at("Jan 2, 10am") == datetime(2027, 1, 2, 10, 0)
    assert usage.reset_time("soon", now) is None and usage.reset_time("", now) is None


def test_l25_rejected_event_after_reading_schedules_auto_continue(isolated):
    _ready()
    p = projects.create("Limit after reading")
    data = usage.plan()
    data["five_hour"] = {"type": "five_hour", "status": "allowed_warning", "percent": 99.0, "percent_at": time.time(),
                         "seen": time.time(), "source": "claude_usage", "reset_text": "", "resets_at": None}
    usage._plan_file().write_text(json.dumps(data), encoding="utf-8")
    entry = usage.record_limit(_limit_info("five_hour", time.time() + 3600))
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    eng.on_limit(p["slug"], {**entry, "label": "Current session (5 hours)"})
    card = next(e for e in hub.history(p["slug"]) if e["type"] == "limit")
    assert card["auto"] and card["continue_at"] and card["resets_at"]
    eng.cancel_auto_continue(p["slug"])
    eng.shutdown()


def test_l25_unknown_reset_time_checks_and_continues(isolated, monkeypatch):
    _ready()
    p = projects.create("Unknown reset")
    monkeypatch.setattr(engine_mod, "LIMIT_CHECK_SECONDS", 0.2)
    calls = []

    def fake_fetch(force=False):
        calls.append(force)
        data = usage.plan()
        full = len(calls) < 2
        data["five_hour"] = {"type": "five_hour", "status": "rejected" if full else "allowed", "percent": 100.0 if full else 3.0,
                             "percent_at": time.time(), "seen": time.time(), "source": "claude_usage", "resets_at": None}
        usage._plan_file().write_text(json.dumps(data), encoding="utf-8")
        return data
    monkeypatch.setattr(usage, "fetch_plan", fake_fetch)

    async def script(i, prompt, c):
        yield say("Back at work.")
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.on_limit(p["slug"], {"type": "five_hour", "label": "Current session (5 hours)", "resets_at": None})
    card = next(e for e in hub.history(p["slug"]) if e["type"] == "limit")
    assert card["checking"] and not card["auto"]
    assert _wait(lambda: any(e["type"] == "message" and "Back at work" in e.get("text", "") for e in hub.history(p["slug"])))
    assert len(calls) >= 2 and all(calls)
    card = next(e for e in hub.history(p["slug"]) if e["type"] == "limit")
    assert card["status"] == "continued"
    eng.shutdown()


def test_l25_cancel_stops_the_checks(isolated, monkeypatch):
    _ready()
    p = projects.create("Cancel checks")
    monkeypatch.setattr(engine_mod, "LIMIT_CHECK_SECONDS", 0.2)
    calls = []
    monkeypatch.setattr(usage, "fetch_plan", lambda force=False: calls.append(1) or {})
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    eng.on_limit(p["slug"], {"type": "five_hour", "label": "x", "resets_at": None})
    time.sleep(0.05)
    eng.cancel_auto_continue(p["slug"])
    time.sleep(0.6)
    assert not calls
    card = next(e for e in hub.history(p["slug"]) if e["type"] == "limit")
    assert card["status"] == "waiting" and not card["checking"]
    eng.shutdown()


def test_l25_weekly_limit_named_from_the_error(isolated):
    _ready()
    p = projects.create("Weekly")

    class WeekClient(FakeClient):
        async def receive_response(self):
            yield ResultMessage(subtype="error", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1,
                                session_id="s", result="You've hit your weekly usage limit", api_error_status=429)
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: WeekClient(o, None))
    eng.send_message(p["slug"], "go on")
    assert _wait(lambda: any(e["type"] == "limit" for e in hub.history(p["slug"])))
    card = next(e for e in hub.history(p["slug"]) if e["type"] == "limit")
    assert "week" in card["label"].lower()
    eng.cancel_auto_continue(p["slug"])
    eng.shutdown()


# ---------------------------------------------------------------- L26: cards vanished after a restart
def test_l26_event_numbers_never_repeat_after_a_restart(isolated):
    p = projects.create("Long project")
    first = EventHub(projects.history_file)
    old = [first.publish(p["slug"], {"type": "message", "role": "fred", "text": f"old {i}"})["seq"] for i in range(5)]
    time.sleep(0.01)
    second = EventHub(projects.history_file)           # the app starts again
    card = second.publish(p["slug"], {"type": "approval", "id": "abc", "status": "pending", "title": "Run a command"})
    assert card["seq"] > max(old)
    seqs = [e["seq"] for e in second.history(p["slug"])]
    assert len(seqs) == len(set(seqs)) and seqs[-1] == card["seq"]
    assert second.wait(card["seq"] - 1, timeout=0.1)[0]["id"] == "abc"   # the live stream still delivers it
