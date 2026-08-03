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
