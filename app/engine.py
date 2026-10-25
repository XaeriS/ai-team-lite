"""The agent engine: one Claude Agent SDK session per conversation (setup or project).

- Fred is the main agent. Worker agents (developer, tester, reviewer) are SDK subagents he starts.
- Every tool call goes through `can_use_tool`, which applies policy.py and, when needed, shows an approval
  card in the chat and waits for the user.
- Everything the user should see becomes an event on the EventHub.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import (
    AgentDefinition,
    RateLimitEvent,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
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

from . import (auth, brains, challenge, iterations, journal, languages, lessons as lessons_mod, models, paths, policy,
               projectmap, projects, prompts, risk, secrets_store, settings, skills as skills_mod, timeline, tooling,
               tracking, tools, usage)
from .events import EventHub

AGENT_TOOLS = {"Agent", "Task"}
QUIET_TOOLS = {"ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool"}  # engine housekeeping, not shown
ASYNC_RECEIPT = re.compile(r"Async agent launched", re.I)
AGENT_ID = re.compile(r"agentId:\s*([A-Za-z0-9_-]+)")
# These tools show their own card in the chat, so no extra activity line
CARD_TOOLS = {f"mcp__aiteam__{n}" for n in ("request_plan_approval", "ask_folder", "request_secret", "ask_choice",
                                            "take_screenshot", "show_image", "launch_for_user", "finish_setup",
                                            "start_iteration", "commit_iteration", "record_test_result",
                                            "handoff_to_architect", "request_architecture_approval", "record_problem",
                                            "record_challenge_review", "record_diagram",
                                            "return_to_fred", "record_prior_art", "ask_council",
                                            "record_change_request", "run_security_test", "record_questions", "connect_phone",
                                            "record_lesson")}
WORKER_LABELS = {"developer": "Developer", "tester": "Tester", "reviewer": "Reviewer", "scout": "Scout",
                 "challenger": "Challenger", "diagrammer": "Diagrammer"}
ROLES = ["Fred", "Mat", "Developer", "Tester", "Reviewer"]
LEADS = {"Fred": "lead", "Mat": "architect"}
STALL_SECONDS = 300
TERMINAL = {"completed", "failed", "stopped", "killed"}
WAKE_GRACE = 8          # after a background worker finishes, give the engine this long to wake Fred by itself (L7)
WATCH_POLL = 30         # how often the listener re-checks while a background worker runs
QUIET_NOTE = 180        # L8: nothing new for 3 minutes while working -> a short "still working" line
QUIET_STUCK = 300       # L8: nothing at all for 5 minutes (no tokens, no tool running) -> say so and offer help
COMMAND_STUCK = 1200    # a single command running for 20 minutes without any output is treated as stuck too
AUTO_HELP_MAX = 3       # L27: automatic wake-ups / restarts per project per hour; after that the user decides
WATCHDOG_TICK = 30
LIMIT_CHECK_SECONDS = 900   # L25: reset time unknown -> look at the plan's figures again every 15 minutes
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}
PIPELINE_KICK_SECONDS = 1800   # never nudge the same iteration twice within this time
PIPELINE_TICK = 60
FRESH_MARKERS = ("start_iteration(", "Continue iteration", "user released iteration")  # safe context-reset points
CONTINUE_TEXT = ("[App] The Claude plan's usage limit has reset. Continue the work exactly where you stopped; do not "
                 "redo steps that were already finished. Tell the user in one line what you are resuming.")
ClientFactory = Callable[[ClaudeAgentOptions], Any]


def validate_anthropic_key(key: str) -> tuple[bool, str]:
    """Cheap check (lists models, costs nothing). Returns (ok, message)."""
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=1", headers={
        "x-api-key": key.strip(), "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True, "Key accepted."
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "Anthropic refused this key. Check you copied all of it (it starts with sk-ant-)."
        return False, f"Anthropic answered with error {e.code}. Try again in a minute."
    except urllib.error.URLError as e:
        return False, f"Could not reach Anthropic ({e.reason}). Check your internet connection."


def _short(value: Any, limit: int = 90) -> str:
    s = " ".join(str(value).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _file_label(path: str, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def describe_tool(name: str, inp: dict, root: Path) -> str:
    """One plain-English line for the activity feed."""
    f = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""
    if name == "Read":
        return f"reading {_file_label(f, root)}"
    if name == "Write":
        return f"writing {_file_label(f, root)}"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"editing {_file_label(f, root)}"
    if name in ("Glob", "Grep", "LS"):
        return "looking through the files"
    if name in ("Bash", "PowerShell"):
        return _short(inp.get("description") or f"running: {inp.get('command', '')}")
    if name == "WebSearch":
        return f"searching the web for “{_short(inp.get('query', ''), 60)}”"
    if name == "WebFetch":
        return f"reading {_short(inp.get('url', ''), 60)}"
    if name in AGENT_TOOLS:
        return f"briefing the {WORKER_LABELS.get(inp.get('subagent_type', ''), 'worker')}"
    if name == "TodoWrite":
        return "updating the to-do list"
    if name in QUIET_TOOLS:
        return "getting ready"
    if name == "mcp__aiteam__run_tests":
        return "running the full test suite" if str(inp.get("scope", "")).lower() == "all" else "running the related tests"
    if name.startswith("mcp__aiteam__"):
        return name.removeprefix("mcp__aiteam__").replace("_", " ")
    return name


APPROVAL_TITLES = {
    "Bash": "Run a command", "PowerShell": "Run a command", "Write": "Create or replace a file",
    "Edit": "Change a file", "MultiEdit": "Change a file", "NotebookEdit": "Change a notebook",
    "Agent": "Start a worker agent", "Task": "Start a worker agent",
    "mcp__aiteam__github_create_repo": "Create a GitHub repository",
    "mcp__aiteam__github_push": "Save the project to GitHub",
    "mcp__aiteam__launch_for_user": "Start your app for you to try",
    "mcp__aiteam__take_screenshot": "Start the app to take a screenshot",
}


def approval_card(name: str, inp: dict, ctx: Any, root: Path) -> dict:
    title = APPROVAL_TITLES.get(name) or getattr(ctx, "title", None) or name
    explanation = inp.get("explanation") or inp.get("description") or getattr(ctx, "description", None) or ""
    details = ""
    if name in ("Bash", "PowerShell"):
        details = str(inp.get("command", ""))
    elif name == "Write":
        details = f"{_file_label(inp.get('file_path', ''), root)}\n\n{str(inp.get('content', ''))[:1500]}"
    elif name in ("Edit", "MultiEdit"):
        edits = inp.get("edits") or [inp]
        parts = [f"{_file_label(inp.get('file_path', ''), root)}"]
        for e in edits[:3]:
            parts.append(f"- {_short(e.get('old_string', ''), 300)}\n+ {_short(e.get('new_string', ''), 300)}")
        details = "\n".join(parts)
    elif name in AGENT_TOOLS:
        explanation = explanation or inp.get("description", "")
        details = f"{WORKER_LABELS.get(inp.get('subagent_type', ''), inp.get('subagent_type', 'worker'))}: " \
                  f"{str(inp.get('prompt', ''))[:1500]}"
    else:
        details = json.dumps({k: v for k, v in inp.items() if k != "explanation"}, indent=2)[:1500]
    return {"title": title, "explanation": explanation, "details": details, "tool": name}


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
        names = list(ROLES) + brain_names
        names += [n for n in self.team if n not in names]
        waiting = self.engine.waiting_user(self.slug)   # someone waits for the user: nobody is "stalled"
        running = {t["who"] for t in self.open_tools.values()}   # a command still running is activity
        out = []
        for name in names:
            r = dict(self.team.get(name) or {"status": "idle", "task": "", "last": "", "last_time": 0, "recent": []})
            if r["status"] == "working" and not waiting and name not in running and r.get("last_time") \
                    and now - r["last_time"] > STALL_SECONDS and now - r.get("since", now) > STALL_SECONDS:
                r["stalled"] = True
            r["name"] = name
            # L27: every worker (Diagrammer, Scout, Challenger...) is a worker; only the council's brains are brains
            r["kind"] = LEADS.get(name) or ("brain" if name in brain_names else "worker")
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
        if tool_name in AGENT_TOOLS and isinstance(result, PermissionResultAllow):
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
            self.turn_started = time.time()
            self.last_activity = time.time()
            self.hub.publish(self.slug, {"type": "busy", "value": True})
            journal.record(self.slug, active=True)
            self.set_role(self.persona, status="working", task="Answering you" if not hidden else "Working",
                          last="thinking")
            if not hidden:
                self.hub.publish(self.slug, {"type": "message", "role": "user", "text": text})
            try:
                if not auth.ready():
                    raise RuntimeError("Fred is not connected to Claude yet (no API key saved and not signed in "
                                       "with a Claude subscription). Open Settings to connect.")
                if hidden and settings.load().get("cap_context", True) and any(m in text for m in FRESH_MARKERS) \
                        and not self.background:  # a fresh context would stop a worker still busy in the background
                    await self._reset_context()   # start this iteration with a fresh, small context (item 48)
                    text = self._briefing() + "\n\n" + text
                notes = self.engine.notes.pop(self.slug, [])
                if auth.mode() == "subscription":
                    note = await asyncio.to_thread(usage.warning_note)  # may refresh the real figures (~2 s)
                    if note:
                        notes.append(note)
                    if hidden and any(m in text for m in FRESH_MARKERS):   # L15: a big step is about to start
                        from . import analytics
                        room = analytics.headroom_warning()
                        if room:
                            self.hub.publish(self.slug, {"type": "headroom", "text": room})
                for attempt in (1, 2):
                    client = await self._ensure_client()
                    try:
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
                self.hub.publish(self.slug, {"type": "error", "text": friendly_error(e)})
                await self._drop_client()
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
        m = AGENT_ID.search(receipt)
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
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _listen(self) -> None:
        self_woke = False
        while True:
            if not self.background and not self.bg_reports and not self_woke:
                return
            timeout: float | None = WATCH_POLL
            if not self.background and self.bg_reports and not self_woke:
                timeout = max(0.05, self.bg_done_at + WAKE_GRACE - time.time())
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
                await asyncio.sleep(min(timeout or WATCH_POLL, WATCH_POLL))
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
                    and time.time() >= self.bg_done_at + WAKE_GRACE:
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
            if (msg.status or "") in TERMINAL:
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
                    if block.name in AGENT_TOOLS:
                        label = WORKER_LABELS.get(block.input.get("subagent_type", ""), "Worker")
                        self.agents[block.id] = label
                        timeline.start(self.slug, block.id, label)
                        self.set_role(label, status="working", task=block.input.get("description", "") or "Working",
                                      last="received a brief from Fred")
                        self.set_role("Fred", status="working", task=f"Supervising the {label}")
                        self.hub.publish(self.slug, {"type": "agent", "id": block.id, "name": label,
                                                     "status": "working",
                                                     "description": block.input.get("description", "")})
                    elif block.name in QUIET_TOOLS:
                        pass
                    elif block.name not in CARD_TOOLS:
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
                    if not block.is_error and ASYNC_RECEIPT.search(summary):
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
                if status in (401, 403):
                    detail = ("Your Claude sign-in has expired or was refused. Open Settings and sign in again."
                              if auth.mode() == "subscription" else
                              "Anthropic refused the API key. Open Settings and paste a new key.")
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
                self.hub.publish(self.slug, {"type": "error", "text": str(detail)[:800]})


def _tail(path: str, limit: int = 4000) -> str:
    """The end of a background worker's output file (its last words are its report)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = []
    for line in text.splitlines():  # the file may be a JSON transcript: keep the assistant's text
        try:
            row = json.loads(line)
        except ValueError:
            lines.append(line)
            continue
        msg = row.get("message") if isinstance(row, dict) else None
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            c = msg.get("content")
            lines.append(c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict)))
    return "\n".join(x for x in lines if x.strip())[-limit:]


