"""Extra "brains" for the council (item 79): free AI models (Google Gemini's free tier, Ollama on this PC) that give a
second and third opinion on big decisions and review each other anonymously, with Fred as chairman.

They are only used by the council: all the building, testing and reviewing is done by Claude. They are called through
their OpenAI-compatible chat API with the standard library (no extra packages).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import paths, secrets_store, settings

PROVIDERS = {
    "gemini": {"name": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key": "gemini_api_key", "key_url": "https://aistudio.google.com/apikey", "badge": "has a free tier",
               "privacy": "On Google's free tier, what you send may be used to improve Google's products. Do not "
                          "send private or client data to this brain.",
               "how": ["Open Google AI Studio and sign in with a Google account (free).",
                       "Click \"Get API key\" (or \"Create API key\"), then copy the key.",
                       "Paste it here and click \"Load the list of models\"."],
               "free_hint": "Flash models are on the free tier. Pro models are paid.",
               "suggested": ["gemini-flash-latest", "gemini-flash-lite-latest"]},
    "ollama": {"name": "Ollama (on this PC)", "base": "http://localhost:11434/v1/", "key": None,
               "key_url": "https://ollama.com/download", "badge": "free, on your PC",
               "privacy": "Runs on your PC: nothing leaves it. Small models make more mistakes.",
               "how": ["Pick a model below: the ones already on your PC are at the top, then the ones that fit it.",
                       "If it is not on your PC yet, AI Team Lite downloads it for you (and installs Ollama first if needed).",
                       "Bigger models are smarter but slower; the recommended one is the best fit for your PC."],
               "free_hint": "Completely free: it runs on your computer, so it uses no online tokens at all.",
               "suggested": ["qwen2.5-coder:7b", "qwen3:8b", "llama3.2:3b"]},
}
# Free vs paid per model (best-effort, by name). Used for the badge on each model (item 40).
def model_tier(provider: str, model: str) -> str:
    m = model.lower()
    if provider == "ollama":
        return "free"
    if provider == "gemini":
        return "free" if "flash" in m and "pro" not in m else "paid"
    return "unknown"


def _gemini_version(m: str) -> float:
    v = re.search(r"gemini-(\d+(?:\.\d+)?)", m)
    return float(v.group(1)) if v else 0.0


def model_note(provider: str, model: str, newest: float = 0.0) -> str:
    """A short, plain-language note on what a model is best used for (item 54). For Gemini it also says whether the
    name is an always-up-to-date alias, an early preview, a fixed snapshot or an older generation, because the
    list returns many near-identical names and those are what tell them apart."""
    m = model.lower()
    if provider == "gemini":
        if "flash-lite" in m:
            role = "Fastest and cheapest: quick summaries, recaps and simple drafts"
        elif "flash" in m:
            role = "Fast all-rounder: a good default for light work"
        elif "pro" in m:
            role = "Most capable Gemini: harder reasoning and long documents"
        elif "gemma" in m:
            role = "Google's small open model, served online"
        else:
            role = "General Gemini model"
        if "thinking" in m:
            role += ", thinks step by step (slower)"
        if m.endswith("-latest"):
            kind = "Always points to the newest version (recommended)"
        elif re.search(r"preview|exp", m):
            kind = "Early preview: may change or be switched off without notice"
        elif re.search(r"-\d{3}$|-\d{2}-\d{4}|-\d{4}$", m):
            kind = "Fixed snapshot: never changes, but is retired eventually"
        elif newest and _gemini_version(m) and _gemini_version(m) < newest:
            kind = "Older generation: prefer a newer one"
        else:
            kind = "Current generation"
        cost = "Free tier" if model_tier(provider, model) == "free" else "Paid"
        return f"{role}. {kind}. {cost}."
    return ""


def _gemini_rank(m: str) -> tuple:
    """Order the Gemini list for a non-technical reader: recommended aliases first, then stable current models, then
    previews, then older generations; within each, Flash-Lite, Flash, Pro."""
    m = m.lower()
    fam = 0 if "flash-lite" in m else 1 if "flash" in m else 2 if "pro" in m else 3
    if m.endswith("-latest"):
        group = 0
    elif re.search(r"preview|exp", m):
        group = 2
    else:
        group = 1
    return (group, -_gemini_version(m), fam, m)


# Local models for Ollama, smallest first (item 55). min_ram_gb follows Ollama's own guidance (about 8 GB of memory
# for 7-8B models, 16 GB for 12-14B, 32 GB for 24-32B); size_gb is the download. Installed models that are not in
# this list are added at the end, so you always see everything you already have.
OLLAMA_CATALOG = [
    {"model": "gemma3:1b", "params": "1B", "size_gb": 0.8, "min_ram_gb": 4,
     "note": "Tiniest. Very quick summaries on a weak PC; makes many mistakes."},
    {"model": "llama3.2:1b", "params": "1B", "size_gb": 1.3, "min_ram_gb": 4,
     "note": "Tiny and fast. Short summaries and simple rewording."},
    {"model": "qwen3:1.7b", "params": "1.7B", "size_gb": 1.4, "min_ram_gb": 4,
     "note": "Very small but surprisingly good at simple reasoning."},
    {"model": "llama3.2:3b", "params": "3B", "size_gb": 2.0, "min_ram_gb": 8,
     "note": "Small and fast. Summaries, recaps and simple drafts."},
    {"model": "qwen2.5-coder:3b", "params": "3B", "size_gb": 1.9, "min_ram_gb": 8,
     "note": "Small coder. Tiny functions and test data on a modest PC."},
    {"model": "phi4-mini", "params": "3.8B", "size_gb": 2.5, "min_ram_gb": 8,
     "note": "Small but bright: light reasoning, explanations, math."},
    {"model": "qwen3:4b", "params": "4B", "size_gb": 2.6, "min_ram_gb": 8,
     "note": "Good reasoning for its size. Explanations and drafts."},
    {"model": "gemma3:4b", "params": "4B", "size_gb": 3.3, "min_ram_gb": 8,
     "note": "Good writing for its size. Drafts, proofreading."},
    {"model": "mistral:7b", "params": "7B", "size_gb": 4.1, "min_ram_gb": 8,
     "note": "Fast general text: drafts, explanations, proofreading."},
    {"model": "qwen2.5-coder:7b", "params": "7B", "size_gb": 4.7, "min_ram_gb": 8,
     "note": "Best small coder: writes small self-contained functions well. A good default."},
    {"model": "deepseek-r1:7b", "params": "7B", "size_gb": 4.7, "min_ram_gb": 8,
     "note": "Thinks step by step for harder problems. Slower."},
    {"model": "llama3.1:8b", "params": "8B", "size_gb": 4.9, "min_ram_gb": 8,
     "note": "Well-rounded: summaries, questions, everyday text."},
    {"model": "qwen3:8b", "params": "8B", "size_gb": 5.2, "min_ram_gb": 8,
     "note": "Strong general reasoning and writing for its size."},
    {"model": "mistral-nemo:12b", "params": "12B", "size_gb": 7.1, "min_ram_gb": 16,
     "note": "Good writer with a long memory for big documents."},
    {"model": "gemma3:12b", "params": "12B", "size_gb": 8.1, "min_ram_gb": 16,
     "note": "Google's open model: good writing and reviews."},
    {"model": "qwen2.5-coder:14b", "params": "14B", "size_gb": 9.0, "min_ram_gb": 16,
     "note": "Stronger coder, closer to a paid model."},
    {"model": "deepseek-r1:14b", "params": "14B", "size_gb": 9.0, "min_ram_gb": 16,
     "note": "Careful step-by-step reasoning for harder problems. Slow."},
    {"model": "phi4:14b", "params": "14B", "size_gb": 9.1, "min_ram_gb": 16,
     "note": "Strong reasoning and math for its size."},
    {"model": "qwen3:14b", "params": "14B", "size_gb": 9.3, "min_ram_gb": 16,
     "note": "Strong all-rounder: reasoning, writing and some code."},
    {"model": "gpt-oss:20b", "params": "20B", "size_gb": 14.0, "min_ram_gb": 16,
     "note": "OpenAI's open model: strong reasoning. Needs a good PC."},
    {"model": "devstral:24b", "params": "24B", "size_gb": 14.0, "min_ram_gb": 32,
     "note": "Made for coding work across several files."},
    {"model": "mistral-small3.2:24b", "params": "24B", "size_gb": 15.0, "min_ram_gb": 32,
     "note": "Capable general model: writing, reviews, reasoning."},
    {"model": "gemma3:27b", "params": "27B", "size_gb": 17.0, "min_ram_gb": 32,
     "note": "Google's largest open model: high-quality writing."},
    {"model": "qwen3:30b", "params": "30B", "size_gb": 19.0, "min_ram_gb": 32,
     "note": "Large but quick for its size (only part of it runs at a time)."},
    {"model": "qwen3-coder:30b", "params": "30B", "size_gb": 19.0, "min_ram_gb": 32,
     "note": "Best local coder. Needs a powerful PC."},
    {"model": "qwen2.5-coder:32b", "params": "32B", "size_gb": 20.0, "min_ram_gb": 32,
     "note": "Very strong coder, close to paid models. Needs a powerful PC."},
    {"model": "deepseek-r1:32b", "params": "32B", "size_gb": 20.0, "min_ram_gb": 32,
     "note": "Deep step-by-step reasoning. Slow without a big graphics card."},
]


def _norm(name: str) -> str:
    """Ollama names a model without a tag "name:latest"."""
    return name if ":" in name else name + ":latest"


def _fits(ram: float, need: float) -> bool:
    """Windows reports an "8 GB" PC as about 7.8 GB (some memory is reserved), so allow for that rounding."""
    return ram == 0 or ram + 0.6 >= need


def recommended_local(ram: float) -> str:
    """The model to suggest for this PC: the strongest coder that fits comfortably (helpers mostly write small
    checked functions)."""
    if ram and _fits(ram, 32):
        return "qwen3-coder:30b"
    if ram and _fits(ram, 16):
        return "qwen2.5-coder:14b"
    if _fits(ram, 8):
        return "qwen2.5-coder:7b"
    return "llama3.2:1b"


# Best local model per kind of work, strongest first: the first one that fits the PC is marked (item 64).
BEST_FOR = {
    "reasoning": ["deepseek-r1:32b", "qwen3:30b", "gpt-oss:20b", "deepseek-r1:14b", "phi4:14b", "qwen3:8b",
                  "deepseek-r1:7b", "qwen3:4b", "phi4-mini", "qwen3:1.7b"],
    "writing": ["gemma3:27b", "mistral-small3.2:24b", "gemma3:12b", "mistral-nemo:12b", "qwen3:8b", "gemma3:4b",
                "llama3.2:3b", "gemma3:1b"],
}
BEST_LABEL = {"coding": "best for coding", "reasoning": "best for reasoning", "writing": "best for writing"}


def best_local(ram: float) -> dict:
    """{"coding": model, "reasoning": model, "writing": model} for this PC."""
    sizes = {c["model"]: c["min_ram_gb"] for c in OLLAMA_CATALOG}
    out = {"coding": recommended_local(ram)}
    for kind, order in BEST_FOR.items():
        out[kind] = next((m for m in order if _fits(ram, sizes.get(m, 99))), order[-1])
    return out


def recommended_why(ram: float) -> str:
    rec = recommended_local(ram)
    return (f"Recommended: {rec}, the strongest coding model that fits {f'{ram:g} GB of memory' if ram else 'your PC'}. "
            "Helper brains mostly write small functions that must pass tests, so a coding model is the best default. "
            "Reasoning models such as DeepSeek-R1 think out loud first: better for hard questions, but slower and "
            "they write many more tokens for this light work.")


def _ollama_installed() -> set[str]:
    try:
        data = _http("GET", base_url("ollama").replace("/v1/", "/api/tags"), None, timeout=5)
        return {str(m.get("name", "")) for m in data.get("models", []) if m.get("name")}
    except BrainError:
        return set()


def ollama_installed_names() -> set[str]:
    return {_norm(n) for n in _ollama_installed()}


def ollama_catalog() -> dict:
    """Every local model in the catalog, whether it fits this PC's memory, its best use, and which are already
    installed (item 55). Starts Ollama first when it is installed but stopped, so installed models are seen."""
    from . import tooling
    try:
        ram = float(tooling.memory_gb() or 0)
    except Exception:  # noqa: BLE001
        ram = 0.0
    try:
        vram = float(tooling.gpu_memory_gb() or 0)
    except Exception:  # noqa: BLE001
        vram = 0.0
    try:
        st = tooling.ollama_status()
        if st["state"] == "stopped" and settings.load().get("ollama_autostart", True):
            st = tooling.ollama_start(wait=15)
    except Exception:  # noqa: BLE001
        st = {"state": "stopped" if tooling.ollama_exe() else "not installed"}
    installed = ollama_installed_names()
    rec = recommended_local(ram)
    best = best_local(ram)
    rows, seen = [], set()
    for c in OLLAMA_CATALOG:
        name = _norm(c["model"])
        rows.append({**c, "installed": name in installed, "ram_ok": _fits(ram, c["min_ram_gb"]),
                     "recommended": c["model"] == rec,
                     "best_for": [BEST_LABEL[k] for k, m in best.items() if m == c["model"] and k != "coding"]})
        seen.add(name)
    for name in sorted(installed - seen):
        rows.append({"model": name.removesuffix(":latest"), "params": "", "size_gb": 0, "min_ram_gb": 0,
                     "note": "Already on your PC (not in AI Team Lite's list, so no description).",
                     "installed": True, "ram_ok": True, "recommended": False, "best_for": []})
    return {"models": rows, "ram_gb": ram, "vram_gb": vram, "ollama_state": st.get("state", "not installed"),
            "ollama_running": st.get("state") == "running", "recommended": rec, "best": best,
            "recommended_why": recommended_why(ram)}
def base_url(provider: str) -> str:
    return PROVIDERS[provider]["base"]


class BrainError(RuntimeError):
    pass


# ---------------------------------------------------------------------------- configuration
def all_brains() -> list[dict]:
    return list(settings.load().get("brains") or [])


def get(brain_id: str) -> dict | None:
    return next((b for b in all_brains() if b["id"] == brain_id), None)


def label(brain: dict) -> str:
    return f"{PROVIDERS[brain['provider']]['name'].split(' (')[0]} · {brain['model']}"


def model_key(brain: dict) -> str:
    """How a brain's model is named in the usage log and the price list, e.g. "gemini:gemini-2.5-flash"."""
    return f"{brain['provider']}:{brain['model']}"


