"""Tests for the markdown export metadata block (source link, duration, pipeline)."""

from src.export import export_markdown, export_summary_markdown
from src.models import Meeting, MeetingSummary, ProcessingMetadata, Segment


def _meeting(**kwargs) -> Meeting:
    defaults = dict(
        meeting_id="2026-02-10-regular-session",
        city="Bloomington",
        date="2026-02-10",
        meeting_type="Regular Session",
        audio_source="https://www.youtube.com/watch?v=AbC123",
        duration_seconds=8076.0,
        segments=[
            Segment(
                segment_id=0,
                start_time=0.0,
                end_time=5.0,
                speaker_label="SPEAKER_00",
                speaker_name="John Smith",
                text="Good evening everyone.",
            )
        ],
        processing_metadata=ProcessingMetadata(
            pipeline_version="1.0.0",
            diarization_model="pyannote/speaker-diarization-3.1",
            transcription_model="large-v3",
        ),
    )
    defaults.update(kwargs)
    return Meeting(**defaults)


def test_markdown_includes_source_link_for_url(tmp_path):
    path = export_markdown(_meeting(), tmp_path / "transcript.md")
    content = path.read_text(encoding="utf-8")
    assert (
        "- **Source:** [https://www.youtube.com/watch?v=AbC123]"
        "(https://www.youtube.com/watch?v=AbC123)" in content
    )
    assert "- **Duration:** 02:14:36" in content
    assert "- **Meeting ID:** 2026-02-10-regular-session" in content
    assert (
        "- **Pipeline:** whisper large-v3 · pyannote/speaker-diarization-3.1"
        " · CouncilScribe v1.0.0" in content
    )
    # Metadata block sits between the H1 and the first segment
    assert content.index("# Bloomington") < content.index("**Source:**")
    assert content.index("**Source:**") < content.index("Good evening")


def test_markdown_local_path_renders_basename_without_link(tmp_path):
    meeting = _meeting(audio_source="/Users/operator/Downloads/meeting video.mp4")
    content = export_markdown(meeting, tmp_path / "transcript.md").read_text(
        encoding="utf-8"
    )
    assert "- **Source file:** meeting video.mp4" in content
    assert "/Users/operator" not in content
    assert "**Source:**" not in content  # no link variant


def test_markdown_omits_source_line_when_empty(tmp_path):
    meeting = _meeting(audio_source="", duration_seconds=0.0)
    content = export_markdown(meeting, tmp_path / "transcript.md").read_text(
        encoding="utf-8"
    )
    assert "Source" not in content
    assert "Duration" not in content
    # Pipeline line still present, so the block separator should be too
    assert "- **Pipeline:**" in content


def test_markdown_no_metadata_at_all(tmp_path):
    meeting = _meeting(
        audio_source="",
        duration_seconds=0.0,
        meeting_id="",
        processing_metadata=ProcessingMetadata(
            pipeline_version="", diarization_model="", transcription_model=""
        ),
    )
    content = export_markdown(meeting, tmp_path / "transcript.md").read_text(
        encoding="utf-8"
    )
    assert "---" not in content
    assert "Good evening everyone." in content


def test_summary_markdown_includes_metadata_block(tmp_path):
    meeting = _meeting()
    summary = MeetingSummary(executive_summary="A productive meeting.")
    content = export_summary_markdown(
        summary, meeting, tmp_path / "summary.md"
    ).read_text(encoding="utf-8")
    assert (
        "- **Source:** [https://www.youtube.com/watch?v=AbC123]"
        "(https://www.youtube.com/watch?v=AbC123)" in content
    )
    assert "- **Duration:** 02:14:36" in content
    assert "A productive meeting." in content


from src.export import export_all
from src.models import Meeting, Segment


def test_export_all_shifts_timestamps_into_source_timeline(tmp_path):
    m = Meeting(
        meeting_id="m1", city="X", date="2026-06-28",
        duration_seconds=20.0,
        clip_start_seconds=1380.0, clip_end_seconds=1400.0,
        segments=[Segment(0, 0.0, 10.0, "S0", text="hello world")],
    )
    export_all(m, tmp_path)
    srt = (tmp_path / "subtitles.srt").read_text()
    # 1380s == 00:23:00 — the subtitle must align to the full episode, not 0:00.
    assert "00:23:00" in srt
    assert "00:00:00,000 -->" not in srt
