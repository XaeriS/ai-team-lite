"""Local web server for the app window. Only reachable from this PC (127.0.0.1) and only with the random
token the window was opened with."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse

from . import (__version__, auth, brains, challenge, checkpoints, engine as engine_mod, features, iterations, journal,
               paths, projects, routes_projects, routes_setup, routes_work, scorecard, secrets_store, settings,
               skills as skills_mod, spelling, telegram, tooling, tools, tracking, updates, usage, voice)
from .engine import Engine
from .events import EventHub
from .server_common import MAX_UPLOAD, NO_ROUTE, SETUP_KICKOFF, UI_DIR, UI_TYPES

HISTORY_WINDOW = 300   # L59: chat events sent when a project opens; earlier ones on request


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
                                           "close_to_tray", "scorecard_online")}
            | {"workspace_dir": str(settings.workspace()), "spell_check": bool(s.get("spell_check", True)),
               "spell_languages": spelling.languages(), "spell_all": spelling.LANGUAGES},
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
        full = self._history(slug)
        start = max(0, len(full) - HISTORY_WINDOW)          # L59: only the latest part of a long chat is sent
        history = [ev for ev in full[:start] if ev.get("status") == "pending"] + full[start:]
        p = {**p, "folder": str(projects.project_dir(slug)), "state": self.engine.project_state(slug, p)}
        p["launch"] = engine_mod.launch_info(slug, p)
        p["estimate"] = iterations.cost_estimate(slug)
        return {"project": p, "history": history, "history_from": start, "busy": self.engine.is_busy(slug) or p["state"] == "working",
                "team": self.engine.session(slug).team_snapshot(), "progress": iterations.progress(slug),
                "docs": {"requirements": projects.read_doc(slug, "REQUIREMENTS.md"),
                         "plan": projects.read_doc(slug, "PLAN.md"),
                         "architecture": projects.read_doc(slug, "ARCHITECTURE.md"),
                         "status": projects.read_doc(slug, "STATUS.md"),
                         "prior_art": projects.read_doc(slug, "reports/PRIOR_ART.md")},
                "screenshots": self._screens(slug), "seq": self.hub.seq}

    def _history(self, slug: str) -> list[dict]:
        """The chat; cards still pending from an earlier run show as expired."""
        live = self.engine.pending_for(slug)
        full = self.hub.history(slug)
        for ev in full:
            if ev.get("status") == "pending" and ev.get("id") not in live:
                ev["status"] = "expired"
        return full

    def history_before(self, slug: str, before: int, limit: int = 0) -> dict:
        """Earlier messages for "Show earlier messages" (L59)."""
        if not projects.load(slug):
            raise KeyError(slug)
        full = self._history(slug)
        before = max(0, min(before, len(full)))
        start = max(0, before - (limit or HISTORY_WINDOW))
        return {"items": full[start:before], "from": start}

    def _screens(self, slug: str) -> list[str]:
        d = projects.project_dir(slug) / "reports" / "screenshots"
        if not d.exists():
            return []
        return [f.relative_to(projects.project_dir(slug)).as_posix()
                for f in sorted(d.glob("*.png"), key=lambda f: f.stat().st_mtime)]




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
                m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/draft", path)
                if m:   # REQ-29: the unsent message of a project
                    return self._json(routes_work.get_draft(m.group(1)))
                m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/history", path)
                if m:   # L59: earlier messages, on request
                    q = self._query()
                    try:
                        before, limit = int(q.get("before") or 0), min(int(q.get("limit") or 0), 2000)
                    except ValueError:
                        return self._error(400, "before and limit are numbers")
                    return self._json(app.history_before(m.group(1), before, limit))
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
                m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/(changes|changes-file)", path)
                if m:   # L48: what an iteration changed
                    if not projects.load(m.group(1)):
                        raise KeyError(m.group(1))
                    q = self._query()
                    try:
                        n = int(q.get("n") or 0)
                        if m.group(2) == "changes":
                            return self._json(checkpoints.changes(m.group(1), n))
                        return self._json(checkpoints.file_diff(m.group(1), n, q.get("path", "")))
                    except (checkpoints.CheckpointError, ValueError) as e:
                        return self._error(400, str(e))
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
                m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/scorecard", path)
                if m:   # L49: the quality of the version
                    if not projects.load(m.group(1)):
                        raise KeyError(m.group(1))
                    return self._json(scorecard.load(m.group(1)) or {"missing": True})
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

        def do_PUT(self):
            if not self._authorized():
                return self._error(403, "forbidden")
            m = re.fullmatch(r"/api/projects/([a-z0-9-]+)/draft", urlparse(self.path).path)
            if not m:
                return self._error(404, "not found")
            try:
                return self._json(routes_work.put_draft(m.group(1), self._body()))
            except KeyError:
                return self._error(404, "Unknown project")
            except (ValueError, TypeError, json.JSONDecodeError) as e:
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
            return routes_setup.post_signin(self, app, path, body)

        def _post_projects(self, path: str, body: dict):
            return routes_projects.post_projects(self, app, path, body)

        def _post_app(self, path: str, body: dict):
            return routes_projects.post_app(self, app, path, body)

        def _post_work(self, path: str, body: dict):
            return routes_work.post_work(self, app, path, body)

        def _post_answers(self, path: str, body: dict):
            return routes_work.post_answers(self, app, path, body)

        def _post_settings(self, path: str, body: dict):
            return routes_setup.post_settings(self, app, path, body)

    return Handler


def serve(app: App, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?token={app.token}"
    return httpd, url
