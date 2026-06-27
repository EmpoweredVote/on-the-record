from __future__ import annotations

from src.models import Meeting, SpeakerMapping
from src.relink import relink_in_meeting


def _meeting(speakers: dict[str, SpeakerMapping]) -> Meeting:
    return Meeting(meeting_id="m1", city="Bloomington", date="2026-04-01", speakers=speakers)


def test_relink_matches_by_name_case_insensitive_and_sets_both_fields():
    m = _meeting({"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="steve hilton")})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == ["SPEAKER_00"]
    assert m.speakers["SPEAKER_00"].politician_id == "uuid-hilton"
    assert m.speakers["SPEAKER_00"].politician_slug == "steve-hilton"


def test_relink_sets_id_when_slug_is_none():
    m = _meeting({"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Steve Hilton")})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", None)
    assert changed == ["SPEAKER_00"]
    assert m.speakers["SPEAKER_00"].politician_id == "uuid-hilton"
    assert m.speakers["SPEAKER_00"].politician_slug is None


def test_relink_no_match_returns_empty_and_leaves_mappings_untouched():
    m = _meeting({"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Jane Doe")})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == []
    assert m.speakers["SPEAKER_00"].politician_id is None


def test_relink_already_linked_is_noop():
    m = _meeting({"SPEAKER_00": SpeakerMapping(
        speaker_label="SPEAKER_00", speaker_name="Steve Hilton",
        politician_id="uuid-hilton", politician_slug="steve-hilton",
    )})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == []


def test_relink_matches_multiple_labels_for_same_person():
    m = _meeting({
        "SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Steve Hilton"),
        "SPEAKER_03": SpeakerMapping(speaker_label="SPEAKER_03", speaker_name="Steve Hilton"),
    })
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert sorted(changed) == ["SPEAKER_00", "SPEAKER_03"]
