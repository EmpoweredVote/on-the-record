"""Tests for global cross-window speaker identity clustering.

The chunked diarization path fans 60-minute windows across Modal containers;
each window's SPEAKER_NN labels are only meaningful inside that window. This
module replaces per-window centroid matching with one constrained
agglomerative clustering over per-turn embeddings at full-meeting scope.
"""
import base64

import numpy as np
import pytest

from src.global_identity import decode_turn_vectors


def test_decode_turn_vectors_round_trips_a_float32_block():
    vectors = np.array([[1.0, 0.0], [0.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    block = {
        "dim": 2,
        "dtype": "float32",
        "turn_indices": [0, 4, 7],
        "b64": base64.b64encode(vectors.tobytes()).decode("ascii"),
    }

    decoded = decode_turn_vectors(block)

    assert sorted(decoded) == [0, 4, 7]
    np.testing.assert_allclose(decoded[0], [1.0, 0.0])
    np.testing.assert_allclose(decoded[7], [3.0, 4.0])
    assert decoded[4].dtype == np.float64  # promoted for stable arithmetic


def test_decode_turn_vectors_rejects_a_row_count_mismatch():
    vectors = np.array([[1.0, 0.0]], dtype=np.float32)
    block = {
        "dim": 2,
        "dtype": "float32",
        "turn_indices": [0, 1],  # claims two rows, carries one
        "b64": base64.b64encode(vectors.tobytes()).decode("ascii"),
    }

    with pytest.raises(ValueError, match="turn_indices"):
        decode_turn_vectors(block)


def test_decode_turn_vectors_drops_non_finite_rows():
    """pyannote/embedding returns NaN on some turns. One NaN turn must not be
    able to poison anything downstream — measured on June 10, where 7 of 86
    window-local centroids were non-finite because the worker averaged
    unfiltered turn vectors."""
    vectors = np.array([[1.0, 0.0], [np.nan, 0.0], [0.0, 1.0]], dtype=np.float32)
    block = {
        "dim": 2,
        "dtype": "float32",
        "turn_indices": [0, 1, 2],
        "b64": base64.b64encode(vectors.tobytes()).decode("ascii"),
    }

    decoded = decode_turn_vectors(block)

    assert sorted(decoded) == [0, 2]


def test_decode_turn_vectors_handles_an_empty_block():
    block = {"dim": 4, "dtype": "float32", "turn_indices": [], "b64": ""}
    assert decode_turn_vectors(block) == {}


from src.global_identity import IdentityNode, build_nodes
from src.speaker_reconcile import ChunkResult, ChunkWindow, LocalTurn


def _chunk(index, start, end, turns, speech=None):
    return ChunkResult(
        window=ChunkWindow(index, start, end),
        turns=[LocalTurn(index, s, e, label) for s, e, label in turns],
        embeddings={},
        speech_seconds=speech or {},
    )


def test_build_nodes_groups_turns_by_window_and_local_label():
    chunks = [
        _chunk(0, 0.0, 60.0, [(0.0, 10.0, "SPEAKER_00"), (20.0, 25.0, "SPEAKER_01"),
                              (30.0, 40.0, "SPEAKER_00")]),
        _chunk(1, 60.0, 120.0, [(70.0, 80.0, "SPEAKER_00")]),
    ]
    vectors = {
        0: {0: np.array([2.0, 0.0]), 2: np.array([0.0, 4.0])},
        1: {0: np.array([1.0, 1.0])},
    }

    nodes = build_nodes(chunks, vectors)

    assert [(n.chunk_index, n.local_speaker) for n in nodes] == [
        (0, "SPEAKER_00"), (0, "SPEAKER_01"), (1, "SPEAKER_00"),
    ]
    assert nodes[0].speech_seconds == pytest.approx(20.0)   # 10s + 10s
    assert nodes[0].vectors.shape == (2, 2)
    # vectors are unit-normalised so later cosines are plain dot products
    np.testing.assert_allclose(np.linalg.norm(nodes[0].vectors, axis=1), [1.0, 1.0])
    assert nodes[1].vectors.shape == (0, 2)   # SPEAKER_01's turn had no vector


def test_build_nodes_survives_a_window_with_no_vectors_at_all():
    chunks = [_chunk(0, 0.0, 60.0, [(0.0, 10.0, "SPEAKER_00")])]
    nodes = build_nodes(chunks, {0: {}})
    assert len(nodes) == 1
    assert nodes[0].vectors.shape == (0, 0)
    assert nodes[0].speech_seconds == pytest.approx(10.0)


def test_build_nodes_ignores_a_vector_for_a_turn_index_that_does_not_exist():
    chunks = [_chunk(0, 0.0, 60.0, [(0.0, 10.0, "SPEAKER_00")])]
    nodes = build_nodes(chunks, {0: {0: np.array([1.0, 0.0]), 99: np.array([0.0, 1.0])}})
    assert nodes[0].vectors.shape == (1, 2)


from src.global_identity import seed_clusters


def test_seam_overlap_links_the_same_person_across_a_window_boundary():
    """Windows 0 and 1 both diarize 55-65s. One person speaks straight through
    it, so both windows have a local label covering the same audio: same person,
    provable without any embedding."""
    chunks = [
        _chunk(0, 0.0, 65.0, [(50.0, 64.0, "SPEAKER_00")]),
        _chunk(1, 55.0, 120.0, [(56.0, 64.0, "SPEAKER_01"), (80.0, 90.0, "SPEAKER_00")]),
    ]
    nodes = build_nodes(chunks, {})

    clusters, diagnostics = seed_clusters(nodes, chunks)

    labels = {(n.chunk_index, n.local_speaker): cid for n, cid in zip(nodes, clusters)}
    assert labels[(0, "SPEAKER_00")] == labels[(1, "SPEAKER_01")]
    assert labels[(1, "SPEAKER_00")] != labels[(0, "SPEAKER_00")]
    assert len(diagnostics["temporal_matches"]) == 1


def test_seam_overlap_link_is_one_to_one_highest_overlap_first():
    """Two locals in window 1 both overlap window 0's single speaker. Only the
    one with more shared seconds may take it; the other cannot, because that
    would put two window-1 locals in one cluster."""
    chunks = [
        _chunk(0, 0.0, 65.0, [(50.0, 64.0, "SPEAKER_00")]),
        _chunk(1, 55.0, 120.0, [(56.0, 63.0, "SPEAKER_00"), (63.0, 64.0, "SPEAKER_01")]),
    ]
    nodes = build_nodes(chunks, {})

    clusters, diagnostics = seed_clusters(nodes, chunks)

    labels = {(n.chunk_index, n.local_speaker): cid for n, cid in zip(nodes, clusters)}
    assert labels[(1, "SPEAKER_00")] == labels[(0, "SPEAKER_00")]   # 7s of overlap
    assert labels[(1, "SPEAKER_01")] != labels[(0, "SPEAKER_00")]   # 1s, blocked
    assert len(diagnostics["temporal_matches"]) == 1


def test_two_locals_in_one_window_are_never_seeded_together():
    chunks = [_chunk(0, 0.0, 60.0, [(0.0, 10.0, "SPEAKER_00"), (10.0, 20.0, "SPEAKER_01")])]
    nodes = build_nodes(chunks, {})
    clusters, _ = seed_clusters(nodes, chunks)
    assert clusters[0] != clusters[1]


def test_cannot_link_pairs_cover_transitive_membership():
    """A cluster inherits every cannot-link of every node in it: once window 0's
    SPEAKER_00 is seeded with window 1's SPEAKER_01, that cluster may never
    absorb another window-0 OR window-1 local."""
    chunks = [
        _chunk(0, 0.0, 65.0, [(50.0, 64.0, "SPEAKER_00"), (10.0, 20.0, "SPEAKER_09")]),
        _chunk(1, 55.0, 120.0, [(56.0, 64.0, "SPEAKER_01"), (80.0, 90.0, "SPEAKER_02")]),
    ]
    nodes = build_nodes(chunks, {})
    clusters, _ = seed_clusters(nodes, chunks)

    from src.global_identity import cannot_link_chunks

    joined = clusters[0]  # (0, SPEAKER_00) seeded with (1, SPEAKER_01)
    assert cannot_link_chunks(nodes, clusters, joined) == {0, 1}
