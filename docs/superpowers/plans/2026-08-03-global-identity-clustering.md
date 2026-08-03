# Global Identity Clustering for Chunked Diarization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace chunked diarization's per-window centroid matching with ONE global constrained clustering over per-turn embeddings, so chunked output stops producing more speaker labels than there are people and `DIARIZE_CHUNK_MINUTES = 60` can ship enabled by default.

**Architecture:** Per `docs/superpowers/specs/2026-08-03-global-identity-clustering-design.md`. Chunking stays exactly as-is for segmentation. The Modal chunk worker stops discarding the per-turn embeddings it already computes and returns them (base64 float32, both candidate embedders, non-finite filtered per turn). A new pure module `src/global_identity.py` builds one node per (window, local speaker), seeds clusters from seam temporal overlap (must-link) under a same-window cannot-link constraint, then runs constrained agglomerative clustering over per-turn cosine similarity at full-meeting scope. `src/modal_compute.stitch_chunk_payloads` gains a branch selecting it; the existing sequential `reconcile_chunks` path stays byte-identical as the fallback and keeps old cached payloads stitchable.

**Tech Stack:** Python 3.12 via `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python` **ONLY** (the venv lives in the MAIN repo; this branch is checked out in a worktree, so always use that absolute path — `python3` is the system 3.14 and lacks project deps). numpy, modal, pyannote.audio 4.0.4, pyannote.metrics (via `bench/score.py`). Branch `perf/global-identity-clustering`, already created off `perf/chunked-diarization` (spec committed as 7576fee).

**House conventions:** flat `tests/test_*.py`, run with `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest`. Pure logic is unit-tested; Modal/GPU-bound code is thin and untested. Config knobs live in `src/config.py` with a comment explaining the measured basis for the value. `tests/conftest.py` strips `DATABASE_URL` from the environment for every test.

---

## File Structure

| File | Responsibility |
|---|---|
| **Create** `src/global_identity.py` | Pure. Decode turn-vector payload blocks; build identity nodes; seam must-link; same-window cannot-link; constrained agglomerative clustering; global labels/centroids/diagnostics. Reuses `speaker_reconcile`'s dataclasses and `_overlap_seconds` / `_ownership_bounds` — no second copy of ownership or overlap logic. |
| **Create** `tests/test_global_identity.py` | Unit tests for every pure behaviour above. |
| **Create** `bench/identity_score.py` | Pure. Score a hypothesis against a human-reviewed named transcript: label→person mapping, fragmentation, conflation, and a reference RTTM writer for named DER. |
| **Create** `tests/test_identity_score.py` | Unit tests for the scorer. |
| **Modify** `bench/modal_app.py` (`diarize_chunk_window`, ~line 1494) | Return per-turn vectors under both embedders; filter non-finite per turn (also fixes the NaN-poisoned centroid). Thin, untested. |
| **Modify** `src/modal_compute.py` (`stitch_chunk_payloads`, ~line 110) | Branch to `cluster_global_identities` when payloads carry turn embeddings and identity mode is `global`; use its centroids instead of recomputing. |
| **Modify** `src/config.py` | `DIARIZE_CHUNK_IDENTITY`, `DIARIZE_CHUNK_CLUSTER_THRESHOLD`, `DIARIZE_CHUNK_LINKAGE`, `DIARIZE_CHUNK_EMBEDDER`; and at the end, `DIARIZE_CHUNK_MINUTES` 0 → 60 **only if the gate passes**. |
| **Modify** `scripts/sweep_chunk_thresholds.py` | `--identity`, `--linkage`, `--embedder`, `--cluster` grids; named-reference reporting; seam spot-check. |
| **Modify** `docs/superpowers/specs/2026-08-03-global-identity-clustering-design.md` | Append measured calibration results at the end (Task 10). |

Not touched: `src/speaker_reconcile.py` (VibeVoice depends on it and its 0.75 is tuned for 50-minute windows), the single-pass path, `src/merge.py`, `src/identify.py`.

---

### Task 1: Turn-vector payload decoding

The worker will ship per-turn vectors as base64 float32 (Task 5). Decode them here first so the
consumer contract is pinned by tests before the producer exists.

**Files:**
- Create: `src/global_identity.py`
- Create: `tests/test_global_identity.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.global_identity'`

- [ ] **Step 3: Write the module header and the decoder**

```python
"""Global cross-window speaker identity for chunked diarization.

Chunked diarization splits a meeting into overlapping windows because
pyannote's cost is ~quadratic in window length (measured: 2x duration =
4.5x cost). Segmentation survives that split almost perfectly (missed
0.0007, false alarm 0.0003) but IDENTITY does not: each window's
``SPEAKER_NN`` labels are window-local, and matching them by per-window
CENTROIDS — one vector averaged over one window's turns — fragments people
(June 10: 49 labels for 41 people).

This module replaces centroid matching with the global view single-pass
clustering gets for free: one constrained agglomerative clustering over
PER-TURN embeddings at full-meeting scope. Measured on June 10 before this
was built:

* 86 window-local speakers map onto exactly 40 human-reviewed people, so
  grouping nodes is SUFFICIENT — the fragmentation is entirely cross-window
  matching failure, not within-window over-splitting.
* No window ever contained two locals belonging to one person, so
  "two locals in one window are two different people" is a sound hard
  CANNOT-LINK constraint. It is what structurally bounds conflation, the
  error mode this repo has been burned by (see the speaker-identity
  collision guard): two people can only merge if no window ever heard them
  both.
* Clustering 2745 turn vectors costs ~12ms. The quadratic blowup inside
  pyannote is over its dense sliding-window embeddings, not per-turn ones.

Pure: numpy + dataclasses only, no torch, no Modal. Dataclasses,
``_overlap_seconds`` and ``_ownership_bounds`` are reused from
``src.speaker_reconcile`` rather than re-implemented — this repo has
already paid for duplicating a stitcher once.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .speaker_reconcile import (
    ChunkResult,
    LocalTurn,
    StableTurn,
    _overlap_seconds,
    _ownership_bounds,
)


def decode_turn_vectors(block: dict[str, Any]) -> dict[int, np.ndarray]:
    """Decode one payload turn-embedding block into {turn_index: vector}.

    The Modal worker ships vectors as base64 float32 so payloads stay
    JSON-cacheable by scripts/sweep_chunk_thresholds.py with no precision
    loss. Non-finite rows are dropped here (pyannote/embedding NaNs on some
    turns) so no caller has to remember to guard.
    """
    indices = list(block.get("turn_indices") or [])
    raw = block.get("b64") or ""
    if not indices or not raw:
        return {}
    dim = int(block["dim"])
    flat = np.frombuffer(base64.b64decode(raw), dtype=np.dtype(block.get("dtype", "float32")))
    if flat.size != len(indices) * dim:
        raise ValueError(
            f"turn_indices claims {len(indices)} rows of {dim} "
            f"({len(indices) * dim} values) but the block carries {flat.size}"
        )
    matrix = flat.reshape(len(indices), dim).astype(np.float64)
    return {
        int(index): matrix[row]
        for row, index in enumerate(indices)
        if np.all(np.isfinite(matrix[row]))
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/global_identity.py tests/test_global_identity.py
git commit -m "feat: decode per-turn embedding payload blocks for global identity"
```

---

### Task 2: Identity nodes

One node per (window, local speaker), carrying its turns and its unit-normalised turn
vectors. Unit-normalising once here makes every later cosine a dot product.

**Files:**
- Modify: `src/global_identity.py`
- Modify: `tests/test_global_identity.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_global_identity.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: FAIL — `ImportError: cannot import name 'IdentityNode'`

- [ ] **Step 3: Implement**

Append to `src/global_identity.py`:

```python
@dataclass
class IdentityNode:
    """One window-local speaker: the atom of global identity.

    A window's own clustering already decided these turns are one person, and
    that decision is trusted (measured: 1 of 86 nodes on June 10 was less than
    75% pure against the human-reviewed reference). ``vectors`` is (k, dim),
    unit-normalised, one row per turn that produced a usable embedding — so it
    can legitimately be empty for a node whose turns all fell in a
    neighbouring window's canonical span.
    """

    chunk_index: int
    local_speaker: str
    turns: list[LocalTurn]
    vectors: np.ndarray
    speech_seconds: float


