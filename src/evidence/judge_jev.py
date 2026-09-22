from __future__ import annotations
import json
from .models import JudgeScores

_MECHANISM = ["a bare goal, target/metric, or vague direction — no concrete lever",
              "gestures at an approach but names no specific instrument",
              "names a specific, contestable policy lever the candidate would use"]
_TAG = ["off-question / does not address the issue", "defensibly about the issue"]
_CONTEXT = ["context too thin to vet the quote", "enough context to vet the quote"]
_DISPUTE = ["clearly the speaker's own on-record position",
            "some ambiguity about attribution or context",
            "easily disownable / high out-of-context risk"]
_FORWARD = ["a recitation of past record or accomplishment — what the candidate already did",
            "a forward-looking stance or proposal — what the candidate would do or believes should happen"]
_LEVELS = {"mechanism": _MECHANISM, "tag_ok": _TAG,
           "context_sufficient": _CONTEXT, "dispute_risk": _DISPUTE,
           "forward_looking": _FORWARD}


def build_questions() -> dict:
    from typesafe_sdk import Score  # lazy: optional dependency
    q = {
        "mechanism": "Does the quote name a specific, contestable policy lever the candidate would use?",
        "tag_ok": "Is the ISSUE tag defensible for this quote?",
        "context_sufficient": "Is there enough context to vet the quote?",
        "dispute_risk": "How easily could the speaker disown this as out-of-context or never said?",
        "forward_looking": "Is this a forward-looking stance/proposal, or a recitation of past record?",
    }
    return {k: Score(instructions=q[k], criteria=_LEVELS[k]) for k in q}


def _norm(ans, levels) -> float:
    denom = (len(levels) - 1) or 1
    return max(0.0, min(1.0, float(ans.score) / denom))


def judge_jev(cand, *, client=None) -> JudgeScores:
    if client is None:
        from typesafe_sdk import TypeSafeClient  # lazy
        client = TypeSafeClient()
    state = f"ISSUE: {cand.issue}\nQUOTE: {cand.text}\nCONTEXT: {cand.context}"
    a = client.system_one(state=state, questions=build_questions()).answers
    conf = {k: getattr(a[k], "confidence", None) for k in _LEVELS}
    return JudgeScores(
        tag_ok=_norm(a["tag_ok"], _TAG),
        context_sufficient=_norm(a["context_sufficient"], _CONTEXT),
        dispute_risk=_norm(a["dispute_risk"], _DISPUTE),
        mechanism=_norm(a["mechanism"], _MECHANISM),
        forward_looking=_norm(a["forward_looking"], _FORWARD),
        notes=json.dumps({"confidence": conf}))
