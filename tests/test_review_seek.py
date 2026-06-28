"""Review-clip seek math: when reviewing a clipped meeting, the local video file
is the FULL source, so seeking a clip-local segment time must add the clip offset.
Clip audio (audio.wav) is already clip-local, so it passes offset 0."""

from run_local import _review_seek


def test_review_seek_adds_offset_for_full_source_video():
    # clip-local 922s into a full-episode video whose clip starts at 4049s
    # -> episode 4968s (922 - 3s lead-in + 4049).
    assert _review_seek(922.0, 4049.0) == 4968.0


def test_review_seek_clip_local_when_no_offset():
    # No clip (or playing the clip-local audio): 3s of lead-in, clip-local.
    assert _review_seek(922.0, 0.0) == 919.0


def test_review_seek_floors_lead_in_at_zero_before_offset():
    assert _review_seek(1.0, 0.0) == 0.0
    assert _review_seek(1.0, 4049.0) == 4049.0
