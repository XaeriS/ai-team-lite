"""Plan limits, an expired sign-in and an engine closed by Windows: wait, then continue on a fresh start (L25, L30, L37).

Part of the Engine (app/engine.py), kept in its own file so each file has one job (L46)."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid

from . import auth, projects, settings, usage
from . import engine_const as const

log = logging.getLogger(__name__)


class LimitsMixin:
    def on_limit(self, slug: str, entry: dict) -> None:
        resets = entry.get("resets_at") or (usage.plan().get(entry.get("type") or "five_hour") or {}).get("resets_at")
        stagger = {0: 10, 1: 25, 2: 40}[self._priority(slug)]  # high-priority projects continue first
        when = (resets + stagger) if resets else None
        item_id = uuid.uuid4().hex[:12]
        self.hub.publish(slug, {"type": "limit", "id": item_id, "status": "waiting", "resets_at": resets,
                                "continue_at": when, "auto": when is not None, "checking": when is None,
                                "label": entry.get("label", ""), "detail": entry.get("detail", "")})
        self.notify("limit", slug=slug, text="Your Claude plan's usage limit was reached, so the work stopped. " + (
            time.strftime("It continues by itself at %H:%M.", time.localtime(when)) if when else
            "AI Team Lite checks every 15 minutes and continues as soon as there is room again."))
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        # L30: known at once, so the master plan cannot start more work in the moment before the timer is set
        self.limit_timers[slug] = (item_id, None, when or 0)
        if when is not None:
            def schedule():
                handle = self.loop.call_later(max(1.0, when - time.time()),
                                              lambda: self.limit_continue(slug, auto=True))
                self.limit_timers[slug] = (item_id, handle, when)
            self.loop.call_soon_threadsafe(schedule)
        else:   # the reset time is unknown: look again every 15 minutes and continue as soon as there is room (L25)
            self.limit_timers[slug] = (item_id, None, 0)
            self.loop.call_soon_threadsafe(lambda: self._schedule_limit_check(slug, item_id, entry.get("type") or "five_hour"))

    def _schedule_limit_check(self, slug: str, item_id: str, kind: str) -> None:
        def check():
            entry = self.limit_timers.get(slug)
            if not entry or entry[0] != item_id or entry[1] is None:
                return   # continued by hand, paused or cancelled meanwhile
            threading.Thread(target=self._limit_check, args=(slug, item_id, kind), daemon=True).start()
        handle = self.loop.call_later(const.LIMIT_CHECK_SECONDS, check)
        self.limit_timers[slug] = (item_id, handle, 0)

    def _limit_check(self, slug: str, item_id: str, kind: str) -> None:
        got = usage.fetch_plan(force=True)
        v = usage.plan().get(kind) or {}
        room = v.get("status") != "rejected" and v.get("percent") is not None and v["percent"] < 100
        entry = self.limit_timers.get(slug)
        if not entry or entry[0] != item_id or entry[1] is None:
            return   # cancelled while Claude's figures were being read
        if got and room:
            self.limit_continue(slug, auto=True)
        elif v.get("resets_at") and v["resets_at"] > time.time():   # now a time is known: wait for it exactly
            when = v["resets_at"] + {0: 10, 1: 25, 2: 40}[self._priority(slug)]
            self.hub.update(slug, item_id, resets_at=v["resets_at"], continue_at=when, auto=True)
            def schedule():
                handle = self.loop.call_later(max(1.0, when - time.time()), lambda: self.limit_continue(slug, auto=True))
                self.limit_timers[slug] = (item_id, handle, when)
            self.loop.call_soon_threadsafe(schedule)
        else:
            self.loop.call_soon_threadsafe(lambda: self._schedule_limit_check(slug, item_id, kind))

    def limit_continue(self, slug: str, auto: bool = False) -> str:
        entry = self.limit_timers.pop(slug, None)
        if entry:
            item_id, handle, _ = entry
            if handle:
                handle.cancel()
            self.hub.update(slug, item_id, status="continued", auto=False,
                            note="Continued automatically." if auto else "Continued.")
        if (projects.load(slug) or {}).get("paused"):
            return "paused"
        if auto:
            self.notify("resumed", slug=slug, text="The usage limit has reset: the work continues.")
        # L30: after hours of waiting the old turn or connection may have hung: continue on a fresh one
        with self._send_lock:
            held = self.queued.pop(slug, [])
        return self.fresh_start(slug, "\n\n".join([const.CONTINUE_TEXT, *held]))

    def on_signin_lost(self, slug: str) -> None:
        """L37: Claude refused the sign-in. Mark it, so the master plan stops trying, and show one card per project
        with a "Sign in again" button; the work continues by itself once signed in (on_signed_in)."""
        if auth.mode() == "subscription":
            settings.save({"subscription_signed_in": False})
        if slug in self.signin_waiting:
            return
        card_id = self.new_id()
        self.signin_waiting[slug] = card_id
        self.hub.publish(slug, {"type": "signin", "id": card_id, "status": "waiting", "mode": auth.mode() or ""})
        self.notify("signin", slug=slug, text="Your Claude sign-in has expired, so the work stopped. Sign in again "
                                              "in AI Team Lite and it continues by itself.")
        self.hub.publish(slug, {"type": "project_list", "transient": True})

    def on_signed_in(self) -> list[str]:
        """L37: signed in again: every project that stopped for it continues where it was."""
        waiting, self.signin_waiting = self.signin_waiting, {}
        for slug, card_id in waiting.items():
            self.hub.update(slug, card_id, status="resolved", note="Signed in again: the work continues.")
            held = self.signin_messages.pop(slug, [])
            self.fresh_start(slug, "\n\n".join(["[App] The user signed in to Claude again after the sign-in had "
                                                 "expired. Continue where you stopped; a step that was cut off may need "
                                                 "to be started again." + (" Meanwhile the user wrote:" if held else ""),
                                                 *held]))
        return list(waiting)

    def on_engine_closed(self, slug: str) -> None:
        """L37: Windows ended Claude's engine (a restart or an update): say so plainly and continue once on a fresh
        connection; a second close within 10 minutes is left to the user (no loop)."""
        now = time.time()
        if now - self.closed_at.get(slug, 0) < 600:
            self.hub.publish(slug, {"type": "notice", "text": "Windows closed Claude's engine again. Send Fred a "
                                                              "message to continue."})
            return
        self.closed_at[slug] = now
        self.hub.publish(slug, {"type": "notice", "text": "Windows closed Claude's engine (probably a restart or an "
                                                          "update). The work continues on a fresh connection."})
        self.fresh_start(slug, "[App] Claude's engine was closed by Windows in the middle of your work. Continue "
                               "where you stopped; check whether the last step finished before relying on it.")

    def fresh_start(self, slug: str, text: str) -> str:
        """L30: start a turn even when the previous one hung (a turn that never really started, a dead connection
        after a long wait). The hung turn is cancelled, the listener stopped and the connection dropped; the new
        turn reconnects and resumes the same conversation, so Fred carries on where he stopped."""
        sess = self.session(slug)

        async def go():
            task = self.tasks.get(slug)
            if task is not None and not task.done() and task is not asyncio.current_task():
                task.cancel()
                try:
                    await asyncio.wait_for(task, 10)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                    pass
            await sess.stop_listening()
            try:
                await asyncio.wait_for(sess._drop_client(), 10)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                sess.client = None
            with self._send_lock:
                sess.busy = False
                sess.turn_started = None
            self.send_message(slug, text, hidden=True)

        self.loop.call_soon_threadsafe(lambda: self.loop.create_task(go()))
        return "started"

    def cancel_auto_continue(self, slug: str, note: str = "") -> None:
        entry = self.limit_timers.get(slug)
        if not entry:
            return
        item_id, handle, _ = entry
        if handle:
            self.loop.call_soon_threadsafe(handle.cancel)
        self.limit_timers[slug] = (item_id, None, 0)
        self.hub.update(slug, item_id, auto=False, continue_at=None, checking=False,
                        note="Auto-continue cancelled." if note != "paused" else "Paused: continue from the Work queue.")
