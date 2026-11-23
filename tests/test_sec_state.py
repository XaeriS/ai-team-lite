"""M-1: "the plan is approved" and the saved Launch command must not be believed just because a file inside the
project folder (which project code and the team can write) says so."""
import json

from app import projects, settings

EVIL = {"command": "evil.bat", "url": ""}


def _hand_edit(slug, **changes):
    f = projects.meta_file(slug)
    data = json.loads(f.read_text(encoding="utf-8"))
    data.update(changes)
    f.write_text(json.dumps(data), encoding="utf-8")


def test_editing_project_json_by_hand_does_not_approve_the_plan(isolated):
    slug = projects.create("Forge")["slug"]
    _hand_edit(slug, plan_approved=True, architecture_approved=True, phase="execute")
    p = projects.load(slug)
    assert not p.get("plan_approved")
    assert not p.get("architecture_approved")


def test_editing_project_json_by_hand_does_not_change_what_is_launched(isolated):
    slug = projects.create("Forge")["slug"]
    projects.update(slug, launch={"command": "run.bat", "url": ""})
    _hand_edit(slug, launch=EVIL)
    assert (projects.load(slug).get("launch") or {}).get("command") != "evil.bat"


def test_a_real_approval_and_launch_command_are_still_remembered(isolated):
    slug = projects.create("Honest")["slug"]
    projects.update(slug, plan_approved=True, architecture_approved=True, phase="execute",
                    launch={"command": "run.bat", "url": ""})
    p = projects.load(slug)
    assert p["plan_approved"] is True and p["launch"]["command"] == "run.bat"
    projects.update(slug, name="Renamed")                      # other changes keep the approval
    assert projects.load(slug)["plan_approved"] is True


def test_approval_is_not_taken_from_a_file_in_the_project_folder(isolated):
    """After a real approval, a copy of the project folder (zip, friend, other PC) carries no approval."""
    slug = projects.create("Copied")["slug"]
    projects.update(slug, plan_approved=True, architecture_approved=True, launch=EVIL)
    saved = json.loads(projects.meta_file(slug).read_text(encoding="utf-8"))
    ws = settings.workspace()
    other = ws.parent / "friend" / "copied"
    (other / ".aiteam").mkdir(parents=True)
    (other / ".aiteam" / "project.json").write_text(json.dumps(saved), encoding="utf-8")
    adopted = projects.create_from_folder(str(other))
    assert not adopted.get("plan_approved")
    assert not (projects.load(adopted["slug"]) or {}).get("plan_approved")


def test_adopting_a_prepared_folder_starts_not_approved_with_no_launch(isolated):
    folder = isolated / "friend" / "prepared"
    (folder / ".aiteam").mkdir(parents=True)
    (folder / ".aiteam" / "project.json").write_text(json.dumps({
        "slug": "prepared", "name": "Prepared", "phase": "execute", "plan_approved": True,
        "architecture_approved": True, "plan_approved_at": 1.0, "launch": EVIL}), encoding="utf-8")
    adopted = projects.create_from_folder(str(folder))
    loaded = projects.load(adopted["slug"])
    for p in (adopted, loaded):
        assert not p.get("plan_approved")
        assert not (p.get("launch") or {}).get("command") == "evil.bat"


def test_an_existing_project_without_the_new_state_starts_not_approved(isolated):
    """A project folder from before this change: its file says approved, the app has no record: ask again."""
    folder = settings.workspace() / "old-one"
    (folder / ".aiteam").mkdir(parents=True)
    (folder / ".aiteam" / "project.json").write_text(json.dumps({
        "slug": "old-one", "name": "Old one", "phase": "execute", "plan_approved": True,
        "architecture_approved": True, "plan_approved_at": 1.0}), encoding="utf-8")
    assert not (projects.load("old-one") or {}).get("plan_approved")


def test_migration_keeps_approval_of_existing_projects_once(isolated):
    folder = settings.workspace() / "old-one"
    (folder / ".aiteam").mkdir(parents=True)
    (folder / ".aiteam" / "project.json").write_text(json.dumps({
        "slug": "old-one", "name": "Old one", "plan_approved": True, "architecture_approved": True,
        "plan_approved_at": 1.0, "launch": {"command": "run.bat", "url": ""}}), encoding="utf-8")
    assert projects.migrate_state() == ["old-one"]
    p = projects.load("old-one")
    assert p["plan_approved"] is True and p["architecture_approved"] is True and not p.get("launch")
    # a folder that shows up after the upgrade is not migrated, and the migration does not run twice
    other = settings.workspace() / "late"
    (other / ".aiteam").mkdir(parents=True)
    (other / ".aiteam" / "project.json").write_text(json.dumps({"slug": "late", "plan_approved": True}),
                                                    encoding="utf-8")
    assert projects.migrate_state() == []
    assert not projects.load("late").get("plan_approved")


def test_migration_does_not_carry_over_the_launch_command(isolated):
    folder = settings.workspace() / "old-two"
    (folder / ".aiteam").mkdir(parents=True)
    (folder / ".aiteam" / "project.json").write_text(json.dumps({
        "slug": "old-two", "name": "Old two", "plan_approved": True, "architecture_approved": True,
        "plan_approved_at": 1.0, "launch": EVIL}), encoding="utf-8")
    projects.migrate_state()
    p = projects.load("old-two")
    assert p["plan_approved"] is True and p.get("plan_approved_at") == 1.0
    assert not p.get("launch")


def test_a_fresh_project_does_not_inherit_a_stale_state_file(isolated):
    stale = projects._state_file("fresh")
    stale.write_text(json.dumps({"plan_approved": True, "launch": EVIL}), encoding="utf-8")
    slug = projects.create("Fresh")["slug"]
    assert slug == "fresh"
    p = projects.load(slug)
    assert not p["plan_approved"] and not p.get("launch")


def test_start_up_still_repairs_when_the_migration_fails():
    import inspect
    from app import main
    src = inspect.getsource(main)
    a, b = src.index("projects.migrate_state()"), src.index("projects.repair()")
    assert "try:" in src[a - 80:a] and "except" in src[a:b]
