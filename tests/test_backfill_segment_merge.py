from __future__ import annotations

import json

from src.models import Meeting, Segment, SpeakerMapping, MeetingSummary, SummarySection
from backfill_segment_merge import remerge_meeting, backfill, reindex_summary_sections


def test_reindex_summary_sections_maps_times_to_merged_indices():
    segs = [
        Segment(segment_id=0, start_time=0.0, end_time=9.0, speaker_label="A", text="a"),
        Segment(segment_id=1, start_time=10.0, end_time=19.0, speaker_label="B", text="b"),
        Segment(segment_id=2, start_time=20.0, end_time=29.0, speaker_label="A", text="c"),
    ]
    sec = SummarySection(section_type="discussion", title="Mid", content="…",
                         start_time=10.0, end_time=19.0,
                         start_segment=99, end_segment=99)  # stale indices
    m = Meeting(meeting_id="m", city="X", date="2026-01-01", meeting_type="R",
                event_kind="council", segments=segs, speakers={},
                summary=MeetingSummary(executive_summary="", sections=[sec]))
    reindex_summary_sections(m)
    assert sec.start_segment == 1 and sec.end_segment == 1  # remapped to the middle segment


def test_reindex_summary_sections_no_summary_is_noop():
    m = Meeting(meeting_id="m", city="X", date="2026-01-01", meeting_type="R",
                event_kind="council",
                segments=[Segment(segment_id=0, start_time=0.0, end_time=1.0,
                                  speaker_label="A", text="a")], speakers={})
    reindex_summary_sections(m)  # must not raise


def _fragmented_meeting(meeting_id="2026-05-15-interview"):
    # Four consecutive same-speaker fragments + one other speaker.
    segs = [
        Segment(segment_id=0, start_time=0.0, end_time=3.0, speaker_label="S0",
                speaker_name="Bass", text="of this being"),
        Segment(segment_id=1, start_time=3.2, end_time=5.0, speaker_label="S0",
                speaker_name="Bass", text="the worst"),
        Segment(segment_id=2, start_time=5.1, end_time=7.0, speaker_label="S0",
                speaker_name="Bass", text="natural disaster"),
        Segment(segment_id=3, start_time=8.0, end_time=10.0, speaker_label="S1",
                speaker_name="Host", text="I see."),
    ]
    sec = SummarySection(section_type="discussion", title="Disaster", content="…",
                         start_time=0.0, end_time=7.0, start_segment=0, end_segment=2)
    return Meeting(meeting_id=meeting_id, city="LA", date="2026-05-15",
                   meeting_type="Interview", event_kind="news_clip",
                   segments=segs, speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name="Bass"),
                                            "S1": SpeakerMapping(speaker_label="S1", speaker_name="Host")},
                   summary=MeetingSummary(executive_summary="", sections=[sec]))


def test_remerge_meeting_collapses_and_reindexes():
    m = _fragmented_meeting()
    before, after = remerge_meeting(m)
    assert before == 4 and after == 2                    # 3 Bass fragments -> 1, Host stays
    assert m.segments[0].text == "of this being the worst natural disaster"
    # section still covers the Bass block, now a single merged segment (index 0)
    assert m.summary.sections[0].start_segment == 0
    assert m.summary.sections[0].end_segment == 0


def test_backfill_rewrites_transcript_named(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    mdir = tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=7)
    (mdir / "transcript_named.json").write_text(json.dumps(_fragmented_meeting().to_dict()))
    # keep the export step from doing real work / needing deps
    import backfill_segment_merge as bf
    monkeypatch.setattr(bf, "live_published_slugs", lambda: None, raising=False)

    changed = backfill(dry_run=False)
    assert changed == 1
    data = json.loads((mdir / "transcript_named.json").read_text())
    assert len(data["segments"]) == 2                    # persisted merged


def test_backfill_dry_run_writes_nothing(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=7)
    original = json.dumps(_fragmented_meeting().to_dict())
    (mdir / "transcript_named.json").write_text(original)

    assert backfill(dry_run=True) == 1                   # reports it would change
    assert (mdir / "transcript_named.json").read_text() == original  # untouched


def test_backfill_skips_already_merged(tagged_meeting_dir, tmp_meetings_dir):
    m = _fragmented_meeting()
    remerge_meeting(m)  # already merged
    mdir = tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=7)
    (mdir / "transcript_named.json").write_text(json.dumps(m.to_dict()))
    assert backfill(dry_run=False) == 0                  # nothing to do
