from src.evidence.commit import build_rows, WRITE_STATUSES

TOPICS = {"housing": "topic-housing-uuid", "homelessness": "topic-homeless-uuid"}

def _item(**kw):
    base = dict(politician_id="p1", issue="housing", evidence_type="quote",
                verbatim_text="We will triple housing construction.",
                source_url="https://ex.com/a", cited_via=None, context="…ctx…",
                deep_link="https://ex.com/a", source_type="primary",
                gates={"verbatim": True, "judge_mechanism": 0.9}, status="green",
                status_reasons=[], provenance={"extractor": "haiku-or", "batch": "b1"})
    base.update(kw)
    return base

def test_drops_non_green_flagged():
    rows = build_rows([_item(status="dropped"), _item(status="green"),
                       _item(status="flagged", verbatim_text="Enforce the ordinance.")], TOPICS)
    assert len(rows) == 2
    assert {r["machine_status"] for r in rows} == {"green", "flagged"}

def test_resolves_mapped_topic_id_and_review_pending():
    r = build_rows([_item(issue="Housing")], TOPICS)[0]
    assert r["topic_id"] == "topic-housing-uuid"      # case-insensitive map
    assert r["issue"] == "Housing" and r["review_status"] == "pending"
    assert r["machine_status"] == "green" and r["evidence_type"] == "quote"

def test_free_issue_leaves_topic_id_none():
    r = build_rows([_item(issue="immigrant entrepreneurship")], TOPICS)[0]
    assert r["topic_id"] is None and r["issue"] == "immigrant entrepreneurship"

def test_gate_flags_and_provenance_shaped():
    r = build_rows([_item(status="flagged", status_reasons=["judge:no-mechanism"],
                          gates={"verbatim": True, "judge_mechanism": 0.2})], TOPICS)[0]
    assert r["gate_flags"] == {"reasons": ["judge:no-mechanism"],
                               "gates": {"verbatim": True, "judge_mechanism": 0.2}}
    assert r["provenance"]["extractor"] == "haiku-or" and r["batch_id"] == "b1"
    assert r["source_cycle_year"] is None