async def _quiet(coro) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001
        pass


def _result_text(block: Any) -> str:
    c = block.content
    return c if isinstance(c, str) else " ".join(x.get("text", "") for x in (c or []) if isinstance(x, dict))


_NOT_A_START = re.compile(r"test|migrat|install|setup|build|clean|deploy|uninstall|lint|format|update", re.I)
_START_FIRST = ("run_app", "run", "start", "launch", "app")


def is_lost_conversation(e: Exception) -> bool:
    """Claude's engine could not find the conversation AI Team Lite asked it to continue (item 74)."""
    s = str(e).lower()
    return "no conversation found" in s or ("session" in s and "not found" in s)


def launch_candidates(slug: str) -> list[str]:
    """Start files in the project folder (run.bat, run_app.bat, start.cmd...), most likely first (item 71)."""
    try:
        folder = projects.project_dir(slug)
        files = [f.name for f in folder.iterdir() if f.is_file() and f.suffix.lower() in (".bat", ".cmd")
                 and not _NOT_A_START.search(f.stem)]
    except OSError:
        return []
    rank = lambda n: (_START_FIRST.index(n.rsplit(".", 1)[0].lower()) if n.rsplit(".", 1)[0].lower() in _START_FIRST
                      else len(_START_FIRST), n.lower())
    return sorted(files, key=rank)


