import threading
import time
from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bench.gpu_sweep import (
    GPU_PRICES_PER_SECOND,
    estimate_gpu_cost,
    gpu_key,
    render_markdown,
)
from bench.run_vibevoice_gpu_sweep import dispatch_sweep


def test_gpu_sweep_script_can_run_directly():
    repo_root = Path(__file__).resolve().parent.parent
    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "bench" / "run_vibevoice_gpu_sweep.py"),
            "--help",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--meeting-id" in completed.stdout


def test_modal_app_exposes_all_gpu_sweep_functions():
    pytest.importorskip("modal")  # optional cloud-benchmark SDK, not in requirements.txt
    from bench import modal_app

    assert modal_app.vibevoice_sweep_l40s is not None
    assert modal_app.vibevoice_sweep_a100_40gb is not None
    assert modal_app.vibevoice_sweep_a100_80gb is not None
    assert modal_app.vibevoice_sweep_reconcile is not None


@pytest.mark.parametrize(
    ("gpu", "expected"),
    [
        ("L40S", "l40s"),
        ("A100-40GB", "a100-40gb"),
        ("A100-80GB", "a100-80gb"),
        ("NVIDIA A100-SXM4-80GB", "a100-80gb"),
    ],
)
def test_gpu_key_normalizes_requested_and_actual_names(gpu, expected):
    assert gpu_key(gpu) == expected


def test_estimate_gpu_cost_uses_requested_gpu_price():
    assert estimate_gpu_cost("L40S", 1800) == pytest.approx(
        GPU_PRICES_PER_SECOND["L40S"] * 1800
    )


def test_render_markdown_includes_success_and_failure():
    markdown = render_markdown(
        "meeting-1",
        [
            {
                "requested_gpu": "L40S",
                "actual_gpu": "NVIDIA L40S",
                "status": "ok",
                "inference_seconds": 100.0,
                "reconciliation_seconds": 3.0,
                "cost_usd": 0.0542,
                "peak_reserved_gib": 31.25,
                "num_turns": 120,
                "num_speakers": 9,
                "parse_errors": 2,
                "temporal_matches": 4,
                "embedding_matches": 7,
            },
            {
                "requested_gpu": "A100-40GB",
                "actual_gpu": None,
                "status": "failed",
                "error": "CUDA out of memory",
            },
        ],
    )

    assert "# VibeVoice GPU Sweep: meeting-1" in markdown
    assert "| L40S | NVIDIA L40S | ok |" in markdown
    assert "100.0s | 3.0s" in markdown
    assert "31.25 GiB" in markdown
    assert "CUDA out of memory" in markdown
    assert "requested GPU rate" in markdown


def test_dispatch_sweep_starts_all_gpu_functions_concurrently():
    barrier = threading.Barrier(3)
    active = 0
    max_active = 0
    lock = threading.Lock()

    class Remote:
        def __init__(self, result):
            self.result = result

        def remote(self, meeting_id):
            nonlocal active, max_active
            assert meeting_id == "meeting-1"
            with lock:
                active += 1
                max_active = max(max_active, active)
            barrier.wait(timeout=2)
            time.sleep(0.01)
            with lock:
                active -= 1
            return self.result

    app = SimpleNamespace(
        app=SimpleNamespace(run=nullcontext),
        vibevoice_sweep_l40s=Remote(
            {"requested_gpu": "L40S", "manifest_path": "/vol/l40s.json"}
        ),
        vibevoice_sweep_a100_40gb=Remote(
            {
                "requested_gpu": "A100-40GB",
                "manifest_path": "/vol/a100-40gb.json",
            }
        ),
        vibevoice_sweep_a100_80gb=Remote(
            {
                "requested_gpu": "A100-80GB",
                "manifest_path": "/vol/a100-80gb.json",
            }
        ),
    )

    class ReconcileRemote:
        def remote(self, meeting_id, inference):
            return {
                **inference,
                "meeting_id": meeting_id,
                "status": "ok",
            }

    app.vibevoice_sweep_reconcile = ReconcileRemote()

    results = dispatch_sweep("meeting-1", app)

    assert max_active == 3
    assert [result["requested_gpu"] for result in results] == [
        "L40S",
        "A100-40GB",
        "A100-80GB",
    ]
