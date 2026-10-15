"""Usage in plain words (L6, L9, L13, L14, L15).

Everything here is computed from what the app already records: one row per model call (tokens, model, who,
iteration, phase, stage) and one span per turn / worker run / wait for the user. Costs are worked out at the
providers' list prices, so the same figures can be shown for an API key ("cost") and for a Claude subscription
("would cost at API prices, not billed").
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import iterations, models, projects, timeline, usage

UNITS = ("day", "week", "month", "year", "all")
UNIT_COUNT = {"day": 14, "week": 12, "month": 12}


def _rows(slug: str | None) -> list[dict]:
    return usage.project_rows(slug) if slug else usage.all_rows()


def _priced(r: dict) -> tuple[int, float]:
    tok = usage.tokens(r)
    if not tok and not r.get("cache_read"):
        return 0, 0.0
    return tok, usage.est_cost(r)


def _stage(r: dict) -> str:
    return r.get("stage") or timeline.stage_for(r.get("who", ""), r.get("phase", ""), project=r.get("project", ""))


def _bucket():
    return {"tokens": 0, "cost": 0.0}


def _add(b: dict, tok: int, cost: float) -> None:
    b["tokens"] += tok
    b["cost"] += cost


def _clean(b: dict) -> dict:
    return {**b, "cost": round(b["cost"], 4)}


def _split(rows: list[dict]) -> dict:
    agents: dict[str, dict] = defaultdict(_bucket)
    stages: dict[str, dict] = defaultdict(_bucket)
    for r, tok, cost in rows:
        _add(agents[timeline.agent_group(r.get("who", ""))], tok, cost)
        _add(stages[_stage(r)], tok, cost)
    return {
        "by_agent": [{"name": k, **_clean(v)} for k, v in sorted(agents.items(), key=lambda kv: -kv[1]["tokens"])],
        "by_stage": [{"stage": k, "label": timeline.STAGE_LABELS.get(k, k), **_clean(v)}
                     for k, v in sorted(stages.items(), key=lambda kv: timeline.STAGE_ORDER.get(kv[0], 99))],
    }


# ---------------------------------------------------------------------------- periods (L9)
def _period_start(d: date, unit: str) -> date:
    if unit == "day":
        return d
    if unit == "week":
        return d - timedelta(days=d.weekday())
    if unit == "month":
        return d.replace(day=1)
    if unit == "year":
        return d.replace(month=1, day=1)
    return date(1970, 1, 1)


def _prev(d: date, unit: str) -> date:
    if unit == "day":
        return d - timedelta(days=1)
    if unit == "week":
        return d - timedelta(days=7)
    if unit == "month":
        return (d - timedelta(days=1)).replace(day=1)
    return d.replace(year=d.year - 1)


def _period_label(d: date, unit: str, today: date) -> str:
    if unit == "day":
        return "Today" if d == today else "Yesterday" if d == today - timedelta(days=1) else d.strftime("%a %d %b")
    if unit == "week":
        return "This week" if d == _period_start(today, "week") else "Week of " + d.strftime("%d %b")
    if unit == "month":
        return "This month" if d == _period_start(today, "month") else d.strftime("%B %Y")
    if unit == "year":
        return str(d.year)
    return "All time"


def periods(rows: list[tuple[dict, int, float]], unit: str, today: date | None = None) -> list[dict]:
    """Tokens and cost for each day / week / month / year (newest first), each with its split by agent and phase."""
    today = today or date.today()
    unit = unit if unit in UNITS else "week"
    groups: dict[date, list] = defaultdict(list)
    first = today
    for r, tok, cost in rows:
        try:
            d = date.fromisoformat(r.get("day") or datetime.fromtimestamp(r.get("t", 0)).date().isoformat())
        except ValueError:
            continue
        first = min(first, d)
        groups[_period_start(d, unit)].append((r, tok, cost))
    if unit == "all":
        starts = [_period_start(today, "all")]
    elif unit == "year":
        starts, y = [], _period_start(today, "year")
        while y >= _period_start(first, "year"):
            starts.append(y)
            y = _prev(y, "year")
    else:
        starts, cur = [], _period_start(today, unit)
        for _ in range(UNIT_COUNT[unit]):
            starts.append(cur)
            if cur <= _period_start(first, unit):
                break
            cur = _prev(cur, unit)
    out = []
    for s in starts:
        items = groups.get(s, [])
        tot = _bucket()
        for _r, tok, cost in items:
            _add(tot, tok, cost)
        out.append({"start": s.isoformat(), "label": _period_label(s, unit, today), **_clean(tot), **_split(items)})
    return out


# ---------------------------------------------------------------------------- live activity (L9)
def activity(slug: str | None, now: float | None = None) -> dict:
    """Tokens per minute for the last 60 minutes and per hour for the last 24 hours."""
    now = now or time.time()
    minutes, hours = [0] * 60, [0] * 24
    for r in _rows(slug):
        t = r.get("t") or 0
        age = now - t
        if age < 0 or age >= 86400:
            continue
        tok = usage.tokens(r)
        if age < 3600:
            minutes[59 - int(age // 60)] += tok
        hours[23 - int(age // 3600)] += tok
    flat = 0
    for v in reversed(minutes):
        if v:
            break
        flat += 1
    return {"minutes": minutes, "hours": hours, "flat_minutes": flat, "now": now}


# ---------------------------------------------------------------------------- iterations (L6, L13, L14)
def iteration_table(slug: str, rows: list[tuple[dict, int, float]]) -> list[dict]:
    its = iterations.load(slug)
    groups: dict = defaultdict(list)
    for item in rows:
        groups[item[0].get("iteration") or 0].append(item)
    out = []
    for it in its:
        items = groups.pop(it["n"], [])
        tot = _bucket()
        for _r, tok, cost in items:
            _add(tot, tok, cost)
        t = timeline.time_by_stage(slug, it["n"])
        out.append({"n": it["n"], "name": it.get("name", ""), "status": it.get("status", "planned"), **_clean(tot),
                    "work": t["work"], "wait": t["wait"], "time_stages": t["stages"], **_split(items)})
    before = groups.pop(0, [])
    for rest in groups.values():   # rows of iterations that no longer exist (plan changed)
        before += rest
    if before:
        tot = _bucket()
        for _r, tok, cost in before:
            _add(tot, tok, cost)
        out.insert(0, {"n": 0, "name": "Before the iterations (problem, requirements, architecture, plan)",
                       "status": "done", **_clean(tot), "work": 0, "wait": 0, "time_stages": [], **_split(before)})
    return out


# ---------------------------------------------------------------------------- plan headroom (L15)
def headroom() -> list[dict]:
    """How much of the Claude plan is left, from Claude's own /usage figures when known (never estimated)."""
    out = []
    data = usage.plan()
    for kind, label in (("five_hour", "5-hour limit"), ("seven_day", "Weekly limit")):
        v = data.get(kind) or {}
        pct = v.get("percent")
        out.append({"kind": kind, "label": label, "percent": pct, "left": None if pct is None else max(0.0, 100 - pct),
                    "resets_at": v.get("resets_at"), "reset_text": v.get("reset_text", ""),
                    "from_claude": v.get("source") == "claude_usage", "at": v.get("percent_at"),
                    "status": v.get("status", "")})
    return out


