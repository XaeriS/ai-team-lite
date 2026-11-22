"""REQ-28: one colour for the taskbar / tray icon from the states of all projects.

orange = a project waits for me ("needs you", "release"); green = something is working ("working", "waiting",
"limit"); grey = nothing running. StatusIcon applies it through a platform object (taskbar_win.WinTaskbar)
only when something changes; a platform failure is logged once and never breaks the app."""
from __future__ import annotations

import logging
import sys
from typing import Callable

ORANGE_STATES = ("needs you", "release")
GREEN_STATES = ("working", "waiting", "limit")
RGB = {"orange": (242, 140, 40), "green": (46, 170, 90), "grey": (140, 144, 150)}

log = logging.getLogger(__name__)


def colour_for(states: list[str]) -> str:
    if any(s in ORANGE_STATES for s in states):
        return "orange"
    if any(s in GREEN_STATES for s in states):
        return "green"
    return "grey"


def _projects(n: int) -> str:
    return f"{n} project" + ("" if n == 1 else "s")


def tooltip_for(states: list[str]) -> str:
    colour = colour_for(states)
    if colour == "orange":
        return f"AI Team Lite: {_projects(sum(s in ORANGE_STATES for s in states))} waiting for you"
    if colour == "green":
        return f"AI Team Lite: {_projects(sum(s in GREEN_STATES for s in states))} working"
    return "AI Team Lite: nothing running"


def flash_decision(colour: str, window_in_front: bool, enabled: bool, flashing: bool) -> str:
    """start | keep | stop | none. Flash only while orange, the setting is on and the window is not in front."""
    want = colour == "orange" and enabled and not window_in_front
    if want:
        return "keep" if flashing else "start"
    return "stop" if flashing else "none"


def dot_image(colour: str, size: int = 32):
    """A filled circle with a thin white ring (readable on dark and light taskbars), transparent outside."""
    from PIL import Image, ImageDraw

    big = size * 4  # draw large, then shrink: smooth edges
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, big - 1, big - 1), fill=(255, 255, 255, 255))
    ring = max(4, big // 10)
    d.ellipse((ring, ring, big - 1 - ring, big - 1 - ring), fill=RGB.get(colour, RGB["grey"]) + (255,))
    return im.resize((size, size), Image.LANCZOS)


class StatusIcon:
    def __init__(self, platform, states: Callable[[], list[str]], window_in_front: Callable[[], bool],
                 flash_enabled: Callable[[], bool]):
        self.platform, self.states = platform, states
        self.window_in_front, self.flash_enabled = window_in_front, flash_enabled
        self.shown: dict[str, tuple] = {}   # "overlay" / "tray" -> (colour, tooltip) last applied
        self.flashing = False
        self.warned: set[str] = set()  # kinds already logged: "overlay", "tray", "flash", "read"

    def reset(self) -> None:
        """Apply again on the next update (e.g. the taskbar button was re-created after hide/show).
        Flashing restarts too: the window may have come back behind other windows."""
        self.shown.clear()
        self.flashing = False

    def _call(self, kind: str, fn, *args) -> bool:
        try:
            fn(*args)
            return True
        except Exception:  # noqa: BLE001  (the normal icon stays; tell the log once per kind)
            self._warn_once(kind)
            return False

    def _warn_once(self, kind: str) -> None:
        if kind not in self.warned:
            self.warned.add(kind)
            log.warning("taskbar status icon (%s) unavailable; the normal icon stays", kind, exc_info=True)

    def update(self) -> None:
        if sys.platform != "win32":
            return
        try:
            states = list(self.states())
            front, enabled = bool(self.window_in_front()), bool(self.flash_enabled())
        except Exception:  # noqa: BLE001
            return self._warn_once("read")
        colour = colour_for(states)
        want = (colour, tooltip_for(states))
        for kind in ("overlay", "tray"):
            if self.shown.get(kind) != want and self._call(kind, getattr(self.platform, "set_" + kind), *want):
                self.shown[kind] = want
        decision = flash_decision(colour, front, enabled, self.flashing)
        if decision in ("start", "stop") and self._call("flash", self.platform.flash, decision == "start"):
            self.flashing = decision == "start"
