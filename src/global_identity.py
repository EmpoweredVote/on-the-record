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
