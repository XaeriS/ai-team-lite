"""Buttons that create and manage projects and the app itself: new project, attachments, releases, launch, sharing, features.

Each function handles its own paths for the local web server (app/server.py) and returns NO_ROUTE for any other
path (L46). `self` is the request handler, `app` the running App."""
from __future__ import annotations

import re
import threading

from . import (checkpoints, engine as engine_mod, features, github, iterations, projects, secrets_store, settings,
               tools, updates, voice, winhelpers)
from .server_common import ADOPTED_KICKOFF, EXISTING_KICKOFF, NO_ROUTE, PROJECT_KICKOFF, _scan_text


def post_projects(self, app, path: str, body: dict):
    """Creating projects, attachments, releases and launching the app."""
    if path == "/api/projects":
        if not settings.load()["setup_complete"]:
            return self._error(400, "Finish the setup with Fred first.")
        folder = str(body.get("folder", "")).strip()
        if folder:
            p = projects.create_from_folder(folder, str(body.get("name", "")))
            kickoff = (ADOPTED_KICKOFF if p.get("adopted") else EXISTING_KICKOFF).replace("{name}", p["name"]) \
                .replace("{folder}", p["source"]).replace("{scan}", _scan_text(p.get("scan") or {})) \
                .replace("{diagram}", "\n7. Once the architecture is approved, ask the diagrammer for the overall "
                         "picture of the app as it is (topic 'architecture')." if tools.diagrams_on() else "")
        else:
            has_ui = body.get("has_ui") if isinstance(body.get("has_ui"), bool) else None
            p = projects.create(str(body.get("name", "")), has_ui=has_ui)
            kickoff = PROJECT_KICKOFF.replace("{name}", p["name"])
        app.engine.send_message(p["slug"], kickoff, hidden=True)
        app.hub.publish(p["slug"], {"type": "project_list", "transient": True})
        return self._json(p)
    m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/delete-attachment", path)
    if m:
        slug = m.group(1)
        if not projects.load(slug):
            raise KeyError(slug)
        rel = str(body.get("path", "")).replace("\\", "/")
        folder = projects.project_dir(slug)
        target = (folder / rel).resolve()
        inputs = (folder / "inputs").resolve()
        if not rel.startswith("inputs/") or inputs not in target.parents:
            return self._error(400, "Only attached files can be deleted here.")
        existed = target.is_file()
        target.unlink(missing_ok=True)
        if body.get("sent"):  # already sent to Fred: record it and tell him on the next turn
            app.hub.publish(slug, {"type": "attachment_deleted", "path": rel})
            app.engine.add_note(slug, f"[App] The user deleted the attached file {rel}. It no longer exists; "
                                      "do not use it or anything you read from it unless they say otherwise.")
        return self._json({"ok": True, "existed": existed})
    m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/(go-back|go-back-undo)", path)
    if m:   # L48
        slug, action = m.groups()
        if not projects.load(slug) or slug == projects.SETUP_SLUG:
            raise KeyError(slug)
        try:
            if action == "go-back":
                return self._json(app.engine.go_back(slug, int(body.get("n") or 0)))
            return self._json(app.engine.undo_go_back(slug))
        except (checkpoints.CheckpointError, ValueError) as e:
            return self._error(400, str(e))
    m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/(release|iteration-after|launch)", path)
    if m:
        slug, action = m.groups()
        p = projects.load(slug)
        if not p or slug == projects.SETUP_SLUG:
            raise KeyError(slug)
        if action == "release":
            return self._json({"result": app.engine.release(slug, int(body.get("n") or 0))})
        if action == "iteration-after":
            if body.get("after") not in iterations.AFTER:
                return self._error(400, "after must be continue or pause")
            iterations.set_after(slug, {int(body.get("n") or 0): body["after"]})
            iterations.refresh_plan_doc(slug)
            app.hub.publish(slug, {"type": "project_list", "transient": True})
            threading.Thread(target=app.engine._start_next, daemon=True).start()
            return self._json({"ok": True})
        info = engine_mod.launch_info(slug, p)
        if not info:
            return self._error(400, "This project has nothing to start yet.")
        chosen = str(body.get("command") or "")
        if chosen:  # the user picked which start file to use: remember it
            if chosen not in info["all_choices"] and chosen != info.get("command"):
                return self._error(400, "Unknown start file.")
            url = (p.get("launch") or {}).get("url", "")
            p = projects.update(slug, launch={"command": chosen, "url": url}, launch_chosen=True)
            info = engine_mod.launch_info(slug, p)
            if body.get("save_only"):
                return self._json({"ok": True, **info})
        elif info["choices"]:
            return self._json({"needs_choice": True, **info})
        try:
            pid = winhelpers.launch_checked(info["command"], projects.project_dir(slug))
        except Exception as e:  # noqa: BLE001
            return self._error(400, f"Could not start it: {e}")
        if info.get("url"):
            threading.Timer(2.5, winhelpers.open_url, args=(info["url"],)).start()
        app.hub.publish(slug, {"type": "notice", "text": f"You launched the project ({info.get('version') or info['command']})."})
        return self._json({"ok": True, "pid": pid, **info})
    return NO_ROUTE


