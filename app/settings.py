"""App settings, stored as JSON in the data folder. Secrets are NOT stored here (see secrets_store)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import paths

DEFAULTS = {
    "setup_complete": False,
    "workspace_dir": "",
    "approval_mode": "plan",   # "plan": after you approve a plan, agents may work inside the project freely
                               # "strict": every action outside reading files asks you first
    "github_login": "",
    "auth_mode": "",                 # "api_key" | "subscription" ("" = not chosen yet)
    "subscription_signed_in": False, # last result of `claude auth status`
    # hands-free talking
    "voice_end_mode": "both",        # "word" | "silence" | "both"
    "voice_end_word": "copy",
    "voice_silence_seconds": 3,
    "voice_review": False,           # True: put the text in the box instead of sending it
    "voice_auto_listen": True,       # listen again automatically when Fred has finished
    "voice_model": "base",           # "base" (fast) | "small" (more accurate, slower)
    "voice_language": "auto",
    # council brains and work scheduling
    "brains": [],                    # [{"id", "provider", "model", "enabled"}]
    "max_parallel": 2,               # how many projects may work at the same time
    "iteration_pause": False,        # default for new plans: pause for release after each iteration
    "pipeline_auto": True,           # continue approved iterations across projects when slots are free
    "pipeline_order": [],            # project slugs in master-plan order
    "cap_context": True,             # fresh, small context per iteration to save tokens (item 48)
    # 0.8.0
    "skills": {},                    # skill key -> on/off (default on), see skills.py
    "council_members": [],           # council brain ids ([] = every usable helper brain)
    "prior_art": True,               # Scout checks the web for existing solutions before Mat
    "challenge_level": "normal",     # 0.14 (item 79): how hard Fred pushes back: gentle | normal | firm
    "tests_first": True,             # tests written and failing before the code (enforced at commit)
    "light_cleanup": True,           # quick clean-up of changed files before each iteration commit
    "version_cleanup": True,         # add a whole-codebase "Clean-up" iteration to each plan
    "ollama_autostart": True,        # start Ollama when a local brain is needed
    "close_to_tray": False,          # the X button hides AI Team Lite to the tray instead of quitting (item 67)
    "ollama_stop_on_exit": False,    # stop Ollama when AI Team Lite closes (only if AI Team Lite started it)
    "auto_resume": False,            # resume interrupted work automatically at start
    "user_name": "",
    "telegram_chat_id": None,        # L17: the owner's own Telegram chat (linked once with a code)
    "telegram_bot": "",              # the bot's @username, to show in Settings
    "telegram_high_risk_on_phone": False,  # high-risk approvals only at the PC unless switched on
}
# Settings Fred is allowed to change through his save_setting tool.
AGENT_EDITABLE = {"workspace_dir", "approval_mode", "user_name"}

_lock = threading.Lock()


def _file() -> Path:
    return paths.data_dir() / "settings.json"


def load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **data}


def save(changes: dict) -> dict:
    with _lock:
        data = load()
        data.update(changes)
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_file())
        return data


def workspace() -> Path:
    ws = load()["workspace_dir"] or str(paths.default_workspace())
    p = Path(ws).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
