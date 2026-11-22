"""REQ-28 (L73): taskbar colours across all projects. Written BEFORE the code (app/statusicon.py).
Assumed API: colour_for, tooltip_for, flash_decision, dot_image, StatusIcon(platform, states,
window_in_front=callable, flash_enabled=callable) with .update()."""
import http.client
import json
import logging
import sys

import pytest

from app import projects, settings
from app import engine as engine_mod
from app.events import EventHub
from app.server import App, serve
from fakeclaude import FakeClient


def _mod():
    from app import statusicon
    return statusicon


# ------------------------------------------------------------------ colour
def test_req28_needs_you_is_orange():
    assert _mod().colour_for(["working", "needs you"]) == "orange"


def test_req28_release_is_orange():
    assert _mod().colour_for(["idle", "release"]) == "orange"


def test_req28_orange_wins_over_green():
    assert _mod().colour_for(["working", "waiting", "limit", "needs you"]) == "orange"


@pytest.mark.parametrize("state", ["working", "waiting", "limit"])
def test_req28_busy_states_are_green(state):
    assert _mod().colour_for(["idle", state]) == "green"


@pytest.mark.parametrize("states", [["paused"], ["idle"], ["paused", "idle"], [], ["banana"]])
def test_req28_quiet_or_unknown_is_grey(states):
    assert _mod().colour_for(states) == "grey"


# ------------------------------------------------------------------ tooltip
def test_req28_tooltip_orange_one_project():
    t = _mod().tooltip_for(["needs you", "working"])
    assert "AI Team Lite" in t and "1 project" in t and "waiting for you" in t and "projects" not in t


def test_req28_tooltip_orange_counts_projects():
    t = _mod().tooltip_for(["needs you", "release", "working"])
    assert "2 projects" in t and "waiting for you" in t


def test_req28_tooltip_green_and_grey():
    assert "working" in _mod().tooltip_for(["working"])
    assert "nothing running" in _mod().tooltip_for([])
    assert "AI Team Lite" in _mod().tooltip_for(["paused"])


# ------------------------------------------------------------------ flashing
def test_req28_flash_starts_when_orange_and_window_behind():
    assert _mod().flash_decision("orange", False, True, False) == "start"


def test_req28_flash_keeps_going_while_still_needed():
    assert _mod().flash_decision("orange", False, True, True) == "keep"


def test_req28_flash_stops_when_window_comes_to_front():
    assert _mod().flash_decision("orange", True, True, True) == "stop"


def test_req28_flash_stops_when_colour_leaves_orange():
    assert _mod().flash_decision("green", False, True, True) == "stop"


def test_req28_flash_stops_when_setting_turned_off():
    assert _mod().flash_decision("orange", False, False, True) == "stop"


@pytest.mark.parametrize("colour,front,enabled", [("orange", True, True), ("green", False, True),
                                                  ("grey", False, True), ("orange", False, False)])
def test_req28_no_flash_means_none(colour, front, enabled):
    assert _mod().flash_decision(colour, front, enabled, False) == "none"


def test_req28_flash_setting_off_never_starts():
    for colour in ("orange", "green", "grey"):
        for front in (True, False):
            assert _mod().flash_decision(colour, front, False, False) != "start"


