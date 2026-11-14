"""L48: what an iteration changed, in plain words, and going back to an earlier iteration without losing anything."""
import time

import pytest

from app import checkpoints, iterations, projects, settings


def _project(name="Invoices"):
    settings.save({"setup_complete": True})
    p = projects.create(name)
    slug, folder = p["slug"], projects.project_dir(p["slug"])
    iterations._git(folder, "init", "-b", "main")
    projects.exclude_meta(folder)
    (folder / "REQUIREMENTS.md").write_text("# Requirements\n\n- **REQ-1** Import invoices from a folder\n- **REQ-2** Monthly totals\n")
    (folder / "PLAN.md").write_text("# Plan\n\nImport invoices, then totals, then export.\n")
    its = []

    def build(n, name, reqs, files, remove=()):
        for rel, text in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(text)
        for rel in remove:
            (folder / rel).unlink()
        iterations._git(folder, "add", "-A")
        iterations._git(folder, "commit", "-m", f"Iteration {n}: {name}")
        head = iterations._git(folder, "rev-parse", "--short", "HEAD").stdout.strip()
        its.append({"n": n, "name": name, "goal": "", "requirements": reqs, "status": "done", "commit": head,
                    "summary": f"{name} works.", "finished": time.time(), "after": "continue", "tasks": [{"title": "t", "files": [], "test": "", "done": True}]})
        projects.update(slug, iterations=its, plan_approved=True, phase="execute")

    build(1, "Import", ["REQ-1"], {"app/importer.py": '"""Reads invoices from a folder."""\n\ndef load():\n    return []\n',
                                   "tests/test_import.py": "def test_load():\n    assert True\n", "STATUS.md": "Import works.\n",
                                   "legacy.py": "old = 1\n"})
    build(2, "Totals", ["REQ-2"], {"app/totals.py": "def total(xs):\n    return sum(xs)\n",
                                   "app/importer.py": '"""Reads invoices from a folder."""\n\ndef load():\n    return [1, 2]\n',
                                   "tests/test_totals.py": "def test_total():\n    assert True\n", "STATUS.md": "Totals work.\n",
                                   "settings.json": "{}\n"}, remove=["legacy.py"])
    build(3, "Export", [], {"app/export.py": "def export():\n    return 'xlsx'\n", "STATUS.md": "Export works.\n"})
    return slug, folder


def test_what_an_iteration_changed_in_plain_words(isolated):
    slug, folder = _project()
    c = checkpoints.changes(slug, 2)
    assert c["name"] == "Totals" and c["requirements"] == [{"id": "REQ-2", "title": "Monthly totals"}]
    by = {g["key"]: {f["path"]: f for f in g["files"]} for g in c["groups"]}
    assert [g["label"] for g in c["groups"]] == ["The app itself", "Tests (the proof that it works)", "Documents",
                                                 "Settings and data files"]
    assert by["app"]["app/totals.py"]["change"] == "new" and by["app"]["app/importer.py"]["change"] == "changed"
    assert by["app"]["legacy.py"]["change"] == "removed" and by["app"]["app/importer.py"]["added"] == 1
    assert "tests/test_totals.py" in by["tests"] and "STATUS.md" in by["docs"] and "settings.json" in by["data"]
    assert c["files"] == 6 and c["later"] == [{"n": 3, "name": "Export"}] and c["can_go_back"]
    first = checkpoints.changes(slug, 1)                       # the very first commit: everything is new
    assert all(f["change"] == "new" for g in first["groups"] for f in g["files"]) and first["files"] >= 5
    last = checkpoints.changes(slug, 3)
    assert not last["can_go_back"], "nothing came after the last iteration"
    d = checkpoints.file_diff(slug, 2, "app/importer.py")
    assert "-    return []" in d["lines"] and "+    return [1, 2]" in d["lines"]
    with pytest.raises(checkpoints.CheckpointError):
        checkpoints.file_diff(slug, 2, "../outside.py")


def test_go_back_keeps_everything_and_can_be_undone(isolated):
    slug, folder = _project()
    (folder / "app" / "export.py").write_text("def export():\n    return 'unsaved work'\n")   # not committed yet
    (folder / ".venv").mkdir()
    (folder / ".venv" / "lib.py").write_text("ignored")
    (folder / ".gitignore").write_text(".venv/\n")
    iterations._git(folder, "add", ".gitignore"); iterations._git(folder, "commit", "-m", "ignore")
    r = checkpoints.go_back(slug, 1)
    assert r["undone"] == [{"n": 2, "name": "Totals"}, {"n": 3, "name": "Export"}] and r["kept_unsaved"]
    assert not (folder / "app" / "totals.py").exists() and not (folder / "app" / "export.py").exists()
    assert (folder / "legacy.py").exists() and "return []" in (folder / "app" / "importer.py").read_text()
    assert (folder / ".venv" / "lib.py").exists(), "files Git ignores are never touched"
    assert ".venv/" in (folder / ".gitignore").read_text(), "the newest ignore list stays"
    assert ".venv" not in iterations._git(folder, "ls-files").stdout
    assert (folder / ".aiteam" / "project.json").exists(), "AI Team Lite's own data stays"
    its = iterations.load(slug)
    assert [x["status"] for x in its] == ["done", "planned", "planned"] and "commit" not in its[1]
    assert not its[1]["tasks"][0].get("done")
    log = iterations._git(folder, "log", "--format=%s").stdout
    assert "Safety copy before going back to iteration 1" in log and "Iteration 3: Export" in log, "history is kept"
    assert "unsaved work" in iterations._git(folder, "show", f"{r['from_commit']}:app/export.py").stdout
    with pytest.raises(checkpoints.CheckpointError, match="already as it was"):
        checkpoints.go_back(slug, 1)
    # undo: everything is back, including the work that had not been saved
    u = checkpoints.undo_go_back(slug)
    assert u["to"] == 1 and "unsaved work" in (folder / "app" / "export.py").read_text()
    assert (folder / "app" / "totals.py").exists() and not (folder / "legacy.py").exists()
    assert [x["status"] for x in iterations.load(slug)] == ["done", "done", "done"]
    assert not (projects.load(slug) or {}).get("went_back")
    with pytest.raises(checkpoints.CheckpointError, match="nothing to undo"):
        checkpoints.undo_go_back(slug)


def test_undo_is_refused_once_new_work_was_built(isolated):
    slug, folder = _project()
    checkpoints.go_back(slug, 2)
    its = iterations.load(slug)
    (folder / "app" / "export2.py").write_text("x = 1\n")
    iterations._git(folder, "add", "-A"); iterations._git(folder, "commit", "-m", "Iteration 3 again")
    its[2].update(status="done", commit=iterations._git(folder, "rev-parse", "--short", "HEAD").stdout.strip())
    projects.update(slug, iterations=its)
    with pytest.raises(checkpoints.CheckpointError, match="New work was built"):
        checkpoints.undo_go_back(slug)
    with pytest.raises(checkpoints.CheckpointError, match="not finished yet"):
        checkpoints.changes(slug, 9)
