from src.models import Segment
from src.vtt_align import align_vtt_to_segments, parse_vtt


def test_parse_vtt_collapses_expanding_lines_within_a_cue(tmp_path):
    vtt = tmp_path / "captions.vtt"
    vtt.write_text(
        """WEBVTT

00:00:10.000 --> 00:00:14.000
only 13 homes are completed
only 13 homes are completed and ready
""",
        encoding="utf-8",
    )

    assert parse_vtt(vtt)[0]["text"] == "only 13 homes are completed and ready"


def test_rolling_vtt_captions_flow_once_across_speakers(tmp_path):
    vtt = tmp_path / "captions.vtt"
    vtt.write_text(
        """WEBVTT

00:00:10.000 --> 00:00:14.000
only 13 homes are completed and ready

00:00:12.000 --> 00:00:16.000
only 13 homes are completed and ready for move-in

00:00:15.000 --> 00:00:18.000
for move-in Do you have a permit

00:00:17.000 --> 00:00:20.000
Do you have a permit No
""",
        encoding="utf-8",
    )
    segments = [
        Segment(0, 10.0, 18.0, "SPEAKER_00"),
        Segment(1, 18.0, 21.0, "SPEAKER_01"),
    ]

    result = align_vtt_to_segments(vtt, segments)

    combined = " ".join(seg.text for seg in result)
    assert combined == (
        "only 13 homes are completed and ready for move-in "
        "Do you have a permit No"
    )
    assert result[0].text == (
        "only 13 homes are completed and ready for move-in "
        "Do you have a permit"
    )
    assert result[1].text == "No"


def test_repeated_text_in_separate_cues_is_preserved(tmp_path):
    vtt = tmp_path / "captions.vtt"
    vtt.write_text(
        """WEBVTT

00:00:10.000 --> 00:00:11.000
No

00:00:20.000 --> 00:00:21.000
No
""",
        encoding="utf-8",
    )
    segments = [Segment(0, 0.0, 30.0, "SPEAKER_00")]

    result = align_vtt_to_segments(vtt, segments)

    assert result[0].text == "No No"


def test_repeated_text_in_adjacent_cues_is_preserved(tmp_path):
    vtt = tmp_path / "captions.vtt"
    vtt.write_text(
        """WEBVTT

00:00:10.000 --> 00:00:11.000
No

00:00:11.000 --> 00:00:12.000
No
""",
        encoding="utf-8",
    )
    segments = [Segment(0, 0.0, 30.0, "SPEAKER_00")]

    result = align_vtt_to_segments(vtt, segments)

    assert result[0].text == "No No"


def test_youtube_rolling_captions_with_snapshot_cues_are_deduplicated(tmp_path):
    vtt = tmp_path / "captions.vtt"
    vtt.write_text(
        """WEBVTT

00:00:13.840 --> 00:00:15.790 align:start position:0%
&gt;&gt; And<00:00:14.200><c> welcome</c><00:00:14.520><c> to</c><00:00:14.640><c> the</c><00:00:14.760><c> beautiful</c><00:00:15.280><c> Skirball</c>

00:00:15.790 --> 00:00:15.800 align:start position:0%
&gt;&gt; And welcome to the beautiful Skirball

00:00:15.800 --> 00:00:17.590 align:start position:0%
&gt;&gt; And welcome to the beautiful Skirball
Cultural<00:00:16.320><c> Center,</c><00:00:16.640><c> a</c><00:00:16.760><c> place</c><00:00:17.120><c> that</c><00:00:17.280><c> brings</c>

00:00:17.590 --> 00:00:17.600 align:start position:0%
Cultural Center, a place that brings

00:00:17.600 --> 00:00:19.710 align:start position:0%
Cultural Center, a place that brings
people<00:00:17.960><c> and</c><00:00:18.080><c> communities</c><00:00:18.720><c> together.</c><00:00:19.520><c> Good</c>
""",
        encoding="utf-8",
    )
    segments = [Segment(0, 13.0, 20.0, "SPEAKER_00")]

    result = align_vtt_to_segments(vtt, segments)

    assert result[0].text == (
        ">> And welcome to the beautiful Skirball Cultural Center, "
        "a place that brings people and communities together. Good"
    )
