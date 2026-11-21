"""L52b: turn a finished project into a real Windows installer, like the ones people are used to.

Owner's choice (2026-11-20): "a real installer preferably, with an icon that we can see on the desktop (or decide not
to add it there, like a usual installer)". So: PyInstaller bundles the app with its own Python (one folder, starts
fast, flagged less often than one big file), and Inno Setup wraps it in a standard setup program: installs for the
current user (no administrator rights), a Start-menu entry with the app's icon, a "Create a desktop shortcut" box,
"Start the app" at the end, and an uninstaller in Windows' list of apps.
Both tools run on the user's PC; installing them asks the user first (L65: new software and new packages ask).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

from . import iterations, paths, projects, settings, testrun

Runner = Callable[[list[str], Path, int], tuple[int, str]]
DATA_DIRS = ("templates", "static", "assets", "data", "ui", "web", "config", "locales", "images", "icons")
ENTRY_NAMES = ("main.py", "app.py", "run.py", "start.py", "gui.py", "main.pyw", "app.pyw")
ISCC_PLACES = [r"{pf86}\Inno Setup 6\ISCC.exe", r"{pf}\Inno Setup 6\ISCC.exe", r"{local}\Programs\Inno Setup 6\ISCC.exe"]
INSTALL_INNO = "winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements"


class PackagingError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", creationflags=testrun.NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"Stopped after {timeout} s."
    except OSError as e:
        return -2, str(e)


def find_iscc() -> str | None:
    env = {"pf86": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
           "pf": os.environ.get("ProgramFiles", r"C:\Program Files"), "local": os.environ.get("LOCALAPPDATA", "")}
    for place in ISCC_PLACES:
        p = Path(place.format(**env))
        if env["local"] or "{local}" not in place:
            if p.is_file():
                return str(p)
    return shutil.which("ISCC") or shutil.which("iscc")


def venv_python(folder: Path) -> Path:
    return folder / ".venv" / ("Scripts/python.exe" if paths.IS_WINDOWS else "bin/python")


def safe_name(name: str) -> str:
    """A file and folder name Windows accepts: letters, digits, spaces and dashes."""
    s = re.sub(r"[^A-Za-z0-9 \-]+", "", name).strip()
    return re.sub(r"\s+", " ", s)[:60] or "App"


def version_of(slug: str, given: str = "") -> str:
    if given and re.fullmatch(r"\d+(\.\d+){0,3}", given.strip()):
        return given.strip()
    done = sum(1 for it in iterations.load(slug) if it.get("status") == "done")
    return f"1.{done}"


def entry_point(folder: Path, given: str = "") -> Path:
    if given:
        p = (folder / given).resolve()
        if folder.resolve() not in p.parents or not p.is_file():
            raise PackagingError(f"The start file {given} was not found in the project.")
        return p
    for n in ENTRY_NAMES:
        if (folder / n).is_file():
            return folder / n
    pyw = sorted(folder.glob("*.pyw"))
    if pyw:
        return pyw[0]
    raise PackagingError("No start file found (main.py, app.py...): pass entry, e.g. entry='src/main.py'.")


def make_icon(folder: Path, name: str) -> Path:
    """The app's own icon: an .ico the project already has, else a simple one made from its first letter."""
    for c in ("icon.ico", "app.ico", "favicon.ico", "assets/icon.ico", "static/favicon.ico", "assets/app.ico"):
        if (folder / c).is_file():
            return folder / c
    found = sorted(p for p in folder.glob("*/*.ico") if ".venv" not in p.parts)
    if found:
        return found[0]
    out = folder / "packaging" / "icon.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image, ImageDraw, ImageFont
    hue = int(uuid.uuid5(uuid.NAMESPACE_URL, name).hex[:2], 16)
    palette = [(37, 99, 235), (5, 150, 105), (217, 119, 6), (124, 58, 237), (219, 39, 119), (8, 145, 178)]
    color = palette[hue % len(palette)]
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), radius=56, fill=color)
    letter = (re.sub(r"[^A-Za-z0-9]", "", name)[:1] or "A").upper()
    try:
        font = ImageFont.load_default(size=150)
    except TypeError:   # older Pillow
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), letter, font=font)
    d.text(((256 - (box[2] - box[0])) / 2 - box[0], (256 - (box[3] - box[1])) / 2 - box[1]), letter, fill="white", font=font)
    img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return out


def pyinstaller_command(python: Path, folder: Path, entry: Path, name: str, icon: Path, windowed: bool) -> list[str]:
    sep = ";" if paths.IS_WINDOWS else ":"
    work = folder / "build" / "package"
    cmd = [str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
           "--windowed" if windowed else "--console", "--name", name, "--icon", str(icon),
           "--distpath", str(work / "dist"), "--workpath", str(work / "work"), "--specpath", str(work)]
    for d in DATA_DIRS:   # folders the app reads at run time (pages, pictures, settings)
        src = entry.parent / d
        if src.is_dir():
            cmd += ["--add-data", f"{src}{sep}{d}"]
    return cmd + [str(entry)]


