"""Fred pushes back like a business analyst (item 79).

- The Problem card: before any requirement, Fred and the user agree who is affected, how often, what it costs today,
  how it is done now and what success looks like. Fred cannot hand over to the architect while it is incomplete.
- The challenge review: an independent "challenger" reads the requirements cold and raises its strongest objections,
  the contradictions it finds, the assumptions nobody confirmed and the points that are still vague. The user decides
  on each one (change it / keep it / accept the risk; confirm or correct an assumption; answer a vague point).
  The user can always overrule; the reason is kept in the decision log so IT can see it later.
- Challenge level (Settings, Advanced): gentle / normal / firm.
"""
from __future__ import annotations

import time
import uuid

from . import projects, settings

PROBLEM_FIELDS = (
    ("who", "Who is affected"),
    ("how_often", "How often it happens"),
    ("cost_today", "What it costs today"),
    ("how_now", "How it is done now"),
    ("success", "What success looks like"),
)
# L5: why each question matters, in plain words (what goes wrong when it is skipped)
PROBLEM_WHY = {
    "who": "If nobody knows who is affected, the tool gets built for the wrong people and nobody uses it.",
    "how_often": "A problem that happens twice a year rarely pays for new software; one that happens every day often does.",
    "cost_today": "Without knowing what the problem costs today, nobody can tell whether the solution is worth building "
                  "and looking after.",
    "how_now": "The way it is done today hides rules nobody wrote down; skip it and the new tool misses them.",
    "success": "Without a measurable goal you cannot tell when it is finished, or whether it helped at all.",
}
CHECKLIST = (
    ("Does something you already have do this, or would a change in the way of working solve it?",
     "Building what Excel, your company systems or a simple process change already do wastes weeks and leaves one more "
     "tool to maintain."),
    ("Where does the data come from, and who uses the result?",
     "A tool that ignores what happens before and after it breaks other people's work or relies on data nobody owns."),
    ("Does it create a second copy of the truth?",
     "Data kept in two places drifts apart, and people stop knowing which number is right."),
    ("Who looks after it when you are away or leave?",
     "A tool only one person understands stops working the day that person is not there."),
    ("Is there sensitive data, and who may see it?",
     "Personal, financial or confidential data in the wrong place is a legal and trust problem, not just a bug."),
    ("Is this the smallest thing that solves the problem?",
     "Big first versions take longer, cost more and are often wrong; a small one shows quickly whether it helps."),
)
KIND_WHY = {
    "objection": "Skipping this kind of question is how tools get built that nobody needs or that break something else.",
    "contradiction": "Two requirements that cannot both be true mean the team will have to guess, and one of them will be wrong.",
    "assumption": "An assumption nobody checked is the most common reason a finished tool does not work with the real data.",
    "clarification": "A vague requirement cannot be tested, so nobody can prove it works.",
}
LEVELS = ("gentle", "normal", "firm")
KINDS = ("objection", "contradiction", "assumption", "clarification")
# what the user can decide for each kind of finding
DECISIONS = {"objection": ("change", "keep", "accept"), "contradiction": ("change", "keep", "accept"),
             "assumption": ("confirm", "correct"), "clarification": ("answer",)}

LEVEL_TEXT = {
    "gentle": ("Challenge level: GENTLE. Fill the Problem card with the user and raise only serious concerns (something "
               "that would clearly waste their time or cause harm). One challenge per topic at most; stay encouraging."),
    "normal": ("Challenge level: NORMAL. Fill the Problem card, then go through the challenge checklist. Say clearly and "
               "kindly when something looks unnecessary, duplicated or risky, and propose the simpler alternative."),
    "firm": ("Challenge level: FIRM. Ask for evidence: real numbers and real examples for the cost of the problem. Question "
             "every must-have (\"what happens if we leave it out?\"). When an existing tool, a setting, a template or a "
             "change in the process would solve it, recommend NOT building and say why. Be direct, never rude; the "
             "user still decides."),
}


def level() -> str:
    v = str(settings.load().get("challenge_level", "normal")).lower()
    return v if v in LEVELS else "normal"


def level_text() -> str:
    return LEVEL_TEXT[level()]


# ---------------------------------------------------------------------------- the Problem card
def problem(slug: str) -> dict:
    return dict((projects.load(slug) or {}).get("problem") or {})


def missing(prob: dict) -> list[str]:
    return [label for key, label in PROBLEM_FIELDS if not str(prob.get(key) or "").strip()]


def save_problem(slug: str, data: dict) -> dict:
    """Merge what Fred learned into the card (empty values never erase an earlier answer)."""
    prob = problem(slug)
    for key in [k for k, _ in PROBLEM_FIELDS] + ["statement"]:
        v = " ".join(str(data.get(key) or "").split())[:600]
        if v:
            prob[key] = v
    prob["updated"] = time.time()
    projects.update(slug, problem=prob)
    return prob
# ---------------------------------------------------------------------------- the challenge review
def review(slug: str) -> dict:
    return dict((projects.load(slug) or {}).get("challenge") or {})


def normalize(raw) -> list[dict]:
    """Findings from the challenger: [{kind, title, detail, recommendation}]. Keeps the useful ones, at most 10."""
    out = []
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict):
            continue
        kind = str(f.get("kind") or "objection").strip().lower()
        kind = kind if kind in KINDS else "objection"
        title = " ".join(str(f.get("title") or f.get("objection") or "").split())[:200]
        if not title:
            continue
        out.append({"id": uuid.uuid4().hex[:8], "kind": kind, "title": title,
                    "detail": " ".join(str(f.get("detail") or "").split())[:800],
                    "recommendation": " ".join(str(f.get("recommendation") or f.get("answer") or "").split())[:600],
                    "why": " ".join(str(f.get("why") or f.get("why_asked") or "").split())[:400] or KIND_WHY[kind],
                    "requirements": [str(r).strip().upper() for r in (f.get("requirements") or []) if str(r).strip()][:8]})
    return out[:10]


