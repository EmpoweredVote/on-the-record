import json
from src.evidence.crosscheck import parse_crosscheck, crosscheck

class FakeProvider:
    def __init__(self, r): self._r=list(r); self.prompts=[]
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.prompts.append(prompt); return self._r.pop(0)

def test_parse_crosscheck():
    v = parse_crosscheck(json.dumps({"own_words": True, "in_context": True,
        "primary": True, "issue": "housing", "notes": "ok"}))
    assert v.own_words and v.primary and v.issue == "housing"

def test_tag_agree_is_computed_from_issue_match():
    p = FakeProvider([json.dumps({"own_words": True, "in_context": True,
        "primary": True, "issue": "Housing", "notes": ""})])
    from src.evidence.models import QuoteCandidate
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    v = crosscheck(cand, "…We will build…", candidate_name="Bass", provider=p,
                   extractor_issue="housing")
    assert v.tag_agree is True

def test_tag_disagreement_when_issues_differ():
    p = FakeProvider([json.dumps({"own_words": True, "in_context": True,
        "primary": True, "issue": "transportation", "notes": ""})])
    from src.evidence.models import QuoteCandidate
    cand = QuoteCandidate(text="We will build.", context="…", issue="housing")
    v = crosscheck(cand, "…We will build…", candidate_name="Bass", provider=p,
                   extractor_issue="housing")
    assert v.tag_agree is False
