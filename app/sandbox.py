"""REQ-22: the team's commands run inside a Windows AppContainer (the "sandbox").

A command in the box may change files only in the project folder and cannot read private files elsewhere (keys,
documents). The network stays open. One AppContainer profile per project; the box may touch (access_list):
  - the project folder: read and write
  - the folder that holds it: read attributes only (pytest looks there), nothing inside it
  - Python (the bundled one, the one in use and the base Python of a virtual environment) and Git: read and run.
    These program folders are opened to every app container ("ALL APPLICATION PACKAGES"), once, like Windows does
    for C:\\Program Files: so a new project does not rewrite the rights of thousands of files each time.
  - a temp folder of its own (TEMP and TMP point to it): read and write
  - for Claude's engine only: its own settings and sign-in folder (AI Team Lite's data folder\\claude). The engine
    has a container of its own (profile_name(..., engine=True)); test runs and Launch use the project's other one,
    which never gets that folder, so a project's tests or app cannot read the Claude sign-in.
Windows' own system files stay readable, as for any app container. Nothing else of the user's profile is reachable.

When the box cannot be set up the work continues outside it (the user is never blocked) and the approval card
says so in one line (card_note). policy.py and shellcheck.py stay as the second guard.
"""
from __future__ import annotations

import atexit
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from . import paths, settings

PROFILE_PREFIX = "AITeamLite.Sbx."
ALL_APP_PACKAGES = "S-1-15-2-1"
COMMAND_TOOLS = {"Bash", "PowerShell", "mcp__aiteam__run_tests", "mcp__aiteam__launch_for_user"}
OUTSIDE_TOOLS = {"mcp__aiteam__take_screenshot", "mcp__aiteam__package_app"}   # started by the app, not boxed
_RIGHTS = {"RW": "(OI)(CI)(M)", "RX": "(OI)(CI)(RX)", "RA": "(RA)"}
NO_WINDOW = 0x08000000 if paths.IS_WINDOWS else 0
LAUNCHER = Path(__file__).with_name("sandbox_launch.py")

_lock = threading.RLock()
_prepared: dict[tuple[str, bool], tuple[str, set]] = {}   # (project, engine?) -> (container SID, {(path, mode)})
_programs_open: set[str] = set()             # program folders already readable by app containers
_engine: dict[str, str] = {}                 # project -> "" when its engine runs boxed, else why not
_unprotected: dict[str, str] = {}            # project -> why its last test run / launch ran outside the box
_available: bool | None = None
_cleanup_registered = False


# ------------------------------------------------------------------ setting and availability
def enabled() -> bool:
    """The setting "sandbox_enabled" (default on)."""
    return settings.load().get("sandbox_enabled", True) is not False


def available() -> bool:
    """True on Windows when an AppContainer profile can be created (checked once)."""
    global _available
    if _available is None:
        _available = False
        if paths.IS_WINDOWS:
            try:
                from . import sandbox_win as w
                name = PROFILE_PREFIX + "check"
                w.profile_sid(name)
                w.delete_profile(name)
                _available = True
            except Exception as e:  # noqa: BLE001  (never block the app: work runs unprotected, the card says so)
                logging.warning("sandbox not available: %s", e)
    return _available


def active() -> bool:
    return enabled() and available()


# ------------------------------------------------------------------ what the box may touch
def _key(project: Path) -> str:
    return str(Path(project).resolve()).lower()


def _hash(project: Path) -> str:
    return hashlib.sha1(_key(project).encode("utf-8")).hexdigest()[:16]


def profile_name(project: Path, engine: bool = False) -> str:
    """The test-run / Launch container of a project, or (engine=True) the one its Claude engine runs in."""
    return PROFILE_PREFIX + ("E." if engine else "") + _hash(project)


def temp_dir(project: Path) -> Path:
    return paths.data_dir() / "sandbox-temp" / _hash(project)


def site_dir(project: Path) -> Path:
    """Holds the sitecustomize.py that Python programs in the box load (PYTHONPATH points here)."""
    return temp_dir(project) / "py-site"


