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


def test_activity_flat_minutes():
    from app import analytics
    from unittest import mock
    now = 10_000_000.0
    rows = [{"t": now - 600, "in": 10, "out": 0, "cache_write": 0}]
    with mock.patch.object(analytics, "_rows", lambda slug: rows):
        a = analytics.activity("x", now=now)
    assert a["minutes"][49] == 10 and a["flat_minutes"] == 10


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
