"""Tests for the diarizer-agnostic cross-chunk speaker reconciler.

Ports the still-relevant cases from the deleted superseded cross-chunk
stitcher's test suite onto the ChunkResult/ChunkWindow/LocalTurn shape used
by src.speaker_reconcile.reconcile_chunks, plus three cases a review of that
superseded stitcher showed matter: temporal-only matching with no embedding
at all, thin-evidence speakers not poisoning a global voiceprint, and
non-finite embeddings being rejected without crashing.
"""
import numpy as np

from src.speaker_reconcile import ChunkResult, ChunkWindow, LocalTurn, reconcile_chunks


def _unit(*values) -> np.ndarray:
    vec = np.array(values, dtype=float)
    return vec / np.linalg.norm(vec)


# Three mutually dissimilar voices (orthogonal -> cosine similarity 0).
ALICE = _unit(1, 0, 0)
BOB = _unit(0, 1, 0)
CAROL = _unit(0, 0, 1)
# Alice with a little noise: still clearly Alice (similarity ~0.997).
ALICE_NOISY = _unit(1, 0.08, 0)


def _speakers(result) -> set[str]:
    return {t.speaker for t in result.turns}


def test_same_voice_across_windows_gets_one_stable_label():
    """No temporal overlap between windows (no shared turns), so this only
    resolves via embedding similarity across the non-overlapping windows."""
    chunks = [
        ChunkResult(
            window=ChunkWindow(0, 0.0, 60.0),
            turns=[LocalTurn(0, 0.0, 30.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
        ChunkResult(
            window=ChunkWindow(1, 60.0, 120.0),
            turns=[LocalTurn(1, 60.0, 90.0, "SPEAKER_00")],  # local label reused
            embeddings={"SPEAKER_00": ALICE_NOISY},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
    ]
    result = reconcile_chunks(chunks, label_prefix="SPEAKER_")
    assert len(_speakers(result)) == 1
    starts = [t.start for t in result.turns]
    assert starts == sorted(starts)


def test_different_voices_reusing_the_same_local_label_stay_separate():
    chunks = [
        ChunkResult(
            window=ChunkWindow(0, 0.0, 60.0),
            turns=[LocalTurn(0, 0.0, 30.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
        ChunkResult(
            window=ChunkWindow(1, 60.0, 120.0),
            turns=[LocalTurn(1, 60.0, 90.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": BOB},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
    ]
    result = reconcile_chunks(chunks, label_prefix="SPEAKER_")
    assert len(_speakers(result)) == 2


def test_two_locals_in_one_window_never_collapse_into_one_global():
    """Correctness constraint: window 2 has two speakers who both look like
    Alice; only the better match may take Alice's global label."""
    chunks = [
        ChunkResult(
            window=ChunkWindow(0, 0.0, 60.0),
            turns=[LocalTurn(0, 0.0, 30.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
        ChunkResult(
            window=ChunkWindow(1, 120.0, 180.0),
            turns=[
                LocalTurn(1, 120.0, 130.0, "SPEAKER_00"),
                LocalTurn(1, 130.0, 140.0, "SPEAKER_01"),
            ],
            embeddings={"SPEAKER_00": ALICE, "SPEAKER_01": ALICE_NOISY},
            speech_seconds={"SPEAKER_00": 10.0, "SPEAKER_01": 10.0},
        ),
    ]
    result = reconcile_chunks(chunks, label_prefix="SPEAKER_")
    window2_speakers = {t.speaker for t in result.turns if t.chunk_index == 1}
    assert len(window2_speakers) == 2


def test_below_threshold_creates_a_new_speaker():
    chunks = [
        ChunkResult(
            window=ChunkWindow(0, 0.0, 60.0),
            turns=[LocalTurn(0, 0.0, 30.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
        ChunkResult(
            window=ChunkWindow(1, 60.0, 120.0),
            turns=[LocalTurn(1, 60.0, 90.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": CAROL},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
    ]
    result = reconcile_chunks(chunks, label_prefix="SPEAKER_")
    assert len(_speakers(result)) == 2
    assert len(result.diagnostics["new_speakers"]) == 2
    assert result.diagnostics["embedding_matches"] == []


def test_centroids_are_duration_weighted_observed_via_a_third_window():
    """A 10s appearance must not drag the running voiceprint as much as a
    300s one. Observed via a THIRD window: if the tiny second appearance had
    dominated the voiceprint, a window that matches the ORIGINAL (pure
    Alice) voice would fail to match; since it still matches, the average
    stayed close to the heavily-weighted first appearance."""
    chunks = [
        ChunkResult(
            window=ChunkWindow(0, 0.0, 60.0),
            turns=[LocalTurn(0, 0.0, 60.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 300.0},
        ),
        ChunkResult(
            window=ChunkWindow(1, 120.0, 180.0),
            turns=[LocalTurn(1, 120.0, 130.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": ALICE_NOISY},
            speech_seconds={"SPEAKER_00": 10.0},
        ),
        ChunkResult(
            window=ChunkWindow(2, 240.0, 300.0),
            turns=[LocalTurn(2, 240.0, 270.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
    ]
    result = reconcile_chunks(chunks, label_prefix="SPEAKER_")
    # All three windows' contributions collapse to one global speaker because
    # the running voiceprint never drifted far enough from pure Alice to miss
    # the third (pure-Alice) window's match.
    assert len(_speakers(result)) == 1


def test_empty_and_single_chunk_inputs():
    empty = reconcile_chunks([], label_prefix="SPEAKER_")
    assert empty.turns == []

    one = ChunkResult(
        window=ChunkWindow(0, 0.0, 60.0),
        turns=[LocalTurn(0, 0.0, 30.0, "SPEAKER_00")],
        embeddings={"SPEAKER_00": ALICE},
        speech_seconds={"SPEAKER_00": 30.0},
    )
    result = reconcile_chunks([one], label_prefix="SPEAKER_")
    assert [(t.start, t.end, t.speaker) for t in result.turns] == [
        (0.0, 30.0, "SPEAKER_00")
    ]


def test_turn_without_an_embedding_is_kept_under_a_fresh_label():
    """A window can emit a turn for a speaker too short to embed; the turn is
    real audio and must not be silently dropped."""
    chunk = ChunkResult(
        window=ChunkWindow(0, 0.0, 60.0),
        turns=[
            LocalTurn(0, 0.0, 30.0, "SPEAKER_00"),
            LocalTurn(0, 30.0, 30.2, "SPEAKER_09"),
        ],
        embeddings={"SPEAKER_00": ALICE},
        speech_seconds={"SPEAKER_00": 30.0, "SPEAKER_09": 0.2},
    )
    result = reconcile_chunks([chunk], label_prefix="SPEAKER_")
    assert len(result.turns) == 2
    assert len(_speakers(result)) == 2
    assert len(result.diagnostics["new_speakers"]) == 2


def test_seam_speaker_matched_temporally_with_no_embedding_at_all():
    """A speaker whose turns overlap the seam between two windows is matched
    by physical turn overlap even when it has NO embedding whatsoever —
    temporal matching must not require voice evidence."""
    result = reconcile_chunks(
        [
            ChunkResult(
                window=ChunkWindow(0, 0.0, 100.0),
                turns=[LocalTurn(0, 80.0, 100.0, "SPEAKER_00")],
            ),
            ChunkResult(
                window=ChunkWindow(1, 90.0, 190.0),
                turns=[LocalTurn(1, 90.0, 110.0, "SPEAKER_00")],
            ),
        ],
        label_prefix="SPEAKER_",
    )
    assert len(_speakers(result)) == 1
    assert result.diagnostics["temporal_matches"] == [
        {"chunk": 1, "local": "SPEAKER_00", "global": "SPEAKER_00"}
    ]
    assert result.diagnostics["embedding_matches"] == []


def test_thin_evidence_speaker_does_not_poison_the_global_voiceprint():
    """A speaker with under MIN_EMBEDDING_SPEECH_SECONDS of speech must not
    be folded into a global voiceprint, even when it DOES get attached to
    that global (via temporal matching, which has no speech-duration floor).

    Window 1's "SPEAKER_00" turn physically overlaps window 0's in the seam,
    so it is matched to the same global by turn overlap alone regardless of
    its embedding or duration. Its embedding (Carol, near-orthogonal to
    Alice) has only 2.9s of speech -- under the 3.0s floor -- so it must NOT
    be blended into the running voiceprint. The math is arranged so the two
    behaviours are numerically distinguishable: blending in 2.9s of Carol
    against 3.0s of Alice drags the average to a Alice-cosine of ~0.719,
    which is BELOW the 0.75 match threshold, while leaving the voiceprint as
    pure Alice keeps it at 1.0. Window 2 (a distant, non-overlapping, clearly
    Alice window) can only resolve via that voiceprint, so its outcome
    reveals which happened.
    """
    chunks = [
        ChunkResult(
            window=ChunkWindow(0, 0.0, 60.0),
            turns=[LocalTurn(0, 40.0, 60.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 3.0},
        ),
        # Overlaps window 0 in [50, 55] -> matched temporally, not by embedding.
        ChunkResult(
            window=ChunkWindow(1, 50.0, 150.0),
            turns=[LocalTurn(1, 50.0, 55.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": CAROL},
            speech_seconds={"SPEAKER_00": 2.9},  # under the 3.0s floor
        ),
        # Far away, no temporal overlap with window 1: must resolve by
        # embedding against whatever the running voiceprint became.
        ChunkResult(
            window=ChunkWindow(2, 300.0, 360.0),
            turns=[LocalTurn(2, 300.0, 330.0, "SPEAKER_00")],
            embeddings={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
    ]
    result = reconcile_chunks(chunks, label_prefix="SPEAKER_")
    # Window 1 matched window 0 temporally (confirms the overlap fired at all).
    assert result.diagnostics["temporal_matches"] == [
        {"chunk": 1, "local": "SPEAKER_00", "global": "SPEAKER_00"}
    ]
    # The voiceprint stayed pure Alice, so window 2 matches the same global
    # as windows 0 and 1 -- one speaker throughout, not two.
    assert len(_speakers(result)) == 1


def test_non_finite_embedding_is_rejected_without_crashing():
    """A NaN/inf embedding must not crash cosine similarity or get folded
    into a global voiceprint; the speaker still opens a (new) stable label."""
    result = reconcile_chunks(
        [
            ChunkResult(
                window=ChunkWindow(0, 0.0, 100.0),
                turns=[LocalTurn(0, 0.0, 20.0, "SPEAKER_00")],
                embeddings={"SPEAKER_00": np.array([1.0, 0.0])},
                speech_seconds={"SPEAKER_00": 20.0},
            ),
            ChunkResult(
                window=ChunkWindow(1, 200.0, 300.0),
                turns=[LocalTurn(1, 220.0, 240.0, "SPEAKER_00")],
                embeddings={"SPEAKER_00": np.array([np.nan, np.inf])},
                speech_seconds={"SPEAKER_00": 20.0},
            ),
        ],
        label_prefix="SPEAKER_",
    )
    assert len(_speakers(result)) == 2
    assert result.diagnostics["embedding_matches"] == []


def test_a_sub_second_seam_overlap_does_not_force_a_temporal_match():
    """Temporal matching is a MUST-LINK: it is applied before, and independently
    of, the embedding threshold, so a wrong one cannot be tuned away. Measured on
    the May 6 council meeting (via the pyannote chunked path, which shares this
    reconciler): of 12 seam joins the 10 correct ones overlapped 1.1-71.0s, while
    the 2 that merged DIFFERENT people overlapped 0.6s and 0.3s and chained three
    real people into one cluster at every threshold tested. Two distinct voices
    below the floor must stay distinct and let voice similarity decide."""
    result = reconcile_chunks(
        [
            ChunkResult(
                window=ChunkWindow(0, 0.0, 100.0),
                turns=[LocalTurn(0, 80.0, 90.3, "SPEAKER_00")],
                embeddings={"SPEAKER_00": ALICE},
                speech_seconds={"SPEAKER_00": 10.3},
            ),
            ChunkResult(
                window=ChunkWindow(1, 90.0, 190.0),
                turns=[LocalTurn(1, 90.0, 110.0, "SPEAKER_00")],
                embeddings={"SPEAKER_00": BOB},
                speech_seconds={"SPEAKER_00": 20.0},
            ),
        ],
        label_prefix="SPEAKER_",
    )
    assert len(_speakers(result)) == 2
    assert result.diagnostics["temporal_matches"] == []


def test_the_seam_overlap_floor_is_configurable():
    chunks = [
        ChunkResult(
            window=ChunkWindow(0, 0.0, 100.0),
            turns=[LocalTurn(0, 80.0, 92.0, "SPEAKER_00")],
        ),
        ChunkResult(
            window=ChunkWindow(1, 90.0, 190.0),
            turns=[LocalTurn(1, 90.0, 110.0, "SPEAKER_00")],
        ),
    ]
    assert len(_speakers(reconcile_chunks(chunks, min_seam_overlap_seconds=1.0))) == 1
    assert len(_speakers(reconcile_chunks(chunks, min_seam_overlap_seconds=5.0))) == 2
