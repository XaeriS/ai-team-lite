"""REQ-22: the team's commands run inside a Windows AppContainer (the "sandbox"). Written BEFORE the code
(app/sandbox.py does not exist yet, so these fail until the Developer builds it).

What the owner asked for: a command the team runs may change files only in the project folder, and cannot read
private files elsewhere (keys, documents). Network stays open. Tests prove that on a real container.

Assumed API (app/sandbox.py; keep it this simple):
  enabled() -> bool                         the setting "sandbox_enabled" (default True)
  available() -> bool                       True on Windows when an AppContainer can be created
  access_list(project_dir: Path) -> list[tuple[Path, str]]
        what the container may touch. Mode letters: "RW" read+write, "RX" read+run, "RA" read attributes only.
        - project_dir: "RW"
        - project_dir.parent: "RA" (pytest needs it); nothing else of the user's profile or the drive
        - the bundled Python folder and the Git folder: "RX" (the Python entry covers the interpreter in use, also
          the base Python and packages a virtual environment points to, so tests can run with it)
        - a temp folder for the project (TEMP/TMP of the command point to it): "RW"
  start_process(cmd: list[str], cwd: Path, project_dir: Path, *, env: dict | None = None)
        starts cmd inside the container (no window) and returns an object with
        .communicate(timeout=None) -> (stdout_text, stderr_text)  and  .returncode (after communicate).
        Raises OSError when the container cannot be set up.
  card_note(tool_name: str) -> str          one line for approval cards; "" when the command is protected or the tool
        runs no command; otherwise one line containing "outside the protection" (sandbox off or not applied).
  testrun.run(command, cwd) uses sandbox.start_process when sandbox.enabled() and sandbox.available(), else the
  old way. The approval card dict gets the line as card["sandbox_note"] (app/session_approvals.py).
"""
import re
import sys
from pathlib import Path

import pytest

from app import paths, settings, testrun

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="AppContainer exists only on Windows")


def _sb():
    from app import sandbox
    return sandbox


def _py() -> str:
    return str(paths.bundled_python())


def _run(cmd, cwd, project):
    p = _sb().start_process(cmd, cwd, project)
    out, err = p.communicate(timeout=120)
    return p.returncode, (out or "") + (err or "")


@pytest.fixture
def box(isolated):
    """A project folder, a folder next to it (outside) and a private canary file outside."""
    proj = isolated / "work" / "proj"
    outside = isolated / "work" / "outside"
    proj.mkdir(parents=True)
    outside.mkdir()
    canary = outside / "canary.txt"
    canary.write_text("REQ22-CANARY-PRIVATE", encoding="utf-8")
    return proj, outside, canary


# ------------------------------------------------------------------ (3) what the container may touch
def _under(path: Path, entry: Path) -> bool:
    try:
        path.resolve().relative_to(entry.resolve())
        return True
    except ValueError:
        return False


def test_req22_access_list_project_is_read_write(box):
    proj, _, _ = box
    acc = {Path(p).resolve(): m for p, m in _sb().access_list(proj)}
    assert acc[proj.resolve()] == "RW"


def test_req22_access_list_parent_is_read_attributes_only(box):
    proj, _, _ = box
    acc = {Path(p).resolve(): m for p, m in _sb().access_list(proj)}
    assert acc[proj.parent.resolve()] == "RA"


def test_req22_access_list_python_and_git_are_read_and_run(box):
    proj, _, _ = box
    acc = [(Path(p), m) for p, m in _sb().access_list(proj)]
    py = Path(_py())
    assert any(m == "RX" and _under(py, p) for p, m in acc), "bundled Python must be readable and runnable"
    git = paths.git_exe()
    if git:
        assert any(m == "RX" and _under(Path(git), p) for p, m in acc), "Git must be readable and runnable"


def test_req22_access_list_has_a_temp_folder(box):
    proj, _, _ = box
    acc = _sb().access_list(proj)
    assert any(m == "RW" and Path(p).resolve() != proj.resolve() for p, m in acc), "a writable temp folder is needed"


def test_req22_access_list_grants_nothing_in_the_user_profile(box):
    proj, outside, canary = box
    home = Path.home().resolve()
    for p, mode in _sb().access_list(proj):
        p = Path(p).resolve()
        assert p != home and not _under(home, p) or mode == "RA", f"{p} ({mode}) would expose the whole profile"
        if mode == "RA":
            # "read attributes" is granted on that folder only (no inheritance), so it covers nothing inside it;
            # it is allowed for the project's own parent and nowhere else
            assert p == proj.parent.resolve(), f"{p} (RA) is not the project's parent"
            continue
        for private in ("Documents", "Desktop", "Downloads", "AppData", ".ssh"):
            assert not _under(home / private, p), f"{p} ({mode}) covers {private}"
        assert not _under(outside, p) and not _under(canary, p), f"{p} ({mode}) covers a private folder"
        assert mode in ("RW", "RX", "RA")


