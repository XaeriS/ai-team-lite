"""Spelling help for what the user types to Fred (L38). Words are checked against the dictionaries of the chosen
languages (a word known in any of them is fine) plus the user's own words; nothing leaves the PC."""
from __future__ import annotations

import locale
import re
import threading

from . import settings

LANGUAGES = {"en": "English", "fr": "French", "pt": "Portuguese", "es": "Spanish", "de": "German", "it": "Italian",
             "nl": "Dutch"}
MAX_TEXT = 6000
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_SKIP_AROUND = re.compile(r"[\w/\\@.:#-]")   # part of a path, link, e-mail, file name or code
_checkers: dict = {}
_lock = threading.Lock()


def default_languages() -> list[str]:
    """English plus the language Windows is set to, when there is a dictionary for it."""
    try:
        system = (locale.getlocale()[0] or "")[:2].lower()
    except (ValueError, TypeError):
        system = ""
    names = {"en": "en", "fr": "fr", "pt": "pt", "es": "es", "sp": "es", "de": "de", "ge": "de", "it": "it", "nl": "nl",
             "du": "nl"}
    langs = ["en"]
    if names.get(system) and names[system] not in langs:
        langs.append(names[system])
    return langs


def languages() -> list[str]:
    chosen = settings.load().get("spell_languages")
    langs = [x for x in (chosen if isinstance(chosen, list) else default_languages()) if x in LANGUAGES]
    return langs


def _checker(lang: str):
    with _lock:
        if lang not in _checkers:
            from spellchecker import SpellChecker
            _checkers[lang] = SpellChecker(language=lang)
        return _checkers[lang]


def _known(word: str, langs: list[str], own: set[str]) -> bool:
    w = word.lower().replace("’", "'")
    if w in own:
        return True
    parts = [x for x in w.split("'") if len(x) > 1]   # l'adresse, don't: each part may be the known word
    for lang in langs:
        c = _checker(lang)
        if c.known([w]) or ("'" in w and all(c.known([x]) for x in parts)):
            return True
    return False


def suggestions(word: str, langs: list[str], limit: int = 5) -> list[str]:
    """Closest known words: the first language's best ones first, then the other languages'."""
    per_lang = []
    for lang in langs:
        c = _checker(lang)
        cands = sorted((x for x in (c.candidates(word.lower()) or ()) if x != word.lower()),
                       key=lambda x: -c.word_usage_frequency(x))
        per_lang.append([x.capitalize() if word[:1].isupper() else x for x in cands])
    out = []
    for take in (3, limit):
        for cands in per_lang:
            for x in cands[:take]:
                if x not in out:
                    out.append(x)
    return out[:limit]


def check(text: str) -> list[dict]:
    """Words that look misspelled: [{word, start, end, suggestions}]. Names (capitalised inside a sentence), ALL CAPS,
    words with digits, links, paths, file names and code are left alone."""
    langs = languages()
    if not langs or not settings.load().get("spell_check", True):
        return []
    text = text[:MAX_TEXT]
    own = {w.lower() for w in (settings.load().get("spell_words") or [])}
    issues, seen = [], {}
    for m in _WORD.finditer(text):
        word, a, b = m.group(), m.start(), m.end()
        before, after = text[a - 1:a], text[b:b + 1]
        if len(word) < 3 or word.isupper() or (before and _SKIP_AROUND.match(before)) or (after and re.match(r"[\w/\\@]", after)):
            continue
        if after == "." and re.match(r"[A-Za-z0-9]", text[b + 1:b + 2] or ""):   # file.name, site.com
            continue
        if text[b:b + 3] == "://":   # https://...
            continue
        sentence_start = not text[:a].strip() or text[:a].rstrip()[-1:] in ".!?:\n"
        if word[:1].isupper() and not sentence_start:   # a name or a product
            continue
        key = word.lower()
        if key not in seen:
            seen[key] = None if _known(word, langs, own) else suggestions(word, langs)
        if seen[key] is not None:
            issues.append({"word": word, "start": a, "end": b, "suggestions": seen[key]})
        if len(issues) >= 40:
            break
    return issues


def add_word(word: str) -> list[str]:
    word = word.strip().lower()
    if not _WORD.fullmatch(word):
        raise ValueError("Only a single word can be added.")
    words = sorted({*(settings.load().get("spell_words") or []), word})
    settings.save({"spell_words": words})
    return words
