"""Local orchestrator for the diarization benchmark.

Reads bench/meetings.yaml, calls each Modal function for every (model,
meeting) pair, then downloads the RTTMs + metadata locally for scoring.

Usage:
    python bench/run.py                      # full sweep
    python bench/run.py --models pyannote_oss --meetings 2026-02-25-council
    python bench/run.py --skip-fetch         # assume audio already in volume
    python bench/run.py --skip-score         # don't run scorer at the end
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BENCH_DIR / "results"


def load_config() -> dict:
    with (BENCH_DIR / "meetings.yaml").open() as f:
        return yaml.safe_load(f)


def fetch_one(meeting: dict, app) -> dict:
    """Download + normalize a single meeting on Modal (cached after first run)."""
    mid = meeting["id"]
    if "permalink" in meeting:
        return app.fetch_meeting.remote(mid, permalink=meeting["permalink"])
    return app.fetch_meeting.remote(mid, latest_filter=meeting["latest"])


def diarize_one(model: str, meeting_id: str, app) -> dict:
    """Dispatch a diarization run to the matching Modal function."""
    if model == "vibevoice":
        inference_path = app.vibevoice_infer_chunks.remote(meeting_id)
        return app.diarize_vibevoice.remote(meeting_id, inference_path)

    function_names = {
        "pyannote_oss": "diarize_pyannote_oss",
        "pyannote_merged": "diarize_pyannote_merged",
        "pyannote_ai": "diarize_pyannote_ai",
        "nemo_sortformer": "diarize_nemo_sortformer",
    }
    function_name = function_names.get(model)
    if function_name is None:
        raise ValueError(f"Unknown model: {model}")
    fn = getattr(app, function_name)
    return fn.remote(meeting_id)


def pull_results(run_dir: Path) -> None:
    """Download `/vol/results/*` from the Modal volume into the local run dir.

    Uses the same Python interpreter's `modal` CLI (sibling of `python` in the
    venv's bin/) so we don't depend on `modal` being on the system PATH.
    """
    print(f"\n  Pulling results from Modal volume → {run_dir} ...")
    run_dir.mkdir(parents=True, exist_ok=True)
    modal_cli = Path(sys.executable).parent / "modal"
    subprocess.run(
        [str(modal_cli), "volume", "get", "councilscribe-bench",
         "/results", str(run_dir), "--force"],
        check=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", help="Subset of models to run")
    ap.add_argument("--meetings", nargs="+", help="Subset of meeting IDs to run")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="Assume audio is already in the volume (skip CATS TV download)")
    ap.add_argument("--skip-score", action="store_true",
                    help="Don't auto-run the scorer after the sweep")
    ap.add_argument("--continue-on-error", action="store_true",
                    help="Don't abort if a single (model, meeting) run raises")
    args = ap.parse_args()

    cfg = load_config()
    meetings = cfg["meetings"]
    if args.meetings:
        meetings = [m for m in meetings if m["id"] in args.meetings]
        if not meetings:
            print(f"No meetings matched: {args.meetings}", file=sys.stderr)
            return 1

    models = args.models or cfg["models"]

    # Lazy import — fails fast if Modal isn't installed before printing the plan
    import modal  # noqa: F401
    sys.path.insert(0, str(REPO_ROOT))
    from bench import modal_app as app

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = RESULTS_DIR / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Run dir: {run_dir}")
    print(f"Meetings: {[m['id'] for m in meetings]}")
    print(f"Models:   {models}")
    print()

    sweep_log: list[dict] = []

    # Modal 1.x requires the App to be explicitly running for out-of-band
    # .remote() calls. `with app.run()` brings the app up once for the whole
    # sweep — much faster than entering/exiting per call.
    with app.app.run():
        # --- Stage 1: fetch ---
        if not args.skip_fetch:
            print("=== Fetching meetings ===")
            for m in meetings:
                print(f"  → {m['id']}")
                info = fetch_one(m, app)
                sweep_log.append({"phase": "fetch", **info})
            print()

        # --- Stage 2: diarize (sweep all model × meeting pairs) ---
        print("=== Diarizing ===")
        for m in meetings:
            for model in models:
                label = f"{m['id']} / {model}"
                print(f"  → {label}")
                t0 = time.time()
                try:
                    result = diarize_one(model, m["id"], app)
                    wall = time.time() - t0
                    print(f"    ok ({wall:.0f}s wall, {result.get('elapsed_seconds')}s gpu, "
                          f"{result.get('num_distinct_speakers')} speakers, "
                          f"${result.get('cost_usd'):.3f})")
                    sweep_log.append({"phase": "diarize", "ok": True, **result})
                except Exception as e:
                    print(f"    FAILED: {e}")
                    sweep_log.append({"phase": "diarize", "ok": False,
                                      "meeting_id": m["id"], "model": model,
                                      "error": str(e)})
                    if not args.continue_on_error:
                        return 1

    # --- Stage 3: download artifacts locally ---
    pull_results(run_dir)
    (run_dir / "sweep_log.json").write_text(json.dumps(sweep_log, indent=2))

    # --- Stage 4: score (unless suppressed) ---
    if not args.skip_score:
        print("\n=== Scoring ===")
        from bench import score
        score.score_run(run_dir, cfg)

    print(f"\nDone. Results in {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
