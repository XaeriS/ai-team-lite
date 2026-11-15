"""The master plan across projects: order, what runs next, release, pause and resume, checkpoints, overviews.

Part of the Engine (app/engine.py), kept in its own file so each file has one job (L46)."""
from __future__ import annotations

import logging
import time

from . import auth, checkpoints, iterations, projects, settings
from . import engine_const as const
from .engine_text import launch_info

log = logging.getLogger(__name__)


class PipelineMixin:
    def _priority(self, slug: str) -> int:
        return const.PRIORITY_ORDER.get((projects.load(slug) or {}).get("priority", "normal"), 1)

    def _start_next(self) -> None:
        """Start waiting turns, highest priority first, while slots are free."""
        with self._send_lock:
            limit = max(1, int(settings.load().get("max_parallel") or 2))
            self.waiting.sort(key=lambda w: (self._priority(w["slug"]), w["ts"]))
            starts = []
            for w in list(self.waiting):
                if self.running_count() + len(starts) >= limit:
                    break
                sess = self.session(w["slug"])
                if sess.busy or (projects.load(w["slug"]) or {}).get("paused"):
                    continue
                if any(s["slug"] == w["slug"] for s in starts):
                    continue
                self.waiting.remove(w)
                texts = [x["text"] for x in self.waiting if x["slug"] == w["slug"]]
                self.waiting = [x for x in self.waiting if x["slug"] != w["slug"]]
                sess.busy = True
                starts.append({"slug": w["slug"], "text": "\n\n".join([w["text"], *texts])})
        for s in starts:
            self.hub.publish(s["slug"], {"type": "project_list", "transient": True})
            self._start(s["slug"], s["text"], True)
        self.pipeline_continue()

    def master_order(self) -> list[dict]:
        order = list(settings.load().get("pipeline_order") or [])
        ps = [p for p in projects.list_projects(include_hidden=False)]
        return sorted(ps, key=lambda p: (order.index(p["slug"]) if p["slug"] in order else len(order),
                                         const.PRIORITY_ORDER.get(p.get("priority", "normal"), 1), p.get("created", 0)))

    def _awaits_reply(self, slug: str) -> bool:
        """The lead's last message (after the user's last one) asks the user something."""
        for ev in reversed(self.hub.history(slug)):
            if ev.get("type") == "message":
                return ev.get("role") != "user" and str(ev.get("text", "")).rstrip().endswith("?")
        return False

    def next_work(self, slug: str, p: dict) -> str | None:
        """The hidden message that continues this project's approved plan, or None if it should not continue."""
        if not p.get("plan_approved") or p.get("paused") or p.get("hidden") or p.get("waiting_release") \
                or p.get("pipeline_hold"):
            return None
        its = iterations.load(slug)
        nxt = next((it for it in its if it.get("status") != "done"), None)
        if not nxt:
            return None
        if nxt["status"] == "building":
            return (f"[App] Continue iteration {nxt['n']} ({nxt['name']}) where you stopped: finish it, test it and "
                    "commit_iteration, then go on with the plan.")
        if not iterations.gate_open(slug, nxt["n"]):
            return None
        return (f"[App] The approved plan continues: start iteration {nxt['n']} ({nxt['name']}) now with "
                f"start_iteration({nxt['n']}), then build, test and commit it.")

    def pipeline_continue(self) -> list[str]:
        """Slots are free: continue the next approved iteration of any project, in master-plan order."""
        s = settings.load()
        if not s.get("pipeline_auto", True) or not auth.ready() or self.limit_timers:
            return []
        started = []
        with self._send_lock:
            limit = max(1, int(s.get("max_parallel") or 2))
            for p in self.master_order():
                if self.running_count() >= limit:
                    break
                slug = p["slug"]
                sess = self.sessions.get(slug)
                if (sess and (sess.busy or sess.background or sess.bg_reports)) or self.waiting_user(slug) \
                        or any(w["slug"] == slug for w in self.waiting):
                    continue
                text = self.next_work(slug, p)
                if not text or self._awaits_reply(slug):
                    continue
                n = next((it["n"] for it in iterations.load(slug) if it.get("status") != "done"), None)
                kick = p.get("pipeline_kick") or {}
                if kick.get("n") == n and time.time() - kick.get("t", 0) < const.PIPELINE_KICK_SECONDS:
                    continue
                projects.update(slug, pipeline_kick={"n": n, "t": time.time()})
                self.session(slug).busy = True
                started.append((slug, text))
        for slug, text in started:
            self.hub.publish(slug, {"type": "notice", "text": "Continuing the approved plan (master plan)."})
            self.hub.publish(slug, {"type": "project_list", "transient": True})
            self._start(slug, text, True)
        return [s for s, _ in started]

    def release(self, slug: str, n: int) -> str:
        it = iterations.release(slug, n)
        self.hub.publish(slug, {"type": "notice", "text": f"You released iteration {n}: {it['name']}."})
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        return self.send_message(slug, f"[App] The user released iteration {n} ({it['name']}). Start it now with "
                                       f"start_iteration({n}), then build, test and commit it.", hidden=True)

    def go_back(self, slug: str, n: int) -> dict:
        """L48: put the project back as it was at iteration n (nothing is lost; it can be undone)."""
        self._quiet_for_checkpoint(slug)
        r = checkpoints.go_back(slug, n)
        projects.update(slug, pipeline_hold=True)   # nothing is rebuilt until the user says what they want
        card = self.new_id()
        self.hub.publish(slug, {"type": "wentback", "id": card, "status": "done", "n": n, "name": r["name"],
                                "undone": r["undone"], "kept_unsaved": r["kept_unsaved"]})
        projects.update(slug, went_back={**(projects.load(slug) or {}).get("went_back", {}), "card": card})
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        names = ", ".join(f"{x['n']} ({x['name']})" for x in r["undone"]) or "none"
        self.send_message(slug, f"[App] The user went back to the end of iteration {n} ({r['name']}): the project's "
                          f"files are now exactly as they were then. Iterations planned again: {names}. Their work is "
                          "kept in the Git history, not deleted. What you remember building after that point is no "
                          "longer in the files: read PLAN.md and STATUS.md again before relying on it, and bring "
                          "STATUS.md in line. Do NOT rebuild anything yet: tell the user in two sentences where the "
                          "project stands now and ask what they want to change before the team continues.", hidden=True)
        return r

    def _quiet_for_checkpoint(self, slug: str) -> None:
        """The files must not change under the team's hands: stop what it is doing first (its work so far is kept in
        the safety copy), and wait until it has really stopped."""
        if not self.is_busy(slug):
            return
        self.stop(slug)
        deadline = time.time() + 20
        while self.is_busy(slug) and time.time() < deadline:
            time.sleep(0.1)
        if self.is_busy(slug):
            raise checkpoints.CheckpointError("The team could not be stopped right now. Wait a moment and try again.")

    def undo_go_back(self, slug: str) -> dict:
        self._quiet_for_checkpoint(slug)
        card = ((projects.load(slug) or {}).get("went_back") or {}).get("card")
        r = checkpoints.undo_go_back(slug)
        if card:
            self.hub.update(slug, card, status="undone")
        self.hub.publish(slug, {"type": "notice", "text": f"Going back to iteration {r['to']} was undone: everything is "
                                                          "as it was before."})
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        self.send_message(slug, "[App] The user undid going back: the project's files and the plan are again as they "
                          "were before (all finished iterations are back). Read STATUS.md and PLAN.md, then tell the "
                          "user in one sentence that everything is back and ask what they want next.", hidden=True)
        return r

    def pipeline_overview(self) -> dict:
        rows = []
        for p in self.master_order():
            slug = p["slug"]
            its = iterations.load(slug)
            if not its:
                continue
            state = self.project_state(slug, p)
            sess = self.sessions.get(slug)
            busy = [r for r in (sess.team_snapshot() if sess else []) if r["status"] in ("working", "question")]
            items = []
            for it in its:
                st = it.get("status", "planned")
                if st == "done":
                    label = "done"
                elif not p.get("plan_approved"):
                    label = "not approved yet"
                elif st == "building":
                    label = "waiting for your answer" if self.waiting_user(slug) else (
                        "building" if state == "working" else "stopped mid-way")
                elif p.get("waiting_release") == it["n"]:
                    label = "waiting for release"
                else:
                    label = "queued"
                items.append({"n": it["n"], "name": it["name"], "status": label, "after": it.get("after", "continue"),
                              "commit": it.get("commit", ""), "requirements": it.get("requirements") or [],
                              "who": [{"name": r["name"], "task": r.get("task", "")} for r in busy] if st == "building" else []})
            rows.append({"slug": slug, "name": p["name"], "state": state, "priority": p.get("priority", "normal"),
                         "approved": bool(p.get("plan_approved")), "waiting_release": p.get("waiting_release"),
                         "launch": launch_info(slug, p), "iterations": items})
        return {"projects": rows, "running": self.running_count(),
                "max_parallel": int(settings.load().get("max_parallel") or 2),
                "auto": bool(settings.load().get("pipeline_auto", True))}

    def pause(self, slug: str) -> None:
        projects.update(slug, paused=True)
        self.cancel_auto_continue(slug, note="paused")
        with self._send_lock:
            held = [w["text"] for w in self.waiting if w["slug"] == slug]
            self.waiting = [w for w in self.waiting if w["slug"] != slug]
            if held:
                self.queued.setdefault(slug, []).extend(held)
        self._interrupt(slug)
        self.hub.publish(slug, {"type": "notice", "text": "Paused. Resume it from the Work queue or the header."})
        self.hub.publish(slug, {"type": "project_list", "transient": True})

    def resume(self, slug: str, restart: bool = False) -> str:
        projects.update(slug, paused=False, pipeline_hold=False)
        with self._send_lock:
            held = self.queued.pop(slug, [])
        note = ("[App] The user restarted the work: start the current step again from the beginning."
                if restart else "[App] The user resumed the work. Continue exactly where you stopped.")
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        return self.send_message(slug, "\n\n".join([note, *held]), hidden=True)

    def set_priority(self, slug: str, priority: str) -> None:
        if priority not in const.PRIORITY_ORDER:
            raise ValueError("priority must be high, normal or low")
        projects.update(slug, priority=priority)
        self.hub.publish(slug, {"type": "project_list", "transient": True})
        self._start_next()

    def project_state(self, slug: str, project: dict | None = None) -> str:
        """working | needs you | paused | limit | waiting | idle (nothing is progressing)."""
        p = project if project is not None else (projects.load(slug) or {})
        sess = self.sessions.get(slug)
        if p.get("paused"):
            return "paused"
        if slug in self.limit_timers:
            return "limit"
        if any(s == slug for (_f, s, _k) in list(self.pending.values())):
            return "needs you"
        if sess and (sess.busy or sess.background):
            return "working"
        if any(w["slug"] == slug for w in self.waiting):
            return "waiting"
        if p.get("waiting_release"):
            return "release"
        return "idle"

    def work_overview(self) -> list[dict]:
        rows = []
        for p in projects.list_projects(include_hidden=False):
            slug = p["slug"]
            sess = self.sessions.get(slug)
            pending = [k for (_f, s, k) in self.pending.values() if s == slug]
            state = self.project_state(slug, p)
            team = sess.team_snapshot() if sess else []
            busy_roles = [r for r in team if r["status"] in ("working", "question")]
            timer = self.limit_timers.get(slug)
            prog = iterations.progress(slug)
            rows.append({"slug": slug, "name": p["name"], "phase": p.get("phase", ""), "state": state, "progress": prog,
                         "priority": p.get("priority", "normal"), "since": sess.turn_started if sess else None,
                         "who": [{"name": r["name"], "status": r["status"], "task": r.get("task", ""),
                                  "last": r.get("last", "")} for r in busy_roles],
                         "pending": pending, "queued": len(self.queued.get(slug, [])),
                         "continue_at": timer[2] if timer and timer[2] else None})
        order = {"needs you": 0, "working": 1, "limit": 2, "waiting": 3, "release": 4, "paused": 5, "idle": 6}
        return sorted(rows, key=lambda r: (order[r["state"]], const.PRIORITY_ORDER.get(r["priority"], 1)))
