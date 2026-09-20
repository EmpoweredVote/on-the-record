from src.evidence.models import GateResults, SourceType, Status
from src.evidence.disposition import decide

def _full(**kw):
    base = dict(verbatim=True, own_words=True, in_context=True, primary=True,
                tag_agree=True, judge_tag_ok=0.9, judge_context_sufficient=0.9,
                judge_dispute_risk=0.1)
    base.update(kw)
    return GateResults(**base)

def test_all_pass_primary_is_green():
    status, reasons = decide(_full(), SourceType.PRIMARY)
    assert status == Status.GREEN.value and reasons == []

def test_verbatim_fail_drops():
    status, reasons = decide(_full(verbatim=False), SourceType.PRIMARY)
    assert status == Status.DROPPED.value and "verbatim-fail" in reasons

def test_scorecard_drops_even_if_verbatim():
    status, reasons = decide(_full(), SourceType.SCORECARD_QUIZ)
    assert status == Status.DROPPED.value

def test_non_primary_flags():
    status, reasons = decide(_full(primary=False), SourceType.SECONDARY_LEAD)
    assert status == Status.FLAGGED.value and "not-primary" in reasons

def test_crosscheck_disagreement_flags():
    status, reasons = decide(_full(own_words=False), SourceType.PRIMARY)
    assert status == Status.FLAGGED.value and "crosscheck:own_words" in reasons

def test_high_dispute_risk_flags():
    status, reasons = decide(_full(judge_dispute_risk=0.8), SourceType.PRIMARY)
    assert status == Status.FLAGGED.value and "judge:dispute-risk" in reasons

def test_none_gate_dims_do_not_flag():
    # Only verbatim set; all crosscheck dims and judge scores left at their
    # None defaults must NOT add reasons — a primary source stays green.
    gates = GateResults(verbatim=True)
    status, reasons = decide(gates, SourceType.PRIMARY)
    assert status == Status.GREEN.value and reasons == []

def test_source_type_accepts_plain_string():
    status, reasons = decide(_full(), "primary")
    assert status == Status.GREEN.value and reasons == []
