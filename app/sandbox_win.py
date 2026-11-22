"""Windows calls behind the sandbox (REQ-22): AppContainer profiles and starting a program inside one.

Built from the proof in tools/sandbox_probe.py (iteration 11), plus: the network capabilities (the network stays
open), a job object that closes everything the program started, an optional hidden desktop (L32) and Popen-like
waiting. Only app/sandbox.py uses this file; it is loaded on Windows only.
"""
from __future__ import annotations

import ctypes
import msvcrt
import os
import subprocess
import threading
from ctypes import wintypes as W

NO_WINDOW = 0x08000000
NEW_CONSOLE = 0x00000010
_SUSPENDED, _UNICODE_ENV, _EXTENDED = 0x4, 0x400, 0x80000
_ATTR_HANDLE_LIST, _ATTR_SECURITY_CAPS = 0x00020002, 0x00020009
_EXISTS = 0x800700B7
_WAIT_TIMEOUT = 0x102
# internetClient, internetClientServer, privateNetworkClientServer: the network stays open (ARCHITECTURE.md)
NETWORK_CAPS = ("S-1-15-3-1", "S-1-15-3-2", "S-1-15-3-3")
vp, sz = ctypes.c_void_p, ctypes.c_size_t


class _SI(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR), ("lpTitle", W.LPWSTR),
                ("dwX", W.DWORD), ("dwY", W.DWORD), ("dwXSize", W.DWORD), ("dwYSize", W.DWORD),
                ("dwXCountChars", W.DWORD), ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD),
                ("dwFlags", W.DWORD), ("wShowWindow", W.WORD), ("cbReserved2", W.WORD), ("lpReserved2", vp),
                ("hStdInput", W.HANDLE), ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]


class _SIEX(ctypes.Structure):
    _fields_ = [("StartupInfo", _SI), ("lpAttributeList", vp)]


class _PI(ctypes.Structure):
    _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]


class _SidAttr(ctypes.Structure):
    _fields_ = [("Sid", vp), ("Attributes", W.DWORD)]


class _SecCaps(ctypes.Structure):
    _fields_ = [("AppContainerSid", vp), ("Capabilities", vp), ("CapabilityCount", W.DWORD), ("Reserved", W.DWORD)]


class _Basic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", sz), ("MaximumWorkingSetSize", sz),
                ("ActiveProcessLimit", W.DWORD), ("Affinity", sz), ("PriorityClass", W.DWORD),
                ("SchedulingClass", W.DWORD)]


class _Extended(ctypes.Structure):
    _fields_ = [("Basic", _Basic), ("Io", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", sz), ("JobMemoryLimit", sz),
                ("PeakProcessMemoryUsed", sz), ("PeakJobMemoryUsed", sz)]


class _Trustee(ctypes.Structure):
    _fields_ = [("pMultipleTrustee", vp), ("MultipleTrusteeOperation", ctypes.c_int), ("TrusteeForm", ctypes.c_int),
                ("TrusteeType", ctypes.c_int), ("ptstrName", vp)]


class _ExplicitAccess(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", W.DWORD), ("grfAccessMode", ctypes.c_int), ("grfInheritance", W.DWORD),
                ("Trustee", _Trustee)]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)
