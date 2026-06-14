import math

import numpy as np
import pytest

from src.vibevoice import (
    ChunkResult,
    ChunkWindow,
    LocalTurn,
    build_chunk_windows,
    parse_vibevoice_segments,
    reconcile_chunks,
    resample_audio,
)


def test_build_chunk_windows_uses_fifty_minutes_with_sixty_second_overlap():
    windows = build_chunk_windows(6600.0)

    assert windows == [
        ChunkWindow(index=0, start=0.0, end=3000.0),
        ChunkWindow(index=1, start=2940.0, end=5940.0),
        ChunkWindow(index=2, start=5880.0, end=6600.0),
    ]


def test_build_chunk_windows_returns_one_short_window():
    assert build_chunk_windows(90.0) == [
        ChunkWindow(index=0, start=0.0, end=90.0)
    ]


def test_resample_audio_preserves_duration_for_vibevoice_target_rate():
    samples = np.zeros(16_000 * 3, dtype=np.float32)

    resampled = resample_audio(samples, 16_000)

    assert resampled.dtype == np.float32
    assert len(resampled) == 24_000 * 3


def test_parse_vibevoice_segments_normalizes_labels_and_offsets_times():
    turns, errors = parse_vibevoice_segments(
        [
            {
                "start_time": 1.25,
                "end_time": 3.5,
                "speaker_id": "Speaker 2",
                "text": "ignored",
            }
        ],
        ChunkWindow(index=1, start=2940.0, end=5940.0),
    )

    assert errors == []
    assert turns == [
        LocalTurn(
            chunk_index=1,
            start=2941.25,
            end=2943.5,
            local_speaker="SPEAKER_02",
        )
    ]


@pytest.mark.parametrize(
    "item",
    [
        {},
        {"start_time": "bad", "end_time": 2, "speaker_id": 1},
        {"start_time": 2, "end_time": 1, "speaker_id": 1},
        {"start_time": math.nan, "end_time": 1, "speaker_id": 1},
        {"start_time": 0, "end_time": 1, "speaker_id": ""},
    ],
)
def test_parse_vibevoice_segments_reports_invalid_items(item):
    turns, errors = parse_vibevoice_segments(
        [item], ChunkWindow(index=0, start=0.0, end=10.0)
    )

    assert turns == []
    assert len(errors) == 1
    assert errors[0]["index"] == 0


def test_parse_vibevoice_segments_clips_to_chunk_duration():
    turns, errors = parse_vibevoice_segments(
        [{"start_time": -1, "end_time": 12, "speaker_id": "A"}],
        ChunkWindow(index=0, start=10.0, end=20.0),
    )

    assert errors == []
    assert turns == [
        LocalTurn(
            chunk_index=0,
            start=10.0,
            end=20.0,
            local_speaker="A",
        )
    ]


def test_reconcile_chunks_assigns_overlap_to_midpoint_once():
    result = reconcile_chunks(
        [
            ChunkResult(
                window=ChunkWindow(0, 0.0, 100.0),
                turns=[LocalTurn(0, 80.0, 100.0, "A")],
            ),
            ChunkResult(
                window=ChunkWindow(1, 90.0, 190.0),
                turns=[LocalTurn(1, 90.0, 110.0, "B")],
            ),
        ]
    )

    assert [(t.start, t.end, t.speaker) for t in result.turns] == [
        (80.0, 95.0, "VIBE_00"),
        (95.0, 110.0, "VIBE_00"),
    ]
    assert result.diagnostics["temporal_matches"] == [
        {"chunk": 1, "local": "B", "global": "VIBE_00"}
    ]


def test_reconcile_chunks_uses_embedding_fallback_for_non_overlap_speaker():
    result = reconcile_chunks(
        [
            ChunkResult(
                window=ChunkWindow(0, 0.0, 100.0),
                turns=[LocalTurn(0, 0.0, 20.0, "A")],
                embeddings={"A": np.array([1.0, 0.0])},
                speech_seconds={"A": 20.0},
            ),
            ChunkResult(
                window=ChunkWindow(1, 90.0, 190.0),
                turns=[LocalTurn(1, 120.0, 140.0, "B")],
                embeddings={"B": np.array([0.8, 0.1])},
                speech_seconds={"B": 20.0},
            ),
        ]
    )

    assert {t.speaker for t in result.turns} == {"VIBE_00"}
    assert result.diagnostics["embedding_matches"] == [
        {
            "chunk": 1,
            "local": "B",
            "global": "VIBE_00",
            "similarity": pytest.approx(0.9922778767),
        }
    ]


@pytest.mark.parametrize(
    "embedding,speech_seconds",
    [
        (np.array([np.nan, 0.0]), 20.0),
        (np.array([1.0, 0.0]), 2.99),
    ],
)
def test_reconcile_chunks_rejects_invalid_or_too_short_embedding(
    embedding, speech_seconds
):
    result = reconcile_chunks(
        [
            ChunkResult(
                window=ChunkWindow(0, 0.0, 100.0),
                turns=[LocalTurn(0, 0.0, 20.0, "A")],
                embeddings={"A": np.array([1.0, 0.0])},
                speech_seconds={"A": 20.0},
            ),
            ChunkResult(
                window=ChunkWindow(1, 90.0, 190.0),
                turns=[LocalTurn(1, 120.0, 140.0, "B")],
                embeddings={"B": embedding},
                speech_seconds={"B": speech_seconds},
            ),
        ]
    )

    assert {t.speaker for t in result.turns} == {"VIBE_00", "VIBE_01"}
    assert result.diagnostics["embedding_matches"] == []


def test_reconcile_chunks_does_not_merge_concurrent_local_speakers():
    result = reconcile_chunks(
        [
            ChunkResult(
                window=ChunkWindow(0, 0.0, 100.0),
                turns=[LocalTurn(0, 90.0, 100.0, "A")],
                embeddings={"A": np.array([1.0, 0.0])},
                speech_seconds={"A": 10.0},
            ),
            ChunkResult(
                window=ChunkWindow(1, 90.0, 190.0),
                turns=[
                    LocalTurn(1, 90.0, 100.0, "B"),
                    LocalTurn(1, 92.0, 98.0, "C"),
                ],
                embeddings={
                    "B": np.array([1.0, 0.0]),
                    "C": np.array([1.0, 0.0]),
                },
                speech_seconds={"B": 10.0, "C": 6.0},
            ),
        ]
    )

    speakers = {
        t.local_speaker: t.speaker
        for t in result.turns
        if t.chunk_index == 1
    }
    assert speakers["B"] == "VIBE_00"
    assert speakers["C"] != "VIBE_00"
