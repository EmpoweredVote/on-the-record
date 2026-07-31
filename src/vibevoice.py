"""Pure helpers for adapting VibeVoice-ASR output to diarization turns."""

from __future__ import annotations

from math import gcd
import math
import re
from typing import Any

import numpy as np

from .speaker_reconcile import (
    ChunkResult,
    ChunkWindow,
    EMBEDDING_MATCH_THRESHOLD,
    LocalTurn,
    MIN_EMBEDDING_SPEECH_SECONDS,
    ReconciliationResult,
    StableTurn,
    _cosine_similarity,
    _overlap_seconds,
    _ownership_bounds,
)
from .speaker_reconcile import reconcile_chunks as _reconcile_chunks


CHUNK_SECONDS = 50 * 60
OVERLAP_SECONDS = 60
VIBEVOICE_MAX_NEW_TOKENS = 65_536
VIBEVOICE_TARGET_SAMPLE_RATE = 24_000
VIBEVOICE_MODEL_ID = "microsoft/VibeVoice-ASR"
VIBEVOICE_MODEL_REVISION = "d0c9efdb8d614685062c04425d91e01b6f37d944"
VIBEVOICE_CODE_REVISION = "303b2833e01cff4578ec278bbfe536da54bd19fe"
VIBEVOICE_TOKENIZER_ID = "Qwen/Qwen2.5-7B"
VIBEVOICE_TOKENIZER_REVISION = "d149729398750b98c0af14eb82c78cfe92750796"


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


def reconcile_chunks(
    chunks: list[ChunkResult],
    embedding_threshold: float = EMBEDDING_MATCH_THRESHOLD,
    min_embedding_speech_seconds: float = MIN_EMBEDDING_SPEECH_SECONDS,
    label_prefix: str = "VIBE_",
) -> ReconciliationResult:
    """Map chunk-local speakers to stable meeting-wide labels.

    Thin VibeVoice-flavoured wrapper around the diarizer-agnostic
    ``speaker_reconcile.reconcile_chunks``: defaults ``label_prefix`` to
    ``"VIBE_"`` so VibeVoice's output stays byte-identical to before the
    reconciler was extracted into its own module.
    """
    return _reconcile_chunks(
        chunks,
        embedding_threshold=embedding_threshold,
        min_embedding_speech_seconds=min_embedding_speech_seconds,
        label_prefix=label_prefix,
    )