def build_nodes(
    chunks: list[ChunkResult],
    turn_vectors: dict[int, dict[int, np.ndarray]],
) -> list[IdentityNode]:
    """Build one IdentityNode per (window, local speaker), in window order.

    `turn_vectors` is {chunk_index: {turn_index: vector}} where turn_index
    indexes that chunk's `turns` list (the worker only embeds turns inside its
    canonical span, so overlap-only turns are absent by design).
    """
    nodes: list[IdentityNode] = []
    for chunk in sorted(chunks, key=lambda c: c.window.index):
        by_local: dict[str, list[tuple[int, LocalTurn]]] = {}
        for position, turn in enumerate(chunk.turns):
            by_local.setdefault(turn.local_speaker, []).append((position, turn))
        available = turn_vectors.get(chunk.window.index, {})
        for local in sorted(by_local):
            entries = by_local[local]
            rows = [available[position] for position, _ in entries if position in available]
            matrix = np.asarray(rows, dtype=float) if rows else np.zeros((0, 0))
            if matrix.size:
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                matrix = matrix / norms
            nodes.append(IdentityNode(
                chunk_index=chunk.window.index,
                local_speaker=local,
                turns=[turn for _, turn in entries],
                vectors=matrix,
                speech_seconds=sum(t.end - t.start for _, t in entries),
            ))
    return nodes
```

- [ ] **Step 4: Run to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/global_identity.py tests/test_global_identity.py
git commit -m "feat: build window-local identity nodes with per-turn vectors"
```

---

### Task 3: Seam must-link and same-window cannot-link

Two structural constraints, both computed before any embedding is consulted. Temporal
overlap in the seam is the strongest available signal (it supplied 7–19 matches per meeting
that centroids missed). Same-window distinctness is the constraint that bounds conflation.

**Files:**
- Modify: `src/global_identity.py`
- Modify: `tests/test_global_identity.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_global_identity.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: FAIL — `ImportError: cannot import name 'seed_clusters'`

- [ ] **Step 3: Implement**

Append to `src/global_identity.py`:

```python
def cannot_link_chunks(
    nodes: list[IdentityNode], clusters: list[int], cluster_id: int
) -> set[int]:
    """Window indices a cluster already occupies — it may absorb no more from them.

    A window's own diarization said its locals are different people, so a
    cluster that already holds one local from window W can never hold another.
    The constraint is on the CLUSTER, not the node, so it survives transitive
    merges.
    """
    return {
        node.chunk_index
        for node, assigned in zip(nodes, clusters)
        if assigned == cluster_id
    }


def seed_clusters(
    nodes: list[IdentityNode], chunks: list[ChunkResult]
) -> tuple[list[int], dict[str, list[dict[str, Any]]]]:
    """Seed one cluster per node, then join nodes that overlap in a seam.

    Returns (cluster_id per node, diagnostics). Overlap candidates are applied
    highest-overlap-first and any join that would violate a cannot-link is
    skipped — the same greedy discipline the sequential reconciler uses, which
    cannot displace a strong match onto a worse partner the way a
    sum-maximizing assignment can.
    """
    clusters = list(range(len(nodes)))
    index_of = {(n.chunk_index, n.local_speaker): i for i, n in enumerate(nodes)}
    windows = {chunk.window.index: chunk.window for chunk in chunks}
    diagnostics: dict[str, list[dict[str, Any]]] = {
        "temporal_matches": [], "embedding_matches": [], "new_speakers": [],
        "cannot_link_blocks": [],
    }

    ordered = sorted(windows)
    candidates: list[tuple[float, int, int]] = []
    for position in range(1, len(ordered)):
        previous_index, current_index = ordered[position - 1], ordered[position]
        previous_window, current_window = windows[previous_index], windows[current_index]
        overlap_start = max(previous_window.start, current_window.start)
        overlap_end = min(previous_window.end, current_window.end)
        if overlap_end <= overlap_start:
            continue
        for node_a in (n for n in nodes if n.chunk_index == previous_index):
            for node_b in (n for n in nodes if n.chunk_index == current_index):
                score = sum(
                    _overlap_seconds(turn_a, turn_b, overlap_start, overlap_end)
                    for turn_a in node_a.turns
                    for turn_b in node_b.turns
                )
                if score > 0:
                    candidates.append((
                        score,
                        index_of[(node_a.chunk_index, node_a.local_speaker)],
                        index_of[(node_b.chunk_index, node_b.local_speaker)],
                    ))

    for score, a, b in sorted(candidates, key=lambda c: (-c[0], c[1], c[2])):
        target, source = clusters[a], clusters[b]
        if target == source:
            continue
        occupied = cannot_link_chunks(nodes, clusters, target)
        if cannot_link_chunks(nodes, clusters, source) & occupied:
            diagnostics["cannot_link_blocks"].append({
                "reason": "temporal",
                "chunk": nodes[b].chunk_index,
                "local": nodes[b].local_speaker,
                "overlap_seconds": round(score, 3),
            })
            continue
        clusters = [target if c == source else c for c in clusters]
        diagnostics["temporal_matches"].append({
            "chunk": nodes[b].chunk_index,
            "local": nodes[b].local_speaker,
            "matched_chunk": nodes[a].chunk_index,
            "matched_local": nodes[a].local_speaker,
            "overlap_seconds": round(score, 3),
        })
    return clusters, diagnostics
```

- [ ] **Step 4: Run to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/global_identity.py tests/test_global_identity.py
git commit -m "feat: seed identity clusters from seam overlap under cannot-link"
```

---

### Task 4: Constrained agglomerative merge over per-turn similarity

The core of the change. Node-pair similarity statistics are precomputed once, so the merge
loop is pure arithmetic over a nodes×nodes matrix — exact for all three linkages, and
measured at milliseconds for June 10's 87 nodes / 2745 turns.

**Files:**
- Modify: `src/global_identity.py`
- Modify: `tests/test_global_identity.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_global_identity.py`:

```python
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
    bridge = _vec(1, 0.55, 0)
    far = _vec(1, 1.05, 0)
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
    nodes = [_node(0, "SPEAKER_00", np.zeros((0, 3))), _node(1, "SPEAKER_00", [ALICE])]
    stats = node_pair_statistics(nodes)
    clusters, _ = merge_clusters(nodes, [0, 1], stats, threshold=0.10, linkage="average")
    assert len(set(clusters)) == 2


def test_the_closest_admissible_pair_merges_first():
    """CAROL is nearer ALICE than BOB is; with only one merge admissible per
    window pair, the stronger match must win."""
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: FAIL — `ImportError: cannot import name 'merge_clusters'`

- [ ] **Step 3: Implement**

Append to `src/global_identity.py`:

```python
#: Above this many embedded turns the nodes x nodes precomputation would need a
#: turn x turn similarity matrix too large to hold comfortably (12k turns is
#: ~576 MB). No real meeting is close — June 10, a 5-hour council meeting, has
#: 2745 — so this raises rather than silently degrading.
MAX_EMBEDDED_TURNS = 20_000


@dataclass
class NodePairStatistics:
    """Exact per-node-pair aggregates, computed once from the turn matrix.

    Every linkage below is computable from these without touching turn vectors
    again, which is what makes the merge loop nodes-sized (87 for June 10)
    rather than turns-sized (2745):

    * ``similarity_sum`` / ``pair_count`` -> average linkage, exactly.
    * ``similarity_min`` -> complete linkage, exactly.
    * ``gram`` (sum-vector dot products) -> centroid linkage, exactly, since
      the dot product of two clusters' summed vectors is the sum of their
      node-pair gram entries.
    """

    similarity_sum: np.ndarray
    pair_count: np.ndarray
    similarity_min: np.ndarray
    gram: np.ndarray
    norm_sq: np.ndarray


def node_pair_statistics(nodes: list[IdentityNode]) -> NodePairStatistics:
    """Precompute node-pair similarity aggregates from per-turn vectors."""
    count = len(nodes)
    rows = [node.vectors for node in nodes if node.vectors.size]
    total_turns = sum(matrix.shape[0] for matrix in rows)
    if total_turns > MAX_EMBEDDED_TURNS:
        raise ValueError(
            f"{total_turns} embedded turns exceeds MAX_EMBEDDED_TURNS "
            f"({MAX_EMBEDDED_TURNS}); raise the chunk size or aggregate in blocks"
        )
    similarity_sum = np.zeros((count, count))
    pair_count = np.zeros((count, count))
    similarity_min = np.full((count, count), np.inf)
    sums = np.zeros((count, max((m.shape[1] for m in rows), default=0)))
    for index, node in enumerate(nodes):
        if node.vectors.size:
            sums[index] = node.vectors.sum(axis=0)
    for i, node_i in enumerate(nodes):
        for j in range(i, count):
            node_j = nodes[j]
            if not node_i.vectors.size or not node_j.vectors.size:
                continue
            block = node_i.vectors @ node_j.vectors.T
            similarity_sum[i, j] = similarity_sum[j, i] = float(block.sum())
            pair_count[i, j] = pair_count[j, i] = float(block.size)
            similarity_min[i, j] = similarity_min[j, i] = float(block.min())
    gram = sums @ sums.T if sums.size else np.zeros((count, count))
    return NodePairStatistics(
        similarity_sum=similarity_sum,
        pair_count=pair_count,
        similarity_min=similarity_min,
        gram=gram,
        norm_sq=np.diag(gram).copy(),
    )