def test_req22_access_list_only_the_project_is_writable_besides_temp(box):
    proj, _, _ = box
    writable = [Path(p) for p, m in _sb().access_list(proj) if m == "RW"]
    assert len(writable) <= 2, f"too many writable places: {writable}"
    assert proj in [w.resolve() for w in writable] or proj.resolve() in [w.resolve() for w in writable]


# ------------------------------------------------------------------ (1) and (2) a real container
@windows_only
def test_req22_process_in_container_can_write_in_project(box):
    proj, _, _ = box
    assert _sb().available(), "AppContainer should be available on this Windows PC"
    target = proj / "made_inside.txt"
    code, out = _run([_py(), "-c", f"open({str(target)!r}, 'w').write('ok'); print('WROTE')"], proj, proj)
    assert code == 0, out
    assert target.read_text() == "ok"


@windows_only
def test_req22_process_in_container_cannot_write_outside_project(box):
    proj, outside, _ = box
    target = outside / "escape.txt"
    code, out = _run([_py(), "-c", f"open({str(target)!r}, 'w').write('x'); print('WROTE')"], proj, proj)
    assert not target.exists(), "the container let a command write outside the project"
    assert code != 0 and "WROTE" not in out
    assert re.search(r"PermissionError|Access is denied", out), out


@windows_only
def test_req22_process_in_container_cannot_read_a_private_file_outside(box):
    proj, _, canary = box
    code, out = _run([_py(), "-c", f"print(open({str(canary)!r}).read())"], proj, proj)
    assert "REQ22-CANARY-PRIVATE" not in out, "the container let a command read a private file"
    assert code != 0
    assert re.search(r"PermissionError|Access is denied", out), out


@windows_only
def test_req22_cleanup_removes_container_access_from_project(box):
    """After the work, the project folder is not left with rights for a profile nobody uses."""
    import subprocess
    proj, _, _ = box
    _run([_py(), "-c", "print(1)"], proj, proj)
    sb = _sb()
    if hasattr(sb, "cleanup"):
        sb.cleanup(proj)
    shown = subprocess.run(["icacls", str(proj)], capture_output=True, text=True).stdout
    assert "S-1-15-2-" not in shown, "AppContainer rights were left on the project folder after cleanup"


def _container_rights(folder: Path, sid: str) -> str:
    """The icacls lines, for folder and everything in it, that name this container (by SID or by name)."""
    import subprocess
    from app import sandbox_win
    shown = subprocess.run(["icacls", str(folder), "/T", "/C", "/Q"], capture_output=True, text=True).stdout
    name = sandbox_win.account_name(sid)
    return "\n".join(ln for ln in shown.splitlines() if sid in ln or (name and name in ln) or "S-1-15-2-" in ln)


@windows_only
def test_req22_rights_cover_old_files_and_cleanup_removes_them_from_every_file(box):
    """A file that existed before the box was set up can be changed by it; after cleanup neither that file nor a
    file (or folder) the box made keeps any right for the container."""
    proj, _, _ = box
    sb = _sb()
    (proj / "sub").mkdir()
    old = proj / "sub" / "old.txt"
    old.write_text("before", encoding="utf-8")
    sid = sb.prepare(proj)
    made = proj / "made" / "new.txt"
    code, out = _run([_py(), "-c", f"import os; open({str(old)!r}, 'a').write('+box'); "
                                   f"os.makedirs({str(made.parent)!r}); open({str(made)!r}, 'w').write('x')"],
                     proj, proj)
    assert code == 0, out
    assert old.read_text(encoding="utf-8") == "before+box" and made.exists()
    sb.cleanup(proj)
    left = _container_rights(proj, sid)
    assert not left, f"container rights left after cleanup:\n{left}"


@windows_only
def test_req22_leftovers_from_a_crash_are_cleaned_at_start_up(box, monkeypatch):
    proj, _, _ = box
    sb = _sb()
    sid = sb.prepare(proj)
    monkeypatch.setattr(sb, "_prepared", {})          # as after a crash: this run knows nothing of it
    sb.cleanup_leftovers()
    assert not _container_rights(proj, sid)
    assert not sb.temp_dir(proj).exists()


def test_req22_deleting_a_project_cleans_its_sandbox(isolated, monkeypatch):
    from app import projects
    sb = _sb()
    seen = []
    monkeypatch.setattr(sb, "cleanup", lambda folder: seen.append(Path(folder)))
    p = projects.create("Box Gone")
    projects.delete(p["slug"], "all")
    assert seen == [projects.project_dir(p["slug"])]


