"""Texts, limits and small helpers shared by the local web server and its route files (L46)."""
from __future__ import annotations

from pathlib import Path
import threading

from . import brains, tooling

UI_DIR = Path(__file__).parent / "ui"
UI_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png",
            ".ico": "image/x-icon"}
MAX_UPLOAD = 15 * 1024 * 1024

SETUP_KICKOFF = ("[App] The user has just connected AI Team Lite to Claude and this is the first-time setup. "
                 "Greet them and start setup step 1.")
PROJECT_KICKOFF = ("[App] The user just created the project \"{name}\". Greet them in one or two sentences and "
                   "start discovering what they want to build. If GitHub is connected, mention you can create a "
                   "repository for it once the idea is clear.")
EXISTING_KICKOFF = (
    "[App] The user created the project \"{name}\" from an EXISTING folder ({folder}). Treat it as a CONTINUATION of a "
    "project that is already under way: rebuild the full picture so the team can carry on, as if AI Team Lite had been "
    "used from the start.\n"
    "What the app already found (free scan): {scan}\n"
    "1. Greet them in one sentence: you are reading the whole folder first (read-only, nothing is changed).\n"
    "2. Scan EVERYTHING, read-only: list all files (Glob); read every document, note and to-do list, every source "
    "file, the tests and the configuration (skip .venv, node_modules, build output and large data files). If it is a "
    "Git repository, read `git log --oneline -50` and `git status`. \n"
    "3. The project documents are REQUIREMENTS.md, ARCHITECTURE.md, PLAN.md and STATUS.md, in the project folder.\n"
    "   - A document that EXISTS (even in docs/ or with other capitals): read it, check it against the code, and "
    "keep the user's content. If it is not in the project folder itself, write it there from that content. Add a "
    "short section \"Checked against the code\" listing anything that no longer matches. Never rewrite it wholesale.\n"
    "   - A document that is MISSING: REBUILD it from what you understood, and start it with the line "
    "\"> Rebuilt by Fred from the existing files - please check.\"\n"
    "     REQUIREMENTS.md: every feature that already works as REQ-n marked \"(existing)\" with acceptance criteria "
    "taken from the code and tests; unfinished or wished-for items (TODOs, notes, failing tests) as further REQ-n.\n"
    "     STATUS.md: what the project is, technologies, how to run it, what works, what is unfinished or broken, "
    "recent history, risks, candidate next steps.\n"
    "     ARCHITECTURE.md and PLAN.md are rebuilt in steps 5 and 6 below (by Mat, then you), so they get approved.\n"
    "4. ASK FOR VALIDATION: tell the user in plain words where the project stands, which documents you found and "
    "which you rebuilt, then call ask_choice with the options \"Yes, that is right\" / \"Some things are wrong\" so "
    "they confirm REQUIREMENTS.md and STATUS.md (they can read them in the side panel). Fix what they correct, and "
    "ask them to PRIORITIZE what remains (most important first).\n"
    "5. Then handoff_to_architect: Mat describes the architecture AS IT IS in the code (or checks the existing "
    "ARCHITECTURE.md against it), only asks about real gaps, and asks for approval with "
    "request_architecture_approval - that is the validation of the architecture.\n"
    "6. Then the plan with request_plan_approval: first the work that is ALREADY finished, as iterations with "
    "already_done: true (shown as done, nothing is rebuilt), then the remaining work by priority. The approval "
    "card is the validation of the plan.{diagram}")
ADOPTED_KICKOFF = (
    "[App] The user opened the project \"{name}\" from its folder ({folder}). This folder was ALREADY an AI Team Lite "
    "project (on this PC before, or another one): its plan, iterations and approvals were kept. What the app found: "
    "{scan}\n"
    "1. Greet them in one sentence and say you are catching up on the project (read-only).\n"
    "2. Read REQUIREMENTS.md, ARCHITECTURE.md, PLAN.md and STATUS.md, `git log --oneline -30` and `git status`, and "
    "check the code still matches (files may have changed since). Rebuild any document that is missing, starting it "
    "with \"> Rebuilt by Fred from the existing files - please check.\", and update STATUS.md.\n"
    "3. Tell the user in plain words where the project stands and what comes next, and ask with ask_choice whether to "
    "continue with the plan as it is (\"Continue the plan\" / \"Change something first\"). If something was rebuilt, "
    "ask them to check it first.")


def _scan_text(scan: dict) -> str:
    if not scan:
        return "(nothing)"
    found = ", ".join(f"{d} (at {where})" for d, where in scan.get("found", {}).items()) or "none of the documents"
    missing = ", ".join(scan.get("missing", [])) or "none"
    return (f"{scan.get('files', 0)} files; found {found}; missing: {missing}; "
            f"{'a Git repository' if scan.get('git') else 'not a Git repository'}"
            f"{'; readme: ' + scan['readme'] if scan.get('readme') else ''}.")


_brain_job = threading.Lock()


def _job_event(app, text, **kw):
    app.hub.publish("setup", {"type": "brainjob", "text": text, "transient": True, **kw})


def _ollama_get_and_add(app, model):
    """Download a local model (installing and starting Ollama first if needed), then test and add it (item 55)."""
    if not _brain_job.acquire(blocking=False):
        _job_event(app, "Another download is already running; wait for it to finish.", error=True)
        return
    try:
        if not tooling.ollama_exe() and tooling.ollama_status()["state"] != "running":
            tooling.ollama_install(lambda m, f=None: _job_event(app, m, frac=f))
        if tooling.ollama_status()["state"] != "running":
            _job_event(app, "Starting Ollama…")
            tooling.ollama_start(wait=30)
        if brains._norm(model) not in brains.ollama_installed_names():
            tooling.ollama_pull(model, lambda m, f=None: _job_event(app, m, frac=f))
        _job_event(app, "Testing the model…")
        ok, msg = brains.test_config("ollama", model)
        if not ok:
            _job_event(app, f"Downloaded, but the test failed: {msg}", error=True, done=True)
            return
        try:
            brains.add("ollama", model)
        except ValueError:
            pass  # already in the list
        _job_event(app, f"✓ {model} downloaded, tested and added.", done=True, added=True)
    except Exception as e:  # noqa: BLE001
        _job_event(app, f"Could not finish: {e}", error=True, done=True)
    finally:
        _brain_job.release()


NO_ROUTE = object()   # a route group returns this when the path is not one of its own