def launch_info(slug: str, p: dict | None = None) -> dict | None:
    """How to start the app (request 21), or None while there is nothing runnable. It shows once something works:
    a finished iteration, a project in review, or an existing folder that already held a working app (item 71)."""
    p = p if p is not None else (projects.load(slug) or {})
    its = iterations.load(slug)
    works = any(it.get("status") == "done" for it in its) or p.get("phase") == "review" or bool(p.get("existing_folder"))
    cands = launch_candidates(slug)
    launch = p.get("launch")  # kept even if its file went missing: Launch then explains and offers a fix
    if not launch and cands:
        launch = {"command": cands[0], "url": ""}
    if not works or not launch:
        return None
    last = next((it for it in reversed(its) if it.get("status") == "done"), None)
    # several start files and the user never chose: ask once which one (the choice is remembered)
    choices = cands if len(cands) > 1 and not p.get("launch_chosen") else []
    return {**launch, "version": f"iteration {last['n']}: {last['name']}" if last else "", "choices": choices,
            "all_choices": cands}


def iterations_note(slug: str) -> str:
    its = iterations.load(slug)
    if not its:
        return "none planned yet"
    return "; ".join(f"{it['n']}. {it['name']} [{it.get('status', 'planned')}{' ' + it['commit'] if it.get('commit') else ''}]"
                     for it in its)


