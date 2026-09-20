import json
from src.evidence.models import QuoteCandidate
from src.evidence.judge import parse_judge, judge


class FakeProvider:
    def __init__(self, r):
        self._r = list(r)

    def complete(self, prompt, *, max_tokens, temperature, system=None):
        return self._r.pop(0)


def test_parse_clamps_and_defaults():
    s = parse_judge(json.dumps({"tag_ok": 1.4, "context_sufficient": 0.8}))
    assert s.tag_ok == 1.0 and s.context_sufficient == 0.8 and s.dispute_risk == 1.0


def test_judge_calls_provider():
    p = FakeProvider(
        [
            json.dumps(
                {
                    "tag_ok": 0.9,
                    "context_sufficient": 0.9,
                    "dispute_risk": 0.1,
                    "notes": "clean",
                }
            )
        ]
    )
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    s = judge(cand, provider=p)
    assert s.dispute_risk == 0.1


def test_parse_judge_non_dict_json_uses_worst_defaults():
    for bad in ("null", "[1,2,3]", '"a string"', "not json"):
        s = parse_judge(bad)
        assert (
            s.tag_ok == 0.0 and s.context_sufficient == 0.0 and s.dispute_risk == 1.0
        )