def apply_answers(findings: list[dict], answers: dict) -> list[dict]:
    """answers = {finding id: {decision, reason}}. Unknown decisions fall back to the first allowed one for its kind."""
    out = []
    for f in findings:
        a = answers.get(f["id"]) if isinstance(answers, dict) else None
        a = a if isinstance(a, dict) else {}
        allowed = DECISIONS[f["kind"]]
        decision = str(a.get("decision") or "").lower()
        out.append({**f, "decision": decision if decision in allowed else None,
                    "reason": " ".join(str(a.get("reason") or "").split())[:600]})
    return out


def undecided(findings: list[dict]) -> list[dict]:
    return [f for f in findings if not f.get("decision")]


def save_review(slug: str, findings: list[dict]) -> dict:
    prev = review(slug)
    rev = {"round": int(prev.get("round") or 0) + 1, "time": time.time(), "findings": findings,
           "done": not undecided(findings)}
    projects.update(slug, challenge=rev)
    return rev


WORDS = {"change": "change it", "keep": "keep it as it is", "accept": "accept the risk", "confirm": "confirmed",
         "correct": "not correct", "answer": "answered"}


def decisions_markdown(findings: list[dict]) -> str:
    """For REQUIREMENTS.md > Decisions and reports/CHALLENGE.md: what was challenged and what the user decided."""
    rows = []
    for f in findings:
        d = WORDS.get(f.get("decision") or "", "not decided")
        line = f"- **{f['kind'].capitalize()}: {f['title']}** - your decision: {d}"
        if f.get("reason"):
            line += f" (\"{f['reason']}\")"
        rows.append(line)
    return "\n".join(rows)


def instructions_for_fred(findings: list[dict]) -> str:
    """What Fred must do with the user's decisions."""
    todo = []
    for f in findings:
        d, why = f.get("decision"), f.get("reason") or ""
        ref = f" ({', '.join(f['requirements'])})" if f.get("requirements") else ""
        if d == "change":
            todo.append(f"CHANGE the requirements{ref} to resolve: {f['title']}. "
                        + (f"The user said: {why}" if why else f"Use your recommendation: {f['recommendation']}"))
        elif d in ("keep", "accept"):
            todo.append(f"The user OVERRULED \"{f['title']}\" ({WORDS[d]}). Record it under Decisions in REQUIREMENTS.md "
                        f"with the reason: {why or '(no reason given)'}. Do not raise it again.")
        elif d == "confirm":
            todo.append(f"Assumption CONFIRMED: {f['title']}. Move it from Assumptions to Constraints or Decisions.")
        elif d == "correct":
            todo.append(f"Assumption WRONG: {f['title']}. The user says: {why or '(ask them what is right)'}. Fix the "
                        "requirements accordingly.")
        elif d == "answer":
            todo.append(f"Vague point ANSWERED: {f['title']}. The answer: {why}. Turn it into a clear When... then... "
                        "check and remove it from Open questions.")
    return "\n".join(f"{i}. {t}" for i, t in enumerate(todo, 1))


# ---------------------------------------------------------------------------- L5: the questions that changed the project
def normalize_questions(raw) -> list[dict]:
    out = []
    for q in raw if isinstance(raw, list) else []:
        if isinstance(q, str):
            q = {"question": q}
        if not isinstance(q, dict):
            continue
        text = " ".join(str(q.get("question") or "").split())[:300]
        if not text:
            continue
        out.append({"question": text, "why": " ".join(str(q.get("why") or "").split())[:400],
                    "changed": " ".join(str(q.get("changed") or q.get("effect") or "").split())[:400]})
    return out[:12]


def save_questions(slug: str, first_ask: str, real_need: str, questions: list[dict]) -> dict:
    data = {"first_ask": " ".join(str(first_ask or "").split())[:600],
            "real_need": " ".join(str(real_need or "").split())[:600], "questions": questions, "time": time.time()}
    projects.update(slug, questions=data)
    folder = projects.project_dir(slug)
    (folder / "reports").mkdir(exist_ok=True)
    (folder / "reports" / "QUESTIONS.md").write_text(questions_markdown(data), encoding="utf-8")
    return data


def questions_markdown(data: dict) -> str:
    lines = ["# The questions that changed your project", "",
             f"**What you first asked for:** {data.get('first_ask') or '-'}", "",
             f"**What you actually need:** {data.get('real_need') or '-'}", "", "## The questions that moved it", ""]
    for i, q in enumerate(data.get("questions") or [], 1):
        lines.append(f"{i}. **{q['question']}**")
        if q.get("why"):
            lines.append(f"   - Why it matters: {q['why']}")
        if q.get("changed"):
            lines.append(f"   - What it changed: {q['changed']}")
    lines += ["", "## Questions to ask on your next project", "",
              "Ask these before anything gets built. Each one prevents a common way projects go wrong.", ""]
    for key, label in PROBLEM_FIELDS:
        lines.append(f"- **{label}?** {PROBLEM_WHY[key]}")
    for q, why in CHECKLIST:
        lines.append(f"- **{q}** {why}")
    return "\n".join(lines) + "\n"
