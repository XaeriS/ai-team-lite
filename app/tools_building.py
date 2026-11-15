"""Tools for building and proving it: iterations, tasks, tests, commits, screenshots, launching, diagrams, reviews, clean-up, lessons.

make(session) returns this group's tools by name; app/tools.py puts the groups together (L46)."""
from __future__ import annotations

from pathlib import Path
import re
import time
from typing import TYPE_CHECKING

from claude_agent_sdk import tool

from . import iterations, paths, policy, projectmap, projects, testrun, tracking, upkeep, winhelpers

if TYPE_CHECKING:
    from .session import Session

from .tools_common import add_test_result, diagram_hint, interrupted, text


def make(session: "Session") -> dict:
    slug = session.slug

    @tool("start_iteration", "Start the next iteration of the approved plan (in order), split into small tasks: "
          "tasks = [{title, files: [...], test}]. Call before briefing workers.", {"n": int, "tasks": list})
    async def start_iteration(args):
        n = int(args.get("n") or 0)
        try:
            it = iterations.start(slug, n, args.get("tasks"))
        except ValueError as e:
            session.hub.publish(slug, {"type": "project_list", "transient": True})
            return text(str(e), True)
        session.hub.publish(slug, {"type": "iteration", "n": it["n"], "name": it["name"], "status": "building",
                                   "requirements": it.get("requirements") or [], "total": len(iterations.load(slug))})
        session.hub.publish(slug, {"type": "project_list", "transient": True})
        first_step = ("Clean-up iteration: existing tests must pass before and after." if it.get("kind") == "cleanup"
                      else "Next: the Tester writes this iteration's tests from the requirements, then record_red_tests.")
        return text(f"Iteration {n} ({it['name']}) started with {len(it.get('tasks') or [])} tasks. Requirements: "
                    f"{', '.join(it.get('requirements') or []) or '-'}. {first_step}")

    @tool("complete_task", "Mark one task of the current iteration as done (task = its number, from 1).",
          {"n": int, "task": int, "note": str})
    async def complete_task(args):
        try:
            t = iterations.complete_task(slug, int(args.get("n") or 0), args.get("task"), str(args.get("note", "")))
        except ValueError as e:
            return text(str(e), True)
        session.hub.publish(slug, {"type": "activity", "who": session.persona, "text": f"task done: {t['title']}"})
        session.hub.publish(slug, {"type": "docs_changed", "transient": True})
        it = iterations.current(slug) or {}
        left = [x["title"] for x in it.get("tasks") or [] if not x.get("done")]
        return text("Done. " + (f"Next task: {left[0]}" if left else "All tasks done: test, review, light clean-up, then commit."))

    @tool("record_red_tests", "Tests first: run this iteration's new tests BEFORE the code is written. They must FAIL now.",
          {"n": int, "test_command": str})
    async def record_red_tests(args):
        n = int(args.get("n") or 0)
        try:
            red = await session.run_blocking(iterations.record_red, slug, n, str(args.get("test_command", "")))
        except ValueError as e:
            return text(str(e), True)
        if red["failed"]:
            session.hub.publish(slug, {"type": "activity", "who": session.persona,
                                       "text": f"iteration {n}: new tests fail as expected (tests first)"})
            return text("Recorded: the new tests fail before the code exists, as they should. Now build task by task "
                        "until they pass.")
        return text("The tests PASS already, so they do not check this iteration's new behaviour. Have the Tester write "
                    "tests for the acceptance criteria that fail now, then record again.\n" + red["output"], True)

    @tool("run_tests", "Run the project's tests and get a SHORT result (totals and only the failures; the full log is "
          "saved in reports/test-logs). Use this instead of running the tests in the shell. scope='affected' (while "
          "building): only the tests related to the files changed; scope='all': the whole suite (always before you "
          "report an iteration as done). test_command: e.g. \"python -m pytest -q\".",
          {"test_command": str, "scope": str})
    async def run_tests(args):
        folder = projects.project_dir(slug)
        cmd = str(args.get("test_command") or "").strip()
        if not cmd or policy.is_risky(cmd) or policy._abs_paths_outside(cmd, [str(folder), str(paths.bundled_python().parent)]):
            return text("test_command is missing or not allowed (it must only run the project's tests).", True)
        note = "all tests"
        if str(args.get("scope") or "affected").lower() != "all":
            cur = iterations.current(slug)
            cmd, note = await session.run_blocking(testrun.affected, folder, cmd, (cur or {}).get("started"))
        ok, summary = await session.run_blocking(iterations.run_tests, cmd, folder, 300,
                                                 "affected" if note != "all tests" else "all")
        return text(f"Ran {note}.\n{summary}", not ok)

    @tool("commit_iteration", "Finish the current iteration: the app runs test_command and commits everything to Git "
          "ONLY if the tests pass. If they fail, nothing is committed: fix and call again.",
          {"n": int, "summary": str, "test_command": str, "light_cleanup": str})
    async def commit_iteration(args):
        n = int(args.get("n") or 0)
        problems = iterations.commit_checks(slug, n, str(args.get("light_cleanup", "")))
        if problems:
            return text("Not committed yet:\n- " + "\n- ".join(problems), True)
        try:
            result = await session.run_blocking(iterations.commit, slug, n, args.get("summary", ""),
                                                str(args.get("test_command", "")), str(args.get("light_cleanup", "")))
        except (ValueError, RuntimeError) as e:
            return text(str(e), True)
        its = iterations.load(slug)
        it = next(x for x in its if x["n"] == n)
        if not result["ok"]:
            session.hub.publish(slug, {"type": "activity", "who": "Fred",
                                       "text": f"iteration {n}: tests failed, nothing committed"})
            return text(f"Tests FAILED, so iteration {n} was NOT committed. Fix the problems and call commit_iteration "
                        f"again.\n{result['output']}", True)
        session.hub.publish(slug, {"type": "iteration", "n": n, "name": it["name"], "status": "done",
                                   "commit": result["commit"], "summary": it.get("summary", ""), "total": len(its)})
        session.hub.publish(slug, {"type": "project_list", "transient": True})
        nxt = next((x for x in its if x["status"] != "done"), None)
        waiting = (projects.load(slug) or {}).get("waiting_release")
        if nxt and waiting == nxt["n"]:
            session.hub.publish(slug, {"type": "release_wait", "n": nxt["n"], "name": nxt["name"]})
            follow = (f"The user chose to release iteration {nxt['n']} ({nxt['name']}) themselves: stop here and tell them "
                      "in one line that it is waiting for their release (Pipeline screen). Do not start it.")
        elif nxt:
            follow = f"Next: iteration {nxt['n']} ({nxt['name']}). Call start_iteration({nxt['n']})."
        else:
            follow = "All iterations are done: summarize for the user and launch the app for them to try."
        unproven = [r["id"] for r in tracking.traceability(slug)
                    if r["id"] in (it.get("requirements") or []) and not r.get("proven")]
        proof = (f" NOT YET PROVEN BY A TEST: {', '.join(unproven)}. Have the Tester add (or link) a passing test for each, "
                 "recorded with record_test_result(requirement=...), before the version is finished." if unproven else "")
        return text(f"Tests passed. Iteration {n} committed to Git ({result['commit']}). {follow}{proof}" + diagram_hint("iteration"))

    @tool("take_screenshot", "Take a screenshot that is shown to the user in the chat. kind='web': target is a URL "
          "(server must already be running). kind='window': target is a command that starts the app; it is "
          "started, captured after wait_seconds, then closed.",
          {"kind": str, "target": str, "caption": str, "wait_seconds": float, "test_name": str})
    async def take_screenshot(args):
        folder = projects.project_dir(slug)
        name = time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^a-z0-9]+", "-", args.get("caption", "shot").lower())[:40]
        out = folder / "reports" / "screenshots" / f"{name.strip('-')}.png"
        try:
            if args.get("kind") == "web":
                await session.run_blocking(winhelpers.screenshot_url, args["target"], out)
            else:
                await session.run_blocking(winhelpers.screenshot_command, args["target"], folder, out,
                                           float(args.get("wait_seconds") or 4))
        except Exception as e:
            return text(f"Screenshot failed: {e}", True)
        session.publish_screenshot(out, args.get("caption", ""))
        rel = out.relative_to(folder).as_posix()
        if args.get("test_name"):
            add_test_result(folder, {"name": args["test_name"], "screenshot": rel, "caption": args.get("caption", "")})
        return text(f"Screenshot saved to {rel} and shown to the user.")

    @tool("show_image", "Show an existing image file from the project folder to the user in the chat.",
          {"path": str, "caption": str})
    async def show_image(args):
        p = Path(args["path"])
        p = p if p.is_absolute() else projects.project_dir(slug) / p
        if not projects.inside(slug, p) or not p.exists() or p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            return text("That image is not in the project folder.", True)
        session.publish_screenshot(p, args.get("caption", ""))
        return text("Shown to the user.")

    @tool("launch_for_user", "Start the app so the user can try it (asks the user first). command runs in the "
          "project folder, e.g. 'run.bat'. If it is a web app, give url too.",
          {"command": str, "url": str, "explanation": str})
    async def launch_for_user(args):
        folder = projects.project_dir(slug)
        try:
            pid = winhelpers.launch(args["command"], folder)
            if args.get("url"):
                await session.sleep(2.5)
                winhelpers.open_url(args["url"])
        except Exception as e:
            return text(f"Could not start it: {e}", True)
        session.hub.publish(slug, {"type": "launched", "command": args["command"], "url": args.get("url", "")})
        projects.update(slug, phase="review", launch={"command": args["command"], "url": args.get("url", "")})
        return text(f"Started (process {pid}). Ask the user to try it and tell you what they think.")

    @tool("record_test_result", "Record one test result for the Tests view: name, requirement (the requirement(s) this "
          "test proves, e.g. REQ-2 or REQ-2, REQ-5), "
          "status (passed|failed|skipped), details (real output for failures), optional screenshot path.",
          {"name": str, "requirement": str, "status": str, "details": str, "screenshot": str})
    async def record_test_result(args):
        status = str(args.get("status", "")).lower()
        if status not in ("passed", "failed", "skipped"):
            return text("status must be passed, failed or skipped", True)
        add_test_result(projects.project_dir(slug), {k: args.get(k, "") for k in
                                                     ("name", "requirement", "details", "screenshot")} | {"status": status})
        session.hub.publish(slug, {"type": "tests_changed", "transient": True})
        return text("Recorded.")

    @tool("record_diagram", "Diagrammer: after Archify's finalize passed, record the diagram so the user sees it. "
          "title = plain words; topic = 'architecture' for the overall picture of the app, else a short slug; kind = the "
          "Archify type; html and json = the two file paths relative to the project folder; question = what it answers.",
          {"title": str, "topic": str, "kind": str, "html": str, "json": str, "question": str})
    async def record_diagram(args):
        from . import diagrams
        try:
            entry = diagrams.record(slug, args.get("title", ""), args.get("topic", ""), args.get("kind", ""),
                                    args.get("html", ""), args.get("json", ""), args.get("question", ""))
        except ValueError as e:
            return text(str(e), True)
        session.hub.publish(slug, {"type": "diagram", **entry})
        return text("Recorded and shown to the user" + (" with a comparison to the previous version" if entry["compare"]
                    else "") + ". Report back in two sentences.")

    @tool("run_security_test", "Before a new version: run the security review and return what to do with the results.", {})
    async def run_security_test(args):
        return text("Security review: brief the Reviewer for a security review of the WHOLE codebase with the bundled "
                    "security-audit skill (.aiteam/skills/security-audit, guidance mode, ATTACK-CLASSES.md), writing "
                    "reports/SECURITY_REVIEW.md. High findings become fix tasks before the version; tell the user the summary.\n"
                    "Findings are tracked until closed (reports/FINDINGS.md). Known so far: " + upkeep.findings_brief(slug)
                    + "\nGive this list to the Reviewer. Afterwards call record_finding for every NEW finding, and "
                    "close_finding(id, \"fixed\") for each one fixed. A finding the user decides to live with is closed "
                    "with close_finding(id, \"accepted\", reason) and is not reported again. Then call clean_leftovers().")

    @tool("record_finding", "Keep a review finding (security or quality) until it is fixed or accepted. A finding "
          "already known under the same title is not added twice.", {"title": str, "severity": str, "where": str})
    async def record_finding(args):
        try:
            item, new = upkeep.record_finding(slug, str(args.get("title", "")), str(args.get("severity", "medium")),
                                              str(args.get("where", "")))
        except ValueError as e:
            return text(str(e), True)
        if new:
            return text(f"Recorded as {item['id']} ({item['severity']}, open).")
        if item["status"] == "accepted":
            return text(f"Already known as {item['id']} and accepted by the user ({item['reason']}): do not report it again.")
        return text(f"Already known as {item['id']} ({item['status']}): fix it, then close_finding(\"{item['id']}\", \"fixed\").")

    @tool("close_finding", "Close a tracked finding: status \"fixed\" (after the fix is tested), or \"accepted\" with "
          "the reason, when the USER decided to live with it.", {"id": str, "status": str, "reason": str})
    async def close_finding(args):
        try:
            item = upkeep.close_finding(slug, str(args.get("id", "")), str(args.get("status", "")), str(args.get("reason", "")))
        except ValueError as e:
            return text(str(e), True)
        left = [f["id"] for f in upkeep.findings(slug) if f["status"] == "open"]
        return text(f"{item['id']} is now {item['status']}. Still open: {', '.join(left) or 'none'}.")

    @tool("clean_leftovers", "Before a new version: look for leftovers in the project folder (old Python environments, "
          "caches, backup copies), show them to the user and remove them only if the user agrees.", {})
    async def clean_leftovers(args):
        folder = projects.project_dir(slug)
        found = await session.run_blocking(upkeep.leftovers, folder)
        if not found:
            return text("No leftovers found: the project folder is tidy.")
        mb = lambda b: f"{b / 1e6:.0f} MB" if b >= 1e6 else f"{max(1, round(b / 1e3))} KB"
        listing = "\n".join(f"- {x['path']}: {x['why']} ({mb(x['bytes'])})" for x in found)
        total = mb(sum(x["bytes"] for x in found))
        answer = await session.ask_user("choice", {
            "title": f"Tidy up before the version? These leftovers take {total} and are not part of your app:\n{listing}",
            "options": ["Remove them", "Keep them"]})
        if interrupted(answer):
            return interrupted(answer)
        if not str(answer.get("value") or "").lower().startswith("remove"):
            return text("The user chose to keep the leftovers. Nothing was deleted.")
        removed = await session.run_blocking(upkeep.remove_leftovers, slug, [x["path"] for x in found])
        projectmap.refresh(folder)
        return text(f"Removed {len(removed)} of {len(found)} leftovers ({total}): {', '.join(removed) or 'none'}.")

    @tool("record_lesson", "Keep ONE plain sentence the team learned the hard way (an environment quirk, a library "
          "gotcha, a fix that took several tries), so future projects on this PC avoid it. No secrets, no personal "
          "data, no project details. Example: 'On this PC, tests must be run with the bundled Python, not py.'",
          {"lesson": str})
    async def record_lesson(args):
        from . import lessons
        try:
            item = lessons.add(str(args.get("lesson", "")), slug)
        except ValueError as e:
            return text(str(e), True)
        return text(f"Lesson kept: {item['text']}")

    return {"start_iteration": start_iteration, "complete_task": complete_task, "record_red_tests": record_red_tests, "run_tests": run_tests, "commit_iteration": commit_iteration, "take_screenshot": take_screenshot, "show_image": show_image, "launch_for_user": launch_for_user, "record_test_result": record_test_result, "record_diagram": record_diagram, "run_security_test": run_security_test, "record_finding": record_finding, "close_finding": close_finding, "clean_leftovers": clean_leftovers, "record_lesson": record_lesson}
