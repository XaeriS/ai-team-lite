"""Buttons that steer the work: pause and resume, messages, interrupted projects, answers to cards, the council.

Each function handles its own paths for the local web server (app/server.py) and returns NO_ROUTE for any other
path (L46). `self` is the request handler, `app` the running App."""
from __future__ import annotations

import base64
from pathlib import Path
import re
import threading

from . import auth, brains, journal, projects, secrets_store, tooling, usage, winhelpers
from .server_common import ADOPTED_KICKOFF, EXISTING_KICKOFF, MAX_UPLOAD, NO_ROUTE, _ollama_get_and_add, _scan_text


def post_work(self, app, path: str, body: dict):
    """Work control: interrupted and broken projects, pause/resume, messages, usage refresh."""
    m = re.fullmatch(r"/api/interrupted/([a-z0-9-]+)/(resume|restart|dismiss)", path)
    if m:
        slug, action = m.groups()
        if not projects.load(slug):
            journal.resolve(slug)
            return self._json({"ok": True})
        folder = projects.project_dir(slug)
        if action == "restart":
            try:
                name = journal.safety_stash(folder)
                app.hub.publish(slug, {"type": "notice", "text": f"Your unfinished changes were set aside ({name}); "
                                       "recover them with Git stash if needed."})
            except Exception as e:  # noqa: BLE001
                return self._error(400, str(e))
        journal.resolve(slug)
        if action in ("resume", "restart"):
            note = ("[App] The app was interrupted and restarted. Start the current step again from the last "
                    "commit." if action == "restart" else
                    "[App] The app was interrupted and reopened. Check the project files for half-finished "
                    "changes, re-run the tests, then continue where you stopped.")
            app.engine.send_message(slug, note, hidden=True)
        return self._json({"ok": True})
    if path == "/api/usage/refresh":
        # the real session / week figures from Claude's own usage command (item 69); no tokens used
        got = usage.fetch_plan(force=bool(body.get("manual"))) if auth.mode() == "subscription" else None
        return self._json({"ok": bool(got), "plan": usage.plan() if auth.mode() == "subscription" else {},
                           "source": "claude" if got else "reports"})
    m = re.fullmatch(r"/api/broken/([a-z0-9-]+)/(remove|reload)", path)
    if m:
        slug, action = m.groups()
        try:
            if action == "remove":
                projects.forget_broken(slug)
                app.hub.publish(slug, {"type": "project_list", "transient": True})
                return self._json({"ok": True, "message": "Removed from AI Team Lite. Nothing on your disk was changed."})
            p = projects.reload_broken(slug, str(body.get("folder") or ""))
        except ValueError as e:
            return self._error(400, str(e))
        kickoff = (ADOPTED_KICKOFF if p.get("adopted") else EXISTING_KICKOFF).replace("{name}", p["name"]) \
            .replace("{folder}", p.get("source") or str(projects.project_dir(p["slug"]))) \
            .replace("{scan}", _scan_text(p.get("scan") or {})).replace("{diagram}", "")
        app.engine.send_message(p["slug"], kickoff, hidden=True)
        return self._json(p)
    m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/reload", path)
    if m:
        slug = m.group(1)
        p = projects.load(slug)
        if not p or slug == projects.SETUP_SLUG:
            raise KeyError(slug)
        if app.engine.project_state(slug, p) in ("working", "needs you", "waiting"):
            return self._error(409, "The team is working on this project. Pause it first, then reload it.")
        folder = projects.project_dir(slug)
        p = projects.update(slug, scan=projects.scan_folder(folder))
        kickoff = ADOPTED_KICKOFF.replace("{name}", p["name"]).replace("{folder}", str(folder)) \
            .replace("{scan}", _scan_text(p["scan"]))
        app.engine.send_message(slug, kickoff, hidden=True)
        return self._json({"ok": True})
    m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/(hide|unhide|delete)", path)
    if m:
        slug, action = m.groups()
        p = projects.load(slug)
        if not p or slug == projects.SETUP_SLUG:
            raise KeyError(slug)
        if action in ("hide", "unhide"):
            projects.set_hidden(slug, action == "hide")
            app.hub.publish(slug, {"type": "project_list", "transient": True})
            return self._json({"ok": True})
        mode = str(body.get("mode", ""))
        if app.engine.project_state(slug, p) in ("working", "needs you", "waiting"):
            return self._error(409, "The team is working on this project. Stop or pause it first, then delete it.")
        if mode == "all" and str(body.get("confirm", "")).strip() != p["name"]:
            return self._error(400, f"Type the project name ({p['name']}) to confirm.")
        app.engine.forget(slug)
        message = projects.delete(slug, mode)
        app.hub.publish(slug, {"type": "project_list", "transient": True})
        return self._json({"ok": True, "message": message})
    m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/(pause|resume|restart|priority|limit-continue|"
                     r"limit-cancel|open-reports|wake|restart-step|watch-check)", path)
    if m:
        slug, action = m.groups()
        if not projects.load(slug):
            raise KeyError(slug)
        if action == "pause":
            app.engine.pause(slug)
        elif action in ("resume", "restart"):
            return self._json({"result": app.engine.resume(slug, restart=action == "restart")})
        elif action == "priority":
            app.engine.set_priority(slug, str(body.get("priority", "")))
        elif action == "limit-continue":
            return self._json({"result": app.engine.limit_continue(slug)})
        elif action == "limit-cancel":
            app.engine.cancel_auto_continue(slug)
        elif action == "wake":   # L8: "Wake Fred up"
            return self._json({"result": app.engine.wake(slug)})
        elif action == "restart-step":   # L8: "Stop and restart this step"
            return self._json({"result": app.engine.restart_step(slug)})
        elif action == "watch-check":   # L8: the team graph shows "no activity": check now
            found = app.engine.watch_check(slug, asked=True)
            return self._json({"state": found.get("state"), "quiet": found.get("quiet", 0)})
        elif action == "open-reports":
            d = projects.project_dir(slug) / "reports"
            d.mkdir(exist_ok=True)
            winhelpers.open_folder(d)
        return self._json({"ok": True})
    m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/(message|stop|attach|open-folder)", path)
    if m:
        slug, action = m.groups()
        if not projects.load(slug):
            raise KeyError(slug)
        if action == "message":
            text = str(body.get("text", "")).strip()
            if not text:
                return self._error(400, "Empty message")
            return self._json({"ok": True, "result": app.engine.send_message(slug, text)})
        if action == "stop":
            app.engine.stop(slug)
            return self._json({"ok": True})
        if action == "open-folder":
            winhelpers.open_folder(projects.project_dir(slug))
            return self._json({"ok": True})
        if action == "attach":
            name = re.sub(r"[^\w.\- ]", "_", Path(str(body.get("name", "file"))).name)[:80] or "file"
            data = base64.b64decode(body.get("data_b64", ""), validate=True)
            if len(data) > MAX_UPLOAD:
                return self._error(400, "File too large (15 MB maximum).")
            dest = projects.project_dir(slug) / "inputs" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return self._json({"ok": True, "path": f"inputs/{name}"})
    return NO_ROUTE


