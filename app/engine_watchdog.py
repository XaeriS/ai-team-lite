"""The watchdog: notices when nothing moves, says so, wakes Fred up or restarts the step (L8, L27).

Part of the Engine (app/engine.py), kept in its own file so each file has one job (L46)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import logging
import time

from . import projects
from . import engine_const as const
from .engine_text import _quiet

if TYPE_CHECKING:
    from .session import Session

log = logging.getLogger(__name__)


class WatchdogMixin:
    def _schedule_watchdog(self) -> None:
        def tick():
            try:
                self.watchdog()
            except Exception:  # noqa: BLE001  (the watchdog must never stop the app)
                pass
            self._schedule_watchdog()
        self.loop.call_later(const.WATCHDOG_TICK, tick)

    def watchdog(self, now: float | None = None) -> None:
        for slug in list(self.sessions):
            self.watch_check(slug, now=now)

    def watch_check(self, slug: str, now: float | None = None, asked: bool = False) -> dict:
        """Look at a project's work. Nothing new for 3 minutes while it works: a short "still working" line saying
        what is running. Nothing at all for 5 minutes (no tokens, no tool running): say so plainly, ask Fred to check
        when he is between turns, and offer "Wake Fred up" / "Stop and restart this step". Returns what was found."""
        sess = self.sessions.get(slug)
        now = now or time.time()
        if sess is None:
            return {"state": "idle"}
        w = sess.watch
        working = sess.busy or bool(sess.background) or bool(sess.bg_reports)
        if not working or self.waiting_user(slug) or slug in self.limit_timers or (projects.load(slug) or {}).get("paused"):
            self._watch_reset(sess, moving=False)
            return {"state": "idle" if not working else "waiting"}
        if w["since"] and sess.last_activity > w["since"]:
            self._watch_reset(sess, moving=True)   # it moved again since the last check
        quiet = now - sess.last_activity
        running = sess.running_now()
        tools_running = [r for r in running if r["kind"] == "tool"]
        found = {"state": "moving", "quiet": round(quiet), "running": running}
        stuck = (quiet >= const.QUIET_STUCK and not tools_running) or quiet >= const.COMMAND_STUCK
        if stuck:
            found["state"] = "stuck"
            first = not w["stuck"]
            if not first and not (w.get("acted_at") and now - w["acted_at"] >= const.QUIET_STUCK):
                return found   # one card per quiet spell; the next automatic step comes 5 minutes after the last one
            minutes = int(quiet // 60)
            action = self._auto_action(sess, now)   # L27: "wake", "restart" or None (the user decides)
            found["auto"] = action
            workers = [r["who"] for r in running if r["kind"] == "worker"]
            if first:
                w["since"] = sess.last_activity
                card_id = self.new_id()
                w["stuck"] = card_id
                text = f"Nothing has moved for {minutes} minutes: no tokens used and no tool working. "
                if action and not sess.busy:
                    text += f"{sess.persona} was asked to check what is going on."
                elif action:
                    text += (f"AI Team Lite woke {sess.persona} up by itself"
                             + (f" (the {', the '.join(workers)} had stopped moving)" if workers else "")
                             + ". If nothing moves within 5 minutes, it restarts this step. You can also wake him up "
                               "or restart this step yourself.")
                else:
                    text += (f"{sess.persona} may be stuck, and waking up or restarting did not help. You can wake "
                             "him up, or stop and restart this step.")
                self.hub.publish(slug, {"type": "stuck", "id": card_id, "status": "open", "text": text,
                                        "minutes": minutes, "running": [r["who"] for r in running], "auto": action})
                self.notify("stuck", slug=slug, card_id=card_id, text=text)
            if action == "wake":
                self._auto_wake(sess, minutes, workers, now)
                w["since"] = w["acted_at"] = sess.last_activity = now   # not "moving again": only real activity is
            elif action == "restart":
                self._auto_restart(sess, now)
            elif not first:
                self.hub.update(slug, w["stuck"], text=f"Nothing has moved for {minutes} minutes, even after waking "
                                f"{sess.persona} up and restarting. Please choose what to do.")
                w["acted_at"] = 0.0
            return found
        if quiet >= const.QUIET_NOTE and (asked or now - (w["noted"] or 0) >= const.QUIET_NOTE):
            w["since"] = w["since"] or sess.last_activity
            w["noted"] = now
            found["state"] = "quiet"
            self.hub.publish(slug, {"type": "watch", "text": self.still_working_text(sess, quiet, running, now)})
        return found

    def _auto_action(self, sess: "Session", now: float) -> str | None:
        """L27 (owner's rule: after 5 minutes without activity, wake or restart the sub-agent by itself). First wake
        Fred up; if nothing moved since, restart the step; if a restart did not help either, or after const.AUTO_HELP_MAX
        automatic steps in an hour, leave it to the user so the app never loops."""
        a = sess.auto_help
        a["times"] = [t for t in a["times"] if now - t < 3600]
        no_progress_since = lambda t: bool(t) and sess.last_activity <= t + 1
        if len(a["times"]) >= const.AUTO_HELP_MAX or no_progress_since(a["restart_at"]):
            return None
        return "restart" if no_progress_since(a["wake_at"]) else "wake"

    def _auto_wake(self, sess: "Session", minutes: int, workers: list[str], now: float) -> None:
        sess.auto_help["times"].append(now)
        sess.auto_help["wake_at"] = now
        note = self.wake_note(sess.slug, minutes, auto=True, stalled=workers)
        self._deliver_wake(sess, note, f"{sess.persona} was woken up automatically after {minutes} minutes without "
                                       "activity." if sess.busy else "")

    def _auto_restart(self, sess: "Session", now: float) -> None:
        sess.auto_help["times"].append(now)
        sess.auto_help["restart_at"] = now
        if sess.watch["stuck"]:
            self.hub.update(sess.slug, sess.watch["stuck"], status="resolved",
                            note="Waking up did not help, so AI Team Lite restarted this step by itself.")
            sess.watch["stuck"] = None
        self.hub.publish(sess.slug, {"type": "notice", "text": "Nothing moved for 5 more minutes after waking "
                                     f"{sess.persona} up: this step was restarted automatically."})
        self.restart_step(sess.slug)
        sess.last_activity = now

    def still_working_text(self, sess: "Session", quiet: float, running: list[dict], now: float) -> str:
        mins = lambda s: f"{max(1, int((now - s) // 60))} min so far"
        tool = next((r for r in running if r["kind"] == "tool"), None)
        if tool:
            who = "Fred" if tool["who"] == "Fred" else f"the {tool['who']}"
            return f"Still working: {who} is {tool['what'][0].lower() + tool['what'][1:]} ({mins(tool['since'])})."
        worker = next((r for r in running if r["kind"] == "worker"), None)
        if worker:
            return f"Still working: the {worker['who']} is thinking ({int(quiet // 60)} min without new output)."
        return f"Still working: {sess.persona} is thinking ({int(quiet // 60)} min without new output)."

    def _watch_reset(self, sess: "Session", moving: bool) -> None:
        w = sess.watch
        if w["stuck"]:
            self.hub.update(sess.slug, w["stuck"], status="resolved",
                            note="Work started moving again." if moving else "The work stopped.")
        sess.watch = {"since": 0.0, "noted": 0.0, "stuck": None}

    def wake_note(self, slug: str, minutes: int, auto: bool = False, stalled: list[str] | None = None) -> str:
        sess = self.sessions.get(slug)
        bg = ", ".join(i["label"] for i in sess.background.values()) if sess else ""
        return (f"[App] {'Nothing has happened' if auto else 'The user pressed Wake Fred up: nothing happened'} for "
                f"{minutes} minutes while work is in progress." + (f" Still marked as working in the background: {bg}."
                                                                    if bg else "") +
                (f" The {', the '.join(stalled)} stopped moving: start its step again." if stalled else "") +
                " Check what is going on (is a worker or a command still running, did something hang?), tell the user "
                "in one plain line what you found, then continue the work, starting again any step that hung. Do not "
                "call TaskOutput (it does not exist); run workers in the foreground.")

    def wake(self, slug: str) -> str:
        """The user pressed "Wake Fred up" (L8)."""
        sess = self.session(slug)
        minutes = int((time.time() - sess.last_activity) // 60)
        self._watch_reset(sess, moving=True)
        sess.last_activity = time.time()
        return self._deliver_wake(sess, self.wake_note(slug, minutes), f"You woke {sess.persona} up.")

    def _deliver_wake(self, sess: "Session", note: str, notice: str) -> str:
        if notice:
            self.hub.publish(sess.slug, {"type": "notice", "text": notice})
        if sess.busy and sess.turn_started is None:   # L30: the turn never really started: begin again
            return self.fresh_start(sess.slug, note)
        if sess.busy:   # stuck inside a turn: interrupt it, the note follows straight after
            with self._send_lock:
                self.queued.setdefault(sess.slug, []).append(note)
            if sess.client is not None:
                self.loop.call_soon_threadsafe(lambda: self.loop.create_task(_quiet(sess.client.interrupt())))
            return "queued"
        return self.send_message(sess.slug, note, hidden=True)

    def restart_step(self, slug: str) -> str:
        """The user pressed "Stop and restart this step" (L8)."""
        sess = self.session(slug)
        self._watch_reset(sess, moving=True)
        sess.last_activity = time.time()
        self.stop(slug)
        projects.update(slug, paused=False, pipeline_hold=False)
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        # L30: a fresh start, so a turn that hung cannot swallow the restart (it used to wait behind it forever)
        return self.fresh_start(slug, "[App] The user restarted the work: start the current step again from the "
                                      "beginning.")
