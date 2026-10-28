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


# ---------------------------------------------------------------------------- no flashing windows (L32)
CREATE_NO_WINDOW = 0x08000000


def hide_engine_console(force: bool = False) -> bool:
    """Start Claude's engine (claude.exe) without a console window. The app itself has no console (pythonw), so
    without this every command the engine runs (PowerShell, git, the tests) could open its own console box that
    flashes on screen; with it they share one invisible console. The engine is started through anyio, so the flag is
    added there; nothing else in the app uses anyio to start programs. Returns True when installed."""
    if not (paths.IS_WINDOWS or force):
        return False
    import anyio
    original = anyio.open_process
    if getattr(original, "hides_console", False):
        return True

    async def open_process(*args, **kwargs):
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW
        return await original(*args, **kwargs)

    open_process.hides_console = True
    anyio.open_process = open_process
    return True


TEST_DESKTOP = "AITeamLiteTests"


def run_on_hidden_desktop(command: str, cwd: Path, env: dict, timeout: int) -> tuple[int, str] | None:
    """L32: run a test command on a second Windows desktop that is never shown, so the windows a test opens (the
    prototype's own screens, console boxes) appear there instead of over the user's work. Returns (exit code, output)
    with exit code -1 on timeout, or None when Windows does not allow it, so the caller runs the command the normal way.
    Everything the command starts stays in a job object and is closed with it, also on timeout."""
    if not paths.IS_WINDOWS:
        return None
    import msvcrt
    import tempfile
    from ctypes import wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                    ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                    ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                    ("hStdError", wt.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD),
                    ("dwThreadId", wt.DWORD)]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    user32.CreateDesktopW.restype = wt.HANDLE
    user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    kernel32.CreateJobObjectW.restype = wt.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    kernel32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
                                        ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
                                        ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD

    desk = user32.CreateDesktopW(TEST_DESKTOP, None, None, 0, 0x10000000, None)   # GENERIC_ALL; opens it if it exists
    if not desk:
        return None
    fd, log_path = tempfile.mkstemp(prefix="aiteam-tests-", suffix=".log")
    out_handle = msvcrt.get_osfhandle(fd)
    os.set_handle_inheritable(out_handle, True)
    null = open(os.devnull, "rb")   # noqa: SIM115  (closed below)
    in_handle = msvcrt.get_osfhandle(null.fileno())
    os.set_handle_inheritable(in_handle, True)
    job = kernel32.CreateJobObjectW(None, None)
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = "WinSta0\\" + TEST_DESKTOP
    si.dwFlags = 0x100 | 0x1   # STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
    si.wShowWindow = 0         # SW_HIDE
    si.hStdInput, si.hStdOutput, si.hStdError = in_handle, out_handle, out_handle
    pi = PROCESS_INFORMATION()
    block = "".join(f"{k}={v}\0" for k, v in env.items()) + "\0"
    env_buf = ctypes.create_unicode_buffer(block, len(block))
    cmdline = ctypes.create_unicode_buffer(f'cmd.exe /d /s /c "{command}"')
    flags = 0x4 | 0x400 | CREATE_NO_WINDOW   # CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | no console window
    code = None
    try:
        if not kernel32.CreateProcessW(None, cmdline, None, None, True, flags, env_buf, str(cwd), ctypes.byref(si),
                                       ctypes.byref(pi)):
            return None
        if job:
            kernel32.AssignProcessToJobObject(job, pi.hProcess)
        kernel32.ResumeThread(pi.hThread)
        if kernel32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) == 0x102:   # WAIT_TIMEOUT
            kernel32.TerminateJobObject(job, 1) if job else kernel32.TerminateProcess(pi.hProcess, 1)
            code = -1
        else:
            exit_code = wt.DWORD()
            kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(exit_code))
            code = int(exit_code.value)
    finally:
        for h in (pi.hThread, pi.hProcess, job):
            if h:
                kernel32.CloseHandle(h)
        user32.CloseDesktop(desk)
        null.close()
        os.close(fd)
    try:
        output = Path(log_path).read_text(encoding="utf-8", errors="replace")
    finally:
        Path(log_path).unlink(missing_ok=True)
    return code, output