def test_config(provider: str, model: str) -> tuple[bool, str]:
    """Check a provider+model (using the key already saved) before adding it to the list (item 39)."""
    if provider not in PROVIDERS:
        return False, "Unknown provider."
    return test({"id": "probe", "provider": provider, "model": model.strip()})


def add(provider: str, model: str) -> dict:
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider")
    model = model.strip()
    if not re.fullmatch(r"[\w.:/\-]{1,80}", model):
        raise ValueError("Invalid model name")
    brains = all_brains()
    bid = re.sub(r"[^a-z0-9]+", "-", f"{provider}-{model}".lower()).strip("-")[:60]
    if any(b["id"] == bid for b in brains):
        raise ValueError("This brain is already added.")
    brain = {"id": bid, "provider": provider, "model": model, "enabled": True, "added": time.time()}
    brains.append(brain)
    settings.save({"brains": brains})
    return brain


def update(brain_id: str, **changes) -> None:
    brains = all_brains()
    for b in brains:
        if b["id"] == brain_id:
            b.update({k: v for k, v in changes.items() if k in ("enabled",)})
    settings.save({"brains": brains})


def remove(brain_id: str) -> None:
    settings.save({"brains": [b for b in all_brains() if b["id"] != brain_id]})
# ---------------------------------------------------------------------------- statistics
def _exhausted_file() -> Path:
    return paths.data_dir() / "brain-exhausted.json"