def post_app(self, app, path: str, body: dict):
    """Sharing the app, requested features and the pipeline order."""
    if path == "/api/app/setup-sharing":
        if not secrets_store.has("github_token"):
            return self._error(400, "Connect GitHub first (Settings > Setup & connections).")
        if settings.load().get("app_repo"):
            return self._error(400, "Sharing is already set up.")
        try:
            repo = updates.set_up_sharing(str(body.get("name") or "ai-team-lite"), bool(body.get("private", False)))
        except Exception as e:  # noqa: BLE001
            return self._error(400, f"Could not set up sharing: {str(e)[:400]}")
        return self._json({"ok": True, **repo})
    if path == "/api/app/link-sharing":
        if not secrets_store.has("github_token"):
            return self._error(400, "Connect GitHub first (Settings > Setup & connections).")
        current = settings.load().get("app_repo") or ""
        wanted = str(body.get("repo") or "")
        if current and github.repo_full_name(current).lower() not in (wanted.lower(), github.repo_full_name(wanted).lower()):
            return self._error(400, f"Sharing already uses {current}.")
        try:
            info = updates.link_existing(wanted)
        except Exception as e:  # noqa: BLE001
            return self._error(400, f"Could not link the repository: {str(e)[:400]}")
        return self._json({"ok": True, **info})
    if path == "/api/features/feedback":
        title = str(body.get("title", "")).strip()
        if not title:
            return self._error(400, "Please write a short title for your feedback.")
        text = str(body.get("body", ""))
        if body.get("voice_log"):   # L11: the latest microphone problems (error messages only, no audio)
            lines = voice.recent_problems(10)
            if lines:
                text += "\n\nRecent microphone problems:\n" + "\n".join(lines)
        return self._json(features.submit_feedback(title, text))
    if path == "/api/features/status":
        if not updates.can_manage_features():
            return self._error(403, "Only the author can change a request's status.")
        try:
            it = features.set_status(str(body.get("id", "")), str(body.get("status", "")), str(body.get("reason", "")))
        except ValueError as e:
            return self._error(400, str(e))
        return self._json({"ok": True, "item": it})
    if path == "/api/features/import":
        try:
            n = features.import_feedback()
        except Exception as e:  # noqa: BLE001
            return self._error(400, f"Could not import feedback: {str(e)[:300]}")
        return self._json({"ok": True, "added": n, **features.summary()})
    if path == "/api/pipeline/order":
        order = [x for x in body.get("order") or [] if isinstance(x, str) and projects.load(x)]
        settings.save({"pipeline_order": order})
        threading.Thread(target=app.engine._start_next, daemon=True).start()
        return self._json(app.engine.pipeline_overview())
    return NO_ROUTE