uenv = ctypes.WinDLL("userenv", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)
B, D, H = W.BOOL, W.DWORD, W.HANDLE
for _fn, _args, _res in [
    (k32.InitializeProcThreadAttributeList, [vp, D, D, ctypes.POINTER(sz)], B),
    (k32.UpdateProcThreadAttribute, [vp, D, sz, vp, sz, vp, vp], B),
    (k32.DeleteProcThreadAttributeList, [vp], None),
    (k32.CreateProcessW, [W.LPCWSTR, vp, vp, vp, B, D, vp, W.LPCWSTR, vp, vp], B),
    (k32.WaitForSingleObject, [H, D], D),
    (k32.GetExitCodeProcess, [H, ctypes.POINTER(D)], B),
    (k32.TerminateProcess, [H, W.UINT], B),
    (k32.ResumeThread, [H], D),
    (k32.CloseHandle, [H], B),
    (k32.LocalFree, [vp], vp),
    (k32.GetStdHandle, [D], H),
    (k32.CreateJobObjectW, [vp, W.LPCWSTR], H),
    (k32.SetInformationJobObject, [H, ctypes.c_int, vp, D], B),
    (k32.AssignProcessToJobObject, [H, H], B),
    (k32.TerminateJobObject, [H, W.UINT], B),
    (adv.ConvertSidToStringSidW, [vp, ctypes.POINTER(vp)], B),
    (adv.ConvertStringSidToSidW, [W.LPCWSTR, ctypes.POINTER(vp)], B),
    (adv.FreeSid, [vp], vp),
    (adv.LookupAccountSidW, [W.LPCWSTR, vp, W.LPWSTR, ctypes.POINTER(D), W.LPWSTR, ctypes.POINTER(D),
                             ctypes.POINTER(D)], B),
    (adv.GetSecurityInfo, [H, ctypes.c_int, D, vp, vp, ctypes.POINTER(vp), vp, ctypes.POINTER(vp)], D),
    (adv.SetSecurityInfo, [H, ctypes.c_int, D, vp, vp, vp, vp], D),
    (adv.SetEntriesInAclW, [W.ULONG, vp, vp, ctypes.POINTER(vp)], D),
    (uenv.CreateAppContainerProfile, [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, vp, D, ctypes.POINTER(vp)], ctypes.c_long),
    (uenv.DeriveAppContainerSidFromAppContainerName, [W.LPCWSTR, ctypes.POINTER(vp)], ctypes.c_long),
    (uenv.DeleteAppContainerProfile, [W.LPCWSTR], ctypes.c_long),
    (u32.CreateDesktopW, [W.LPCWSTR, W.LPCWSTR, vp, D, D, vp], H),
    (u32.CloseDesktop, [H], B),
    (u32.GetThreadDesktop, [D], H),
    (u32.GetUserObjectInformationW, [H, ctypes.c_int, vp, D, ctypes.POINTER(D)], B),
    (k32.GetCurrentThreadId, [], D),
]:
    _fn.argtypes, _fn.restype = _args, _res


