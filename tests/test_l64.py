"""L64: once an iteration passes its full test run and is saved, its results replace the older ones."""
import json
import os
import sys
import time

from app import freshtests, iterations, projects
from app.tools_common import add_test_result, load_tests
from test_checkpoints import _project


def _old(path, seconds_ago):
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def test_refresh_keeps_one_result_per_test_and_drops_stale_ones(isolated):
    slug, folder = _project("Refresh")
    started = time.time() - 60
    shots = folder / "reports" / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    rows = [
        {"name": "imports invoices", "status": "failed", "time": started - 500},
        {"name": "imports invoices", "status": "passed", "time": started - 400, "screenshot": "reports/screenshots/a-old.png"},
        {"name": "monthly totals", "status": "failed", "time": started - 300},             # never re-recorded: stale
        {"name": "export", "status": "passed", "time": started + 10, "screenshot": "reports/screenshots/b-new.png"},
        {"name": "loose old picture", "status": "screenshot", "time": started - 200},
        {"name": "loose new picture", "status": "screenshot", "time": started + 20},
    ]
    (folder / "reports" / "test_results.json").write_text(json.dumps(rows))
    for name, age in (("a-old.png", 400), ("b-new.png", 0), ("c-orphan-old.png", 900), ("d-loose-new.png", 0)):
        (shots / name).write_bytes(b"png")
        if age:
            _old(shots / name, age)
    logs = folder / "reports" / "test-logs"
    logs.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (logs / f"2026111{i}-run.log").write_text("x")
        _old(logs / f"2026111{i}-run.log", 500 - i * 100)

    out = freshtests.refresh(folder, 7, started)
    kept = {r["name"]: r for r in load_tests(folder)}
    assert set(kept) == {"imports invoices", "export", "loose new picture"}
    assert kept["imports invoices"]["status"] == "passed" and kept["imports invoices"]["confirmed_in"] == 7
    assert "iteration" not in kept["imports invoices"]                 # recorded before this iteration
    assert kept["export"]["iteration"] == 7 and kept["export"]["confirmed_in"] == 7
    assert sorted(p.name for p in shots.glob("*.png")) == ["a-old.png", "b-new.png", "d-loose-new.png"]
    assert [p.name for p in logs.glob("*.log")] == ["20261114-run.log"]  # only the newest run
    assert out == {"results": 3, "screenshots": 1, "logs": 4, "kept": 3}
    assert freshtests.refresh(folder, 7, started)["results"] == 0          # nothing left to do


def test_commit_refreshes_the_results(isolated):
    slug, folder = _project("Commit")
    its = iterations.load(slug)
    its.append({"n": 4, "name": "Report", "goal": "", "requirements": [], "status": "building", "after": "continue",
                "started": time.time() - 30, "tasks": []})
    projects.update(slug, iterations=its)
    add_test_result(folder, {"name": "report", "status": "failed"})
    add_test_result(folder, {"name": "report", "status": "passed"})
    (folder / "tests" / "test_report.py").write_text("def test_report():\n    assert True\n")
    r = iterations.commit(slug, 4, "Report works.", f'"{sys.executable}" -m pytest -q tests', "done")
    assert r["ok"], r["output"][-500:]
    assert [(t["name"], t["status"], t.get("iteration")) for t in load_tests(folder)] == [("report", "passed", 4)]
    it = next(x for x in iterations.load(slug) if x["n"] == 4)
    assert it["fresh_tests"]["results"] == 1
    tracked = iterations._git(folder, "show", "--stat", "HEAD").stdout
    assert "reports/test_results.json" in tracked                           # saved with the iteration
