"""Tools for agreeing what to build: the problem, the challenge, existing tools, the council, the architecture, the plan, change requests.

make(session) returns this group's tools by name; app/tools.py puts the groups together (L46)."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING
import uuid

from claude_agent_sdk import tool

from . import brains, challenge, council, diagrams, ideas as ideas_mod, iterations, projects, settings, tracking, usage

if TYPE_CHECKING:
    from .session import Session

from .tools_common import (ARCH_KICKOFF, BACK_KICKOFF, PLAN_KICKOFF, decisions_markdown, diagram_hint, interrupted,
                           normalize_decisions, normalize_objections, prior_art_markdown, text)


def make(session: "Session") -> dict:
    slug = session.slug

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
            session.pending_kickoff = PLAN_KICKOFF   # L69: the plan first; the diagram is offered once it is approved
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
            draw = ""
            if diagram_hint("architecture") and not any(d.get("topic") == "architecture" for d in diagrams.items(slug)):
                pick = await session.ask_user("choice", {   # L69: the user decides whether the diagrammer starts now
                    "title": "Draw the overview diagram before building starts?",
                    "options": ["Draw it", "Skip"],
                    "hints": ["The diagrammer draws how the app will work (a few minutes, some of your Claude plan).",
                              "Go straight to building; the first iteration that adds parts draws it, or ask any time "
                              "from the Diagrams tab."]})
                if interrupted(pick):
                    return interrupted(pick)
                if str(pick.get("value") or "").lower().startswith("draw"):
                    draw = " Before that:" + diagram_hint("architecture")
            return text(f"The user APPROVED the plan ({len([i for i in proposed if i['status'] != 'done'])} iterations). "
                        f"Start with iteration {first['n']} ({first['name']}): call start_iteration({first['n']}), then "
                        "build, test, and commit_iteration before moving on." + draw +
                        (f" The user wants to release the next iteration themselves after iteration(s) {', '.join(pauses)}."
                         if pauses else ""))
        projects.update(slug, phase="plan", plan_approved=False)
        return text("The user did NOT approve yet. Do not build anything. Requested changes: " +
                    (answer.get("feedback") or "(none given)"))

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
                    "the idea with the checklist (with a short 'Why I ask' line for each question), then call "
                    "record_questions before writing the requirements.")

    @tool("record_questions", "Fred: at the end of the problem stage, summarise the questions that changed the project: "
          "first_ask (what the user first asked for, in their words), real_need (what they actually need), questions = "
          "[{question, why (what goes wrong when it is skipped), changed (what it changed in this project)}], most "
          "important first. Shows the user the card 'The questions that changed your project' and saves "
          "reports/QUESTIONS.md.", {"first_ask": str, "real_need": str, "questions": list})
    async def record_questions(args):
        qs = challenge.normalize_questions(args.get("questions"))
        if not qs:
            return text("Pass questions = [{question, why, changed}] (at least one).", True)
        data = challenge.save_questions(slug, args.get("first_ask", ""), args.get("real_need", ""), qs)
        session.hub.publish(slug, {"type": "questions", **data})
        session.hub.publish(slug, {"type": "docs_changed", "transient": True})
        return text("Shown to the user and saved in reports/QUESTIONS.md. Continue with the requirements.")

    @tool("record_challenge_review", "Fred: after the challenger worker reviewed the requirements, show its findings to "
          "the user and wait for their decisions. findings = [{kind: objection|contradiction|assumption|clarification, "
          "current (what the requirements say today), title, detail, recommendation, requirements: [REQ-n]}] (most "
          "important first, at most 10). Returns what the "
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

    @tool("propose_ideas", "Fred: after an iteration was committed, show at most 3 improvement ideas to the user, who "
          "answers Yes or No on each. ideas = [{title, why (one plain sentence), effort: small|medium|large, "
          "source_link (optional)}]. Ideas already decided, or already in the requirements or plan, are dropped.",
          {"ideas": list})
    async def propose_ideas(args):
        folder = projects.project_dir(slug)
        given = [i for i in args.get("ideas") or [] if isinstance(i, dict) and str(i.get("title", "")).strip()]
        if not given:
            return text("There was no idea to show, so no card was shown. Carry on.")
        known = []
        for name in ("REQUIREMENTS.md", "PLAN.md"):
            try:
                known.append((folder / name).read_text(encoding="utf-8"))
            except OSError:
                pass
        fresh = ideas_mod.filter_new(folder, given, known)
        if not fresh:
            return text("All of these ideas were already decided or are already in the requirements or plan. Do not "
                        "propose them again; no card was shown.")
        rows = [{"id": uuid.uuid4().hex[:8], "title": " ".join(str(i["title"]).split())[:200],
                 "why": " ".join(str(i.get("why", "")).split())[:500], "effort": str(i.get("effort", ""))[:40],
                 "source": str(i.get("source_link") or "")[:300] if str(i.get("source_link") or "").startswith(("http://", "https://")) else ""}
                for i in fresh]
        answer = await session.ask_user("ideas", {"ideas": rows})
        if interrupted(answer):
            return interrupted(answer)
        if answer.get("cancelled"):
            return text("The user closed the ideas card without deciding. Do not ask again now.")
        yes = [r for r in rows if any(d.get("idea") == r["id"] and d.get("decision") == "yes"
                                      for d in answer.get("decisions") or [])]
        if not yes:
            return text("The user said No to every idea (logged in reports/IDEAS.md). Carry on.")
        return text("The user said Yes to: " + "; ".join(f"{r['title']} ({r['why']})" for r in yes) + ". For each one, call "
                    "record_change_request (title, description, effort) so the user can approve it and the plan is revised. "
                    "The others were logged as No.")

    return {"propose_ideas": propose_ideas, "handoff_to_architect": handoff_to_architect,"request_architecture_approval": request_architecture_approval, "return_to_fred": return_to_fred, "request_plan_approval": request_plan_approval, "record_problem": record_problem, "record_questions": record_questions, "record_challenge_review": record_challenge_review, "record_prior_art": record_prior_art, "ask_council": ask_council, "record_change_request": record_change_request}