def _cluster_similarity(
    members_a: list[int], members_b: list[int], stats: NodePairStatistics, linkage: str
) -> float:
    """Similarity between two clusters under the requested linkage, or -inf.

    -inf means "no embedding evidence connects these", which keeps a node with
    no usable vectors out of every embedding merge instead of merging it
    arbitrarily.
    """
    pairs = stats.pair_count[np.ix_(members_a, members_b)]
    if pairs.sum() == 0:
        return float("-inf")
    if linkage == "average":
        return float(stats.similarity_sum[np.ix_(members_a, members_b)].sum() / pairs.sum())
    if linkage == "complete":
        block = stats.similarity_min[np.ix_(members_a, members_b)]
        return float(block[np.isfinite(block)].min())
    if linkage == "centroid":
        dot = float(stats.gram[np.ix_(members_a, members_b)].sum())
        norm_a = float(stats.gram[np.ix_(members_a, members_a)].sum()) ** 0.5
        norm_b = float(stats.gram[np.ix_(members_b, members_b)].sum()) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return float("-inf")
        return dot / (norm_a * norm_b)
    raise ValueError(f"unknown linkage {linkage!r}; use average, complete or centroid")


def merge_clusters(
    nodes: list[IdentityNode],
    clusters: list[int],
    stats: NodePairStatistics,
    *,
    threshold: float,
    linkage: str = "average",
) -> tuple[list[int], dict[str, list[dict[str, Any]]]]:
    """Merge clusters by per-turn similarity, respecting cannot-link.

    Repeatedly joins the single most similar admissible pair while its
    similarity is at or above `threshold`. Closest-first (rather than
    first-found) is what lets a person's strongest cross-window match claim
    them before a weaker candidate can.

    Diagnostics carry `embedding_matches`, `cannot_link_blocks` (pairs above
    threshold refused by the constraint) and `margin` — how far below the
    threshold the nearest non-merge sat. A small margin means the run came
    close to the conflation cliff even if the speaker count looks right.
    """
    diagnostics: dict[str, Any] = {"embedding_matches": [], "cannot_link_blocks": []}
    clusters = list(clusters)
    best_rejected = float("-inf")

    while True:
        members: dict[int, list[int]] = {}
        for index, cluster_id in enumerate(clusters):
            members.setdefault(cluster_id, []).append(index)
        occupied = {
            cluster_id: {nodes[i].chunk_index for i in indices}
            for cluster_id, indices in members.items()
        }
        best: tuple[float, int, int] | None = None
        for a, b in ((a, b) for a in members for b in members if b > a):
            similarity = _cluster_similarity(members[a], members[b], stats, linkage)
            if similarity == float("-inf"):
                continue
            if occupied[a] & occupied[b]:
                if similarity >= threshold:
                    diagnostics["cannot_link_blocks"].append({
                        "reason": "embedding",
                        "similarity": round(similarity, 4),
                        "windows": sorted(occupied[a] & occupied[b]),
                    })
                continue
            if similarity < threshold:
                best_rejected = max(best_rejected, similarity)
                continue
            if best is None or similarity > best[0]:
                best = (similarity, a, b)
        if best is None:
            break
        similarity, target, source = best
        clusters = [target if c == source else c for c in clusters]
        diagnostics["embedding_matches"].append({
            "similarity": round(similarity, 4),
            "windows": sorted(occupied[target] | occupied[source]),
        })

    diagnostics["margin"] = (
        round(threshold - best_rejected, 4) if best_rejected > float("-inf") else None
    )
    return clusters, diagnostics
```

- [ ] **Step 4: Run to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add src/global_identity.py tests/test_global_identity.py
git commit -m "feat: constrained agglomerative merge over per-turn similarity"
```

---

### Task 5: Assemble `cluster_global_identities`

Wire nodes → seeds → merge → stable turns, labels, centroids, diagnostics into the single
entry point the orchestrator calls.

**Files:**
- Modify: `src/global_identity.py`
- Modify: `tests/test_global_identity.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_global_identity.py`:

```python
from src.global_identity import cluster_global_identities


def test_cluster_global_identities_end_to_end_two_windows_two_people():
    chunks = [
        _chunk(0, 0.0, 65.0, [(0.0, 30.0, "SPEAKER_00"), (30.0, 60.0, "SPEAKER_01")]),
        _chunk(1, 55.0, 120.0, [(66.0, 100.0, "SPEAKER_00"), (100.0, 110.0, "SPEAKER_01")]),
    ]
    vectors = {
        0: {0: ALICE, 1: BOB},
        1: {0: BOB, 1: ALICE_NOISY},   # local labels swapped between windows
    }

    result = cluster_global_identities(chunks, vectors, threshold=0.60)

    assert len({t.speaker for t in result.turns}) == 2
    by_start = {round(t.start, 1): t.speaker for t in result.turns}
    assert by_start[0.0] == by_start[100.0]      # ALICE in both windows
    assert by_start[30.0] == by_start[66.0]      # BOB in both windows
    # SPEAKER_00 is the most talkative person (BOB: 30s + 34s)
    assert by_start[30.0] == "SPEAKER_00"
    assert sorted(result.centroids) == ["SPEAKER_00", "SPEAKER_01"]
    assert len(result.centroids["SPEAKER_00"]) == 3
    assert result.diagnostics["clusters"] == 2
    assert result.diagnostics["nodes"] == 4
    assert result.diagnostics["window_speaker_bounds"] == [2, 4]


def test_cluster_global_identities_clips_turns_at_the_overlap_midpoint():
    """Both windows diarize 55-65s; each second of audio must be owned once.
    The midpoint is 60.0, so window 0's turn is cut there and window 1's starts
    there — the same ownership rule the sequential path uses."""
    chunks = [
        _chunk(0, 0.0, 65.0, [(50.0, 64.0, "SPEAKER_00")]),
        _chunk(1, 55.0, 120.0, [(56.0, 70.0, "SPEAKER_00")]),
    ]
    result = cluster_global_identities(chunks, {0: {0: ALICE}, 1: {0: ALICE}}, threshold=0.60)

    spans = sorted((t.start, t.end) for t in result.turns)
    assert spans == [(50.0, 60.0), (60.0, 70.0)]
    assert len({t.speaker for t in result.turns}) == 1


def test_cluster_global_identities_labels_unmatched_nodes_as_new_speakers():
    chunks = [
        _chunk(0, 0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")]),
        _chunk(1, 60.0, 120.0, [(70.0, 80.0, "SPEAKER_00")]),
    ]
    result = cluster_global_identities(chunks, {0: {0: ALICE}, 1: {0: CAROL}}, threshold=0.60)
    assert len({t.speaker for t in result.turns}) == 2
    assert len(result.diagnostics["new_speakers"]) == 2


def test_cluster_global_identities_reports_a_speaker_with_no_centroid():
    """A node whose turns never embedded still publishes its turns; it just has
    no voiceprint, and the caller must be able to see that rather than
    discover it downstream."""
    chunks = [_chunk(0, 0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")])]
    result = cluster_global_identities(chunks, {0: {}}, threshold=0.60)
    assert len(result.turns) == 1
    assert result.centroids == {}
    assert result.diagnostics["speakers_without_centroid"] == ["SPEAKER_00"]


def test_cluster_global_identities_rejects_an_unknown_linkage():
    chunks = [_chunk(0, 0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")])]
    with pytest.raises(ValueError, match="unknown linkage"):
        cluster_global_identities(
            chunks, {0: {0: ALICE}}, threshold=0.60, linkage="banana"
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: FAIL — `ImportError: cannot import name 'cluster_global_identities'`

- [ ] **Step 3: Implement**

Append to `src/global_identity.py`:

```python
@dataclass
class GlobalIdentityResult:
    """`ReconciliationResult`'s turns + diagnostics, plus global centroids.

    The sequential path recomputes centroids in the orchestrator by averaging
    per-window centroids; here they come straight from the cluster's own turn
    vectors, which is a better voiceprint and one less place to get the
    weighting wrong.
    """

    turns: list[StableTurn]
    diagnostics: dict[str, Any]
    centroids: dict[str, list[float]] = field(default_factory=dict)


