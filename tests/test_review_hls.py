"""load_review_page exposes an hls_url for HLS (.m3u8) sources (e.g. House Clerk
CDN), treats it as a full-source stream for seek math, and leaves non-HLS
meetings unchanged."""
from __future__ import annotations

import json

from gui.review_api import load_review_page
from src.models import Meeting, Segment, SpeakerMapping


def _seg(label, start, end, text="x"):
    return Segment(segment_id=0, start_time=start, end_time=end,
                   speaker_label=label, text=text)


def _write_meeting(mdir, mid, *, source_audio_url=None, audio_source="",
                   clip_start=None):
    """Write the on-disk files load_review_page reads. Returns nothing."""
    m0 = SpeakerMapping(speaker_label="SPEAKER_00")
    m0.speaker_name = "Rep. Smith"; m0.confidence = 1.0
    meeting = Meeting(meeting_id=mid, city=None, date="2026-07-16",
                      meeting_type="House Floor", audio_source=audio_source)
    meeting.event_kind = "floor"
    meeting.segments = [_seg("SPEAKER_00", 120.0, 150.0, "hello")]
    meeting.speakers = {"SPEAKER_00": m0}
    if source_audio_url is not None:
        meeting.processing_metadata.source_audio_url = source_audio_url
    if clip_start is not None:
        meeting.clip_start_seconds = clip_start
    (mdir / "transcript_named.json").write_text(
        json.dumps(meeting.to_dict()), encoding="utf-8")
    (mdir / "diarization.json").write_text(
        json.dumps([s.to_dict() for s in meeting.segments]), encoding="utf-8")
    (mdir / "embeddings.json").write_text(
        json.dumps({"SPEAKER_00": [1.0, 0.0]}), encoding="utf-8")
    (mdir / "audio.wav").write_bytes(b"")


def test_hls_source_sets_hls_url(tmp_meetings_dir):
    mid = "2026-07-16-house-floor"
    mdir = tmp_meetings_dir / mid; mdir.mkdir(parents=True)
    url = "https://houseliveprod.example.net/east/2026-07-16/manifest.m3u8"
    _write_meeting(mdir, mid, source_audio_url=url,
                   audio_source="https://live.house.gov/?date=2026-07-16")

    page = load_review_page(mid)

    assert page is not None
    assert page.hls_url == url
    assert page.youtube_id is None


def test_hls_seeks_carry_clip_offset(tmp_meetings_dir):
    # HLS is a full-source stream, so a clip-local candidate must add the offset.
    mid = "clipped-hls"
    mdir = tmp_meetings_dir / mid; mdir.mkdir(parents=True)
    _write_meeting(mdir, mid,
                   source_audio_url="https://x.example/manifest.m3u8",
                   clip_start=1000.0)

    page = load_review_page(mid)

    # segment start 120.0 -> base 117.0 (3s lead-in) -> +1000.0 offset = 1117.0
    card = (page.needs_attention + page.confirmed)[0]
    assert card.clip_seeks[0] == 1117.0


def test_no_hls_source_leaves_hls_url_none(tmp_meetings_dir):
    mid = "podcast-meeting"
    mdir = tmp_meetings_dir / mid; mdir.mkdir(parents=True)
    _write_meeting(mdir, mid, source_audio_url=None,
                   audio_source="https://example.com/episode-page")

    page = load_review_page(mid)

    assert page.hls_url is None
    assert page.media_kind == "audio"  # falls through to the local audio.wav
