"""Community skills the team may use (reviewed copies in app/skills, see SOURCES.md).

A skill is a folder of instructions. AI Team Lite copies the switched-on skills into the project's .aiteam/skills folder
and tells the matching team member to read and follow it. Nothing is installed in the user's own Claude setup.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import settings

BUNDLED = Path(__file__).parent / "skills"
SKILLS = {
    "ponytail": {"label": "Ponytail", "who": ["Developer"], "when": "always",
                 "what": "The Developer writes the smallest change that fully solves each task: less code, fewer tokens.",
                 "how": "Follow it at level 'full' for all code you write. Never cut validation, error handling, security "
                        "or accessibility."},
    "security-audit": {"label": "Security audit (Cloudflare)", "who": ["Reviewer"], "when": "always",
                       "what": "The Reviewer checks every iteration's changes for security problems before the commit.",
                       "how": "Use it in GUIDANCE MODE only: a focused review of this iteration's changes with "
                              "ATTACK-CLASSES.md (the other companion files it mentions are not included; skip them). "
                              "Do not run the full six-phase audit, do not create an output directory, do not run the "
                              "project's code outside the tests. Put findings in reports/REVIEW.md under 'Security', each "
                              "with severity (critical/high/medium/low), file, evidence and the smallest fix."},
    "impeccable": {"label": "Impeccable (design)", "who": ["Developer", "Reviewer"], "when": "ui",
                   "what": "For apps with screens: better-looking, consistent interfaces without generic AI design.",
                   "how": "Use it for every screen you build or review. Its launcher (scripts/impeccable) is NOT included: "
                          "follow its 'Launcher unavailable' path, read reference/craft-floor.md before UI edits, "
                          "reference/operate.md for app screens and reference/polish.md before the iteration ends."},
    "no-ai-slop": {"label": "No AI slop (writing)", "who": ["Fred", "Mat", "Reviewer"], "when": "always",
                   "what": "Removes predictable AI phrasing from messages, documents and the text inside the apps.",
                   "how": "Fred and Mat: apply its rules to your own messages and to REQUIREMENTS.md, ARCHITECTURE.md, "
                          "PLAN.md, STATUS.md and README. Reviewer: use DETECT mode on the user-facing text of the app "
                          "(labels, errors, help) and list findings. Never change code, numbers, names or requirement IDs."},
}
SKILLS["archify"] = {
    "label": "Archify (diagrams)", "who": ["Fred"], "when": "always",
    "what": "Clear \"how it works\" diagrams of your app (8-12 labelled parts), checked against the code. Each one uses "
            "some Claude tokens.",
    "how": "You have a diagrammer worker (subagent_type 'diagrammer', on a cheaper model). Ask it for a diagram: right "
           "after the architecture is approved (topic 'architecture': the overall picture), after an iteration that "
           "adds or changes parts or connections (update topic 'architecture'; the app then shows what changed), and "
           "whenever the user asks how something works. Give it ONE precise question. Do not ask for diagrams of tiny "
           "changes: each one costs tokens."}
INSTALLED_TOOLS = {"archify"}  # skills that need a program installed on first use (see tooling.py)


def enabled() -> dict:
    on = settings.load().get("skills") or {}
    return {k: bool(on.get(k, True)) for k in SKILLS}


def applies(key: str, project: dict) -> bool:
    when = SKILLS[key]["when"]
    if when == "ui":
        return project.get("has_ui", True)  # unknown -> assume it has screens
    if key == "archify":
        from . import tooling
        return tooling.archify_ready()   # only when the diagram program is installed
    return True


def install_into(folder: Path) -> Path:
    """Copy the bundled skills into the project (kept out of Git with the rest of .aiteam)."""
    dest = folder / ".aiteam" / "skills"
    for key in SKILLS:
        src = BUNDLED / key
        if src.exists():
            shutil.copytree(src, dest / key, dirs_exist_ok=True)
    return dest


def note_for(role: str, project: dict, folder: Path | None = None) -> str:
    """Instructions to append to a team member's prompt for the skills that apply to them."""
    on = enabled()
    base = (folder / ".aiteam" / "skills") if folder else Path(".aiteam/skills")
    lines = []
    for key, s in SKILLS.items():
        if role in s["who"] and on[key] and applies(key, project) and key not in INSTALLED_TOOLS:
            lines.append(f"- {s['label']}: read {base / key / 'SKILL.md'} first. {s['how']}")
        elif role in s["who"] and on[key] and applies(key, project):
            lines.append(f"- {s['label']}: {s['how']}")
    if not lines:
        return ""
    return "\n## Skills you use (read them before working)\n" + "\n".join(lines) + "\n"


def summary() -> list[dict]:
    on = enabled()
    return [{"key": k, "label": s["label"], "what": s["what"], "who": s["who"], "enabled": on[k],
             "needs_install": k in INSTALLED_TOOLS} for k, s in SKILLS.items()]
