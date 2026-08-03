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
        # dim comes from any vector present in this window, so a local whose
        # own turns had no vector still gets an (0, dim) matrix rather than
        # (0, 0) — shape-compatible with its window siblings' vectors.
        dim = len(next(iter(available.values()))) if available else 0
        for local in sorted(by_local):
            entries = by_local[local]
            rows = [available[position] for position, _ in entries if position in available]
            matrix = np.asarray(rows, dtype=float) if rows else np.zeros((0, dim))
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
