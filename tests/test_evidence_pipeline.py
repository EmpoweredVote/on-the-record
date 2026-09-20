import json
from src.evidence.pipeline import Providers, run_source, run_candidate
from src.evidence.models import Status, SourceType

class FP:
    def __init__(self, r): self._r=list(r)
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        return self._r.pop(0)

SRC = "On her site Bass wrote: We will build 30,000 units of housing this term."

def _providers(extract, cross, judge):
    return Providers(extractor=FP([extract]), crosschecker=FP([cross]), judge=FP([judge]))

def test_primary_quote_that_passes_is_green():
    extract = json.dumps({"quotes": [{"text":"We will build 30,000 units of housing",
        "context": SRC, "issue":"housing","date":"2026","setting":"campaign site",
        "is_own_words":True,"is_primary_venue":True,"reported_event":None,
        "primary_handle":None}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,
                        "issue":"housing","notes":""})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1})
    items, leads = run_source(politician_id="p1",
        source_url="https://karenbass.com/housing", cited_via=None,
        providers=_providers(extract, cross, jud), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.GREEN.value
    assert items[0].source_type == SourceType.PRIMARY.value

def test_reported_secondary_becomes_lead_not_item():
    extract = json.dumps({"quotes": [{"text":"I will end street homelessness",
        "context":"At the debate she said…","issue":"homelessness","date":"2026",
        "setting":"debate","is_own_words":True,"is_primary_venue":False,
        "reported_event":"LAist debate, 2026-05-01","primary_handle":None}]})
    items, leads = run_source(politician_id="p1",
        source_url="https://laist.com/story", cited_via=None,
        providers=_providers(extract, "{}", "{}"), fetcher=lambda u: "…",
        candidate_name="Karen Bass", batch_id="b1")
    assert items == [] and len(leads) == 1
    assert "NOT verified against a primary" in leads[0].note

def test_hallucinated_quote_is_dropped():
    extract = json.dumps({"quotes": [{"text":"I will privatize every park",
        "context":"…","issue":"parks","date":"2026","setting":"site",
        "is_own_words":True,"is_primary_venue":True,"reported_event":None,
        "primary_handle":None}]})
    items, leads = run_source(politician_id="p1",
        source_url="https://karenbass.com/x", cited_via=None,
        providers=_providers(extract, "{}", "{}"), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.DROPPED.value
    assert "verbatim-fail" in items[0].status_reasons

def test_scorecard_domain_dropped_without_llm():
    items, leads = run_source(politician_id="p1",
        source_url="https://lcv.org/scorecard", cited_via=None,
        providers=_providers("{}", "{}", "{}"), fetcher=lambda u: "should not fetch",
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.DROPPED.value

def test_video_domain_becomes_lead_without_fetch_or_llm():
    called = {"fetched": False}
    def fetcher(u):
        called["fetched"] = True
        return "should not fetch"
    items, leads = run_source(politician_id="p1",
        source_url="https://youtu.be/abc", cited_via=None,
        providers=_providers("{}", "{}", "{}"), fetcher=fetcher,
        candidate_name="Karen Bass", batch_id="b1")
    assert items == [] and len(leads) == 1
    assert called["fetched"] is False
    assert "youtu.be/abc" in (leads[0].event or "") or "youtu.be/abc" in (leads[0].primary_handle or "")

def test_pointer_source_quote_is_flagged_never_green():
    extract = json.dumps({"quotes": [{"text": "We will build 30,000 units of housing",
        "context": SRC, "issue": "housing", "date": "2026", "setting": "wiki",
        "is_own_words": True, "is_primary_venue": True, "reported_event": None,
        "primary_handle": None}]})
    cross = json.dumps({"own_words": True, "in_context": True, "primary": True,
                        "issue": "housing", "notes": ""})
    jud = json.dumps({"tag_ok": 0.9, "context_sufficient": 0.9, "dispute_risk": 0.1})
    items, leads = run_source(politician_id="p1",
        source_url="https://en.wikipedia.org/wiki/Karen_Bass", cited_via=None,
        providers=_providers(extract, cross, jud), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1
    assert items[0].source_type == SourceType.POINTER.value
    assert items[0].status == Status.FLAGGED.value
    assert "not-primary" in items[0].status_reasons

def test_run_candidate_aggregates_across_sources():
    extract = json.dumps({"quotes": [{"text": "We will build 30,000 units of housing",
        "context": SRC, "issue": "housing", "date": "2026", "setting": "site",
        "is_own_words": True, "is_primary_venue": True, "reported_event": None,
        "primary_handle": None}]})
    cross = json.dumps({"own_words": True, "in_context": True, "primary": True,
                        "issue": "housing", "notes": ""})
    jud = json.dumps({"tag_ok": 0.9, "context_sufficient": 0.9, "dispute_risk": 0.1})
    providers = Providers(extractor=FP([extract]), crosschecker=FP([cross]), judge=FP([jud]))
    items, leads = run_candidate(politician_id="p1", candidate_name="Karen Bass",
        sources=[("https://karenbass.com/housing", None), ("https://lcv.org/x", None)],
        providers=providers, fetcher=lambda u: SRC, batch_id="b1")
    assert len(items) == 2
    statuses = {it.status for it in items}
    assert Status.GREEN.value in statuses and Status.DROPPED.value in statuses