def _error(what: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{what} failed: {ctypes.FormatError(code).strip()} (Windows error {code})")


# ------------------------------------------------------------------ profiles
def profile_sid(name: str) -> str:
    """Create the AppContainer profile (or find the one left by an earlier run); its SID as text."""
    psid = vp()
    hr = uenv.CreateAppContainerProfile(name, "AI Team Lite sandbox", "Commands of one AI Team Lite project", None, 0,
                                        ctypes.byref(psid))
    if hr & 0xFFFFFFFF == _EXISTS:
        hr = uenv.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(psid))
    if hr != 0:
        raise OSError(f"CreateAppContainerProfile failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
    try:
        text = vp()
        if not adv.ConvertSidToStringSidW(psid, ctypes.byref(text)):
            raise _error("ConvertSidToStringSidW")
        try:
            return ctypes.wstring_at(text.value)
        finally:
            k32.LocalFree(text)
    finally:
        adv.FreeSid(psid)


def delete_profile(name: str) -> bool:
    return uenv.DeleteAppContainerProfile(name) == 0


def _sid(text: str) -> vp:
    p = vp()
    if not adv.ConvertStringSidToSidW(text, ctypes.byref(p)):
        raise _error(f"ConvertStringSidToSidW({text})")
    return p


# ------------------------------------------------------------------ hidden desktop (L32)
def open_desktop(name: str, sid: str):
    """Open (or create) a desktop that is never shown and let the container use it. Returns its handle."""
    desk = u32.CreateDesktopW(name, None, None, 0, 0x10000000, None)   # GENERIC_ALL; opens it if it exists
    if not desk:
        raise _error("CreateDesktopW")
    psid, old, sd, new = _sid(sid), vp(), vp(), vp()
    try:
        if adv.GetSecurityInfo(desk, 7, 4, None, None, ctypes.byref(old), None, ctypes.byref(sd)):   # window, DACL
            raise OSError("GetSecurityInfo on the test desktop failed")
        ea = _ExplicitAccess(0x10000000, 1, 0, _Trustee(None, 0, 0, 0, psid.value))   # GENERIC_ALL, GRANT, by SID
        if adv.SetEntriesInAclW(1, ctypes.byref(ea), old, ctypes.byref(new)):
            raise OSError("SetEntriesInAclW on the test desktop failed")
        if adv.SetSecurityInfo(desk, 7, 4, None, None, new, None):
            raise OSError("SetSecurityInfo on the test desktop failed")
        return desk
    except BaseException:
        u32.CloseDesktop(desk)
        raise
    finally:
        for p in (new, sd, psid):
            if p.value:
                k32.LocalFree(p)


def current_desktop() -> str:
    """The name of the desktop this thread is on ("Default" is the one the user sees); "" if unknown."""
    desk = u32.GetThreadDesktop(k32.GetCurrentThreadId())
    buf, n = ctypes.create_unicode_buffer(256), D()
    if not desk or not u32.GetUserObjectInformationW(desk, 2, buf, ctypes.sizeof(buf), ctypes.byref(n)):   # UOI_NAME
        return ""
    return buf.value


def close_desktop(desk) -> None:
    if desk:
        u32.CloseDesktop(desk)


# ------------------------------------------------------------------ jobs
def kill_on_close_job():
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise _error("CreateJobObjectW")
    ext = _Extended()
    ext.Basic.LimitFlags = 0x2000                                   # KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.addressof(ext), ctypes.sizeof(ext)):
        k32.CloseHandle(job)
        raise _error("SetInformationJobObject")
    return job


def std_handles() -> tuple[int, int, int]:
    """This process's own stdin, stdout and stderr handles (the launcher passes them on to the engine);
    0 for one that is missing."""
    out = []
    for n in (-10, -11, -12):
        h = k32.GetStdHandle(n & 0xFFFFFFFF) or 0
        out.append(0 if h in (0, -1, 2 ** 64 - 1, 2 ** 32 - 1) else int(h))
    return out[0], out[1], out[2]


def account_name(sid_text: str) -> str:
    """The name Windows (in its own language) shows for a SID, as icacls prints it; "" if unknown."""
    psid = _sid(sid_text)
    try:
        name, dom = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        n, d, use = D(256), D(256), D()
        if not adv.LookupAccountSidW(None, psid, name, ctypes.byref(n), dom, ctypes.byref(d), ctypes.byref(use)):
            return ""
        return f"{dom.value}\\{name.value}" if dom.value else name.value
    finally:
        k32.LocalFree(psid)


# ------------------------------------------------------------------ starting a program
def spawn(cmdline: str, cwd: str, env: dict, sid: str, *, std: tuple[int, int, int] | None = None,
          new_console: bool = False, desktop: str | None = None, job=None) -> tuple[int, int]:
    """Start cmdline inside the AppContainer `sid`, suspended, put it in `job`, resume it.
    std: (stdin, stdout, stderr) handles the program gets (inherited, nothing else is). Returns (process handle, pid)."""
    psid = _sid(sid)
    caps_sids = [_sid(s) for s in NETWORK_CAPS]
    try:
        caps = (_SidAttr * len(caps_sids))(*[_SidAttr(s.value, 0x4) for s in caps_sids])   # SE_GROUP_ENABLED
        sec = _SecCaps(psid.value, ctypes.addressof(caps), len(caps_sids), 0)
        attrs = [(_ATTR_SECURITY_CAPS, ctypes.addressof(sec), ctypes.sizeof(sec))]
        handles = None
        if std:
            wanted = list(dict.fromkeys(h for h in std if h))
            for h in wanted:
                os.set_handle_inheritable(h, True)
            handles = (W.HANDLE * len(wanted))(*wanted)
            attrs.append((_ATTR_HANDLE_LIST, ctypes.addressof(handles), ctypes.sizeof(handles)))
        size = sz()
        k32.InitializeProcThreadAttributeList(None, len(attrs), 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not k32.InitializeProcThreadAttributeList(ctypes.addressof(buf), len(attrs), 0, ctypes.byref(size)):
            raise _error("InitializeProcThreadAttributeList")
        try:
            for attr, ptr, n in attrs:
                if not k32.UpdateProcThreadAttribute(ctypes.addressof(buf), 0, attr, ptr, n, None, None):
                    raise _error("UpdateProcThreadAttribute")
            si = _SIEX()
            si.StartupInfo.cb = ctypes.sizeof(si)
            if desktop:
                si.StartupInfo.lpDesktop = desktop
                si.StartupInfo.dwFlags |= 0x1                       # STARTF_USESHOWWINDOW, SW_HIDE
            if std:
                si.StartupInfo.dwFlags |= 0x100                     # STARTF_USESTDHANDLES
                si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = std
            si.lpAttributeList = ctypes.addressof(buf)
            cmd = ctypes.create_unicode_buffer(cmdline)
            block = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
            envbuf = ctypes.create_unicode_buffer(block, len(block))
            flags = _UNICODE_ENV | _EXTENDED | _SUSPENDED | (NEW_CONSOLE if new_console else NO_WINDOW)
            pi = _PI()
            if not k32.CreateProcessW(None, ctypes.addressof(cmd), None, None, bool(std), flags,
                                      ctypes.addressof(envbuf), str(cwd), ctypes.addressof(si), ctypes.addressof(pi)):
                raise _error("CreateProcess in the sandbox")
        finally:
            k32.DeleteProcThreadAttributeList(ctypes.addressof(buf))
    finally:
        for s in [psid, *caps_sids]:
            k32.LocalFree(s)
    try:
        if job and not k32.AssignProcessToJobObject(job, pi.hProcess):
            err = _error("AssignProcessToJobObject")
            k32.TerminateProcess(pi.hProcess, 1)
            k32.CloseHandle(pi.hProcess)
            raise err
        k32.ResumeThread(pi.hThread)
    finally:
        k32.CloseHandle(pi.hThread)
    return pi.hProcess, int(pi.dwProcessId)


def wait(hproc, timeout: float | None) -> int | None:
    """The exit code, or None when it is still running after timeout seconds."""
    ms = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
    if k32.WaitForSingleObject(hproc, ms) == _WAIT_TIMEOUT:
        return None
    code = D()
    k32.GetExitCodeProcess(hproc, ctypes.byref(code))
    return int(code.value)


class Process:
    """A program running in the sandbox, used like subprocess.Popen: poll, wait, communicate, kill."""

    def __init__(self, args, cmdline: str, cwd: str, env: dict, sid: str, *, capture: bool = True,
                 merge: bool = False, new_console: bool = False, desktop: str | None = None):
        self.args, self.returncode = args, None
        self._job = kill_on_close_job() if capture else None
        self._h, self._desk = None, None
        if not desktop and new_console:
            # a window of its own opens on the desktop this app is on; on any desktop but the normal one the box has
            # no right to it and the program hangs before it starts, so the box is given that desktop too
            here = current_desktop()
            desktop = here if here and here.lower() != "default" else None
        if desktop:
            try:
                self._desk = open_desktop(desktop, sid)
            except OSError:                  # L32 is a comfort: without it the program still runs, protected
                self._desk = None
        self._readers: list[tuple[threading.Thread, list[bytes]]] = []
        fds: list[int] = []
        try:
            std = None
            if capture:
                nul = os.open(os.devnull, os.O_RDWR)
                fds.append(nul)
                outs = []
                for _ in range(1 if merge else 2):
                    r, w = os.pipe()
                    fds.append(w)
                    outs.append(w)
                    self._readers.append(self._reader(r))
                hs = [msvcrt.get_osfhandle(f) for f in (nul, *outs)]
                std = (hs[0], hs[1], hs[-1])
            self._h, self.pid = spawn(cmdline, cwd, env, sid, std=std, new_console=new_console,
                                      desktop=f"WinSta0\\{desktop}" if self._desk else None, job=self._job)
        except BaseException:
            self._close_handles()
            raise
        finally:
            for f in fds:                    # the child has its own copies now
                os.close(f)

    @staticmethod
    def _reader(fd: int):
        chunks: list[bytes] = []

        def run():
            try:
                while True:
                    b = os.read(fd, 65536)
                    if not b:
                        return
                    chunks.append(b)
            except OSError:
                pass
            finally:
                os.close(fd)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t, chunks

    def poll(self) -> int | None:
        if self.returncode is None and self._h:
            self.returncode = wait(self._h, 0)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = wait(self._h, timeout)
            if self.returncode is None:
                raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def kill(self) -> None:
        if self._job:
            k32.TerminateJobObject(self._job, 1)
        elif self._h and self.returncode is None:
            k32.TerminateProcess(self._h, 1)

    terminate = kill

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.wait(timeout)
        self._close_handles()                # closes the job: whatever the program left running stops too
        texts = []
        for t, chunks in self._readers:
            t.join(15)
            texts.append(b"".join(chunks).decode("utf-8", "replace"))
        return (texts[0] if texts else ""), (texts[1] if len(texts) > 1 else "")

    def _close_handles(self) -> None:
        for name in ("_job", "_h"):
            h = getattr(self, name, None)
            if h:
                k32.CloseHandle(h)
                setattr(self, name, None)
        close_desktop(getattr(self, "_desk", None))
        self._desk = None

    def __del__(self):
        try:
            self._close_handles()            # a launched app keeps running; only our handle goes
        except Exception:  # noqa: BLE001
            pass
