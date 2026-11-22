"""Buttons of the sign-in and Settings screens: Claude connection, settings, secrets, voice, phone approvals, lessons.

Each function handles its own paths for the local web server (app/server.py) and returns NO_ROUTE for any other
path (L46). `self` is the request handler, `app` the running App."""
from __future__ import annotations

import re
import subprocess
import threading
import time

from . import auth, engine as engine_mod, projects, secrets_store, settings, spelling, voice, winhelpers
from .server_common import NO_ROUTE


def post_signin(self, app, path: str, body: dict):
    """Sign-in: API key and Claude subscription."""
    if path == "/api/onboarding/key":
        key = str(body.get("key", "")).strip()
        if not key.startswith("sk-ant-"):
            return self._error(400, "That does not look like an Anthropic API key (it starts with sk-ant-).")
        ok, msg = engine_mod.validate_anthropic_key(key)
        if not ok:
            return self._error(400, msg)
        secrets_store.set_secret("anthropic_api_key", key)
        settings.save({"auth_mode": "api_key"})
        self._kickoff_setup()
        app.engine.on_signed_in()   # L37
        return self._json({"ok": True})
    if path == "/api/auth/subscription/start":
        try:
            pid = auth.start_subscription_login()
        except RuntimeError as e:
            return self._error(500, str(e))
        return self._json({"ok": True, "pid": pid})
    if path == "/api/auth/subscription/check":
        st = auth.subscription_status()
        settings.save({"subscription_signed_in": st["signed_in"]})
        if st["signed_in"]:
            settings.save({"auth_mode": "subscription"})
            self._kickoff_setup()
            app.engine.on_signed_in()   # L37: work that stopped for the sign-in continues
        return self._json(st)
    if path == "/api/auth/subscription/logout":
        auth.subscription_logout()
        settings.save({"subscription_signed_in": False})
        if auth.mode() == "subscription":
            settings.save({"auth_mode": "api_key" if secrets_store.has("anthropic_api_key") else ""})
        return self._json(app.state())
    return NO_ROUTE


def post_settings(self, app, path: str, body: dict):
    """Settings, secrets, voice, phone approvals, lessons and folder picking."""
    if path == "/api/spell":   # L38: spelling help for the message box
        return self._json({"issues": spelling.check(str(body.get("text") or "")), "languages": spelling.languages()})
    if path == "/api/spell/add":
        try:
            spelling.add_word(str(body.get("word") or ""))
        except ValueError as e:
            return self._error(400, str(e))
        return self._json({"ok": True})
    if path == "/api/clipboard/read":   # L28: right-click > Paste in a text box
        try:
            return self._json({"text": winhelpers.read_clipboard()})
        except (OSError, subprocess.SubprocessError):
            return self._error(500, "Press Ctrl+V to paste.")
    if path == "/api/settings":
        changes = {}
        if body.get("approval_mode") in ("plan", "strict"):
            changes["approval_mode"] = body["approval_mode"]
        if isinstance(body.get("max_parallel"), int) and 1 <= body["max_parallel"] <= 5:
            changes["max_parallel"] = body["max_parallel"]
        for k in ("iteration_pause", "pipeline_auto", "close_to_tray", "scorecard_online", "flash_taskbar"):
            if isinstance(body.get(k), bool):
                changes[k] = body[k]
        if isinstance(body.get("spell_check"), bool):   # L38
            changes["spell_check"] = body["spell_check"]
        if isinstance(body.get("spell_languages"), list):
            changes["spell_languages"] = [x for x in body["spell_languages"] if x in spelling.LANGUAGES]
        if isinstance(body.get("user_name"), str):   # L12: changed in Settings > General
            changes["user_name"] = " ".join(body["user_name"].split())[:40]
        settings.save(changes)
        return self._json(app.state())
    if path == "/api/secrets/clear":
        name = body.get("name")
        if name in secrets_store.KNOWN:
            secrets_store.delete_secret(name)
            if name == "github_token":
                settings.save({"github_login": ""})
        return self._json(app.state())
    if path == "/api/setup/restart":
        settings.save({"setup_complete": False})
        app.engine.send_message(projects.SETUP_SLUG, "[App] The user reopened setup to change something. "
                                "Ask what they would like to change.", hidden=True)
        return self._json({"ok": True})
    if path == "/api/voice/start":
        try:
            return self._json({"state": app.voice_start(str(body.get("slug", "")))})
        except ValueError as e:
            return self._error(409 if str(e) == "needs-download" else 400, str(e))
    if path == "/api/voice/stop":
        app.voice_stop(bool(body.get("cancel")))
        return self._json({"ok": True})
    m = re.fullmatch(r"/api/telegram/(setup|test|disconnect|settings)", path)
    if m:   # L17: phone approvals, from Settings > Setup & connections
        action = m.group(1)
        if action == "setup":
            app.engine.send_message(projects.SETUP_SLUG, "[App] The user opened Settings and wants to set up "
                                    "approvals from the phone (Telegram). Do only that step (3b of the setup), "
                                    "starting with the explanation and asking permission.", hidden=True)
        elif action == "test":
            if not app.telegram.connected():
                return self._error(400, "No phone is linked yet.")
            if not app.telegram.send("👋 Test from AI Team Lite: approvals and alerts will arrive here."):
                return self._error(400, "The message could not be sent. Check the internet connection, "
                                        "or link the phone again.")
        elif action == "disconnect":
            app.telegram.disconnect()
        else:
            settings.save({"telegram_high_risk_on_phone": bool(body.get("high_risk_on_phone"))})
        return self._json(app.state())
    if path == "/api/lessons/delete":   # L23: the user removes a lesson
        from . import lessons
        lessons.delete(str(body.get("id", "")))
        return self._json({"items": sorted(lessons.load(), key=lambda i: -i.get("time", 0))})
    if path == "/api/voice/test":   # L11: "Test microphone" with a live level bar
        slug = str(body.get("slug") or projects.SETUP_SLUG)
        if not voice.packages_available():
            return self._error(400, "Voice is not available in this installation (a part is missing). "
                                    "Reinstall AI Team Lite.")
        if app.listener is not None:
            return self._error(409, "Stop listening first, then test the microphone.")

        def run_test():
            last = {"t": 0.0}

            def level(v):
                if time.time() - last["t"] >= 0.1:
                    last["t"] = time.time()
                    app.hub.publish(slug, {"type": "voice", "state": "test_level", "level": round(v, 3),
                                           "transient": True})
            res = voice.test_microphone(level)
            app.hub.publish(slug, {"type": "voice", "state": "test_done", **res, "transient": True})
        threading.Thread(target=run_test, name="mic-test", daemon=True).start()
        return self._json({"ok": True, "device": voice.device_name()})
    if path == "/api/voice/open-privacy":
        winhelpers.open_url(voice.PRIVACY_URL)
        return self._json({"ok": True})
    if path == "/api/voice/download":
        app.voice_download(str(body.get("slug", "")))
        return self._json({"ok": True})
    if path == "/api/pick-folder":
        chosen = app.bridge.pick_folder(body.get("start", "")) if app.bridge else None
        return self._json({"path": chosen})
    return NO_ROUTE
