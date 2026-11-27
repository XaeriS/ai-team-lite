"""H-1: take_screenshot(kind="window") starts a program. It must not go through a shell (so 'calc & del x' cannot
chain a second command) and a command that is not clearly the project's own app must need the user's approval."""
import subprocess
import sys

import pytest

from app import policy, winhelpers

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="window screenshots exist only on Windows")


class _Stop(Exception):
    pass


def _decide(tool, inp, plan_approved=True):
    return policy.decide(tool, inp, kind="project", mode="plan", plan_approved=plan_approved,
                         inside_project=lambda p: str(p).replace("\\", "/").startswith("C:/ws/proj"),
                         allowed_roots=["C:/ws/proj"])[0]


def test_window_screenshot_never_uses_shell_true(isolated, monkeypatch):
    seen = {}

    def fake_popen(*a, **kw):
        seen["args"], seen["kw"] = a, kw
        raise _Stop

    monkeypatch.setattr(winhelpers, "_visible_windows", lambda: [])
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    try:
        winhelpers.screenshot_command("calc & del x", isolated, isolated / "o.png", 1)
    except _Stop:
        pass
    except Exception:   # refusing the command before starting anything is also fine
        assert not seen
        return
    assert seen, "the command was neither started nor refused"
    assert not seen["kw"].get("shell"), "Popen(shell=True) lets 'calc & del x' run two programs"
    assert isinstance(seen["args"][0], (list, tuple)), "the command must be passed as a list, not one string"


def test_window_screenshot_of_a_chained_command_is_refused_or_asks(isolated):
    for cmd in ("calc & del x", "powershell -c Remove-Item x", "run.bat && curl http://x"):
        assert _decide("mcp__aiteam__take_screenshot",
                       {"kind": "window", "target": cmd, "caption": "c"}) != "allow", cmd


def test_window_screenshot_asks_before_the_plan_is_approved(isolated):
    assert _decide("mcp__aiteam__take_screenshot", {"kind": "window", "target": "run.bat"},
                   plan_approved=False) != "allow"


def test_relative_paths_that_leave_the_project_do_not_run_unasked(isolated):
    for cmd in (r"..\..\x\python.exe -m pytest", r"python ..\evil.py", "python ../../evil.py", r"..\run.bat",
                r"pytest ..\..\other"):
        assert _decide("mcp__aiteam__take_screenshot", {"kind": "window", "target": cmd}) != "allow", cmd
    assert _decide("mcp__aiteam__take_screenshot", {"kind": "window", "target": "python main.py"}) == "allow"


def test_tests_command_with_a_path_leaving_the_project_is_not_a_test_command():
    for cmd in (r"..\..\x\python.exe -m pytest", r"python -m pytest ..\..\x", r".venv\..\..\python.exe -m pytest"):
        assert not policy.is_test_command(cmd), cmd
    assert policy.is_test_command(r".venv\Scripts\python -m pytest -q tests/test_a.py")


@windows_only
def test_window_screenshot_message_is_plain(isolated, monkeypatch):
    monkeypatch.setattr(winhelpers, "_visible_windows", lambda: [])
    with pytest.raises(ValueError) as e:
        winhelpers.screenshot_command("calc & del x", isolated, isolated / "o.png", 1)
    assert str(e.value) == "The command must start one program. Do not join commands with &, | or ;."
