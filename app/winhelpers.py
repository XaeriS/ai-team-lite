"""Windows helpers: voice typing, screenshots, launching programs. Each has a fallback for other systems
(used only for development and tests)."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import paths

NO_WINDOW = 0x08000000 if paths.IS_WINDOWS else 0
NEW_CONSOLE = 0x00000010 if paths.IS_WINDOWS else 0


# ---------------------------------------------------------------------------- voice typing
def start_voice_typing() -> bool:
    """Press Win+H: Windows voice typing writes what you say into the focused text box."""
    if not paths.IS_WINDOWS:
        return False
    user32 = ctypes.windll.user32
    VK_LWIN, VK_H, KEYUP = 0x5B, 0x48, 0x0002
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    user32.keybd_event(VK_H, 0, 0, 0)
    user32.keybd_event(VK_H, 0, KEYUP, 0)
    user32.keybd_event(VK_LWIN, 0, KEYUP, 0)
    return True


# ---------------------------------------------------------------------------- browsers
def find_headless_browser() -> str | None:
    candidates = []
    if paths.IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                               Path(base) / "Google/Chrome/Application/chrome.exe"]
    else:
        for name in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge"):
            if shutil.which(name):
                candidates.append(Path(shutil.which(name)))
        pw = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
        candidates += sorted(pw.glob("chromium-*/chrome-linux*/chrome"))
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return None


def screenshot_url(url: str, out: Path, width: int = 1280, height: int = 800, wait_ms: int = 1500) -> Path:
    browser = find_headless_browser()
    if not browser:
        raise RuntimeError("No Edge or Chrome browser found for web screenshots.")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
           "--no-default-browser-check", f"--window-size={width},{height}",
           f"--virtual-time-budget={wait_ms}", f"--screenshot={out}", url]
    if not paths.IS_WINDOWS and os.geteuid() == 0:
        cmd.insert(1, "--no-sandbox")
    subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"The browser did not produce a screenshot of {url}.")
    return out


# ---------------------------------------------------------------------------- window screenshots
def _windows_of_pid_tree(pid: int) -> list[int]:
    """Top-level visible windows belonging to pid or its children (Windows only)."""
    user32 = ctypes.windll.user32
    pids = {pid}
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"Get-CimInstance Win32_Process | Where-Object {{$_.ParentProcessId -eq {pid}}} | "
                              "Select-Object -ExpandProperty ProcessId"], capture_output=True, text=True,
                             timeout=20, creationflags=NO_WINDOW).stdout
        pids |= {int(x) for x in out.split() if x.isdigit()}
    except Exception:
        pass
    found: list[int] = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value in pids:
                found.append(hwnd)
        return True

    user32.EnumWindows(PROC(cb), 0)
    return found


def screenshot_command(command: str, cwd: Path, out: Path, wait_seconds: float = 4.0) -> Path:
    """Start a program, wait, capture its window (or the screen), then close the program."""
    from PIL import ImageGrab

    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(command, cwd=str(cwd), shell=True, creationflags=NEW_CONSOLE if paths.IS_WINDOWS else 0)
    try:
        time.sleep(max(1.0, min(wait_seconds, 30)))
        bbox = None
        if paths.IS_WINDOWS:
            ctypes.windll.user32.SetProcessDPIAware()
            wins = _windows_of_pid_tree(proc.pid)
            if wins:
                hwnd = wins[0]
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.6)
                import ctypes.wintypes as wt

                rect = wt.RECT()
                ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                bbox = (rect.left, rect.top, rect.right, rect.bottom)
        img = ImageGrab.grab(bbox=bbox, all_screens=paths.IS_WINDOWS)
        img.save(out)
        if proc.poll() is not None and proc.returncode not in (0, None):
            raise RuntimeError(f"The program exited with code {proc.returncode} before the screenshot.")
        return out
    finally:
        if proc.poll() is None:
            if paths.IS_WINDOWS:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                               creationflags=NO_WINDOW)
            else:
                proc.kill()


# ---------------------------------------------------------------------------- launching
def launch(command: str, cwd: Path) -> int:
    """Start the user's app in its own window and leave it running."""
    flags = NEW_CONSOLE if paths.IS_WINDOWS else 0
    proc = subprocess.Popen(command, cwd=str(cwd), shell=True, creationflags=flags,
                            start_new_session=not paths.IS_WINDOWS)
    return proc.pid


def launch_checked(command: str, cwd: Path, wait: float = 2.0) -> int:
    """Like launch, but reports an app that stops with an error straight away (missing file, crash on start)."""
    import time

    flags = NEW_CONSOLE if paths.IS_WINDOWS else 0
    proc = subprocess.Popen(command, cwd=str(cwd), shell=True, creationflags=flags,
                            start_new_session=not paths.IS_WINDOWS)
    end = time.time() + wait
    while time.time() < end and proc.poll() is None:
        time.sleep(0.1)
    if proc.returncode not in (None, 0):
        raise RuntimeError(f"the app stopped right away with error code {proc.returncode} (command: {command})")
    return proc.pid


def open_url(url: str) -> None:
    import webbrowser

    webbrowser.open(url)


def open_folder(folder: Path) -> None:
    if paths.IS_WINDOWS:
        os.startfile(str(folder))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
