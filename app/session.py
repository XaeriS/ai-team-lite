"""One project's conversation with Claude: the team's state, approvals, a turn, background workers and the
messages coming back from Claude (L46; the Engine in app/engine.py owns the sessions)."""
from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

from . import (auth, brains, challenge, engine_const as const, journal, languages, lessons as lessons_mod, models,
               paths, projectmap, projects, prompts, secrets_store, settings, skills as skills_mod, timeline, tooling,
               tools, usage)
from .engine_text import (OVERSIZE_NOTE, closed_by_windows, friendly_error, is_lost_conversation, is_oversize,
                          is_signin_lost, iterations_note)
from .events import EventHub

if TYPE_CHECKING:
    from .engine import Engine

from .session_approvals import ApprovalsMixin
from .session_background import BackgroundMixin
from .session_messages import MessagesMixin


class Session(ApprovalsMixin, BackgroundMixin, MessagesMixin):
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

    async def run_blocking(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def publish_screenshot(self, path: Path, caption: str) -> None:
        rel = path.resolve().relative_to(self.folder.resolve()).as_posix()
        self.hub.publish(self.slug, {"type": "screenshot", "path": rel, "caption": caption})

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
            max_buffer_size=const.MAX_MESSAGE_MB * 1024 * 1024,   # L67: the engine's default is 1 MB
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
                        if attempt == 1 and is_oversize(e):   # L67: recover by itself, and tell Fred why
                            await self._drop_client()
                            self.hub.publish(self.slug, {"type": "notice", "text": "A file was too big to read in one "
                                                         "go; the work restarted and the team now reads it in parts."})
                            text = OVERSIZE_NOTE.format(mb=const.MAX_MESSAGE_MB)
                            continue
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
