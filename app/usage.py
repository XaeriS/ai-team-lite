"""Consumption tracking.

- Plan usage (Claude subscription): the Claude engine sends rate-limit updates with the 5-hour session and
  weekly utilization and reset times. The latest of each is kept in usage.json.
- Tokens and API-price cost per reply are appended to usage-log.jsonl (one line per model call or turn);
  analytics.py turns them into the Usage window (per period, agent, phase and iteration).
- Each line also records the project's iteration and phase, and is copied into a ledger INSIDE the project
  folder (.aiteam/usage.jsonl), so a project keeps its full token history by model and iteration even if it is
  moved to another folder or another PC (item 57).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import ledger, models, paths

LIMIT_LABELS = {"five_hour": "Current session (5 hours)", "seven_day": "This week", "seven_day_sonnet": "This week (Sonnet only)",
                "seven_day_opus": "This week (Opus)", "overage": "Extra usage"}
_lock = threading.Lock()


def _plan_file() -> Path:
    return paths.data_dir() / "usage.json"


def _log_file() -> Path:
    return paths.data_dir() / "usage-log.jsonl"


def _percent(v) -> float | None:
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v * 100 if v <= 1.0 else v, 1)


def _epoch(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def record_limit(info) -> dict:
    """Store a RateLimitInfo. Returns the stored entry."""
    kind = getattr(info, "rate_limit_type", None) or "five_hour"
    entry = {"type": kind, "label": LIMIT_LABELS.get(kind, kind), "status": getattr(info, "status", ""),
             "percent": _percent(getattr(info, "utilization", None)),
             "resets_at": _epoch(getattr(info, "resets_at", None)), "seen": time.time()}
    with _lock:
        data = plan()
        old = data.get(kind) or {}
        if entry["percent"] is None and old.get("source") == "claude_usage" and entry["seen"] - (old.get("percent_at") or 0) < 900:
            # a real figure from Claude's usage command is fresher than a status report without a percentage, but the
            # report's exact reset time and a "limit reached" status are kept: they drive the automatic restart (L25)
            merged = {**old, "seen": entry["seen"]}
            if entry["resets_at"]:
                merged["resets_at"] = entry["resets_at"]
            if entry["status"] == "rejected":
                merged.update(status="rejected", percent=max(float(old.get("percent") or 0), 100.0))
            data[kind] = merged
            _plan_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
            return merged
        same_window = not old.get("resets_at") or not entry["resets_at"] or abs(old["resets_at"] - entry["resets_at"]) < 120
        if not old.get("resets_at") and entry["resets_at"] and old.get("percent_at"):
            # the old figure had no reset time: it belongs to this window only if it was taken after the window
            # began (e.g. a 98% from before the weekly reset must not survive it - item 73)
            same_window = old["percent_at"] >= entry["resets_at"] - _WINDOW.get(kind, 7 * 86400)
        recovered = entry["status"] == "allowed" and old.get("status") in ("allowed_warning", "rejected")
        if entry["percent"] is None and recovered:
            # Claude only reports on status changes. "Back to normal" after a warning means the usage went down
            # (a reset, early reset or extra usage): the old high % is no longer true (item 61).
            entry["percent"], entry["below_warning"] = None, True
            entry["resets_at"] = entry["resets_at"] or old.get("resets_at")
        elif entry["percent"] is None:
            # Most updates do not carry the percentage: keep the last known one for the same window (bug #13)
            if same_window and old.get("percent") is not None:
                entry["percent"], entry["percent_at"] = old["percent"], old.get("percent_at", old.get("seen"))
            entry["resets_at"] = entry["resets_at"] or old.get("resets_at")
        else:
            entry["percent_at"] = entry["seen"]
        if entry["status"] == "rejected" and entry["percent"] is None:
            entry["percent"], entry["percent_at"] = 100.0, entry["seen"]
        data[kind] = entry
        _plan_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return entry


# ---------------------------------------------------------------------------- real figures from Claude (item 69)
# Claude's own tool has a non-interactive "/usage" command that prints the same figures as its usage page, e.g.
#   Current session: 46% used · resets Oct 12 at 7:30pm
#   Current week (all models): 8% used · resets Oct 16 at 8pm
# It runs locally with Claude's own sign-in (AI Team Lite never reads it) and calls no model, so it costs no tokens.
_USAGE_LINE = re.compile(r"^\s*(Current [^:\n]+):\s*(\d+(?:\.\d+)?)%\s*used(?:\s*[\u00b7·|-]\s*resets?\s*(.+?))?\s*$",
                         re.M | re.I)
_TITLE_KIND = {"current session": "five_hour", "current week (all models)": "seven_day",
               "current week (sonnet only)": "seven_day_sonnet"}
_WINDOW = {"five_hour": 5 * 3600, "seven_day": 7 * 86400, "seven_day_sonnet": 7 * 86400}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def parse_usage_text(text: str) -> dict:
    out = {}
    for title, pct, reset in _USAGE_LINE.findall(text or ""):
        kind = _TITLE_KIND.get(title.strip().lower()) or "model:" + re.sub(r"\W+", "_", title.strip().lower())
        out[kind] = {"label": title.strip(), "percent": round(float(pct), 1), "reset_text": reset.strip()}
    return out


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                          "dec"), 1)}
_DAYS = {d: i for i, d in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}
_RESET = re.compile(r"^(?:(?P<mon>[a-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?:at\s+)?)?"
                    r"(?:(?P<wd>[a-z]{3})[a-z]*\.?,?\s+(?:at\s+)?)?"
                    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?", re.I)


def reset_time(text: str, now: float | None = None) -> float | None:
    """'7:30pm', 'Fri 8pm', 'Oct 16 at 8pm' (local time, as Claude's /usage prints it) -> the next such moment."""
    m = _RESET.match(re.sub(r"^\s*resets?\s+", "", (text or "").strip(), flags=re.I))
    if not m:
        return None
    h, mi = int(m.group("h")), int(m.group("m") or 0)
    ap = (m.group("ap") or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    base = datetime.fromtimestamp(now or time.time())
    try:
        if m.group("mon") and m.group("mon").lower() in _MONTHS:
            cand = base.replace(month=_MONTHS[m.group("mon").lower()], day=int(m.group("day")), hour=h, minute=mi,
                                second=0, microsecond=0)
            if cand < base - timedelta(days=180):
                cand = cand.replace(year=cand.year + 1)
            return cand.timestamp()
        cand = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        if m.group("wd") and m.group("wd").lower() in _DAYS:
            cand += timedelta(days=(_DAYS[m.group("wd").lower()] - cand.weekday()) % 7)
        if cand <= base - timedelta(minutes=1):
            cand += timedelta(days=7 if m.group("wd") else 1)
        return cand.timestamp()
    except ValueError:
        return None


_last_failed = {"t": 0.0}


def fetch_plan(timeout: float = 40, force: bool = False) -> dict | None:
    """Ask Claude's own tool for the real plan usage. Returns the parsed figures, or None (not signed in with a
    subscription, engine missing, or the figures were not in the answer - the older reports are then used)."""
    from . import auth
    exe = auth.cli_path()
    if not exe or auth.mode() != "subscription":
        return None
    if not force and time.time() - _last_failed["t"] < 120:
        return None  # it just failed: do not slow every message down by trying again at once
    env = {**os.environ, **auth.cli_env("subscription")}
    env.pop("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", None)  # the usage figures must be allowed to load
    try:
        r = subprocess.run([str(exe), "-p", "/usage", "--no-session-persistence"], cwd=str(paths.data_dir()),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env=env, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        _last_failed["t"] = time.time()
        return None
    parsed = parse_usage_text(r.stdout)
    if not parsed:
        _last_failed["t"] = time.time()
        return None
    now = time.time()
    with _lock:
        try:
            data = json.loads(_plan_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for kind, v in parsed.items():
            old = data.get(kind) or {}
            status = "rejected" if v["percent"] >= 100 else "allowed_warning" if v["percent"] >= 80 else "allowed"
            data[kind] = {"type": kind, "label": LIMIT_LABELS.get(kind, v["label"]), "status": status,
                          "percent": v["percent"], "percent_at": now, "seen": now, "source": "claude_usage",
                          "reset_text": v["reset_text"],
                          "resets_at": reset_time(v["reset_text"], now) or (old.get("resets_at") if old.get("source") == "claude_usage" else None)}
        _plan_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    return parsed


def plan() -> dict:
    try:
        data = json.loads(_plan_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    now = time.time()
    for kind, v in data.items():
        if not isinstance(v, dict):
            continue
        if v.get("resets_at") and v["resets_at"] < now:  # a window that has really reset is back to 0
            v.update(percent=0.0, percent_at=v["resets_at"], status="allowed", resets_at=None)
        # never show a figure older than its own window: it may be from before the last reset (item 69)
        age = now - (v.get("percent_at") or v.get("seen") or now)
        if v.get("percent") is not None and age > _WINDOW.get(kind, 7 * 86400):
            v.update(percent=None, status="allowed", stale=True, reset_text="")
    for kind in ("five_hour", "seven_day"):   # always show both windows (item 62), even before any report
        data.setdefault(kind, {"type": kind, "label": LIMIT_LABELS[kind], "status": "allowed", "percent": None,
                               "resets_at": None, "below_warning": True, "no_report": True})
    return data


def _ledger(project: str, create: bool = False) -> Path | None:
    """The project's own usage ledger (inside its folder), or None for the setup chat / unknown projects."""
    return ledger.project_file(project, "usage.jsonl", create)


def _stage(project: str, who: str = "") -> dict:
    """Which iteration, phase and stage (problem, build, test...) the project is in right now, for the ledger."""
    try:
        from . import projects, timeline
        p = projects.load(project) or {}
        return {**timeline.project_stage(project, who), "project_name": p.get("name", "")}
    except Exception:  # noqa: BLE001
        return {"iteration": None, "phase": ""}


def _backfill(project: str, path: Path) -> None:
    """First time a project gets its ledger: copy its earlier rows from the app's log, matching each to the
    iteration that was running then (by the iterations' start and finish times)."""
    try:
        from . import iterations
        spans = [(it["n"], it["started"], it.get("finished") or time.time()) for it in iterations.load(project)
                 if it.get("started")]
    except Exception:  # noqa: BLE001
        spans = []
    lines = []
    for r in all_rows():
        if r.get("project") != project:
            continue
        if "iteration" not in r:
            r["iteration"] = next((n for n, a, b in spans if a <= r.get("t", 0) <= b), None)
        lines.append(json.dumps(r))
    path.write_text("".join(x + "\n" for x in lines), encoding="utf-8")


def record_tokens(project: str, who: str, usage: dict | None, cost_usd: float = 0.0, model: str = "") -> None:
    usage = usage or {}
    row = {"t": time.time(), "day": date.today().isoformat(), "project": project, "who": who, "model": model,
           "in": int(usage.get("input_tokens") or 0), "out": int(usage.get("output_tokens") or 0),
           "cache_read": int(usage.get("cache_read_input_tokens") or 0),
           "cache_write": int(usage.get("cache_creation_input_tokens") or 0), "cost": float(cost_usd or 0)}
    if not any((row["in"], row["out"], row["cache_read"], row["cache_write"], row["cost"])):
        return
    row.update(_stage(project, who))
    with _lock:
        own = _ledger(project, create=True)
        if own is not None and not own.exists():
            _backfill(project, own)
        ledger.append(row, _log_file(), own)


def all_rows() -> list[dict]:
    """Every token row of every project (the app's own log)."""
    return ledger.read(_log_file())


def project_rows(slug: str) -> list[dict]:
    """A project's usage history: its own ledger when it has one (it travels with the folder), else the app's log."""
    own = _ledger(slug)
    if own is not None and own.exists():
        return ledger.read(own)
    return [r for r in all_rows() if r.get("project") == slug]


def row_model(r: dict) -> str:
    """Rows from before 0.7.0 have no model: Claude rows then used the default model (Sonnet)."""
    if r.get("model"):
        return r["model"]
    return "" if r.get("who") == "total" else models.CLAUDE["sonnet"]["id"]


def tokens(r: dict) -> int:
    """Tokens of one row as the app counts them: input, output and cache writes (cache reads are nearly free)."""
    return r.get("in", 0) + r.get("out", 0) + r.get("cache_write", 0)


def est_cost(r: dict) -> float:
    """What one row costs at the providers' list prices (0 when the model has no known price)."""
    return models.cost({**r, "model": row_model(r)}) or 0.0


def cost_today() -> float:
    """What the API key was really billed today (the engine reports it per turn)."""
    today = date.today().isoformat()
    return round(sum(r.get("cost", 0) for r in all_rows() if r.get("day") == today), 2)


def project_cost(slug: str, windows: list[tuple[float, float]] | None = None) -> dict:
    """Estimated cost of a project at the providers' prices (whatever the sign-in method), plus the cost of each
    time window given (used per iteration)."""
    total, per_window, count = 0.0, [0.0] * len(windows or []), 0
    for r in project_rows(slug):
        if not tokens(r):
            continue
        c = est_cost(r)
        total += c
        count += tokens(r)
        for i, (a, b) in enumerate(windows or []):
            if a <= r.get("t", 0) <= b:
                per_window[i] += c
    return {"est_cost": round(total, 4), "tokens": count, "windows": [round(x, 4) for x in per_window]}


def _fresh_real(data: dict, max_age: float = 180) -> bool:
    now = time.time()
    return any(isinstance(v, dict) and v.get("source") == "claude_usage" and now - (v.get("percent_at") or 0) < max_age
               for v in data.values())


def warning_note() -> str:
    """A short note for Fred when plan usage is high, or '' when it is fine. Uses Claude's real figures, refreshed
    first if they are more than 3 minutes old, and says how fresh each figure is (item 73)."""
    data = plan()
    if not _fresh_real(data):
        if fetch_plan():
            data = plan()
    real = _fresh_real(data, 900)
    notes = []
    for v in data.values():
        if not isinstance(v, dict) or v.get("percent") is None:
            continue
        if real and v.get("source") != "claude_usage":
            continue  # real figures are available: ignore older status reports
        if v["percent"] >= 75 or v.get("status") == "allowed_warning":
            when = v.get("reset_text") or (datetime.fromtimestamp(v["resets_at"]).strftime("%a %H:%M")
                                           if v.get("resets_at") else "later")
            at = datetime.fromtimestamp(v.get("percent_at") or time.time()).strftime("%H:%M")
            fresh = f"read from Claude at {at}" if v.get("source") == "claude_usage" else \
                f"last reported at {at}, may be out of date"
            notes.append(f"{v['label']}: {v['percent']:.0f}% used (resets {when}; {fresh})")
    if not notes:
        return ""
    return ("[App] Plan usage is high: " + "; ".join(notes) + ". Before starting a large step (building, testing), "
            "tell the user and ask whether to continue now or wait for the reset.")
