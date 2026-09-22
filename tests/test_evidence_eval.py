from src.evidence.models import EvidenceItem, GateResults, SourceType, Status
from src.evidence.eval import score, item_key

def _item(pid, url, text, status, src=SourceType.PRIMARY):
    return EvidenceItem(politician_id=pid, issue="housing", evidence_type="quote",
        verbatim_text=text, source_url=url, cited_via=None, context="…",
        deep_link=url, source_type=src,
        gates=GateResults(verbatim=(status != Status.DROPPED.value)),
        status=status, status_reasons=[], provenance={})

def test_counts_and_rates():
    items = [_item("p1","https://a.com","We build","green"),
             _item("p1","https://a.com","We tax","flagged"),
             _item("p1","https://x.com","made up","dropped")]
    m = score(items, leads=[], gold={"labels": {}, "recall_sample": {}})
    assert m.green == 1 and m.flagged == 1 and m.dropped == 1
    assert 0.0 <= m.primary_source_rate <= 1.0

def test_precision_recall_against_gold():
    it = _item("p1","https://a.com","We build 30k units","green")
    gold = {"labels": {item_key(it): "green"},
            "recall_sample": {"https://a.com": 2}}
    m = score([it], leads=[], gold=gold)
    assert m.precision == 1.0
    assert m.recall == 0.5   # 1 found of 2 expected on the sampled source

def test_precision_is_over_green_picks_only_reject_lowers_score():
    it = _item("p1","https://a.com","We build 30k units","green")
    gold = {"labels": {item_key(it): "reject"}, "recall_sample": {}}
    m = score([it], leads=[], gold=gold)
    assert m.precision == 0.0

def test_labeling_a_dropped_item_does_not_change_green_only_precision():
    green_it = _item("p1","https://a.com","We build 30k units","green")
    dropped_it = _item("p1","https://x.com","made up","dropped")
    gold = {"labels": {item_key(green_it): "green",
                       item_key(dropped_it): "reject"},
            "recall_sample": {}}
    m = score([green_it, dropped_it], leads=[], gold=gold)
    assert m.precision == 1.0
