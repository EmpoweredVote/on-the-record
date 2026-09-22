from src.evidence.models import (SourceType, Status, GateResults, EvidenceItem, Lead)

def test_evidence_item_to_json_roundtrips_enums_and_gates():
    item = EvidenceItem(
        politician_id="p1", issue="housing", evidence_type="quote",
        verbatim_text="We will build.", source_url="https://ex.com/a",
        cited_via="https://ontheissues.org/x", context="…We will build…",
        deep_link="https://ex.com/a", source_type=SourceType.PRIMARY,
        gates=GateResults(verbatim=True, own_words=True, in_context=True,
                          primary=True, tag_agree=True, judge_tag_ok=0.9,
                          judge_context_sufficient=0.9, judge_dispute_risk=0.1),
        status=Status.GREEN, status_reasons=[], provenance={"batch": "b1"})
    j = item.to_json()
    assert j["source_type"] == "primary" and j["status"] == "green"
    assert j["gates"]["verbatim"] is True and j["issue"] == "housing"

def test_lead_defaults_carry_the_unverified_marker():
    lead = Lead(politician_id="p1", reported_text="I said X at the debate.",
                issue="crime", event="KABC debate, 2026-05-01",
                secondary_url="https://news.com/story", primary_handle=None)
    assert "NOT verified against a primary" in lead.note
    assert lead.to_json()["reported_text"] == "I said X at the debate."
