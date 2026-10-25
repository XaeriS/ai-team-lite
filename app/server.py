"""Local web server for the app window. Only reachable from this PC (127.0.0.1) and only with the random
token the window was opened with."""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import (__version__, auth, brains, challenge, github, iterations, journal, paths, projects, secrets_store,
               settings, skills as skills_mod, tooling, tracking, tools, features, updates, usage, voice, winhelpers)
from . import engine as engine_mod
from . import telegram
from .engine import Engine
from .events import EventHub

UI_DIR = Path(__file__).parent / "ui"
UI_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png",
            ".ico": "image/x-icon"}
MAX_UPLOAD = 15 * 1024 * 1024

SETUP_KICKOFF = ("[App] The user has just connected AI Team Lite to Claude and this is the first-time setup. "
                 "Greet them and start setup step 1.")
PROJECT_KICKOFF = ("[App] The user just created the project \"{name}\". Greet them in one or two sentences and "
                   "start discovering what they want to build. If GitHub is connected, mention you can create a "
                   "repository for it once the idea is clear.")
EXISTING_KICKOFF = (
    "[App] The user created the project \"{name}\" from an EXISTING folder ({folder}). Treat it as a CONTINUATION of a "
    "project that is already under way: rebuild the full picture so the team can carry on, as if AI Team Lite had been "
    "used from the start.\n"
    "What the app already found (free scan): {scan}\n"
    "1. Greet them in one sentence: you are reading the whole folder first (read-only, nothing is changed).\n"
    "2. Scan EVERYTHING, read-only: list all files (Glob); read every document, note and to-do list, every source "
    "file, the tests and the configuration (skip .venv, node_modules, build output and large data files). If it is a "
    "Git repository, read `git log --oneline -50` and `git status`. \n"
    "3. The project documents are REQUIREMENTS.md, ARCHITECTURE.md, PLAN.md and STATUS.md, in the project folder.\n"
    "   - A document that EXISTS (even in docs/ or with other capitals): read it, check it against the code, and "
    "keep the user's content. If it is not in the project folder itself, write it there from that content. Add a "
    "short section \"Checked against the code\" listing anything that no longer matches. Never rewrite it wholesale.\n"
    "   - A document that is MISSING: REBUILD it from what you understood, and start it with the line "
    "\"> Rebuilt by Fred from the existing files - please check.\"\n"
    "     REQUIREMENTS.md: every feature that already works as REQ-n marked \"(existing)\" with acceptance criteria "
    "taken from the code and tests; unfinished or wished-for items (TODOs, notes, failing tests) as further REQ-n.\n"
    "     STATUS.md: what the project is, technologies, how to run it, what works, what is unfinished or broken, "
    "recent history, risks, candidate next steps.\n"
    "     ARCHITECTURE.md and PLAN.md are rebuilt in steps 5 and 6 below (by Mat, then you), so they get approved.\n"
    "4. ASK FOR VALIDATION: tell the user in plain words where the project stands, which documents you found and "
    "which you rebuilt, then call ask_choice with the options \"Yes, that is right\" / \"Some things are wrong\" so "
    "they confirm REQUIREMENTS.md and STATUS.md (they can read them in the side panel). Fix what they correct, and "
    "ask them to PRIORITIZE what remains (most important first).\n"
    "5. Then handoff_to_architect: Mat describes the architecture AS IT IS in the code (or checks the existing "
    "ARCHITECTURE.md against it), only asks about real gaps, and asks for approval with "
    "request_architecture_approval - that is the validation of the architecture.\n"
    "6. Then the plan with request_plan_approval: first the work that is ALREADY finished, as iterations with "
    "already_done: true (shown as done, nothing is rebuilt), then the remaining work by priority. The approval "
    "card is the validation of the plan.{diagram}")
