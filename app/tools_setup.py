"""Tools for setting things up with the user: the PC's environment, settings, secrets, folders, choices, GitHub, the phone.

make(session) returns this group's tools by name; app/tools.py puts the groups together (L46)."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import re
import time
from typing import TYPE_CHECKING
import uuid

from claude_agent_sdk import tool

from . import github, models, paths, projects, secrets_store, settings, winhelpers

if TYPE_CHECKING:
    from .session import Session

from .tools_common import interrupted, text


def make(session: "Session") -> dict:
    slug = session.slug

    @tool("check_environment", "Check what is installed and configured on this PC (no changes).", {})
    async def check_environment(args):
        s = settings.load()
        info = {
            "os": f"{platform.system()} {platform.release()}",
            "python_for_projects": str(paths.bundled_python()),
            "git": paths.git_exe() or "missing",
            "browser_for_screenshots": winhelpers.find_headless_browser() or "missing",
            "workspace_dir": str(settings.workspace()),
            "claude_connection": "Claude subscription (signed in)" if s.get("auth_mode") == "subscription"
            else ("API key saved" if secrets_store.has("anthropic_api_key") else "missing"),
            "github": f"connected as {s['github_login']}" if s["github_login"] and secrets_store.has("github_token")
            else "not connected",
            "approval_mode": s["approval_mode"],
            "claude_models": ", ".join(v["label"] for v in models.CLAUDE.values()),
            "model_choice": "Fred chooses per step",
        }
        return text(json.dumps(info, indent=2))

    @tool("save_setting", "Save a user setting. key: user_name | workspace_dir | approval_mode (plan|strict).",
          {"key": str, "value": str})
    async def save_setting(args):
        key, value = args["key"], str(args["value"]).strip()
        if key not in settings.AGENT_EDITABLE:
            return text(f"'{key}' cannot be changed from the chat.", True)
        if key == "approval_mode" and value not in ("plan", "strict"):
            return text("approval_mode must be 'plan' or 'strict'.", True)
        if key == "workspace_dir":
            p = Path(value).expanduser()
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return text(f"Cannot use that folder: {e}", True)
            value = str(p)
        settings.save({key: value})
        session.hub.publish(slug, {"type": "project_list", "transient": True})
        return text(f"Saved {key} = {value}")

    @tool("request_secret", "Show the user a secure box to paste a secret. It is saved in Windows Credential "
          "Manager; you never see it. name: github_token | anthropic_api_key | telegram_bot_token.",
          {"name": str, "title": str, "explanation": str, "help_url": str})
    async def request_secret(args):
        name = args["name"]
        if name not in secrets_store.KNOWN:
            return text(f"Unknown secret '{name}'.", True)
        answer = await session.ask_user("secret", {
            "title": args.get("title") or f"Paste your {secrets_store.KNOWN[name]}",
            "explanation": args.get("explanation", ""), "help_url": args.get("help_url", ""), "name": name})
        if interrupted(answer):
            return interrupted(answer)
        if answer.get("cancelled") or not answer.get("value"):
            return text("The user did not provide it.")
        secrets_store.set_secret(name, answer["value"].strip())
        return text(f"{secrets_store.KNOWN[name]} saved securely.")

    @tool("ask_folder", "Let the user choose a folder on their PC. Returns the chosen path.",
          {"title": str, "explanation": str, "suggested_path": str})
    async def ask_folder(args):
        answer = await session.ask_user("folder", {"title": args.get("title", "Choose a folder"),
                                                   "explanation": args.get("explanation", ""),
                                                   "suggested": args.get("suggested_path") or str(paths.default_workspace())})
        if interrupted(answer):
            return interrupted(answer)
        if answer.get("cancelled") or not answer.get("value"):
            return text("The user did not choose a folder.")
        return text(f"The user chose: {answer['value']}")

    @tool("ask_choice", "Show the user clickable options (they can also type their own answer).",
          {"question": str, "options": list})
    async def ask_choice(args):
        opts = [str(o) for o in (args.get("options") or [])][:5]
        answer = await session.ask_user("choice", {"title": args.get("question", ""), "options": opts})
        if interrupted(answer):
            return interrupted(answer)
        return text(f"The user answered: {answer.get('value') or '(no answer)'}")

    @tool("github_check", "Check that the saved GitHub token works and which account it belongs to.", {})
    async def github_check(args):
        try:
            info = github.check()
        except github.GitHubError as e:
            return text(str(e), True)
        settings.save({"github_login": info["login"]})
        note = "" if "repo" in info["scopes"] or not info["scopes"] else \
            f" Warning: the token's scopes are '{info['scopes']}', 'repo' is needed to create repositories."
        return text(f"GitHub connected as {info['login']}.{note}")

    @tool("github_create_repo", "Create a GitHub repository for this project (asks the user first).",
          {"name": str, "private": bool, "description": str, "explanation": str})
    async def github_create_repo(args):
        try:
            repo = github.create_repo(re.sub(r"[^A-Za-z0-9._-]", "-", args["name"])[:90],
                                      bool(args.get("private", True)), args.get("description", ""))
        except github.GitHubError as e:
            return text(str(e), True)
        projects.update(slug, github_repo=repo["url"], github_clone=repo["clone_url"])
        return text(f"Repository created: {repo['url']}")

    @tool("github_push", "Save the project's current files to its GitHub repository (asks the user first).",
          {"message": str, "explanation": str})
    async def github_push(args):
        p = projects.load(slug) or {}
        if not p.get("github_clone"):
            return text("This project has no GitHub repository yet. Use github_create_repo first.", True)
        try:
            last = github.push(projects.project_dir(slug), args.get("message", "Update"), p["github_clone"])
        except github.GitHubError as e:
            return text(f"Push failed: {e}", True)
        return text(f"Pushed to {p['github_repo']} (latest commit: {last}).")

    @tool("finish_setup", "Mark the first-time setup as complete.", {"summary": str})
    async def finish_setup(args):
        settings.save({"setup_complete": True})
        session.hub.publish(slug, {"type": "setup_done", "summary": args.get("summary", "")})
        session.hub.publish(slug, {"type": "project_list", "transient": True})
        return text("Setup is complete.")

    @tool("connect_phone", "Setup: after the user agreed and pasted their Telegram bot key (request_secret "
          "name=telegram_bot_token), link their phone: shows a code the user sends to their bot, waits until it "
          "arrives (up to 10 minutes) and sends a test message. Returns whether it worked.", {})
    async def connect_phone(args):
        bridge = session.engine.telegram
        if bridge is None:
            return text("Phone approvals are not available in this installation.", True)
        if not bridge.token():
            return text("No bot key yet: ask for it first with request_secret(name=\"telegram_bot_token\").", True)
        from . import telegram
        try:
            info = await session.run_blocking(bridge.begin_link)
        except telegram.TelegramError as e:
            return text(f"Could not use the bot key: {e} Ask the user to copy it again from @BotFather.", True)
        card = uuid.uuid4().hex[:12]
        session.hub.publish(slug, {"type": "telegram", "id": card, "state": "linking", **info})
        end = time.time() + telegram.LINK_SECONDS
        while time.time() < end and not bridge.chat_id():
            await session.sleep(2)
        if not bridge.chat_id():
            session.hub.update(slug, card, state="expired")
            return text("The code did not arrive within 10 minutes. Offer to try again (call connect_phone again) or "
                        "to do it later from Settings > Setup & connections.")
        session.hub.update(slug, card, state="linked")
        settings.save({"telegram_high_risk_on_phone": False})
        return text(f"Linked: the user's phone now gets approvals and alerts from @{info['bot']} (a confirmation "
                    "message was sent). High-risk approvals stay at the PC unless they change it in Settings.")

    return {"check_environment": check_environment, "save_setting": save_setting, "request_secret": request_secret, "ask_folder": ask_folder, "ask_choice": ask_choice, "github_check": github_check, "github_create_repo": github_create_repo, "github_push": github_push, "finish_setup": finish_setup, "connect_phone": connect_phone}
