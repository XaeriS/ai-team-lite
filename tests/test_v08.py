"""0.8.0 backend: council, run journal, languages, change tracking, skills, tooling recommendations."""
import time

import pytest

from app import council, journal, languages, projects, settings, skills, tooling, tracking, iterations


# ------------------------------------------------------------------------------- languages (35)
# ------------------------------------------------------------------------------- council (22)
def test_parse_ranking():
    assert council.parse_ranking("bla\nFINAL RANKING: B, A, C", ["A", "B", "C"]) == ["B", "A", "C"]
    assert council.parse_ranking("FINAL RANKING: C C A Z", ["A", "B", "C"]) == ["C", "A"]  # dedup, ignore unknown
    assert council.parse_ranking("no ranking here", ["A", "B"]) == []


def test_council_run_with_fake_brains(isolated):
    settings.save({"brains": [
        {"id": "g", "provider": "gemini", "model": "gemini-2.5-flash", "enabled": True},
        {"id": "o", "provider": "ollama", "model": "qwen3:8b", "enabled": True}]})
    from app import secrets_store
    secrets_store.set_secret("gemini_api_key", "k")
    answers = {council.FIRST: "Use SQLite. Risk: file corruption.", council.REVIEW: "A is best.\nFINAL RANKING: A, B"}
    recorded = []

    def fake_ask(brain, system, user):
        return answers[system], {"input_tokens": 100, "output_tokens": 50}

    res = council.run("Which database?", "context here", "I suggest Postgres.", "Fred",
                      scout_report="Found sqlite-utils (MIT).", ask=fake_ask,
                      record=lambda who, u, meta: recorded.append(who))
    whos = [o["who"] for o in res["opinions"]]
    assert "Fred (lead)" in whos and "Scout (web search)" in whos
    assert any("Gemini" in w for w in whos) and any("Ollama" in w for w in whos)
    assert res["reviews"] and res["ranked"] and recorded
    assert "COUNCIL RESULT" in council.for_chairman(res)


def test_council_estimate_and_privacy(isolated):
    settings.save({"brains": [{"id": "g", "provider": "gemini", "model": "gemini-2.5-flash", "enabled": True}]})
    from app import secrets_store
    secrets_store.set_secret("gemini_api_key", "k")
    ms = council.members()
    assert len(ms) == 1
    est = council.estimate(ms, 4000)
    assert est["members"] == 1 and est["cost"] > 0 and est["tokens"] > 0
    assert council.privacy_warnings(ms) and "Google" in council.privacy_warnings(ms)[0]


# ------------------------------------------------------------------------------- run journal (33)
def test_journal_interrupted_detection(isolated):
    p = projects.create("Busy")
    slug = p["slug"]
    assert journal.start() == []  # first run, nothing before
    journal.record(slug, active=True, step="building")
    # simulate a crash: new start without clean_exit
    found = journal.start()
    assert [x["slug"] for x in found] == [slug]
    assert journal.interrupted() and journal.interrupted()[0]["slug"] == slug
    # a clean exit leaves nothing interrupted next time
    journal.record(slug, active=True)
    journal.clean_exit()
    assert journal.start() == []
    journal.resolve(slug)
    assert journal.interrupted() == []


def test_journal_ignores_setup_and_gone_projects(isolated):
    journal.record(projects.SETUP_SLUG, active=True)  # ignored
    journal.record("ghost-project", active=True)
    journal.start()  # ghost has no project.json -> not reported
    assert all(x["slug"] != "ghost-project" for x in journal.interrupted())


# ------------------------------------------------------------------------------- change tracking (34)
def _write_doc(slug, name, text):
    (projects.project_dir(slug) / name).write_text(text, encoding="utf-8")


def test_tracking_versions_and_approval(isolated):
    p = projects.create("Track")
    slug = p["slug"]
    _write_doc(slug, "REQUIREMENTS.md", "# App\n- REQ-1 Add expense\n- REQ-2 Totals\n")
    new = tracking.snapshot(slug, "first requirements", git=False)
    assert [n["doc"] for n in new] == ["requirements"] and new[0]["v"] == 1
    assert tracking.snapshot(slug, "no change", git=False) == []  # identical -> no new version
    _write_doc(slug, "REQUIREMENTS.md", "# App\n- REQ-1 Add expense\n- REQ-2 Totals\n- REQ-3 Export\n")
    new = tracking.snapshot(slug, "added export", git=False)
    assert new[0]["v"] == 2
    assert len(tracking.versions(slug, "requirements")) == 2
    tracking.mark_approved(slug, "requirements")
    assert tracking.versions(slug, "requirements")[-1]["approved"]
    assert "REQ-3" in tracking.text_of(slug, "requirements", 2) and "REQ-3" not in tracking.text_of(slug, "requirements", 1)


def test_tracking_traceability_and_progress(isolated):
    p = projects.create("Trace")
    slug = p["slug"]
    _write_doc(slug, "REQUIREMENTS.md", "- REQ-1 Add\n- REQ-2 Totals\n")
    tracking.snapshot(slug, "v1", git=False)
    tracking.mark_approved(slug, "requirements")
    # REQ-1 built and tested, REQ-2 still planned
    projects.update(slug, iterations=[
        {"n": 1, "name": "Add", "status": "done", "commit": "abc", "requirements": ["REQ-1"]},
        {"n": 2, "name": "Totals", "status": "planned", "requirements": ["REQ-2"]}])
    from app import tools
    tools.add_test_result(projects.project_dir(slug), {"name": "adds", "requirement": "REQ-1", "status": "passed"})
    rows = {r["id"]: r for r in tracking.traceability(slug)}
    assert rows["REQ-1"]["status"] == "tested" and rows["REQ-1"]["iteration"] == 1
    assert rows["REQ-2"]["status"] == "planned"
    # a new requirement added after approval shows as proposed
    _write_doc(slug, "REQUIREMENTS.md", "- REQ-1 Add\n- REQ-2 Totals\n- REQ-4 New\n")
    tracking.snapshot(slug, "added REQ-4", git=False)
    rows = {r["id"]: r for r in tracking.traceability(slug)}
    assert rows["REQ-4"]["status"] == "proposed"


def test_change_requests(isolated):
    p = projects.create("CR")
    slug = p["slug"]
    cr = tracking.add_change_request(slug, {"title": "Dark mode", "description": "add a dark theme",
                                            "affects_requirements": ["REQ-1"], "effort": "1 iteration"})
    assert cr["id"] == "CR-1" and cr["status"] == "pending"
    tracking.decide_change_request(slug, "CR-1", approved=True, note="go")
    assert tracking.change_requests(slug)[0]["status"] == "approved"


# ------------------------------------------------------------------------------- skills (24, 30)
def test_skills_apply(isolated):
    assert set(skills.enabled()) >= {"ponytail", "security-audit", "impeccable", "no-ai-slop"} and "graphify" not in skills.SKILLS
    assert skills.applies("ponytail", {}) and skills.applies("security-audit", {})
    assert skills.applies("impeccable", {"has_ui": True}) and not skills.applies("impeccable", {"has_ui": False})
    settings.save({"skills": {"ponytail": False}})
    assert skills.enabled()["ponytail"] is False
    note = skills.note_for("Developer", {"has_ui": True})
    assert "Ponytail" in note or "Impeccable" in note


# ------------------------------------------------------------------------------- tooling recommendations (36)