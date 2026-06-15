"""Run the VibeVoice long-meeting GPU sweep concurrently on Modal."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bench.gpu_sweep import render_markdown


FUNCTIONS = [
    ("L40S", "vibevoice_sweep_l40s"),
    ("A100-40GB", "vibevoice_sweep_a100_40gb"),
    ("A100-80GB", "vibevoice_sweep_a100_80gb"),
]


def dispatch_sweep(meeting_id: str, app) -> list[dict]:
    """Start one isolated remote call per GPU and preserve display order."""
    def run_one(requested_gpu: str, function_name: str) -> dict:
        try:
            inference = getattr(app, function_name).remote(meeting_id)
            return app.vibevoice_sweep_reconcile.remote(meeting_id, inference)
        except Exception as exc:
            return {
                "requested_gpu": requested_gpu,
                "status": "failed",
                "error": str(exc),
            }

    with app.app.run():
        with ThreadPoolExecutor(max_workers=len(FUNCTIONS)) as executor:
            futures = [
                executor.submit(run_one, requested_gpu, function_name)
                for requested_gpu, function_name in FUNCTIONS
            ]
            return [future.result() for future in futures]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    from bench import modal_app

    results = dispatch_sweep(args.meeting_id, modal_app)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meeting_id": args.meeting_id,
        "pricing_captured_on": "2026-06-14",
        "pricing_source": "https://modal.com/pricing",
        "gpu_docs": "https://modal.com/docs/guide/gpu",
        "results": results,
    }
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2) + "\n"
    )
    (args.output_dir / "README.md").write_text(
        render_markdown(args.meeting_id, results)
    )
    print(render_markdown(args.meeting_id, results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