def post_answers(self, app, path: str, body: dict):
    """Answers to cards and the council's brains."""
    if path == "/api/respond":
        item_id = str(body.get("id", ""))
        payload = {k: v for k, v in body.items() if k != "id"}
        if not app.engine.respond(item_id, payload):
            return self._error(409, "This request is no longer waiting for an answer.")
        return self._json({"ok": True})
    if path.startswith("/api/brains/"):
        action = path.rsplit("/", 1)[1]
        provider = str(body.get("provider", ""))
        if action in ("models", "add") and provider not in brains.PROVIDERS:
            return self._error(400, "Unknown provider")
        if action in ("models", "add") and body.get("key") and brains.PROVIDERS[provider]["key"]:
            secrets_store.set_secret(brains.PROVIDERS[provider]["key"], str(body["key"]).strip())
        try:
            if action == "models":
                if provider == "ollama":
                    cat = brains.ollama_catalog()
                    return self._json({"models": [r["model"] for r in cat["models"]],
                                       "tiered": cat["models"], "catalog": True, **{k: v for k, v in cat.items()
                                                                                     if k != "models"}})
                return self._json({"models": brains.list_models(provider),
                                   "tiered": brains.list_models_tiered(provider)})
            if action == "add":
                model = str(body.get("model", ""))
                if not model.strip():
                    return self._error(400, "Choose a model first.")
                # A local model you don't have yet: offer to download it instead of failing the test (item 55)
                if provider == "ollama" and brains._norm(model) not in brains.ollama_installed_names():
                    row = next((c for c in brains.OLLAMA_CATALOG if c["model"] == model), {})
                    return self._json({"added": False, "needs_download": True, "model": model,
                                       "size_gb": row.get("size_gb"),
                                       "ollama_installed": bool(tooling.ollama_exe())})
                # Test BEFORE adding: a brain that does not work is not saved (item 39)
                ok, msg = brains.test_config(provider, model)
                if not ok and not body.get("force"):
                    return self._json({"added": False, "test_ok": False, "test": msg,
                                       "tier": brains.model_tier(provider, model)})
                b = brains.add(provider, model)
                if not ok:  # user chose "add anyway": keep it switched off so it is never used
                    brains.update(b["id"], enabled=False)
                return self._json({"added": True, "brain": b, "test_ok": ok, "test": msg, **brains.summary()})
            if action == "ollama-get":
                model = str(body.get("model", "")).strip()
                if not re.fullmatch(r"[\w.:/\-]{1,80}", model):
                    return self._error(400, "Choose a model first.")
                threading.Thread(target=_ollama_get_and_add, args=(app, model), daemon=True).start()
                return self._json({"ok": True})
            if action == "test":
                b = brains.get(str(body.get("id", "")))
                if not b:
                    raise KeyError("brain")
                ok, msg = brains.test(b)
                return self._json({"ok": ok, "message": msg})
            if action == "remove":
                brains.remove(str(body.get("id", "")))
            elif action == "toggle":
                brains.update(str(body.get("id", "")), enabled=bool(body.get("enabled")))
            else:
                return self._error(404, "not found")
            return self._json(brains.summary())
        except brains.BrainError as e:
            return self._error(400, f"Could not reach the provider: {e}")
    return NO_ROUTE
