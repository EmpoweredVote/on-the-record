from src.models import Meeting, MeetingSummary, Segment, SpeakerMapping


def test_segment_to_dict_coerces_numpy_confidence_to_native_float():
    # scipy's cosine similarity yields numpy floats; to_dict must emit a plain
    # float so the serialized JSON/DB payload carries no numpy scalar. Use
    # `type(...) is float` (not isinstance) since numpy.float64 subclasses float.
    import numpy as np

    seg = Segment(segment_id=1, start_time=0.0, end_time=1.0,
                  speaker_label="A", confidence=np.float64(0.987))
    d = seg.to_dict()
    assert type(d["confidence"]) is float
    assert d["confidence"] == 0.987


def test_speaker_mapping_to_dict_coerces_numpy_confidence_to_native_float():
    import numpy as np

    sm = SpeakerMapping(speaker_label="A", confidence=np.float64(0.512))
    d = sm.to_dict()
    assert type(d["confidence"]) is float
    assert d["confidence"] == 0.512


def test_meeting_summary_highlights_field():
    ms = MeetingSummary(highlights=["item 1"])
    assert ms.highlights == ["item 1"]


def test_meeting_summary_from_dict_key_decisions_compat():
    d = {"key_decisions": ["vote passed"], "executive_summary": "...", "model": "", "generated_at": ""}
    ms = MeetingSummary.from_dict(d)
    assert ms.highlights == ["vote passed"]


def test_meeting_summary_to_dict_uses_highlights():
    ms = MeetingSummary(highlights=["vote passed"])
    d = ms.to_dict()
    assert "highlights" in d
    assert "key_decisions" not in d


def test_meeting_summary_from_dict_highlights_takes_precedence():
    d = {"highlights": ["new"], "key_decisions": ["old"], "executive_summary": "", "model": "", "generated_at": ""}
    ms = MeetingSummary.from_dict(d)
    assert ms.highlights == ["new"]


def test_meeting_clip_window_roundtrip():
    m = Meeting(
        meeting_id="m1", city="X", date="2026-06-28",
        clip_start_seconds=1380.0, clip_end_seconds=2880.0,
    )
    d = m.to_dict()
    assert d["clip_start_seconds"] == 1380.0
    assert d["clip_end_seconds"] == 2880.0
    back = Meeting.from_dict(d)
    assert back.clip_start_seconds == 1380.0
    assert back.clip_end_seconds == 2880.0


def test_meeting_clip_window_defaults_none():
    m = Meeting(meeting_id="m1", city="X", date="2026-06-28")
    assert m.clip_start_seconds is None
    assert m.clip_end_seconds is None
    back = Meeting.from_dict({"meeting_id": "m1", "city": "X", "date": "2026-06-28"})
    assert back.clip_start_seconds is None


def test_meeting_no_silent_classification_defaults():
    # Constructing without metadata must NOT invent council / Regular Session.
    m = Meeting(meeting_id="m1", city=None, date="2026-06-28")
    assert m.event_kind is None
    assert m.meeting_type is None


def test_meeting_from_dict_absent_fields_stay_none():
    back = Meeting.from_dict({"meeting_id": "m1", "city": None, "date": "2026-06-28"})
    assert back.event_kind is None
    assert back.meeting_type is None


def test_meeting_from_dict_preserves_real_values():
    d = {
        "meeting_id": "m1", "city": "Bloomington", "date": "2026-06-28",
        "meeting_type": "Regular Session", "event_kind": "council",
    }
    back = Meeting.from_dict(d)
    assert back.meeting_type == "Regular Session"
    assert back.event_kind == "council"


def test_processing_metadata_roundtrips_channel_and_chapters():
    from src.models import ProcessingMetadata

    meta = ProcessingMetadata(
        source_title="Some Video Title",
        source_channel="Brian Tyler Cohen",
        source_chapters=[{"start_time": 0.0, "end_time": 30.0, "title": "Intro"}],
    )
    restored = ProcessingMetadata.from_dict(meta.to_dict())
    assert restored.source_channel == "Brian Tyler Cohen"
    assert restored.source_chapters == [{"start_time": 0.0, "end_time": 30.0, "title": "Intro"}]


def test_processing_metadata_omits_unset_new_fields():
    from src.models import ProcessingMetadata

    d = ProcessingMetadata().to_dict()
    assert "source_channel" not in d
    assert "source_chapters" not in d