def cluster_global_identities(
    chunks: list[ChunkResult],
    turn_vectors: dict[int, dict[int, np.ndarray]],
    *,
    threshold: float,
    linkage: str = "average",
    label_prefix: str = "SPEAKER_",
) -> GlobalIdentityResult:
    """Assign meeting-wide speaker labels from per-turn embeddings.

    One global pass, not a walk over seams: seam overlap seeds the clusters,
    same-window distinctness constrains them, and constrained agglomerative
    clustering over per-turn cosine similarity does the rest.
    """
    if linkage not in ("average", "complete", "centroid"):
        raise ValueError(f"unknown linkage {linkage!r}; use average, complete or centroid")
    chunks = sorted(chunks, key=lambda chunk: chunk.window.index)
    windows = [chunk.window for chunk in chunks]
    nodes = build_nodes(chunks, turn_vectors)
    seeded, diagnostics = seed_clusters(nodes, chunks)
    stats = node_pair_statistics(nodes)
    clusters, merge_diagnostics = merge_clusters(
        nodes, seeded, stats, threshold=threshold, linkage=linkage
    )
    diagnostics["embedding_matches"] = merge_diagnostics["embedding_matches"]
    diagnostics["cannot_link_blocks"] += merge_diagnostics["cannot_link_blocks"]
    diagnostics["margin"] = merge_diagnostics["margin"]

    members: dict[int, list[int]] = {}
    for index, cluster_id in enumerate(clusters):
        members.setdefault(cluster_id, []).append(index)
    # Most talkative person first: deterministic, and it puts the chair at
    # SPEAKER_00, which makes review listings read naturally.
    order = sorted(
        members,
        key=lambda cid: (-sum(nodes[i].speech_seconds for i in members[cid]), cid),
    )
    label_of = {cid: f"{label_prefix}{position:02d}" for position, cid in enumerate(order)}

    node_label = [label_of[cluster_id] for cluster_id in clusters]
    position_of_window = {window.index: position for position, window in enumerate(windows)}
    stable_turns: list[StableTurn] = []
    for node, label in zip(nodes, node_label):
        owned_start, owned_end = _ownership_bounds(
            windows, position_of_window[node.chunk_index]
        )
        for turn in node.turns:
            start = max(turn.start, owned_start)
            end = min(turn.end, owned_end)
            if end <= start:
                continue
            stable_turns.append(StableTurn(
                chunk_index=turn.chunk_index,
                start=round(start, 3),
                end=round(end, 3),
                local_speaker=turn.local_speaker,
                speaker=label,
            ))
    stable_turns.sort(key=lambda turn: (turn.start, turn.end, turn.speaker))

    centroids: dict[str, list[float]] = {}
    for cluster_id, indices in members.items():
        rows = [nodes[i].vectors for i in indices if nodes[i].vectors.size]
        if rows:
            centroids[label_of[cluster_id]] = np.vstack(rows).mean(axis=0).tolist()

    present = {turn.speaker for turn in stable_turns}
    per_window = [
        len({node.local_speaker for node in nodes if node.chunk_index == window.index})
        for window in windows
    ]
    diagnostics["new_speakers"] = [
        {"global": label} for label in sorted(present) if label not in {
            d.get("global") for d in diagnostics["temporal_matches"]
        } and len(members[order[int(label[len(label_prefix):])]]) == 1
    ]
    diagnostics["clusters"] = len(present)
    diagnostics["nodes"] = len(nodes)
    diagnostics["linkage"] = linkage
    diagnostics["threshold"] = threshold
    # Pyannote's own per-window counts bound the answer: a person can appear in
    # every window (lower bound = the busiest window) and at most once per
    # window (upper bound = the sum). June 10 @60min: 26 <= K <= 87, truth 40.
    diagnostics["window_speaker_bounds"] = [max(per_window, default=0), sum(per_window)]
    diagnostics["speakers_without_centroid"] = sorted(present - set(centroids))
    return GlobalIdentityResult(stable_turns, diagnostics, centroids)
```

- [ ] **Step 4: Run to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: 24 passed. If `new_speakers` accounting is awkward to express this way, simplify it to
"clusters containing exactly one node" — the count is a diagnostic, and the test asserts only
its length.

- [ ] **Step 5: Run the whole suite — nothing else may move**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest -q 2>&1 | tail -5`
Expected: same pass count as before this task's changes (the branch baseline is ~1681 tests), 0 failures.

- [ ] **Step 6: Commit**

```bash
git add src/global_identity.py tests/test_global_identity.py
git commit -m "feat: cluster_global_identities — one global identity pass over turn embeddings"
```

---

### Task 6: Chunk worker returns per-turn embeddings

Thin, Modal-bound, untested by house convention. Segmentation is untouched — only the
embedding loop's output changes, plus the per-turn non-finite filter that fixes the
NaN-poisoned centroid.

**Files:**
- Modify: `bench/modal_app.py` (`diarize_chunk_window`, ~line 1494–1615)

- [ ] **Step 1: Replace the signature and docstring**

Change the function signature to add an `embedders` parameter:

```python
def diarize_chunk_window(
    meeting_id: str,
    start_s: float,
    end_s: float,
    overlap_s: float = 60.0,
    window_index: int = 0,
    embedders: tuple[str, ...] = ("pyannote/wespeaker-voxceleb-resnet34-LM",),
) -> str:
```

Append to the existing docstring, before the `Returns:` paragraph:

```
    Per-turn embeddings are returned as well as centroids, because global
    identity clustering (src/global_identity.py) needs the per-turn view that
    per-window centroids average away. Vectors are computed for CANONICAL-span
    turns only and non-finite rows are dropped per turn — pyannote/embedding
    NaNs on some turns, and averaging unfiltered poisoned 7 of 86 window-local
    centroids on the June 10 meeting.

    `embedders` selects which embedding model(s) to run. Calibration passes
    both candidates in one call so the expensive segmentation is paid once:
    "pyannote/embedding" (512-dim, the historical chunk/single-pass space) and
    "pyannote/wespeaker-voxceleb-resnet34-LM" (256-dim, config.EMBEDDING_MODEL,
    what pyannote 3.1 clusters on internally and what voice profiles use).
```

Extend the `Returns:` JSON list with:

```
    "turn_embeddings": {model_id: {"dim": int, "dtype": "float32",
    "turn_indices": [int, ...], "b64": str}} where turn_indices index the
    "turns" list above,
```

- [ ] **Step 2: Replace the embedding block**

Replace everything from `emb_model = Model.from_pretrained("pyannote/embedding", ...)` through
the `centroids = {...}` comprehension with:

```python
    import base64 as _base64

    # Canonical-span clips, computed once and reused by every embedder.
    clips: list[tuple[int, str, np.ndarray]] = []
    for turn_index, (start, end, label) in enumerate(turns):
        c0 = max(start, start_s)
        c1 = min(end, end_s)
        if c1 - c0 < 0.3:  # too little canonical audio to embed
            continue
        i0 = int((c0 - read_start) * sr)
        i1 = int((c1 - read_start) * sr)
        clip = samples[i0:i1]
        if len(clip) < int(sr * 0.3):
            continue
        clips.append((turn_index, label, clip))

    turn_embeddings: dict[str, dict] = {}
    centroids: dict[str, list] = {}
    for model_id in embedders:
        emb_model = Model.from_pretrained(model_id, token=os.environ["HF_TOKEN"])
        inference = Inference(emb_model, window="whole", device=device)
        indices: list[int] = []
        vectors: list[np.ndarray] = []
        per_speaker: dict[str, list] = {}
        for turn_index, label, clip in clips:
            wf = torch.tensor(clip, dtype=torch.float32).unsqueeze(0).to(device)
            vector = np.asarray(
                inference({"waveform": wf, "sample_rate": sr}), dtype=np.float32
            ).reshape(-1)
            # Drop non-finite PER TURN: one NaN turn must not be able to poison
            # a centroid (it silently did, for 7 of 86 nodes on June 10).
            if not np.all(np.isfinite(vector)):
                continue
            indices.append(turn_index)
            vectors.append(vector)
            per_speaker.setdefault(label, []).append(vector)
        stacked = (
            np.vstack(vectors).astype(np.float32)
            if vectors else np.zeros((0, 0), dtype=np.float32)
        )
        turn_embeddings[model_id] = {
            "dim": int(stacked.shape[1]) if stacked.size else 0,
            "dtype": "float32",
            "turn_indices": indices,
            "b64": _base64.b64encode(stacked.tobytes()).decode("ascii"),
        }
        model_centroids = {
            label: np.mean(rows, axis=0).tolist() for label, rows in per_speaker.items()
        }
        if not centroids:
            # First embedder listed owns the legacy `centroids` field, which the
            # sequential reconciler path still reads.
            centroids = model_centroids
        print(f"  [chunk {window_index}] {model_id}: {len(indices)} turn vectors, "
              f"{len(model_centroids)} centroids")
```

- [ ] **Step 3: Add the new field to the returned JSON**

