"""Programs the team may need on the PC: Ollama (local brains for the council) and Archify (diagrams).

Everything here detects first and installs only after the user approved it in the app. Downloads come only from the
official sources, and Windows installers are checked for a valid signature from the expected publisher before they run.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import paths

NO_WINDOW = 0x08000000 if paths.IS_WINDOWS else 0
DETACHED = 0x00000008 if paths.IS_WINDOWS else 0
Progress = Callable[[str, float | None], None]  # (message, fraction 0..1 or None)

OLLAMA_URL = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_BASE = os.environ.get("AITEAM_OLLAMA_URL", "http://localhost:11434")


def _noop(msg: str, frac: float | None = None) -> None:
    pass


def run(cmd: list[str], timeout: float = 120, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None,
                          env={**os.environ, "PATH": paths.tool_path(), **(env or {})}, creationflags=NO_WINDOW)


def which(name: str, extra: list[Path] | None = None) -> str | None:
    for d in extra or []:
        for cand in (d / name, d / f"{name}.exe", d / f"{name}.cmd"):
            if cand.exists():
                return str(cand)
    return shutil.which(name, path=paths.tool_path())
def download(url: str, dest: Path, progress: Progress = _noop, label: str = "Downloading") -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "AI-Team-Lite"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            got += len(chunk)
            progress(f"{label}… {got // (1 << 20)} MB" + (f" of {total // (1 << 20)} MB" if total else ""),
                     got / total if total else None)
    return dest


def verify_signature(exe: Path, publisher: str) -> None:
    """Windows only: the installer must carry a valid Authenticode signature from the expected publisher."""
    if not paths.IS_WINDOWS:
        return
    ps = (f"$s = Get-AuthenticodeSignature -LiteralPath '{exe}'; "
          "Write-Output ($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)")
    out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=60).stdout.strip()
    status, _, subject = out.partition("|")
    if status != "Valid" or publisher.lower() not in subject.lower():
        raise RuntimeError(f"The downloaded installer is not signed by {publisher} (signature: {out or 'none'}). "
                           "It was not run.")


# ---------------------------------------------------------------------------- PC capacity (for local models)
def memory_gb() -> float:
    try:
        if paths.IS_WINDOWS:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / (1 << 30), 1)
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                return round(int(line.split()[1]) / (1 << 20), 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def gpu_memory_gb() -> float:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0.0
    try:
        out = run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"], timeout=15).stdout
        return round(max(int(x) for x in out.split() if x.strip().isdigit()) / 1024, 1)
    except Exception:  # noqa: BLE001
        return 0.0
# ---------------------------------------------------------------------------- Ollama (item 36)
_ollama_proc: subprocess.Popen | None = None


def ollama_exe() -> str | None:
    extra = []
    if os.environ.get("LOCALAPPDATA"):
        extra.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama")
    return which("ollama", extra)


def _ollama_get(path: str, timeout: float = 3) -> dict | None:
    try:
        with urllib.request.urlopen(OLLAMA_BASE + path, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001
        return None


def ollama_status() -> dict:
    tags = _ollama_get("/api/tags")
    models = sorted(m.get("name", "") for m in (tags or {}).get("models", []))
    exe = ollama_exe()
    state = "running" if tags is not None else ("stopped" if exe else "not installed")
    return {"state": state, "installed": bool(exe) or tags is not None, "models": models,
            "started_by_app": _ollama_proc is not None and _ollama_proc.poll() is None}


def ollama_start(wait: float = 20) -> dict:
    global _ollama_proc
    st = ollama_status()
    if st["state"] == "running":
        return st
    exe = ollama_exe()
    if not exe:
        raise RuntimeError("Ollama is not installed.")
    _ollama_proc = subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=NO_WINDOW | DETACHED, start_new_session=not paths.IS_WINDOWS)
    end = time.time() + wait
    while time.time() < end:
        if _ollama_get("/api/tags", 1) is not None:
            return ollama_status()
        time.sleep(0.5)
    raise RuntimeError("Ollama was started but does not answer yet. Try again in a moment.")


def ollama_stop_if_started() -> None:
    global _ollama_proc
    if _ollama_proc is not None and _ollama_proc.poll() is None:
        _ollama_proc.terminate()
    _ollama_proc = None


def ollama_install(progress: Progress = _noop) -> None:
    if not paths.IS_WINDOWS:
        raise RuntimeError("Automatic installation of Ollama is only available on Windows.")
    winget = shutil.which("winget")
    if winget:
        progress("Installing Ollama with winget…", None)
        r = run([winget, "install", "--id", "Ollama.Ollama", "-e", "--silent", "--accept-package-agreements",
                 "--accept-source-agreements"], timeout=1800)
        if r.returncode == 0 or ollama_exe():
            return
    exe = Path(tempfile.mkdtemp()) / "OllamaSetup.exe"
    download(OLLAMA_URL, exe, progress, "Downloading Ollama")
    verify_signature(exe, "Ollama")
    progress("Installing Ollama…", None)
    r = subprocess.run([str(exe), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"], timeout=1800, creationflags=NO_WINDOW)
    if r.returncode != 0 and not ollama_exe():
        raise RuntimeError(f"The Ollama installer ended with code {r.returncode}.")


def ollama_pull(model: str, progress: Progress = _noop) -> None:
    if not re.fullmatch(r"[\w.:/\-]{1,80}", model):
        raise ValueError("Invalid model name")
    req = urllib.request.Request(OLLAMA_BASE + "/api/pull", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=3600) as r:
        for line in r:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                raise RuntimeError(ev["error"])
            total, done = ev.get("total"), ev.get("completed")
            progress(f"{ev.get('status', 'downloading')} {model}" +
                     (f" {done // (1 << 20)} / {total // (1 << 20)} MB" if total and done else ""),
                     done / total if total and done else None)


# ---------------------------------------------------------------------------- Python tools in their own venv
# ---------------------------------------------------------------------------- Archify diagrams (item 78)
# Archify (MIT, github.com/tt-a1i/archify) turns a typed JSON description into a validated, self-contained,
# interactive HTML diagram. The team writes the JSON from the code; Archify checks it and draws it (no AI involved
# in the drawing). It runs on Node.js, which the installer ships in "node\".
def node_exe() -> str | None:
    bundled = paths.install_dir() / "node" / ("node.exe" if paths.IS_WINDOWS else "node")
    return str(bundled) if bundled.exists() else which("node")


def archify_dir() -> Path | None:
    for d in (os.environ.get("AITEAM_ARCHIFY"), paths.install_dir() / "archify"):
        if d and (Path(d) / "bin" / "archify.mjs").exists():
            return Path(d)
    return None


def archify_ready() -> bool:
    return bool(node_exe() and archify_dir()) and archify_works()


_archify_ok: dict = {}


def archify_works() -> bool:
    """Once per run: can Archify really draw on this PC? Draws its own example in a temporary folder and checks it.
    If not, the Diagrams feature stays hidden instead of failing in the middle of a project."""
    if not (node_exe() and archify_dir()):
        return False
    key = (node_exe(), str(archify_dir()))
    if key not in _archify_ok:
        ok = False
        try:
            ex = archify_dir() / "examples" / "checkout-platform.base.architecture.json"
            with tempfile.TemporaryDirectory(prefix="aiteam-archify-") as t:
                d = Path(t) / "selftest"; d.mkdir()
                (d / "c.json").write_bytes(ex.read_bytes())
                run([node_exe(), str(archify_dir() / "bin" / "archify.mjs"), "finalize", "architecture",
                     str(d / "c.json"), str(d / "c.html")], timeout=90, cwd=d)
                r = run([node_exe(), str(archify_dir() / "bin" / "archify.mjs"), "check", str(d / "c.html"), "--json"],
                        timeout=60, cwd=d)
                ok = r.returncode == 0
        except Exception:
            ok = False
        _archify_ok[key] = ok
    return _archify_ok[key]


def archify_cmd() -> str:
    """How the team runs it (quoted, for a shell)."""
    return f'"{node_exe()}" "{archify_dir() / "bin" / "archify.mjs"}"'


def _archify(args: list[str], cwd: Path, timeout: float = 120) -> subprocess.CompletedProcess:
    return run([node_exe(), str(archify_dir() / "bin" / "archify.mjs"), *args], timeout=timeout, cwd=cwd)


def archify_check(html: Path, project: Path | None = None) -> tuple[bool, str]:
    """Is this a valid Archify diagram (structure and provenance)? Runs from the project folder."""
    if not archify_ready():
        return False, "Archify is not available"
    r = _archify(["check", str(html), "--json"], cwd=project or html.parent, timeout=60)
    return r.returncode == 0, (r.stdout or r.stderr)[-400:]


def archify_compare(base: Path, head: Path, out: Path, project: Path | None = None) -> bool:
    """Architecture only: a diagram of what changed between two versions (added / removed / moved / rerouted)."""
    if not archify_ready():
        return False
    args = ["compare", "architecture", str(base), str(head), str(out)]
    if project:
        args += ["--repo-root", str(project)]
    r = _archify(args, cwd=project or out.parent, timeout=120)
    return r.returncode == 0 and out.exists()
