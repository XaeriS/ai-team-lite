"""AI Team Lite: a separate app from AI Team (own data, keys, sign-in, install, instance) with the expert features removed."""
import re
from pathlib import Path

from app import main, paths, secrets_store, settings, tooling
from app import engine as engine_mod
from app.events import EventHub
from fakeclaude import FakeClient

ROOT = Path(__file__).resolve().parent.parent


def test_separate_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("AITEAM_HOME", raising=False)
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "AI Team Lite"            # AI Team uses %APPDATA%\AI Team
    assert secrets_store.SERVICE == "AI Team Lite"                   # its own entries in Credential Manager
    assert main.INSTANCE_PORT != 47823                               # both apps can run at the same time
    nsi = (ROOT / "installer" / "ai-team.nsi").read_text(encoding="utf-8")
    assert '!define APPNAME "AI Team Lite"' in nsi and "Software\\AITeamLite" in nsi and "Uninstall\\AITeamLite" in nsi
    assert not re.search(r"Software\\AITeam(?!Lite)", nsi)
    assert "AI-Team-Lite-Setup" in (ROOT / "installer" / "build.sh").read_text(encoding="utf-8")


def test_expert_features_are_gone(isolated):
    hub = EventHub(lambda s: Path(settings.workspace()) / f"{s}.jsonl")
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, None))
    try:
        from app import projects
        p = projects.create("Lite")
        s = eng.session(p["slug"])
        opts = s.build_options()
        names = set(s.tool_handlers)
        assert not names & {"ask_brain", "delegate_code", "ensure_toolchain", "map_codebase", "query_codebase"}
        assert {"ask_council", "record_problem", "record_challenge_review", "run_security_test"} <= names
        system = opts.system_prompt["append"]
        for gone in ("Rust", "delegate_code", "ask_brain", "OmniRoute", "Graphify", "code map"):
            assert gone not in system, gone
    finally:
        eng.shutdown()
    for gone in ("strix", "omniroute", "graphify", "rust_install"):
        assert not any(gone in n for n in dir(tooling)), gone
    ui = (ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
    for gone in ("data-adv", "Advanced mode", "OmniRoute", "Strix", "renderChanges", "cleanBtn", "setLang", "roleBox"):
        assert gone not in ui, gone
