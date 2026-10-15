"""Consumption tracking.

- Plan usage (Claude subscription): the Claude engine sends rate-limit updates with the 5-hour session and
  weekly utilization and reset times. The latest of each is kept in usage.json.
- Tokens and API-price cost per reply are appended to usage-log.jsonl (one line per model call or turn),
  so the app can show today / this week / this month and who used what.
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
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import models, paths

LIMIT_LABELS = {"five_hour": "Current session (5 hours)", "seven_day": "This week", "seven_day_sonnet": "This week (Sonnet only)",
                "seven_day_opus": "This week (Opus)", "seven_day_sonnet": "This week (Sonnet)", "overage": "Extra usage"}
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
            # a real figure from Claude's usage command is fresher than a status report without a percentage
            old["seen"] = entry["seen"]
            data[kind] = old
            _plan_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
            return old
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
                          "reset_text": v["reset_text"], "resets_at": old.get("resets_at") if old.get("source") == "claude_usage" else None}
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
    if not project or project in ("setup", "?"):
        return None
    try:
        from . import projects
        if not projects.load(project):
            return None
        d = projects.project_dir(project) / projects.META
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d / "usage.jsonl"
    except Exception:  # noqa: BLE001
        return None


def _stage(project: str, who: str = "") -> dict:
    """Which iteration, phase and stage (problem, build, test...) the project is in right now, for the ledger."""
    try:
        from . import projects, timeline
        p = projects.load(project) or {}
        return {**timeline.project_stage(project, who), "project_name": p.get("name", "")}
    except Exception:  # noqa: BLE001
        return {"iteration": None, "phase": ""}


def _backfill(project: str, ledger: Path) -> None:
    """First time a project gets its ledger: copy its earlier rows from the app's log, matching each to the
    iteration that was running then (by the iterations' start and finish times)."""
    try:
        from . import iterations
        spans = [(it["n"], it["started"], it.get("finished") or time.time()) for it in iterations.load(project)
                 if it.get("started")]
    except Exception:  # noqa: BLE001
        spans = []
    lines = []
    for r in _rows():
        if r.get("project") != project:
            continue
        if "iteration" not in r:
            r["iteration"] = next((n for n, a, b in spans if a <= r.get("t", 0) <= b), None)
        lines.append(json.dumps(r))
    ledger.write_text("".join(x + "\n" for x in lines), encoding="utf-8")


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
        ledger = _ledger(project, create=True)
        if ledger is not None and not ledger.exists():
            _backfill(project, ledger)
        with open(_log_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if ledger is not None:
            try:
                with open(ledger, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
            except OSError:
                pass  # the folder may be read-only or offline; the app's log still has it


def _rows(path: Path | None = None) -> list[dict]:
    try:
        lines = (path or _log_file()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _row_model(r: dict) -> str:
    """Rows from before 0.7.0 have no model: Claude rows then used the default model (Sonnet)."""
    if r.get("model"):
        return r["model"]
    return "" if r.get("who") == "total" else models.CLAUDE["sonnet"]["id"]


def _tok(r: dict) -> int:
    return r.get("in", 0) + r.get("out", 0) + r.get("cache_write", 0)


def summary(days: int = 14) -> dict:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    by_day: dict[str, dict] = {(today - timedelta(days=i)).isoformat(): defaultdict(int) for i in range(days)}
    cost = {"today": 0.0, "week": 0.0, "month": 0.0}
    tokens = {"today": 0, "week": 0, "month": 0}
    split = {"input": 0, "output": 0, "cached": 0}       # this month, input vs output vs cached input (item 44)
    cache = {"cached_input": 0, "fresh_input": 0, "saved": 0.0}  # cache savings this month (item 43a)
    periods = {"today": today, "week": week_start, "month": month_start}
    projects: dict[str, dict] = defaultdict(lambda: {"tokens": 0, "cost": 0.0, "est_cost": 0.0, "week": 0,
                                                     "models": defaultdict(int), "unpriced": False})
    by_model: dict[str, dict] = defaultdict(lambda: {"today": 0, "week": 0, "month": 0, "total": 0,
                                                     "est_cost": 0.0, "est_week": 0.0,
                                                     "input": 0, "output": 0, "cached": 0})
    by_day_model: dict[str, dict] = {k: defaultdict(int) for k in by_day}
    for r in _rows():
        d = date.fromisoformat(r["day"])
        tok = _tok(r)
        model = _row_model(r)
        est = models.cost({**r, "model": model}) if tok else 0.0
        for key, start in periods.items():
            if d >= start:
                cost[key] += r.get("cost", 0)
                tokens[key] += tok
        if r["day"] in by_day and tok:
            by_day[r["day"]][r.get("who") or "Fred"] += tok
            by_day_model[r["day"]][models.label(model)] += tok
        p = projects[r.get("project") or "?"]
        p["tokens"] += tok
        p["cost"] += r.get("cost", 0)
        if d >= week_start:
            p["week"] += tok
        if tok:
            p["models"][models.label(model)] += tok
            if est is None:
                p["unpriced"] = True
            else:
                p["est_cost"] += est
            m = by_model[model]
            m["total"] += tok
            m["est_cost"] += est or 0
            m["input"] += r.get("in", 0); m["output"] += r.get("out", 0)
            m["cached"] += r.get("cache_read", 0)
            for key, start in periods.items():
                if d >= start:
                    m[key] += tok
            if d >= week_start:
                m["est_week"] += est or 0
            if d >= month_start:
                split["input"] += r.get("in", 0); split["output"] += r.get("out", 0)
                split["cached"] += r.get("cache_read", 0)
                cache["cached_input"] += r.get("cache_read", 0)
                cache["fresh_input"] += r.get("in", 0) + r.get("cache_write", 0)
                pr = models.price_for(model)
                if pr:  # what the cached input would have cost at the full input price, minus the cache-read price
                    cache["saved"] += r.get("cache_read", 0) * (pr["in"] - pr["cache_read"]) / 1_000_000
    week_total = sum(p["week"] for p in projects.values()) or 1
    total_in = cache["cached_input"] + cache["fresh_input"]
    cache["cached_pct"] = round(100 * cache["cached_input"] / total_in, 1) if total_in else 0.0
    cache["saved"] = round(cache["saved"], 2)
    return {"plan": plan(), "cost": {k: round(v, 2) for k, v in cost.items()}, "tokens": tokens,
            "split": split, "cache": cache,
            "days": [{"day": k, **dict(v)} for k, v in sorted(by_day.items())],
            "days_by_model": [{"day": k, **dict(v)} for k, v in sorted(by_day_model.items())],
            "models": [{"model": k, "label": models.label(k), "priced": models.price_for(k) is not None,
                        **{kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}}
                       for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["total"])],
            "projects": {k: {"tokens": v["tokens"], "cost": round(v["cost"], 2), "est_cost": round(v["est_cost"], 2),
                             "week_share": round(100 * v["week"] / week_total, 1), "unpriced": v["unpriced"],
                             "models": dict(v["models"])} for k, v in projects.items()},
            "prices_checked": models.PRICES_CHECKED}


def project_rows(slug: str) -> list[dict]:
    """A project's usage history: its own ledger when it has one (it travels with the folder), else the app's log."""
    ledger = _ledger(slug)
    if ledger is not None and ledger.exists():
        return _rows(ledger)
    return [r for r in _rows() if r.get("project") == slug]


PHASE_LABELS = {"discover": "Requirements (Fred)", "architecture": "Architecture (Mat)", "plan": "Planning",
                "execute": "Between iterations", "review": "Review"}


def project_usage(slug: str) -> dict:
    """Tokens and estimated cost of one project by model and by iteration (item 57)."""
    from . import iterations
    names = {it["n"]: it["name"] for it in iterations.load(slug)}

    def blank():
        return {"in": 0, "out": 0, "cached": 0, "cache_write": 0, "tokens": 0, "est_cost": 0.0, "unpriced": False}
    total, by_model, groups = blank(), defaultdict(blank), {}
    for r in project_rows(slug):
        tok = _tok(r)
        if not tok and not r.get("cache_read"):
            continue
        model = _row_model(r)
        est = models.cost({**r, "model": model})
        n = r.get("iteration")
        key = f"it-{n}" if n else f"ph-{r.get('phase') or 'other'}"
        if key not in groups:
            label = f"Iteration {n}: {names.get(n, '')}".rstrip(": ") if n else \
                PHASE_LABELS.get(r.get("phase") or "", "Before AI Team Lite tracked iterations")
            groups[key] = {"key": key, "n": n or 0, "label": label, **blank(), "models": defaultdict(blank)}
        for bucket in (total, by_model[model], groups[key], groups[key]["models"][model]):
            bucket["in"] += r.get("in", 0); bucket["out"] += r.get("out", 0)
            bucket["cached"] += r.get("cache_read", 0); bucket["cache_write"] += r.get("cache_write", 0)
            bucket["tokens"] += tok
            if est is None:
                bucket["unpriced"] = True
            else:
                bucket["est_cost"] += est

    def clean(b):
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.items() if k != "models"}
    ordered = sorted(groups.values(), key=lambda g: (g["n"] == 0, g["n"]))
    ledger = _ledger(slug)
    return {"total": clean(total),
            "by_model": [{"model": m, "label": models.label(m), **clean(v)}
                         for m, v in sorted(by_model.items(), key=lambda kv: -kv[1]["tokens"])],
            "by_iteration": [{**clean(g), "models": [{"model": m, "label": models.label(m), **clean(v)}
                                                    for m, v in sorted(g["models"].items(), key=lambda kv: -kv[1]["tokens"])]}
                             for g in ordered],
            "portable": bool(ledger and ledger.exists()), "ledger": str(ledger) if ledger else ""}


def project_cost(slug: str, windows: list[tuple[float, float]] | None = None) -> dict:
    """Estimated cost of a project at the providers' prices (whatever the sign-in method), plus the cost of each
    time window given (used per iteration)."""
    total, per_window, tokens = 0.0, [0.0] * len(windows or []), 0
    for r in project_rows(slug):
        if not _tok(r):
            continue
        c = models.cost({**r, "model": _row_model(r)}) or 0.0
        total += c
        tokens += _tok(r)
        for i, (a, b) in enumerate(windows or []):
            if a <= r.get("t", 0) <= b:
                per_window[i] += c
    return {"est_cost": round(total, 4), "tokens": tokens, "windows": [round(x, 4) for x in per_window]}


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
