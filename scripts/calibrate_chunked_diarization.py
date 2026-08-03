#!/usr/bin/env python
"""Compare chunked vs single-pass diarization on already-processed meetings.

For each meeting: run the chunked path at each requested chunk size, score it
against the SINGLE-PASS output as reference (DER via pyannote.metrics, the
same scorer bench/score.py uses), and print a table. No DB writes, no LLM.

Gate (from the design doc): DER <= 10% and speaker count within +-20% of the
single-pass result. A chunk size that fails on ANY meeting is not eligible to
become the default.

Usage:
  .venv/bin/python scripts/calibrate_chunked_diarization.py \
      bloomington-city-council-2026-07-29 --chunks 30 45 60
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local

load_env_local()

from bench.score import calculate_der  # noqa: E402
from src import config  # noqa: E402
from src.modal_compute import _modal_app, _run_chunked_diarization, upload_audio  # noqa: E402

DER_GATE = 0.10
DRIFT_GATE = 0.20

#: The single-pass reference arm is the expensive one (~10 min on an 82-minute
#: meeting, ~2 h on a 5-hour one). Cache it next to the audio so adding a chunk
#: size later costs only that arm's GPU time.
_REFERENCE_FILENAME = "calibration_single_pass.json"


def _rttm(turns, meeting_id: str, path: Path) -> Path:
    lines = [
        f"SPEAKER {meeting_id} 1 {start:.3f} {end - start:.3f} "
        f"<NA> <NA> {label} <NA> <NA>"
        for start, end, label in turns
        if end - start > 0
    ]
    dropped = len(turns) - len(lines)
    if dropped:
        print(f"  (dropped {dropped} zero-length turn(s) from {path.name})")
    path.write_text("\n".join(lines) + "\n")
    return path


def _turns_from_segments(segments) -> list[tuple[float, float, str]]:
    return [(s["start_time"], s["end_time"], s["speaker_label"]) for s in segments]


def _single_pass_reference(
    app, meeting_id: str, wav: Path, refresh: bool
) -> tuple[list[tuple[float, float, str]], float]:
    """Return (turns, elapsed_s) for the single-pass arm, using the cache if any."""
    cache = wav.parent / _REFERENCE_FILENAME
    if cache.exists() and not refresh:
        cached = json.loads(cache.read_text())
        turns = [(t[0], t[1], t[2]) for t in cached["turns"]]
        print(f"  reusing cached single-pass reference from {cache.name} "
              f"({cached['elapsed_s']:.0f}s when measured)", flush=True)
        return turns, float(cached["elapsed_s"])

    t0 = time.time()
    with app.app.run():
        payload = app.pipeline_diarize_and_embed.remote(meeting_id, use_merge=True)
    elapsed = time.time() - t0
    turns = _turns_from_segments(json.loads(payload)["segments"])
    cache.write_text(json.dumps({"turns": turns, "elapsed_s": elapsed}))
    return turns, elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("meeting_ids", nargs="+")
    ap.add_argument("--chunks", type=int, nargs="+", default=[30, 45, 60])
    ap.add_argument(
        "--refresh-reference",
        action="store_true",
        help="re-run the single-pass arm even if a cached reference exists",
    )
    args = ap.parse_args()

    app = _modal_app()
    rows = []
    for meeting_id in args.meeting_ids:
        wav = config.MEETINGS_DIR / meeting_id / "audio.wav"
        if not wav.exists():
            print(f"SKIP {meeting_id}: no local audio at {wav}", file=sys.stderr)
            continue
        upload_audio(wav, meeting_id)

        # Reference = the single-pass output this repo ships today.
        print(f"\n=== {meeting_id}: single-pass reference ===", flush=True)
        base_turns, base_elapsed = _single_pass_reference(
            app, meeting_id, wav, args.refresh_reference
        )
        base_speakers = len({t[2] for t in base_turns})
        print(f"  {len(base_turns)} turns, {base_speakers} speakers, "
              f"{base_elapsed:.0f}s", flush=True)

        with tempfile.TemporaryDirectory() as tmp:
            ref = _rttm(base_turns, meeting_id, Path(tmp) / "single_pass.rttm")
            for chunk_minutes in args.chunks:
                print(f"\n=== {meeting_id}: chunked @ {chunk_minutes} min ===",
                      flush=True)
                t0 = time.time()
                segments, centroids = _run_chunked_diarization(
                    app, wav, meeting_id, chunk_minutes, use_merge=True
                )
                elapsed = time.time() - t0
                turns = _turns_from_segments(segments)
                speakers = len({t[2] for t in turns})
                hyp = _rttm(turns, meeting_id, Path(tmp) / f"chunk{chunk_minutes}.rttm")
                metrics = calculate_der(ref, hyp)
                der = metrics["der"] if metrics else None
                drift = (speakers - base_speakers) / base_speakers if base_speakers else 0
                rows.append({
                    "meeting": meeting_id,
                    "chunk_minutes": chunk_minutes,
                    "der": der,
                    "confusion": metrics["confusion"] if metrics else None,
                    "missed": metrics["missed_detection"] if metrics else None,
                    "false_alarm": metrics["false_alarm"] if metrics else None,
                    "turns": len(turns),
                    "base_turns": len(base_turns),
                    "speakers": speakers,
                    "base_speakers": base_speakers,
                    "speaker_drift": round(drift, 3),
                    "elapsed_s": round(elapsed),
                    "base_elapsed_s": round(base_elapsed),
                    "speedup": round(base_elapsed / elapsed, 1) if elapsed else None,
                    "passes_gate": bool(
                        der is not None and der <= DER_GATE and abs(drift) <= DRIFT_GATE
                    ),
                })
                print(f"  DER vs single-pass: {der:.4f}" if der is not None
                      else "  DER: unavailable")
                if metrics:
                    print(f"  components: confusion {metrics['confusion']:.4f}, "
                          f"missed {metrics['missed_detection']:.4f}, "
                          f"false alarm {metrics['false_alarm']:.4f}")
                print(f"  {speakers} speakers (single-pass {base_speakers}, "
                      f"drift {drift:+.1%}), {len(turns)} turns "
                      f"(single-pass {len(base_turns)}), {elapsed:.0f}s "
                      f"(speedup {rows[-1]['speedup']}x), "
                      f"gate {'PASS' if rows[-1]['passes_gate'] else 'FAIL'}",
                      flush=True)

    print("\n================ SUMMARY ================")
    print(f"{'meeting':<42} {'chunk':>6} {'DER':>8} {'spk':>5} {'base':>5} "
          f"{'drift':>7} {'secs':>6} {'base_s':>7} {'speedup':>8} gate")
    for r in rows:
        der = f"{r['der']:.4f}" if r["der"] is not None else "n/a"
        print(f"{r['meeting']:<42} {r['chunk_minutes']:>6} {der:>8} "
              f"{r['speakers']:>5} {r['base_speakers']:>5} "
              f"{r['speaker_drift']:>+7.1%} "
              f"{r['elapsed_s']:>6} {r['base_elapsed_s']:>7} "
              f"{str(r['speedup']) + 'x':>8} "
              f"{'PASS' if r['passes_gate'] else 'FAIL'}")
    print("=========================================")
    print("JSON " + json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