def headroom_warning() -> str:
    """One plain sentence when the room left looks too small for a big step, else ''."""
    for h in headroom():
        if h["percent"] is None:
            continue
        if (h["kind"] == "five_hour" and h["percent"] >= 80) or (h["kind"] == "seven_day" and h["percent"] >= 90):
            when = h["reset_text"] or (datetime.fromtimestamp(h["resets_at"]).strftime("%H:%M") if h["resets_at"] else "")
            return (f"About {h['left']:.0f}% of your {h['label'].lower()} is left{f' (resets {when})' if when else ''}. "
                    "This step may stop at the limit; it then continues by itself after the reset.")
    return ""


# ---------------------------------------------------------------------------- the whole picture
def report(slug: str | None, unit: str = "week") -> dict:
    """Everything the Usage window shows, for one project or for all projects."""
    raw = _rows(slug)
    rows = [(r, *_priced(r)) for r in raw if r.get("who") != "total"]
    rows = [x for x in rows if x[1] or x[2]]
    tot = _bucket()
    for _r, tok, cost in rows:
        _add(tot, tok, cost)
    today = date.today()
    month = [x for x in rows if (x[0].get("day") or "") >= today.replace(day=1).isoformat()]
    mtot = _bucket()
    for _r, tok, cost in month:
        _add(mtot, tok, cost)
    out = {"unit": unit if unit in UNITS else "week", "total": _clean(tot), "month": _clean(mtot),
           "periods": periods(rows, unit, today), "split": _split(rows), "activity": activity(slug),
           "time": timeline.time_by_stage(slug), "headroom": headroom(), "prices_checked": models.PRICES_CHECKED}
    if slug:
        p = projects.load(slug) or {}
        its = iterations.load(slug)
        out["project"] = {"slug": slug, "name": p.get("name", slug)}
        out["iterations"] = iteration_table(slug, rows)
        out["done"] = sum(1 for it in its if it.get("status") == "done")
        out["planned"] = len(its)
        out["estimate"] = iterations.cost_estimate(slug)
    else:
        names = {p["slug"]: p["name"] for p in projects.list_projects()}
        names[projects.SETUP_SLUG] = "Setup"
        per: dict[str, dict] = defaultdict(_bucket)
        for r, tok, cost in rows:
            _add(per[r.get("project") or "?"], tok, cost)
        out["projects"] = [{"slug": k, "name": names.get(k, k), **_clean(v)}
                           for k, v in sorted(per.items(), key=lambda kv: -kv[1]["tokens"])]
    return out