# ------------------------------------------------------------------ dot images
def test_req28_dot_images_have_three_different_colours():
    sm = _mod()
    px = {}
    for c in ("orange", "green", "grey"):
        im = sm.dot_image(c).convert("RGB")
        assert im.width <= 64 and im.height <= 64
        px[c] = im.getpixel((im.width // 2, im.height // 2))
    assert len(set(px.values())) == 3
    r, g, b = px["orange"]; assert r > 200 and r > g > b
    r, g, b = px["green"]; assert g > r and g > b
    r, g, b = px["grey"]; assert abs(r - g) < 25 and abs(g - b) < 25


# ------------------------------------------------------------------ controller
class FakePlatform:
    def __init__(self, boom=False):
        self.calls, self.boom = [], boom

    def _do(self, *c):
        self.calls.append(c)
        if self.boom:
            raise OSError("no taskbar")

    def set_overlay(self, colour, tooltip): self._do("overlay", colour, tooltip)
    def flash(self, on): self._do("flash", on)
    def set_tray(self, colour, tooltip): self._do("tray", colour, tooltip)


def _icon(plat, states, front=False, enabled=True):
    box = {"states": states, "front": front, "enabled": enabled}
    ic = _mod().StatusIcon(plat, lambda: box["states"], window_in_front=lambda: box["front"],
                           flash_enabled=lambda: box["enabled"])
    return ic, box


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


def test_req28_update_sets_overlay_tray_and_flash(on_windows):
    plat = FakePlatform()
    ic, _ = _icon(plat, ["needs you"])
    ic.update()
    kinds = {c[0]: c for c in plat.calls}
    assert kinds["overlay"][1] == "orange" and "waiting for you" in kinds["overlay"][2]
    assert kinds["tray"][1] == "orange"
    assert kinds["flash"][1] is True


def test_req28_ten_same_updates_make_one_call_each(on_windows):
    plat = FakePlatform()
    ic, _ = _icon(plat, ["working"])
    for _i in range(10):
        ic.update()
    assert sum(1 for c in plat.calls if c[0] == "overlay") == 1
    assert sum(1 for c in plat.calls if c[0] == "tray") == 1
    assert not any(c[0] == "flash" and c[1] is True for c in plat.calls)


def test_req28_change_makes_a_new_call_and_flash_stops_when_answered(on_windows):
    plat = FakePlatform()
    ic, box = _icon(plat, ["needs you"])
    ic.update()
    box["states"] = ["working"]
    ic.update()
    overlays = [c for c in plat.calls if c[0] == "overlay"]
    assert [c[1] for c in overlays] == ["orange", "green"]
    assert [c for c in plat.calls if c[0] == "flash"][-1][1] is False


def test_req28_flash_stops_when_window_in_front(on_windows):
    plat = FakePlatform()
    ic, box = _icon(plat, ["needs you"])
    ic.update()
    box["front"] = True
    ic.update()
    assert [c for c in plat.calls if c[0] == "flash"][-1][1] is False


def test_req28_flash_off_setting_never_flashes(on_windows):
    plat = FakePlatform()
    ic, _ = _icon(plat, ["needs you"], enabled=False)
    ic.update()
    assert not any(c[0] == "flash" and c[1] is True for c in plat.calls)
    assert any(c[0] == "overlay" and c[1] == "orange" for c in plat.calls)


def test_req28_platform_failure_is_logged_once_and_never_escapes(on_windows, caplog):
    plat = FakePlatform(boom=True)
    ic, box = _icon(plat, ["needs you"])
    with caplog.at_level(logging.DEBUG):
        for st in (["needs you"], ["working"], ["idle"], ["needs you"]):
            box["states"] = st
            ic.update()          # must not raise
    errs = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(errs) == 3  # one per kind of failure: overlay, tray, flash (not once per app)


def test_l73_later_failure_kind_is_still_logged(on_windows, caplog):
    class TrayAndFlashBroken(FakePlatform):
        def set_tray(self, colour, tooltip): raise OSError("tray")
        def flash(self, on): raise OSError("flash")
    ic, _ = _icon(TrayAndFlashBroken(), ["needs you"])
    with caplog.at_level(logging.DEBUG):
        ic.update()
    errs = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(errs) == 2  # the tray failure must not hide the flash failure


def test_l73_reset_restarts_flash_when_card_still_open(on_windows):
    plat = FakePlatform()
    ic, _ = _icon(plat, ["needs you"], front=False)
    ic.update()
    ic.reset()  # window came back from the tray, but is behind other windows again
    ic.update()
    starts = [c for c in plat.calls if c[0] == "flash" and c[1] is True]
    assert len(starts) == 2


def test_req28_nothing_happens_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    plat = FakePlatform()
    ic, _ = _icon(plat, ["needs you"])
    ic.update()
    assert plat.calls == []


# ------------------------------------------------------------------ setting
def test_req28_flash_taskbar_defaults_to_on():
    assert settings.load()["flash_taskbar"] is True


def test_req28_flash_taskbar_setting_saved_by_route():
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    app = App(engine=eng, hub=hub)
    httpd, _ = serve(app)
    port = httpd.server_address[1]

    def call(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Content-Type": "application/json", "Host": "127.0.0.1", "X-Token": app.token})
        r = c.getresponse(); raw = r.read()
        return r.status, (json.loads(raw) if raw else {})
    try:
        assert call("GET", "/api/state")[1]["settings"]["flash_taskbar"] is True
        assert call("POST", "/api/settings", {"flash_taskbar": False})[0] == 200
        assert call("GET", "/api/state")[1]["settings"]["flash_taskbar"] is False
        assert settings.load()["flash_taskbar"] is False
    finally:
        app.engine.shutdown(); httpd.shutdown()