ADOPTED_KICKOFF = (
    "[App] The user opened the project \"{name}\" from its folder ({folder}). This folder was ALREADY an AI Team Lite "
    "project (on this PC before, or another one): its plan, iterations and approvals were kept. What the app found: "
    "{scan}\n"
    "1. Greet them in one sentence and say you are catching up on the project (read-only).\n"
    "2. Read REQUIREMENTS.md, ARCHITECTURE.md, PLAN.md and STATUS.md, `git log --oneline -30` and `git status`, and "
    "check the code still matches (files may have changed since). Rebuild any document that is missing, starting it "
    "with \"> Rebuilt by Fred from the existing files - please check.\", and update STATUS.md.\n"
    "3. Tell the user in plain words where the project stands and what comes next, and ask with ask_choice whether to "
    "continue with the plan as it is (\"Continue the plan\" / \"Change something first\"). If something was rebuilt, "
    "ask them to check it first.")


def _scan_text(scan: dict) -> str:
    if not scan:
        return "(nothing)"
    found = ", ".join(f"{d} (at {where})" for d, where in scan.get("found", {}).items()) or "none of the documents"
    missing = ", ".join(scan.get("missing", [])) or "none"
    return (f"{scan.get('files', 0)} files; found {found}; missing: {missing}; "
            f"{'a Git repository' if scan.get('git') else 'not a Git repository'}"
            f"{'; readme: ' + scan['readme'] if scan.get('readme') else ''}.")


class App:
    def __init__(self, engine: Engine | None = None, hub: EventHub | None = None):
        self.hub = hub or EventHub(projects.history_file)
        self.engine = engine or Engine(self.hub)
        self.token = secrets.token_urlsafe(24)
        self.bridge = None  # set by main.py when running in the desktop window
        self.listener = None
        self.voice_downloading = False
        self.voice_lock = threading.Lock()
        self.telegram = telegram.Bridge(self.engine)   # L17: approvals and alerts on the phone
        self.engine.telegram = self.telegram
        if self.telegram.connected():
            self.telegram.start()

    # ---------------------------------------------------------------- voice
    def voice_start(self, slug: str) -> str:
        with self.voice_lock:
            if self.listener is not None:
                return "already listening"
            if not voice.packages_available():
                raise ValueError("Voice is not available in this installation.")
            if not voice.model_ready():
                raise ValueError("needs-download")

            def on_event(ev):
                self.hub.publish(slug, {"type": "voice", **ev, "transient": True})
                if ev["state"] in ("final", "cancelled", "error"):
                    with self.voice_lock:
                        self.listener = None

            extra = {k: make() for k, make in self.voice_overrides.items()}  # test hooks (factories)
            self.listener = voice.Listener(on_event, **extra).start()
            return "listening"

    voice_overrides: dict = {}

    def voice_stop(self, cancel: bool) -> None:
        with self.voice_lock:
            if self.listener is not None:
                self.listener.stop(cancel=cancel)

    def voice_download(self, slug: str) -> None:
        if self.voice_downloading:
            return
        self.voice_downloading = True

        def run():
            try:
                voice.download_model()
                self.hub.publish(slug, {"type": "voice", "state": "downloaded", "text": "", "transient": True})
            except Exception as e:  # noqa: BLE001
                self.hub.publish(slug, {"type": "voice", "state": "error", "transient": True,
                                        "text": f"The speech model could not be downloaded: {e}"})
            finally:
                self.voice_downloading = False

        threading.Thread(target=run, name="voice-download", daemon=True).start()

    def state(self) -> dict:
        s = settings.load()
        return {
            "version": __version__,
            "has_api_key": auth.ready(),   # "Fred can think" (API key or subscription)
            "auth_mode": auth.mode(),
            "api_key_saved": secrets_store.has("anthropic_api_key"),
            "setup_complete": bool(s["setup_complete"]),
            "settings": {k: s[k] for k in ("approval_mode", "user_name", "github_login", "iteration_pause", "voice_end_mode",
                                           "pipeline_auto", "voice_end_word", "voice_silence_seconds", "voice_review",
                                           "voice_auto_listen", "voice_model", "voice_language", "auto_resume",
                                           "close_to_tray")}
            | {"workspace_dir": str(settings.workspace())},
            "github_connected": secrets_store.has("github_token"),
            "broken": projects.broken(),
            "is_owner": updates.is_owner(),
            "can_manage_features": updates.can_manage_features(),
            "features_new": sum(1 for i in features.load() if i.get("status") == "new"),
            "projects": [{"slug": p["slug"], "name": p["name"], "phase": p.get("phase", "discover"),
                          "busy": self.engine.is_busy(p["slug"]), "paused": bool(p.get("paused")),
                          "state": self.engine.project_state(p["slug"], p), "hidden": bool(p.get("hidden")),
                          "existing": bool(p.get("existing_folder")),
                          "launch": engine_mod.launch_info(p["slug"], p),
                          "priority": p.get("priority", "normal"), "progress": iterations.progress(p["slug"])}
                         for p in projects.list_projects()],
            "max_parallel": int(s.get("max_parallel") or 2),
            "brains_count": len([b for b in s.get("brains") or [] if b.get("enabled")]),
            "voice": {"packages": voice.packages_available(), "model_ready": voice.model_ready(),
                      "listening": self.listener is not None, "downloading": self.voice_downloading,
                      "model_size": voice.MODEL_SIZES.get(s["voice_model"], "")},
            "plan_usage": usage.plan() if auth.mode() == "subscription" else {},
            "cost_today": usage.cost_today() if auth.mode() != "subscription" else None,
            "desktop": self.bridge is not None,
            "windows": paths.IS_WINDOWS,
            "interrupted": journal.interrupted(),
            "why": {"problem": challenge.PROBLEM_WHY},
            "telegram": self.telegram.status(),   # L5: "Why Fred asked this" texts, kept in one place
            "seq": self.hub.seq,
        }

    def project_view(self, slug: str) -> dict:
        p = projects.load(slug)
        if not p:
            raise KeyError(slug)
        live = self.engine.pending_for(slug)
        history = self.hub.history(slug)
        for ev in history:
            if ev.get("status") == "pending" and ev.get("id") not in live:
                ev["status"] = "expired"
        p = {**p, "folder": str(projects.project_dir(slug)), "state": self.engine.project_state(slug, p)}
        p["launch"] = engine_mod.launch_info(slug, p)
        p["estimate"] = iterations.cost_estimate(slug)
        return {"project": p, "history": history, "busy": self.engine.is_busy(slug) or p["state"] == "working",
                "team": self.engine.session(slug).team_snapshot(), "progress": iterations.progress(slug),
                "docs": {"requirements": projects.read_doc(slug, "REQUIREMENTS.md"),
                         "plan": projects.read_doc(slug, "PLAN.md"),
                         "architecture": projects.read_doc(slug, "ARCHITECTURE.md"),
                         "status": projects.read_doc(slug, "STATUS.md"),
                         "prior_art": projects.read_doc(slug, "reports/PRIOR_ART.md")},
                "screenshots": self._screens(slug), "seq": self.hub.seq}

    def _screens(self, slug: str) -> list[str]:
        d = projects.project_dir(slug) / "reports" / "screenshots"
        if not d.exists():
            return []
        return [f.relative_to(projects.project_dir(slug)).as_posix()
                for f in sorted(d.glob("*.png"), key=lambda f: f.stat().st_mtime)]