# ------------------------------------------------------------------ (4) test runs
def _project_with_test(proj: Path, body: str) -> str:
    (proj / "test_it.py").write_text(body, encoding="utf-8")
    return f'"{_py()}" -m pytest -q -p no:cacheprovider test_it.py'


@windows_only
def test_req22_testrun_in_container_passes_a_normal_test(box):
    proj, _, _ = box
    settings.save({"sandbox_enabled": True})
    cmd = _project_with_test(proj, "def test_ok():\n    assert 1 + 1 == 2\n")
    passed, out = testrun.run(cmd, proj, timeout=240)
    assert passed, out
    assert "1 passed" in out


@windows_only
def test_req22_testrun_in_container_fails_a_test_that_writes_outside(box):
    proj, outside, _ = box
    settings.save({"sandbox_enabled": True})
    target = outside / "from_test.txt"
    cmd = _project_with_test(proj, f"def test_escape():\n    open({str(target)!r}, 'w').write('x')\n")
    passed, out = testrun.run(cmd, proj, timeout=240)
    assert not target.exists(), "a test run wrote outside the project"
    assert not passed, out
    assert re.search(r"PermissionError|Access is denied", out), out


@windows_only
def test_req22_testrun_in_container_can_use_private_temp_folders(box):
    """Python 3.12.4+ makes mkdir(mode=0o700) folders private to the user, which shut out the container from the
    folders it had just made: pytest's cache and every test using tmp_path failed with Access is denied."""
    proj, _, _ = box
    settings.save({"sandbox_enabled": True})
    (proj / "test_it.py").write_text("import os, tempfile\n"
                                     "def test_tmp(tmp_path):\n    (tmp_path / 'a.txt').write_text('x')\n"
                                     "def test_mkdtemp():\n    d = tempfile.mkdtemp(dir='.')\n"
                                     "    open(os.path.join(d, 'b.txt'), 'w').write('y')\n", encoding="utf-8")
    passed, out = testrun.run(f'"{_py()}" -m pytest -q test_it.py', proj, timeout=240)
    assert passed, out
    assert "2 passed" in out and "Access is denied" not in out


# ------------------------------------------------------------------ (5) off means no container
def test_req22_testrun_uses_the_container_when_on(box, monkeypatch):
    proj, _, _ = box
    settings.save({"sandbox_enabled": True})
    sb = _sb()
    monkeypatch.setattr(sb, "available", lambda: True)
    calls = []

    class Fake:
        returncode = 0

        def communicate(self, timeout=None):
            return "1 passed in 0.01s", ""

    monkeypatch.setattr(sb, "start_process", lambda *a, **k: calls.append(a) or Fake())
    passed, out = testrun.run("pytest -q", proj)
    assert calls, "tests did not go through the sandbox although it is on"
    assert passed


def test_req22_setting_off_means_no_container_is_used(box, monkeypatch):
    proj, _, _ = box
    settings.save({"sandbox_enabled": False, "tests_hidden_desktop": False})
    sb = _sb()
    assert sb.enabled() is False

    def boom(*a, **k):
        raise AssertionError("the sandbox was used although the setting is off")

    monkeypatch.setattr(sb, "start_process", boom)
    passed, out = testrun.run(f'"{sys.executable}" -c "print(42)"', proj)
    assert passed and "42" in out


# ------------------------------------------------------------------ (6) the approval card says it
def test_req22_card_note_is_empty_when_command_is_protected(monkeypatch):
    sb = _sb()
    settings.save({"sandbox_enabled": True})
    monkeypatch.setattr(sb, "available", lambda: True)
    assert sb.card_note("Bash") == ""


def test_req22_card_note_when_sandbox_is_off(monkeypatch):
    sb = _sb()
    settings.save({"sandbox_enabled": False})
    note = sb.card_note("Bash")
    assert "outside the protection" in note
    assert "\n" not in note.strip(), "one clear line"


def test_req22_card_note_when_sandbox_not_applied(monkeypatch):
    sb = _sb()
    settings.save({"sandbox_enabled": True})
    monkeypatch.setattr(sb, "available", lambda: False)
    note = sb.card_note("Bash")
    assert "outside the protection" in note
    assert "\n" not in note.strip()


def test_req22_card_note_not_shown_for_tools_that_run_no_command(monkeypatch):
    sb = _sb()
    settings.save({"sandbox_enabled": False})
    assert sb.card_note("Read") == ""


def test_req22_approval_flow_puts_the_note_on_the_card():
    src = (Path(__file__).resolve().parent.parent / "app" / "session_approvals.py").read_text(encoding="utf-8")
    assert "card_note" in src and "sandbox_note" in src, "approval cards must carry the sandbox line"
