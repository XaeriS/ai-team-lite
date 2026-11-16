"""Asking the user: approval cards, questions, and the check every tool call goes through.

Part of a Session (app/session.py), kept in its own file so each file has one job (L46)."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
import uuid

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from . import engine_const as const, models, paths, policy, projects, risk, scorecard, settings, timeline
from .engine_text import approval_card, interrupted_text


class ApprovalsMixin:
    async def ask_user(self, kind: str, payload: dict) -> dict:
        """Show an interactive card and wait for the user's answer."""
        item_id = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self.engine.pending[item_id] = (fut, self.slug, kind)
        self.hub.publish(self.slug, {"type": kind, "id": item_id, "status": "pending", **payload})
        self.hub.publish(self.slug, {"type": "project_list", "transient": True})
        self.engine.notify("card", slug=self.slug, item_id=item_id, kind=kind, payload=payload)
        # while this project waits for the user, its working slot goes to the next work in the master plan
        threading.Thread(target=self.engine._start_next, daemon=True).start()
        asked = time.time()
        answer: dict = {}
        try:
            answer = await fut
            return answer
        except asyncio.CancelledError:
            self.hub.update(self.slug, item_id, status="cancelled")
            return {"cancelled": True}
        finally:
            self.engine.pending.pop(item_id, None)
            self.engine.notify("answered", slug=self.slug, item_id=item_id, via=answer.get("via"))
            self.last_activity = time.time()
            timeline.record(self.slug, "wait", self.persona, asked, time.time())   # L14: time waiting for you

    async def can_use_tool(self, tool_name: str, tool_input: dict, ctx: Any):
        result = await self._can_use_tool(tool_name, tool_input, ctx)
        if tool_name in const.AGENT_TOOLS and isinstance(result, PermissionResultAllow):
            # The app follows each worker until it reports back: workers always run in the foreground.
            # The model is the one Fred chose (if switched on) or the one the user fixed for that role.
            inp = dict(tool_input or {})
            role = models.WORKER_KEYS.get(str(inp.get("subagent_type", "")))
            changes = {"run_in_background": False}
            if role:
                changes["model"] = models.nearest(inp.get("model") or models.for_role(role))
            if any(inp.get(k) != v for k, v in changes.items()):
                result = PermissionResultAllow(updated_input={**inp, **changes})
        return result

    async def _ask_user_question(self, tool_input: dict):
        """Claude's built-in multiple-choice question (AskUserQuestion): shown as AI Team Lite's normal question card,
        one card per question, and the answers are handed back the way the engine expects (item 72)."""
        answers = {}
        for q in (tool_input.get("questions") or [])[:4]:
            if not isinstance(q, dict):
                continue
            text = str(q.get("question") or q.get("header") or "").strip()
            opts = [str(o.get("label") if isinstance(o, dict) else o) for o in (q.get("options") or [])][:5]
            hints = [str(o.get("description") or "") for o in (q.get("options") or []) if isinstance(o, dict)][:5]
            card = {"title": text, "options": opts}
            if any(hints):
                card["hints"] = hints
            if q.get("multiSelect"):
                card["note"] = "You can type several answers, separated by commas."
            answer = await self.ask_user("choice", card)
            if answer.get("interrupted") or answer.get("cancelled"):
                return PermissionResultDeny(message=interrupted_text(answer) if answer.get("interrupted") else
                                            "The user closed the question without answering.",
                                            interrupt=bool(answer.get("cancelled")))
            answers[text] = str(answer.get("value") or "").strip() or "(no answer)"
        return PermissionResultAllow(updated_input={**tool_input, "answers": answers})

    async def _can_use_tool(self, tool_name: str, tool_input: dict, ctx: Any):
        if tool_name == "AskUserQuestion":
            return await self._ask_user_question(tool_input or {})
        s = settings.load()
        project = projects.load(self.slug) or {}
        roots = [str(self.folder), str(paths.bundled_python().parent), str(paths.install_dir())]
        decision, reason = policy.decide(
            tool_name, tool_input or {}, kind=self.kind, mode=s["approval_mode"],
            plan_approved=bool(project.get("plan_approved")),
            inside_project=lambda p: projects.inside(self.slug, p), allowed_roots=roots)
        if decision == "allow":
            return PermissionResultAllow()
        card = approval_card(tool_name, tool_input or {}, ctx, self.folder)
        agent_id = getattr(ctx, "agent_id", None)
        who = self.persona if not agent_id else (self._current_worker() or "A worker agent")
        card["who"] = who
        card["reason"] = reason
        card.update(risk.explain(tool_name, tool_input or {}, self.folder, lambda p: projects.inside(self.slug, p)))
        if tool_name == "mcp__aiteam__github_push":   # L49: red points of the scorecard are shown, not blocking
            warn = scorecard.push_warning(self.slug)
            if warn:
                card["quality"] = warn
        said = (self.says_for_tool.pop(who, "") or self.last_text.pop(who, "")).strip()
        if said:
            card["says"] = said[-700:]
        self.set_role(who, status="question", last=f"waiting for your approval: {card['title'].lower()}")
        answer = await self.ask_user("approval", card)
        self.set_role(who, status="working")   # L27: approved or not, the agent carries on (it was shown idle before)
        if answer.get("interrupted"):
            return PermissionResultDeny(message=interrupted_text(answer))
        if answer.get("approved"):
            return PermissionResultAllow()
        note = (answer.get("note") or "").strip()
        msg = "The user declined this action." + (f" Their note: {note}" if note else "") + \
              " Do not retry it. Ask the user how they would like to proceed."
        return PermissionResultDeny(message=msg, interrupt=bool(answer.get("cancelled")))

    def _current_worker(self) -> str | None:
        return next(reversed(self.agents.values()), None) if self.agents else None
