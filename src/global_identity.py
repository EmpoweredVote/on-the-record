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
) -> tuple[list[int], dict[str, Any]]:
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


def node_pair_statistics(nodes: list[IdentityNode]) -> NodePairStatistics:
    """Precompute node-pair similarity aggregates from per-turn vectors."""
    count = len(nodes)
    rows = [node.vectors for node in nodes if node.vectors.size]
    dims = {matrix.shape[1] for matrix in rows}
    if len(dims) > 1:
        raise ValueError(
            f"embedded turns carry mismatched dimensions {sorted(dims)} — this "
            "means vectors from two different embedding models (e.g. 256-dim "
            "wespeaker and 512-dim pyannote/embedding) ended up in one meeting's "
            "payload"
        )
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
) -> tuple[list[int], dict[str, Any]]:
    """Merge clusters by per-turn similarity, respecting cannot-link.

    Repeatedly joins the single most similar admissible pair while its
    similarity is at or above `threshold`. Closest-first (rather than
    first-found) is what lets a person's strongest cross-window match claim
    them before a weaker candidate can.

    Diagnostics carry `embedding_matches`, `cannot_link_blocks` (pairs above
    threshold refused by the constraint, as of the FINAL scan only — a block
    can never be lifted, so re-recording it on every intervening iteration
    would just inflate the count) and `margin` — how far below the threshold
    the nearest non-merge sat. A small margin means the run came close to the
    conflation cliff even if the speaker count looks right.
    """
    if linkage not in {"average", "complete", "centroid"}:
        raise ValueError(f"unknown linkage {linkage!r}; use average, complete or centroid")

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
        blocked: list[dict[str, Any]] = []
        for a, b in ((a, b) for a in members for b in members if b > a):
            similarity = _cluster_similarity(members[a], members[b], stats, linkage)
            if similarity == float("-inf"):
                continue
            if occupied[a] & occupied[b]:
                if similarity >= threshold:
                    blocked.append({
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
        diagnostics["cannot_link_blocks"] = blocked
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
