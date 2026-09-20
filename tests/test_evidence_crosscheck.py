import json
from src.evidence.crosscheck import parse_crosscheck, crosscheck, build_crosscheck_prompt

class FakeProvider:
    def __init__(self, r): self._r=list(r); self.prompts=[]
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.prompts.append(prompt); return self._r.pop(0)

def test_parse_crosscheck():
    v = parse_crosscheck(json.dumps({"own_words": True, "in_context": True,
        "primary": True, "tag_ok": True, "issue": "housing", "notes": "ok"}))
    assert v.own_words and v.primary and v.issue == "housing"

def test_build_crosscheck_prompt_includes_proposed_tag_and_asks_for_tag_ok():
    from src.evidence.models import QuoteCandidate
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    prompt = build_crosscheck_prompt(cand, "…We will build…", "Bass")
    assert "housing" in prompt
    assert "tag_ok" in prompt

def test_tag_ok_true_sets_tag_agree_even_when_suggested_issue_differs():
    # The model judges the extractor's tag defensible (tag_ok: true) even though
    # it suggests a differently-worded label — proving this is no longer a
    # string match against the extractor's issue.
    p = FakeProvider([json.dumps({"own_words": True, "in_context": True,
        "primary": True, "tag_ok": True, "issue": "immigrant entrepreneurship",
        "notes": ""})])
    from src.evidence.models import QuoteCandidate
    cand = QuoteCandidate(text="We will build.", context="…", issue="immigration")
    v = crosscheck(cand, "…We will build…", candidate_name="Bass", provider=p)
    assert v.tag_agree is True
    assert v.issue == "immigrant entrepreneurship"

def test_tag_ok_false_sets_tag_agree_false():
    p = FakeProvider([json.dumps({"own_words": True, "in_context": True,
        "primary": True, "tag_ok": False, "issue": "transportation", "notes": ""})])
    from src.evidence.models import QuoteCandidate
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    v = crosscheck(cand, "…We will build…", candidate_name="Bass", provider=p)
    assert v.tag_agree is False

def test_missing_tag_ok_defaults_tag_agree_false():
    p = FakeProvider([json.dumps({"own_words": True, "in_context": True,
        "primary": True, "issue": "housing", "notes": ""})])
    from src.evidence.models import QuoteCandidate
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    v = crosscheck(cand, "…We will build…", candidate_name="Bass", provider=p)
    assert v.tag_agree is False

def test_parse_crosscheck_unparseable_returns_all_false():
    for bad in ("not json", "null", "[1,2,3]", "42", '"a string"'):
        v = parse_crosscheck(bad)
        assert v.own_words is False and v.in_context is False
        assert v.primary is False and v.tag_agree is False and v.issue is None

def test_parse_crosscheck_non_string_issue_does_not_raise():
    v = parse_crosscheck(json.dumps({"own_words": True, "in_context": True,
        "primary": True, "tag_ok": True, "issue": 5, "notes": "ok"}))
    assert v.issue is None

def test_crosscheck_non_string_issue_through_full_flow_does_not_raise():
    p = FakeProvider([json.dumps({"own_words": True, "in_context": True,
        "primary": True, "tag_ok": True, "issue": 5, "notes": ""})])
    from src.evidence.models import QuoteCandidate
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    v = crosscheck(cand, "…We will build…", candidate_name="Bass", provider=p)
    assert v.tag_agree is True
    assert v.issue is None
