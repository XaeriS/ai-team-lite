"""Small helpers and texts shared by the team's tools: answers, test results, diagrams, decisions (L46)."""
from __future__ import annotations

import json
from pathlib import Path
import time


def interrupted(answer: dict) -> dict | None:
    if answer.get("interrupted"):
        from .engine_text import interrupted_text

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
