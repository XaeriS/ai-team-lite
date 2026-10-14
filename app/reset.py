"""Start AI Team Lite fresh: erase its settings, saved keys, Claude sign-in, setup chat, logs and window data.
Your project folders are NOT touched.

  python reset.py --yes     erase without asking (used by the installer / uninstaller)
  python reset.py           ask first (used by the "Reset AI Team Lite" Start-menu shortcut)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "app"

from app import paths, secrets_store  # noqa: E402

NO_WINDOW = 0x08000000 if paths.IS_WINDOWS else 0


def _ask(text: str) -> bool:
    if paths.IS_WINDOWS:
        import ctypes

        return ctypes.windll.user32.MessageBoxW(None, text, "Reset AI Team Lite", 0x04 | 0x30 | 0x100) == 6  # Yes/No
    return input(text + " [y/N] ").strip().lower() == "y"


def _tell(text: str) -> None:
    if paths.IS_WINDOWS:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "Reset AI Team Lite", 0x40)
    else:
        print(text)


def close_running_app() -> None:
    """Stop AI Team Lite and its Claude engine if they are running from this installation."""
    if not paths.IS_WINDOWS:
        return
    root = str(paths.install_dir()).replace("'", "''")
    me = os.getpid()
    script = (f"Get-Process python,pythonw,claude -ErrorAction SilentlyContinue | "
              f"Where-Object {{ $_.Path -like '{root}\\*' -and $_.Id -ne {me} }} | Stop-Process -Force")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=60,
                   creationflags=NO_WINDOW)
    time.sleep(1)


def reset() -> list[str]:
    done = []
    for name in secrets_store.KNOWN:
        if secrets_store.has(name):
            secrets_store.delete_secret(name)
            done.append(f"removed saved {secrets_store.KNOWN[name]}")
    data = paths.data_dir()  # settings, setup chat, Claude sign-in (claude/), logs, window data
    for _ in range(3):
        shutil.rmtree(data, ignore_errors=True)
        if not data.exists():
            break
        time.sleep(1)
    done.append(f"erased {data}" if not data.exists() else f"could not fully erase {data} (close AI Team Lite and retry)")
    fallback = Path.home() / ".ai-team-lite"
    if fallback.exists() and fallback != data:
        shutil.rmtree(fallback, ignore_errors=True)
    return done


def main(argv: list[str]) -> int:
    quiet = "--yes" in argv
    if not quiet and not _ask("Start AI Team Lite completely fresh?\n\nThis erases its settings, your saved Anthropic "
                              "key and GitHub token, the Claude sign-in, and Fred's setup chat.\n\nYour project "
                              "folders are kept."):
        return 1
    close_running_app()
    done = reset()
    if not quiet:
        _tell("AI Team Lite was reset:\n- " + "\n- ".join(done) + "\n\nOpen AI Team Lite to set it up again.")
    else:
        print("\n".join(done))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
