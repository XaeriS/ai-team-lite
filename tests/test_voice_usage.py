import time

import numpy as np
import pytest

from app import projects, secrets_store, settings, usage, voice
from app.events import EventHub


# ------------------------------------------------------------------------------- end word
@pytest.mark.parametrize("text,word,expected", [
    ("Add a login page. Copy.", "copy", "Add a login page"),
    ("add a login page copy", "copy", "add a login page"),
    ("Make it blue, over and out!", "over and out", "Make it blue"),
    ("I want to copy files", "copy", None),
    ("Copy the invoice table", "copy", None),
    ("", "copy", None),
])
def test_strip_end_word(text, word, expected):
    assert voice.strip_end_word(text, word) == expected


# ------------------------------------------------------------------------------- listener
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_source(clock, pattern):
    """pattern: list of 'v' (voice) / 's' (silence) per 0.25 s chunk."""
    def gen():
        for p in pattern:
            clock.t += 0.25
            amp = 0.2 if p == "v" else 0.001
            yield (np.random.default_rng(0).standard_normal(4000) * amp).astype("float32")
    return gen()


def run_listener(pattern, transcribe, **cfg):
    settings.save({"voice_end_mode": "both", "voice_end_word": "copy", "voice_silence_seconds": 2, **cfg})
    events = []
    clock = Clock()
    lis = voice.Listener(events.append, transcribe=transcribe, source=make_source(clock, pattern), clock=clock)
    lis.start().thread.join(5)
    return events


def test_listener_end_word_sends_early():
    calls = []

    def tr(audio, final):
        calls.append(final)
        return "Please build a calculator copy" if len(audio) > 16000 * 3 else "Please build"
    events = run_listener("ssss" + "v" * 40, tr, voice_end_mode="word")
    assert events[0]["state"] == "listening"
    assert events[-1] == {"state": "final", "text": "Please build a calculator"}
    assert any(e["state"] == "partial" for e in events) and True not in calls  # ended before the final pass


def test_listener_silence_ends_turn():
    events = run_listener("ss" + "v" * 12 + "s" * 12, lambda a, f: "Track my expenses." if f else "Track my",
                          voice_end_mode="silence")
    assert events[-1] == {"state": "final", "text": "Track my expenses."}


def test_listener_no_speech_is_cancelled():
    events = run_listener("s" * 30, lambda a, f: "Thank you.", voice_silence_seconds=1)
    # never spoke: source runs out, nothing is sent (no Whisper 'Thank you' hallucination)
    assert events[-1]["state"] == "cancelled"


def test_listener_end_word_in_final_pass_is_removed():
    events = run_listener("ss" + "v" * 3 + "s" * 10, lambda a, f: "Yes go ahead, copy." if f else "",
                          voice_end_mode="silence")
    assert events[-1] == {"state": "final", "text": "Yes go ahead"}


# ------------------------------------------------------------------------------- usage
class Info:
    def __init__(self, **kw):
        self.__dict__.update({"status": "allowed", "resets_at": None, "rate_limit_type": "five_hour",
                              "utilization": None, **kw})


def test_usage_plan_and_summary():
    e = usage.record_limit(Info(utilization=0.42, resets_at=time.time() + 3600))
    assert e["percent"] == 42.0 and e["label"].startswith("Current session")
    usage.record_limit(Info(rate_limit_type="seven_day", utilization=81, status="allowed_warning",
                            resets_at=int((time.time() + 86400) * 1000)))
    p = usage.plan()
    assert p["seven_day"]["percent"] == 81 and p["seven_day"]["resets_at"] > time.time()
    note = usage.warning_note()
    assert "This week: 81% used" in note and "Current session" not in note
    usage.record_limit(Info(utilization=0.9, resets_at=time.time() - 5))  # window already reset
    assert usage.plan()["five_hour"]["percent"] == 0.0
    usage.record_tokens("calc", "Fred", {"input_tokens": 100, "output_tokens": 50})
    usage.record_tokens("calc", "Developer", {"input_tokens": 1000, "output_tokens": 500, "cache_creation_input_tokens": 10})
    usage.record_tokens("calc", "total", None, 0.37)
    from app import analytics
    assert usage.cost_today() == 0.37
    s = analytics.report(None, "day")
    assert s["periods"][0]["label"] == "Today" and s["periods"][0]["tokens"] == 1660
    agents = {a["name"]: a["tokens"] for a in s["periods"][0]["by_agent"]}
    assert agents == {"Fred": 150, "Developer": 1510}, "the engine's per-turn cost row is not an agent"


# ------------------------------------------------------------------------------- engine: questions while a card waits, usage events
def test_question_while_card_waits_goes_to_fred(isolated):
    from app import engine as engine_mod
    from claude_agent_sdk import RateLimitEvent, RateLimitInfo
    from fakeclaude import FakeClient

    secrets_store.set_secret("anthropic_api_key", "sk-ant-x")
    settings.save({"setup_complete": True, "auth_mode": "subscription", "subscription_signed_in": True})
    p = projects.create("Q")

    async def script(i, prompt, c):
        yield RateLimitEvent(rate_limit_info=RateLimitInfo(status="allowed_warning", resets_at=int(time.time()) + 600,
                                                           rate_limit_type="five_hour", utilization=0.8),
                             uuid="u", session_id="s")
        ok, result, msgs, _ = await c.use_tool("mcp__aiteam__request_secret", {
            "name": "github_token", "title": "GitHub token", "explanation": "", "help_url": ""})
        c.secret_result = result
        for m in msgs:
            yield m
        ok, result, msgs, _ = await c.use_tool("PowerShell", {"command": "mkdir out"})
        c.approval_result = result
        for m in msgs:
            yield m

    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    eng.send_message(p["slug"], "start")
    slug = p["slug"]

    def wait_card(kind):
        for _ in range(200):
            if any(k == kind for (_f, s, k) in eng.pending.values() if s == slug):
                return
            time.sleep(0.02)
        raise AssertionError(f"no {kind} card")

    wait_card("secret")
    assert eng.send_message(slug, "Which scopes does the token need?") == "answered"
    wait_card("approval")
    assert eng.send_message(slug, "What does dir do?") == "answered"
    for _ in range(200):
        if not eng.is_busy(slug):
            break
        time.sleep(0.02)
    eng.shutdown()
    c = FakeClient.instances[-1]
    assert "Which scopes does the token need?" in c.secret_result and "ask again" in c.secret_result
    assert "What does dir do?" in c.approval_result
    assert not secrets_store.has("github_token")
    hist = hub.history(slug)
    assert [e["status"] for e in hist if e["type"] in ("secret", "approval")] == ["paused", "paused"]
    assert [e["text"] for e in hist if e["type"] == "message" and e["role"] == "user"][-2:] == [
        "Which scopes does the token need?", "What does dir do?"]
    assert any(e["type"] == "notice" and "80%" in e["text"] for e in hist)
    assert usage.plan()["five_hour"]["percent"] == 80.0
    # the next turn tells Fred that usage is high
    assert "Plan usage is high" not in c.prompts[0]  # recorded during that turn, so only later turns carry it
    assert usage.warning_note()