def interrupted_text(answer: dict) -> str:
    return ("The user has not answered this yet. Before that, they asked: \"" + str(answer.get("question", "")) +
            "\". Answer their question first. Then, if it is still needed, ask again (call the same tool again).")


def friendly_error(e: Exception) -> str:
    name = type(e).__name__
    text = str(e)
    if name == "CLINotFoundError":
        return "The Claude engine is missing from the installation. Reinstall AI Team Lite."
    if "Not logged in" in text or "auth login" in text:
        return "Fred is not signed in to your Claude subscription. Open Settings and sign in again."
    if "API key" in text or "401" in text:
        return f"Problem with the Anthropic API key: {text[:300]}"
    return f"Something went wrong ({name}): {text[:500]}"


class Engine:
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
        self.loop.call_later(PIPELINE_TICK, tick)

    def notify(self, event: str, **details) -> None:
        """Tell listeners (the phone) that a card waits, was answered, or that work is stuck / hit the limit."""
        for listener in list(self.listeners):
            try:
                listener(event, **details)
            except Exception:  # noqa: BLE001  (a listener must never break the work)
                pass

    def _schedule_watchdog(self) -> None:
        def tick():
            try:
                self.watchdog()
            except Exception:  # noqa: BLE001  (the watchdog must never stop the app)
                pass
            self._schedule_watchdog()
        self.loop.call_later(WATCHDOG_TICK, tick)

    # ------------------------------------------------------------------ L8: the watchdog
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
        stuck = (quiet >= QUIET_STUCK and not tools_running) or quiet >= COMMAND_STUCK
        if stuck:
            found["state"] = "stuck"
            first = not w["stuck"]
            if not first and not (w.get("acted_at") and now - w["acted_at"] >= QUIET_STUCK):
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
        if quiet >= QUIET_NOTE and (asked or now - (w["noted"] or 0) >= QUIET_NOTE):
            w["since"] = w["since"] or sess.last_activity
            w["noted"] = now
            found["state"] = "quiet"
            self.hub.publish(slug, {"type": "watch", "text": self.still_working_text(sess, quiet, running, now)})
        return found

    def _auto_action(self, sess: "Session", now: float) -> str | None:
        """L27 (owner's rule: after 5 minutes without activity, wake or restart the sub-agent by itself). First wake
        Fred up; if nothing moved since, restart the step; if a restart did not help either, or after AUTO_HELP_MAX
        automatic steps in an hour, leave it to the user so the app never loops."""
        a = sess.auto_help
        a["times"] = [t for t in a["times"] if now - t < 3600]
        no_progress_since = lambda t: bool(t) and sess.last_activity <= t + 1
        if len(a["times"]) >= AUTO_HELP_MAX or no_progress_since(a["restart_at"]):
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
        return self.resume(slug, restart=True)

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

    def _priority(self, slug: str) -> int:
        return PRIORITY_ORDER.get((projects.load(slug) or {}).get("priority", "normal"), 1)

    def _start_next(self) -> None:
        """Start waiting turns, highest priority first, while slots are free."""
        with self._send_lock:
            limit = max(1, int(settings.load().get("max_parallel") or 2))
            self.waiting.sort(key=lambda w: (self._priority(w["slug"]), w["ts"]))
            starts = []
            for w in list(self.waiting):
                if self.running_count() + len(starts) >= limit:
                    break
                sess = self.session(w["slug"])
                if sess.busy or (projects.load(w["slug"]) or {}).get("paused"):
                    continue
                if any(s["slug"] == w["slug"] for s in starts):
                    continue
                self.waiting.remove(w)
                texts = [x["text"] for x in self.waiting if x["slug"] == w["slug"]]
                self.waiting = [x for x in self.waiting if x["slug"] != w["slug"]]
                sess.busy = True
                starts.append({"slug": w["slug"], "text": "\n\n".join([w["text"], *texts])})
        for s in starts:
            self.hub.publish(s["slug"], {"type": "project_list", "transient": True})
            self._start(s["slug"], s["text"], True)
        self.pipeline_continue()

    # ------------------------------------------------------------------ master plan (requests 19 and 20)
    def master_order(self) -> list[dict]:
        order = list(settings.load().get("pipeline_order") or [])
        ps = [p for p in projects.list_projects(include_hidden=False)]
        return sorted(ps, key=lambda p: (order.index(p["slug"]) if p["slug"] in order else len(order),
                                         PRIORITY_ORDER.get(p.get("priority", "normal"), 1), p.get("created", 0)))

    def _awaits_reply(self, slug: str) -> bool:
        """The lead's last message (after the user's last one) asks the user something."""
        for ev in reversed(self.hub.history(slug)):
            if ev.get("type") == "message":
                return ev.get("role") != "user" and str(ev.get("text", "")).rstrip().endswith("?")
        return False

    def next_work(self, slug: str, p: dict) -> str | None:
        """The hidden message that continues this project's approved plan, or None if it should not continue."""
        if not p.get("plan_approved") or p.get("paused") or p.get("hidden") or p.get("waiting_release") \
                or p.get("pipeline_hold"):
            return None
        its = iterations.load(slug)
        nxt = next((it for it in its if it.get("status") != "done"), None)
        if not nxt:
            return None
        if nxt["status"] == "building":
            return (f"[App] Continue iteration {nxt['n']} ({nxt['name']}) where you stopped: finish it, test it and "
                    "commit_iteration, then go on with the plan.")
        if not iterations.gate_open(slug, nxt["n"]):
            return None
        return (f"[App] The approved plan continues: start iteration {nxt['n']} ({nxt['name']}) now with "
                f"start_iteration({nxt['n']}), then build, test and commit it.")

    def pipeline_continue(self) -> list[str]:
        """Slots are free: continue the next approved iteration of any project, in master-plan order."""
        s = settings.load()
        if not s.get("pipeline_auto", True) or not auth.ready() or self.limit_timers:
            return []
        started = []
        with self._send_lock:
            limit = max(1, int(s.get("max_parallel") or 2))
            for p in self.master_order():
                if self.running_count() >= limit:
                    break
                slug = p["slug"]
                sess = self.sessions.get(slug)
                if (sess and (sess.busy or sess.background or sess.bg_reports)) or self.waiting_user(slug) \
                        or any(w["slug"] == slug for w in self.waiting):
                    continue
                text = self.next_work(slug, p)
                if not text or self._awaits_reply(slug):
                    continue
                n = next((it["n"] for it in iterations.load(slug) if it.get("status") != "done"), None)
                kick = p.get("pipeline_kick") or {}
                if kick.get("n") == n and time.time() - kick.get("t", 0) < PIPELINE_KICK_SECONDS:
                    continue
                projects.update(slug, pipeline_kick={"n": n, "t": time.time()})
                self.session(slug).busy = True
                started.append((slug, text))
        for slug, text in started:
            self.hub.publish(slug, {"type": "notice", "text": "Continuing the approved plan (master plan)."})
            self.hub.publish(slug, {"type": "project_list", "transient": True})
            self._start(slug, text, True)
        return [s for s, _ in started]

    def release(self, slug: str, n: int) -> str:
        it = iterations.release(slug, n)
        self.hub.publish(slug, {"type": "notice", "text": f"You released iteration {n}: {it['name']}."})
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        return self.send_message(slug, f"[App] The user released iteration {n} ({it['name']}). Start it now with "
                                       f"start_iteration({n}), then build, test and commit it.", hidden=True)

    def pipeline_overview(self) -> dict:
        rows = []
        for p in self.master_order():
            slug = p["slug"]
            its = iterations.load(slug)
            if not its:
                continue
            state = self.project_state(slug, p)
            sess = self.sessions.get(slug)
            busy = [r for r in (sess.team_snapshot() if sess else []) if r["status"] in ("working", "question")]
            items = []
            for it in its:
                st = it.get("status", "planned")
                if st == "done":
                    label = "done"
                elif not p.get("plan_approved"):
                    label = "not approved yet"
                elif st == "building":
                    label = "waiting for your answer" if self.waiting_user(slug) else (
                        "building" if state == "working" else "stopped mid-way")
                elif p.get("waiting_release") == it["n"]:
                    label = "waiting for release"
                else:
                    label = "queued"
                items.append({"n": it["n"], "name": it["name"], "status": label, "after": it.get("after", "continue"),
                              "commit": it.get("commit", ""), "requirements": it.get("requirements") or [],
                              "who": [{"name": r["name"], "task": r.get("task", "")} for r in busy] if st == "building" else []})
            rows.append({"slug": slug, "name": p["name"], "state": state, "priority": p.get("priority", "normal"),
                         "approved": bool(p.get("plan_approved")), "waiting_release": p.get("waiting_release"),
                         "launch": launch_info(slug, p), "iterations": items})
        return {"projects": rows, "running": self.running_count(),
                "max_parallel": int(settings.load().get("max_parallel") or 2),
                "auto": bool(settings.load().get("pipeline_auto", True))}

    # ------------------------------------------------------------------ pause / resume / priority
    def pause(self, slug: str) -> None:
        projects.update(slug, paused=True)
        self.cancel_auto_continue(slug, note="paused")
        with self._send_lock:
            held = [w["text"] for w in self.waiting if w["slug"] == slug]
            self.waiting = [w for w in self.waiting if w["slug"] != slug]
            if held:
                self.queued.setdefault(slug, []).extend(held)
        self._interrupt(slug)
        self.hub.publish(slug, {"type": "notice", "text": "Paused. Resume it from the Work queue or the header."})
        self.hub.publish(slug, {"type": "project_list", "transient": True})

    def resume(self, slug: str, restart: bool = False) -> str:
        projects.update(slug, paused=False, pipeline_hold=False)
        with self._send_lock:
            held = self.queued.pop(slug, [])
        note = ("[App] The user restarted the work: start the current step again from the beginning."
                if restart else "[App] The user resumed the work. Continue exactly where you stopped.")
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        return self.send_message(slug, "\n\n".join([note, *held]), hidden=True)

    def set_priority(self, slug: str, priority: str) -> None:
        if priority not in PRIORITY_ORDER:
            raise ValueError("priority must be high, normal or low")
        projects.update(slug, priority=priority)
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        self._start_next()

    # ------------------------------------------------------------------ plan limit reached (request F)
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
        handle = self.loop.call_later(LIMIT_CHECK_SECONDS, check)
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
        return self.send_message(slug, CONTINUE_TEXT, hidden=True)

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

    # ------------------------------------------------------------------ overview for the Work queue screen
    def project_state(self, slug: str, project: dict | None = None) -> str:
        """working | needs you | paused | limit | waiting | idle (nothing is progressing)."""
        p = project if project is not None else (projects.load(slug) or {})
        sess = self.sessions.get(slug)
        if p.get("paused"):
            return "paused"
        if slug in self.limit_timers:
            return "limit"
        if any(s == slug for (_f, s, _k) in list(self.pending.values())):
            return "needs you"
        if sess and (sess.busy or sess.background):
            return "working"
        if any(w["slug"] == slug for w in self.waiting):
            return "waiting"
        if p.get("waiting_release"):
            return "release"
        return "idle"

    def work_overview(self) -> list[dict]:
        rows = []
        for p in projects.list_projects(include_hidden=False):
            slug = p["slug"]
            sess = self.sessions.get(slug)
            pending = [k for (_f, s, k) in self.pending.values() if s == slug]
            state = self.project_state(slug, p)
            team = sess.team_snapshot() if sess else []
            busy_roles = [r for r in team if r["status"] in ("working", "question")]
            timer = self.limit_timers.get(slug)
            prog = iterations.progress(slug)
            rows.append({"slug": slug, "name": p["name"], "phase": p.get("phase", ""), "state": state, "progress": prog,
                         "priority": p.get("priority", "normal"), "since": sess.turn_started if sess else None,
                         "who": [{"name": r["name"], "status": r["status"], "task": r.get("task", ""),
                                  "last": r.get("last", "")} for r in busy_roles],
                         "pending": pending, "queued": len(self.queued.get(slug, [])),
                         "continue_at": timer[2] if timer and timer[2] else None})
        order = {"needs you": 0, "working": 1, "limit": 2, "waiting": 3, "release": 4, "paused": 5, "idle": 6}
        return sorted(rows, key=lambda r: (order[r["state"]], PRIORITY_ORDER.get(r["priority"], 1)))

    # ------------------------------------------------------------------ 0.8.0 helpers
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
