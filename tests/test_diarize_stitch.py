"""Cross-chunk speaker unification tests.

The stitcher's job: chunk-local SPEAKER_xx labels are meaningless across
chunks, so match them by centroid similarity into a global label space.
The load-bearing rule is one-to-one per chunk — two speakers the chunk's own
clustering called distinct must never collapse into one global speaker.
"""
import numpy as np

from src.diarize_stitch import ChunkResult, stitch_chunks


def _unit(*values) -> list[float]:
    vec = np.array(values, dtype=float)
    return list(vec / np.linalg.norm(vec))


# Three mutually dissimilar voices (orthogonal → cosine similarity 0).
ALICE = _unit(1, 0, 0)
BOB = _unit(0, 1, 0)
CAROL = _unit(0, 0, 1)
# Alice with a little noise: still clearly Alice (similarity ~0.997).
ALICE_NOISY = _unit(1, 0.08, 0)


def test_same_voice_across_chunks_gets_one_global_label():
    chunks = [
        ChunkResult(
            start_s=0.0, end_s=60.0,
            turns=[(0.0, 30.0, "SPEAKER_00")],
            centroids={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
        ChunkResult(
            start_s=60.0, end_s=120.0,
            turns=[(60.0, 90.0, "SPEAKER_00")],  # local label reused, same person
            centroids={"SPEAKER_00": ALICE_NOISY},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
    ]
    turns, centroids, log = stitch_chunks(chunks, threshold=0.8)
    assert len({label for _, _, label in turns}) == 1
    assert len(centroids) == 1
    assert [t[0] for t in turns] == [0.0, 60.0]  # absolute times preserved, ordered


def test_different_voices_reusing_the_same_local_label_stay_separate():
    chunks = [
        ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 30.0}),
        ChunkResult(60.0, 120.0, [(60.0, 90.0, "SPEAKER_00")], {"SPEAKER_00": BOB},
                    {"SPEAKER_00": 30.0}),
    ]
    turns, centroids, log = stitch_chunks(chunks, threshold=0.8)
    assert len(centroids) == 2
    assert len({label for _, _, label in turns}) == 2


def test_one_to_one_two_locals_never_collapse_into_one_global():
    """The correctness constraint: chunk 2 has two speakers who both look like
    Alice; only the better match may take Alice's global label."""
    chunks = [
        ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 30.0}),
        ChunkResult(
            60.0, 120.0,
            turns=[(60.0, 70.0, "SPEAKER_00"), (70.0, 80.0, "SPEAKER_01")],
            centroids={"SPEAKER_00": ALICE, "SPEAKER_01": ALICE_NOISY},
            speech_seconds={"SPEAKER_00": 10.0, "SPEAKER_01": 10.0},
        ),
    ]
    turns, centroids, log = stitch_chunks(chunks, threshold=0.8)
    # Two distinct labels within chunk 2 must remain two distinct globals.
    chunk2 = [label for start, _, label in turns if start >= 60.0]
    assert len(set(chunk2)) == 2
    assert len(centroids) == 2


def test_below_threshold_creates_a_new_global_speaker():
    chunks = [
        ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 30.0}),
        ChunkResult(60.0, 120.0, [(60.0, 90.0, "SPEAKER_00")], {"SPEAKER_00": CAROL},
                    {"SPEAKER_00": 30.0}),
    ]
    turns, centroids, _ = stitch_chunks(chunks, threshold=0.8)
    assert len(centroids) == 2


def test_global_labels_are_canonical_and_time_ordered():
    chunks = [
        ChunkResult(60.0, 120.0, [(60.0, 90.0, "SPEAKER_00")], {"SPEAKER_00": BOB},
                    {"SPEAKER_00": 30.0}),
        ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 30.0}),
    ]
    turns, centroids, _ = stitch_chunks(chunks, threshold=0.8)
    # Chunks may arrive out of order; output is sorted by time and the first
    # speaker seen chronologically is SPEAKER_00.
    assert [t[0] for t in turns] == [0.0, 60.0]
    assert turns[0][2] == "SPEAKER_00"
    assert sorted(centroids) == ["SPEAKER_00", "SPEAKER_01"]


def test_centroids_are_duration_weighted():
    """A 10s appearance must not drag a global centroid as much as a 300s one."""
    chunks = [
        ChunkResult(0.0, 60.0, [(0.0, 300.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 300.0}),
        ChunkResult(60.0, 120.0, [(60.0, 70.0, "SPEAKER_00")],
                    {"SPEAKER_00": ALICE_NOISY}, {"SPEAKER_00": 10.0}),
    ]
    _, centroids, _ = stitch_chunks(chunks, threshold=0.8)
    merged = np.array(centroids["SPEAKER_00"])
    # Much closer to pure Alice than to the noisy sample.
    assert float(merged @ np.array(ALICE)) > float(merged @ np.array(ALICE_NOISY))


def test_empty_and_single_chunk_inputs():
    assert stitch_chunks([], threshold=0.8) == ([], {}, [])
    one = ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                      {"SPEAKER_00": 30.0})
    turns, centroids, _ = stitch_chunks([one], threshold=0.8)
    assert turns == [(0.0, 30.0, "SPEAKER_00")]
    assert list(centroids) == ["SPEAKER_00"]


def test_turn_without_a_centroid_is_kept_under_a_fresh_label():
    """A chunk can emit a turn for a speaker too short to embed; the turn is
    real audio and must not be silently dropped."""
    chunk = ChunkResult(
        0.0, 60.0,
        turns=[(0.0, 30.0, "SPEAKER_00"), (30.0, 30.2, "SPEAKER_09")],
        centroids={"SPEAKER_00": ALICE},
        speech_seconds={"SPEAKER_00": 30.0, "SPEAKER_09": 0.2},
    )
    turns, centroids, log = stitch_chunks([chunk], threshold=0.8)
    assert len(turns) == 2
    assert len({label for _, _, label in turns}) == 2
    assert any("no centroid" in line for line in log)
