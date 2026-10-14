"""Start AI Team Lite: a local server plus a desktop window (pywebview / Edge WebView2).

  python -m app.main            normal start (desktop window)
  python -m app.main --browser  open in the default browser instead (development)
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):  # started as a script: make "app" importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "app"

from app import paths  # noqa: E402

INSTANCE_PORT = 47824  # AI Team (expert) uses 47823: both can run side by side


def _setup_logging() -> None:
    log_dir = paths.data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_dir / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stderr is None or "pythonw" in Path(sys.executable).name.lower():
        f = open(log_dir / "stderr.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stderr = sys.stdout = f


def _hidden_console() -> None:
    """pythonw has no console, so every console program the agents start (claude.exe, PowerShell, python)
    would pop up a black window. Give the app one hidden console that those programs share."""
    if not paths.IS_WINDOWS:
        return
    import ctypes

    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    if not k32.GetConsoleWindow():
        k32.AllocConsole()
    hwnd = k32.GetConsoleWindow()
    if hwnd:
        u32.ShowWindow(hwnd, 0)


def _single_instance() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


def _ask_running_to_show() -> bool:
    """Starting AI Team Lite while it is already running (maybe hidden in the tray): ask that copy to show its window."""
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2) as c:
            c.sendall(b"show")
            return c.recv(2) == b"ok"
    except OSError:
        return False


class Tray:
    """The icon next to the clock when "keep running in the tray" is on (item 67): Open / Quit."""

    def __init__(self, bridge: "Bridge"):
        self.bridge, self.icon, self.told = bridge, None, False

    def ensure(self) -> bool:
        if self.icon is not None:
            return True
        try:
            import pystray
            from PIL import Image
            img = Image.open(paths.install_dir() / "app" / "ui" / "icon.png")
            menu = pystray.Menu(pystray.MenuItem("Open AI Team Lite", lambda *_: self.bridge.show(), default=True),
                                pystray.MenuItem("Quit AI Team Lite", lambda *_: self.bridge.quit()))
            self.icon = pystray.Icon("AI Team Lite", img, "AI Team Lite", menu)
            self.icon.run_detached()
            return True
        except Exception:  # noqa: BLE001  (no tray available: fall back to a normal close)
            logging.exception("tray icon unavailable")
            self.icon = None
            return False

    def notify_once(self) -> None:
        if self.icon is not None and not self.told:
            self.told = True
            try:
                self.icon.notify("AI Team Lite is still running here. Click the icon to open it, or right-click > Quit.",
                                 "AI Team Lite")
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self.icon = None


def _message(text: str) -> None:
    if paths.IS_WINDOWS:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, paths.APP_NAME, 0x40)
    else:
        print(text)


class Bridge:
    """Things the page cannot do by itself."""

    def __init__(self):
        self.window = None
        self.quitting = False
        self.tray = Tray(self)

    def show(self) -> None:
        if self.window:
            try:
                self.window.show()
                self.window.restore()
            except Exception:  # noqa: BLE001
                pass

    def quit(self) -> None:
        """Quit for real (from the tray). The usual "the team is still working" warning still applies."""
        self.quitting = True
        self.show()
        if self.window:
            self.window.destroy()

    def voice(self) -> bool:
        from app import winhelpers

        return winhelpers.start_voice_typing()

    def pick_folder(self, start: str = "") -> str | None:
        import webview

        if not self.window:
            return None
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG, directory=start or str(Path.home()))
        return result[0] if result else None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _setup_logging()
    lock = _single_instance()
    if lock is None:
        if not _ask_running_to_show():
            _message("AI Team Lite is already open. Look for its window in the taskbar, or its icon next to the clock.")
        return 0
    _hidden_console()

    from app import journal
    from app.server import App, serve

    app = App()
    try:
        from app import projects
        fixed = projects.repair()  # heal projects damaged by the 0.11.0 "reopen a folder" bug (item 65)
        if fixed:
            logging.info("repaired projects: %s", fixed)
    except Exception:  # noqa: BLE001
        logging.exception("project repair failed")
    try:
        journal.start()  # detect work interrupted by an unclean shutdown last time (item 33)
    except Exception:  # noqa: BLE001
        logging.exception("journal.start failed")
    httpd, url = serve(app, port=int(os.environ.get("AITEAM_PORT", "0")))
    logging.info("serving at %s", url.split("?")[0])

    def _auto_share():
        try:
            from app import updates
            if updates.auto_push_if_owner_updated():
                logging.info("owner update pushed to the shared repo")
        except Exception:  # noqa: BLE001
            logging.exception("auto share-update failed")
    threading.Thread(target=_auto_share, name="share-update", daemon=True).start()
    def _archify_check():
        try:
            from app import tooling
            logging.info("archify works: %s", tooling.archify_works())
        except Exception:  # noqa: BLE001
            logging.exception("archify self-test failed")
    threading.Thread(target=_archify_check, name="archify-check", daemon=True).start()

    use_browser = "--browser" in argv
    if not use_browser:
        try:
            import webview
        except Exception as e:  # noqa: BLE001
            logging.warning("pywebview unavailable (%s); falling back to browser", e)
            use_browser = True

    try:
        if use_browser:
            import webbrowser

            webbrowser.open(url)
            print(f"AI Team Lite is running at {url}\nPress Ctrl+C to quit.")
            while True:
                time.sleep(3600)
        else:
            bridge = Bridge()
            app.bridge = bridge
            icon = paths.install_dir() / "app" / "ui" / "icon.ico"
            bridge.window = webview.create_window(paths.APP_NAME, url, width=1360, height=880, min_size=(900, 600),
                                                  background_color="#14171c")

            def instance_listener():
                """Another start of AI Team Lite asks this one to show itself (useful when it is hidden in the tray)."""
                while True:
                    try:
                        conn, _ = lock.accept()
                        with conn:
                            if conn.recv(16) == b"show":
                                bridge.show()
                                conn.sendall(b"ok")
                    except OSError:
                        return
            threading.Thread(target=instance_listener, name="instance", daemon=True).start()

            def on_closing():
                """X button: hide to the tray when that option is on (item 67); otherwise warn before closing while
                agents are working (item 33). Returning False cancels the close."""
                from app import settings as _settings
                if not bridge.quitting and _settings.load().get("close_to_tray") and bridge.tray.ensure():
                    bridge.window.hide()
                    bridge.tray.notify_once()
                    return False
                try:
                    busy = [p for p in __import__("app.projects", fromlist=["x"]).list_projects()
                            if app.engine.project_state(p["slug"], p) in ("working", "needs you", "waiting")]
                except Exception:  # noqa: BLE001
                    busy = []
                if not busy:
                    return True
                names = ", ".join(p["name"] for p in busy[:5])
                if paths.IS_WINDOWS:
                    import ctypes

                    # MB_OKCANCEL | MB_ICONWARNING; IDOK == 1
                    r = ctypes.windll.user32.MessageBoxW(
                        None, f"The team is still working on: {names}.\n\n"
                        "If you close now, the current step is interrupted (nothing already committed is lost, and "
                        "AI Team Lite will offer to resume it next time).\n\nClose anyway?",
                        paths.APP_NAME, 0x31)
                    if r != 1:
                        bridge.quitting = False  # the user chose to keep it open
                    return r == 1
                return True

            try:
                bridge.window.events.closing += on_closing
            except Exception:  # noqa: BLE001  (older pywebview)
                logging.info("closing event not available")
            try:
                webview.start(gui="edgechromium" if paths.IS_WINDOWS else None, private_mode=False,
                              storage_path=str(paths.data_dir() / "webview"), icon=str(icon) if icon.exists() else None)
            except Exception as e:  # noqa: BLE001  (e.g. Microsoft Edge WebView2 not installed)
                logging.exception("desktop window failed: %s", e)
                import webbrowser

                app.bridge = None
                webbrowser.open(url)
                _message("AI Team Lite opened in your web browser because the app window could not start "
                         "(Microsoft Edge WebView2 may be missing). Close this message to quit AI Team Lite.")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            app.bridge and app.bridge.tray.stop()
        except Exception:  # noqa: BLE001
            pass
        app.engine.shutdown()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
