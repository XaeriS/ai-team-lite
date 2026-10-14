"""A stand-in for ClaudeSDKClient that plays scripted turns, including real calls into the app's
permission callback and custom tools, so the whole app can be tested without an API key."""
from __future__ import annotations

import asyncio
import itertools

from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

_ids = itertools.count(1)


def say(text, parent=None):
    return AssistantMessage(content=[TextBlock(text)], model="claude-sonnet-5-5", parent_tool_use_id=parent,
                            usage={"input_tokens": 1200, "output_tokens": 300}, message_id=f"m{next(_ids)}")


class FakeClient:
    """script(turn_index, prompt, client) -> async generator of SDK messages."""

    instances: list["FakeClient"] = []

    def __init__(self, options, script):
        self.options = options
        self.script = script
        self.prompts: list[str] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.connected = False
        self.interrupted = False
        self.permission_log: list[tuple[str, str]] = []
        FakeClient.instances.append(self)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def interrupt(self):
        self.interrupted = True

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_response(self):
        yield SystemMessage("init", {"session_id": "sess-1"})
        async for m in self.script(len(self.prompts) - 1, self.prompts[-1], self):
            yield m
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                            session_id="sess-1", total_cost_usd=0.01)

    # ---- helpers for scripts --------------------------------------------------------------
    async def use_tool(self, name, inp, parent=None):
        """Ask permission like Claude Code does, then run the tool (custom tools really execute).
        Returns (allowed, result_text, messages_to_yield)."""
        tid = f"tu{next(_ids)}"
        # Like the real engine: the tool-use message arrives BEFORE the permission request
        session = self.options.can_use_tool.__self__
        session.handle(AssistantMessage(content=[ToolUseBlock(tid, name, inp)], model="claude-sonnet-5-5", parent_tool_use_id=parent,
                                        usage={"input_tokens": 2000, "output_tokens": 400}, message_id=f"m{tid}"))
        msgs = []
        ctx = type("Ctx", (), {"agent_id": "a1" if parent else None, "title": None, "description": None})()
        decision = await self.options.can_use_tool(name, inp, ctx)
        allowed = isinstance(decision, PermissionResultAllow)
        self.permission_log.append((name, "allow" if allowed else "deny"))
        result = getattr(decision, "message", "")
        if allowed and name.startswith("mcp__aiteam__"):
            out = await session.tool_handlers[name.removeprefix("mcp__aiteam__")](inp)
            result = out["content"][0]["text"]
        elif allowed:
            result = "ok"
        msgs.append(UserMessage(content=[ToolResultBlock(tid, result, is_error=not allowed)]))
        return allowed, result, msgs, tid