# Python 3.12.4+ gives a folder made with mode 0o700 (tempfile.mkdtemp, pytest's tmp_path and cache) a private list
# of rights naming only the user, so a program in the box is refused the folder it has just made. In the box such a
# folder simply keeps the rights of the folder it is in (the project or the box's own temp folder).
SITE_CODE = '''"""Written by AI Team Lite (REQ-22) for Python programs in its sandbox; see app/sandbox.py."""
import os
import sys

if sys.platform == "win32" and not getattr(os.mkdir, "aiteam_sandbox", False):
    _mkdir = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        return _mkdir(path, 0o777 if mode == 0o700 else mode, dir_fd=dir_fd)

    mkdir.aiteam_sandbox = True
    os.mkdir = mkdir
'''


def _write_site(project: Path) -> None:
    d = site_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "sitecustomize.py"
    if not f.exists() or f.read_text(encoding="utf-8", errors="replace") != SITE_CODE:
        f.write_text(SITE_CODE, encoding="utf-8")


def _flag(project: Path) -> Path:
    """Written by the engine's launcher when it had to start the engine outside the box."""
    return paths.data_dir() / "sandbox-temp" / f"{_hash(project)}.unprotected"


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _venv_home(venv: Path) -> list[Path]:
    try:
        for line in (venv / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace").splitlines():
            k, _, v = line.partition("=")
            if k.strip().lower() == "home" and v.strip():
                return [Path(v.strip())]
    except OSError:
        pass
    return []


def _python_roots() -> list[Path]:
    """The interpreter in use, the bundled one, and the base Python and packages a virtual environment uses."""
    roots: list[Path] = []
    for exe in (paths.bundled_python(), Path(sys.executable)):
        d = exe.parent
        if d.name.lower() in ("scripts", "bin") and (d.parent / "pyvenv.cfg").exists():
            roots += [d.parent, *_venv_home(d.parent)]
        else:
            roots.append(d)
    return roots + [Path(sys.prefix), Path(sys.base_prefix)]


def git_root() -> Path | None:
    exe = paths.git_exe()
    if not exe:
        return None
    p = Path(exe).resolve().parent
    for up in (p, *list(p.parents)[:3]):
        if (up / "cmd" / "git.exe").exists() or (up / "git-bash.exe").exists():
            return up
    return p.parent if p.name.lower() in ("cmd", "bin") else p


def _program_folders(project: Path) -> list[Path]:
    home = Path.home().resolve()
    out: list[Path] = []
    for r in _python_roots() + [git_root()]:
        if r is None or not r.exists():
            continue
        r = r.resolve()
        # never a drive, the user's profile or anything above it, nor the project (already read/write)
        if r == Path(r.anchor) or _under(home, r) or _under(r, project):
            continue
        if not any(_under(r, o) for o in out):
            out = [o for o in out if not _under(o, r)] + [r]
    return out


def access_list(project_dir: Path) -> list[tuple[Path, str]]:
    """What the container may touch: (folder, "RW" read+write | "RX" read+run | "RA" read attributes only)."""
    proj = Path(project_dir).resolve()
    out = [(proj, "RW"), (proj.parent, "RA")]
    out += [(p, "RX") for p in _program_folders(proj)]
    out.append((temp_dir(proj), "RW"))
    return out


def engine_access_list(project_dir: Path, config: bool = True) -> list[tuple[Path, str]]:
    """access_list plus what Claude's engine needs: its own settings and sign-in folder (only for the engine's own
    container, and only once it is known the engine can start in the box: config=False before that)."""
    from . import auth
    return access_list(project_dir) + ([(auth.claude_config_dir().resolve(), "RW")] if config else [])


# ------------------------------------------------------------------ rights on folders
def _icacls(path: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["icacls", str(path), *args, "/C", "/Q"], capture_output=True, text=True, errors="replace",
                       creationflags=NO_WINDOW, timeout=900)
    return r.returncode, (r.stdout + r.stderr).strip()


def _readable_by_containers(folder: Path) -> bool:
    from . import sandbox_win as w
    name = w.account_name(ALL_APP_PACKAGES) or "ALL APPLICATION PACKAGES"
    code, out = _icacls(folder)
    lines = [ln for ln in out.splitlines() if name in ln or f"*{ALL_APP_PACKAGES}" in ln]
    reads = ("(RX)", "(F)", "(M)", "GR", "(R)")
    return (any("(IO)" not in ln and any(r in ln for r in reads) for ln in lines)
            and any("(OI)(CI)" in ln and any(r in ln for r in reads) for ln in lines))


def _open_program_folder(folder: Path) -> None:
    key = str(folder).lower()
    if key in _programs_open:
        return
    if not _readable_by_containers(folder):
        code, out = _icacls(folder, "/grant", f"*{ALL_APP_PACKAGES}:{_RIGHTS['RX']}")
        if code:
            raise OSError(f"could not let the sandbox read and run {folder}: {out[-300:]}")
    _programs_open.add(key)


def prepare(project_dir: Path, engine: bool = False, config: bool = True) -> str:
    """Create the project's container profile (engine=True: the engine's own one) and give it its rights (once per
    run of the app). Returns its SID. Raises OSError when that is not possible."""
    if not available():
        raise OSError("Windows could not set up the sandbox on this PC")
    from . import sandbox_win as w
    global _cleanup_registered
    project = Path(project_dir).resolve()
    with _lock:
        key = (_key(project), bool(engine))
        sid, done = _prepared.get(key) or (w.profile_sid(profile_name(project, engine)), set())
        _prepared[key] = (sid, done)
        if not _cleanup_registered:
            atexit.register(cleanup_all)
            _cleanup_registered = True
        temp_dir(project).mkdir(parents=True, exist_ok=True)
        (temp_dir(project) / "project.txt").write_text(str(project), encoding="utf-8")   # for cleanup after a crash
        _write_site(project)
        for path, mode in (engine_access_list(project, config) if engine else access_list(project)):
            if (str(path), mode) in done:
                continue
            if mode == "RX":
                _open_program_folder(path)
            else:
                path.mkdir(parents=True, exist_ok=True) if mode == "RW" else None
                code, out = _icacls(path, "/grant", f"*{sid}:{_RIGHTS[mode]}")
                if code:
                    raise OSError(f"could not give the sandbox its rights on {path}: {out[-300:]}")
            done.add((str(path), mode))
        return sid


def _remove_rights(sid: str, done: set) -> None:
    for path, mode in done:
        if mode == "RX" or not Path(path).exists():
            continue
        # read+write folders: also every file and folder inside (/T), so a file the box made that names the
        # container in its own rights loses it too; never /T on the parent ("RA" covers that one folder only)
        code, out = _icacls(Path(path), "/remove:g", f"*{sid}", *(["/T"] if mode == "RW" else []))
        if code:
            logging.warning("sandbox: could not remove rights on %s: %s", path, out[-300:])


def cleanup(project_dir: Path) -> None:
    """Remove both containers' rights from the project (and the rest of their lists), its temp folder and profiles.
    Does nothing for a project the box was never set up for (no temp folder, nothing granted)."""
    if not paths.IS_WINDOWS:
        return
    from . import sandbox_win as w
    project = Path(project_dir).resolve()
    key = _key(project)
    with _lock:
        _engine.pop(key, None)
        _shell_checked.pop(key, None)
        entries = {e: _prepared.pop((key, e), None) for e in (False, True)}
        if not any(entries.values()) and not temp_dir(project).exists():
            return
        for engine, entry in entries.items():
            try:
                sid = entry[0] if entry else w.profile_sid(profile_name(project, engine))
            except OSError:
                continue
            done = entry[1] if entry else {(str(p), m) for p, m in
                                           (engine_access_list(project) if engine else access_list(project))}
            _remove_rights(sid, done)
            w.delete_profile(profile_name(project, engine))
        shutil.rmtree(temp_dir(project), ignore_errors=True)
        _flag(project).unlink(missing_ok=True)


def cleanup_leftovers() -> None:
    """At start-up: rights a crash (or a forced close) left behind, found through the temp folders' project.txt."""
    root = paths.data_dir() / "sandbox-temp"
    if not paths.IS_WINDOWS or not root.is_dir():
        return
    for d in root.iterdir():
        try:
            marker = d / "project.txt"
            if not marker.is_file():
                continue
            project = Path(marker.read_text(encoding="utf-8").strip())
            with _lock:
                if any(k[0] == _key(project) for k in _prepared):   # already in use again in this run
                    continue
                cleanup(project)
                shutil.rmtree(d, ignore_errors=True)   # also when the project folder was moved or deleted
        except Exception:  # noqa: BLE001  (start-up must not fail on this)
            logging.exception("sandbox: leftover cleanup failed for %s", d)


def cleanup_all() -> None:
    for key in {k for k, _ in _prepared}:
        try:
            cleanup(Path(key))
        except Exception:  # noqa: BLE001  (closing the app must not fail on this)
            logging.exception("sandbox cleanup failed for %s", key)


# ------------------------------------------------------------------ starting programs in the box
def box_env(project_dir: Path, env: dict | None = None) -> dict:
    base = dict(os.environ if env is None else env)
    if env is None:
        base["PATH"] = paths.tool_path()
    tmp = str(temp_dir(project_dir))
    base.update(TEMP=tmp, TMP=tmp, PIP_CACHE_DIR=str(temp_dir(project_dir) / "pip-cache"))
    base["PYTHONPATH"] = os.pathsep.join(x for x in (str(site_dir(project_dir)), base.get("PYTHONPATH", "")) if x)
    return base


def start_process(cmd, cwd: Path, project_dir: Path, *, env: dict | None = None, merge: bool = False,
                  desktop: str | None = None, capture: bool = True, new_console: bool = False):
    """Start cmd (a list, or a ready command line) inside the project's container, without a window unless
    new_console. Returns a Popen-like object (.communicate(timeout) -> (stdout, stderr), .returncode, .poll(),
    .kill(), .pid). Raises OSError when the container cannot be set up."""
    from . import sandbox_win as w
    sid = prepare(project_dir)
    line = cmd if isinstance(cmd, str) else subprocess.list2cmdline([str(c) for c in cmd])
    proc = w.Process(cmd, line, str(cwd), box_env(project_dir, env), sid, capture=capture, merge=merge,
                     new_console=new_console, desktop=desktop)
    _unprotected.pop(_key(project_dir), None)
    return proc


def shell_line(command: str) -> str:
    """A shell command line (as the team writes it) run by cmd.exe, as subprocess(shell=True) does."""
    comspec = os.environ.get("ComSpec") or r"C:\Windows\System32\cmd.exe"
    return f'"{comspec}" /d /s /c "{command}"'


def note_unprotected(project_dir: Path, reason: str) -> None:
    _unprotected[_key(project_dir)] = reason
    logging.warning("sandbox not applied for %s: %s", project_dir, reason)


def _allow_engine_folder(project: Path, sid: str, folder: Path) -> None:
    """claude.exe normally sits in the Python packages (already read+run). If it lives elsewhere, only this
    project's engine container may read and run its folder (never the user's profile itself or a drive)."""
    if any(m == "RX" and _under(folder, p) for p, m in access_list(project)):
        return
    home = Path.home().resolve()
    if folder == Path(folder.anchor) or _under(home, folder):
        raise OSError(f"Claude's engine is in {folder}, which cannot be opened to the sandbox")
    with _lock:
        done = _prepared[(_key(project), True)][1]
        if (str(folder), "RXS") not in done:
            code, out = _icacls(folder, "/grant", f"*{sid}:{_RIGHTS['RX']}")
            if code:
                raise OSError(f"could not let the sandbox run Claude's engine in {folder}: {out[-300:]}")
            done.add((str(folder), "RXS"))   # removed again by cleanup (only shared "RX" folders stay)


def git_bash() -> Path | None:
    """The Git Bash Claude's engine uses for the team's commands (as the engine finds it), or None."""
    named = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    root = git_root()
    for p in ([Path(named)] if named else []) + ([root / "bin" / "bash.exe"] if root else []):
        if p.is_file():
            return p
    return None


_shell_checked: dict[str, str] = {}


def shell_problem(project_dir: Path) -> str:
    """Empty when the shell the engine runs the team's commands with works inside the box, else why not (in words).
    Checked once per project per run of the app. On the owner's PC (iteration 22): Git Bash (MSYS2) does not start
    in an AppContainer at all (0xC0000142) and PowerShell cannot enter a folder whose parents it may not list."""
    project = Path(project_dir).resolve()
    key = _key(project)
    if key not in _shell_checked:
        bash = git_bash()
        if bash:
            cmd, name = [str(bash), "-c", "exit 0"], "Git Bash"
        else:
            ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            lit = str(project).replace("'", "''")
            cmd, name = [str(ps), "-NoProfile", "-Command", f"Set-Location -LiteralPath '{lit}'; exit 0"], "PowerShell"
        try:
            p = start_process(cmd, project, project, merge=True)
            try:
                p.communicate(timeout=60)
                ok = p.returncode == 0
            except subprocess.TimeoutExpired:
                p.kill()
                ok = False
        except OSError:
            ok = False
        _shell_checked[key] = "" if ok else f"{name}, which the team's commands use, cannot run inside the sandbox on this PC"
    return _shell_checked[key]


def engine_command(cmd: list, cwd) -> list:
    """Claude's engine (claude.exe) started inside the project's container, through the small launcher
    (sandbox_launch.py) so the SDK keeps a normal process with pipes. Anything else, or a failure: cmd unchanged."""
    if not cwd or not isinstance(cmd, (list, tuple)) or not cmd or Path(str(cmd[0])).stem.lower() != "claude":
        return cmd
    project = Path(cwd)
    key = _key(project)
    if not enabled():
        _engine[key] = "file protection is switched off in Settings"
        return cmd
    why = ""
    try:
        # the engine's own container, without the Claude sign-in folder until it is known the engine can start in it
        sid = prepare(project, engine=True, config=False)
        _allow_engine_folder(project, sid, Path(str(cmd[0])).resolve().parent)
        problem = shell_problem(project)
        if problem:
            why = "the program that runs the team's commands cannot start inside file protection on this PC"
        else:
            prepare(project, engine=True)       # now the sign-in folder, for the engine's container only
    except Exception as e:  # noqa: BLE001  (whatever goes wrong here, the engine still starts: never block the user)
        problem, why = f"set-up failed: {e}", "file protection could not be set up for this project"
    if problem:
        _engine[key] = why                      # in words on the card; the details only in the log
        logging.warning("engine for %s starts outside the sandbox: %s", project, problem)
        return cmd
    _engine[key] = ""
    _flag(project).unlink(missing_ok=True)
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe" and py.with_name("python.exe").exists():
        py = py.with_name("python.exe")
    return [str(py), str(LAUNCHER), sid, str(temp_dir(project)), str(_flag(project)), "--", *[str(c) for c in cmd]]


# ------------------------------------------------------------------ the approval card
def _outside(why: str) -> str:
    """One plain line; users only ever see the term "file protection" (raw Windows details stay in the log)."""
    return f"{why[:1].upper()}{why[1:]}, so this runs outside the protection and can reach your files like you can."


def sandbox_status(project_dir: Path) -> dict:
    """H-3: is Fred's engine boxed for this project? {"protected": bool, "warning": one plain sentence or ""}."""
    why = _engine.get(_key(Path(project_dir)))
    if why is None:      # the engine has not started yet: only the settings can tell
        why = "" if enabled() and available() else (
            "file protection is switched off in Settings" if not enabled()
            else "Windows could not set up file protection on this PC")
    if not why:
        return {"protected": True, "warning": ""}
    return {"protected": False, "warning": f"{_outside(why)} Be careful what you ask the team to do."}


def card_note(tool_name: str, project_dir: Path | None = None) -> str:
    """One line for the approval card: "" when the command is protected or the tool runs no command."""
    if tool_name in OUTSIDE_TOOLS:
        return _outside("AI Team Lite starts this program itself, without file protection")
    if tool_name not in COMMAND_TOOLS:
        return ""
    if not enabled():
        return _outside("file protection is switched off in Settings")
    if not available():
        return _outside("Windows could not set up file protection on this PC")
    if project_dir is not None:
        key = _key(project_dir)
        if tool_name in ("Bash", "PowerShell"):
            if _engine.get(key):
                return _outside(_engine[key])
            if _flag(project_dir).exists():
                return _outside("Fred's engine could not start inside file protection")
        elif _unprotected.get(key):
            return _outside("last time file protection could not be set up for this project")
    return ""
