"""API keys and tokens live in Windows Credential Manager (via keyring), never in plain files or prompts."""
from __future__ import annotations

import json
import os
from pathlib import Path

SERVICE = "AI Team Lite"
KNOWN = {"anthropic_api_key": "Anthropic API key", "github_token": "GitHub token",
         "gemini_api_key": "Google Gemini API key",
         "app_repo_token": "AI Team Lite sharing token"}


def _fallback_file() -> Path | None:
    p = os.environ.get("AITEAM_SECRETS_FILE")
    return Path(p) if p else None


def _keyring():
    if _fallback_file():
        return None
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def _read_file() -> dict:
    f = _fallback_file() or Path.home() / ".ai-team-lite" / "secrets.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_file(data: dict) -> None:
    f = _fallback_file() or Path.home() / ".ai-team-lite" / "secrets.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def set_secret(name: str, value: str) -> None:
    if name not in KNOWN:
        raise ValueError(f"Unknown secret: {name}")
    kr = _keyring()
    if kr:
        kr.set_password(SERVICE, name, value)
    else:
        data = _read_file()
        data[name] = value
        _write_file(data)


def get_secret(name: str) -> str | None:
    kr = _keyring()
    if kr:
        try:
            return kr.get_password(SERVICE, name)
        except Exception:
            return None
    return _read_file().get(name)


def delete_secret(name: str) -> None:
    kr = _keyring()
    if kr:
        try:
            kr.delete_password(SERVICE, name)
        except Exception:
            pass
    else:
        data = _read_file()
        data.pop(name, None)
        _write_file(data)


def has(name: str) -> bool:
    return bool(get_secret(name))
