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
