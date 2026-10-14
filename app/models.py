"""Claude models for the team, who uses which, and the price list used for cost estimates.

Only Claude models can drive the engine that edits files and runs commands (Fred, Mat, Developer, Tester,
Reviewer). Claude picks the model for each step; other brains (brains.py) only sit on the council.
"""
from __future__ import annotations

from . import settings

CLAUDE = {
    "opus": {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "rank": 3,
             "good_for": "the hardest thinking: architecture, tricky debugging, big refactors"},
    "sonnet": {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "rank": 2,
               "good_for": "everyday coding, testing and planning"},
    "haiku": {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5", "rank": 1,
              "good_for": "simple, quick steps: small fixes, re-running tests, checks, summaries"},
}
ROLES = ["Fred", "Mat", "Developer", "Tester", "Reviewer"]
# What "Let Fred choose" starts from for each role (Fred may pick another model per brief)
AUTO_DEFAULTS = {"Fred": "sonnet", "Mat": "opus", "Developer": "sonnet", "Tester": "sonnet", "Reviewer": "haiku"}
WORKER_KEYS = {"developer": "Developer", "tester": "Tester", "reviewer": "Reviewer"}

# USD per million tokens: input, output, cache read, cache write. Checked 2026-10-10 against the providers'
# published prices (Claude: platform.claude.com pricing page; Gemini: published API rates).
PRICES_CHECKED = "2026-10-10"
DEFAULT_PRICES = {
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0},
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.10, "cache_write": 2.50},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "gemini:gemini-2.5-flash": {"in": 0.30, "out": 2.50, "cache_read": 0.0, "cache_write": 0.0},
    "gemini:gemini-2.5-flash-lite": {"in": 0.10, "out": 0.40, "cache_read": 0.0, "cache_write": 0.0},
    "ollama:*": {"in": 0.0, "out": 0.0, "cache_read": 0.0, "cache_write": 0.0},
}


def nearest(alias: str | None) -> str:
    """A Claude model alias from a name ("sonnet", "claude-sonnet-5-5"...); unknown names fall back to Sonnet."""
    alias = (alias or "").lower()
    return next((k for k in CLAUDE if k in alias), "sonnet")


def for_role(role: str) -> str:
    """The model a role starts with. Fred picks another one per brief when a step needs it."""
    return AUTO_DEFAULTS.get(role, "sonnet")


def model_id(alias: str) -> str:
    return CLAUDE[nearest(alias)]["id"]


def guide() -> str:
    """Instructions for Fred about which model to give each brief."""
    return "\n".join([
        "- Claude models: " + ", ".join(f'"{k}" ({v["good_for"]})' for k, v in CLAUDE.items()) + ".",
        "- You choose the model for each worker brief with the Agent tool's model parameter, to balance quality "
        "and usage: the cheapest model that can do the step well. Defaults: Developer "
        f'"{for_role("Developer")}", Tester "{for_role("Tester")}", Reviewer "{for_role("Reviewer")}".',
        "- Escalate: if a step fails (tests keep failing, the reviewer rejects twice), give the retry to a "
        "stronger model and say so in one line. Go back to the cheaper model for the next ordinary step.",
    ])


# ---------------------------------------------------------------------------- prices and cost
def prices() -> dict:
    return {**DEFAULT_PRICES, **(settings.load().get("prices") or {})}


def price_for(model: str) -> dict | None:
    if not model:
        return None
    table = prices()
    if model in table:
        return table[model]
    provider = model.split(":", 1)[0] if ":" in model else ""
    if provider and f"{provider}:*" in table:
        return table[f"{provider}:*"]
    keys = sorted((k for k in table if model.startswith(k.rstrip("*")) or k in model), key=len, reverse=True)
    return table[keys[0]] if keys else None


def cost(row: dict) -> float | None:
    p = price_for(row.get("model") or "")
    if p is None:
        return None
    return (row.get("in", 0) * p["in"] + row.get("out", 0) * p["out"] + row.get("cache_read", 0) * p["cache_read"]
            + row.get("cache_write", 0) * p["cache_write"]) / 1_000_000


def label(model: str) -> str:
    for k, v in CLAUDE.items():
        if v["id"] in model or model == k:
            return v["label"]
    if ":" in model:
        provider, name = model.split(":", 1)
        return {"gemini": "Gemini", "ollama": "Ollama"}.get(provider, provider) + " · " + name
    return model or "Unknown model"
