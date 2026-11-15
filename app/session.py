"""One project's conversation with Claude: the team's state, approvals, a turn, background workers and the
messages coming back from Claude (L46; the Engine in app/engine.py owns the sessions)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import asyncio
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    RateLimitEvent,
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

try:  # background-task lifecycle messages (Claude Agent SDK 0.2.150+); older engines simply never send them
    from claude_agent_sdk import TaskNotificationMessage, TaskProgressMessage, TaskStartedMessage, TaskUpdatedMessage
except ImportError:  # pragma: no cover
    TaskNotificationMessage = TaskProgressMessage = TaskStartedMessage = TaskUpdatedMessage = type("_Never", (), {})

from . import (auth, brains, challenge, journal, languages, lessons as lessons_mod, models, paths, policy, projectmap, projects,
               prompts, risk, secrets_store, settings, skills as skills_mod, timeline, tooling, tools, usage)
from .events import EventHub
from . import engine_const as const
from .engine_text import _quiet, _result_text, _tail, approval_card, closed_by_windows, describe_tool, friendly_error, interrupted_text, is_lost_conversation, is_signin_lost, iterations_note

if TYPE_CHECKING:
    from .engine import Engine

log = logging.getLogger(__name__)


class Session:
    def __init__(self, engine: "Engine", slug: str):
        self.engine = engine
        self.hub: EventHub = engine.hub
        self.slug = slug
        self.client = None
        self.client_key = None
        self.busy = False
        self.agents: dict[str, str] = {}  # tool_use_id -> worker label
        self.tool_handlers: dict[str, Any] = {}
        self.counted: set = set()    # model calls already counted for usage
        self.warned: set = set()     # plan limits already warned about
        self.lock = asyncio.Lock()
        self.last_text: dict[str, str] = {}   # who -> what they said since their last action
        self.says_for_tool: dict[str, str] = {}  # who -> what they said just before their current action
        self.team: dict[str, dict] = {}       # role -> live status for the team view
        self.limit_hit: dict | None = None
        self.turn_started: float | None = None
        self.turn_ended = 0.0
        self.pending_kickoff: str | None = None  # hidden message for the next speaker after a handoff
        self.background: dict[str, dict] = {}    # worker started in the background anyway: agent id -> card
        self.task_tools: dict[str, str] = {}     # engine task id -> the tool use id that started it
        self.bg_reports: list[dict] = []         # finished background workers whose report Fred has not seen yet
        self.bg_done_at = 0.0
        self.watch_task: asyncio.Task | None = None  # listens for background workers between turns (L7)
        self.last_activity = time.time()         # last sign of life: a message, a token, a tool result (L8)
        self.last_lead_t = 0.0                   # when the lead last said or did something
        self.open_tools: dict[str, dict] = {}    # tool calls started and not answered yet (a running command)
        self.watch = {"since": 0.0, "noted": 0.0, "stuck": None}  # watchdog state for the current quiet spell
        self.user_stopped = False
        self.auto_help = {"times": [], "wake_at": 0.0, "restart_at": 0.0}  # L27: what the watchdog did by itself

    # ------------------------------------------------------------------ team view
    def set_role(self, role: str, **changes) -> None:
        r = self.team.setdefault(role, {"status": "idle", "task": "", "last": "", "last_time": 0, "recent": []})
        if "last" in changes and changes["last"]:
            r["recent"] = ([{"text": changes["last"], "time": time.time()}] + r["recent"])[:12]
            r["last_time"] = time.time()
        if changes.get("status") == "working" and r["status"] != "working":
            r["since"] = time.time()
        r.update(changes)
        self.hub.publish(self.slug, {"type": "team", "team": self.team_snapshot(), "transient": True})

    def team_snapshot(self) -> list[dict]:
        now = time.time()
        brain_names = [brains.label(b) for b in brains.all_brains() if b.get("enabled")]
        names = list(const.ROLES) + brain_names
        names += [n for n in self.team if n not in names]
        waiting = self.engine.waiting_user(self.slug)   # someone waits for the user: nobody is "stalled"
        running = {t["who"] for t in self.open_tools.values()}   # a command still running is activity
        out = []
        for name in names:
            r = dict(self.team.get(name) or {"status": "idle", "task": "", "last": "", "last_time": 0, "recent": []})
            if r["status"] == "working" and not waiting and name not in running and r.get("last_time") \
                    and now - r["last_time"] > const.STALL_SECONDS and now - r.get("since", now) > const.STALL_SECONDS:
                r["stalled"] = True
            r["name"] = name
            # L27: every worker (Diagrammer, Scout, Challenger...) is a worker; only the council's brains are brains
            r["kind"] = const.LEADS.get(name) or ("brain" if name in brain_names else "worker")
            out.append(r)
        # the lead supervising a worker that is moving (or asking) is not stalled himself
        if any(o["kind"] == "worker" and o["status"] in ("working", "question") and not o.get("stalled") for o in out):
            for o in out:
                if o["kind"] in ("lead", "architect"):
                    o.pop("stalled", None)
        return out

    @property
    def kind(self) -> str:
        return "setup" if self.slug == projects.SETUP_SLUG else "project"

    @property
    def persona(self) -> str:
        """Who is talking with the user: Mat (lead architect) during the architecture phase, otherwise Fred."""
        if self.kind == "project" and (projects.load(self.slug) or {}).get("phase") == "architecture":
            return "Mat"
        return "Fred"

    @property
    def folder(self) -> Path:
        return projects.project_dir(self.slug)

    # ------------------------------------------------------------------ helpers used by tools
    async def run_blocking(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def publish_screenshot(self, path: Path, caption: str) -> None:
        rel = path.resolve().relative_to(self.folder.resolve()).as_posix()
        self.hub.publish(self.slug, {"type": "screenshot", "path": rel, "caption": caption})

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

    # ------------------------------------------------------------------ permissions
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

    # ------------------------------------------------------------------ SDK options
    def build_options(self) -> ClaudeAgentOptions:
        s = settings.load()
        project = projects.load(self.slug) or {}
        existing = prompts.EXISTING_NOTE if project.get("existing_folder") else prompts.NEW_NOTE
        persona = self.persona
        conv = languages.conventions()
        if self.kind != "setup":
            skills_mod.install_into(self.folder)
            projectmap.refresh(self.folder)   # L20
        learned = lessons_mod.note()           # L23: lessons from earlier projects
        if self.kind == "setup":
            system = prompts.render(prompts.SETUP, workspace=str(paths.default_workspace()))
            agents = None
        elif persona == "Mat":
            system = prompts.render(prompts.ARCHITECT, name=project.get("name", self.slug), folder=str(self.folder),
                                    existing=existing, language="Python") \
                + skills_mod.note_for("Mat", project, self.folder)
            agents = None
        else:
            github = f"connected as {s['github_login']}" if s["github_login"] and secrets_store.has("github_token") \
                else "not connected"
            if project.get("github_repo"):
                github += f"; repository {project['github_repo']}"
            system = prompts.render(prompts.PROJECT, name=project.get("name", self.slug), folder=str(self.folder),
                                    phase=project.get("phase", "discover"), existing=existing,
                                    architecture_approved="yes" if projects.architecture_approved(project) else "no",
                                    plan_approved="yes" if project.get("plan_approved") else "no", github=github,
                                    iterations=iterations_note(self.slug),
                                    models=models.guide(), language="Python", conventions=conv,
                                    challenge=challenge.level_text()) \
                + skills_mod.note_for("Fred", project, self.folder) + learned
            agents = {
                "developer": AgentDefinition(description="Builds and fixes the software from Fred's brief.",
                                             prompt=prompts.render(prompts.DEVELOPER, conventions=conv)
                                             + skills_mod.note_for("Developer", project, self.folder) + learned,
                                             model=models.for_role("Developer")),
                "tester": AgentDefinition(description="Writes and runs tests and takes screenshots as proof.",
                                          prompt=prompts.render(prompts.TESTER, conventions=conv)
                                          + skills_mod.note_for("Tester", project, self.folder) + learned,
                                          model=models.for_role("Tester")),
                "reviewer": AgentDefinition(description="Reviews the work against the requirements; read-only.",
                                            prompt=prompts.render(prompts.REVIEWER)
                                            + skills_mod.note_for("Reviewer", project, self.folder) + learned,
                                            model=models.for_role("Reviewer"),
                                            tools=["Read", "Glob", "Grep", "Write", "Bash", "PowerShell",
                                                   "mcp__aiteam__run_tests"]),
                "challenger": AgentDefinition(  # item 79: independent devil's advocate, never saw the chat
                    description="Reads the problem and requirements cold and returns objections, contradictions, "
                                "unconfirmed assumptions and vague points as JSON. Read-only.",
                    prompt=prompts.render(prompts.CHALLENGER), model=models.for_role("Reviewer"),
                    tools=["Read", "Glob", "Grep"]),
                "scout": AgentDefinition(description="Searches the web for existing solutions (prior art).",
                                         prompt=prompts.render(prompts.SCOUT), model=models.for_role("Reviewer"),
                                         tools=["Read", "Glob", "Grep", "Write", "WebSearch", "WebFetch"]),
            }
            if tooling.archify_ready() and skills_mod.enabled().get("archify"):
                agents["diagrammer"] = AgentDefinition(  # item 78: focused, validated "how it works" diagrams
                    description="Draws one focused, validated diagram of the project with Archify and records it.",
                    prompt=prompts.render(prompts.DIAGRAMMER, cmd=tooling.archify_cmd(),
                                          skill=str(tooling.archify_dir() / "SKILL.md")),
                    model=models.for_role("Tester"),
                    tools=["Read", "Glob", "Grep", "Write", "Bash", "PowerShell", "mcp__aiteam__record_diagram"])
        server, chosen = tools.build_server(self)
        self.tool_handlers = {t.name: t.handler for t in chosen}
        env = auth.cli_env(auth.mode())
        return ClaudeAgentOptions(
            model=self.lead_model(), cwd=str(self.folder),
            system_prompt={"type": "preset", "preset": "claude_code", "append": system},
            mcp_servers={"aiteam": server}, agents=agents, can_use_tool=self.can_use_tool,
            permission_mode="default", resume=project.get("session_id"), env=env, setting_sources=[],
        )

    def lead_model(self) -> str:
        return models.model_id(models.for_role(self.persona))

    async def _reset_context(self) -> None:
        """Drop the long conversation so the next iteration starts small; the essentials are re-sent as a briefing."""
        projects.update(self.slug, session_id=None)
        await self._drop_client()
        self.counted.clear()
        self.warned.clear()

    def _briefing(self) -> str:
        """A compact recap from the maintained documents, instead of the whole past conversation."""
        p = projects.load(self.slug) or {}
        def trim(name, limit):
            t = projects.read_doc(self.slug, name).strip()
            return (t[:limit] + " …") if len(t) > limit else t
        parts = [f"[App] Continuing the approved plan for \"{p.get('name', self.slug)}\" in a fresh context to save "
                 "tokens. Here is the state; the full earlier chat is not resent. Keep talking to the user normally."]
        for name, limit in (("REQUIREMENTS.md", 2500), ("ARCHITECTURE.md", 2000), ("PLAN.md", 1500), ("STATUS.md", 1200)):
            doc = trim(name, limit)
            if doc:
                parts.append(f"## {name}\n{doc}")
        parts.append("## Plan status\n" + iterations_note(self.slug))
        return "\n\n".join(parts)

    async def _ensure_client(self):
        key = (self.lead_model(), auth.mode(), bool(secrets_store.get_secret("anthropic_api_key")), self.persona)
        if self.client is not None and key == self.client_key:
            return self.client
        await self._drop_client()
        self.client = self.engine.client_factory(self.build_options())
        await self.client.connect()
        self.client_key = key
        return self.client

    async def _drop_client(self):
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None

    # ------------------------------------------------------------------ a turn of conversation
    async def turn(self, text: str, hidden: bool = False) -> None:
        await self.stop_listening()   # the turn reads the engine's messages itself from here on
        async with self.lock:
            self.busy = True
            self.limit_hit = None
            self.user_stopped = False   # L31: set when the user pauses, stops or restarts during this turn
            self.turn_started = time.time()
            self.last_activity = time.time()
            self.hub.publish(self.slug, {"type": "busy", "value": True})
            journal.record(self.slug, active=True)
            self.set_role(self.persona, status="working", task="Answering you" if not hidden else "Working",
                          last="thinking")
            if not hidden:
                self.hub.publish(self.slug, {"type": "message", "role": "user", "text": text})
            try:
                if not auth.ready() and self.slug in self.engine.signin_waiting:   # L37: the card says what to do
                    self.hub.publish(self.slug, {"type": "notice", "text": "Fred will read this once you have "
                                                 "signed in again (see the card above)."})
                    self.engine.signin_messages.setdefault(self.slug, []).append(text)
                    return
                if not auth.ready():
                    raise RuntimeError("Fred is not connected to Claude yet (no API key saved and not signed in "
                                       "with a Claude subscription). Open Settings to connect.")
                if hidden and settings.load().get("cap_context", True) and any(m in text for m in const.FRESH_MARKERS) \
                        and not self.background:  # a fresh context would stop a worker still busy in the background
                    await self._reset_context()   # start this iteration with a fresh, small context (item 48)
                    text = self._briefing() + "\n\n" + text
                notes = self.engine.notes.pop(self.slug, [])
                if auth.mode() == "subscription":
                    note = await asyncio.to_thread(usage.warning_note)  # may refresh the real figures (~2 s)
                    if note:
                        notes.append(note)
                    if hidden and any(m in text for m in const.FRESH_MARKERS):   # L15: a big step is about to start
                        from . import analytics
                        room = analytics.headroom_warning()
                        if room:
                            self.hub.publish(self.slug, {"type": "headroom", "text": room})
                for attempt in (1, 2):
                    try:
                        # L36: a refused resume is reported while connecting (e.g. a project started in AI Team, whose
                        # conversation lives under AI Team's own Claude sign-in), so connecting is inside the guard too
                        client = await self._ensure_client()
                        await client.query("\n".join(notes) + f"\n\n{text}" if notes else text)
                        async for msg in client.receive_response():
                            self.handle(msg)
                        break
                    except Exception as e:  # noqa: BLE001
                        if attempt == 2 or not is_lost_conversation(e):
                            raise
                        # Fred's saved conversation cannot be found (folder path changed, or the conversation was
                        # removed): forget it and continue in a fresh one, caught up from the documents (item 74)
                        projects.update(self.slug, session_id=None)
                        await self._drop_client()
                        self.counted.clear()
                        self.hub.publish(self.slug, {"type": "notice", "text": f"{self.persona}'s earlier chat for "
                                                     "this project could not be found, so he caught up from the "
                                                     "project documents and continued."})
                        text = self._briefing() + "\n\n" + text
            except asyncio.CancelledError:
                self.hub.publish(self.slug, {"type": "notice", "text": "Stopped."})
                await self._drop_client()
            except Exception as e:  # noqa: BLE001
                await self._drop_client()
                if is_signin_lost(str(e)):            # L37: one clear card, not a red error per attempt
                    self.engine.on_signin_lost(self.slug)
                elif closed_by_windows(e) and not self.user_stopped:
                    self.engine.on_engine_closed(self.slug)
                else:
                    self.hub.publish(self.slug, {"type": "error", "text": friendly_error(e)})
            finally:
                timeline.record(self.slug, "lead", self.persona, self.turn_started or time.time(), time.time())
                self.turn_ended = time.time()
                self.turn_started = None
                self.open_tools.clear()
                # a worker that finished during this turn and was followed by the lead's own words: he has its report
                self.bg_reports = [r for r in self.bg_reports if r["t"] > self.last_lead_t]
                bg_roles = {b["label"] for b in self.background.values()}
                for role, r in list(self.team.items()):
                    if r.get("status") in ("working", "question") and role not in bg_roles:
                        self.set_role(role, status="idle")
                self.set_role("Fred", status="idle", task="")
                self.set_role("Mat", status="idle", task="")
                if self.limit_hit is not None:
                    self.engine.on_limit(self.slug, self.limit_hit)
                projects.update(self.slug, updated=time.time())
                journal.record(self.slug, active=False)
                more = bool(self.engine.queued.get(self.slug)) or bool(self.pending_kickoff) or bool(self.background) \
                    or bool(self.bg_reports)
                if not more:
                    self.hub.publish(self.slug, {"type": "busy", "value": False})
                self.hub.publish(self.slug, {"type": "docs_changed", "transient": True})

    # ------------------------------------------------------------------ L7: a worker in the background
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

    # ------------------------------------------------------------------ L8: is anything still moving?
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
