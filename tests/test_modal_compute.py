"""Window planning for chunked diarization (pure; no Modal, no network)."""
from src.modal_compute import plan_chunk_windows


def test_short_audio_is_a_single_window():
    assert plan_chunk_windows(1800.0, chunk_minutes=30) == [(0.0, 1800.0)]


def test_windows_tile_the_audio_without_gaps_or_overlap():
    windows = plan_chunk_windows(9000.0, chunk_minutes=30)  # 2.5 hours
    assert windows[0][0] == 0.0
    assert windows[-1][1] == 9000.0
    assert len(windows) == 5
    for (prev_start, prev_end), (next_start, _) in zip(windows, windows[1:]):
        assert prev_end == next_start  # canonical spans abut exactly


def test_short_trailing_remainder_folds_into_the_previous_window():
    """A 61-minute meeting must not produce a 1-minute window whose speakers
    can barely be embedded."""
    windows = plan_chunk_windows(3660.0, chunk_minutes=30)
    assert windows == [(0.0, 1800.0), (1800.0, 3660.0)]


def test_chunking_disabled_returns_one_window():
    assert plan_chunk_windows(9000.0, chunk_minutes=0) == [(0.0, 9000.0)]
