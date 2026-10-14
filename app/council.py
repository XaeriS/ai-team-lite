"""LLM Council (item 22), after Andrej Karpathy's llm-council: several brains answer the same question, review each
other's answers anonymously, then the chairman (Fred, or Mat for architecture) writes the final recommendation.

Members are the helper brains that are switched on and reachable (text only, no file access), the lead's own answer,
and optionally the Scout's web report (item 23).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import random
import re
import string
import time
from typing import Callable

from . import brains, models, settings

FIRST = ("You are a member of an expert council reviewing a software decision for a non-technical client. Answer the "
         "question on your own, from the context given. Be concrete: a recommendation, the reasons, the main risks, "
         "and what you would check. Max 350 words.")
REVIEW = ("You are reviewing anonymous answers from a council to the same question. For each answer, judge accuracy, "
          "insight and risks missed. Then give a ranking from best to worst. Reply with a short evaluation per answer, "
          "then on the LAST line exactly: FINAL RANKING: <letters best to worst, comma separated>.")


def members() -> list[dict]:
    chosen = set(settings.load().get("council_members") or [])
    out = []
    for b in brains.all_brains():
        if (not chosen or b["id"] in chosen) and brains.usable(b):
            out.append(b)
    return out


def privacy_warnings(ms: list[dict]) -> list[str]:
    return [f"{brains.label(b)}: {brains.PROVIDERS[b['provider']]['privacy']}" for b in ms if b["provider"] == "gemini"]


def estimate(ms: list[dict], context_chars: int) -> dict:
    """Rough tokens/cost before running: each member answers once and reviews once."""
    tok_in = (context_chars // 4 + 400) * 2 + 2000
    tok_out = 900
    cost = 0.0
    for b in ms:
        p = models.price_for(brains.model_key(b))
        if p:
            cost += (tok_in * p["in"] + tok_out * p["out"]) / 1e6
    return {"members": len(ms), "tokens": (tok_in + tok_out) * len(ms), "cost": round(cost, 4)}


def _letters(n: int) -> list[str]:
    return list(string.ascii_uppercase[:n])


def parse_ranking(text: str, letters: list[str]) -> list[str]:
    m = re.search(r"FINAL RANKING:\s*(.+)$", text.strip(), re.I | re.M)
    found = re.findall(r"\b([A-Z])\b", (m.group(1) if m else ""))
    seen = []
    for x in found:
        if x in letters and x not in seen:
            seen.append(x)
    return seen


def run(question: str, context: str, lead_answer: str, lead_name: str, scout_report: str = "",
        ask: Callable | None = None, record: Callable[[str, dict, dict], None] | None = None) -> dict:
    """Returns {"opinions": [{who, answer}], "reviews": [{who, ranking, text}], "scores": {who: avg rank},
    "agreement": str}. `ask(brain, system, user)` -> (text, usage) is brains.chat by default."""
    ask = ask or (lambda b, sys_, user: brains.chat(b, sys_, user, max_tokens=1200))
    ms = members()
    prompt = f"Question:\n{question}\n\nContext:\n{context[:12000]}"
    opinions = [{"who": f"{lead_name} (lead)", "answer": lead_answer.strip()}]
    if scout_report.strip():
        opinions.append({"who": "Scout (web search)", "answer": scout_report.strip()})

    def first(b):
        t0 = time.time()
        try:
            text, u = ask(b, FIRST, prompt)
            if record:
                record(brains.label(b), u, {"model": brains.model_key(b)})
            return {"who": brains.label(b), "answer": text.strip(), "seconds": round(time.time() - t0, 1)}
        except brains.BrainError as e:
            return {"who": brains.label(b), "answer": "", "error": str(e)}

    with cf.ThreadPoolExecutor(max_workers=max(1, len(ms))) as pool:
        results = list(pool.map(first, ms))
    opinions += [r for r in results if r.get("answer")]
    failed = [r for r in results if not r.get("answer")]
    # anonymous peer review: shuffle, label A, B, C...
    order = list(range(len(opinions)))
    random.shuffle(order)
    letters = _letters(len(order))
    anon = {letters[i]: opinions[k] for i, k in enumerate(order)}
    listing = "\n\n".join(f"Answer {L}:\n{o['answer'][:3000]}" for L, o in anon.items())
    review_prompt = f"Question:\n{question}\n\n{listing}"

    def review(b):
        try:
            text, u = ask(b, REVIEW, review_prompt)
            if record:
                record(brains.label(b), u, {"model": brains.model_key(b)})
            return {"who": brains.label(b), "ranking": [anon[x]["who"] for x in parse_ranking(text, letters)],
                    "text": text.strip()[-1500:]}
        except brains.BrainError as e:
            return {"who": brains.label(b), "ranking": [], "error": str(e)}

    reviewers = [b for b in ms if any(o["who"] == brains.label(b) for o in opinions)] or ms
    with cf.ThreadPoolExecutor(max_workers=max(1, len(reviewers))) as pool:
        reviews = list(pool.map(review, reviewers)) if len(opinions) > 1 else []
    scores: dict[str, list[int]] = {o["who"]: [] for o in opinions}
    for r in reviews:
        for pos, who in enumerate(r.get("ranking") or [], 1):
            scores.setdefault(who, []).append(pos)
    avg = {w: round(sum(v) / len(v), 2) for w, v in scores.items() if v}
    ranked = sorted(avg, key=avg.get)
    agreement = ""
    if reviews:
        tops = [r["ranking"][0] for r in reviews if r.get("ranking")]
        if tops and len(set(tops)) == 1:
            agreement = f"All reviewers ranked {tops[0]} first."
        elif tops:
            agreement = "Reviewers disagreed on the best answer: " + ", ".join(sorted(set(tops))) + "."
    return {"question": question, "opinions": opinions, "failed": failed, "reviews": reviews, "scores": avg,
            "ranked": ranked, "agreement": agreement, "members": [brains.label(b) for b in ms]}


def for_chairman(result: dict) -> str:
    """What the chairman (the lead) gets back to write the final recommendation."""
    parts = [f"COUNCIL RESULT for: {result['question']}", result.get("agreement", "")]
    for o in result["opinions"]:
        parts.append(f"--- {o['who']} (average rank {result['scores'].get(o['who'], '-')}):\n{o['answer'][:2500]}")
    for f in result.get("failed") or []:
        parts.append(f"({f['who']} could not answer: {f.get('error', '')})")
    parts.append("You are the chairman: write the final recommendation for the user, say where the council agreed and "
                 "disagreed, answer the Scout's findings (reuse vs build), and keep your own view if it is better "
                 "argued. Then continue (e.g. show the approval card again).")
    return "\n\n".join(p for p in parts if p)


def dumps(result: dict) -> str:
    return json.dumps(result)[:200000]
