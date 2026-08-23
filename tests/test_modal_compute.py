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


# --- chunking is only enabled for validated meeting kinds -------------------

def test_validated_kinds_keep_their_chunk_size():
    from src.modal_compute import chunk_minutes_for_kind
    assert chunk_minutes_for_kind("council", 60) == 60
    assert chunk_minutes_for_kind("floor", 60) == 60


def test_a_dense_debate_is_not_chunked():
    """Measured on the LA mayoral debate (~33 people in 106 min): chunking gave
    29 labels for 33 people, and 14 window-local labels already spanned two
    people BEFORE any cross-window step. That is pyannote clustering inside a
    60-minute window, so no threshold fixes it — the meeting must not chunk."""
    from src.modal_compute import chunk_minutes_for_kind
    assert chunk_minutes_for_kind("debate", 60) == 0
    assert chunk_minutes_for_kind("forum", 60) == 0


def test_an_unknown_or_missing_kind_is_not_chunked():
    """Unvalidated kinds take the untouched single-pass path — the same speed as
    before chunking existed, so declining to chunk can never be a regression."""
    from src.modal_compute import chunk_minutes_for_kind
    assert chunk_minutes_for_kind(None, 60) == 0
    assert chunk_minutes_for_kind("", 60) == 0
    assert chunk_minutes_for_kind("podcast", 60) == 0
    assert chunk_minutes_for_kind("some_future_kind", 60) == 0


def test_the_kind_gate_never_turns_chunking_on():
    from src.modal_compute import chunk_minutes_for_kind
    assert chunk_minutes_for_kind("council", 0) == 0


def test_the_allowlist_is_overridable_for_a_deliberate_experiment():
    from src.modal_compute import chunk_minutes_for_kind
    assert chunk_minutes_for_kind("debate", 60, allowed=("debate",)) == 60
