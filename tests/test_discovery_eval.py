from src.discovery.eval import classify_outcome, summarize
from src.discovery.models import Verdict


def test_classify_outcome():
    hit = Verdict(relevant=True, confidence=0.9, original_vs_clip="original")
    assert classify_outcome(True, hit) == "true_positive"
    assert classify_outcome(False, hit) == "false_positive"
    miss = Verdict(relevant=False, confidence=0.2)
    assert classify_outcome(True, miss) == "false_negative"
    assert classify_outcome(False, miss) == "true_negative"
    broken = Verdict(relevant=False, confidence=0.0, rejected_reason="no JSON in reply")
    assert classify_outcome(True, broken) == "parse_failure"


def test_summarize_counts():
    s = summarize("haiku", ["true_positive", "true_positive", "false_negative"])
    assert s["model"] == "haiku" and s["true_positive"] == 2
    assert s["recall"] == 2 / 3
