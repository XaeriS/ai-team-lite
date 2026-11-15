"""The agent engine: one Claude Agent SDK session per conversation (setup or project).

- Fred is the main agent. Worker agents (developer, tester, reviewer) are SDK subagents he starts.
- Every tool call goes through `can_use_tool`, which applies policy.py and, when needed, shows an approval
  card in the chat and waits for the user.
- Everything the user should see becomes an event on the EventHub.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Callable

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from . import engine_const as const
from . import engine_text, journal, projects, settings, timeline, tooling, tracking, winhelpers
from .engine_limits import LimitsMixin
from .engine_pipeline import PipelineMixin
from .engine_text import _quiet
from .events import EventHub
from .engine_watchdog import WatchdogMixin
from .session import Session

ClientFactory = Callable[[ClaudeAgentOptions], Any]
# reached through this module by the server and the tests
CONTINUE_TEXT = const.CONTINUE_TEXT
launch_info = engine_text.launch_info
is_signin_lost = engine_text.is_signin_lost
closed_by_windows = engine_text.closed_by_windows
validate_anthropic_key = engine_text.validate_anthropic_key
log = logging.getLogger(__name__)
winhelpers.hide_engine_console()   # L32: no console boxes flashing while the team works


class Engine(WatchdogMixin, LimitsMixin, PipelineMixin):
    def __init__(self, hub: EventHub, client_factory: ClientFactory | None = None):
        self.hub = hub
        self.client_factory: ClientFactory = client_factory or (lambda options: ClaudeSDKClient(options=options))
        self.sessions: dict[str, Session] = {}
        self.pending: dict[str, tuple[asyncio.Future, str, str]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.queued: dict[str, list[str]] = {}
        self.waiting: list[dict] = []          # turns waiting for a free slot (max parallel projects)
        self.notes: dict[str, list[str]] = {}  # app notes for Fred, delivered with the next message
        self.limit_timers: dict[str, tuple[str, Any, float]] = {}  # slug -> (card id, timer, when)
        self.signin_waiting: dict[str, str] = {}   # L37: slug -> its "sign in again" card
        self.signin_messages: dict[str, list[str]] = {}   # L37: messages sent while signed out
        self.closed_at: dict[str, float] = {}      # L37: when Windows last closed the engine, per project
        self.listeners: list[Callable[..., None]] = []  # e.g. the Telegram bridge (L17): notify(event, **details)
        self.telegram = None
        self._send_lock = threading.RLock()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="agent-loop", daemon=True)
        self.thread.start()
        self.loop.call_soon_threadsafe(self._schedule_tick)
        self.loop.call_soon_threadsafe(self._schedule_watchdog)

    def _schedule_tick(self) -> None:
        def tick():
            threading.Thread(target=self._start_next, daemon=True).start()
            self._schedule_tick()
        self.loop.call_later(const.PIPELINE_TICK, tick)

    def notify(self, event: str, **details) -> None:
        """Tell listeners (the phone) that a card waits, was answered, or that work is stuck / hit the limit."""
        for listener in list(self.listeners):
            try:
                listener(event, **details)
            except Exception:  # noqa: BLE001  (a listener must never break the work)
                pass

    def session(self, slug: str) -> Session:
        if slug not in self.sessions:
            self.sessions[slug] = Session(self, slug)
        return self.sessions[slug]

    def is_busy(self, slug: str) -> bool:
        s = self.sessions.get(slug)
        return bool(s and s.busy)

    def add_note(self, slug: str, note: str) -> None:
        with self._send_lock:
            self.notes.setdefault(slug, []).append(note)

    def waiting_user(self, slug: str) -> bool:
        return any(s == slug for (_f, s, _k) in list(self.pending.values()))

    def running_count(self) -> int:
        """Projects using a working slot. A project waiting for the user's answer does not hold one."""
        return sum(1 for slug, s in list(self.sessions.items()) if s.busy and not self.waiting_user(slug))

    def send_message(self, slug: str, text: str, hidden: bool = False) -> str:
        """Returns "started", "answered" (it answered/paused a question card), "queued" (Fred is busy on this
        project), "waiting" (other projects use all slots) or "paused" (the project is paused)."""
        with self._send_lock:
            if not hidden and slug != projects.SETUP_SLUG and (projects.load(slug) or {}).get("pipeline_hold"):
                projects.update(slug, pipeline_hold=False)
            if not hidden:
                waiting = [(i, k) for i, (_f, s, k) in list(self.pending.items()) if s == slug]
                for item_id, kind in waiting:
                    if kind == "choice":
                        self.respond(item_id, {"value": text})
                        return "answered"
                if waiting:
                    # Fred is paused on a card: show the question and hand it to him now (bug #5)
                    self.hub.publish(slug, {"type": "message", "role": "user", "text": text})
                    for item_id, _kind in waiting:
                        self.respond(item_id, {"interrupted": True, "question": text})
                    return "answered"
            sess = self.session(slug)
            project = projects.load(slug) or {}
            if project.get("paused") and slug != projects.SETUP_SLUG:
                self.queued.setdefault(slug, []).append(text)
                if not hidden:
                    self.hub.publish(slug, {"type": "message", "role": "user", "text": text, "queued": True})
                return "paused"
            if not hidden and not sess.busy and sess.turn_ended and slug != projects.SETUP_SLUG \
                    and self._awaits_reply(slug):
                # Fred had asked something: the time until this answer was time waiting for you (L14)
                timeline.record(slug, "wait", sess.persona, sess.turn_ended, min(time.time(), sess.turn_ended + 12 * 3600))
                sess.turn_ended = 0.0
            if sess.busy:
                self.queued.setdefault(slug, []).append(text)
                if not hidden:
                    self.hub.publish(slug, {"type": "message", "role": "user", "text": text, "queued": True})
                return "queued"
            limit = max(1, int(settings.load().get("max_parallel") or 2))
            if slug != projects.SETUP_SLUG and self.running_count() >= limit:
                if not hidden:
                    self.hub.publish(slug, {"type": "message", "role": "user", "text": text, "queued": True})
                self.waiting.append({"slug": slug, "text": text, "ts": time.time()})
                self.hub.publish(slug, {"type": "notice", "text": f"Waiting: {limit} project(s) are already working. "
                                        "This one starts when a slot frees up (see Work queue)."})
                self.hub.publish(slug, {"type": "project_list", "transient": True})
                return "waiting"
            sess.busy = True  # claim immediately so a double click cannot start two turns
        self._start(slug, text, hidden)
        return "started"

    def _start(self, slug: str, text: str, hidden: bool) -> None:
        sess = self.session(slug)

        async def run():
            await sess.turn(text, hidden)
            while True:
                with self._send_lock:
                    paused = (projects.load(slug) or {}).get("paused")
                    kickoff = [] if paused or not sess.pending_kickoff else [sess.pending_kickoff]
                    if kickoff:
                        sess.pending_kickoff = None
                    waiting = [] if paused else kickoff + self.queued.pop(slug, [])
                    if not waiting:
                        sess.busy = False
                        break
                    sess.busy = True
                await sess.turn("\n\n".join(waiting), hidden=True)
            sess.start_listening()   # a worker still runs in the background: wait for it and wake Fred (L7)
            self._start_next()

        self.loop.call_soon_threadsafe(lambda: self.tasks.__setitem__(slug, self.loop.create_task(run())))

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def snapshot_docs(self, slug: str, reason: str) -> None:
        try:
            tracking.snapshot(slug, reason)
        except Exception:  # noqa: BLE001  (history is best-effort)
            pass

    def respond(self, item_id: str, payload: dict) -> bool:
        entry = self.pending.get(item_id)
        if not entry:
            return False
        fut, slug, kind = entry
        public = {k: v for k, v in payload.items() if k != "value"} if kind == "secret" else dict(payload)
        if payload.get("interrupted"):
            public = {"status": "paused", "note": "You asked a question first; Fred will answer, then ask again."}
        elif kind == "approval":
            public["status"] = "approved" if payload.get("approved") else "declined"
        elif kind in ("plan", "architecture"):
            public["status"] = "approved" if payload.get("approved") else "changes requested"
        elif kind == "change":
            public["status"] = "approved" if payload.get("approved") else "rejected"
        elif kind == "secret":
            public["status"] = "cancelled" if payload.get("cancelled") or not payload.get("value") else "saved"
        else:
            public["status"] = "cancelled" if payload.get("cancelled") else "answered"
        self.hub.update(slug, item_id, **public)

        def resolve():
            if not fut.done():
                fut.set_result(payload)

        self.loop.call_soon_threadsafe(resolve)
        return True

    def stop(self, slug: str) -> None:
        if slug != projects.SETUP_SLUG and projects.load(slug):
            projects.update(slug, pipeline_hold=True)  # the user stopped it: the master plan leaves it alone
        with self._send_lock:
            self.queued.pop(slug, None)
            self.waiting = [w for w in self.waiting if w["slug"] != slug]
        self._interrupt(slug)

    def _interrupt(self, slug: str) -> None:
        def do():
            for item_id, (fut, s, _kind) in list(self.pending.items()):
                if s == slug and not fut.done():
                    fut.set_result({"cancelled": True, "approved": False})
                    self.hub.update(slug, item_id, status="cancelled")
            sess = self.sessions.get(slug)
            if sess is not None:
                sess.user_stopped = True   # L31
            if sess and (sess.background or sess.bg_reports):
                sess.stop_background()
                self.loop.create_task(sess.stop_listening())
                if not sess.busy:
                    self.hub.publish(slug, {"type": "busy", "value": False})
                    self.hub.publish(slug, {"type": "notice", "text": "Stopped."})
            if sess and sess.client is not None and sess.busy:
                self.loop.create_task(_quiet(sess.client.interrupt()))

        self.loop.call_soon_threadsafe(do)

    def forget(self, slug: str) -> None:
        """The project is being deleted or removed: stop everything and release its folder."""
        self.stop(slug)
        self.cancel_auto_continue(slug)
        self.limit_timers.pop(slug, None)
        with self._send_lock:
            self.notes.pop(slug, None)
            sess = self.sessions.pop(slug, None)
        if sess is not None:
            task = self.tasks.pop(slug, None)
            if task is not None:
                self.loop.call_soon_threadsafe(task.cancel)
            try:
                asyncio.run_coroutine_threadsafe(sess.stop_listening(), self.loop).result(timeout=5)
                asyncio.run_coroutine_threadsafe(sess._drop_client(), self.loop).result(timeout=10)
            except Exception:
                pass

    def pending_for(self, slug: str) -> set[str]:
        return {i for i, (_f, s, _k) in self.pending.items() if s == slug}

    def shutdown(self) -> None:
        async def close_all():
            for s in self.sessions.values():
                await s._drop_client()

        try:
            asyncio.run_coroutine_threadsafe(close_all(), self.loop).result(timeout=10)
        except Exception:
            pass
        journal.clean_exit()
        if settings.load().get("ollama_stop_on_exit"):
            tooling.ollama_stop_if_started()
        self.loop.call_soon_threadsafe(self.loop.stop)
