from src.discovery.eval import calibration, classify_outcome, summarize
from src.discovery.models import Verdict


def _v(relevant, conf):
    return Verdict(relevant=relevant, confidence=conf)


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


def test_calibration_brier_perfect_predictions():
    pairs = [(True, _v(True, 1.0)), (False, _v(False, 1.0))]
    out = calibration(pairs)
    assert out["n"] == 2
    assert out["brier"] == 0.0


def test_calibration_brier_scores_implied_relevance_probability():
    # says relevant at 0.8 but gold is False -> (0.8 - 0)^2 = 0.64
    out = calibration([(False, _v(True, 0.8))])
    assert abs(out["brier"] - 0.64) < 1e-9


def test_calibration_skips_parse_failures_and_buckets():
    bad = Verdict(False, 0.0, rejected_reason="no JSON in reply")
    pairs = [(True, _v(True, 0.95)), (False, _v(True, 0.95)), (True, bad)]
    out = calibration(pairs)
    assert out["n"] == 2
    top = [b for b in out["buckets"] if b["range"] == "0.8–1.0"]
    assert top and top[0]["n"] == 2 and abs(top[0]["actual"] - 0.5) < 1e-9
