"""L58: one folder whose name is not a project id (capitals, spaces) emptied the whole app: no projects, no setup
guide, no Browse button, no token counts. Rebuilt from the owner's PC: "VLC-Multiplayer" added as an existing folder
inside the projects folder, then its record renamed to the folder's own name by repair()."""
import json

from app import projects, settings
import test_server

srv = test_server.srv


def _odd_folder(name="VLC-Multiplayer", slug="VLC-Multiplayer"):
    d = settings.workspace() / name
    (d / ".aiteam").mkdir(parents=True)
    (d / "main.py").write_text("print('hi')\n")
    (d / ".aiteam" / "project.json").write_text(json.dumps(
        {"slug": slug, "name": "VLC multi-player", "kind": "project", "phase": "discover", "created": 1.0,
         "updated": 2.0, "existing_folder": True, "source": str(d)}))
    return d


def test_one_odd_folder_never_empties_the_list(isolated):
    settings.save({"setup_complete": True})
    good = projects.create("Invoices")["slug"]
    _odd_folder()
    (settings.workspace() / "Notes and ideas").mkdir()                   # a plain folder: ignored as before
    assert [p["slug"] for p in projects.list_projects()] == [good]       # used to raise "Invalid project id"
    assert projects.broken() == []


def test_repair_brings_the_odd_folder_back(isolated):
    settings.save({"setup_complete": True})
    projects.create("Invoices")
    d = _odd_folder()
    fixed = projects.repair()
    assert fixed == ["VLC-Multiplayer registered as vlc-multiplayer"]
    assert projects.external()["vlc-multiplayer"] == str(d)
    assert json.loads((d / ".aiteam" / "project.json").read_text())["slug"] == "vlc-multiplayer"
    slugs = {p["slug"]: p["name"] for p in projects.list_projects()}
    assert slugs == {"invoices": "Invoices", "vlc-multiplayer": "VLC multi-player"}
    assert projects.project_dir("vlc-multiplayer") == d
    assert projects.repair() == []                                       # nothing left to do, nothing undone
    assert projects.slug_for_folder(d) == "vlc-multiplayer"


def test_adding_a_folder_with_capitals_inside_the_projects_folder(isolated):
    settings.save({"setup_complete": True})
    d = settings.workspace() / "My App"
    d.mkdir(parents=True)
    (d / "app.py").write_text("x = 1\n")
    p = projects.create_from_folder(str(d))
    assert p["slug"] == "my-app" and projects.external()["my-app"] == str(d.resolve())
    projects.repair()                                                    # used to rename the record to "My App"
    assert [x["slug"] for x in projects.list_projects()] == ["my-app"]
    again = projects.create_from_folder(str(d))                          # choosing it again reopens it
    assert again["slug"] == "my-app" and again.get("reopened")
    assert list(projects.external()) == ["my-app"]


def test_a_record_naming_itself_differently_still_opens(isolated):
    settings.save({"setup_complete": True})
    d = _odd_folder(name="vlc", slug="VLC-Multiplayer")                  # valid folder name, odd record
    assert [p["slug"] for p in projects.list_projects()] == ["vlc"]
    assert d.exists()


def test_the_app_state_survives(srv):
    settings.save({"setup_complete": True})
    projects.create("Invoices")
    _odd_folder()
    status, state = srv.call("GET", "/api/state")
    assert status == 200 and [p["slug"] for p in state["projects"]] == ["invoices"]

