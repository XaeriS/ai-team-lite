"""Event hub: everything the chat shows is an event. Events are pushed live to the window (SSE) and the
ones that matter are saved in the project's history so the chat comes back after a restart."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

TRANSIENT = {"busy", "activity_live", "docs_changed", "project_list"}  # not saved to history


class EventHub:
    def __init__(self, history_file: Callable[[str], Path]):
        self._history_file = history_file
        self._cond = threading.Condition()
        self._events: list[dict] = []
        self._seq = 0

    def publish(self, slug: str, event: dict) -> dict:
        with self._cond:
            self._seq += 1
            ev = {**event, "slug": slug, "seq": self._seq, "ts": time.time()}
            self._events.append(ev)
            del self._events[:-2000]
            if ev["type"] not in TRANSIENT and not ev.get("transient"):
                try:
                    f = self._history_file(slug)
                except ValueError:
                    f = None
                if f is not None and f.parent.parent.exists():  # never re-create a deleted project folder
                    f.parent.mkdir(exist_ok=True)
                    with open(f, "a", encoding="utf-8") as h:
                        h.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self._cond.notify_all()
            return ev

    def update(self, slug: str, item_id: str, **changes) -> dict:
        """Change a card already in the chat (e.g. an approval going from pending to approved)."""
        return self.publish(slug, {"type": "update", "id": item_id, "changes": changes})

    def wait(self, since: int, timeout: float = 15.0) -> list[dict]:
        with self._cond:
            if self._seq <= since:
                self._cond.wait(timeout)
            return [e for e in self._events if e["seq"] > since]

    @property
    def seq(self) -> int:
        return self._seq

    def history(self, slug: str) -> list[dict]:
        """Saved events with updates folded in. Cards still pending from a previous run become 'expired'."""
        f = self._history_file(slug)
        items: list[dict] = []
        by_id: dict[str, dict] = {}
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "update":
                    target = by_id.get(ev.get("id"))
                    if target:
                        target.update(ev.get("changes") or {})
                    continue
                items.append(ev)
                if ev.get("id"):
                    by_id[ev["id"]] = ev
        return items
