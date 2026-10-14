"""Tools that Fred and the workers can call besides the built-in ones (files, shell, web).
Each tool that needs the user (a secret, a folder, an approval of the plan) shows a card in the chat and
waits for the answer."""
from __future__ import annotations

import json
import platform
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import challenge, tracking

from . import brains, council, github, iterations, models, paths, projects, secrets_store, settings, usage, winhelpers

if TYPE_CHECKING:
    from .engine import Session


def interrupted(answer: dict) -> dict | None:
    if answer.get("interrupted"):
        from .engine import interrupted_text

        return text(interrupted_text(answer))
    return None


def text(s: str, error: bool = False) -> dict:
    out = {"content": [{"type": "text", "text": s}]}
    if error:
        out["is_error"] = True
    return out


def tests_file(folder: Path) -> Path:
    return folder / "reports" / "test_results.json"


def load_tests(folder: Path) -> list[dict]:
    try:
        return json.loads(tests_file(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def add_test_result(folder: Path, entry: dict) -> None:
    """Add a result, or attach a screenshot to the latest result with the same name."""
    results = load_tests(folder)
    entry = {k: v for k, v in entry.items() if v not in (None, "")}
    if "status" not in entry:  # a screenshot for an existing test
        for r in reversed(results):
            if r.get("name") == entry.get("name"):
                r.update(entry)
                break
        else:
            results.append({"status": "screenshot", "time": time.time(), **entry})
    else:
        results.append({"time": time.time(), **entry})
    tests_file(folder).parent.mkdir(parents=True, exist_ok=True)
    tests_file(folder).write_text(json.dumps(results[-500:], indent=1), encoding="utf-8")


ARCH_KICKOFF = ("[App] Fred has handed the conversation to you, Mat, the lead architect. His summary: {summary}\n"
                "Introduce yourself to the user in one or two sentences and start the architecture questions "
                "(with your recommendations).")
PLAN_KICKOFF = ("[App] You are Fred again. The user APPROVED the architecture (ARCHITECTURE.md). Tell them in one line "
                "that you are back, then propose the plan split into iterations with request_plan_approval, following "
                "the architecture.")
def diagrams_on() -> bool:
    """The diagrammer exists only when Archify is installed and its skill is switched on (item 78)."""
    from . import skills as _skills, tooling as _tooling
    return _tooling.archify_ready() and bool(_skills.enabled().get("archify"))


def diagram_hint(when: str) -> str:
    if not diagrams_on():
        return ""
    if when == "architecture":
        return (" First ask the diagrammer (subagent_type 'diagrammer') for the overall picture of the app (topic "
                "'architecture', 6-12 plain-named parts) from ARCHITECTURE.md and any existing code, so the user can "
                "see what will be built.")
    return (" If this iteration added or changed parts or connections, ask the diagrammer to update the 'architecture' "
            "diagram (the app then shows what changed); skip it for small changes.")


BACK_KICKOFF = ("[App] You are Fred again. Mat handed the conversation back to you because: {reason}\n"
                "Continue the requirements with the user; when they are agreed, hand off to Mat again.")


def normalize_decisions(raw) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("List the architecture decisions: decisions = [{topic, decision, reason}].")
    out = []
    for d in raw[:40]:
        if not isinstance(d, dict) or not str(d.get("topic", "")).strip() or not str(d.get("decision", "")).strip():
            raise ValueError("Each decision needs a topic and a decision (and a reason).")
        out.append({k: " ".join(str(d.get(k, "")).split())[:400] for k in ("topic", "decision", "reason")})
    return out


def decisions_markdown(decisions: list[dict]) -> str:
    rows = ["| Topic | Decision | Why |", "|---|---|---|"]
    rows += [f"| {d['topic'].replace('|', '/')} | {d['decision'].replace('|', '/')} | {d['reason'].replace('|', '/')} |"
             for d in decisions]
    return "\n".join(rows)


def normalize_objections(raw) -> list[dict]:
    out = []
    for o in raw if isinstance(raw, list) else []:
        if isinstance(o, dict) and str(o.get("objection", "")).strip():
            out.append({"objection": " ".join(str(o["objection"]).split())[:400],
                        "answer": " ".join(str(o.get("answer", "")).split())[:400]})
    return out[:8]


def prior_art_markdown(items: list[dict], recommendation: str) -> str:
    rows = ["# Existing solutions (prior art)", "", "| Name | What it does | How close | Cost / licence | Could reuse |",
            "|---|---|---|---|---|"]
    for i in items:
        name = f"[{i.get('name', '?')}]({i['url']})" if i.get("url") else i.get("name", "?")
        rows.append(" | ".join(["", name, i.get("what", ""), i.get("closeness", ""), i.get("cost_licence", ""),
                                i.get("reuse", ""), ""]).strip())
    return "\n".join(rows) + f"\n\n## Recommendation\n{recommendation.strip()}\n"


def build_server(session: "Session"):
    slug = session.slug

    @tool("check_environment", "Check what is installed and configured on this PC (no changes).", {})
    async def check_environment(args):
        s = settings.load()
        info = {
            "os": f"{platform.system()} {platform.release()}",
            "python_for_projects": str(paths.bundled_python()),
            "git": paths.git_exe() or "missing",
            "browser_for_screenshots": winhelpers.find_headless_browser() or "missing",
            "workspace_dir": str(settings.workspace()),
            "claude_connection": "Claude subscription (signed in)" if s.get("auth_mode") == "subscription"
            else ("API key saved" if secrets_store.has("anthropic_api_key") else "missing"),
            "github": f"connected as {s['github_login']}" if s["github_login"] and secrets_store.has("github_token")
            else "not connected",
            "approval_mode": s["approval_mode"],
            "claude_models": ", ".join(v["label"] for v in models.CLAUDE.values()),
            "model_choice": "Fred chooses per step",
        }
        return text(json.dumps(info, indent=2))

    @tool("save_setting", "Save a user setting. key: user_name | workspace_dir | approval_mode (plan|strict).",
          {"key": str, "value": str})
    async def save_setting(args):
        key, value = args["key"], str(args["value"]).strip()
        if key not in settings.AGENT_EDITABLE:
            return text(f"'{key}' cannot be changed from the chat.", True)
        if key == "approval_mode" and value not in ("plan", "strict"):
            return text("approval_mode must be 'plan' or 'strict'.", True)
        if key == "workspace_dir":
            p = Path(value).expanduser()
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return text(f"Cannot use that folder: {e}", True)
            value = str(p)
        settings.save({key: value})
        session.hub.publish(slug, {"type": "project_list", "transient": True})
        return text(f"Saved {key} = {value}")

    @tool("request_secret", "Show the user a secure box to paste a secret. It is saved in Windows Credential "
          "Manager; you never see it. name: github_token | anthropic_api_key.",
          {"name": str, "title": str, "explanation": str, "help_url": str})
    async def request_secret(args):
        name = args["name"]
        if name not in secrets_store.KNOWN:
            return text(f"Unknown secret '{name}'.", True)
        answer = await session.ask_user("secret", {
            "title": args.get("title") or f"Paste your {secrets_store.KNOWN[name]}",
            "explanation": args.get("explanation", ""), "help_url": args.get("help_url", ""), "name": name})
        if interrupted(answer):
            return interrupted(answer)
        if answer.get("cancelled") or not answer.get("value"):
            return text("The user did not provide it.")
        secrets_store.set_secret(name, answer["value"].strip())
        return text(f"{secrets_store.KNOWN[name]} saved securely.")

    @tool("ask_folder", "Let the user choose a folder on their PC. Returns the chosen path.",
          {"title": str, "explanation": str, "suggested_path": str})
    async def ask_folder(args):
        answer = await session.ask_user("folder", {"title": args.get("title", "Choose a folder"),
                                                   "explanation": args.get("explanation", ""),
                                                   "suggested": args.get("suggested_path") or str(paths.default_workspace())})
        if interrupted(answer):
            return interrupted(answer)
        if answer.get("cancelled") or not answer.get("value"):
            return text("The user did not choose a folder.")
        return text(f"The user chose: {answer['value']}")

    @tool("ask_choice", "Show the user clickable options (they can also type their own answer).",
          {"question": str, "options": list})
    async def ask_choice(args):
        opts = [str(o) for o in (args.get("options") or [])][:5]
        answer = await session.ask_user("choice", {"title": args.get("question", ""), "options": opts})
        if interrupted(answer):
            return interrupted(answer)
        return text(f"The user answered: {answer.get('value') or '(no answer)'}")

    @tool("github_check", "Check that the saved GitHub token works and which account it belongs to.", {})
    async def github_check(args):
        try:
            info = github.check()
        except github.GitHubError as e:
            return text(str(e), True)
        settings.save({"github_login": info["login"]})
        note = "" if "repo" in info["scopes"] or not info["scopes"] else \
            f" Warning: the token's scopes are '{info['scopes']}', 'repo' is needed to create repositories."
        return text(f"GitHub connected as {info['login']}.{note}")

    @tool("github_create_repo", "Create a GitHub repository for this project (asks the user first).",
          {"name": str, "private": bool, "description": str, "explanation": str})
    async def github_create_repo(args):
        try:
            repo = github.create_repo(re.sub(r"[^A-Za-z0-9._-]", "-", args["name"])[:90],
                                      bool(args.get("private", True)), args.get("description", ""))
        except github.GitHubError as e:
            return text(str(e), True)
        projects.update(slug, github_repo=repo["url"], github_clone=repo["clone_url"])
        return text(f"Repository created: {repo['url']}")

    @tool("github_push", "Save the project's current files to its GitHub repository (asks the user first).",
          {"message": str, "explanation": str})
    async def github_push(args):
        p = projects.load(slug) or {}
        if not p.get("github_clone"):
            return text("This project has no GitHub repository yet. Use github_create_repo first.", True)
        try:
            last = github.push(projects.project_dir(slug), args.get("message", "Update"), p["github_clone"])
        except github.GitHubError as e:
            return text(f"Push failed: {e}", True)
        return text(f"Pushed to {p['github_repo']} (latest commit: {last}).")

    @tool("handoff_to_architect", "Fred only: when the user is satisfied with the requirements, hand the "
          "conversation to Mat, the lead architect, who agrees HOW it is built with the user. Say nothing after it.",
          {"summary": str, "prior_art_skipped_because": str, "challenge_skipped_because": str})
    async def handoff_to_architect(args):
        summary = " ".join(str(args.get("summary", "")).split())[:2000]
        p = projects.load(slug) or {}
        if settings.load().get("prior_art", True) and not p.get("prior_art_done") \
                and not str(args.get("prior_art_skipped_because", "")).strip():
            return text("Prior-art check first: brief the scout worker to search for existing solutions, then call "
                        "record_prior_art. (Or pass prior_art_skipped_because if the user asked to skip it.)", True)
        skip = str(args.get("challenge_skipped_because", "")).strip()
        if not skip and not p.get("existing_folder"):
            miss = challenge.missing(p.get("problem") or {})
            if miss:
                return text("The Problem card is not complete (missing: " + ", ".join(miss) + "). Ask the user, save it "
                            "with record_problem, then hand over. (Or pass challenge_skipped_because if the user asked to "
                            "skip it.)", True)
            rev = p.get("challenge") or {}
            if not rev.get("findings"):
                return text("Challenge review first: brief the \"challenger\" worker to review REQUIREMENTS.md cold, then "
                            "call record_challenge_review with its findings so the user decides on each. (Or pass "
                            "challenge_skipped_because if the user asked to skip it.)", True)
            if not rev.get("done"):
                return text("The user has not decided on every point of the challenge review yet: ask about the open "
                            "ones, then call record_challenge_review again with them.", True)
        projects.update(slug, phase="architecture", architecture_approved=False, plan_approved=False)
        session.hub.publish(slug, {"type": "handoff", "from": "Fred", "to": "Mat", "summary": summary})
        session.hub.publish(slug, {"type": "project_list", "transient": True})
        session.pending_kickoff = ARCH_KICKOFF.replace("{summary}", summary or "(none)")
        return text("Handed over to Mat. End your turn now without writing anything else: Mat introduces himself next.")

    @tool("request_architecture_approval", "Mat only: show the architecture to the user for approval. "
          "architecture_markdown = the full ARCHITECTURE.md; decisions = [{topic, decision, reason}] (data sources "
          "and refresh, storage, encryption, hosting, backups...). Returns 'approved' or the requested changes.",
          {"title": str, "architecture_markdown": str, "decisions": list, "objections": list})
    async def request_architecture_approval(args):
        if (projects.load(slug) or {}).get("phase") != "architecture":
            return text("Only Mat, during the architecture phase, can ask for the architecture approval.", True)
        try:
            decisions = normalize_decisions(args.get("decisions"))
        except ValueError as e:
            return text(f"{e} Call request_architecture_approval again.", True)
        objections = normalize_objections(args.get("objections"))
        doc = str(args.get("architecture_markdown", "")).rstrip().split("\n## Decisions\n")[0].rstrip()
        doc = (doc or "# Architecture") + "\n\n## Decisions\n" + decisions_markdown(decisions) + "\n"
        (projects.project_dir(slug) / "ARCHITECTURE.md").write_text(doc, encoding="utf-8")
        projects.update(slug, architecture_approved=False, architecture_decisions=decisions)
        session.hub.publish(slug, {"type": "docs_changed", "transient": True})
        session.engine.snapshot_docs(slug, "Mat proposed the architecture for approval")
        answer = await session.ask_user("architecture", {"title": args.get("title") or "Architecture",
                                                         "markdown": doc.split("\n## Decisions\n")[0],
                                                         "decisions": decisions, "objections": objections})
        if interrupted(answer):
            return interrupted(answer)
        if answer.get("approved"):
            projects.update(slug, architecture_approved=True, architecture_approved_at=time.time(), phase="plan")
            tracking.mark_approved(slug, "architecture")
            session.hub.publish(slug, {"type": "handoff", "from": "Mat", "to": "Fred",
                                       "summary": "Architecture approved. Fred prepares the plan by iterations."})
            session.hub.publish(slug, {"type": "project_list", "transient": True})
            session.pending_kickoff = PLAN_KICKOFF + diagram_hint("architecture")
            return text("The user APPROVED the architecture. End your turn now without writing anything else: "
                        "Fred takes over to plan the iterations.")
        return text("The user did NOT approve the architecture yet. Requested changes: " +
                    (answer.get("feedback") or "(none given)") + ". Discuss them, update ARCHITECTURE.md and ask again.")

    @tool("return_to_fred", "Mat only: hand the conversation back to Fred when the user wants to change the "
          "requirements (what is built). Say nothing after it.", {"reason": str})
    async def return_to_fred(args):
        reason = " ".join(str(args.get("reason", "")).split())[:1000]
        projects.update(slug, phase="discover", architecture_approved=False)
        session.hub.publish(slug, {"type": "handoff", "from": "Mat", "to": "Fred", "summary": reason})
        session.hub.publish(slug, {"type": "project_list", "transient": True})
        session.pending_kickoff = BACK_KICKOFF.replace("{reason}", reason or "(none given)")
        return text("Handed back to Fred. End your turn now without writing anything else.")

    @tool("request_plan_approval", "Show the plan to the user for EXPRESS approval. The plan must be split into "
          "iterations (one feature each, in build order): iterations = [{name, goal, requirements: [REQ-1, ...]}]. "
          "Existing folder: list the work that was ALREADY finished before AI Team Lite first, each with already_done: true "
          "(shown as done, nothing is rebuilt), then the remaining iterations. "
          "Nothing may be built until the user approves. Saves PLAN.md. Returns 'approved' or the requested changes.",
          {"title": str, "plan_markdown": str, "iterations": list, "objections": list})
    async def request_plan_approval(args):
        if not projects.architecture_approved(projects.load(slug) or {}):
            return text("The architecture has not been approved yet. First call handoff_to_architect so Mat agrees the "
                        "architecture with the user; the plan comes after.", True)
        try:
            its = iterations.normalize(args.get("iterations"))
        except ValueError as e:
            return text(f"{e} Call request_plan_approval again with the iterations list.", True)
        # a new plan always needs a new approval before anything is built
        projects.update(slug, phase="plan", plan_approved=False)
        proposed = iterations.propose(slug, its)
        plan = args["plan_markdown"].rstrip() + "\n\n## Iterations\n" + iterations.markdown(proposed) + "\n"
        folder = projects.project_dir(slug)
        (folder / "PLAN.md").write_text(plan, encoding="utf-8")
        session.hub.publish(slug, {"type": "docs_changed", "transient": True})
        session.engine.snapshot_docs(slug, "Fred proposed the plan for approval")
        answer = await session.ask_user("plan", {"title": args.get("title") or "Plan of action", "markdown": plan,
                                                 "iterations": proposed, "estimate": iterations.cost_estimate(slug),
                                                 "objections": normalize_objections(args.get("objections"))})
        if interrupted(answer):
            return interrupted(answer)
        if answer.get("approved"):
            if answer.get("include_cleanup") is False:
                proposed = iterations.remove_cleanup(slug)
            if isinstance(answer.get("after"), dict):
                proposed = iterations.set_after(slug, answer["after"])
            projects.update(slug, phase="execute", plan_approved=True, plan_approved_at=time.time())
            first = iterations.approve(slug)
            plan = args["plan_markdown"].rstrip() + "\n\n## Iterations\n" + iterations.markdown(iterations.load(slug)) + "\n"
            (folder / "PLAN.md").write_text(plan, encoding="utf-8")
            session.engine.snapshot_docs(slug, "Plan approved")
            tracking.mark_approved(slug, "plan")
            tracking.mark_approved(slug, "requirements")
            pauses = [str(it["n"]) for it in proposed if it["status"] != "done" and it.get("after") == "pause"]
            return text(f"The user APPROVED the plan ({len([i for i in proposed if i['status'] != 'done'])} iterations). "
                        f"Start with iteration {first['n']} ({first['name']}): call start_iteration({first['n']}), then "
                        "build, test, and commit_iteration before moving on." +
                        (f" The user wants to release the next iteration themselves after iteration(s) {', '.join(pauses)}."
                         if pauses else ""))
        projects.update(slug, phase="plan", plan_approved=False)
        return text("The user did NOT approve yet. Do not build anything. Requested changes: " +
                    (answer.get("feedback") or "(none given)"))

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
                    "tests for the acceptance criteria that fail now, then record again.\n" + red["output"][-800:], True)

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
                        f"again. Test output:\n{result['output'][-2000:]}", True)
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

    @tool("finish_setup", "Mark the first-time setup as complete.", {"summary": str})
    async def finish_setup(args):
        settings.save({"setup_complete": True})
        session.hub.publish(slug, {"type": "setup_done", "summary": args.get("summary", "")})
        session.hub.publish(slug, {"type": "project_list", "transient": True})
        return text("Setup is complete.")


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

    @tool("record_problem", "Fred: save what you learned about the PROBLEM (before any requirement). Fields: who (who is "
          "affected), how_often, cost_today (time, money, errors, frustration, with numbers when possible), how_now (how "
          "it is done today), success (what will be measurably better), statement (one plain sentence). Send only what "
          "you know; earlier answers are kept. Shows the Problem card to the user and returns what is still missing.",
          {"who": str, "how_often": str, "cost_today": str, "how_now": str, "success": str, "statement": str})
    async def record_problem(args):
        prob = challenge.save_problem(slug, args)
        miss = challenge.missing(prob)
        session.hub.publish(slug, {"type": "problem", **prob, "missing": miss})
        session.hub.publish(slug, {"type": "docs_changed", "transient": True})
        if miss:
            return text("Saved. Still missing on the Problem card: " + ", ".join(miss) + ". Ask about the most important one "
                        "next (at most two questions at a time).")
        return text("The Problem card is complete. Put it at the top of REQUIREMENTS.md (section Problem), then challenge "
                    "the idea with the checklist before writing requirements.")

    @tool("record_challenge_review", "Fred: after the challenger worker reviewed the requirements, show its findings to "
          "the user and wait for their decisions. findings = [{kind: objection|contradiction|assumption|clarification, "
          "title, detail, recommendation, requirements: [REQ-n]}] (most important first, at most 10). Returns what the "
          "user decided and what you must change.", {"findings": list})
    async def record_challenge_review(args):
        findings = challenge.normalize(args.get("findings"))
        if not findings:
            return text("No usable findings. Pass findings = [{kind, title, detail, recommendation, requirements}]. If the "
                        "challenger found nothing important, pass one finding of kind 'objection' saying so.", True)
        answer = await session.ask_user("challenge", {"findings": findings, "level": challenge.level()})
        if interrupted(answer):
            return interrupted(answer)
        if answer.get("cancelled"):
            return text("The user closed the review without deciding. Ask them when they want to go through it.")
        decided = challenge.apply_answers(findings, answer.get("answers") or {})
        rev = challenge.save_review(slug, decided)
        folder = projects.project_dir(slug)
        (folder / "reports").mkdir(exist_ok=True)
        (folder / "reports" / "CHALLENGE.md").write_text(
            f"# Challenge review (round {rev['round']})\n\nAn independent reviewer challenged the requirements; these are "
            "the user's decisions.\n\n" + challenge.decisions_markdown(decided) + "\n", encoding="utf-8")
        todo = challenge.instructions_for_fred(decided)
        left = challenge.undecided(decided)
        return text("The user decided:\n" + challenge.decisions_markdown(decided) + "\n\nWhat you must do now:\n" + todo
                    + ("\n\nNot decided yet: " + "; ".join(f["title"] for f in left) + ". Ask about them." if left else "")
                    + "\n\nUpdate REQUIREMENTS.md (Decisions keeps every overruled point with its reason), recap the "
                    "changes in a few lines, then continue (prior-art check, then hand over to Mat).")

    @tool("record_prior_art", "After the scout searched the web: record the existing solutions found. items = "
          "[{name, url, what, closeness: same|similar|different, cost_licence, reuse}]. Shows a card and saves "
          "reports/PRIOR_ART.md.", {"items": list, "recommendation": str})
    async def record_prior_art(args):
        items = [i for i in args.get("items") or [] if isinstance(i, dict) and i.get("name")][:30]
        for i in items:
            if i.get("url") and not str(i["url"]).startswith(("http://", "https://")):
                i["url"] = ""
        rec = str(args.get("recommendation", "")).strip()[:2000]
        folder = projects.project_dir(slug)
        (folder / "reports").mkdir(exist_ok=True)
        (folder / "reports" / "PRIOR_ART.md").write_text(prior_art_markdown(items, rec), encoding="utf-8")
        projects.update(slug, prior_art_done=True)
        session.hub.publish(slug, {"type": "prior_art", "items": items, "recommendation": rec})
        return text(f"Recorded {len(items)} findings and shown to the user. Discuss what to reuse, then hand over to Mat.")

    @tool("ask_council", "Ask the council about a big decision: several brains answer, review each other anonymously, "
          "and you get all opinions and rankings back to write the final recommendation as chairman. Write your own "
          "answer first (your_answer). scout_report: the Scout's findings if any.",
          {"question": str, "context": str, "your_answer": str, "scout_report": str})
    async def ask_council(args):
        ms = council.members()
        if not ms:
            return text("No helper brain is switched on and reachable, so there is no council. Tell the user they can add "
                        "brains in Settings > Brains (or use automatic detection), and decide yourself.")
        q = str(args.get("question", ""))[:2000]
        est = council.estimate(ms, len(str(args.get("context", ""))))
        card_id = session.engine.new_id()
        session.hub.publish(slug, {"type": "council", "id": card_id, "status": "working", "question": q,
                                   "members": [brains.label(b) for b in ms], "estimate": est,
                                   "warnings": council.privacy_warnings(ms)})
        for b in ms:
            session.set_role(brains.label(b), status="working", task="Council: " + q[:60], last="answering the council")

        def record(who, u, extra):
            usage.record_tokens(slug, "Council · " + who, u, model=extra.get("model", ""))

        result = await session.run_blocking(lambda: council.run(q, str(args.get("context", "")),
                                                                str(args.get("your_answer", "")), session.persona,
                                                                str(args.get("scout_report", "")), record=record))
        for b in ms:
            session.set_role(brains.label(b), status="idle", task="", last="answered the council")
        session.hub.update(slug, card_id, status="done", opinions=result["opinions"], reviews=result["reviews"],
                           scores=result["scores"], ranked=result["ranked"], agreement=result["agreement"],
                           failed=result["failed"])
        return text(council.for_chairman(result))

    @tool("record_change_request", "After the plan was approved, record a change the user wants (CR-n) with its impact. "
          "The user approves or rejects it on a card.",
          {"title": str, "description": str, "affects_requirements": list, "affects_decisions": list,
           "affects_iterations": list, "effort": str})
    async def record_change_request(args):
        cr = tracking.add_change_request(slug, {k: args.get(k) for k in (
            "title", "description", "affects_requirements", "affects_decisions", "affects_iterations", "effort")})
        answer = await session.ask_user("change", {**{k: v for k, v in cr.items() if k != "id"}, "cr": cr["id"]})
        if interrupted(answer):
            return interrupted(answer)
        cr = tracking.decide_change_request(slug, cr["id"], bool(answer.get("approved")), str(answer.get("note", ""))) or cr
        if cr["status"] == "approved":
            arch = " It touches architecture decisions: hand over to Mat first." if cr.get("affects_decisions") else ""
            return text(f"{cr['id']} APPROVED. Update REQUIREMENTS.md (mark it {cr['id']}), then propose the revised plan "
                        f"with request_plan_approval.{arch}")
        return text(f"{cr['id']} was rejected by the user" + (f": {cr.get('note')}" if cr.get("note") else "") + ". Do not do it.")

    @tool("run_security_test", "Before a new version: run the security review and return what to do with the results.", {})
    async def run_security_test(args):
        return text("Security review: brief the Reviewer for a security review of the WHOLE codebase with the bundled "
                    "security-audit skill (.aiteam/skills/security-audit, guidance mode, ATTACK-CLASSES.md), writing "
                    "reports/SECURITY_REVIEW.md. High findings become fix tasks before the version; tell the user the summary.")

    setup_tools = [check_environment, save_setting, request_secret, ask_folder, ask_choice, github_check, finish_setup]
    project_tools = [check_environment, save_setting, request_secret, ask_choice, github_check, github_create_repo,
                     github_push, request_plan_approval, take_screenshot, show_image, launch_for_user,
                     record_test_result, start_iteration, commit_iteration]
    project_tools += [handoff_to_architect, complete_task, record_red_tests, record_prior_art, ask_council,
                      record_change_request, run_security_test,
                      record_problem, record_challenge_review, record_diagram]
    architect_tools = [check_environment, ask_choice, request_architecture_approval, return_to_fred,
                       ask_council]
    chosen = setup_tools if session.kind == "setup" else (architect_tools if session.persona == "Mat" else project_tools)
    return create_sdk_mcp_server(name="aiteam", version="1.0.0", tools=chosen), chosen