In the `return _json.dumps({...})` call, add after `"speech_seconds": speech_seconds,`:

```python
        "turn_embeddings": turn_embeddings,
```

- [ ] **Step 4: Verify the module still imports and nothing else regressed**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -c "import ast,pathlib; ast.parse(pathlib.Path('bench/modal_app.py').read_text()); print('parses')"`
Expected: `parses`

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest -q 2>&1 | tail -3`
Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add bench/modal_app.py
git commit -m "feat: chunk worker returns per-turn embeddings, filters non-finite per turn"
```

---

### Task 7: Orchestrator branch and config knobs

**Files:**
- Modify: `src/config.py`
- Modify: `src/modal_compute.py` (`fetch_chunk_payloads`, `stitch_chunk_payloads`)
- Modify: `tests/test_global_identity.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_global_identity.py`:

```python
import base64
import json

from src.modal_compute import stitch_chunk_payloads


def _payload(window_index, window_start, window_end, turns, vectors, model):
    """Build a worker-shaped payload; `vectors` is {turn_index: np.ndarray}."""
    indices = sorted(vectors)
    stacked = (
        np.vstack([vectors[i] for i in indices]).astype(np.float32)
        if indices else np.zeros((0, 0), dtype=np.float32)
    )
    labels = {}
    for start, end, label in turns:
        labels[label] = labels.get(label, 0.0) + (end - start)
    return json.dumps({
        "window_index": window_index,
        "window_start_s": window_start,
        "window_end_s": window_end,
        "turns": [[s, e, l] for s, e, l in turns],
        "centroids": {label: [1.0, 0.0, 0.0] for label in labels},
        "speech_seconds": labels,
        "elapsed_s": 1.0,
        "turn_embeddings": {model: {
            "dim": int(stacked.shape[1]) if stacked.size else 0,
            "dtype": "float32",
            "turn_indices": indices,
            "b64": base64.b64encode(stacked.tobytes()).decode("ascii"),
        }},
    })


def test_stitch_uses_global_identity_when_turn_embeddings_are_present():
    model = "pyannote/wespeaker-voxceleb-resnet34-LM"
    payloads = [
        _payload(0, 0.0, 65.0, [(0.0, 30.0, "SPEAKER_00")], {0: ALICE}, model),
        _payload(1, 55.0, 120.0, [(70.0, 100.0, "SPEAKER_00")], {0: ALICE_NOISY}, model),
    ]

    segments, centroids = stitch_chunk_payloads(
        payloads, use_merge=False, identity="global",
        cluster_threshold=0.60, embedder=model,
    )

    assert len({s["speaker_label"] for s in segments}) == 1
    assert len(next(iter(centroids.values()))) == 3


def test_stitch_falls_back_to_the_sequential_path_for_legacy_payloads():
    """Payloads cached before per-turn embeddings existed must still stitch, so
    old-vs-new comparison in the sweep costs no GPU."""
    legacy = []
    for payload in [
        _payload(0, 0.0, 65.0, [(0.0, 30.0, "SPEAKER_00")], {0: ALICE},
                 "pyannote/embedding"),
        _payload(1, 55.0, 120.0, [(70.0, 100.0, "SPEAKER_00")], {0: ALICE},
                 "pyannote/embedding"),
    ]:
        data = json.loads(payload)
        del data["turn_embeddings"]
        legacy.append(json.dumps(data))

    segments, centroids = stitch_chunk_payloads(legacy, use_merge=False, identity="global")

    assert len({s["speaker_label"] for s in segments}) == 1  # matched on centroids
    assert len(next(iter(centroids.values()))) == 3


def test_stitch_honours_an_explicit_sequential_request():
    model = "pyannote/wespeaker-voxceleb-resnet34-LM"
    payloads = [
        _payload(0, 0.0, 65.0, [(0.0, 30.0, "SPEAKER_00")], {0: ALICE}, model),
        _payload(1, 55.0, 120.0, [(70.0, 100.0, "SPEAKER_00")], {0: CAROL}, model),
    ]
    segments, _ = stitch_chunk_payloads(
        payloads, use_merge=False, identity="sequential", embedding_threshold=0.50,
    )
    # sequential path matches on the payload `centroids` field (identical here),
    # so it unifies where global identity on the turn vectors would not
    assert len({s["speaker_label"] for s in segments}) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -k stitch -v`
Expected: FAIL — `TypeError: stitch_chunk_payloads() got an unexpected keyword argument 'identity'`

- [ ] **Step 3: Add the config knobs**

In `src/config.py`, immediately after the `DIARIZE_CHUNK_STITCH_THRESHOLD = 0.50` block, add:

```python
# How chunked diarization turns window-local speaker labels into meeting-wide
# ones. "global" (src/global_identity.py) runs ONE constrained agglomerative
# clustering over PER-TURN embeddings at full-meeting scope — the same
# information single-pass clustering uses. "sequential"
# (src/speaker_reconcile.reconcile_chunks) is the older per-window CENTROID
# matcher, kept as an escape hatch and for stitching payloads cached before
# per-turn embeddings existed.
#
# Why global: per-window centroids fragment people (June 10: 49 labels for 41
# people) and the cause is structural, not a threshold. Measured on that
# meeting's human-reviewed transcript: its 86 window-local speakers map onto
# exactly 40 real people, so grouping them is SUFFICIENT; 7 of 86 centroids
# were non-finite (unfiltered NaN turns) and so could never match at all; and
# at the sequential path's 0.50 threshold, centroid matching recovers only
# 83.3% of same-person cross-window pairs while producing ZERO false
# positives — headroom that greedy one-to-one matching against a running mean
# cannot exploit safely.
DIARIZE_CHUNK_IDENTITY = "global"
# Cosine similarity required to merge two clusters of per-turn embeddings.
# NOT interchangeable with DIARIZE_CHUNK_STITCH_THRESHOLD (0.50, per-window
# centroids) or speaker_reconcile.EMBEDDING_MATCH_THRESHOLD (0.75, VibeVoice's
# 50-minute windows): three matchers over three different signals, and reusing
# a value measured on one of them elsewhere is how a tuned number ends up
# somewhere it was never calibrated. Calibrated by
# scripts/sweep_chunk_thresholds.py; conflation (silent quote
# misattribution) is far worse than fragmentation (an extra unnamed speaker
# the review gate catches), so ties break toward the HIGHER value.
DIARIZE_CHUNK_CLUSTER_THRESHOLD = 0.60
# Cluster-distance linkage: "average" (mean pairwise turn similarity),
# "complete" (worst pair — most conservative) or "centroid".
DIARIZE_CHUNK_LINKAGE = "average"
# Which embedder's per-turn vectors the global pass clusters. wespeaker is
# what pyannote 3.1 clusters on internally AND what voice profiles are built
# on (config.EMBEDDING_MODEL), so its centroids need no re-extraction in
# run_local's dimension guard.
DIARIZE_CHUNK_EMBEDDER = EMBEDDING_MODEL
```

- [ ] **Step 4: Add the orchestrator branch**

In `src/modal_compute.py`, change `stitch_chunk_payloads`'s signature to:

```python
def stitch_chunk_payloads(
    payloads: list[str],
    use_merge: bool,
    embedding_threshold: float | None = None,
    merge_threshold: float | None = None,
    identity: str | None = None,
    cluster_threshold: float | None = None,
    linkage: str | None = None,
    embedder: str | None = None,
) -> tuple[list[dict], dict[str, list[float]]]:
```

Extend its docstring with:

```
    `identity` selects the cross-window identity strategy: "global"
    (src.global_identity, one constrained clustering over per-turn
    embeddings) or "sequential" (src.speaker_reconcile, per-window centroid
    matching). Global requires payloads carrying `turn_embeddings`; payloads
    cached before that field existed fall back to sequential automatically, so
    calibration can compare both on the same cached GPU work.
```

Inside the function, after the existing `if embedding_threshold is None:` block, add:

```python
    if identity is None:
        identity = config.DIARIZE_CHUNK_IDENTITY
    if cluster_threshold is None:
        cluster_threshold = config.DIARIZE_CHUNK_CLUSTER_THRESHOLD
    if linkage is None:
        linkage = config.DIARIZE_CHUNK_LINKAGE
    if embedder is None:
        embedder = config.DIARIZE_CHUNK_EMBEDDER
```

Then, immediately after the loop that appends to `chunks` and before the
`print(f"  Slowest window: ...")` line, collect the turn vectors:

```python
    from .global_identity import cluster_global_identities, decode_turn_vectors

    turn_vectors: dict[int, dict[int, "np.ndarray"]] = {}
    have_turn_embeddings = True
    for payload in payloads:
        data = json.loads(payload)
        blocks = data.get("turn_embeddings") or {}
        block = blocks.get(embedder)
        if block is None and len(blocks) == 1:
            block = next(iter(blocks.values()))  # single-embedder payload
        if block is None:
            have_turn_embeddings = False
            break
        turn_vectors[data["window_index"]] = decode_turn_vectors(block)
