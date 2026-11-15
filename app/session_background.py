"""Workers running in the background: noticing when they finish, waking Fred, stopping them (L7).

Part of a Session (app/session.py), kept in its own file so each file has one job (L46)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage

try:  # background-task lifecycle messages (Claude Agent SDK 0.2.150+); older engines simply never send them
    from claude_agent_sdk import TaskNotificationMessage, TaskProgressMessage, TaskStartedMessage, TaskUpdatedMessage
except ImportError:  # pragma: no cover
    TaskNotificationMessage = TaskProgressMessage = TaskStartedMessage = TaskUpdatedMessage = type("_Never", (), {})

from . import engine_const as const, timeline
from .engine_text import _quiet, _tail, describe_tool


class BackgroundMixin:
    def _went_background(self, block: Any, receipt: str) -> None:
        """The engine started a worker in the background and returned only a receipt. Keep the worker shown as
        working and hide the receipt. AI Team Lite itself now waits for the worker's result (it listens to the
        engine between turns) and wakes Fred with the report: no TaskOutput, which this engine no longer has."""
        label = self.agents.pop(block.tool_use_id)
        m = const.AGENT_ID.search(receipt)
        aid = m.group(1) if m else ""
        self.background[aid or block.tool_use_id] = {"card": block.tool_use_id, "label": label, "started": time.time()}
        self.hub.update(self.slug, block.tool_use_id, status="working", background=True)
        self.set_role(label, status="working", last="working in the background")

    def _bg_key(self, task_id: str, tool_use_id: str | None) -> str | None:
        tool_use_id = tool_use_id or self.task_tools.get(task_id)
        if task_id in self.background:
            return task_id
        return next((k for k, v in self.background.items() if tool_use_id and v["card"] == tool_use_id), None)

    def _bg_label(self, parent: str | None) -> str | None:
        if not parent:
            return None
        return next((v["label"] for v in self.background.values() if v["card"] == parent), None)

    def _background_done(self, task_id: str, tool_use_id: str | None, status: str, summary: str = "",
                         output_file: str = "") -> None:
        key = self._bg_key(task_id, tool_use_id)
        info = self.background.pop(key, None) if key else None
        if not info:
            return
        timeline.stop(self.slug, info["card"])
        failed = status in ("failed", "stopped", "killed")
        report = (self.last_text.pop(info["label"], "") or "").strip()
        if not report and output_file:
            report = _tail(output_file)
        report = report or (summary or "").strip() or ("It stopped without a report." if failed else "It finished.")
        done_task = (self.team.get(info["label"]) or {}).get("task", "")
        self.set_role(info["label"], status="idle", task=f"Finished: {done_task}" if done_task else "",
                      last="reported back to Fred" if not failed else "stopped before finishing")
        self.hub.update(self.slug, info["card"], status="failed" if failed else "done", background=False,
                        summary=report[:1200])
        self.hub.publish(self.slug, {"type": "docs_changed", "transient": True})
        now = time.time()
        self.bg_reports.append({"label": info["label"], "report": report, "failed": failed, "t": now})
        self.bg_done_at = now

    def wake_text(self) -> str:
        parts = []
        for r in self.bg_reports:
            state = "stopped before finishing" if r["failed"] else "has finished"
            parts.append(f"The {r['label']} {state} (it ran in the background). Its report:\n{r['report'][:6000]}")
        return ("[App] " + "\n\n".join(parts) + "\n\nContinue the work with this report now. Do not call TaskOutput "
                "(it does not exist in this engine) and never start workers in the background "
                "(always run_in_background=false).")

    def start_listening(self) -> None:
        """Between turns, while a worker still runs in the background: read the engine's messages so the screen
        stays live, notice when the worker finishes, and wake Fred with its report (L7)."""
        if (self.background or self.bg_reports) and (self.watch_task is None or self.watch_task.done()):
            self.watch_task = asyncio.get_running_loop().create_task(self._listen())

    async def stop_listening(self) -> None:
        t, self.watch_task = self.watch_task, None
        if t is not None and not t.done() and t is not asyncio.current_task():
            t.cancel()
            try:
                await asyncio.wait_for(t, 5)   # L30: a listener stuck on a dead connection must not hold up the turn
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass

    async def _listen(self) -> None:
        self_woke = False
        while True:
            if not self.background and not self.bg_reports and not self_woke:
                return
            timeout: float | None = const.WATCH_POLL
            if not self.background and self.bg_reports and not self_woke:
                timeout = max(0.05, self.bg_done_at + const.WAKE_GRACE - time.time())
            client = self.client
            msg = None
            if client is not None and hasattr(client, "receive_messages"):
                agen = client.receive_messages()
                try:
                    msg = await asyncio.wait_for(agen.__anext__(), timeout)
                except asyncio.TimeoutError:
                    msg = None
                except StopAsyncIteration:
                    client = None
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001  (the engine went away: the watchdog takes over)
                    return
                finally:
                    try:
                        await agen.aclose()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                await asyncio.sleep(min(timeout or const.WATCH_POLL, const.WATCH_POLL))
            if msg is not None:
                if isinstance(msg, AssistantMessage) and not msg.parent_tool_use_id and not self_woke:
                    # the engine woke the lead by itself with the worker's result: show that turn live
                    self_woke = True
                    self.bg_reports.clear()
                    self.hub.publish(self.slug, {"type": "busy", "value": True})
                    self.set_role(self.persona, status="working", task="Reading the report", last="thinking")
                self.handle(msg)
                if isinstance(msg, ResultMessage) and self_woke:
                    self_woke = False
                    self.set_role(self.persona, status="idle", task="")
                    if not self.background:
                        self.hub.publish(self.slug, {"type": "busy", "value": False})
                        self.hub.publish(self.slug, {"type": "docs_changed", "transient": True})
                continue
            if not self.background and self.bg_reports and not self_woke \
                    and time.time() >= self.bg_done_at + const.WAKE_GRACE:
                text = self.wake_text()
                self.bg_reports.clear()
                self.engine.send_message(self.slug, text, hidden=True)
                return

    def stop_background(self) -> None:
        """The user stopped the work: stop the workers still running in the background too."""
        client = self.client
        for key, info in list(self.background.items()):
            task_id = key if key in self.task_tools else next((t for t, u in self.task_tools.items()
                                                                if u == info["card"]), key)
            if client is not None and hasattr(client, "stop_task"):
                asyncio.get_running_loop().create_task(_quiet(client.stop_task(task_id)))
            self.hub.update(self.slug, info["card"], status="failed", background=False, summary="Stopped by you.")
            self.set_role(info["label"], status="idle", task="", last="stopped by you")
        self.background.clear()
        self.bg_reports.clear()

    def running_now(self) -> list[dict]:
        """What is in progress right now: commands/tools started and not finished, and workers."""
        now = time.time()
        out = [{"who": t["who"], "what": t["line"], "since": t["t"], "kind": "tool"} for t in self.open_tools.values()]
        for label in self.agents.values():
            out.append({"who": label, "what": (self.team.get(label) or {}).get("task", "") or "working",
                        "since": (self.team.get(label) or {}).get("since", now), "kind": "worker"})
        for info in self.background.values():
            out.append({"who": info["label"], "what": "working in the background", "since": info["started"],
                        "kind": "worker"})
        return out

    def _task_message(self, msg: Any) -> bool:
        """Background-task lifecycle messages from the engine. Returns True when the message was one of them."""
        if isinstance(msg, TaskStartedMessage):
            if msg.tool_use_id:
                self.task_tools[msg.task_id] = msg.tool_use_id
            return True
        if isinstance(msg, TaskProgressMessage):
            key = self._bg_key(msg.task_id, msg.tool_use_id)
            if key:
                tool = msg.last_tool_name or ""
                self.set_role(self.background[key]["label"], status="working",
                              last=f"working in the background{': ' + describe_tool(tool, {}, self.folder) if tool else ''}")
            return True
        if isinstance(msg, TaskNotificationMessage):
            self._background_done(msg.task_id, msg.tool_use_id, msg.status, msg.summary or "", msg.output_file or "")
            return True
        if isinstance(msg, TaskUpdatedMessage):
            if (msg.status or "") in const.TERMINAL:
                self._background_done(msg.task_id, None, msg.status or "completed",
                                      str((msg.patch or {}).get("result") or (msg.patch or {}).get("error") or ""))
            return True
        return False
