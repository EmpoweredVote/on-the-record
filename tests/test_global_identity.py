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


from src.global_identity import merge_clusters, node_pair_statistics


def _vec(*values):
    vec = np.array(values, dtype=float)
    return vec / np.linalg.norm(vec)


ALICE = _vec(1, 0, 0)
ALICE_NOISY = _vec(1, 0.30, 0)   # cos(ALICE, ALICE_NOISY) ~ 0.958
BOB = _vec(0, 1, 0)
CAROL = _vec(0, 0, 1)


def _node(chunk_index, local, vectors, speech=30.0, turns=None):
    return IdentityNode(
        chunk_index=chunk_index,
        local_speaker=local,
        turns=turns or [LocalTurn(chunk_index, 0.0, speech, local)],
        vectors=np.asarray(vectors, dtype=float),
        speech_seconds=speech,
    )


def test_average_linkage_merges_the_same_voice_across_windows():
    nodes = [_node(0, "SPEAKER_00", [ALICE]), _node(1, "SPEAKER_00", [ALICE_NOISY])]
    stats = node_pair_statistics(nodes)
    clusters, diagnostics = merge_clusters(
        nodes, [0, 1], stats, threshold=0.60, linkage="average"
    )
    assert len(set(clusters)) == 1
    assert len(diagnostics["embedding_matches"]) == 1


def test_different_voices_are_not_merged():
    nodes = [_node(0, "SPEAKER_00", [ALICE]), _node(1, "SPEAKER_00", [BOB])]
    stats = node_pair_statistics(nodes)
    clusters, diagnostics = merge_clusters(
        nodes, [0, 1], stats, threshold=0.60, linkage="average"
    )
    assert len(set(clusters)) == 2
    # the nearest rejected merge is reported so operators can see how close a
    # run came to the conflation cliff
    assert diagnostics["margin"] == pytest.approx(0.60, abs=0.01)


def test_cannot_link_blocks_a_merge_that_similarity_alone_would_make():
    """Two locals in ONE window with near-identical voices: the window's own
    clustering says they are different people, and that wins. This is the
    structural bound on conflation."""
    nodes = [_node(0, "SPEAKER_00", [ALICE]), _node(0, "SPEAKER_01", [ALICE_NOISY])]
    stats = node_pair_statistics(nodes)
    clusters, diagnostics = merge_clusters(
        nodes, [0, 1], stats, threshold=0.60, linkage="average"
    )
    assert len(set(clusters)) == 2
    assert len(diagnostics["cannot_link_blocks"]) == 1
    assert diagnostics["cannot_link_blocks"][0]["similarity"] == pytest.approx(0.958, abs=0.01)


def test_merging_is_transitive_across_three_windows():
    """A person's window-2 appearance can join through their window-1 one even
    if window 0 vs window 2 alone would be borderline — the global view the
    sequential running-mean matcher cannot provide."""
    # NOTE: deviation from the plan's literal values (bridge=0.55, far=1.05) —
    # see the Task 4 report for why: those numbers make bridge-far (0.953) the
    # single closest pair, so the documented "closest-admissible-pair-first"
    # merge order joins bridge+far BEFORE alice, and the resulting
    # alice-vs-(bridge+far) average (0.783) never clears threshold=0.80 — the
    # test fails against a spec-compliant implementation. These values make
    # alice-bridge (0.944) the closest pair instead, so it merges first, and
    # the subsequent (alice+bridge)-vs-far average (0.833) clears the
    # threshold — which is what the docstring below actually describes.
    bridge = _vec(1, 0.35, 0)
    far = _vec(1, 0.90, 0)
    nodes = [
        _node(0, "SPEAKER_00", [ALICE]),
        _node(1, "SPEAKER_00", [bridge]),
        _node(2, "SPEAKER_00", [far]),
    ]
    stats = node_pair_statistics(nodes)
    clusters, _ = merge_clusters(nodes, [0, 1, 2], stats, threshold=0.80, linkage="average")
    assert len(set(clusters)) == 1


def test_complete_linkage_is_more_conservative_than_average():
    """Complete linkage scores a candidate by its WORST pair, so one dissimilar
    turn holds a merge back. Same data, different verdict."""
    nodes = [
        _node(0, "SPEAKER_00", [ALICE, ALICE]),
        _node(1, "SPEAKER_00", [ALICE, _vec(1, 1, 0)]),   # one turn at cos 0.707
    ]
    stats = node_pair_statistics(nodes)
    average, _ = merge_clusters(nodes, [0, 1], stats, threshold=0.80, linkage="average")
    complete, _ = merge_clusters(nodes, [0, 1], stats, threshold=0.80, linkage="complete")
    assert len(set(average)) == 1
    assert len(set(complete)) == 2


def test_a_node_with_no_vectors_never_merges_by_embedding():
    # threshold is negative so a broken "-inf becomes 0.0" implementation
    # would still pass the merge check; only the real -inf sentinel is
    # guaranteed to stay below a negative threshold too.
    nodes = [_node(0, "SPEAKER_00", np.zeros((0, 3))), _node(1, "SPEAKER_00", [ALICE])]
    stats = node_pair_statistics(nodes)
    clusters, _ = merge_clusters(nodes, [0, 1], stats, threshold=-1.0, linkage="average")
    assert len(set(clusters)) == 2


def test_the_closest_admissible_pair_merges_first():
    """Node 0 (ALICE) is nearer node 2's vector than node 1's, so the (0, 2)
    pair merges first even though node 1 is visited first in iteration order.
    Nodes 1 and 2 share a window, so cannot-link then permanently blocks node
    1 from ever joining the cluster node 0 and node 2 formed."""
    nearly_alice = _vec(1, 0.10, 0)
    nodes = [
        _node(0, "SPEAKER_00", [ALICE]),
        _node(1, "SPEAKER_00", [_vec(1, 0.80, 0)]),
        _node(1, "SPEAKER_01", [nearly_alice]),
    ]
    stats = node_pair_statistics(nodes)
    clusters, _ = merge_clusters(nodes, [0, 1, 2], stats, threshold=0.60, linkage="average")
    assert clusters[0] == clusters[2]
    assert clusters[0] != clusters[1]


def test_node_pair_statistics_rejects_more_turns_than_max_embedded_turns(monkeypatch):
    """MAX_EMBEDDED_TURNS is a documented safety valve against an O(turns^2)
    similarity matrix; patch it down rather than construct a real 20k-turn
    meeting to exercise it cheaply."""
    monkeypatch.setattr("src.global_identity.MAX_EMBEDDED_TURNS", 2)
    nodes = [_node(0, "SPEAKER_00", [ALICE, ALICE]), _node(1, "SPEAKER_00", [ALICE])]
    with pytest.raises(ValueError, match="MAX_EMBEDDED_TURNS"):
        node_pair_statistics(nodes)