def iss_script(*, name: str, version: str, publisher: str, app_id: str, dist: Path, icon: Path, out_dir: Path,
               out_name: str) -> str:
    q = lambda s: str(s).replace('"', "")
    return f"""; Made by AI Team Lite (L52b). Builds a standard Windows setup program with Inno Setup.
#define AppName "{q(name)}"
#define AppVersion "{q(version)}"
#define AppExe "{q(name)}.exe"

[Setup]
AppId={{{{{app_id}}}
AppName={{#AppName}}
AppVersion={{#AppVersion}}
AppPublisher={q(publisher)}
DefaultDirName={{localappdata}}\\Programs\\{{#AppName}}
DefaultGroupName={{#AppName}}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={q(out_dir)}
OutputBaseFilename={q(out_name)}
SetupIconFile={q(icon)}
UninstallDisplayIcon={{app}}\\{{#AppExe}}
UninstallDisplayName={{#AppName}}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{q(dist)}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\{{#AppName}}"; Filename: "{{app}}\\{{#AppExe}}"
Name: "{{group}}\\Uninstall {{#AppName}}"; Filename: "{{uninstallexe}}"
Name: "{{userdesktop}}\\{{#AppName}}"; Filename: "{{app}}\\{{#AppExe}}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{{#AppExe}}"; Description: "Start {{#AppName}} now"; Flags: nowait postinstall skipifsilent
"""


def _ignore(folder: Path, line: str) -> None:
    gi = folder / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if line not in text.splitlines():
        gi.write_text(text.rstrip("\n") + ("\n" if text else "") + line + "\n", encoding="utf-8")


def needs(slug: str) -> list[str]:
    """What is missing on this PC before packaging can run (each one asks the user when Fred installs it)."""
    folder = projects.project_dir(slug)
    out = []
    py = venv_python(folder)
    if not py.exists():
        out.append("the project's .venv (create it with the bundled Python, then install requirements.txt)")
    elif _run([str(py), "-c", "import PyInstaller"], folder, 60)[0] != 0:
        out.append(f'PyInstaller in the project\'s .venv: "{py}" -m pip install pyinstaller')
    if not find_iscc():
        out.append(f"Inno Setup (makes the setup program): {INSTALL_INNO}")
    return out


def build(slug: str, *, entry: str = "", windowed: bool | None = None, version: str = "", runner: Runner | None = None,
          smoke: Callable[[Path], tuple[bool, str]] | None = None) -> dict:
    """Package the project. Returns {"installer": relative path, "version", "name", "bytes"}."""
    p = projects.load(slug)
    if not p:
        raise KeyError(slug)
    run = runner or _run
    folder = projects.project_dir(slug)
    missing = needs(slug) if runner is None else []
    if missing:
        raise PackagingError("Before packaging, install: " + "; ".join(missing) + ".")
    name = safe_name(p.get("name") or slug)
    ver = version_of(slug, version)
    start = entry_point(folder, entry)
    if windowed is None:
        windowed = start.suffix == ".pyw" or bool(re.search(r"(?m)^\s*(import|from)\s+(tkinter|PySide6|PyQt[56]|wx|webview)\b",
                                                            start.read_text(encoding="utf-8", errors="ignore")))
    icon = make_icon(folder, name)
    log = folder / "reports" / "package.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    code, out = run(pyinstaller_command(venv_python(folder), folder, start, name, icon, windowed), folder, 1800)
    log.write_text(f"$ PyInstaller ({time.strftime('%Y-%m-%d %H:%M')})\n{out}", encoding="utf-8")
    dist = folder / "build" / "package" / "dist" / name
    if code != 0 or not (dist / f"{name}.exe").exists():
        raise PackagingError("PyInstaller could not bundle the app (full log: reports/package.log):\n" + out[-1500:])
    ok, why = (smoke or _smoke)(dist / f"{name}.exe")
    if not ok:
        raise PackagingError(f"The packaged app did not start: {why[-800:]}")
    delivery = folder / "delivery"
    delivery.mkdir(exist_ok=True)
    out_name = f"{name.replace(' ', '-')}-Setup-{ver}"
    iss = folder / "packaging" / "setup.iss"
    iss.parent.mkdir(parents=True, exist_ok=True)
    iss.write_text(iss_script(name=name, version=ver, publisher=settings.load().get("user_name") or "AI Team Lite user",
                              app_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"aiteamlite:{slug}")).upper(), dist=dist,
                              icon=icon, out_dir=delivery, out_name=out_name), encoding="utf-8")
    code, out2 = run([find_iscc() or "ISCC", str(iss)], folder, 1800)
    with log.open("a", encoding="utf-8") as h:
        h.write(f"\n\n$ Inno Setup\n{out2}")
    setup = delivery / f"{out_name}.exe"
    if code != 0 or not setup.exists():
        raise PackagingError("Inno Setup could not make the setup program:\n" + out2[-1500:])
    for line in ("build/", "delivery/"):
        _ignore(folder, line)   # big files: never saved to Git (they go to a GitHub release instead)
    info = {"installer": setup.relative_to(folder).as_posix(), "version": ver, "name": name, "bytes": setup.stat().st_size,
            "time": time.time()}
    projects.update(slug, package=info)
    return info


def _smoke(exe: Path) -> tuple[bool, str]:
    """Start the packaged app out of sight for a few seconds: still running (or a clean exit) means it starts."""
    from . import winhelpers
    res = winhelpers.run_on_hidden_desktop(f'"{exe}"', exe.parent, dict(os.environ), 8)
    if res is None:
        return True, "not checked"
    code, out = res
    return (code in (-1, 0)), out
