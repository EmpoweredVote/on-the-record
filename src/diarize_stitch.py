"""Unify chunk-local speaker labels into one global label space.

Chunked diarization runs pyannote on fixed windows because its cost is
~quadratic in duration (measured July 2026: 2x duration = 4.5x cost). Each
window's SPEAKER_xx labels are only meaningful inside that window, so the
windows' speakers must be matched to each other by voice embedding.

The load-bearing rule is ONE-TO-ONE per chunk: two labels a chunk's own
clustering called distinct are distinct people, so they must never collapse
into a single global speaker (the identity-collision failure that once
conflated two people in an interview). A greedy nearest-centroid walk can
violate that; an optimal assignment cannot, so matching is solved with
scipy's linear_sum_assignment over the similarity matrix, keeping only pairs
at or above the threshold.

Global centroids are duration-weighted running means: a 10-second appearance
must not move a speaker's voiceprint as much as a 5-minute one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment


@dataclass
class ChunkResult:
    """One window's diarization, in ABSOLUTE meeting time.

    `centroids` and `speech_seconds` are keyed by the chunk-local speaker
    label. A label may appear in `turns`/`speech_seconds` without a centroid
    when it had too little audio to embed.
    """

    start_s: float
    end_s: float
    turns: list[tuple[float, float, str]]
    centroids: dict[str, list[float]]
    speech_seconds: dict[str, float] = field(default_factory=dict)


def _cosine_matrix(locals_: list[np.ndarray], globals_: list[np.ndarray]) -> np.ndarray:
    """Rows = chunk-local centroids, cols = global centroids."""
    if not locals_ or not globals_:
        return np.zeros((len(locals_), len(globals_)))
    a = np.vstack(locals_)
    b = np.vstack(globals_)
    a = a / np.clip(np.linalg.norm(a, axis=1, keepdims=True), 1e-12, None)
    b = b / np.clip(np.linalg.norm(b, axis=1, keepdims=True), 1e-12, None)
    return a @ b.T


def stitch_chunks(
    chunks: list[ChunkResult], threshold: float
) -> tuple[list[tuple[float, float, str]], dict[str, list[float]], list[str]]:
    """Merge per-chunk diarizations into one globally-labelled turn list.

    Returns (turns sorted by start time, global centroids, human-readable log).
    Global labels are assigned in chronological order of first appearance:
    SPEAKER_00, SPEAKER_01, ...
    """
    log: list[str] = []
    # Global speaker table, parallel lists so assignment indices map directly.
    g_vectors: list[np.ndarray] = []
    g_weights: list[float] = []
    g_turns: list[list[tuple[float, float]]] = []

    for chunk in sorted(chunks, key=lambda c: c.start_s):
        local_labels = sorted(chunk.centroids)
        local_vecs = [np.asarray(chunk.centroids[label], dtype=float) for label in local_labels]
        assigned: dict[str, int] = {}

        if local_vecs and g_vectors:
            sims = _cosine_matrix(local_vecs, g_vectors)
            # Maximize total similarity => minimize its negation.
            rows, cols = linear_sum_assignment(-sims)
            for row, col in zip(rows, cols):
                if sims[row, col] >= threshold:
                    assigned[local_labels[row]] = col
                    log.append(
                        f"chunk@{chunk.start_s:.0f}s {local_labels[row]} -> "
                        f"global {col} (sim {sims[row, col]:.3f})"
                    )

        # Unmatched locals (and every local in the first chunk) open new globals.
        for label, vec in zip(local_labels, local_vecs):
            if label in assigned:
                continue
            g_vectors.append(vec)
            g_weights.append(0.0)
            g_turns.append([])
            assigned[label] = len(g_vectors) - 1
            log.append(f"chunk@{chunk.start_s:.0f}s {label} -> new global {assigned[label]}")

        # A turn whose label never got a centroid still happened; give it its
        # own global speaker rather than dropping or guessing it.
        for label in {lbl for _, _, lbl in chunk.turns} - set(assigned):
            g_vectors.append(np.zeros_like(g_vectors[0]) if g_vectors else np.zeros(1))
            g_weights.append(0.0)
            g_turns.append([])
            assigned[label] = len(g_vectors) - 1
            log.append(
                f"chunk@{chunk.start_s:.0f}s {label} -> new global {assigned[label]} "
                "(no centroid: too little audio to embed)"
            )

        # Fold this chunk's turns and voice evidence into the global table.
        for start, end, label in chunk.turns:
            g_turns[assigned[label]].append((start, end))
        for label, index in assigned.items():
            weight = max(chunk.speech_seconds.get(label, 0.0), 0.0)
            if weight <= 0 or label not in chunk.centroids:
                continue
            vec = np.asarray(chunk.centroids[label], dtype=float)
            total = g_weights[index] + weight
            g_vectors[index] = (
                (g_vectors[index] * g_weights[index] + vec * weight) / total
                if g_weights[index] > 0
                else vec
            )
            g_weights[index] = total

    # Relabel by chronological first appearance so output is stable and reads
    # like single-pass output.
    first_seen = {
        index: min((start for start, _ in spans), default=float("inf"))
        for index, spans in enumerate(g_turns)
    }
    order = sorted(first_seen, key=lambda i: (first_seen[i], i))
    names = {index: f"SPEAKER_{rank:02d}" for rank, index in enumerate(order)}

    turns = sorted(
        (
            (start, end, names[index])
            for index, spans in enumerate(g_turns)
            for start, end in spans
        ),
        key=lambda t: (t[0], t[1]),
    )
    centroids = {
        names[index]: list(g_vectors[index])
        for index in order
        if g_weights[index] > 0
    }
    return turns, centroids, log
