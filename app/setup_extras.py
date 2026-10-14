"""Run by the installer after the files are copied (item 70): set up the optional extras the user ticked, and
skip anything that is already on this PC. Never fails the installation: whatever cannot be done now is simply
done later, on first use, as before.

  python setup_extras.py [--voice] [--ollama] [--model]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def say(msg: str) -> None:
    print(msg, flush=True)


def _progress(prefix: str):
    last = {"step": -1}

    def cb(msg: str, frac: float | None = None) -> None:
        if frac is None:
            say(f"{prefix}: {msg}")
            return
        step = int(frac * 10)  # one line per 10%, so the installer window stays readable
        if step != last["step"]:
            last["step"] = step
            say(f"{prefix}: {msg} {int(frac * 100)}%")
    return cb


def voice() -> None:
    from app import voice as v
    if v.model_ready():
        say("Voice: the speech model is already on this PC - nothing to download.")
        return
    if not v.packages_available():
        say("Voice: the speech packages are missing from this installation - voice will not be available.")
        return
    say(f"Voice: downloading the speech model ({v.MODEL_SIZES.get('base', '')})...")
    v.download_model()
    say("Voice: ready. Click the microphone next to the message box to talk to Fred.")


def ollama() -> None:
    from app import tooling
    if tooling.ollama_exe() or tooling.ollama_status()["state"] == "running":
        say("Ollama: already installed on this PC - nothing to download.")
        return
    say("Ollama: downloading and installing (free local brains, about 1 GB)...")
    tooling.ollama_install(_progress("Ollama"))
    say("Ollama: installed.")


def local_model() -> None:
    from app import brains, tooling
    if not tooling.ollama_exe() and tooling.ollama_status()["state"] != "running":
        say("Local model: Ollama is not installed, so no model was downloaded.")
        return
    ram = tooling.memory_gb()
    rec = brains.recommended_local(ram)
    if tooling.ollama_status()["state"] != "running":
        tooling.ollama_start(wait=30)
    if brains._norm(rec) in brains.ollama_installed_names():
        say(f"Local model: {rec} is already on this PC - nothing to download.")
        return
    size = next((c["size_gb"] for c in brains.OLLAMA_CATALOG if c["model"] == rec), "?")
    say(f"Local model: downloading {rec} ({size} GB, the best fit for {ram:g} GB of memory)...")
    tooling.ollama_pull(rec, _progress("Local model"))
    say(f"Local model: {rec} is ready. Add it in Settings > Brains to let it take light work.")


def main(argv: list[str]) -> int:
    jobs = [("--voice", voice), ("--ollama", ollama), ("--model", local_model)]
    for flag, job in jobs:
        if flag in argv:
            try:
                job()
            except Exception as e:  # noqa: BLE001 - never fail the installation
                say(f"{flag[2:].capitalize()}: could not finish now ({str(e)[:200]}). "
                    "AI Team Lite will offer it again when you first need it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
