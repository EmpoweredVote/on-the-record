import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import run_local
from bench import run as bench_run
from src import modal_compute
from src.vibevoice import VIBEVOICE_MODEL_ID, VIBEVOICE_MODEL_REVISION


def test_validate_vibevoice_requires_modal_compute():
    with pytest.raises(ValueError, match="requires --compute modal"):
        run_local._validate_diarizer_compute(
            SimpleNamespace(diarizer="vibevoice", compute="local")
        )


def test_validate_vibevoice_accepts_modal_compute():
    run_local._validate_diarizer_compute(
        SimpleNamespace(diarizer="vibevoice", compute="modal")
    )


def test_diarization_model_name_includes_pinned_vibevoice_revision():
    assert run_local._diarization_model_name("vibevoice") == (
        f"{VIBEVOICE_MODEL_ID}@{VIBEVOICE_MODEL_REVISION}"
    )


def test_benchmark_dispatches_vibevoice_function():
    expected = {"model": "vibevoice"}

    class Remote:
        def __init__(self, result):
            self.result = result

        def remote(self, *args):
            return self.result

    app = SimpleNamespace(
        vibevoice_infer_chunks=Remote("/vol/vibevoice/meeting-1/inference.json"),
        diarize_vibevoice=Remote(expected),
    )

    assert bench_run.diarize_one("vibevoice", "meeting-1", app) == expected


def test_modal_compute_dispatches_vibevoice_then_embedding_extraction(
    monkeypatch, tmp_path
):
    calls = []

    class Remote:
        def __init__(self, name, result):
            self.name = name
            self.result = result

        def remote(self, *args, **kwargs):
            calls.append((self.name, args, kwargs))
            return self.result

    app = SimpleNamespace(
        app=SimpleNamespace(run=nullcontext),
        vibevoice_infer_chunks=Remote(
            "inference", "/vol/vibevoice/meeting-1/inference.json"
        ),
        pipeline_vibevoice_diarize=Remote(
            "vibevoice",
            json.dumps(
                {
                    "segments": [
                        {
                            "segment_id": 0,
                            "start_time": 0.0,
                            "end_time": 1.0,
                            "speaker_label": "VIBE_00",
                            "text": "",
                            "words": [],
                        }
                    ],
                    "diagnostics": {"chunks": []},
                }
            ),
        ),
        pipeline_extract_embeddings=Remote(
            "embeddings", json.dumps({"VIBE_00": [1.0, 0.0]})
        ),
    )
    monkeypatch.setattr(modal_compute, "_modal_app", lambda: app)
    monkeypatch.setattr(modal_compute, "upload_audio", lambda *_: None)
    wav_path = tmp_path / "audio.wav"
    wav_path.write_bytes(b"wav")

    segments, embeddings = modal_compute.run_diarization(
        wav_path, "meeting-1", diarizer="vibevoice"
    )

    assert segments[0]["speaker_label"] == "VIBE_00"
    assert embeddings == {"VIBE_00": [1.0, 0.0]}
    assert [call[0] for call in calls] == [
        "inference",
        "vibevoice",
        "embeddings",
    ]
    assert calls[1][1] == (
        "meeting-1",
        "/vol/vibevoice/meeting-1/inference.json",
    )
    diagnostics_path = tmp_path / "vibevoice_diagnostics.json"
    assert json.loads(diagnostics_path.read_text()) == {"chunks": []}
