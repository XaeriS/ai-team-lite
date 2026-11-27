"""REQ-22: Claude's engine (claude.exe) is started inside the project's sandbox, through app/sandbox_launch.py,
unless the shell the team's commands use cannot run in it; then it starts the normal way and the card says so."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import auth, sandbox, settings

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="AppContainer exists only on Windows")


@pytest.fixture
def proj(isolated, monkeypatch):
    p = isolated / "work" / "proj"
    p.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "_engine", {})
    monkeypatch.setattr(sandbox, "_shell_checked", {})
    return p


def test_other_programs_are_not_wrapped(proj):
    cmd = ["node", "x.js"]
    assert sandbox.engine_command(cmd, proj) is cmd
    assert sandbox.engine_command(["claude.exe", "-v"], None) == ["claude.exe", "-v"]   # no project folder


@windows_only
def test_engine_is_wrapped_by_the_launcher_when_the_box_works(proj, monkeypatch):
    settings.save({"sandbox_enabled": True})
    monkeypatch.setattr(sandbox, "prepare", lambda p, engine=False, config=True: "S-1-15-2-1-2-3")
    monkeypatch.setattr(sandbox, "_allow_engine_folder", lambda *a: None)
    monkeypatch.setattr(sandbox, "shell_problem", lambda p: "")
    monkeypatch.setattr(sandbox, "available", lambda: True)
    out = sandbox.engine_command([r"C:\x\claude.exe", "--print"], proj)
    assert out[1] == str(sandbox.LAUNCHER) and out[2] == "S-1-15-2-1-2-3"
    assert out[out.index("--") + 1:] == [r"C:\x\claude.exe", "--print"]
    assert sandbox.card_note("Bash", proj) == ""


def test_engine_runs_unprotected_and_card_says_why_when_the_shell_cannot_run(proj, monkeypatch):
    settings.save({"sandbox_enabled": True})
    monkeypatch.setattr(sandbox, "prepare", lambda p, engine=False, config=True: "S-1-15-2-1")
    monkeypatch.setattr(sandbox, "_allow_engine_folder", lambda *a: None)
    monkeypatch.setattr(sandbox, "shell_problem", lambda p: "Git Bash, which the team's commands use, cannot run")
    monkeypatch.setattr(sandbox, "available", lambda: True)
    cmd = ["claude.exe", "--print"]
    assert sandbox.engine_command(cmd, proj) is cmd
    note = sandbox.card_note("Bash", proj)
    assert "outside the protection" in note and "file protection" in note and "\n" not in note
    assert "Git Bash" not in note, "no program names on the card (the details stay in the log)"


def test_signin_folder_is_never_in_the_test_run_list(proj):
    """Test runs and Launch use access_list: the Claude sign-in folder must not be in it."""
    cfg = auth.claude_config_dir().resolve()
    assert all(Path(p).resolve() != cfg for p, _ in sandbox.access_list(proj))
    assert (cfg, "RW") in [(Path(p).resolve(), m) for p, m in sandbox.engine_access_list(proj)]
    assert all(Path(p).resolve() != cfg for p, _ in sandbox.engine_access_list(proj, config=False))
    assert sandbox.profile_name(proj) != sandbox.profile_name(proj, engine=True)


def test_signin_folder_granted_only_after_the_engine_is_known_to_start(proj, monkeypatch):
    settings.save({"sandbox_enabled": True})
    calls = []
    monkeypatch.setattr(sandbox, "prepare", lambda p, engine=False, config=True: calls.append((engine, config)) or "S-1-15-2-9")
    monkeypatch.setattr(sandbox, "_allow_engine_folder", lambda *a: None)
    monkeypatch.setattr(sandbox, "shell_problem", lambda p: "Git Bash cannot run")
    sandbox.engine_command(["claude.exe"], proj)
    assert calls == [(True, False)], "the sign-in folder was granted although the engine cannot start in the box"
    calls.clear()
    monkeypatch.setattr(sandbox, "shell_problem", lambda p: "")
    sandbox.engine_command(["claude.exe"], proj)
    assert calls == [(True, False), (True, True)]


@windows_only
def test_test_run_container_has_no_rights_on_the_signin_folder(proj):
    from app import sandbox_win
    cfg = auth.claude_config_dir().resolve()
    run_sid = sandbox.prepare(proj)
    eng_sid = sandbox.prepare(proj, engine=True)
    try:
        assert run_sid != eng_sid
        shown = subprocess.run(["icacls", str(cfg)], capture_output=True, text=True).stdout

        def named(sid):
            name = sandbox_win.account_name(sid)
            return sid in shown or bool(name and name in shown)
        assert named(eng_sid), shown
        assert not named(run_sid), "the test-run / Launch container may touch the Claude sign-in folder"
    finally:
        sandbox.cleanup(proj)
    assert "S-1-15-2-" not in subprocess.run(["icacls", str(cfg)], capture_output=True, text=True).stdout


def test_engine_starts_even_when_the_box_cannot_be_set_up(proj, monkeypatch):
    settings.save({"sandbox_enabled": True})

    def fail(p, engine=False, config=True):
        raise OSError("no profile")
    monkeypatch.setattr(sandbox, "prepare", fail)
    cmd = ["claude.exe"]
    assert sandbox.engine_command(cmd, proj) is cmd
    assert "outside the protection" in sandbox.card_note("Bash", proj)


def test_engine_not_wrapped_when_setting_is_off(proj):
    settings.save({"sandbox_enabled": False})
    cmd = ["claude.exe"]
    assert sandbox.engine_command(cmd, proj) is cmd


@windows_only
def test_launcher_runs_the_engine_inside_the_box(proj):
    """The real claude.exe through the real launcher: it answers through the pipes, and is in the container."""
    exe = auth.cli_path()
    if not exe or not Path(exe).exists():
        pytest.skip("claude.exe is not installed")
    sid = sandbox.prepare(proj, engine=True)
    flag = proj.parent / "flag"
    cmd = [sys.executable, str(sandbox.LAUNCHER), sid, str(sandbox.temp_dir(proj)), str(flag), "--", str(exe), "--version"]
    r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, timeout=120, stdin=subprocess.PIPE)
    assert r.returncode == 0 and "Claude Code" in r.stdout, r.stdout + r.stderr
    assert not flag.exists(), "the launcher fell back to running the engine outside the box"
    sandbox.cleanup(proj)


@windows_only
def test_launcher_falls_back_and_leaves_a_flag_when_the_box_fails(proj):
    flag = proj.parent / "flag"
    cmd = [sys.executable, str(sandbox.LAUNCHER), "S-1-0-0-not-a-container", str(proj), str(flag), "--",
           sys.executable, "-c", "print('ran')"]
    r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, timeout=120, stdin=subprocess.PIPE)
    assert r.returncode == 0 and "ran" in r.stdout, r.stdout + r.stderr
    assert flag.exists()


@windows_only
def test_app_with_its_own_window_starts_from_a_hidden_desktop(proj, monkeypatch):
    """When AI Team Lite itself is on a desktop other than the normal one (as when tests run out of sight), a boxed
    program with its own console used to hang before it started: the box had no right to that desktop."""
    from app import sandbox_win
    monkeypatch.setattr(sandbox_win, "current_desktop", lambda: "AITeamLiteTests")
    p = sandbox.start_process([sys.executable, "-c", "import sys; sys.exit(3)"], proj, proj, capture=False,
                              new_console=True)
    try:
        for _ in range(150):
            if p.poll() is not None:
                break
            time.sleep(0.1)
        assert p.returncode == 3
    finally:
        p.kill()
        sandbox.cleanup(proj)
