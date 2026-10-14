"""Requested features and feedback (items 50, 51).

Every request - typed by you, sent through the in-app feedback button, or imported from the shared repo - is a
row here with a status you control: new -> approved (goes to the build queue / council) or rejected (with a reason).
Stored in the data folder, so nothing here is ever part of the published app.
"""
from __future__ import annotations

import json
import time
import uuid

from . import paths, settings

STATUSES = ("new", "approved", "rejected", "planned", "built")


def _file():
    return paths.data_dir() / "feature-requests.json"


def load() -> list[dict]:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    _file().write_text(json.dumps(items, indent=2), encoding="utf-8")


def add(title: str, body: str = "", source: str = "me", author: str = "", ref: str = "") -> dict:
    """source: me | feedback | imported. ref: e.g. github issue number, to avoid importing twice."""
    items = load()
    if ref and any(i.get("ref") == ref for i in items):
        return next(i for i in items if i.get("ref") == ref)
    item = {"id": uuid.uuid4().hex[:12], "title": " ".join(str(title).split())[:200] or "Untitled",
            "body": str(body).strip()[:8000], "source": source, "author": author[:80], "ref": ref,
            "status": "new", "reason": "", "created": time.time(), "updated": time.time()}
    items.insert(0, item)
    _save(items)
    return item


def set_status(item_id: str, status: str, reason: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError("Unknown status.")
    items = load()
    for it in items:
        if it["id"] == item_id:
            it["status"] = status
            it["updated"] = time.time()
            if status == "rejected":
                it["reason"] = str(reason).strip()[:1000]
            _save(items)
            return it
    raise ValueError("No such request.")


def summary() -> dict:
    items = load()
    by = {s: [i for i in items if i["status"] == s] for s in STATUSES}
    return {"items": items, "counts": {s: len(v) for s, v in by.items()}}


def submit_feedback(title: str, body: str) -> dict:
    """The in-app feedback button. Always recorded locally; if a shared repo + GitHub are available it is also
    filed there so the owner receives it. Returns {"local", "shared"(bool), "url", "channel"}."""
    from . import github
    item = add(title, body, source="feedback", author=settings.load().get("user_name", ""))
    repo = settings.load().get("app_repo") or ""
    if repo:
        full = github.repo_full_name(repo)
        try:
            issue = github.create_issue(full, title, body + "\n\n— sent from AI Team Lite feedback")
            item["ref"] = f"issue-{issue['number']}"
            items = load()
            for it in items:
                if it["id"] == item["id"]:
                    it["ref"] = item["ref"]
            _save(items)
            return {"local": item, "shared": True, "url": issue["url"], "channel": "github"}
        except github.GitHubError as e:
            return {"local": item, "shared": False, "channel": "local", "error": str(e)}
    return {"local": item, "shared": False, "channel": "local"}


def import_feedback() -> int:
    """Owner only: pull feedback issues from the shared repo into the queue. Returns how many were new."""
    from . import github
    repo = settings.load().get("app_repo") or ""
    if not repo or not settings.load().get("is_owner"):
        return 0
    full = github.repo_full_name(repo)
    added = 0
    for iss in github.list_issues(full):
        ref = f"issue-{iss['number']}"
        if not any(i.get("ref") == ref for i in load()):
            add(iss["title"], iss["body"], source="imported", author=iss["author"], ref=ref)
            added += 1
    return added
