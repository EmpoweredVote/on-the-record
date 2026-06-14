"""Pure helpers for adapting VibeVoice-ASR output to diarization turns."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from math import gcd
from typing import Any

import numpy as np


CHUNK_SECONDS = 50 * 60
OVERLAP_SECONDS = 60
VIBEVOICE_MAX_NEW_TOKENS = 65_536
VIBEVOICE_TARGET_SAMPLE_RATE = 24_000
EMBEDDING_MATCH_THRESHOLD = 0.75
MIN_EMBEDDING_SPEECH_SECONDS = 3.0
VIBEVOICE_MODEL_ID = "microsoft/VibeVoice-ASR"
VIBEVOICE_MODEL_REVISION = "d0c9efdb8d614685062c04425d91e01b6f37d944"
VIBEVOICE_CODE_REVISION = "303b2833e01cff4578ec278bbfe536da54bd19fe"
VIBEVOICE_TOKENIZER_ID = "Qwen/Qwen2.5-7B"
VIBEVOICE_TOKENIZER_REVISION = "d149729398750b98c0af14eb82c78cfe92750796"


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


def build_chunk_windows(
    duration_seconds: float,
    chunk_seconds: float = CHUNK_SECONDS,
    overlap_seconds: float = OVERLAP_SECONDS,
) -> list[ChunkWindow]:
    """Split audio into overlapping windows without exceeding the duration."""
    if duration_seconds <= 0:
        return []
    if overlap_seconds < 0 or chunk_seconds <= overlap_seconds:
        raise ValueError("chunk_seconds must be greater than overlap_seconds")

    windows: list[ChunkWindow] = []
    start = 0.0
    step = chunk_seconds - overlap_seconds
    while start < duration_seconds:
        end = min(duration_seconds, start + chunk_seconds)
        windows.append(ChunkWindow(len(windows), round(start, 3), round(end, 3)))
        if end >= duration_seconds:
            break
        start += step
    return windows


def resample_audio(
    samples: np.ndarray,
    source_sample_rate: int,
    target_sample_rate: int = VIBEVOICE_TARGET_SAMPLE_RATE,
) -> np.ndarray:
    """Resample NumPy audio because VibeVoice assumes arrays are already 24 kHz."""
    if source_sample_rate <= 0 or target_sample_rate <= 0:
        raise ValueError("sample rates must be positive")
    samples = np.asarray(samples, dtype=np.float32)
    if source_sample_rate == target_sample_rate:
        return samples

    from scipy.signal import resample_poly

    divisor = gcd(source_sample_rate, target_sample_rate)
    return np.asarray(
        resample_poly(
            samples,
            target_sample_rate // divisor,
            source_sample_rate // divisor,
        ),
        dtype=np.float32,
    )


def normalize_speaker_label(value: Any) -> str:
    """Normalize generated speaker IDs while preserving non-numeric labels."""
    text = str(value).strip()
    if not text:
        raise ValueError("speaker_id is empty")
    match = re.search(r"(\d+)$", text)
    if match:
        return f"SPEAKER_{int(match.group(1)):02d}"
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    if not normalized:
        raise ValueError("speaker_id contains no usable characters")
    return normalized


def parse_vibevoice_segments(
    segments: Any,
    window: ChunkWindow,
) -> tuple[list[LocalTurn], list[dict[str, Any]]]:
    """Validate VibeVoice structured segments and convert to global timestamps."""
    turns: list[LocalTurn] = []
    errors: list[dict[str, Any]] = []
    if not isinstance(segments, list):
        return [], [{"index": None, "error": "segments must be a list"}]

    duration = window.end - window.start
    for index, item in enumerate(segments):
        try:
            if not isinstance(item, dict):
                raise ValueError("segment must be an object")
            start = float(item["start_time"])
            end = float(item["end_time"])
            if not math.isfinite(start) or not math.isfinite(end):
                raise ValueError("timestamps must be finite")
            if end <= start:
                raise ValueError("end_time must be greater than start_time")
            speaker = normalize_speaker_label(item["speaker_id"])
            start = max(0.0, start)
            end = min(duration, end)
            if end <= start:
                raise ValueError("segment falls outside its chunk")
            turns.append(
                LocalTurn(
                    chunk_index=window.index,
                    start=round(window.start + start, 3),
                    end=round(window.start + end, 3),
                    local_speaker=speaker,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"index": index, "error": str(exc), "segment": item})
    turns.sort(key=lambda turn: (turn.start, turn.end, turn.local_speaker))
    return turns, errors


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
) -> ReconciliationResult:
    """Map chunk-local speakers to stable meeting-wide labels."""
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
                    if score > 0:
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
            global_label = f"VIBE_{next_global:02d}"
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
