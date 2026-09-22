from src.evidence.models import GateResults, SourceType, Status
from src.evidence.disposition import decide

def _full(**kw):
    base = dict(verbatim=True, own_words=True, in_context=True, primary=True,
                tag_agree=True, judge_tag_ok=0.9, judge_context_sufficient=0.9,
                judge_dispute_risk=0.1, judge_mechanism=0.9)
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

def test_low_mechanism_flags_no_mechanism():
    status, reasons = decide(_full(judge_mechanism=0.4), SourceType.PRIMARY)
    assert status == Status.FLAGGED.value and "judge:no-mechanism" in reasons

def test_mechanism_at_min_does_not_flag():
    status, reasons = decide(_full(judge_mechanism=0.7), SourceType.PRIMARY)
    assert status == Status.GREEN.value and "judge:no-mechanism" not in reasons

def test_mechanism_none_does_not_flag():
    status, reasons = decide(_full(judge_mechanism=None), SourceType.PRIMARY)
    assert status == Status.GREEN.value and "judge:no-mechanism" not in reasons

def test_record_recitation_flags_even_with_high_mechanism():
    # a record names concrete actions (high mechanism) but is not forward → flag, not green
    status, reasons = decide(_full(judge_mechanism=0.95, judge_forward_looking=0.2), SourceType.PRIMARY)
    assert status == Status.FLAGGED.value and "judge:record-not-forward" in reasons

def test_forward_stance_greens():
    status, reasons = decide(_full(judge_forward_looking=0.9), SourceType.PRIMARY)
    assert status == Status.GREEN.value and reasons == []

def test_forward_gate_inert_when_none():
    # judge_forward_looking absent (None) → gate does not fire (backward compatible)
    status, reasons = decide(_full(judge_forward_looking=None), SourceType.PRIMARY)
    assert status == Status.GREEN.value and "judge:record-not-forward" not in reasons
