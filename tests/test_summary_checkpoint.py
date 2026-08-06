"""Summary resolution on a resumed run: embedded copy beats summary.json.

The summary lives in two places on disk — the standalone summary.json stage
checkpoint and the copy embedded in transcript_named.json. Segment-renumbering
backfills rewrite only the embedded copy, so summary.json can hold section
boundaries that point at the pre-backfill numbering. The resumed full pipeline
publishes whatever it loads here, so it must prefer the embedded copy.
"""

import json

from src.models import Meeting, MeetingSummary, SummarySection
from run_local import _load_summary_checkpoint


def _summary(*, exec_summary: str, end_segment: int) -> MeetingSummary:
    return MeetingSummary(
        executive_summary=exec_summary,
        highlights=["h"],
        sections=[
            SummarySection(
                section_type="discussion",
                title="Item 1",
                content="body",
                start_segment=0,
                end_segment=end_segment,
            )
        ],
        model="test-model",
        generated_at="2026-08-06T00:00:00Z",
    )


def _meeting(summary: MeetingSummary | None) -> Meeting:
    return Meeting(meeting_id="m1", city="Bloomington", date="2026-08-06", summary=summary)


def test_stale_checkpoint_does_not_replace_embedded_summary(tmp_path):
    """The drift that published pre-merge section boundaries for live meetings."""
    stale = _summary(exec_summary="pre-merge", end_segment=412)
    fresh = _summary(exec_summary="post-merge", end_segment=137)
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(stale.to_dict(), indent=2))

    meeting = _meeting(fresh)
    _load_summary_checkpoint(meeting, summary_path)

    assert meeting.summary.sections[0].end_segment == 137
    assert meeting.summary.executive_summary == "post-merge"


def test_stale_checkpoint_is_resynced_from_embedded_summary(tmp_path):
    stale = _summary(exec_summary="pre-merge", end_segment=412)
    fresh = _summary(exec_summary="post-merge", end_segment=137)
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(stale.to_dict(), indent=2))

    _load_summary_checkpoint(_meeting(fresh), summary_path)

    assert json.loads(summary_path.read_text()) == fresh.to_dict()


def test_missing_checkpoint_is_written_from_embedded_summary(tmp_path):
    """summary.json is the SUMMARIZED stage marker; don't leave it absent."""
    fresh = _summary(exec_summary="post-merge", end_segment=137)
    summary_path = tmp_path / "summary.json"

    _load_summary_checkpoint(_meeting(fresh), summary_path)

    assert json.loads(summary_path.read_text()) == fresh.to_dict()


def test_in_sync_checkpoint_is_left_untouched(tmp_path):
    summary = _summary(exec_summary="same", end_segment=137)
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary.to_dict(), indent=2))
    before = summary_path.stat().st_mtime_ns

    meeting = _meeting(_summary(exec_summary="same", end_segment=137))
    _load_summary_checkpoint(meeting, summary_path)

    assert summary_path.stat().st_mtime_ns == before
    assert meeting.summary.sections[0].end_segment == 137


def test_legacy_key_names_in_checkpoint_are_not_drift(tmp_path):
    """Older summary.json files spell highlights `key_decisions`.

    MeetingSummary.from_dict reads both, so such a checkpoint carries the same
    summary and must not be rewritten as though it had drifted. 15 of the local
    meetings are in this shape.
    """
    summary = _summary(exec_summary="same", end_segment=137)
    legacy = summary.to_dict()
    legacy["key_decisions"] = legacy.pop("highlights")
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(legacy, indent=2))
    before = summary_path.stat().st_mtime_ns

    _load_summary_checkpoint(_meeting(_summary(exec_summary="same", end_segment=137)), summary_path)

    assert summary_path.stat().st_mtime_ns == before
    assert "key_decisions" in json.loads(summary_path.read_text())


def test_checkpoint_loaded_when_transcript_has_no_embedded_summary(tmp_path):
    """Back-compat: meetings summarized before the embedded copy existed."""
    checkpoint = _summary(exec_summary="only copy", end_segment=137)
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(checkpoint.to_dict(), indent=2))

    meeting = _meeting(None)
    _load_summary_checkpoint(meeting, summary_path)

    assert meeting.summary is not None
    assert meeting.summary.executive_summary == "only copy"
    assert meeting.summary.sections[0].end_segment == 137


def test_no_summary_anywhere_is_not_an_error(tmp_path):
    meeting = _meeting(None)
    _load_summary_checkpoint(meeting, tmp_path / "summary.json")

    assert meeting.summary is None
    assert not (tmp_path / "summary.json").exists()
