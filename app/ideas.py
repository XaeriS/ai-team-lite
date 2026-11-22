"""REQ-30: improvement ideas after each iteration, and what the user decided about each.

.aiteam/ideas.jsonl is the append-only log (one decision per line); reports/IDEAS.md is rebuilt from it as a plain table.
An idea that was decided (yes or no) is never proposed again."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from . import safefile

MAX_IDEAS = 3
DECISIONS = ("yes", "no")


def _log(folder: Path) -> Path:
    return Path(folder) / ".aiteam" / "ideas.jsonl"


def norm(title) -> str:
    """Lower case, no punctuation, single spaces: 'Export  to Excel!' and 'export to excel' are the same idea."""
    return " ".join(re.sub(r"[\W_]+", " ", str(title or "").lower()).split())


def decisions(folder: Path) -> list[dict]:
    f = _log(folder)
    with safefile.lock_for(f):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def decided_titles(folder: Path) -> set[str]:
    return {norm(r.get("title")) for r in decisions(folder)} - {""}


def add_decision(folder: Path, idea: dict, decision: str, reason: str = "") -> dict:
    if decision not in DECISIONS:
        raise ValueError("The decision must be yes or no.")
    row = {"id": str(idea.get("id") or uuid.uuid4().hex[:12]), "title": " ".join(str(idea.get("title", "")).split())[:200],
           "why": " ".join(str(idea.get("why", "")).split())[:500], "effort": " ".join(str(idea.get("effort", "")).split())[:40],
           "source": str(idea.get("source") or idea.get("source_link") or "")[:300], "decision": decision,
           "date": time.strftime("%Y-%m-%d"), "reason": " ".join(str(reason or "").split())[:500]}
    f = _log(folder)
    with safefile.lock_for(f):
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        _write_md(folder)
    return row


def _cell(v) -> str:
    return " ".join(str(v or "").split()).replace("|", "/")


def _write_md(folder: Path) -> None:
    lines = ["# Improvement ideas", "", "What was suggested after each iteration and what the user decided.", "",
             "| Idea | Why | Effort | Decision | Date | Reason |", "|---|---|---|---|---|---|"]
    for r in decisions(folder):
        lines.append("| " + " | ".join(_cell(r.get(k)) for k in ("title", "why", "effort", "decision", "date", "reason")) + " |")
    out = Path(folder) / "reports" / "IDEAS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def filter_new(folder: Path, ideas, known_texts) -> list[dict]:
    """At most 3 ideas that were not decided before and are not already in the given documents."""
    seen = decided_titles(folder)
    hay = " " + " ".join(norm(t) for t in known_texts or []) + " "
    out = []
    for i in ideas or []:
        if not isinstance(i, dict):
            continue
        n = norm(i.get("title"))
        if not n or n in seen or f" {n} " in hay:
            continue
        seen.add(n)
        out.append(i)
        if len(out) == MAX_IDEAS:
            break
    return out
