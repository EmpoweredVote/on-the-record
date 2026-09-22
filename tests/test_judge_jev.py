from src.evidence.judge_jev import judge_jev
from src.evidence.models import QuoteCandidate


class _Ans:
    def __init__(self, score, confidence):
        self.score = score
        self.confidence = confidence


class _Resp:
    def __init__(self, answers):
        self.answers = answers


class _FakeClient:
    """Returns fixed score/confidence per question name; records the state it saw."""

    def __init__(self, scored):
        self._scored = scored
        self.state = None
        self.questions = None

    def system_one(self, *, state, questions):
        self.state = state
        self.questions = questions
        return _Resp({k: _Ans(*self._scored[k]) for k in questions})


def _cand():
    return QuoteCandidate(text="We will build 40,000 units by cutting permit timelines.",
                           context="housing Q", issue="housing")


def test_judge_jev_normalizes_scores_to_0_1():
    # mechanism uses a 3-level scale: raw score 1.43 -> 1.43/2 = 0.715
    fake = _FakeClient({"mechanism": (1.43, 0.35), "tag_ok": (1.0, 0.9),
                         "context_sufficient": (1.0, 0.8), "dispute_risk": (0.0, 0.95),
                         "forward_looking": (1.0, 0.9)})
    js = judge_jev(_cand(), client=fake)
    assert abs(js.mechanism - 0.715) < 1e-6
    assert js.tag_ok == 1.0 and js.context_sufficient == 1.0 and js.dispute_risk == 0.0
    import json
    assert "confidence" in json.loads(js.notes)          # confidences carried
    assert "QUOTE:" in fake.state and "housing" in fake.state          # state built from cand


def test_judge_jev_returns_forward_looking():
    fake = _FakeClient({"mechanism":(1.0,0.9), "tag_ok":(1.0,0.9), "context_sufficient":(1.0,0.9),
                        "dispute_risk":(0.0,0.9), "forward_looking":(0.0,0.9)})  # 2-level → 0/1 = 0.0
    js = judge_jev(_cand(), client=fake)
    assert js.forward_looking == 0.0