# ---------------------------------------------------------------------------- clipboard
def read_clipboard() -> str:
    """The text on the Windows clipboard (L28: right-click > Paste when the window may not read it itself)."""
    if not paths.IS_WINDOWS:
        return ""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-Clipboard -Raw"],
                         capture_output=True, timeout=10, creationflags=NO_WINDOW)
    return out.stdout.decode("utf-8", errors="replace").removesuffix("\r\n")


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
def _descendants(pid: int) -> set[int]:
    """pid and every process it started, however deep (run.bat -> cmd -> python -> app)."""
    pids = {pid}
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
                             capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout
        parent = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                parent[int(parts[0])] = int(parts[1])
        grew = True
        while grew:
            grew = False
            for child, par in parent.items():
                if par in pids and child not in pids:
                    pids.add(child)
                    grew = True
    except Exception:  # noqa: BLE001
        pass
    return pids


def _visible_windows() -> list[tuple[int, int]]:
    """(window handle, process id) of every visible top-level window with a title (Windows only)."""
    user32 = ctypes.windll.user32
    found: list[tuple[int, int]] = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            found.append((hwnd, owner.value))
        return True

    user32.EnumWindows(PROC(cb), 0)
    return found


def find_app_window(pid: int, before: set[int]) -> int | None:
    """The started program's window: one owned by its process tree, else a window that appeared after the launch."""
    wins = _visible_windows()
    tree = _descendants(pid)
    own = [h for h, owner in wins if owner in tree]
    if own:
        return own[0]
    new = [h for h, _owner in wins if h not in before]
    return new[0] if len(new) == 1 else None


def capture_window(hwnd: int):
    """An image of that one window's own content (PrintWindow, full content): other windows on top of it or on other
    screens never appear. Returns a PIL image."""
    import ctypes.wintypes as wt

    from PIL import Image

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError("The app's window has no size (it may be minimised).")
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, 2):   # 2 = PW_RENDERFULLCONTENT
            raise RuntimeError("Windows could not draw the app's window.")

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


class NoWindow(RuntimeError):
    """The started program showed no window AI Team Lite could recognise: no picture is taken (L19)."""


def screenshot_command(command: str, cwd: Path, out: Path, wait_seconds: float = 4.0) -> Path:
    """Start a program, wait, capture ONLY its window, then close the program. Never captures the whole desktop:
    other screens (a film, mail, anything private) must never end up in a screenshot that the AI also sees (L19)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if not paths.IS_WINDOWS:
        raise NoWindow("Window screenshots are only available on Windows.")
    ctypes.windll.user32.SetProcessDPIAware()
    before = {h for h, _ in _visible_windows()}
    proc = subprocess.Popen(command, cwd=str(cwd), shell=True, creationflags=NEW_CONSOLE)
    try:
        time.sleep(max(1.0, min(wait_seconds, 30)))
        hwnd = find_app_window(proc.pid, before)
        if hwnd is None:
            if proc.poll() is not None and proc.returncode not in (0, None):
                raise RuntimeError(f"The program exited with code {proc.returncode} before the screenshot.")
            raise NoWindow("I couldn't find the app's window, so no screenshot was taken (your screens are never "
                           "captured as a whole). Check that the command opens a window, or give it more wait_seconds.")
        capture_window(hwnd).save(out)
        return out
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)


# ---------------------------------------------------------------------------- launching
def launch(command: str, cwd: Path) -> int:
    """Start the user's app in its own window and leave it running."""
    flags = NEW_CONSOLE if paths.IS_WINDOWS else 0
    proc = subprocess.Popen(command, cwd=str(cwd), shell=True, creationflags=flags,
                            start_new_session=not paths.IS_WINDOWS)
    return proc.pid


def launch_checked(command: str, cwd: Path, wait: float = 2.0) -> int:
    """Like launch, but reports an app that stops with an error straight away (missing file, crash on start)."""
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
    if paths.IS_WINDOWS and url.startswith("ms-settings:"):
        os.startfile(url)  # noqa: S606  (opens the Windows Settings page)
        return
    import webbrowser

    webbrowser.open(url)


def open_folder(folder: Path) -> None:
    if paths.IS_WINDOWS:
        os.startfile(str(folder))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