def exhausted_until(brain_id: str) -> float:
    try:
        return float(json.loads(_exhausted_file().read_text(encoding="utf-8")).get(brain_id, 0))
    except (OSError, ValueError):
        return 0.0


def _mark_exhausted(brain_id: str, seconds: int = 3600) -> None:
    try:
        data = json.loads(_exhausted_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[brain_id] = time.time() + seconds
    _exhausted_file().write_text(json.dumps(data), encoding="utf-8")


# "Let Fred choose": cheapest capable helper first (local and free before paid)
def key_required(provider: str) -> bool:
    p = PROVIDERS[provider]
    return bool(p["key"]) and not p.get("key_optional")
def usable(b: dict) -> bool:
    """A council member that can answer now: switched on, its key saved, not out of quota."""
    return b.get("provider") in PROVIDERS and bool(b.get("enabled")) and exhausted_until(b["id"]) <= time.time() \
        and (not key_required(b["provider"]) or secrets_store.has(PROVIDERS[b["provider"]]["key"]))
# ---------------------------------------------------------------------------- calling a brain
def _key(provider: str) -> str | None:
    name = PROVIDERS[provider]["key"]
    return secrets_store.get_secret(name) if name else None


def _http(method: str, url: str, key: str | None, body: dict | None = None, timeout: float = 120) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": "AI-Team-Lite"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise BrainError(f"HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise BrainError("timed out") from None
        raise BrainError(f"not reachable ({e.reason})") from None
    except TimeoutError:  # the provider accepted the call but did not answer in time
        raise BrainError("timed out") from None


def chat(brain: dict, system: str, user: str, max_tokens: int = 4000, timeout: float = 120) -> tuple[str, dict]:
    p = PROVIDERS[brain["provider"]]
    key = _key(brain["provider"])
    if key_required(brain["provider"]) and not key:
        raise BrainError(f"no API key saved for {p['name']}")
    try:
        base = base_url(brain["provider"])
        if not base:
            raise BrainError(f"no address set for {p['name']}")
        if brain["provider"] == "ollama" and settings.load().get("ollama_autostart", True):
            try:
                from . import tooling
                if tooling.ollama_status()["state"] == "stopped":
                    tooling.ollama_start(wait=10)
            except Exception:  # noqa: BLE001
                pass
        data = _http("POST", base + "chat/completions", key, {
            "model": brain["model"], "max_tokens": max_tokens, "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, timeout=timeout)
    except BrainError as e:
        if "HTTP 429" in str(e):
            _mark_exhausted(brain["id"])
            raise BrainError(f"{label(brain)} is out of quota for now") from None
        raise
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    u = data.get("usage") or {}
    return text.strip(), {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}


def list_models_tiered(provider: str) -> list[dict]:
    names = list_models(provider)
    if provider == "gemini":
        names.sort(key=_gemini_rank)
    # the current generation is the newest STABLE one: a preview of the next generation must not make today's
    # stable models look outdated
    newest = max((_gemini_version(n) for n in names if not re.search(r"preview|exp", n)), default=0.0) \
        if provider == "gemini" else 0.0
    added = {b["model"] for b in all_brains() if b["provider"] == provider}
    rows = []
    for m in names:
        rec = provider == "gemini" and m.lower().endswith("-latest") and "pro" not in m.lower()
        rows.append({"model": m, "tier": model_tier(provider, m), "note": model_note(provider, m, newest),
                     "recommended": rec, "added": m in added})
    return rows


def list_models(provider: str) -> list[str]:
    p = PROVIDERS[provider]
    if provider == "ollama":
        data = _http("GET", base_url("ollama").replace("/v1/", "/api/tags"), None, timeout=5)
        return sorted(m["name"] for m in data.get("models", []))
    base = base_url(provider)
    if not base:
        raise BrainError(f"no address set for {p['name']}")
    data = _http("GET", base + "models", _key(provider), timeout=20)
    ids = [str(m.get("id", "")).removeprefix("models/") for m in data.get("data", [])]
    if provider == "gemini":
        # only text models the team can use (no embeddings, image, speech, live-audio or robotics models)
        ids = [i for i in ids if i.startswith("gemini")
               and not re.search(r"embed|image|tts|audio|live|vision|native|aqa|computer-use|robotics", i)]
    return sorted(set(i for i in ids if i))


TEST_TIMEOUT = 30  # seconds: a test must answer quickly, or the user is left with a spinner (item 63)


def test(brain: dict) -> tuple[bool, str]:
    try:
        # local models may need to load into memory first, so they get longer
        wait = 90 if brain["provider"] == "ollama" else TEST_TIMEOUT
        # thinking models (Gemini 2.5+) spend tokens before answering: leave room so the reply is not empty
        text, _ = chat(brain, "Reply with the single word: ready", "Are you ready?", max_tokens=200, timeout=wait)
        return True, f"Answered: {text[:40] or '(ok)'}"
    except BrainError as e:
        if "timed out" in str(e).lower():
            return False, f"no answer within {wait} seconds (the provider may be busy; try again)"
        return False, str(e)
# ---------------------------------------------------------------------------- test-checked code delegation
def summary() -> dict:
    """The council's extra brains for Settings > Brains."""
    out = [{**b, "label": label(b), "provider_name": PROVIDERS[b["provider"]]["name"],
            "badge": PROVIDERS[b["provider"]]["badge"], "tier": model_tier(b["provider"], b["model"]),
            "privacy": PROVIDERS[b["provider"]]["privacy"], "out_of_quota": exhausted_until(b["id"]) > time.time(),
            "has_key": not key_required(b["provider"]) or secrets_store.has(PROVIDERS[b["provider"]]["key"])}
           for b in all_brains() if b.get("provider") in PROVIDERS]
    return {"brains": out,
            "providers": {k: {kk: v.get(kk) for kk in ("name", "key_url", "badge", "privacy", "suggested", "how", "free_hint")}
                          | {"needs_key": bool(v["key"]), "is_local": k == "ollama"} for k, v in PROVIDERS.items()}}
