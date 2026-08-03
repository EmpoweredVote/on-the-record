"""Diarizer-agnostic cross-chunk speaker reconciler.

Any diarization backend that runs on overlapping fixed-length windows (because
whole-file diarization cost grows faster than linearly with duration) ends up
with the same problem: each window's speaker labels (``SPEAKER_00``, ...) are
only meaningful *inside* that window. This module turns those window-local
labels into stable, meeting-wide labels.

Originally written for VibeVoice's 50-minute windows (see ``src/vibevoice.py``,
which now re-imports these names for backwards compatibility); pulled out here
so any diarizer's chunked path can reuse it. The module is pure (numpy /
dataclasses only) and has no torch or Modal dependency, so it is safe to
import from any code path, local or remote.

Matching proceeds in two passes, strongest signal first:

1. **Temporal overlap** — inside the region where two consecutive windows
   physically overlap, a speaker's turns there are matched against the
   previous window's turns by summed overlap seconds. This is a stronger
   signal than voice similarity: two windows diarizing the same few seconds
   of audio pin down "same person" without needing an embedding at all.
2. **Embedding similarity** — any speaker not resolved temporally is matched
   against the running global voiceprints by cosine similarity, greedy
   highest-similarity-first with a ``used_globals`` set. This is one-to-one
   (a global can only absorb one local speaker per window) and, unlike an
   optimal (Hungarian) sum-maximizing assignment, cannot displace a
   near-perfect match onto a worse global just because the total across all
   pairs is higher.

Guards: non-finite embeddings are rejected before they can poison a
voiceprint or crash a similarity computation; a speaker with under
``MIN_EMBEDDING_SPEECH_SECONDS`` of speech is never used to update a global
voiceprint (thin evidence, e.g. someone who says one word near a seam); and
``_ownership_bounds`` clips each window's contributed turns to the midpoint of
its overlap with its neighbours, so overlap audio is evidence for matching but
never double-counted in the final stable-turn timeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


EMBEDDING_MATCH_THRESHOLD = 0.75
MIN_EMBEDDING_SPEECH_SECONDS = 3.0
#: Minimum shared speech, in seconds, for a seam overlap to count as a temporal
#: match. Temporal matching is a MUST-LINK — applied before, and independently
#: of, the embedding threshold — so a wrong one cannot be tuned away.
#: Measured on the May 6 council meeting against its human-reviewed transcript:
#: of 12 seam joins, the 10 correct ones overlapped 1.1-71.0s while the 2 that
#: joined DIFFERENT people overlapped 0.6s and 0.3s, and those two chained three
#: real people into a single speaker at every embedding threshold tested. A
#: sub-second overlap is two windows disagreeing about a turn boundary, not
#: evidence of one speaker; below the floor, voice similarity decides instead.
MIN_SEAM_OVERLAP_SECONDS = 1.0


@dataclass(frozen=True)
class ChunkWindow:
    index: int
    start: float
    end: float


@dataclass(frozen=True)
class LocalTurn:
    chunk_index: int
    start: float
    end: float
    local_speaker: str


@dataclass
class ChunkResult:
    window: ChunkWindow
    turns: list[LocalTurn]
    embeddings: dict[str, np.ndarray] = field(default_factory=dict)
    speech_seconds: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class StableTurn:
    chunk_index: int
    start: float
    end: float
    local_speaker: str
    speaker: str


@dataclass
class ReconciliationResult:
    turns: list[StableTurn]
    diagnostics: dict[str, list[dict[str, Any]]]


def _overlap_seconds(a: LocalTurn, b: LocalTurn, start: float, end: float) -> float:
    return max(0.0, min(a.end, b.end, end) - max(a.start, b.start, start))


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float | None:
    left = np.asarray(left, dtype=float).reshape(-1)
    right = np.asarray(right, dtype=float).reshape(-1)
    if left.shape != right.shape or left.size == 0:
        return None
    if not np.all(np.isfinite(left)) or not np.all(np.isfinite(right)):
        return None
    denominator = np.linalg.norm(left) * np.linalg.norm(right)
    if denominator == 0:
        return None
    return float(np.dot(left, right) / denominator)


def _ownership_bounds(windows: list[ChunkWindow], index: int) -> tuple[float, float]:
    window = windows[index]
    owned_start = window.start
    owned_end = window.end
    if index > 0:
        previous = windows[index - 1]
        owned_start = (window.start + previous.end) / 2
    if index + 1 < len(windows):
        following = windows[index + 1]
        owned_end = (following.start + window.end) / 2
    return owned_start, owned_end


def reconcile_chunks(
    chunks: list[ChunkResult],
    embedding_threshold: float = EMBEDDING_MATCH_THRESHOLD,
    min_embedding_speech_seconds: float = MIN_EMBEDDING_SPEECH_SECONDS,
    label_prefix: str = "SPEAKER_",
    min_seam_overlap_seconds: float = MIN_SEAM_OVERLAP_SECONDS,
) -> ReconciliationResult:
    """Map chunk-local speakers to stable meeting-wide labels.

    Seam overlaps below `min_seam_overlap_seconds` are not treated as temporal
    matches — see that constant for the measurement behind the floor.
    """
    chunks = sorted(chunks, key=lambda chunk: chunk.window.index)
    windows = [chunk.window for chunk in chunks]
    next_global = 0
    mappings: list[dict[str, str]] = []
    global_embeddings: dict[str, np.ndarray] = {}
    global_embedding_weights: dict[str, float] = {}
    diagnostics: dict[str, list[dict[str, Any]]] = {
        "temporal_matches": [],
        "embedding_matches": [],
        "new_speakers": [],
    }

    for chunk_position, chunk in enumerate(chunks):
        local_speakers = sorted({turn.local_speaker for turn in chunk.turns})
        mapping: dict[str, str] = {}
        used_globals: set[str] = set()

        if chunk_position > 0:
            previous = chunks[chunk_position - 1]
            previous_mapping = mappings[-1]
            overlap_start = max(previous.window.start, chunk.window.start)
            overlap_end = min(previous.window.end, chunk.window.end)
            candidates: list[tuple[float, str, str]] = []
            for local in local_speakers:
                current_turns = [
                    turn for turn in chunk.turns if turn.local_speaker == local
                ]
                for previous_local, global_label in previous_mapping.items():
                    previous_turns = [
                        turn
                        for turn in previous.turns
                        if turn.local_speaker == previous_local
                    ]
                    score = sum(
                        _overlap_seconds(current_turn, previous_turn, overlap_start, overlap_end)
                        for current_turn in current_turns
                        for previous_turn in previous_turns
                    )
                    if score >= min_seam_overlap_seconds:
                        candidates.append((score, local, global_label))
            for _, local, global_label in sorted(candidates, reverse=True):
                if local in mapping or global_label in used_globals:
                    continue
                mapping[local] = global_label
                used_globals.add(global_label)
                diagnostics["temporal_matches"].append(
                    {
                        "chunk": chunk.window.index,
                        "local": local,
                        "global": global_label,
                    }
                )

        embedding_candidates: list[tuple[float, str, str]] = []
        for local in local_speakers:
            if local in mapping:
                continue
            speech_seconds = chunk.speech_seconds.get(local, 0.0)
            embedding = chunk.embeddings.get(local)
            if embedding is None or speech_seconds < min_embedding_speech_seconds:
                continue
            for global_label, global_embedding in global_embeddings.items():
                if global_label in used_globals:
                    continue
                similarity = _cosine_similarity(embedding, global_embedding)
                if similarity is not None and similarity >= embedding_threshold:
                    embedding_candidates.append((similarity, local, global_label))
        for similarity, local, global_label in sorted(
            embedding_candidates, reverse=True
        ):
            if local in mapping or global_label in used_globals:
                continue
            mapping[local] = global_label
            used_globals.add(global_label)
            diagnostics["embedding_matches"].append(
                {
                    "chunk": chunk.window.index,
                    "local": local,
                    "global": global_label,
                    "similarity": similarity,
                }
            )

        for local in local_speakers:
            if local in mapping:
                continue
            global_label = f"{label_prefix}{next_global:02d}"
            next_global += 1
            mapping[local] = global_label
            used_globals.add(global_label)
            diagnostics["new_speakers"].append(
                {
                    "chunk": chunk.window.index,
                    "local": local,
                    "global": global_label,
                }
            )

        for local, global_label in mapping.items():
            embedding = chunk.embeddings.get(local)
            weight = chunk.speech_seconds.get(local, 0.0)
            if (
                embedding is None
                or weight < min_embedding_speech_seconds
                or not np.all(np.isfinite(embedding))
            ):
                continue
            vector = np.asarray(embedding, dtype=float)
            previous_weight = global_embedding_weights.get(global_label, 0.0)
            if global_label in global_embeddings:
                vector = (
                    global_embeddings[global_label] * previous_weight
                    + vector * weight
                ) / (previous_weight + weight)
            global_embeddings[global_label] = vector
            global_embedding_weights[global_label] = previous_weight + weight
        mappings.append(mapping)

    stable_turns: list[StableTurn] = []
    for position, (chunk, mapping) in enumerate(zip(chunks, mappings)):
        owned_start, owned_end = _ownership_bounds(windows, position)
        for turn in chunk.turns:
            start = max(turn.start, owned_start)
            end = min(turn.end, owned_end)
            if end <= start:
                continue
            stable_turns.append(
                StableTurn(
                    chunk_index=turn.chunk_index,
                    start=round(start, 3),
                    end=round(end, 3),
                    local_speaker=turn.local_speaker,
                    speaker=mapping[turn.local_speaker],
                )
            )
    stable_turns.sort(key=lambda turn: (turn.start, turn.end, turn.speaker))
    return ReconciliationResult(stable_turns, diagnostics)
