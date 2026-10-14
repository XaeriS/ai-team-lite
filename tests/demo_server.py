"""Run the real app server with a scripted stand-in for Claude (for UI tests and screenshots).
Usage: AITEAM_HOME=... AITEAM_SECRETS_FILE=... python tests/demo_server.py  -> prints the URL."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import engine as engine_mod  # noqa: E402
from app import projects, settings  # noqa: E402
from app.events import EventHub  # noqa: E402
from app.server import App, serve  # noqa: E402
from fakeclaude import FakeClient, say  # noqa: E402

engine_mod.validate_anthropic_key = lambda k: (True, "ok")

import os  # noqa: E402

from app import auth  # noqa: E402

if os.environ.get("DEMO_SUB"):  # simulate Claude Code's browser sign-in finishing after two checks
    _checks = {"n": 0}
    auth.start_subscription_login = lambda: 1234

    def _status():
        _checks["n"] += 1
        return {"signed_in": _checks["n"] >= 2, "method": "claude.ai"}
    auth.subscription_status = _status


async def script(i, prompt, c):
    session = c.options.can_use_tool.__self__
    folder = session.folder

    async def use(tool, inp, parent=None):
        ok, result, msgs, tid = await c.use_tool(tool, inp, parent)
        return ok, result, msgs, tid

    if session.kind == "setup":
        if prompt.startswith("[App]"):
            yield say("Hi, I'm **Fred**, your AI team lead. Setup takes about five minutes: where your projects "
                      "live, GitHub (optional) and how you want to approve things.\n\nFirst, what's your first name?")
            return
        _, _, m, _ = await use("mcp__aiteam__save_setting", {"key": "user_name", "value": prompt.split()[0]})
        for x in m:
            yield x
        yield say(f"Nice to meet you, {prompt.split()[0]}! Every project gets its own folder. Pick where they should live:")
        _, res, m, _ = await use("mcp__aiteam__ask_folder", {"title": "Where should your projects live?",
                                                             "explanation": "Each project gets a sub-folder here.",
                                                             "suggested_path": settings.load()["workspace_dir"]})
        for x in m:
            yield x
        path = res.split("chose: ", 1)[-1]
        _, _, m, _ = await use("mcp__aiteam__save_setting", {"key": "workspace_dir", "value": path})
        for x in m:
            yield x
        yield say("GitHub keeps an online backup and history of every project. Create a token on the page below "
                  "(keep **repo** ticked), then paste it into the secure box.")
        _, _, m, _ = await use("mcp__aiteam__request_secret", {
            "name": "github_token", "title": "Paste your GitHub token",
            "explanation": "1. Open the page\n2. Keep **repo** ticked\n3. Click *Generate token* and copy it",
            "help_url": "https://github.com/settings/tokens/new?scopes=repo&description=AI%20Team"})
        for x in m:
            yield x
        _, _, m, _ = await use("mcp__aiteam__finish_setup", {"summary": "- Projects folder chosen\n- GitHub token saved\n- Approvals: you approve each plan"})
        for x in m:
            yield x
        yield say("You're all set! Click **+ New project** to start.")
        return

    # project conversation
    from claude_agent_sdk import RateLimitEvent, RateLimitInfo
    yield RateLimitEvent(rate_limit_info=RateLimitInfo(status="allowed", resets_at=int(time.time()) + 3 * 3600,
                                                       rate_limit_type="five_hour", utilization=0.23), uuid="u", session_id="s")
    yield RateLimitEvent(rate_limit_info=RateLimitInfo(status="allowed", resets_at=int(time.time()) + 4 * 86400,
                                                       rate_limit_type="seven_day", utilization=0.41), uuid="u2", session_id="s")
    if "EXISTING folder" in prompt:
        yield say("Hi! I'm scanning your folder first (read-only: nothing is changed).")
        for tool, inp in [("Glob", {"pattern": "**/*"}), ("Read", {"file_path": str(folder / "README.md")}),
                          ("PowerShell", {"command": "git log --oneline -30", "description": "reading the Git history"})]:
            _, _, m, _ = await use(tool, inp)
            for x in m:
                yield x
        (folder / "STATUS.md").write_text(
            "# Where the project stands\n\n**What it is:** a price watcher that reads supplier price lists.\n\n"
            "## Works today\n- Reads one CSV price list (`src/main.py`)\n\n## Unfinished\n- TODO in README: daily refresh\n"
            "- No tests yet\n\n## Candidate next steps\n1. Daily automatic refresh\n2. Price-drop alerts by email\n"
            "3. Tests for the CSV reader\n", encoding="utf-8")
        yield say("**Where we are:** the app reads one supplier price list, but the daily refresh in your README "
                  "is not done and there are no tests.\n\n**What remains** (my suggestion):\n1. Daily automatic refresh\n"
                  "2. Price-drop alerts by email\n3. Tests for the CSV reader\n\nWhich matters most to you? Put them "
                  "in order and add anything I missed.")
        return
    if prompt.startswith("[App] Fred has handed"):
        yield say("Hi, I'm **Mat**, the lead architect. Fred told me what you need; now let's agree how it will work. "
                  "Two questions, with my recommendations:\n\n1. **Data refresh:** is the data entered once, or "
                  "refreshed regularly? I recommend a refresh when the app starts plus once a day.\n"
                  "2. **Storage:** I recommend a small local database (SQLite). The data is personal, so should we "
                  "**encrypt** it? I recommend yes, with the key kept in Windows Credential Manager.")
        return
    if "released iteration" in prompt:
        import re as _re
        n = int(_re.search(r"released iteration (\d+)", prompt).group(1))
        yield say(f"Thanks! Starting iteration {n}.")
        _, _, m, _ = await use("mcp__aiteam__start_iteration", {"n": n})
        for x in m:
            yield x
        return
    if "v08 cards" in prompt.lower():  # demo of the 0.8.0 cards for the UI test
        _, _, m, _ = await use("mcp__aiteam__record_prior_art", {
            "items": [{"name": "sqlite-utils", "url": "https://sqlite-utils.datasette.io", "what": "CLI + library for SQLite",
                       "closeness": "similar", "cost_licence": "Free, Apache-2.0", "reuse": "use for the database layer"},
                      {"name": "GnuCash", "url": "https://gnucash.org", "what": "full accounting app", "closeness": "different",
                       "cost_licence": "Free, GPL", "reuse": "not a fit, too heavy"}],
            "recommendation": "Reuse sqlite-utils for storage; build the rest."})
        for x in m:
            yield x
        _, _, m, _ = await use("mcp__aiteam__record_change_request", {
            "title": "Add dark mode", "description": "The user wants a dark theme toggle.",
            "affects_requirements": ["REQ-1"], "affects_decisions": [], "affects_iterations": [], "effort": "1 small iteration"})
        for x in m:
            yield x
        yield say("Those were the demo cards.")
        return
    if prompt.startswith("[App] You are Fred again. The user APPROVED"):
        prompt = "make a plan"
    elif prompt.startswith("[App]"):
        yield say("Hi! What would you like to build? Tell me who it's for and what problem it solves.")
        return
    if session.persona == "Mat":
        _, res, m, _ = await use("mcp__aiteam__request_architecture_approval", {
            "title": "Architecture: Expense tracker",
            "architecture_markdown": "# Architecture\n\n## Overview\nA desktop window (Python, Tkinter) on this PC only.\n\n"
                                     "## Data\n- Entered by you; no outside source.\n- Stored in a local SQLite database, "
                                     "encrypted at rest.\n- Automatic backup copy every week.\n\n## Security and privacy\n"
                                     "- Encryption key in Windows Credential Manager, never in the code.\n",
            "decisions": [{"topic": "Data refresh", "decision": "Entered by you; totals recalculated instantly",
                           "reason": "no outside data source"},
                          {"topic": "Storage", "decision": "Local SQLite database", "reason": "simple, fast, no server"},
                          {"topic": "Encryption", "decision": "Encrypt the database; key in Windows Credential Manager",
                           "reason": "expenses are personal"},
                          {"topic": "Backups", "decision": "Weekly copy to your Documents folder",
                           "reason": "protects against a broken disk"}]})
        for x in m:
            yield x
        if "APPROVED" not in res:
            yield say("Understood, I'll adjust the architecture.")
        return
    if "simulate limit" in prompt.lower():
        from claude_agent_sdk import ResultMessage
        yield say("Starting the next step…")
        yield RateLimitEvent(rate_limit_info=RateLimitInfo(status="rejected", resets_at=int(time.time()) + 3600,
                                                           rate_limit_type="five_hour", utilization=1.0), uuid="u3", session_id="s")
        yield ResultMessage(subtype="error", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1,
                            session_id="s", result="Claude usage limit reached", api_error_status=429)
        return
    if "make a plan" not in prompt.lower():
        _, _, m, _ = await use("Write", {"file_path": str(folder / "REQUIREMENTS.md"), "content":
            "# Expense tracker\n\n## Summary\nA small desktop app to log expenses and see monthly totals.\n\n"
            "## Requirements\n- **REQ-1** Add an expense (date, amount, category)\n  - AC: it appears in the list\n"
            "- **REQ-2** Monthly total per category\n\n## Out of scope\n- Bank connections\n\n## Open questions\n- [x] Desktop window: yes"})
        for x in m:
            (folder / "REQUIREMENTS.md").write_text("# Expense tracker\n\n## Summary\nA small desktop app to log "
                "expenses and see monthly totals.\n\n## Requirements\n- **REQ-1** Add an expense (date, amount, "
                "category)\n  - AC: it appears in the list\n- **REQ-2** Monthly total per category\n\n## Out of scope\n"
                "- Bank connections\n", encoding="utf-8")
            yield x
        _, _, m, _ = await use("mcp__aiteam__record_problem", {
            "who": "You (and your partner later)", "how_often": "Every week",
            "cost_today": "About an hour a month redoing a messy spreadsheet; totals are often wrong",
            "how_now": "A spreadsheet you fill in by hand", "success": "Monthly totals per category in one click",
            "statement": "Logging expenses is slow and the monthly totals cannot be trusted."})
        for x in m:
            yield x
        yield say("Got it. Two quick questions:\n1. Should it be a **desktop window** or a **page in your browser**?\n"
                  "2. Do you want to export the totals to Excel?\n\nWhen you're happy, say *make a plan*.")
        return
    if not projects.architecture_approved(projects.load(session.slug) or {}):
        yield say("Great, the requirements are clear. I'm handing over to **Mat**, our lead architect, to agree how "
                  "it will be built.")
        if "challenge" in prompt.lower():
            ok, res, m, _ = await use("Agent", {"subagent_type": "challenger", "description": "Challenge the requirements",
                                                "prompt": "Review the folder cold."})
            for x in m:
                yield x
            _, res, m, _ = await use("mcp__aiteam__record_challenge_review", {"findings": [
                {"kind": "objection", "title": "A spreadsheet template may be enough",
                 "detail": "Two requirements, both doable with a pivot table.", "recommendation": "Try a template first",
                 "requirements": ["REQ-1", "REQ-2"]},
                {"kind": "contradiction", "title": "Totals 'per month' but expenses have no month rule",
                 "detail": "REQ-2 needs to know which month a late refund belongs to.",
                 "recommendation": "Use the expense date", "requirements": ["REQ-2"]},
                {"kind": "assumption", "title": "Only one currency is used", "detail": "Nothing says so.",
                 "recommendation": "Assume dollars only"},
                {"kind": "clarification", "title": "What should happen with a negative amount?",
                 "detail": "No When... then... check covers it.", "recommendation": "Treat it as a refund"}]})
            for x in m:
                yield x
            yield say("Thanks, I updated the requirements with your decisions.")
            args = {"summary": "Desktop expense tracker: add expenses, monthly totals per category."}
        else:
            args = {"summary": "Desktop expense tracker: add expenses, monthly totals per category.",
                    "challenge_skipped_because": "demo"}
        _, _, m, _ = await use("mcp__aiteam__handoff_to_architect", args)
        for x in m:
            yield x
        return
    plan = ("**Goal:** a desktop expense tracker.\n\n**Steps**\n1. Developer builds the window and storage\n"
            "2. Tester writes tests and takes screenshots\n3. Reviewer checks against the requirements\n\n"
            "**You will receive:** test results, screenshots, then the app starts for you to try.")
    _, res, m, _ = await use("mcp__aiteam__request_plan_approval", {"title": "Plan: Expense tracker v1", "plan_markdown": plan,
                                                                     "iterations": [{"name": "Add and list expenses", "goal": "the simplest working version", "requirements": ["REQ-1"]},
                                                                                    {"name": "Monthly totals", "goal": "totals per category", "requirements": ["REQ-2"]}]})
    for x in m:
        yield x
    if "APPROVED" not in res:
        yield say("Understood, I'll revise the plan.")
        return
    yield say("Plan approved. Starting iteration 1.")
    _, _, m, _ = await use("mcp__aiteam__start_iteration", {"n": 1})
    for x in m:
        yield x
    ok, _, m, tid = await use("Agent", {"subagent_type": "developer", "description": "Build expense tracker v1", "prompt": "..."})
    (folder / "run.bat").write_text("@echo off\npython main.py\n", encoding="utf-8")
    for f in ("main.py", "storage.py", "run.bat"):
        _, _, inner, _ = await use("Write", {"file_path": str(folder / f), "content": "# code"}, parent=tid)
        for x in inner:
            yield x
        await asyncio.sleep(0.3)
    yield m[0].__class__(content=[m[0].content[0].__class__(tid, "Built main.py, storage.py, run.bat. 6 unit tests pass.")])
    _, _, m, _ = await use("PowerShell", {"command": "winget install Python.Launcher", "description": "Install the Python launcher system-wide"})
    for x in m:
        yield x
    page = folder / "reports" / "demo.html"
    page.write_text("<body style='font-family:Segoe UI,sans-serif;padding:30px'><h2>Expenses — October</h2>"
                    "<table border=1 cellpadding=8><tr><th>Date</th><th>Category</th><th>Amount</th></tr>"
                    "<tr><td>Oct 3</td><td>Food</td><td>$42.10</td></tr><tr><td>Oct 5</td><td>Transport</td><td>$18.00</td></tr></table>"
                    "<p><b>Total: $60.10</b></p></body>")
    for name, req, status, details in [("adds an expense", "REQ-1", "passed", ""),
                                       ("monthly total per category", "REQ-2", "passed", ""),
                                       ("rejects a negative amount", "REQ-1", "failed", "AssertionError: expected an error message, got none")]:
        _, _, m, _ = await use("mcp__aiteam__record_test_result", {"name": name, "requirement": req, "status": status,
                                                                   "details": details, "screenshot": ""})
        for x in m:
            yield x
    _, _, m, _ = await use("mcp__aiteam__take_screenshot", {"kind": "web", "target": page.as_uri(),
                                                            "caption": "Expense list with monthly total",
                                                            "test_name": "monthly total per category"})
    for x in m:
        yield x
    _, _, m, _ = await use("mcp__aiteam__commit_iteration", {"n": 1, "summary": "Add and list expenses works; 3 tests run.",
                                                             "test_command": f'"{sys.executable}" -c "print(1)"'})
    for x in m:
        yield x
    yield say("All 6 tests pass and the reviewer approved. I'm starting the app so you can try it: add an "
              "expense and check the monthly total.")


def _demo_voice(app):
    """Simulated microphone + speech recognition: 'speaks' a sentence ending with the end word."""
    import numpy as np

    from app import voice

    def fake_download(size=None):
        time.sleep(1.5)
        d = voice.models_dir() / (size or settings.load()["voice_model"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.bin").write_bytes(b"demo")
    voice.download_model = fake_download
    voice.packages_available = lambda: True  # Linux test box has no sound driver for sounddevice

    def source():
        def gen():
            for i in range(40):
                time.sleep(0.05)
                yield (np.ones(4000) * (0.2 if 2 <= i < 30 else 0.001)).astype("float32")
        return gen()

    def transcribe():
        said = ["Can you add", "Can you add a dark mode", "Can you add a dark mode to the app copy"]

        def tr(audio, final):
            return said[min(len(said) - 1, int(len(audio) / 16000 / 1.3) - 1)] if len(audio) > 16000 else ""
        return tr
    app.voice_overrides = {"source": source, "transcribe": transcribe}


GEMINI_DEMO = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest", "gemini-2.5-flash",
               "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-001",
               "gemini-2.0-flash-lite", "gemini-2.0-flash-lite-001", "gemini-2.5-flash-preview-09-2025",
               "gemini-2.5-flash-lite-preview-09-2025", "gemini-3-pro-preview", "gemini-3-flash-preview",
               "gemini-2.0-flash-exp", "gemini-2.0-flash-thinking-exp", "gemini-exp-1206", "gemini-2.0-pro-exp",
               "gemini-2.5-pro-preview-06-05", "gemini-2.5-flash-preview-05-20", "gemini-1.5-flash",
               "gemini-1.5-flash-8b", "gemini-1.5-pro",
               # filtered out (not text models the team can use)
               "gemini-embedding-001", "gemini-2.5-flash-image", "gemini-2.5-flash-preview-tts"]


def _demo_brain():
    """One fake server standing in for Ollama (tags, pull, chat), Gemini (model list) and an OmniRoute gateway."""
    import json as _json
    import threading as _th
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app import brains, settings, tooling

    installed = {"qwen2.5-coder:7b", "llama3.2:3b"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, raw=None):
            b = raw if raw is not None else _json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path.startswith("/gemini/models"):
                return self._send({"data": [{"id": "models/" + m} for m in GEMINI_DEMO]})
            if self.path.startswith("/v1/models"):  # the OmniRoute gateway
                return self._send({"data": [{"id": "gemini/gemini-2.5-flash"}, {"id": "groq/llama-3.3-70b"},
                                            {"id": "openai/gpt-4o-mini"}]})
            self._send({"models": [{"name": n} for n in sorted(installed)]})  # /api/tags

        def do_POST(self):
            body = _json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            if self.path.startswith("/api/pull"):
                time.sleep(0.4)
                installed.add(body["model"] if ":" in body["model"] else body["model"] + ":latest")
                return self._send(None, b'{"status":"pulling","total":100,"completed":50}\n{"status":"success"}\n')
            q = body["messages"][-1]["content"]
            if body.get("model", "").split(":")[0] and self.path.startswith("/ollama") and \
                    (body["model"] if ":" in body["model"] else body["model"] + ":latest") not in installed:
                self.send_response(404); self.end_headers(); return
            if "winget" in q:
                time.sleep(1.0)
                a = ("Installs the Python launcher for every user on this PC.\nRisk: high - it changes the whole "
                     "computer, and the project already has its own Python.")
            else:
                a = "ready"
            self._send({"choices": [{"message": {"content": a}}], "usage": {"prompt_tokens": 80, "completion_tokens": 40}})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    _th.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    brains.PROVIDERS["ollama"]["base"] = base + "/ollama/v1/"
    brains.PROVIDERS["gemini"]["base"] = base + "/gemini/"
    tooling.OLLAMA_BASE = base
    tooling.memory_gb = lambda: 16.0
    tooling.gpu_memory_gb = lambda: 0.0
    settings.save({"omniroute_url": base})

    # /ollama/api/tags -> same handler; make the tags path match what brains asks for
    orig = H.do_GET

    def do_get(self):
        if self.path.startswith("/ollama/api/tags"):
            self.path = "/api/tags"
        return orig(self)
    H.do_GET = do_get


def main():
    if not os.environ.get("DEMO_PIPELINE"):  # older UI tests expect the conversation to stop where the script ends
        settings.save({"pipeline_auto": False})
    if not os.environ.get("DEMO_V08"):  # older UI/flow tests predate the 0.8.0 build gates
        settings.save({"version_cleanup": False, "tests_first": False, "light_cleanup": False, "prior_art": False})
    hub = EventHub(projects.history_file)
    eng = engine_mod.Engine(hub, client_factory=lambda o: FakeClient(o, script))
    app = App(engine=eng, hub=hub)
    if os.environ.get("DEMO_VOICE"):
        _demo_voice(app)
    if os.environ.get("DEMO_BRAIN"):
        _demo_brain()
    if os.environ.get("DEMO_USAGE"):  # stand-in for Claude's own "/usage" command (item 69)
        import tempfile as _tf
        from app import auth as _auth
        fake = Path(_tf.mkdtemp()) / "claude"
        fake.write_text("#!/bin/sh\ncat <<'EOF'\nTotal cost: $0.00\nCurrent session: 46% used \u00b7 resets 7:30pm\n"
                        "Current week (all models): 8% used \u00b7 resets Fri 8pm\nEOF\n", encoding="utf-8")
        fake.chmod(0o755)
        _auth.cli_path = lambda: fake
    httpd, url = serve(app, port=int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(url, flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
