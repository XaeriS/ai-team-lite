"""H-3: when the engine runs outside the sandbox, the app exposes a permanent, plain-language notice (not only a
line on an approval card): sandbox.sandbox_status(project_dir) -> {"protected": bool, "warning": str}."""
import pytest

from app import sandbox, settings


@pytest.fixture
def proj(isolated, monkeypatch):
    p = isolated / "work" / "proj"
    p.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "_engine", {})
    monkeypatch.setattr(sandbox, "_shell_checked", {})
    monkeypatch.setattr(sandbox, "prepare", lambda p, engine=False, config=True: "S-1-15-2-1")
    monkeypatch.setattr(sandbox, "_allow_engine_folder", lambda *a: None)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    settings.save({"sandbox_enabled": True})
    return p


def _assert_plain_warning(st):
    assert st["protected"] is False
    w = st["warning"]
    assert isinstance(w, str) and len(w) > 20 and "\n" not in w
    assert "Git Bash" not in w and "Traceback" not in w, "no program names or raw errors"


def test_status_says_protected_when_the_engine_runs_in_the_box(proj, monkeypatch):
    monkeypatch.setattr(sandbox, "shell_problem", lambda p: "")
    sandbox.engine_command(["claude.exe", "--print"], proj)
    st = sandbox.sandbox_status(proj)
    assert st["protected"] is True and st["warning"] == ""


def test_status_warns_when_the_shell_cannot_run_in_the_box(proj, monkeypatch):
    monkeypatch.setattr(sandbox, "shell_problem", lambda p: "Git Bash, which the team's commands use, cannot run")
    sandbox.engine_command(["claude.exe", "--print"], proj)
    _assert_plain_warning(sandbox.sandbox_status(proj))


def test_status_warns_when_setup_fails(proj, monkeypatch):
    def boom(*a, **k):
        raise OSError("access denied")
    monkeypatch.setattr(sandbox, "prepare", boom)
    sandbox.engine_command(["claude.exe", "--print"], proj)
    _assert_plain_warning(sandbox.sandbox_status(proj))


def test_status_warns_when_the_setting_is_off(proj):
    settings.save({"sandbox_enabled": False})
    sandbox.engine_command(["claude.exe", "--print"], proj)
    _assert_plain_warning(sandbox.sandbox_status(proj))
