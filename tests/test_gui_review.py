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


from fastapi.testclient import TestClient

from gui.app import create_app


def test_review_route_renders_groups(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())
    resp = client.get("/meetings/2026-02-04-council/review")
    assert resp.status_code == 200
    body = resp.text
    assert "Mayor Johnson" in body            # confirmed speaker
    assert "SPEAKER_01" in body               # needs-attention label
    assert "Needs attention" in body and "Confirmed" in body


def test_review_route_404_for_unknown_meeting(tmp_meetings_dir):
    client = TestClient(create_app())
    assert client.get("/meetings/ghost/review").status_code == 404


def test_media_route_serves_audio_with_range(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    (mdir / "audio.wav").write_bytes(b"0123456789")
    client = TestClient(create_app())

    full = client.get("/meetings/2026-02-04-council/media")
    assert full.status_code == 200
    assert full.content == b"0123456789"

    part = client.get("/meetings/2026-02-04-council/media", headers={"Range": "bytes=0-3"})
    assert part.status_code == 206
    assert part.content == b"0123"


def test_media_route_404_when_no_media(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    client = TestClient(create_app())
    assert client.get("/meetings/2026-02-04-council/media").status_code == 404


def test_media_route_404_unsafe_id(tmp_meetings_dir):
    client = TestClient(create_app())
    assert client.get("/meetings/..%2Fx/media").status_code in (404, 400)


def test_review_page_has_media_player_and_clip_buttons(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    (mdir / "audio.wav").write_bytes(b"RIFF0000")
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    assert '/meetings/2026-02-04-council/media' in body   # media element src
    assert 'data-seek=' in body                            # at least one clip button
    assert 'review.js' in body                             # playback script wired


def test_library_links_to_review(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/").text
    assert 'href="/meetings/2026-02-04-council/review"' in body


def test_load_review_page_malformed_transcript_shape_returns_none(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    # Valid JSON but wrong shape: a list, not the Meeting object dict.
    (mdir / "transcript_named.json").write_text("[]")

    assert load_review_page("2026-02-04-council") is None
    resp = TestClient(create_app()).get("/meetings/2026-02-04-council/review")
    assert resp.status_code == 404


def test_load_review_page_malformed_embeddings_degrade_gracefully(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    # Valid JSON but wrong shape for embeddings (list, not a dict of label->vector).
    (mdir / "embeddings.json").write_text("[1, 2]")

    page = load_review_page("2026-02-04-council")
    assert page is not None  # bad embeddings degrade to "no hints", not a crash
    resp = TestClient(create_app()).get("/meetings/2026-02-04-council/review")
    assert resp.status_code == 200


from gui.review_api import _load_meeting_ctx, persist_review


def test_load_meeting_ctx_returns_none_for_unsafe_or_missing(tmp_meetings_dir):
    assert _load_meeting_ctx("../x") is None
    assert _load_meeting_ctx("ghost") is None


def test_persist_review_syncs_segments_and_writes_named(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    ctx = _load_meeting_ctx("2026-02-04-council")
    assert ctx is not None
    meeting, meeting_dir, _roster = ctx

    # Simulate a rename having happened on the mapping only.
    meeting.speakers["SPEAKER_01"].speaker_name = "Clerk Smith"
    meeting.speakers["SPEAKER_01"].confidence = 1.0
    meeting.speakers["SPEAKER_01"].id_method = "human_review"

    persist_review(meeting, meeting_dir)

    # transcript_named.json now carries the new name on BOTH mapping and segment.
    import json as _json
    data = _json.loads((meeting_dir / "transcript_named.json").read_text())
    assert data["speakers"]["SPEAKER_01"]["speaker_name"] == "Clerk Smith"
    seg01 = [s for s in data["segments"] if s["speaker_label"] == "SPEAKER_01"][0]
    assert seg01["speaker_name"] == "Clerk Smith"


def test_persist_review_recomputes_gate_quality_json(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    meeting, meeting_dir, _ = _load_meeting_ctx("2026-02-04-council")
    persist_review(meeting, meeting_dir)
    # Gate ran (best-effort): quality.json written and state mirrored.
    assert (meeting_dir / "quality.json").exists()
    from src.checkpoint import PipelineState
    assert PipelineState(meeting_dir).review_status in ("pass", "review", "failed")


from gui.review_api import apply_rename


def test_apply_rename_sets_name_and_confirms(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_rename("2026-02-04-council", "SPEAKER_01", "Clerk Smith") is True

    # Reload the page: SPEAKER_01 is now named + confident -> Confirmed group.
    page = load_review_page("2026-02-04-council")
    conf_labels = [c.label for c in page.confirmed]
    assert "SPEAKER_01" in conf_labels
    card = [c for c in page.confirmed if c.label == "SPEAKER_01"][0]
    assert card.name == "Clerk Smith"
    assert card.confidence == 1.0


def test_apply_rename_rejects_unknown_meeting_label_and_empty(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_rename("ghost", "SPEAKER_00", "X") is False          # no meeting
    assert apply_rename("2026-02-04-council", "SPEAKER_99", "X") is False  # unknown label
    assert apply_rename("2026-02-04-council", "SPEAKER_00", "   ") is False  # empty name
    assert apply_rename("../x", "SPEAKER_00", "X") is False           # unsafe id
