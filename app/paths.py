"""Where things live: the install folder (read-only app files) and the data folder (your settings)."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "AI Team Lite"
IS_WINDOWS = sys.platform == "win32"


def install_dir() -> Path:
    """Folder chosen in the installer. It contains app/, python/ and git/."""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    if os.environ.get("AITEAM_HOME"):
        base = Path(os.environ["AITEAM_HOME"])
    elif IS_WINDOWS and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / APP_NAME
    else:
        base = Path.home() / ".ai-team-lite"
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_workspace() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.exists() else Path.home()) / "AI Team Lite Projects"


def bundled_python() -> Path:
    """The Python shipped with the app. Agents use it to create each project's virtual environment."""
    exe = install_dir() / "python" / ("python.exe" if IS_WINDOWS else "bin/python3")
    return exe if exe.exists() else Path(sys.executable)


def git_exe() -> str | None:
    bundled = install_dir() / "git" / "cmd" / "git.exe"
    if bundled.exists():
        return str(bundled)
    return shutil.which("git")


def tool_path() -> str:
    """PATH for agents: bundled Git and Python first, then the normal PATH."""
    extra: list[Path] = []
    git_root = install_dir() / "git"
    if git_root.exists():
        extra += [git_root / "cmd", git_root / "mingw64" / "bin", git_root / "usr" / "bin"]
    py = bundled_python().parent
    extra += [py, py / "Scripts"]
    parts = [str(p) for p in extra if p.exists()] + [os.environ.get("PATH", "")]
    return os.pathsep.join(parts)