_brain_job = threading.Lock()


def _job_event(app, text, **kw):
    app.hub.publish("setup", {"type": "brainjob", "text": text, "transient": True, **kw})


def _ollama_get_and_add(app, model):
    """Download a local model (installing and starting Ollama first if needed), then test and add it (item 55)."""
    if not _brain_job.acquire(blocking=False):
        _job_event(app, "Another download is already running; wait for it to finish.", error=True)
        return
    try:
        if not tooling.ollama_exe() and tooling.ollama_status()["state"] != "running":
            tooling.ollama_install(lambda m, f=None: _job_event(app, m, frac=f))
        if tooling.ollama_status()["state"] != "running":
            _job_event(app, "Starting Ollama…")
            tooling.ollama_start(wait=30)
        if brains._norm(model) not in brains.ollama_installed_names():
            tooling.ollama_pull(model, lambda m, f=None: _job_event(app, m, frac=f))
        _job_event(app, "Testing the model…")
        ok, msg = brains.test_config("ollama", model)
        if not ok:
            _job_event(app, f"Downloaded, but the test failed: {msg}", error=True, done=True)
            return
        try:
            brains.add("ollama", model)
        except ValueError:
            pass  # already in the list
        _job_event(app, f"✓ {model} downloaded, tested and added.", done=True, added=True)
    except Exception as e:  # noqa: BLE001
        _job_event(app, f"Could not finish: {e}", error=True, done=True)
    finally:
        _brain_job.release()


