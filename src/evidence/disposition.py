from __future__ import annotations
from .models import GateResults, SourceType, Status

TAG_MIN = 0.7
CONTEXT_MIN = 0.7
DISPUTE_MAX = 0.3
MECHANISM_MIN = 0.7

_DROP_TYPES = {SourceType.SCORECARD_QUIZ.value, SourceType.DEAD.value}


def _tv(v):
    return v.value if isinstance(v, SourceType) else v


def decide(gates: GateResults, source_type) -> tuple[str, list]:
    st = _tv(source_type)
    if not gates.verbatim:
        return Status.DROPPED.value, ["verbatim-fail"]
    if st in _DROP_TYPES:
        return Status.DROPPED.value, [st]
    reasons: list = []
    if st != SourceType.PRIMARY.value:
        reasons.append("not-primary")
    for name, val in (("own_words", gates.own_words), ("in_context", gates.in_context),
                      ("primary", gates.primary), ("tag_agree", gates.tag_agree)):
        if val is False:
            reasons.append(f"crosscheck:{name}")
    if gates.judge_tag_ok is not None and gates.judge_tag_ok < TAG_MIN:
        reasons.append("judge:tag")
    if gates.judge_context_sufficient is not None and gates.judge_context_sufficient < CONTEXT_MIN:
        reasons.append("judge:context")
    if gates.judge_dispute_risk is not None and gates.judge_dispute_risk > DISPUTE_MAX:
        reasons.append("judge:dispute-risk")
    if gates.judge_mechanism is not None and gates.judge_mechanism < MECHANISM_MIN:
        reasons.append("judge:no-mechanism")
    return (Status.FLAGGED.value, reasons) if reasons else (Status.GREEN.value, [])