```

Replace the `result = reconcile_chunks(...)` call and the block that follows it (down to and
including the `centroids = {...}` comprehension and the `speakers without centroid` warning
loop) with:

```python
    if identity == "global" and have_turn_embeddings:
        global_result = cluster_global_identities(
            chunks, turn_vectors,
            threshold=cluster_threshold, linkage=linkage, label_prefix="SPEAKER_",
        )
        result = global_result
        centroids = global_result.centroids
        diag = global_result.diagnostics
        print(f"  Global identity ({linkage} linkage @ {cluster_threshold:.2f}, "
              f"{embedder}): {diag['nodes']} window-local speaker(s) -> "
              f"{diag['clusters']} global (bounds "
              f"{diag['window_speaker_bounds'][0]}-{diag['window_speaker_bounds'][1]}); "
              f"{len(diag['temporal_matches'])} seam match(es), "
              f"{len(diag['embedding_matches'])} embedding merge(s), "
              f"{len(diag['cannot_link_blocks'])} blocked by cannot-link, "
              f"margin {diag['margin']}")
        for speaker in diag["speakers_without_centroid"]:
            print(f"  WARNING: global speaker {speaker} has no centroid "
                  "(no turn of theirs could be embedded); turns still publish "
                  "but voice-profile matching will skip it.")
    else:
        if identity == "global":
            print("  Note: payloads carry no per-turn embeddings for "
                  f"{embedder} — falling back to sequential centroid matching.")
        result = reconcile_chunks(
            chunks, embedding_threshold=embedding_threshold, label_prefix="SPEAKER_"
        )
        diag = result.diagnostics
        print(f"  Reconciled to {len({t.speaker for t in result.turns})} global "
              f"speaker(s): {len(diag['temporal_matches'])} temporal match(es), "
              f"{len(diag['embedding_matches'])} embedding match(es), "
              f"{len(diag['new_speakers'])} new speaker(s)")
        centroids = _recompute_centroids(
            result, per_window_centroids, per_window_speech
        )
```

Move the existing centroid-recomputation code (the `weighted_sums` / `weighted_totals` /
`seen_pairs` loop, the `centroids = {...}` comprehension and the "no centroid" warning loop)
into a new module-level helper directly above `stitch_chunk_payloads`, unchanged in
behaviour:

```python
def _recompute_centroids(result, per_window_centroids, per_window_speech):
    """Duration-weighted global centroids for the sequential path.

    `reconcile_chunks` returns turns + diagnostics but not global voiceprints.
    Each StableTurn retains chunk_index/local_speaker, so the mapping back to
    the chunk-local centroid that produced it is exact. (The global-identity
    path builds centroids from its own per-turn vectors instead.)
    """
```

- [ ] **Step 5: Run the tests**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_global_identity.py -v`
Expected: 27 passed

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest -q 2>&1 | tail -3`
Expected: 0 failures.

- [ ] **Step 6: Thread the embedder choice through the fetch path**

In `src/modal_compute.fetch_chunk_payloads`, add an `embedders: tuple[str, ...] | None = None`
parameter, default it to `(config.DIARIZE_CHUNK_EMBEDDER,)`, and pass it as the sixth
positional element of each starmap tuple:

```python
    args = [
        (meeting_id, start, end, overlap, index, tuple(embedders))
        for index, (start, end) in enumerate(windows)
    ]
```

- [ ] **Step 7: Commit**

```bash
git add src/config.py src/modal_compute.py tests/test_global_identity.py
git commit -m "feat: route chunked diarization through global identity clustering"
```

---

### Task 8: Named-reference accuracy scorer

DER against single-pass measures *change*, not correctness. June 10 and July 29 have
human-reviewed named transcripts, so fragmentation and conflation can be measured against
real people.

**Files:**
- Create: `bench/identity_score.py`
- Create: `tests/test_identity_score.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for scoring diarization identity against a human-reviewed reference."""
import pytest

from bench.identity_score import identity_report, map_labels_to_reference

# A reviewed reference: ALICE speaks twice, BOB once.
REFERENCE = [
    (0.0, 30.0, "Alice"),
    (30.0, 60.0, "Bob"),
    (60.0, 90.0, "Alice"),
]


def test_labels_map_to_the_person_owning_most_of_their_speech():
    hypothesis = [(0.0, 29.0, "SPEAKER_00"), (31.0, 59.0, "SPEAKER_01")]
    mapping = map_labels_to_reference(hypothesis, REFERENCE)
    assert mapping["SPEAKER_00"].person == "Alice"
    assert mapping["SPEAKER_01"].person == "Bob"
    assert mapping["SPEAKER_00"].purity == pytest.approx(1.0)


def test_a_person_split_across_two_labels_is_fragmentation():
    """Alice's two appearances got different labels — exactly what chunked
    diarization does today, and what identify._dedupe_identities then demotes
    to unnamed+needs_review."""
    hypothesis = [(0.0, 30.0, "SPEAKER_00"), (30.0, 60.0, "SPEAKER_01"),
                  (60.0, 90.0, "SPEAKER_02")]
    report = identity_report(hypothesis, REFERENCE)
    assert report.speakers == 3
    assert report.reference_people == 2
    assert [f.person for f in report.fragmentation] == ["Alice"]
    assert sorted(report.fragmentation[0].labels) == ["SPEAKER_00", "SPEAKER_02"]
    assert report.conflation == []


def test_two_people_under_one_label_is_conflation():
    hypothesis = [(0.0, 60.0, "SPEAKER_00"), (60.0, 90.0, "SPEAKER_01")]
    report = identity_report(hypothesis, REFERENCE)
    assert [c.label for c in report.conflation] == ["SPEAKER_00"]
    assert sorted(report.conflation[0].people) == ["Alice", "Bob"]


def test_a_perfect_hypothesis_reports_neither_error():
    hypothesis = [(0.0, 30.0, "SPEAKER_00"), (30.0, 60.0, "SPEAKER_01"),
                  (60.0, 90.0, "SPEAKER_00")]
    report = identity_report(hypothesis, REFERENCE)
    assert report.fragmentation == []
    assert report.conflation == []
    assert report.speakers == 2


def test_sub_floor_slivers_are_not_counted_as_errors():
    """A 0.4s bleed across a boundary is diarization noise, not a second
    person; counting it would make every run look conflated."""
    hypothesis = [(0.0, 30.4, "SPEAKER_00"), (30.4, 60.0, "SPEAKER_01"),
                  (60.0, 90.0, "SPEAKER_00")]
    report = identity_report(hypothesis, REFERENCE, min_seconds=3.0)
    assert report.conflation == []
    assert report.fragmentation == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_identity_score.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.identity_score'`

- [ ] **Step 3: Implement**

```python
"""Score diarization identity against a human-reviewed named transcript.

DER against single-pass output answers "did chunking change what we ship",
which is the honest question when there is no ground truth — but it is a
SIMILARITY measure, and single-pass is not verified correct. Two Bloomington
meetings do have a human-reviewed `transcript_named.json` (June 10: 871
voice_profile + 184 human_review segments over 40 people; July 29: 13
people), which supports the measure that actually matters:

* **fragmentation** — one real person split across two or more labels. This
  is the error chunked diarization makes, and it is not cosmetic:
  `identify._dedupe_identities` treats two labels resolving to one person as
  a mis-identification and demotes all but the highest-confidence one to
  unnamed + needs_review, so an unmerged fragment publishes a real person's
  remarks attributed to nobody if a reviewer misses it.
* **conflation** — one label spanning two real people. Silent quote
  misattribution; strictly worse, and the reason every threshold judgment in
  this pipeline errs toward fewer merges.

