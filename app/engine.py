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

from . import (auth, brains, challenge, iterations, journal, languages, models, paths, policy, projects, prompts, risk,
               secrets_store, settings, skills as skills_mod, tooling, tracking, tools, usage)
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
                                            "record_change_request", "run_security_test")}
WORKER_LABELS = {"developer": "Developer", "tester": "Tester", "reviewer": "Reviewer", "scout": "Scout",
                 "challenger": "Challenger", "diagrammer": "Diagrammer"}
ROLES = ["Fred", "Mat", "Developer", "Tester", "Reviewer"]
LEADS = {"Fred": "lead", "Mat": "architect"}
STALL_SECONDS = 300
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
    if name == "TaskOutput":
        return "waiting for a worker's report"
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
        self.pending_kickoff: str | None = None  # hidden message for the next speaker after a handoff
        self.background: dict[str, dict] = {}    # worker started in the background anyway: agent id -> card
        self.waiting_bg: dict[str, str] = {}     # TaskOutput tool use id -> agent id

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
        names = list(ROLES) + [brains.label(b) for b in brains.all_brains() if b.get("enabled")]
        names += [n for n in self.team if n not in names]
        out = []
        for name in names:
            r = dict(self.team.get(name) or {"status": "idle", "task": "", "last": "", "last_time": 0, "recent": []})
            if r["status"] == "working" and r.get("last_time") and now - r["last_time"] > STALL_SECONDS \
                    and now - r.get("since", now) > STALL_SECONDS:
                r["stalled"] = True
            r["name"] = name
            r["kind"] = LEADS.get(name) or ("worker" if name in ROLES else "brain")
            out.append(r)
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
        # while this project waits for the user, its working slot goes to the next work in the master plan
        threading.Thread(target=self.engine._start_next, daemon=True).start()
        try:
            return await fut
        except asyncio.CancelledError:
            self.hub.update(self.slug, item_id, status="cancelled")
            return {"cancelled": True}
        finally:
            self.engine.pending.pop(item_id, None)

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
        prev = (self.team.get(who) or {}).get("status", "idle")
        self.set_role(who, status="question", last=f"waiting for your approval: {card['title'].lower()}")
        answer = await self.ask_user("approval", card)
        self.set_role(who, status=prev if prev != "question" else "working")
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
                + skills_mod.note_for("Fred", project, self.folder)
            agents = {
                "developer": AgentDefinition(description="Builds and fixes the software from Fred's brief.",
                                             prompt=prompts.render(prompts.DEVELOPER, conventions=conv)
                                             + skills_mod.note_for("Developer", project, self.folder),
                                             model=models.for_role("Developer")),
                "tester": AgentDefinition(description="Writes and runs tests and takes screenshots as proof.",
                                          prompt=prompts.render(prompts.TESTER, conventions=conv)
                                          + skills_mod.note_for("Tester", project, self.folder),
                                          model=models.for_role("Tester")),
                "reviewer": AgentDefinition(description="Reviews the work against the requirements; read-only.",
                                            prompt=prompts.render(prompts.REVIEWER)
                                            + skills_mod.note_for("Reviewer", project, self.folder),
                                            model=models.for_role("Reviewer"),
                                            tools=["Read", "Glob", "Grep", "Write", "Bash", "PowerShell"]),
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
        async with self.lock:
            self.busy = True
            self.limit_hit = None
            self.turn_started = time.time()
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
                if hidden and settings.load().get("cap_context", True) and any(m in text for m in FRESH_MARKERS):
                    await self._reset_context()   # start this iteration with a fresh, small context (item 48)
                    text = self._briefing() + "\n\n" + text
                notes = self.engine.notes.pop(self.slug, [])
                if auth.mode() == "subscription":
                    note = await asyncio.to_thread(usage.warning_note)  # may refresh the real figures (~2 s)
                    if note:
                        notes.append(note)
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
                self.turn_started = None
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
                more = bool(self.engine.queued.get(self.slug)) or bool(self.pending_kickoff)
                if not more:
                    self.hub.publish(self.slug, {"type": "busy", "value": False})
                self.hub.publish(self.slug, {"type": "docs_changed", "transient": True})

    # ------------------------------------------------------------------ safety net: a worker in the background
    def _went_background(self, block: Any, receipt: str) -> None:
        """The engine started a worker in the background and returned only a receipt. Keep the worker shown as
        working, hide the receipt, and make the lead wait for the real report right after this turn."""
        label = self.agents.pop(block.tool_use_id)
        m = AGENT_ID.search(receipt)
        aid = m.group(1) if m else ""
        self.background[aid or block.tool_use_id] = {"card": block.tool_use_id, "label": label}
        self.hub.update(self.slug, block.tool_use_id, status="working", background=True)
        self.set_role(label, status="working", last="working in the background")
        wait = (f"call TaskOutput with task_id \"{aid}\" and block=true (repeat until it has finished)" if aid
                else "wait for its report")
        self.pending_kickoff = (f"[App] The {label} was started in the background, which AI Team Lite does not support: the "
                                f"user only sees work that you wait for. Do nothing else first: {wait}, then continue "
                                "the iteration with its report. Never start workers in the background "
                                "(always run_in_background=false).")

    def _background_report(self, block: Any) -> None:
        aid = self.waiting_bg.pop(block.tool_use_id)
        text = _result_text(block)
        if not block.is_error and re.search(r"\b(running|in progress|not (yet )?(finished|complete))\b", text[:400], re.I):
            return  # still working; the lead asks again
        info = self.background.pop(aid, None)
        if not info:
            return
        done_task = (self.team.get(info["label"]) or {}).get("task", "")
        self.set_role(info["label"], status="idle", task=f"Finished: {done_task}" if done_task else "",
                      last="reported back to Fred" if not block.is_error else "stopped with an error")
        self.hub.update(self.slug, info["card"], status="failed" if block.is_error else "done", background=False,
                        summary=text[:1200])
        self.hub.publish(self.slug, {"type": "docs_changed", "transient": True})

    def handle(self, msg: Any) -> None:
        if isinstance(msg, SystemMessage):
            sid = (msg.data or {}).get("session_id")
            if msg.subtype == "init" and sid:
                projects.update(self.slug, session_id=sid)
            return
        if isinstance(msg, AssistantMessage):
            if getattr(msg, "error", None):  # the engine's own error text; the ResultMessage explains it
                return
            worker = self.agents.get(msg.parent_tool_use_id or "")
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
                        self.set_role(label, status="working", task=block.input.get("description", "") or "Working",
                                      last="received a brief from Fred")
                        self.set_role("Fred", status="working", task=f"Supervising the {label}")
                        self.hub.publish(self.slug, {"type": "agent", "id": block.id, "name": label,
                                                     "status": "working",
                                                     "description": block.input.get("description", "")})
                    elif block.name in QUIET_TOOLS:
                        pass
                    elif block.name not in CARD_TOOLS:
                        if block.name == "TaskOutput":
                            aid = str((block.input or {}).get("task_id") or (block.input or {}).get("agentId") or "")
                            if aid in self.background:
                                self.waiting_bg[block.id] = aid
                        line = describe_tool(block.name, block.input or {}, self.folder)
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
                if block.tool_use_id in self.waiting_bg:
                    self._background_report(block)
                    continue
                if block.tool_use_id in self.agents:
                    summary = _result_text(block)
                    if not block.is_error and ASYNC_RECEIPT.search(summary):
                        self._went_background(block, summary)
                        continue
                    label = self.agents.pop(block.tool_use_id)
                    done_task = (self.team.get(label) or {}).get("task", "")
                    self.set_role(label, status="idle", task=f"Finished: {done_task}" if done_task else "",
                                  last="reported back to Fred" if not block.is_error else "stopped with an error")
                    self.set_role("Fred", status="working", task="Reading the report")
                    self.hub.update(self.slug, block.tool_use_id, status="failed" if block.is_error else "done",
                                    summary=summary[:1200])
                    self.hub.publish(self.slug, {"type": "docs_changed", "transient": True})
                    _ = label
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
                        five = usage.plan().get("five_hour") or {}
                        self.limit_hit = {"type": "five_hour", "label": "Current session (5 hours)",
                                          "resets_at": five.get("resets_at"), "detail": str(detail)[:300]}
                    return  # the limit card explains it (request F)
                self.hub.publish(self.slug, {"type": "error", "text": str(detail)[:800]})


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
        self._send_lock = threading.RLock()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="agent-loop", daemon=True)
        self.thread.start()
        self.loop.call_soon_threadsafe(self._schedule_tick)

    def _schedule_tick(self) -> None:
        def tick():
            threading.Thread(target=self._start_next, daemon=True).start()
            self._schedule_tick()
        self.loop.call_later(PIPELINE_TICK, tick)

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
                if (sess and sess.busy) or self.waiting_user(slug) or any(w["slug"] == slug for w in self.waiting):
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
                                "continue_at": when, "auto": when is not None, "label": entry.get("label", ""),
                                "detail": entry.get("detail", "")})
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        if when is not None:
            def schedule():
                handle = self.loop.call_later(max(1.0, when - time.time()),
                                              lambda: self.limit_continue(slug, auto=True))
                self.limit_timers[slug] = (item_id, handle, when)
            self.loop.call_soon_threadsafe(schedule)
        else:
            self.limit_timers[slug] = (item_id, None, 0)

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
        return self.send_message(slug, CONTINUE_TEXT, hidden=True)

    def cancel_auto_continue(self, slug: str, note: str = "") -> None:
        entry = self.limit_timers.get(slug)
        if not entry:
            return
        item_id, handle, _ = entry
        if handle:
            self.loop.call_soon_threadsafe(handle.cancel)
        self.limit_timers[slug] = (item_id, None, 0)
        self.hub.update(slug, item_id, auto=False, continue_at=None,
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
            if sess and sess.client is not None:
                self.loop.create_task(sess.client.interrupt())

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
