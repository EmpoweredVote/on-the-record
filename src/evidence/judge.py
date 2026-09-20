from __future__ import annotations
import json
import re
from .models import JudgeScores

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)
_SYSTEM = (
    "You score a proposed evidence quote. Respond with ONLY the requested "
    "JSON. Scores are 0..1."
)

_INSTRUCTIONS = """Score this quote as ranking/compass evidence.
QUOTE: {quote}
ISSUE TAG: {issue}
CONTEXT: {context}

- tag_ok: does the quote actually answer the ISSUE (1) or is it off-question (0)?
- context_sufficient: is the CONTEXT enough for a reader to vet the quote (1) or thin (0)?
- dispute_risk: how likely the speaker could say "I never said that" / "out of context"
  (0 = safe, 1 = high risk).
- mechanism: does the quote name a SPECIFIC, CONTESTABLE policy lever/instrument the
  candidate would use (e.g. build shelters, enforce encampment ordinances, expand
  treatment/services, triple housing construction) — score near 1. A bare GOAL
  ("reduce homelessness — no one disagrees"), a TARGET/METRIC ("cut encampments 50%
  by 2028"), or a VAGUE direction ("direct dollars to what works", "deliver immediate
  treatment", "work with the County") names no concrete lever — score near 0.
Return JSON: {{"tag_ok","context_sufficient","dispute_risk","mechanism","notes"}}.
"""


def build_judge_prompt(cand) -> str:
    return _INSTRUCTIONS.format(quote=cand.text, issue=cand.issue, context=cand.context)


def _clamp(v, default):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def parse_judge(raw: str) -> JudgeScores:
    m = _FENCE.search(raw or "")
    payload = m.group(1) if m else (raw or "")
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    return JudgeScores(
        tag_ok=_clamp(d.get("tag_ok"), 0.0),
        context_sufficient=_clamp(d.get("context_sufficient"), 0.0),
        dispute_risk=_clamp(d.get("dispute_risk"), 1.0),
        mechanism=_clamp(d.get("mechanism"), 0.0),
        notes=(d.get("notes") or ""),
    )


def judge(cand, *, provider, max_tokens=800) -> JudgeScores:
    raw = provider.complete(
        build_judge_prompt(cand),
        max_tokens=max_tokens,
        temperature=0.0,
        system=_SYSTEM,
    )
    return parse_judge(raw)
