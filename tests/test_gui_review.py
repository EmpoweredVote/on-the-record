from __future__ import annotations

from gui.models import SpeakerCard, ReviewPageData, CONFIDENT_THRESHOLD


def _card(label, name, conf):
    return SpeakerCard(
        label=label, name=name, confidence=conf, method="llm",
        minutes=3.0, seg_count=4, sample_text="hello", hints=[], clip_seeks=[12.0],
    )


def test_confident_threshold_value():
    assert CONFIDENT_THRESHOLD == 0.85


def test_speaker_card_is_confirmed_requires_name_and_high_confidence():
    assert _card("S0", "Mayor Johnson", 0.91).is_confirmed is True
    assert _card("S1", "Mayor Johnson", 0.5).is_confirmed is False   # low conf
    assert _card("S2", None, 0.99).is_confirmed is False              # no name
    assert _card("S3", "(unidentified)", 0.99).is_confirmed is False  # placeholder name


def test_speaker_card_display_name_placeholder():
    assert _card("S0", None, 0.0).display_name == "(unidentified)"
    assert _card("S0", "Mayor Johnson", 0.9).display_name == "Mayor Johnson"


def test_review_page_data_holds_groups():
    page = ReviewPageData(
        meeting_id="m", display_name="Council", media_kind="video",
        needs_attention=[_card("S1", None, 0.0)],
        confirmed=[_card("S0", "Mayor Johnson", 0.9)],
    )
    assert page.speaker_count == 2
    assert page.needs_attention[0].label == "S1"
    assert page.confirmed[0].label == "S0"
