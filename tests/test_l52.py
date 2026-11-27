"""L52b-d: a real Windows installer for a finished project, automatic tests on GitHub, and a GitHub release.
PyInstaller and Inno Setup only run on Windows: here their runs are simulated; what they are given is checked."""
import asyncio
import io
import json
from pathlib import Path

import pytest

from app import engine as engine_mod, github, iterations, packaging, paths, projects, shipping
from app.events import EventHub
from fakeclaude import FakeClient
from test_checkpoints import _project


def test_setup_script_is_a_usual_installer():
    s = packaging.iss_script(name="Printer Queue", version="1.4", publisher="Fred", app_id="ABC-123",
                             dist=Path("C:/p/build/package/dist/Printer Queue"), icon=Path("C:/p/packaging/icon.ico"),
                             out_dir=Path("C:/p/delivery"), out_name="Printer-Queue-Setup-1.4")
    assert "PrivilegesRequired=lowest" in s and "DefaultDirName={localappdata}\\Programs\\{#AppName}" in s   # no admin
    assert 'Name: "desktopicon"; Description: "Create a desktop shortcut"' in s                            # the box
    assert 'Name: "{userdesktop}\\{#AppName}"; Filename: "{app}\\{#AppExe}"; Tasks: desktopicon' in s
    assert 'Name: "{group}\\{#AppName}"' in s and 'Name: "{group}\\Uninstall {#AppName}"' in s               # Start menu
    assert f"SetupIconFile={Path('C:/p/packaging/icon.ico')}" in s and "UninstallDisplayIcon={app}\\{#AppExe}" in s     # its icon
    assert "AppId={{ABC-123}" in s and 'Description: "Start {#AppName} now"' in s


def test_names_versions_and_start_file(isolated, tmp_path):
    assert packaging.safe_name("Cyber assistant: v2!") == "Cyber assistant v2" and packaging.safe_name("!!!") == "App"
    slug, folder = _project("Versioned")
    assert packaging.version_of(slug) == "1.3" and packaging.version_of(slug, "2.0.1") == "2.0.1"
    assert packaging.version_of(slug, "latest") == "1.3"
    (folder / "app.py").write_text("print(1)\n")
    assert packaging.entry_point(folder).name == "app.py"
    with pytest.raises(packaging.PackagingError):
        packaging.entry_point(folder, "../outside.py")
    empty = tmp_path / "empty"; empty.mkdir()
    with pytest.raises(packaging.PackagingError, match="No start file"):
        packaging.entry_point(empty)


def test_icon_made_from_the_first_letter_or_the_projects_own(isolated):
    slug, folder = _project("Icons")
    ico = packaging.make_icon(folder, "Printer Queue")
    data = ico.read_bytes()
    assert ico == folder / "packaging" / "icon.ico" and data[:4] == b"\x00\x00\x01\x00" and int.from_bytes(data[4:6], "little") >= 5
    (folder / "assets").mkdir(exist_ok=True)
    (folder / "assets" / "icon.ico").write_bytes(data)
    assert packaging.make_icon(folder, "Printer Queue") == folder / "assets" / "icon.ico"


def test_pyinstaller_is_given_the_app_and_its_folders(isolated):
    slug, folder = _project("Bundled")
    (folder / "main.py").write_text("import tkinter\n")
    (folder / "templates").mkdir(); (folder / "static").mkdir()
    cmd = packaging.pyinstaller_command(Path("py"), folder, folder / "main.py", "Bundled", folder / "i.ico", True)
    assert cmd[:3] == ["py", "-m", "PyInstaller"] and "--onedir" in cmd and "--windowed" in cmd
    adds = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--add-data"]
    assert [a.rsplit(":" if ":" in a[2:] else ";", 1)[-1] for a in adds] == ["templates", "static"]
    assert cmd[-1].endswith("main.py")


def _fake_tools(folder, name, fail=""):
    calls = []

    def runner(cmd, cwd, timeout):
        calls.append(cmd)
        if cmd[1:3] == ["-m", "PyInstaller"]:
            if fail == "pyinstaller":
                return 1, "ModuleNotFoundError: No module named 'flask'"
            d = folder / "build" / "package" / "dist" / name
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.exe").write_bytes(b"MZ")
            return 0, "Building EXE completed successfully."
        if fail == "inno":
            return 2, "Error on line 12"
        iss = Path(cmd[-1]).read_text()
        out = iss.split("OutputDir=")[1].split("\n")[0]
        base = iss.split("OutputBaseFilename=")[1].split("\n")[0]
        (Path(out) / f"{base}.exe").write_bytes(b"MZ" * 1000)
        return 0, "Successful compile"
    return runner, calls