Pure: no torch, no Modal, no I/O beyond what the caller passes in.
"""

from __future__ import annotations

from dataclasses import dataclass

Turns = list[tuple[float, float, str]]


@dataclass(frozen=True)
class LabelMapping:
    person: str
    seconds: float
    purity: float


@dataclass(frozen=True)
class Fragmentation:
    person: str
    labels: list[str]
    seconds: dict[str, float]


@dataclass(frozen=True)
class Conflation:
    label: str
    people: list[str]
    seconds: dict[str, float]


@dataclass(frozen=True)
class IdentityReport:
    speakers: int
    reference_people: int
    fragmentation: list[Fragmentation]
    conflation: list[Conflation]
    mapping: dict[str, LabelMapping]


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _cross_seconds(hypothesis: Turns, reference: Turns) -> dict[str, dict[str, float]]:
    matrix: dict[str, dict[str, float]] = {}
    for h_start, h_end, label in hypothesis:
        row = matrix.setdefault(label, {})
        for r_start, r_end, person in reference:
            shared = _overlap(h_start, h_end, r_start, r_end)
            if shared > 0:
                row[person] = row.get(person, 0.0) + shared
    return matrix


def map_labels_to_reference(
    hypothesis: Turns, reference: Turns
) -> dict[str, LabelMapping]:
    """Map each hypothesis label to the reviewed person owning most of its speech."""
    mapping: dict[str, LabelMapping] = {}
    for label, row in _cross_seconds(hypothesis, reference).items():
        total = sum(row.values())
        person, seconds = max(row.items(), key=lambda item: item[1])
        mapping[label] = LabelMapping(
            person=person, seconds=seconds, purity=seconds / total if total else 0.0
        )
    return mapping


def identity_report(
    hypothesis: Turns, reference: Turns, min_seconds: float = 3.0
) -> IdentityReport:
    """Fragmentation and conflation against a human-reviewed reference.

    `min_seconds` floors both error modes: a sub-floor overlap between a label
    and a person is boundary noise (diarization routinely bleeds a word across
    a turn edge), not evidence of a second identity.
    """
    matrix = _cross_seconds(hypothesis, reference)

    conflation: list[Conflation] = []
    for label in sorted(matrix):
        people = {p: s for p, s in matrix[label].items() if s >= min_seconds}
        if len(people) > 1:
            conflation.append(Conflation(
                label=label,
                people=sorted(people),
                seconds={p: round(s, 1) for p, s in sorted(people.items())},
            ))

    by_person: dict[str, dict[str, float]] = {}
    for label, row in matrix.items():
        for person, seconds in row.items():
            if seconds >= min_seconds:
                by_person.setdefault(person, {})[label] = seconds
    fragmentation = [
        Fragmentation(
            person=person,
            labels=sorted(labels),
            seconds={l: round(s, 1) for l, s in sorted(labels.items())},
        )
        for person, labels in sorted(by_person.items())
        if len(labels) > 1
    ]

    return IdentityReport(
        speakers=len({label for _, _, label in hypothesis}),
        reference_people=len({person for _, _, person in reference}),
        fragmentation=fragmentation,
        conflation=conflation,
        mapping=map_labels_to_reference(hypothesis, reference),
    )


def named_reference_turns(transcript_named: dict) -> Turns:
    """Reference turns from a reviewed transcript_named.json payload.

    Segments with no `speaker_name` keep their diarized label so they still
    occupy their audio rather than silently vanishing from the reference.
    """
    return [
        (
            float(segment["start_time"]),
            float(segment["end_time"]),
            segment.get("speaker_name") or f"UNNAMED::{segment['speaker_label']}",
        )
        for segment in transcript_named["segments"]
        if float(segment["end_time"]) > float(segment["start_time"])
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_identity_score.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add bench/identity_score.py tests/test_identity_score.py
git commit -m "test: score diarization identity against human-reviewed names"
```

---

### Task 9: Extend the sweep harness

Same pay-GPU-once-then-sweep-locally method. No new harness.

**Files:**
- Modify: `scripts/sweep_chunk_thresholds.py`

- [ ] **Step 1: Add the new arguments**

Add to the `argparse` block:

```python
    ap.add_argument("--identity", choices=["global", "sequential", "both"],
                    default="global",
                    help="cross-window identity strategy to sweep")
    ap.add_argument("--cluster", type=float, nargs="+",
                    default=[0.70, 0.65, 0.60, 0.55, 0.50, 0.45],
                    help="global-identity cluster similarity threshold(s)")
    ap.add_argument("--linkage", nargs="+", default=["average"],
                    choices=["average", "complete", "centroid"])
    ap.add_argument("--embedder", nargs="+",
                    default=["pyannote/wespeaker-voxceleb-resnet34-LM",
                             "pyannote/embedding"],
                    help="per-turn embedder(s) to cluster; the worker computes "
                         "every one listed in ONE pass so segmentation GPU is "
                         "paid once")
    ap.add_argument("--payload-suffix", default="turnemb",
                    help="cache filename suffix; keeps pre-turn-embedding "
                         "caches intact for old-vs-new comparison")
```

- [ ] **Step 2: Make the payload cache suffix- and embedder-aware**

Replace `_payload_cache` and `_payloads` with:

```python
def _payload_cache(wav: Path, chunk_minutes: int, suffix: str) -> Path:
    stem = f"calibration_chunks_{chunk_minutes}min"
    return wav.parent / (f"{stem}_{suffix}.json" if suffix else f"{stem}.json")


def _payloads(app, wav: Path, meeting_id: str, chunk_minutes: int,
              suffix: str, embedders: list[str]) -> list[str]:
    """Chunk-worker payloads, from cache when available (this is the GPU cost)."""
    cache = _payload_cache(wav, chunk_minutes, suffix)
    if cache.exists():
        print(f"  reusing cached chunk payloads for {chunk_minutes} min "
              f"({cache.name})", flush=True)
        return json.loads(cache.read_text())
    payloads = fetch_chunk_payloads(
        app, wav, meeting_id, chunk_minutes, embedders=tuple(embedders)
    )
    cache.write_text(json.dumps(payloads))
    print(f"  cached {cache.name} ({cache.stat().st_size / 1e6:.1f} MB)", flush=True)
    return payloads
```

- [ ] **Step 3: Sweep the new configuration space**

Replace the innermost `for emb in args.embedding: for mrg in args.merge:` loop with a
configuration list built from the requested identity mode:

```python
                configs: list[dict] = []
                if args.identity in ("global", "both"):
                    configs += [
                        {"identity": "global", "embedder": embedder,
                         "linkage": linkage, "cluster_threshold": cluster,
                         "merge_threshold": mrg}
                        for embedder in args.embedder
                        for linkage in args.linkage
                        for cluster in args.cluster
                        for mrg in args.merge
                    ]
                if args.identity in ("sequential", "both"):
                    configs += [
                        {"identity": "sequential", "embedding_threshold": emb,
                         "merge_threshold": mrg}
                        for emb in args.embedding
                        for mrg in args.merge
                    ]

                for cfg in configs:
                    segments, centroids = stitch_chunk_payloads(
                        payloads, use_merge=True, **cfg
                    )
                    turns = _turns(segments)
                    speakers = len({t[2] for t in turns})
                    hyp = _rttm(turns, meeting_id, Path(tmp) / "hyp.rttm")
                    metrics = calculate_der(ref, hyp)
                    der = metrics["der"] if metrics else None
                    drift = ((speakers - base_speakers) / base_speakers
                             if base_speakers else 0.0)
                    row = {
                        "meeting": meeting_id, "chunk_minutes": chunk_minutes,
                        "der": der, "speakers": speakers,
                        "base_speakers": base_speakers,
                        "speaker_drift": round(drift, 3),
                        "confusion": metrics["confusion"] if metrics else None,
                        "passes_gate": bool(der is not None and der <= DER_GATE
                                            and abs(drift) <= DRIFT_GATE),
                        **cfg,
                    }
                    if named_reference:
                        report = identity_report(turns, named_reference)
                        row["named_fragmentation"] = len(report.fragmentation)
                        row["named_conflation"] = len(report.conflation)
                        row["named_people"] = report.reference_people
                    rows.append(row)
                    label = (f"{cfg['identity']}"
                             + (f" {cfg['linkage']}@{cfg['cluster_threshold']:.2f}"
                                f" {cfg['embedder'].split('/')[-1][:12]}"
                                if cfg["identity"] == "global"
                                else f" emb@{cfg['embedding_threshold']:.2f}"))
                    extra = ""
                    if named_reference:
                        extra = (f" | vs reviewed names: "
                                 f"{row['named_fragmentation']} fragmented, "
                                 f"{row['named_conflation']} conflated")
                    print(f"    {label}: DER {der:.4f}, {speakers} spk "
                          f"(drift {drift:+.1%}) "
                          f"{'PASS' if row['passes_gate'] else 'fail'}{extra}",
                          flush=True)
```

- [ ] **Step 4: Load the named reference and score single-pass too**

Add the import at the top of the script:

```python
from bench.identity_score import identity_report, named_reference_turns  # noqa: E402
```

After the `base_turns, base_elapsed = _reference(...)` call, add:

```python
        named_path = wav.parent / "transcript_named.json"
        named_reference = None
        if named_path.exists():
            named_reference = named_reference_turns(json.loads(named_path.read_text()))
            base_report = identity_report(base_turns, named_reference)
            print(f"  human-reviewed reference: {base_report.reference_people} people; "
                  f"SINGLE-PASS itself fragments {len(base_report.fragmentation)} and "
                  f"conflates {len(base_report.conflation)} of them "
                  "(this is the bar to beat, not DER)", flush=True)
```

- [ ] **Step 5: Add the seam spot-check**

Add this helper and call it for the best passing row's configuration at the end of `main`:

```python
def _seam_report(payloads: list[str], segments: list[dict], window_s: float = 10.0) -> None:
    """Print label continuity across each chunk boundary.

    A person speaking across a seam must not change label there; this prints
    the turns on both sides so a human can see it rather than trusting an
    aggregate.
    """
    seams = sorted({json.loads(p)["window_start_s"] for p in payloads} - {0.0})
    turns = _turns(segments)
    for seam in seams:
        before = [t for t in turns if seam - window_s <= t[1] <= seam]
        after = [t for t in turns if seam <= t[0] <= seam + window_s]
        crossing = {t[2] for t in before} & {t[2] for t in after}
        print(f"  seam @ {seam / 60:.1f} min: {len(before)} turn(s) before, "
              f"{len(after)} after, {len(crossing)} label(s) continuous "
              f"across it: {sorted(crossing)}")
```

- [ ] **Step 6: Verify the script still parses and its help works**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python scripts/sweep_chunk_thresholds.py --help`
Expected: help text listing `--identity`, `--cluster`, `--linkage`, `--embedder`, `--payload-suffix`.

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest -q 2>&1 | tail -3`
Expected: 0 failures.

- [ ] **Step 7: Commit**

```bash
git add scripts/sweep_chunk_thresholds.py
git commit -m "test: sweep global identity configs and score against reviewed names"
```

---

### Task 10: Calibrate (the part that can fail)

GPU is spent here. **Do not re-derive the single-pass references** — they are cached at
`~/CouncilScribe/meetings/<slug>/calibration_single_pass.json` (June 10 7100 s, May 6
3586 s, July 29 584 s) and cost ~2 hours to rebuild. Only chunk payloads are re-fetched,
because per-turn embeddings did not exist when the old ones were cached.

- [ ] **Step 1: Confirm the cached references are present before spending anything**

```bash
ls -la ~/CouncilScribe/meetings/bloomington-city-council-2026-0{6-10,5-06,7-29}/calibration_single_pass.json
```
Expected: three files. If any is missing, STOP and report — do not silently re-run a
2-hour reference arm.

- [ ] **Step 2: Fetch new payloads and sweep the two gate meetings**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python scripts/sweep_chunk_thresholds.py bloomington-city-council-2026-06-10 bloomington-city-council-2026-05-06 --chunks 60 --identity both --cluster 0.75 0.70 0.65 0.60 0.55 0.50 0.45 --linkage average complete --embedder pyannote/wespeaker-voxceleb-resnet34-LM pyannote/embedding 2>&1 | tee /tmp/global-identity-sweep-60min.log
```

Expect ~10 minutes of GPU (5 + 4 windows, ~111 s each, fanned out concurrently) and then a
free local sweep. Watch for: payload cache sizes printed per meeting (if a Modal return-size
error appears, the fallback is writing `.npy` to the volume and returning its path — report
before changing approach), and the `human-reviewed reference` line for June 10.

- [ ] **Step 3: Cross-check the winning threshold on a third meeting**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python scripts/sweep_chunk_thresholds.py bloomington-city-council-2026-07-29 --chunks 45 --identity global --cluster 0.75 0.70 0.65 0.60 0.55 0.50 0.45 --linkage average --embedder <winning embedder> 2>&1 | tee /tmp/global-identity-sweep-jul29.log
```

July 29 does not chunk at 60 minutes, so this is an independent check that the chosen
threshold is not fitted to two meetings — **not** gate evidence. Say so in the write-up.

- [ ] **Step 4: Record the results verbatim**

Write the full result table into the spec's new "Calibration results" section: per meeting,
per configuration — DER, speaker count, drift, fragmented/conflated counts against the
reviewed names, margin, and the slowest window vs the single-pass time.

- [ ] **Step 5: Apply the gate honestly**

The gate is **DER ≤ 0.10 vs single-pass AND speaker count within ±20 %, on both long
meetings**. It is not to be relaxed, re-scoped, or best-of-N'd. The stronger target is
speaker count **at or below** single-pass (41 / 42) with **zero** conflation against the
reviewed names; June 10's measured ceiling is 40.

- If the gate passes AND fragmentation is gone → Task 11.
- If the gate passes but the count still exceeds single-pass → **leave
  `DIARIZE_CHUNK_MINUTES = 0`**, ship the capability, and report which mechanism still
  fragments (use `cannot_link_blocks`, `speakers_without_centroid` and the per-person
  fragmentation list, which names the actual people who split).
- If the gate fails → same, and report the DER/count numbers plainly.

---

### Task 11: Flip the default — only if Task 10 earned it

**Files:**
- Modify: `src/config.py`
- Modify: `docs/superpowers/specs/2026-08-03-global-identity-clustering-design.md`
- Modify: `docs/superpowers/specs/2026-07-31-chunked-parallel-diarization-design.md`

- [ ] **Step 1: Set the calibrated values**

Set `DIARIZE_CHUNK_CLUSTER_THRESHOLD`, `DIARIZE_CHUNK_LINKAGE` and
`DIARIZE_CHUNK_EMBEDDER` to the measured winners, and replace the long
`DIARIZE_CHUNK_MINUTES = 0` comment block (the one that reads "This is the DEFAULT BY
DELIBERATE CHOICE …") with the measured result and `DIARIZE_CHUNK_MINUTES = 60`. Keep the
sentence explaining that meetings under ~90 minutes still take the single-pass path
byte-identically.

- [ ] **Step 2: Append the calibration section to this change's spec**

Include the result table, the chosen configuration, why that threshold (with the shape of the
DER/count curve on both sides of it), what the reviewed-names check showed, and any residual
risk stated as plainly as the previous spec stated June 10's +19.5 % drift.

- [ ] **Step 3: Close the loop in the chunked-diarization spec**

Its final paragraph proposes exactly this change as future work. Append a short note: what
was built, where it lives, and the measured outcome.

- [ ] **Step 4: Full suite green**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest -q 2>&1 | tail -3`
Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add src/config.py docs/superpowers/specs/
git commit -m "feat: enable chunked diarization by default on global identity clustering"
```

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin perf/global-identity-clustering
```

PR body: the problem (fragmentation blocked the default), the measured ceiling of 40, the
architecture, the calibration table, and the gate result. Note that the branch is stacked on
`perf/chunked-diarization` (PR #141) and state explicitly whether #141 should merge first
(still OFF) or the two land together.

---

## Self-Review

**Spec coverage:** worker per-turn embeddings + both embedders + per-turn non-finite filter →
Task 6. `src/global_identity.py` nodes/must-link/cannot-link/linkages/labels/ownership/
centroids/diagnostics incl. `margin` and `window_speaker_bounds` → Tasks 1–5. Orchestrator
branch + legacy-payload fallback + config knobs → Task 7. Named-reference
fragmentation/conflation scorer → Task 8. Sweep flags, cached-reference reuse, seam
spot-check → Task 9. Gate application and the flip → Tasks 10–11. Distance-threshold
rationale is in the spec and the config comment; the K-selection alternatives are
deliberately not implemented (spec: "a new mechanism to calibrate, not a reused one").
`merge_similar_speakers` keeps its existing wiring and is measured in the sweep via
`--merge`. VibeVoice and the single-pass path are untouched.

**Placeholders:** none — every code step carries its code, every command its expected output.
The two deliberate unknowns are values that only measurement can supply: the winning
embedder in Task 10 Step 3, and the calibrated numbers in Task 11 Step 1.

**Type consistency:** `decode_turn_vectors(block) -> dict[int, np.ndarray]`;
`build_nodes(chunks, turn_vectors: dict[int, dict[int, np.ndarray]]) -> list[IdentityNode]`;
`seed_clusters(nodes, chunks) -> (list[int], diagnostics)`;
`node_pair_statistics(nodes) -> NodePairStatistics`;
`merge_clusters(nodes, clusters, stats, *, threshold, linkage) -> (list[int], diagnostics)`;
`cluster_global_identities(...) -> GlobalIdentityResult(turns, diagnostics, centroids)`.
`stitch_chunk_payloads` keeps its `(list[dict], dict[str, list[float]])` return contract, so
`run_diarization`'s callers are unaffected. `identity_report(...) -> IdentityReport` with
`.speakers`, `.reference_people`, `.fragmentation[].person/.labels`, `.conflation[].label/.people`,
`.mapping[label].person/.purity` — the names used in Task 9's row building.
