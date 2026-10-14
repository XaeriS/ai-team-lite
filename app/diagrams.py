"""'How it works' diagrams of a project (item 78), drawn with Archify by the team's diagrammer.

A diagram is a self-contained interactive HTML file in the project's .archify folder plus the typed JSON it was drawn
from. The project keeps a list of them; the main overall picture uses the topic "architecture", and every new version
of it is compared with the previous one so the user can see what an iteration added or changed.
"""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from . import iterations, projects, tooling

KINDS = ("architecture", "workflow", "sequence", "dataflow", "lifecycle", "erd", "class", "tree", "timeline",
         "waterfall")


def _inside(slug: str, rel: str) -> Path:
    folder = projects.project_dir(slug).resolve()
    p = (folder / str(rel or "")).resolve()
    if folder not in p.parents or not p.is_file():
        raise ValueError(f"{rel} is not a file in the project folder.")
    return p


def items(slug: str) -> list[dict]:
    return list((projects.load(slug) or {}).get("diagrams") or [])


def record(slug: str, title: str, topic: str, kind: str, html: str, json_path: str, question: str = "") -> dict:
    """Check a finished diagram and add it to the project. Returns the stored entry."""
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"Unknown diagram type '{kind}'. Use one of: {', '.join(KINDS)}.")
    h, j = _inside(slug, html), _inside(slug, json_path)
    ok, detail = tooling.archify_check(h, projects.project_dir(slug).resolve())
    if not ok:
        raise ValueError(f"Archify's check did not pass for {html}: {detail}. Fix the candidate and finalize again.")
    folder = projects.project_dir(slug).resolve()
    topic = re.sub(r"[^a-z0-9-]+", "-", str(topic or kind).lower()).strip("-")[:40] or kind
    cur = iterations.current(slug)
    done = [it for it in iterations.load(slug) if it.get("status") == "done"]
    entry = {"id": uuid.uuid4().hex[:10], "title": " ".join(str(title).split())[:120] or topic, "topic": topic,
             "kind": kind, "html": h.relative_to(folder).as_posix(), "json": j.relative_to(folder).as_posix(),
             "question": str(question)[:500], "created": time.time(),
             "iteration": (cur or (done[-1] if done else {})).get("n"), "compare": ""}
    previous = next((d for d in reversed(items(slug)) if d["topic"] == topic and d["kind"] == kind), None)
    if previous and kind == "architecture":
        try:
            base = _inside(slug, previous["json"])
            out = h.parent / f"{h.stem}-changes.html"
            if tooling.archify_compare(base, j, out, folder):
                entry["compare"] = out.relative_to(folder).as_posix()
                entry["compare_with"] = previous["id"]
        except ValueError:
            pass  # the earlier version's files are gone: no comparison, the new diagram still counts
    projects.update(slug, diagrams=items(slug) + [entry])
    return entry


def path_of(slug: str, diagram_id: str, which: str = "html") -> Path:
    d = next((x for x in items(slug) if x["id"] == diagram_id), None)
    if not d or not d.get(which):
        raise KeyError(diagram_id)
    return _inside(slug, d[which])