def test_build_makes_the_installer(isolated):
    slug, folder = _project("Printer queue")
    (folder / "main.py").write_text("print('hi')\n")
    runner, calls = _fake_tools(folder, "Printer queue")
    info = packaging.build(slug, runner=runner, smoke=lambda exe: (True, "running"))
    assert info["installer"] == "delivery/Printer-queue-Setup-1.3.exe" and info["version"] == "1.3" and info["bytes"] == 2000
    assert len(calls) == 2 and "--console" in calls[0]                    # no window of its own: keeps its console
    assert (folder / "packaging" / "setup.iss").exists() and (folder / "packaging" / "icon.ico").exists()
    ignored = (folder / ".gitignore").read_text().splitlines()
    assert "build/" in ignored and "delivery/" in ignored                 # big files never go into Git
    assert projects.load(slug)["package"]["installer"] == info["installer"]
    assert "Successful compile" in (folder / "reports" / "package.log").read_text()


@pytest.mark.parametrize("fail,words", [("pyinstaller", "could not bundle"), ("inno", "could not make the setup")])
def test_build_failures_are_explained(isolated, fail, words):
    slug, folder = _project("Broken " + fail)
    (folder / "main.py").write_text("print('hi')\n")
    runner, _ = _fake_tools(folder, "Broken " + fail, fail)
    with pytest.raises(packaging.PackagingError, match=words):
        packaging.build(slug, runner=runner, smoke=lambda exe: (True, ""))


def test_app_that_does_not_start_is_not_packaged(isolated):
    slug, folder = _project("Crashes")
    (folder / "main.py").write_text("print('hi')\n")
    runner, calls = _fake_tools(folder, "Crashes")
    with pytest.raises(packaging.PackagingError, match="did not start"):
        packaging.build(slug, runner=runner, smoke=lambda exe: (False, "Traceback: ImportError"))
    assert len(calls) == 1                                                # no setup made for a broken app


def test_what_is_missing_first(isolated, monkeypatch):
    monkeypatch.setattr(packaging, "find_iscc", lambda: None)            # pretend Inno Setup is not installed
    slug, folder = _project("Missing")
    missing = packaging.needs(slug)
    assert any(".venv" in m for m in missing) and any("Inno Setup" in m and "winget" in m for m in missing)


def test_ci_workflow(isolated):
    slug, folder = _project("Tested")
    its = iterations.load(slug)
    its[-1]["test_command"] = '"C:/Programs/AI Team Lite/python/python.exe" -m pytest -q tests'
    projects.update(slug, iterations=its)
    assert shipping.ci_command(slug) == "python -m pytest -q tests"
    out = shipping.write_ci(slug)
    y = out.read_text()
    assert out == folder / ".github" / "workflows" / "tests.yml"
    assert "runs-on: windows-latest" in y and "pip install -r requirements.txt" in y and "run: python -m pytest -q tests" in y


def test_release_uploads_the_installer(isolated, monkeypatch, tmp_path):
    asset = tmp_path / "App-Setup-1.3.exe"; asset.write_bytes(b"MZ123")
    seen = {}
    monkeypatch.setattr(github, "token", lambda: "tok")
    monkeypatch.setattr(github, "_request", lambda method, url, token, body=None: (seen.update(api=(method, url, body)) or
                        ({"html_url": "https://github.com/me/app/releases/tag/v1.3",
                          "upload_url": "https://uploads.github.com/repos/me/app/releases/9/assets{?name,label}"}, {})))

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(github.urllib.request, "urlopen", lambda req, timeout=0: seen.update(upload=req) or Resp(b"{}"))
    url = github.create_release("me/app", "v1.3", "App 1.3", "First version", asset)
    assert url.endswith("/v1.3") and seen["api"][2]["tag_name"] == "v1.3" and seen["api"][2]["target_commitish"] == "main"
    req = seen["upload"]
    assert req.full_url == "https://uploads.github.com/repos/me/app/releases/9/assets?name=App-Setup-1.3.exe"
    assert req.data == b"MZ123" and req.get_header("Content-type") == "application/octet-stream"


def test_workflow_permission_check(monkeypatch):
    monkeypatch.setattr(github, "check", lambda: {"scopes": "repo, gist"})
    assert github.needs_workflow_scope()
    monkeypatch.setattr(github, "check", lambda: {"scopes": "repo, workflow"})
    assert not github.needs_workflow_scope()
    monkeypatch.setattr(github, "check", lambda: {"scopes": ""})          # fine-grained key: cannot tell
    assert not github.needs_workflow_scope()


def test_tools(isolated, monkeypatch):
    slug, folder = _project("Delivered")
    eng = engine_mod.Engine(EventHub(projects.history_file), client_factory=lambda o: FakeClient(o, None))
    try:
        s = eng.session(slug)
        s.build_options()
        call = lambda name, args=None: asyncio.run(s.tool_handlers[name](args or {}))["content"][0]["text"]
        if not paths.IS_WINDOWS:
            assert "Windows only" in call("package_app")
        assert "no GitHub repository" in call("github_release")
        projects.update(slug, github_repo="me/delivered", github_clone="https://example.invalid/x.git")
        assert "call package_app first" in call("github_release")
        monkeypatch.setattr(github, "needs_workflow_scope", lambda: True)
        out = call("add_ci")
        assert "Wrote .github/workflows/tests.yml" in out and "scopes=repo,workflow" in out
        h = projects.history_file(slug)
        assert not h.exists() or all(json.loads(x)["type"] != "error" for x in h.read_text().splitlines())
    finally:
        eng.shutdown()
