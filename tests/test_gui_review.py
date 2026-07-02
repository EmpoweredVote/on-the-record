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


import json

import pytest

from gui.review_api import find_meeting_media, load_review_page


def _write_meeting(mdir, *, clip_start=None):
    """Write a transcript_named.json with 2 speakers (one confident, one not)."""
    from src.models import Meeting, Segment, SpeakerMapping

    segs = [
        Segment(segment_id=0, start_time=10.0, end_time=70.0, speaker_label="SPEAKER_00",
                text="Good evening and welcome to the council meeting.", speaker_name="Mayor Johnson"),
        Segment(segment_id=1, start_time=80.0, end_time=95.0, speaker_label="SPEAKER_01",
                text="Point of order.", speaker_name=None),
    ]
    speakers = {
        "SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Mayor Johnson",
                                     confidence=0.95, id_method="voice"),
        "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01", speaker_name=None, confidence=0.0),
    }
    meeting = Meeting(meeting_id=mdir.name, city="Bloomington", date="2026-02-04",
                      meeting_type="Regular Session", event_kind="council",
                      segments=segs, speakers=speakers, clip_start_seconds=clip_start)
    (mdir / "transcript_named.json").write_text(json.dumps(meeting.to_dict()))


def test_find_meeting_media_prefers_video_then_audio(tmp_path):
    assert find_meeting_media(tmp_path) is None
    (tmp_path / "audio.wav").write_bytes(b"RIFF")
    assert find_meeting_media(tmp_path) == ("audio", "audio.wav")
    (tmp_path / "source.mp4").write_bytes(b"\x00\x00")
    assert find_meeting_media(tmp_path) == ("video", "source.mp4")


def test_load_review_page_missing_meeting_returns_none(tmp_meetings_dir):
    assert load_review_page("nope") is None
    assert load_review_page("../escape") is None  # unsafe id


def test_load_review_page_groups_and_orders(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    page = load_review_page("2026-02-04-council")
    assert page is not None
    assert page.display_name == "Council" or "Bloomington" in page.display_name
    # SPEAKER_00 is named @0.95 -> confirmed; SPEAKER_01 unnamed -> needs attention.
    assert [c.label for c in page.confirmed] == ["SPEAKER_00"]
    assert [c.label for c in page.needs_attention] == ["SPEAKER_01"]
    assert page.media_kind is None  # no media files written


def test_load_review_page_computes_audio_seeks_cliplocal(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    (mdir / "audio.wav").write_bytes(b"RIFF")
    page = load_review_page("2026-02-04-council")
    assert page.media_kind == "audio"
    # SPEAKER_00's longest turn starts at 10.0; audio is clip-local, 3s lead-in -> 7.0
    conf = page.confirmed[0]
    assert conf.clip_seeks and conf.clip_seeks[0] == pytest.approx(7.0)


def test_load_review_page_video_seeks_add_clip_offset(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir, clip_start=600.0)
    (mdir / "source.mp4").write_bytes(b"\x00")
    page = load_review_page("2026-02-04-council")
    assert page.media_kind == "video"
    # video is full source: seek = max(0, 10-3) + 600 = 607.0
    assert page.confirmed[0].clip_seeks[0] == pytest.approx(607.0)
