"""Change tracking and progress (item 34).

- Every change to REQUIREMENTS.md, ARCHITECTURE.md, PLAN.md and STATUS.md is saved as a numbered version with a reason
  (the user's message that led to it) in .aiteam/history/<doc>/, and committed to Git on its own ("docs:" commit).
- Approved versions are marked (plan approval -> requirements + plan; architecture approval -> architecture).
- Each requirement (REQ-n) gets a status computed from the documents, the iterations and the test results.
- Change requests after approval are recorded as CR-n with their impact and need the user's approval.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from . import iterations, paths, projects, tools

DOCS = {"requirements": ("REQUIREMENTS.md", "Requirements"), "architecture": ("ARCHITECTURE.md", "Architecture"),
        "plan": ("PLAN.md", "Plan"), "status": ("STATUS.md", "Status")}
REQ_LINE = re.compile(r"\b(REQ-\d+)\b\**[\s:.)\-–]*(.*)")


def _hist(slug: str, doc: str) -> Path:
    return projects.project_dir(slug) / projects.META / "history" / doc


def versions(slug: str, doc: str) -> list[dict]:
    try:
        return json.loads((_hist(slug, doc) / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_index(slug: str, doc: str, items: list[dict]) -> None:
    d = _hist(slug, doc)
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text(json.dumps(items, indent=1), encoding="utf-8")


def text_of(slug: str, doc: str, v: int) -> str:
    try:
        return (_hist(slug, doc) / f"v{v}.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def snapshot(slug: str, reason: str, seq: int | None = None, git: bool = True) -> list[dict]:
    """Save a new version of every document that changed since its last version. Returns the new versions."""
    folder = projects.project_dir(slug)
    new = []
    for doc, (fname, label) in DOCS.items():
        f = folder / fname
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        sha = hashlib.sha256(text.encode()).hexdigest()[:16]
        items = versions(slug, doc)
        if items and items[-1]["sha"] == sha:
            continue
        v = len(items) + 1
        _hist(slug, doc).mkdir(parents=True, exist_ok=True)
        (_hist(slug, doc) / f"v{v}.md").write_text(text, encoding="utf-8")
        entry = {"v": v, "time": time.time(), "reason": " ".join(reason.split())[:300], "seq": seq, "sha": sha,
                 "approved": False, "lines": text.count("\n") + 1}
        items.append(entry)
        _save_index(slug, doc, items)
        new.append({"doc": doc, "label": label, "file": fname, **entry})
    if new and git:
        _commit_docs(folder, new)
    return new


def _commit_docs(folder: Path, new: list[dict]) -> None:
    if not paths.git_exe():
        return
    try:
        if not (folder / ".git").exists():
            iterations._git(folder, "init", "-b", "main")
        projects.exclude_meta(folder)
        files = [n["file"] for n in new]
        iterations._git(folder, "add", "--", *files)
        msg = "docs: " + ", ".join(f"{n['label']} v{n['v']}" for n in new) + f"\n\nReason: {new[0]['reason']}"
        iterations._git(folder, "commit", "-m", msg, "--", *files)
    except Exception:  # noqa: BLE001  (history in .aiteam is kept even when Git fails)
        pass


def mark_approved(slug: str, doc: str, by: str = "you") -> dict | None:
    items = versions(slug, doc)
    if not items:
        return None
    items[-1].update(approved=True, approved_at=time.time(), approved_by=by)
    _save_index(slug, doc, items)
    return items[-1]


def diff(slug: str, doc: str, a: int, b: int) -> list[dict]:
    old = text_of(slug, doc, a).splitlines() if a else []
    new = text_of(slug, doc, b).splitlines()
    out = []
    for line in difflib.ndiff(old, new):
        tag = line[:1]
        if tag in "+- ":
            out.append({"t": {"+": "add", "-": "del", " ": "same"}[tag], "text": line[2:]})
    return out


def history(slug: str) -> dict:
    return {doc: {"label": DOCS[doc][1], "versions": versions(slug, doc)} for doc in DOCS}


# ---------------------------------------------------------------------------- requirements status
def parse_requirements(text: str) -> dict[str, str]:
    reqs: dict[str, str] = {}
    for line in text.splitlines():
        m = REQ_LINE.search(line)
        if m and m.group(1) not in reqs:
            reqs[m.group(1)] = re.sub(r"[*_`]", "", m.group(2)).strip()[:200]
    return reqs


def traceability(slug: str) -> list[dict]:
    folder = projects.project_dir(slug)
    current = parse_requirements(projects.read_doc(slug, "REQUIREMENTS.md"))
    vs = versions(slug, "requirements")
    approved_v = next((v for v in reversed(vs) if v.get("approved")), None)
    approved = parse_requirements(text_of(slug, "requirements", approved_v["v"])) if approved_v else {}
    ever: dict[str, str] = {}
    for v in vs:
        for k, t in parse_requirements(text_of(slug, "requirements", v["v"])).items():
            ever.setdefault(k, t)
    its = iterations.load(slug)
    tests = tools.load_tests(folder)
    by_req = defaultdict(lambda: {"passed": 0, "failed": 0})
    latest: dict[tuple, str] = {}  # (requirement, test name) -> latest status: a test fixed later counts as passing
    for t in tests:
        if t.get("status") not in ("passed", "failed"):
            continue
        for r in sorted(set(re.findall(r"REQ-\d+", str(t.get("requirement") or "").upper()))):
            by_req[r][t["status"]] += 1
            latest[(r, t.get("name") or "")] = t["status"]
    proven = defaultdict(lambda: {"passing": 0, "failing": 0})
    for (r, _name), st in latest.items():
        proven[r]["passing" if st == "passed" else "failing"] += 1
    rows = []
    for rid in sorted(set(current) | set(ever), key=lambda x: int(x.split("-")[1])):
        it = next((i for i in its if rid in (i.get("requirements") or [])), None)
        row = {"id": rid, "text": current.get(rid, ever.get(rid, "")), "iteration": it["n"] if it else None,
               "iteration_name": it["name"] if it else "", "commit": it.get("commit", "") if it else "",
               "tests": dict(by_req[rid]), "proven": bool(proven[rid]["passing"]) and not proven[rid]["failing"],
               "proof": dict(proven[rid])}
        if rid not in current:
            status = "dropped"
        elif approved_v and rid not in approved:
            status = "proposed"
        elif not approved_v:
            status = "proposed"
        elif approved.get(rid) != current[rid] and not it:
            status = "changed"
        elif it and it.get("status") == "done":
            status = "tested" if row["proven"] else "built"
        elif it and it.get("status") == "building":
            status = "building"
        elif it:
            status = "planned"
        else:
            status = "approved"
        if approved_v and rid in approved and rid in current and approved[rid] != current[rid]:
            row["changed_since_approval"] = True
        row["status"] = status
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------- change requests
def change_requests(slug: str) -> list[dict]:
    return list((projects.load(slug) or {}).get("change_requests") or [])


def add_change_request(slug: str, data: dict) -> dict:
    crs = change_requests(slug)
    cr = {"id": f"CR-{len(crs) + 1}", "status": "pending", "created": time.time(),
          **{k: data.get(k) for k in ("title", "description", "affects_requirements", "affects_decisions",
                                      "affects_iterations", "effort")}}
    crs.append(cr)
    projects.update(slug, change_requests=crs)
    return cr


def decide_change_request(slug: str, cr_id: str, approved: bool, note: str = "") -> dict | None:
    crs = change_requests(slug)
    for cr in crs:
        if cr["id"] == cr_id:
            cr.update(status="approved" if approved else "rejected", decided=time.time(), note=note)
            projects.update(slug, change_requests=crs)
            return cr
    return None


# ---------------------------------------------------------------------------- progress
def progress(slug: str) -> dict:
    rows = traceability(slug)
    live = [r for r in rows if r["status"] != "dropped"]
    done = [r for r in live if r["status"] in ("built", "tested")]
    its = iterations.load(slug)
    tests = tools.load_tests(projects.project_dir(slug))
    days: dict[str, dict] = defaultdict(lambda: {"passed": 0, "failed": 0})
    for t in tests:
        if t.get("status") in ("passed", "failed") and t.get("time"):
            days[time.strftime("%Y-%m-%d", time.localtime(t["time"]))][t["status"]] += 1
    last = {}
    for doc in DOCS:
        vs = versions(slug, doc)
        if vs:
            last[doc] = {"v": vs[-1]["v"], "time": vs[-1]["time"], "approved": vs[-1].get("approved", False)}
    return {"requirements": {"total": len(live), "done": len(done),
                             "percent": round(100 * len(done) / len(live)) if live else 0},
            "iterations": {"total": len(its), "done": sum(1 for i in its if i.get("status") == "done")},
            "tests_by_day": [{"day": k, **v} for k, v in sorted(days.items())][-14:],
            "documents": last, "open_change_requests": sum(1 for c in change_requests(slug) if c["status"] == "pending")}
