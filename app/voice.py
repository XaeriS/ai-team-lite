"""Hands-free talking: AI Team Lite listens through the microphone, turns speech into text on this PC
(faster-whisper, nothing is sent anywhere), and ends your turn when you say the end word (default "copy")
or after a few seconds of silence.

The speech model (about 145 MB for "base") is downloaded once, after the user agrees.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Callable

from . import paths, settings

RATE = 16000
MODEL_SIZES = {"base": "about 145 MB", "small": "about 480 MB"}
_model = None
_model_lock = threading.Lock()


def models_dir() -> Path:
    d = paths.data_dir() / "voice-models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def packages_available() -> bool:
    """Cheap check (no import): the speech packages are installed. They are loaded on first use."""
    import importlib.util

    return all(importlib.util.find_spec(m) is not None for m in ("faster_whisper", "numpy", "sounddevice"))


def model_ready(size: str | None = None) -> bool:
    size = size or settings.load()["voice_model"]
    return (models_dir() / size / "model.bin").exists()


def download_model(size: str | None = None) -> Path:
    from faster_whisper import download_model as dl

    size = size or settings.load()["voice_model"]
    out = models_dir() / size
    dl(size, output_dir=str(out))
    return out


def _load_model():
    global _model
    with _model_lock:
        size = settings.load()["voice_model"]
        if _model is None or _model[0] != size:
            from faster_whisper import WhisperModel

            _model = (size, WhisperModel(str(models_dir() / size), device="cpu", compute_type="int8"))
        return _model[1]


def whisper_transcribe(audio, final: bool) -> str:
    lang = settings.load()["voice_language"]
    model = _load_model()
    segments, _ = model.transcribe(audio, language=None if lang == "auto" else lang,
                                   beam_size=5 if final else 1, vad_filter=final, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


# ---------------------------------------------------------------------------- L11: say why listening failed
PRIVACY_URL = "ms-settings:privacy-microphone"
PRIVACY_TEXT = ("Windows is not letting AI Team Lite use the microphone. Open Windows Settings > Privacy & security > "
                "Microphone and turn on \"Microphone access\" and \"Let desktop apps access your microphone\".")


class MicSilent(Exception):
    """The microphone opened but sends nothing at all (Windows privacy settings usually)."""


def explain(e: BaseException | str) -> tuple[str, str]:
    """A plain reason for a microphone or speech problem, and a code the screen uses to offer the right fix:
    no_mic | blocked | busy | model | packages | other."""
    t = str(e)
    low = t.lower()
    if isinstance(e, MicSilent) or "access is denied" in low or "unanticipated host error" in low or "-9999" in low:
        return PRIVACY_TEXT, "blocked"
    if "no default input" in low or "invalid device" in low or "-9996" in low or "no input device" in low \
            or "error querying device -1" in low or "no microphone" in low:
        return ("No microphone was found. Plug one in (or turn on the laptop's built-in one) and check it is the "
                "default recording device in Windows sound settings."), "no_mic"
    if "device unavailable" in low or "-9985" in low or "busy" in low or "exclusive" in low:
        return ("The microphone is busy: another app (Teams, Zoom, a recorder) may be using it on its own. Close that "
                "app or its call, then try again."), "busy"
    if "model.bin" in low or "unable to open file" in low or "ctranslate2" in low or "whisper" in low:
        return ("The speech model is missing or damaged. Click the microphone again to download it once more."), "model"
    if "no module named" in low or "portaudio library not found" in low:
        return "Voice is not available in this installation (a part is missing). Reinstall AI Team Lite.", "packages"
    return f"The microphone could not be used: {t[:200]}", "other"


def _log_file() -> Path:
    d = paths.data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "voice.log"


def log_problem(text: str) -> None:
    """Keep the error (never any audio or words) so it can be sent with Send feedback."""
    try:
        with open(_log_file(), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + " ".join(str(text).split())[:600] + "\n")
    except OSError:
        pass


def recent_problems(n: int = 10) -> list[str]:
    try:
        return _log_file().read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []


def device_name() -> str:
    try:
        import sounddevice as sd
        return str(sd.query_devices(kind="input")["name"])
    except Exception:  # noqa: BLE001
        return ""


def test_microphone(on_level: Callable[[float], None], seconds: float = 4.0, source=None) -> dict:
    """Listen for a few seconds and report the level live; returns {ok, device, peak, reason, code}."""
    import numpy as np

    device = "" if source is not None else device_name()
    peak, got = 0.0, 0
    try:
        src = source or _microphone(block_seconds=0.1)
        started = time.monotonic()
        try:
            for chunk in src:
                got += 1
                level = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
                peak = max(peak, float(np.max(np.abs(chunk))) if len(chunk) else 0.0)
                on_level(min(1.0, level * 8))
                if time.monotonic() - started >= seconds:
                    break
        finally:
            close = getattr(src, "close", None)
            if close:
                close()
    except Exception as e:  # noqa: BLE001
        reason, code = explain(e)
        log_problem(f"microphone test failed ({device or 'no device'}): {e}")
        return {"ok": False, "device": device, "peak": 0.0, "reason": reason, "code": code}
    if not got or peak <= 1e-6:
        log_problem(f"microphone test: no sound at all from {device or 'the default microphone'}")
        return {"ok": False, "device": device, "peak": peak, "reason": PRIVACY_TEXT, "code": "blocked"}
    if peak < 0.02:
        return {"ok": True, "device": device, "peak": peak, "code": "quiet",
                "reason": "The microphone works but is very quiet. Speak closer, or raise its level in Windows sound settings."}
    return {"ok": True, "device": device, "peak": peak, "code": "ok", "reason": "The microphone works."}


# ---------------------------------------------------------------------------- end-of-turn logic (pure, tested)
def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def strip_end_word(text: str, end_word: str) -> str | None:
    """If text ends with the end word (ignoring punctuation and case), return the text without it."""
    target = _words(end_word)
    if not target:
        return None
    words = _words(text)
    if len(words) < len(target) or words[-len(target):] != target:
        return None
    # cut the original text just before the end word(s)
    pattern = r"[\s,.;:!?-]*" + r"[\s,.;:!?-]+".join(re.escape(w) for w in target) + r"[\s,.;:!?'\"]*$"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


class Listener:
    """One listening turn. `source` yields float32 numpy chunks; `transcribe(audio, final)` returns text.
    Calls on_event({"state": "listening"|"partial"|"final"|"cancelled"|"error", "text": ...})."""

    def __init__(self, on_event: Callable[[dict], None], transcribe=whisper_transcribe, source=None,
                 clock=time.monotonic):
        s = settings.load()
        self.mode = s["voice_end_mode"]          # "word" | "silence" | "both"
        self.end_word = s["voice_end_word"] or "copy"
        self.silence = float(s["voice_silence_seconds"] or 3)
        self.max_seconds = 180
        self.on_event, self.transcribe, self.source, self.clock = on_event, transcribe, source, clock
        self._stop = threading.Event()
        self._cancel = False
        self.thread = threading.Thread(target=self._run, name="voice", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self, cancel: bool = False):
        self._cancel = cancel
        self._stop.set()

    # -------------------------------------------------------------- main loop
    def _run(self):
        import numpy as np

        try:
            source = self.source or _microphone()
        except Exception as e:  # noqa: BLE001
            return self._error(e)
        self.on_event({"state": "listening", "text": "", "end_word": self.end_word, "mode": self.mode})
        chunks: list = []
        floor, spoke, last_voice, started = None, False, self.clock(), self.clock()
        peak = 0.0
        last_partial_at, partial_len, text = 0.0, 0, ""
        try:
            for chunk in source:
                if self._stop.is_set():
                    break
                chunks.append(chunk)
                rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
                peak = max(peak, float(np.max(np.abs(chunk))) if len(chunk) else 0.0)
                now = self.clock()
                if floor is None or (not spoke and now - started < 0.6):
                    floor = rms if floor is None else (floor * 0.8 + rms * 0.2)
                if rms > max(0.012, (floor or 0) * 3.0):
                    spoke, last_voice = True, now
                total = sum(len(c) for c in chunks)
                # live text every ~1.2 s of new audio while speaking
                if spoke and total - partial_len >= RATE * 1.2 and now - last_partial_at >= 1.0:
                    text = self.transcribe(np.concatenate(chunks), False)
                    partial_len, last_partial_at = total, now
                    self.on_event({"state": "partial", "text": text})
                    if self.mode in ("word", "both"):
                        cut = strip_end_word(text, self.end_word)
                        if cut is not None:
                            return self._finish(cut)
                if spoke and self.mode in ("silence", "both") and now - last_voice >= self.silence:
                    break
                if now - started > self.max_seconds:
                    break
            if self._cancel:
                self.on_event({"state": "cancelled", "text": ""})
                return
            if not spoke:
                if not chunks or peak <= 1e-6:   # not a single sound: Windows blocks the microphone (usually)
                    raise MicSilent("no sound at all from the microphone")
                quiet = peak < 0.02
                self.on_event({"state": "cancelled", "text": "", "code": "quiet" if quiet else "",
                               "reason": "No speech heard: the microphone is very quiet. Speak closer or raise its level."
                               if quiet else "No speech heard."})
                return
            final = self.transcribe(np.concatenate(chunks), True) or text
            cut = strip_end_word(final, self.end_word)
            self._finish(cut if cut is not None else final)
        except Exception as e:  # noqa: BLE001
            self._error(e)
        finally:
            close = getattr(source, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass

    def _error(self, e: BaseException) -> None:
        reason, code = explain(e)
        log_problem(f"listening failed ({type(e).__name__}): {e}")
        self.on_event({"state": "error", "text": reason, "code": code})

    def _finish(self, text: str):
        self.on_event({"state": "final", "text": text.strip()})


def _microphone(block_seconds: float = 0.25):
    """Generator of mono float32 chunks from the default microphone."""
    import queue

    import sounddevice as sd

    q: queue.Queue = queue.Queue()
    stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=int(RATE * block_seconds),
                            callback=lambda data, frames, t, status: q.put(data[:, 0].copy()))
    stream.start()

    class Mic:
        def __iter__(self):
            first = True
            while True:
                try:
                    yield q.get(timeout=2)
                    first = False
                except queue.Empty:
                    if first:   # the stream opened but never delivered a single block
                        raise MicSilent("the microphone sends no sound")
                    return

        def close(self):
            stream.stop()
            stream.close()

    return Mic()
