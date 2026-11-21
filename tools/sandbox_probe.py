"""L47 / REQ-22 probe: do the team's tools (Python, Git, pip, pytest, claude.exe) run inside a Windows sandbox on
this PC, and does the sandbox refuse writing elsewhere and reading private files?

Investigation tool only (iteration 11): nothing in app/ uses it. Standard library + ctypes, no packages.
Run:  python tools/sandbox_probe.py [appcontainer|restricted_token ...]  -> reports/L47-probe-results.json

Everything it creates is temporary and removed at the end, also on errors: the AppContainer profile, the
access rights it adds to the tool folders, and its folders under the Windows temp folder. It never touches
the user's Documents, keys or browser data: the "private" file is a canary in a throwaway temp folder.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "reports" / "L47-probe-results.json"
INSTALL_DIR = Path(r"C:\ZZ - Projects\AI Team Lite")      # where AI Team Lite is installed on the owner's PC
PROFILE_NAME = "AITeamLite.SandboxProbe"
CANARY = "L47-canary: if you can read this, the sandbox let a private file through"

CHECKS = ["python", "git", "pip", "pytest", "claude", "write_outside_refused", "read_private_refused"]
OPTIONS = {
    "appcontainer": "AppContainer, no capabilities; read/write granted on the test folder, read+run on the tools",
    "restricted_token": "Restricted token (DISABLE_MAX_PRIVILEGE) in a job object with UI limits",
}
NO_WINDOW = 0x08000000


# ------------------------------------------------------------------ where the tools are (no side effects)

def _python_exe() -> Path:
    exe = INSTALL_DIR / "python" / "python.exe"
    return exe if exe.exists() else Path(sys.executable)


def _venv_python() -> Path | None:
    exe = ROOT / ".venv" / "Scripts" / "python.exe"
    return exe if exe.exists() else None


def _git_exe() -> str | None:
    """Same rule as app/paths.py git_exe(), applied to the installed app; then git_exe() itself."""
    bundled = INSTALL_DIR / "git" / "cmd" / "git.exe"
    if bundled.exists():
        return str(bundled)
    sys.path.insert(0, str(ROOT))
    try:
        from app import paths
        return paths.git_exe()
    except Exception:
        return shutil.which("git")
    finally:
        sys.path.remove(str(ROOT))


def _claude_exe() -> Path | None:
    """Like app/auth.py cli_path(): claude_agent_sdk/_bundled/claude.exe, in this Python or the bundled one."""
    import importlib.util
    spec = importlib.util.find_spec("claude_agent_sdk")
    roots = list(spec.submodule_search_locations or []) if spec else []
    roots.append(str(INSTALL_DIR / "python" / "Lib" / "site-packages" / "claude_agent_sdk"))
    for r in roots:
        exe = Path(r) / "_bundled" / "claude.exe"
        if exe.exists():
            return exe
    return None


def _git_root(git: str) -> Path:
    p = Path(git).parent
    return p.parent if p.name.lower() in ("cmd", "bin") else p


# ------------------------------------------------------------------ Windows calls (loaded only on a real run)

class _Win:
    def __init__(self):
        import ctypes
        import msvcrt
        from ctypes import wintypes as W
        self.c, self.W, self.msvcrt = ctypes, W, msvcrt
        k32 = self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = self.adv = ctypes.WinDLL("advapi32", use_last_error=True)
        uenv = self.uenv = ctypes.WinDLL("userenv", use_last_error=True)
        vp, sz, B, D, H = ctypes.c_void_p, ctypes.c_size_t, W.BOOL, W.DWORD, W.HANDLE

        class SI(ctypes.Structure):
            _fields_ = [("cb", D), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR), ("lpTitle", W.LPWSTR),
                        ("dwX", D), ("dwY", D), ("dwXSize", D), ("dwYSize", D), ("dwXCountChars", D),
                        ("dwYCountChars", D), ("dwFillAttribute", D), ("dwFlags", D), ("wShowWindow", W.WORD),
                        ("cbReserved2", W.WORD), ("lpReserved2", vp), ("hStdInput", H), ("hStdOutput", H),
                        ("hStdError", H)]

        class SIEX(ctypes.Structure):
            _fields_ = [("StartupInfo", SI), ("lpAttributeList", vp)]

        class PI(ctypes.Structure):
            _fields_ = [("hProcess", H), ("hThread", H), ("dwProcessId", D), ("dwThreadId", D)]

        class SECCAPS(ctypes.Structure):
            _fields_ = [("AppContainerSid", vp), ("Capabilities", vp), ("CapabilityCount", D), ("Reserved", D)]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", D), ("MinimumWorkingSetSize", sz), ("MaximumWorkingSetSize", sz),
                        ("ActiveProcessLimit", D), ("Affinity", sz), ("PriorityClass", D), ("SchedulingClass", D)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", sz),
                        ("JobMemoryLimit", sz), ("PeakProcessMemoryUsed", sz), ("PeakJobMemoryUsed", sz)]

        self.SIEX, self.PI, self.SECCAPS, self.EXTENDED = SIEX, PI, SECCAPS, EXTENDED
        for fn, args, res in [
            (k32.InitializeProcThreadAttributeList, [vp, D, D, ctypes.POINTER(sz)], B),
            (k32.UpdateProcThreadAttribute, [vp, D, sz, vp, sz, vp, vp], B),
            (k32.DeleteProcThreadAttributeList, [vp], None),
            (k32.CreateProcessW, [W.LPCWSTR, vp, vp, vp, B, D, vp, W.LPCWSTR, vp, vp], B),
            (adv.CreateProcessAsUserW, [H, W.LPCWSTR, vp, vp, vp, B, D, vp, W.LPCWSTR, vp, vp], B),
            (k32.WaitForSingleObject, [H, D], D),
            (k32.GetExitCodeProcess, [H, ctypes.POINTER(D)], B),
            (k32.TerminateProcess, [H, W.UINT], B),
            (k32.ResumeThread, [H], D),
            (k32.CloseHandle, [H], B),
            (k32.GetCurrentProcess, [], H),
            (k32.LocalFree, [vp], vp),
            (k32.CreateJobObjectW, [vp, W.LPCWSTR], H),
            (k32.SetInformationJobObject, [H, ctypes.c_int, vp, D], B),
            (k32.AssignProcessToJobObject, [H, H], B),
            (adv.OpenProcessToken, [H, D, ctypes.POINTER(H)], B),
            (adv.CreateRestrictedToken, [H, D, D, vp, D, vp, D, vp, ctypes.POINTER(H)], B),
            (adv.ConvertSidToStringSidW, [vp, ctypes.POINTER(vp)], B),
            (adv.FreeSid, [vp], vp),
            (uenv.CreateAppContainerProfile, [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, vp, D, ctypes.POINTER(vp)],
             ctypes.c_long),
            (uenv.DeriveAppContainerSidFromAppContainerName, [W.LPCWSTR, ctypes.POINTER(vp)], ctypes.c_long),
            (uenv.DeleteAppContainerProfile, [W.LPCWSTR], ctypes.c_long),
        ]:
            fn.argtypes, fn.restype = args, res

    def error(self, what: str) -> OSError:
        code = self.c.get_last_error()
        return OSError(code, f"{what} failed: {self.c.FormatError(code).strip()} (Windows error {code})")

    def spawn(self, argv, cwd, env, timeout, attrs=(), token=None, job=None):
        """Start argv[0] with STARTUPINFOEX; returns (exit code or None on timeout, combined output)."""
        c, W = self.c, self.W
        r_fd, w_fd = os.pipe()
        nul_fd = os.open(os.devnull, os.O_RDWR)
        hout, hin = self.msvcrt.get_osfhandle(w_fd), self.msvcrt.get_osfhandle(nul_fd)
        os.set_handle_inheritable(hout, True)
        os.set_handle_inheritable(hin, True)
        handles = (W.HANDLE * 2)(hout, hin)
        attrs = list(attrs) + [(0x00020002, c.addressof(handles), c.sizeof(handles))]  # HANDLE_LIST
        size = c.c_size_t()
        self.k32.InitializeProcThreadAttributeList(None, len(attrs), 0, c.byref(size))
        buf = c.create_string_buffer(size.value)
        pi = self.PI()
        try:
            if not self.k32.InitializeProcThreadAttributeList(c.addressof(buf), len(attrs), 0, c.byref(size)):
                raise self.error("InitializeProcThreadAttributeList")
            try:
                for attr, ptr, n in attrs:
                    if not self.k32.UpdateProcThreadAttribute(c.addressof(buf), 0, attr, ptr, n, None, None):
                        raise self.error("UpdateProcThreadAttribute")
                si = self.SIEX()
                si.StartupInfo.cb = c.sizeof(si)
                si.StartupInfo.dwFlags = 0x100                      # STARTF_USESTDHANDLES
                si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = hin, hout, hout
                si.lpAttributeList = c.addressof(buf)
                cmd = c.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in argv]))
                block = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
                envbuf = (c.c_wchar * len(block))(*block)
                # UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT | NO_WINDOW | SUSPENDED
                flags = 0x400 | 0x80000 | NO_WINDOW | 0x4
                args = (str(argv[0]), c.addressof(cmd), None, None, True, flags, c.addressof(envbuf), str(cwd),
                        c.addressof(si), c.addressof(pi))
                ok = (self.adv.CreateProcessAsUserW(token, *args) if token is not None
                      else self.k32.CreateProcessW(*args))
                if not ok:
                    raise self.error("CreateProcess")
            finally:
                self.k32.DeleteProcThreadAttributeList(c.addressof(buf))
        except BaseException:
            os.close(r_fd)
            raise
        finally:
            os.close(w_fd)
            os.close(nul_fd)
        chunks: list[bytes] = []

        def _read():
            while True:
                b = os.read(r_fd, 65536)
                if not b:
                    return
                chunks.append(b)
        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        try:
            if job is not None and not self.k32.AssignProcessToJobObject(job, pi.hProcess):
                err = self.error("AssignProcessToJobObject")
                self.k32.TerminateProcess(pi.hProcess, 1)
                raise err
            self.k32.ResumeThread(pi.hThread)
            code: int | None = None
            if self.k32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) == 0x102:   # WAIT_TIMEOUT
                self.k32.TerminateProcess(pi.hProcess, 1)
            else:
                out = W.DWORD()
                self.k32.GetExitCodeProcess(pi.hProcess, c.byref(out))
                code = out.value
            reader.join(15)
        finally:
            self.k32.CloseHandle(pi.hThread)
            self.k32.CloseHandle(pi.hProcess)
            if not reader.is_alive():       # a grandchild may still hold the pipe: leave it then
                os.close(r_fd)
        return code, b"".join(chunks).decode("utf-8", "replace").strip()


def _icacls(path: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["icacls", str(path), *args], capture_output=True, text=True, errors="replace",
                       creationflags=NO_WINDOW, timeout=900)
    return r.returncode, (r.stdout + r.stderr).strip()


# ------------------------------------------------------------------ the two sandboxes

class _AppContainer:
    def __init__(self, win: _Win):
        self.win, self.sid, self.sid_str, self.granted, self.profile = win, None, "", [], False

    def setup(self, work: Path, read_dirs: list[Path]) -> list[str]:
        c, uenv = self.win.c, self.win.uenv
        psid = c.c_void_p()
        hr = uenv.CreateAppContainerProfile(PROFILE_NAME, "AI Team Lite sandbox probe",
                                            "L47 probe; deleted after the run", None, 0, c.byref(psid))
        if hr & 0xFFFFFFFF == 0x800700B7:                    # already exists (left by an earlier run)
            hr = uenv.DeriveAppContainerSidFromAppContainerName(PROFILE_NAME, c.byref(psid))
        if hr != 0:
            raise OSError(f"CreateAppContainerProfile failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        self.profile, self.sid = True, psid
        s = c.c_void_p()
        if not self.win.adv.ConvertSidToStringSidW(psid, c.byref(s)):
            raise self.win.error("ConvertSidToStringSidW")
        self.sid_str = c.wstring_at(s.value)
        self.win.k32.LocalFree(s)
        self.caps = self.win.SECCAPS(AppContainerSid=psid.value)
        notes = [f"AppContainer profile {PROFILE_NAME} ({self.sid_str}), no capabilities (so no network)"]
        code, out = _icacls(work, "/grant", f"*{self.sid_str}:(OI)(CI)(M)")
        if code:
            raise OSError(f"could not give the container read/write on the test folder: {out}")
        self.granted.append(work)
        for d in read_dirs:
            code, out = _icacls(d, "/grant", f"*{self.sid_str}:(OI)(CI)(RX)")
            if code:
                notes.append(f"read+run grant on {d} FAILED: {out}")
            else:
                self.granted.append(d)
                notes.append(f"read+run granted on {d}")
        return notes

    def allow_stat(self, folder: Path) -> str:
        """Read attributes only, on this folder only (no listing, nothing inherited by its contents)."""
        code, out = _icacls(folder, "/grant", f"*{self.sid_str}:(RA)")
        if code:
            return f"read-attributes grant on {folder} FAILED: {out}"
        self.granted.append(folder)
        return f"read-attributes (only) granted on {folder}"

    def spawn(self, argv, cwd, env, timeout):
        c = self.win.c
        attr = (0x00020009, c.addressof(self.caps), c.sizeof(self.caps))   # SECURITY_CAPABILITIES
        return self.win.spawn(argv, cwd, env, timeout, attrs=[attr])

    def close(self) -> list[str]:
        notes = []
        for d in reversed(self.granted):
            code, out = _icacls(d, "/remove:g", f"*{self.sid_str}")
            notes.append(f"access removed on {d}" if code == 0 else f"could NOT remove access on {d}: {out}")
            if code == 0 and (d / "python.exe").exists():      # inherited rights really gone from a child?
                left = self.sid_str in _icacls(d / "python.exe")[1]
                notes.append(f"check on {d / 'python.exe'}: {'container SID still listed' if left else 'clean'}")
        if self.sid is not None:
            self.win.adv.FreeSid(self.sid)
        if self.profile:
            hr = self.win.uenv.DeleteAppContainerProfile(PROFILE_NAME)
            notes.append("AppContainer profile deleted" if hr == 0
                         else f"DeleteAppContainerProfile HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        return notes


class _RestrictedToken:
    def __init__(self, win: _Win):
        self.win, self.token, self.job = win, None, None

    def setup(self, work: Path, read_dirs: list[Path]) -> list[str]:
        c, W, k32, adv = self.win.c, self.win.W, self.win.k32, self.win.adv
        own = W.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x1 | 0x2 | 0x8, c.byref(own)):
            raise self.win.error("OpenProcessToken")
        try:
            tok = W.HANDLE()
            if not adv.CreateRestrictedToken(own, 0x1, 0, None, 0, None, 0, None, c.byref(tok)):  # DISABLE_MAX_PRIVILEGE
                raise self.win.error("CreateRestrictedToken")
            self.token = tok
        finally:
            k32.CloseHandle(own)
        self.job = k32.CreateJobObjectW(None, None)
        if not self.job:
            raise self.win.error("CreateJobObjectW")
        ui = W.DWORD(0xFF)                                    # JOB_OBJECT_UILIMIT_ALL
        if not k32.SetInformationJobObject(self.job, 4, c.addressof(ui), c.sizeof(ui)):
            raise self.win.error("SetInformationJobObject(UI limits)")
        ext = self.win.EXTENDED()
        ext.Basic.LimitFlags = 0x2000                         # KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(self.job, 9, c.addressof(ext), c.sizeof(ext)):
            raise self.win.error("SetInformationJobObject(kill on close)")
        return ["restricted token: all privileges except SeChangeNotify removed; user and groups unchanged",
                "job object: UI limits (clipboard, desktop, system settings, exit Windows), kill on close"]

    def allow_stat(self, folder: Path) -> str:
        return f"no grant needed on {folder}: the restricted token keeps the user's own rights"

    def spawn(self, argv, cwd, env, timeout):
        return self.win.spawn(argv, cwd, env, timeout, token=self.token, job=self.job)

    def close(self) -> list[str]:
        for h in (self.job, self.token):
            if h:
                self.win.k32.CloseHandle(h)
        return ["job object and token closed"]


# ------------------------------------------------------------------ the checks

def _tail(text: str, n: int = 3) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[-n:]) or "(no output)"


def _tool_check(box, argv, work, env, timeout) -> dict:
    try:
        code, out = box.spawn(argv, work, env, timeout)
    except OSError as exc:
        return {"status": "error", "detail": f"could not start {Path(argv[0]).name}: {exc}"}
    if code is None:
        return {"status": "error", "detail": f"no answer within {timeout} s (stopped); output: {_tail(out)}"}
    if code == 0:
        return {"status": "pass", "detail": f"exit 0: {_tail(out, 2)}"}
    errors = [ln.strip() for ln in out.splitlines() if "Error" in ln or "denied" in ln][:2]
    return {"status": "fail", "detail": f"exit {code}: {' | '.join(errors + [_tail(out, 1)])}"}


def _escape_check(box, py, script, work, env, leaked) -> dict:
    try:
        code, out = box.spawn([py, "-c", script], work, env, 60)
    except OSError as exc:
        return {"status": "error", "detail": f"could not start python: {exc}"}
    if leaked(out):
        return {"status": "fail", "detail": f"NOT refused (exit {code}): {_tail(out, 1)}"}
    if "PermissionError" in out or "Access is denied" in out:
        return {"status": "pass", "detail": f"refused: {_tail(out, 1)}"}
    return {"status": "error", "detail": f"exit {code}, unclear result: {_tail(out)}"}


def run(option: str, dry_run: bool = False) -> dict:
    """Run every check for one option. Returns {check: {"status": pass|fail|error, "detail": str}}."""
    if option not in OPTIONS:
        raise ValueError(f"unknown option {option!r}; choose from {list(OPTIONS)}")
    if dry_run:
        return {c: {"status": "error", "detail": f"dry run of {option}: nothing started, Windows not touched"}
                for c in CHECKS}
    if sys.platform != "win32":
        return {c: {"status": "error", "detail": "needs Windows"} for c in CHECKS}
    res: dict = {}
    notes: list[str] = []
    base = Path(tempfile.mkdtemp(prefix="aitl-l47-"))
    try:
        work, outside, private, tmp = base / "work", base / "outside", base / "private", base / "work" / "tmp"
        for d in (work, outside, private, tmp):
            d.mkdir(parents=True, exist_ok=True)
        canary = private / "canary.txt"
        canary.write_text(CANARY, encoding="utf-8")
        (work / "test_tiny.py").write_text("def test_tiny():\n    assert 1 + 1 == 2\n", encoding="utf-8")
        py, venv, git, claude = _python_exe(), _venv_python(), _git_exe(), _claude_exe()
        read_dirs = [py.parent] + ([_git_root(git)] if git else []) + ([ROOT / ".venv"] if venv else [])
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__")}
        path_extra = [str(py.parent), str(py.parent / "Scripts")] + ([str(Path(git).parent)] if git else [])
        env.update(TEMP=str(tmp), TMP=str(tmp), PYTHONDONTWRITEBYTECODE="1",
                   PATH=os.pathsep.join(path_extra + [os.environ.get("PATH", "")]))
        win = _Win()
        box = _AppContainer(win) if option == "appcontainer" else _RestrictedToken(win)
        try:
            notes += box.setup(work, read_dirs)
            res["python"] = _tool_check(box, [py, "--version"], work, env, 60)
            res["git"] = (_tool_check(box, [git, "--version"], work, env, 60) if git
                          else {"status": "error", "detail": "git not found"})
            res["pip"] = _tool_check(box, [py, "-m", "pip", "--version"], work, env, 120)
            res["pytest"] = _tool_check(box, [venv or py, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                              "test_tiny.py"], work, env, 180)
            # extra: pytest stats the folder above the test folder (_pytest/main.py _collect_path ->
            # gethookproxy(path.parent) -> is_file()). Allow only "read attributes" there, no listing.
            notes.append(box.allow_stat(base))
            res["pytest_parent_attributes_allowed"] = _tool_check(
                box, [venv or py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_tiny.py"], work, env, 180)
            res["claude"] = (_tool_check(box, [claude, "--version"], work, env, 120) if claude
                             else {"status": "error", "detail": "claude.exe not found (claude_agent_sdk/_bundled)"})
            target = outside / "escape.txt"
            res["write_outside_refused"] = _escape_check(
                box, py, f"open({str(target)!r}, 'w').write('x'); print('WROTE')", work, env,
                lambda out: target.exists())
            res["read_private_refused"] = _escape_check(
                box, py, f"print(open({str(canary)!r}).read())", work, env, lambda out: CANARY in out)
        finally:
            notes += box.close()
    except Exception as exc:
        for c in CHECKS:
            res.setdefault(c, {"status": "error", "detail": f"setup failed: {exc}"})
    finally:
        shutil.rmtree(base, ignore_errors=True)
        notes.append("temp folders deleted" if not base.exists() else f"temp folder left behind: {base}")
    bad = [n for n in notes if "FAILED" in n or "NOT" in n or "still listed" in n or "left behind" in n]
    res["setup_and_cleanup"] = {"status": "error" if bad else "pass", "detail": "; ".join(notes)}
    return res


def _machine() -> dict:
    info = {"platform": sys.platform}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            for name in ("EditionID", "DisplayVersion", "CurrentBuild"):
                info[name] = str(winreg.QueryValueEx(k, name)[0])
    except OSError as exc:
        info["error"] = str(exc)
    return info


def main(argv: list[str] | None = None) -> int:
    wanted = (argv if argv is not None else sys.argv[1:]) or ["appcontainer"]
    data = {"ran_at": _dt.datetime.now().isoformat(timespec="seconds"), "machine": _machine(),
            "probe_python": sys.executable, "options": {}}
    for opt in wanted:
        data["options"][opt] = run(opt)
    app = data["options"].get("appcontainer", {})
    if "restricted_token" not in data["options"] and any(app.get(c, {}).get("status") != "pass" for c in CHECKS):
        data["options"]["restricted_token"] = run("restricted_token")
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for opt, checks in data["options"].items():
        for name, e in checks.items():
            print(f"{opt:17} {name:22} {e['status']:5} {e['detail'][:150]}")
    print(f"saved {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