NO_ROUTE = object()   # a route group returns this when the path is not one of its own


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AITeamLite"

        def log_message(self, *args):  # keep the console quiet
            pass

        # ---------------------------------------------------------------- plumbing
        def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200):
            self._send(code, json.dumps(data).encode("utf-8"))

        def _error(self, code: int, message: str):
            self._json({"error": message}, code)

        def _query(self) -> dict:
            return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

        def _authorized(self) -> bool:
            if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                return False
            tok = self.headers.get("X-Token") or self._query().get("token", "")
            return secrets.compare_digest(tok, app.token)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD * 2:
                raise ValueError("Request too large")
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw or b"{}")

        # ---------------------------------------------------------------- GET
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                if not self._authorized():
                    return self._send(403, b"Open AI Team Lite from its desktop icon.", "text/plain")
                html = (UI_DIR / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/ui/"):
                f = (UI_DIR / path[4:]).resolve()
                if UI_DIR.resolve() in f.parents and f.is_file():
                    # fixed types: the Windows registry sometimes maps .js to text/plain
                    ctype = UI_TYPES.get(f.suffix.lower()) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    return self._send(200, f.read_bytes(), ctype)
                return self._error(404, "not found")
            if not self._authorized():
                return self._error(403, "forbidden")
            try:
                if path == "/api/state":
                    return self._json(app.state())
                m = re.fullmatch(r"/api/projects/([a-z0-9-]+)", path)
                if m:
                    return self._json(app.project_view(m.group(1)))
                m = re.fullmatch(r"/files/([a-z0-9-]+)/(.+)", path)
                if m:
                    return self._file(m.group(1), unquote(m.group(2)))
                m = re.fullmatch(r"/diagram/([a-z0-9-]+)/([a-f0-9]{6,32})", path)
                if m:  # an Archify diagram (self-contained HTML in the project's .archify folder)
                    from . import diagrams
                    which = "compare" if self._query().get("which") == "compare" else "html"
                    try:
                        f = diagrams.path_of(m.group(1), m.group(2), which)
                    except (KeyError, ValueError):
                        return self._error(404, "Diagram not found.")
                    return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
                m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/diagrams", path)
                if m:
                    from . import diagrams
                    if not projects.load(m.group(1)):
                        raise KeyError(m.group(1))
                    return self._json({"items": diagrams.items(m.group(1)), "available": tooling.archify_ready(),
                                       "enabled": skills_mod.enabled().get("archify", True)})
                m2 = re.fullmatch(r"/api/projects/([a-z0-9-]+)/(team|tests)", path)
                if m2:
                    slug, what = m2.groups()
                    if not projects.load(slug):
                        raise KeyError(slug)
                    if what == "team":
                        return self._json({"team": app.engine.session(slug).team_snapshot()})
                    folder = projects.project_dir(slug)
                    results = tools.load_tests(folder)
                    linked = {r.get("screenshot") for r in results}
                    loose = [s for s in app._screens(slug) if s not in linked]
                    return self._json({"results": results, "screenshots": loose})
                if path == "/api/work":
                    return self._json({"rows": app.engine.work_overview(), "running": app.engine.running_count(),
                                       "max_parallel": int(settings.load().get("max_parallel") or 2)})
                if path == "/api/lessons":   # L23
                    from . import lessons
                    return self._json({"items": sorted(lessons.load(), key=lambda i: -i.get("time", 0))})
                if path == "/api/brains":
                    return self._json(brains.summary())
                if path == "/api/pipeline":
                    return self._json(app.engine.pipeline_overview())
                if path == "/api/app/update-status":
                    st = updates.check()
                    st["busy"] = app.engine.running_count() > 0 or bool(list(app.engine.pending.values()))
                    st["is_owner"] = updates.is_owner()
                    st["sharing"] = bool(settings.load().get("app_repo"))
                    st["repo"] = settings.load().get("app_repo") or updates.share_config().get("app_repo") or ""
                    return self._json(st)
                if path == "/api/features":
                    return self._json({**features.summary(), "is_owner": updates.is_owner(),
                                       "can_manage": updates.can_manage_features()})
                m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/traceability", path)
                if m:
                    if not projects.load(m.group(1)):
                        raise KeyError(m.group(1))
                    return self._json({"rows": tracking.traceability(m.group(1))})
                if path == "/api/analytics":   # L6, L9, L13, L14, L15
                    from . import analytics
                    q = self._query()
                    slug = q.get("project") or None
                    if slug and not projects.load(slug):
                        raise KeyError(slug)
                    data = analytics.report(slug, q.get("unit") or "week")
                    data["mode"] = auth.mode()
                    data["checked"] = time.time()
                    if slug:
                        data["state"] = app.engine.project_state(slug)
                    return self._json(data)
                m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/activity", path)
                if m:   # L9: the mini chart next to the working indicator
                    from . import analytics
                    if not projects.load(m.group(1)):
                        raise KeyError(m.group(1))
                    return self._json({**analytics.activity(m.group(1)), "state": app.engine.project_state(m.group(1)),
                                       "headroom": analytics.headroom() if auth.mode() == "subscription" else []})
                if path == "/api/stream":
                    return self._stream()
            except KeyError:
                return self._error(404, "Unknown project")
            except ValueError as e:
                return self._error(400, str(e))
            return self._error(404, "not found")

        def _file(self, slug: str, rel: str):
            root = projects.project_dir(slug).resolve()
            f = (root / rel).resolve()
            if root not in f.parents or not f.is_file() or f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                return self._error(404, "not found")
            return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "image/png")

        def _stream(self):
            since = int(self._query().get("since") or 0)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    events = app.hub.wait(since, timeout=15)
                    if not events:
                        self.wfile.write(b": keep-alive\n\n")
                    for ev in events:
                        since = ev["seq"]
                        self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass   # the window closed or reloaded

        # ---------------------------------------------------------------- POST
        def do_POST(self):
            if not self._authorized():
                return self._error(403, "forbidden")
            path = urlparse(self.path).path
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError):
                return self._error(400, "Bad request")
            try:
                return self._post(path, body)
            except KeyError:
                return self._error(404, "Unknown project")
            except ValueError as e:
                return self._error(400, str(e))

        def _kickoff_setup(self):
            if not settings.load()["setup_complete"]:
                projects.update(projects.SETUP_SLUG, updated=0)
                if not app.hub.history(projects.SETUP_SLUG):
                    app.engine.send_message(projects.SETUP_SLUG, SETUP_KICKOFF, hidden=True)

        def _post(self, path: str, body: dict):
            """Every button press in the window arrives here; each group handles its own paths, in order."""
            for group in (self._post_signin, self._post_projects, self._post_app, self._post_work, self._post_answers,
                          self._post_settings):
                result = group(path, body)
                if result is not NO_ROUTE:
                    return result
            return self._error(404, "not found")

        def _post_signin(self, path: str, body: dict):
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
                return self._json(st)
            if path == "/api/auth/subscription/logout":
                auth.subscription_logout()
                settings.save({"subscription_signed_in": False})
                if auth.mode() == "subscription":
                    settings.save({"auth_mode": "api_key" if secrets_store.has("anthropic_api_key") else ""})
                return self._json(app.state())
            return NO_ROUTE

        def _post_projects(self, path: str, body: dict):
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

        def _post_app(self, path: str, body: dict):
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

        def _post_work(self, path: str, body: dict):
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

        def _post_answers(self, path: str, body: dict):
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

        def _post_settings(self, path: str, body: dict):
            """Settings, secrets, voice, phone approvals, lessons and folder picking."""
            if path == "/api/settings":
                changes = {}
                if body.get("approval_mode") in ("plan", "strict"):
                    changes["approval_mode"] = body["approval_mode"]
                if isinstance(body.get("max_parallel"), int) and 1 <= body["max_parallel"] <= 5:
                    changes["max_parallel"] = body["max_parallel"]
                for k in ("iteration_pause", "pipeline_auto", "close_to_tray"):
                    if isinstance(body.get(k), bool):
                        changes[k] = body[k]
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

    return Handler


def serve(app: App, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?token={app.token}"
    return httpd, url
