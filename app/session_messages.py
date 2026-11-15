"""What Claude sends back, turned into what the user sees: messages, tool steps, workers, results, errors.

Part of a Session (app/session.py), kept in its own file so each file has one job (L46)."""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from claude_agent_sdk import (AssistantMessage, RateLimitEvent, ResultMessage, SystemMessage, TextBlock,
                              ToolResultBlock, ToolUseBlock, UserMessage)

from . import engine_const as const, projects, timeline, usage
from .engine_text import _result_text, describe_tool, is_signin_lost

log = logging.getLogger(__name__)


class MessagesMixin:
    def handle(self, msg: Any) -> None:
        if not isinstance(msg, RateLimitEvent):
            self.last_activity = time.time()
        if self._task_message(msg):
            return
        if isinstance(msg, SystemMessage):
            sid = (msg.data or {}).get("session_id")
            if msg.subtype == "init" and sid:
                projects.update(self.slug, session_id=sid)
            return
        if isinstance(msg, AssistantMessage):
            if getattr(msg, "error", None):  # the engine's own error text; the ResultMessage explains it
                return
            parent = msg.parent_tool_use_id or ""
            worker = self.agents.get(parent) or self._bg_label(parent)
            if not parent:
                self.last_lead_t = time.time()
            mid = getattr(msg, "message_id", None)
            if msg.usage and mid not in self.counted:  # one model call may arrive as several messages
                self.counted.add(mid)
                usage.record_tokens(self.slug, worker or self.persona, msg.usage, model=getattr(msg, "model", "") or "")
            lead = self.persona
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    self.last_text[worker or lead] = block.text
                if isinstance(block, TextBlock) and block.text.strip() and not msg.parent_tool_use_id:
                    self.hub.publish(self.slug, {"type": "message", "role": "fred", "speaker": lead, "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    # what the agent said right before this action belongs to this action only
                    self.says_for_tool[worker or lead] = self.last_text.pop(worker or lead, "")
                    if block.name in const.AGENT_TOOLS:
                        label = const.WORKER_LABELS.get(block.input.get("subagent_type", ""), "Worker")
                        self.agents[block.id] = label
                        timeline.start(self.slug, block.id, label)
                        self.set_role(label, status="working", task=block.input.get("description", "") or "Working",
                                      last="received a brief from Fred")
                        self.set_role("Fred", status="working", task=f"Supervising the {label}")
                        self.hub.publish(self.slug, {"type": "agent", "id": block.id, "name": label,
                                                     "status": "working",
                                                     "description": block.input.get("description", "")})
                    elif block.name in const.QUIET_TOOLS:
                        pass
                    elif block.name not in const.CARD_TOOLS:
                        line = describe_tool(block.name, block.input or {}, self.folder)
                        self.open_tools[block.id] = {"who": worker or lead, "line": line, "t": time.time()}
                        self.set_role(worker or lead, status="working", last=line)
                        self.hub.publish(self.slug, {"type": "activity", "who": worker or lead, "text": line})
                        if block.name in ("Write", "Edit", "MultiEdit") and \
                                str(block.input.get("file_path", "")).lower().endswith(".md"):
                            self.hub.publish(self.slug, {"type": "docs_changed", "transient": True})
            return
        if isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if not isinstance(block, ToolResultBlock):
                    continue
                self.open_tools.pop(block.tool_use_id, None)
                if block.tool_use_id in self.agents:
                    summary = _result_text(block)
                    if not block.is_error and const.ASYNC_RECEIPT.search(summary):
                        self._went_background(block, summary)
                        continue
                    label = self.agents.pop(block.tool_use_id)
                    timeline.stop(self.slug, block.tool_use_id)
                    done_task = (self.team.get(label) or {}).get("task", "")
                    self.set_role(label, status="idle", task=f"Finished: {done_task}" if done_task else "",
                                  last="reported back to Fred" if not block.is_error else "stopped with an error")
                    self.set_role("Fred", status="working", task="Reading the report")
                    self.hub.update(self.slug, block.tool_use_id, status="failed" if block.is_error else "done",
                                    summary=summary[:1200])
                    self.hub.publish(self.slug, {"type": "docs_changed", "transient": True})
            return
        if isinstance(msg, RateLimitEvent):
            entry = usage.record_limit(msg.rate_limit_info)
            self.hub.publish(self.slug, {"type": "usage", "entry": entry, "transient": True})
            if entry.get("status") == "rejected":
                self.limit_hit = entry
            if entry.get("status") == "allowed_warning" and entry["type"] not in self.warned:
                self.warned.add(entry["type"])
                pct = f"{entry['percent']:.0f}% of " if entry.get("percent") is not None else "most of "
                self.hub.publish(self.slug, {"type": "notice", "text": f"You have used {pct}your plan's "
                                             f"{entry['label'].lower()} limit."})
            return
        if isinstance(msg, ResultMessage):
            usage.record_tokens(self.slug, "total", None, msg.total_cost_usd or 0)
            changes = {}
            if msg.session_id:
                changes["session_id"] = msg.session_id
            if msg.total_cost_usd:
                p = projects.load(self.slug) or {}
                changes["cost_usd"] = round(float(p.get("cost_usd") or 0) + float(msg.total_cost_usd), 4)
                self.hub.publish(self.slug, {"type": "cost", "usd": changes["cost_usd"], "transient": True})
            if changes:
                projects.update(self.slug, **changes)
            if msg.is_error:
                status = getattr(msg, "api_error_status", None)
                detail = msg.result or "; ".join(map(str, getattr(msg, "errors", None) or [])) or msg.subtype
                if status in (401, 403) or is_signin_lost(str(detail)):
                    self.engine.on_signin_lost(self.slug)   # L37
                    return
                elif status == 429 or re.search(r"usage limit|rate limit|limit reached|out of (extra )?usage", str(detail), re.I):
                    if self.limit_hit is None:
                        pl = usage.plan()
                        week = pl.get("seven_day") or {}
                        weekly = week.get("status") == "rejected" or (week.get("percent") or 0) >= 100 \
                            or bool(re.search(r"week", str(detail), re.I))
                        kind = "seven_day" if weekly else "five_hour"
                        self.limit_hit = {"type": kind, "label": usage.LIMIT_LABELS[kind],
                                          "resets_at": (pl.get(kind) or {}).get("resets_at"), "detail": str(detail)[:300]}
                    return  # the limit card explains it (request F)
                if self.user_stopped or "[ede_diagnostic]" in str(detail):
                    # L31: the engine's answer to an interruption (pause / stop / restart): not an error for the user
                    log.info("engine result after an interruption (%s): %s", self.slug, str(detail)[:300])
                    return
                self.hub.publish(self.slug, {"type": "error", "text": str(detail)[:800]})
