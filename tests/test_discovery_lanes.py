import pytest
from src.discovery.lanes import content_lane, FORMAL_EVENT_KINDS

@pytest.mark.parametrize("ovc,kind,expected", [
    ("original", "news_clip", "full_event"),
    ("original", None, "full_event"),
    ("original", "debate", "full_event"),
    ("clip", "debate", "event_clip"),
    ("clip", "forum", "event_clip"),
    ("clip", "press_conference", "event_clip"),
    ("clip", "community_meeting", "event_clip"),
    ("clip", "news_clip", "news_clip"),
    ("clip", "podcast", "news_clip"),
    ("clip", "council", "news_clip"),
    ("clip", "school_board", "news_clip"),
    ("clip", None, "news_clip"),
    (None, "debate", "unknown"),
    (None, None, "unknown"),
])
def test_content_lane(ovc, kind, expected):
    assert content_lane(ovc, kind) == expected

def test_formal_kinds_are_known_event_kinds():
    from src.event_kinds import EVENT_KINDS
    assert FORMAL_EVENT_KINDS <= set(EVENT_KINDS)


@pytest.mark.parametrize("ovc,kind,expected", [
    ("original", "questionnaire", "questionnaire"),
    ("clip", "questionnaire", "questionnaire"),
    (None, "questionnaire", "questionnaire"),
    ("original", "debate", "full_event"),   # unchanged
    ("clip", "news_clip", "news_clip"),      # unchanged
])
def test_content_lane_questionnaire(ovc, kind, expected):
    assert content_lane(ovc, kind) == expected
