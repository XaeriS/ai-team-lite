"""L47 / REQ-22: sandbox investigation. The probe tool, its saved results, and the options report."""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools" / "sandbox_probe.py"
RESULTS = ROOT / "reports" / "L47-probe-results.json"
OPTIONS_MD = ROOT / "reports" / "L47-sandbox-options.md"

REQUIRED_CHECKS = {"python", "git", "pip", "pytest", "claude", "write_outside_refused", "read_private_refused"}
STATUSES = {"pass", "fail", "error"}


def _load():
    assert PROBE.exists(), "tools/sandbox_probe.py does not exist"
    spec = importlib.util.spec_from_file_location("sandbox_probe_under_test", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_req22_probe_file_exists_and_imports_without_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(ROOT.rglob("*.json")) | set(tmp_path.rglob("*"))
    _load()
    assert set(ROOT.rglob("*.json")) | set(tmp_path.rglob("*")) == before   # nothing written on import


def test_req22_probe_exposes_checks_with_required_names():
    mod = _load()
    assert isinstance(mod.CHECKS, list)
    assert REQUIRED_CHECKS <= set(mod.CHECKS)


def test_req22_probe_exposes_options_appcontainer_and_restricted_token():
    mod = _load()
    names = list(mod.OPTIONS)
    assert "appcontainer" in names
    assert "restricted_token" in names


@pytest.mark.parametrize("option", ["appcontainer", "restricted_token"])
def test_req22_probe_dry_run_returns_every_check_with_status_and_detail(option):
    mod = _load()
    res = mod.run(option, dry_run=True)
    assert isinstance(res, dict)
    assert set(mod.CHECKS) <= set(res)
    for name in mod.CHECKS:
        entry = res[name]
        assert entry["status"] in STATUSES, (name, entry)
        assert isinstance(entry["detail"], str)


def test_req22_probe_dry_run_does_not_touch_windows(monkeypatch, tmp_path):
    mod = _load()
    import subprocess
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("spawned")))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("spawned")))
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))   # conftest's autouse fixture already put data/ here
    mod.run("appcontainer", dry_run=True)
    assert not calls
    assert set(tmp_path.rglob("*")) == before


# ---- saved results of the real run on this PC ----

def _results():
    assert RESULTS.exists(), "reports/L47-probe-results.json is missing: run the probe on this PC"
    return json.loads(RESULTS.read_text(encoding="utf-8"))


def _option(data, name):
    # accept {"appcontainer": {...}} or {"options": {"appcontainer": {...}}}
    opts = data.get("options", data) if isinstance(data, dict) else {}
    return opts.get(name)


def test_req22_results_json_is_valid_and_has_appcontainer():
    assert isinstance(_option(_results(), "appcontainer"), dict)


def test_req22_results_every_check_has_status_and_nonempty_detail():
    data = _results()
    opts = data.get("options", data)
    seen = 0
    for oname, checks in opts.items():
        if not isinstance(checks, dict):
            continue
        for cname, entry in checks.items():
            if not isinstance(entry, dict):
                continue
            seen += 1
            assert entry.get("status") in STATUSES, (oname, cname, entry)
            assert str(entry.get("detail", "")).strip(), (oname, cname, "empty detail")
    assert seen >= len(REQUIRED_CHECKS)
    app = _option(data, "appcontainer")
    assert REQUIRED_CHECKS <= set(app)


def test_req22_results_restricted_token_present_when_appcontainer_has_failure():
    data = _results()
    app = _option(data, "appcontainer")
    if any(e.get("status") in ("fail", "error") for e in app.values() if isinstance(e, dict)):
        rt = _option(data, "restricted_token")
        assert isinstance(rt, dict), "appcontainer had a fail/error, so restricted_token must be probed too"
        assert REQUIRED_CHECKS <= set(rt)


# ---- the options report for the owner ----

def _md():
    assert OPTIONS_MD.exists(), "reports/L47-sandbox-options.md is missing"
    return OPTIONS_MD.read_text(encoding="utf-8")


def _section(text, pattern):
    """Text from the heading matching pattern up to the next heading of the same or higher level."""
    m = re.search(rf"^(#+)\s*[^\n]*{pattern}[^\n]*$", text, re.I | re.M)
    assert m, f"no heading for {pattern}"
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(rf"^#{{1,{level}}}\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


@pytest.mark.parametrize("label,pattern", [
    ("A", r"restricted token"),
    ("B", r"AppContainer"),
    ("C", r"Windows Sandbox"),
    ("D", r"WSL"),
    ("E", r"separate (Windows )?user"),
])
def test_req22_options_report_covers_each_option(label, pattern):
    sec = _section(_md(), pattern).lower()
    assert "protect" in sec, f"option {label}: what it protects is missing"
    assert re.search(r"user (must|has to|needs to)|you (must|have to|need to)|what the user must do|needs from you", sec), \
        f"option {label}: what the user must do is missing"
    assert "edition" in sec or ("home" in sec and "pro" in sec), f"option {label}: Windows editions missing"


def test_req22_options_report_has_recommendation_section():
    sec = _section(_md(), r"Recommendation")
    assert len(sec.strip()) > 40


def test_req22_options_report_states_owner_has_windows_11_home():
    assert re.search(r"Windows 11 Home", _md(), re.I)


def test_req22_options_report_ends_with_question_to_owner():
    text = _md().rstrip()
    last = text.splitlines()[-1].strip().strip("*_> ").strip()
    assert last.endswith("?"), f"last line is not a question: {last!r}"
    assert re.search(r"This protects .+ but not .+, and needs .+ from you once\. Do you want it\?\s*$",
                     last, re.S), last
