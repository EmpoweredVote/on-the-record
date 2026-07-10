from scripts.checks import (
    check_note_quality, check_deid_present, check_trailing_ellipsis,
    check_partisan_tell_in_blind, check_source_tier, topic_live_count, topic_min_candidates,
)

def row(**kw):
    base = dict(id="q1", candidate="A", topic_key="housing", race_id="r1",
                readrank_selected=True, quote_text="We must build more homes.",
                deidentified_text="We must build more homes.", editor_note="Verbatim, no edits.",
                source_name="www.youtube.com", source_url="https://youtu.be/x?t=1s")
    base.update(kw); return base

def test_note_missing_is_high_guided():
    f = check_note_quality(row(editor_note=None))
    assert f and f.check_id == "note-missing" and f.severity == "high" and f.fix_class == "guided"

def test_note_with_section_ref_flagged():
    f = check_note_quality(row(editor_note="Matches stance (§4.3); tier-1 debate."))
    assert f and f.check_id == "note-section-ref"

def test_note_too_long_flagged():
    long = "One sentence here. Two here. Three here. Four here."
    f = check_note_quality(row(editor_note=long))
    assert f and f.check_id == "note-too-long"

def test_good_note_passes():
    assert check_note_quality(row(editor_note="Clear housing supply position. Verbatim, no edits.")) is None

def test_deid_null_flagged():
    f = check_deid_present(row(deidentified_text=None))
    assert f and f.check_id == "deid-missing" and f.fix_class == "guided"

def test_trailing_ellipsis_flagged():
    f = check_trailing_ellipsis(row(quote_text="We must act …"))
    assert f and f.check_id == "trailing-ellipsis"

def test_partisan_tell_in_blind_flagged():
    f = check_partisan_tell_in_blind(row(deidentified_text="These Democrat policies failed."))
    assert f and f.check_id == "partisan-tell" and f.fix_class == "guided"

def test_source_tier_campaign_site_flagged():
    f = check_source_tier(row(source_url="https://www.xavierbecerra2026.com/housing", source_name="www.xavierbecerra2026.com"))
    assert f and f.check_id == "source-tier-4"

def test_topic_same_candidate_two_live_flagged_legacy():
    g = {"race_id": "r1", "topic_key": "housing",
         "quotes": [row(id="a", readrank_selected=True), row(id="b", readrank_selected=True)]}
    f = topic_live_count(g)
    assert f and f.check_id == "multiple-live" and f.severity == "high"

def test_topic_one_candidate_not_rankable():
    g = {"race_id": "r1", "topic_key": "housing", "quotes": [row(readrank_selected=True)]}
    f = topic_min_candidates(g)
    assert f and f.check_id == "not-rankable"

def test_topic_two_candidates_one_each_is_clean():
    g = {"race_id": "r1", "topic_key": "housing",
         "quotes": [row(id="a", candidate="A", readrank_selected=True),
                    row(id="b", candidate="B", readrank_selected=True)]}
    assert topic_live_count(g) is None

def test_topic_same_candidate_two_live_flagged():
    g = {"race_id": "r1", "topic_key": "housing",
         "quotes": [row(id="a", candidate="A", readrank_selected=True),
                    row(id="b", candidate="A", readrank_selected=True)]}
    f = topic_live_count(g)
    assert f and f.check_id == "multiple-live" and f.severity == "high"

def test_run_mechanical_aggregates_quote_and_topic():
    from scripts.checks import run_mechanical
    rows = [row(id="a", candidate="A", editor_note=None), row(id="b", candidate="B", editor_note=None)]
    fs = run_mechanical(rows)
    ids = {f.check_id for f in fs}
    assert "note-missing" in ids  # quote-level
    # two distinct candidates, one live each -> NOT multiple-live
    assert "multiple-live" not in ids
