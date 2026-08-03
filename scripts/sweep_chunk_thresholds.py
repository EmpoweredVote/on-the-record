#!/usr/bin/env python
"""Find the reconciliation thresholds that make chunked diarization match single-pass.

The July 29 calibration showed chunking is fast (7.4x) and temporally accurate
(missed 0.0007, false alarm 0.0003) but FRAGMENTS speakers: 18 globals vs 14,
because same-person centroids fall below the 0.75 embedding-match threshold
across windows. Retuning that threshold by re-running GPU work per candidate
would be absurdly wasteful, so this script pays for the chunk work ONCE per
chunk size, caches the payloads, and then re-stitches them locally across a
threshold grid — seconds per candidate, no GPU.

For each (chunk size, embedding threshold, merge threshold) it reports DER
against the cached single-pass reference plus the speaker count, so the
fragmentation/conflation tradeoff is visible rather than guessed at:
lowering the threshold should pull the speaker count toward the reference,
and DER should fall with it — until the threshold gets low enough to conflate
two real people, at which point DER climbs again. The best setting is the
bottom of that U.

No DB writes, no LLM. Usage:
  .venv/bin/python scripts/sweep_chunk_thresholds.py \
      bloomington-city-council-2026-07-29 --chunks 30 45 \
      --embedding 0.75 0.70 0.65 0.60 0.55 0.50
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
from src.modal_compute import (  # noqa: E402
    _modal_app,
    fetch_chunk_payloads,
    stitch_chunk_payloads,
    upload_audio,
)

DER_GATE = 0.10
DRIFT_GATE = 0.20
_REFERENCE_FILENAME = "calibration_single_pass.json"


def _rttm(turns, meeting_id: str, path: Path) -> Path:
    lines = [
        f"SPEAKER {meeting_id} 1 {start:.3f} {end - start:.3f} "
        f"<NA> <NA> {label} <NA> <NA>"
        for start, end, label in turns
        if end - start > 0
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def _turns(segments) -> list[tuple[float, float, str]]:
    return [(s["start_time"], s["end_time"], s["speaker_label"]) for s in segments]


def _reference(app, meeting_id: str, wav: Path) -> tuple[list, float]:
    """Single-pass turns, from cache when available (it is the expensive arm)."""
    cache = wav.parent / _REFERENCE_FILENAME
    if cache.exists():
        data = json.loads(cache.read_text())
        print(f"  reusing cached single-pass reference ({data['elapsed_s']:.0f}s "
              "when measured)", flush=True)
        return [(t[0], t[1], t[2]) for t in data["turns"]], float(data["elapsed_s"])
    t0 = time.time()
    with app.app.run():
        payload = app.pipeline_diarize_and_embed.remote(meeting_id, use_merge=True)
    elapsed = time.time() - t0
    turns = _turns(json.loads(payload)["segments"])
    cache.write_text(json.dumps({"turns": turns, "elapsed_s": elapsed}))
    return turns, elapsed


def _payload_cache(wav: Path, chunk_minutes: int) -> Path:
    return wav.parent / f"calibration_chunks_{chunk_minutes}min.json"


def _payloads(app, wav: Path, meeting_id: str, chunk_minutes: int) -> list[str]:
    """Chunk-worker payloads, from cache when available (this is the GPU cost)."""
    cache = _payload_cache(wav, chunk_minutes)
    if cache.exists():
        print(f"  reusing cached chunk payloads for {chunk_minutes} min", flush=True)
        return json.loads(cache.read_text())
    payloads = fetch_chunk_payloads(app, wav, meeting_id, chunk_minutes)
    cache.write_text(json.dumps(payloads))
    return payloads


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("meeting_ids", nargs="+")
    ap.add_argument("--chunks", type=int, nargs="+", default=[30, 45])
    ap.add_argument("--embedding", type=float, nargs="+",
                    default=[0.75, 0.70, 0.65, 0.60, 0.55, 0.50])
    ap.add_argument("--merge", type=float, nargs="+", default=[0.80],
                    help="post-reconcile merge_similar_speakers threshold(s)")
    args = ap.parse_args()

    app = _modal_app()
    rows: list[dict] = []
    for meeting_id in args.meeting_ids:
        wav = config.MEETINGS_DIR / meeting_id / "audio.wav"
        if not wav.exists():
            print(f"SKIP {meeting_id}: no local audio at {wav}", file=sys.stderr)
            continue
        upload_audio(wav, meeting_id)

        print(f"\n=== {meeting_id}: single-pass reference ===", flush=True)
        base_turns, base_elapsed = _reference(app, meeting_id, wav)
        base_speakers = len({t[2] for t in base_turns})
        print(f"  {len(base_turns)} turns, {base_speakers} speakers, "
              f"{base_elapsed:.0f}s", flush=True)

        with tempfile.TemporaryDirectory() as tmp:
            ref = _rttm(base_turns, meeting_id, Path(tmp) / "ref.rttm")
            for chunk_minutes in args.chunks:
                print(f"\n=== {meeting_id}: chunk payloads @ {chunk_minutes} min ===",
                      flush=True)
                payloads = _payloads(app, wav, meeting_id, chunk_minutes)
                for emb in args.embedding:
                    for mrg in args.merge:
                        segments, centroids = stitch_chunk_payloads(
                            payloads, use_merge=True,
                            embedding_threshold=emb, merge_threshold=mrg,
                        )
                        turns = _turns(segments)
                        speakers = len({t[2] for t in turns})
                        hyp = _rttm(turns, meeting_id, Path(tmp) / "hyp.rttm")
                        metrics = calculate_der(ref, hyp)
                        der = metrics["der"] if metrics else None
                        drift = ((speakers - base_speakers) / base_speakers
                                 if base_speakers else 0.0)
                        rows.append({
                            "meeting": meeting_id, "chunk_minutes": chunk_minutes,
                            "embedding_threshold": emb, "merge_threshold": mrg,
                            "der": der, "speakers": speakers,
                            "base_speakers": base_speakers,
                            "speaker_drift": round(drift, 3),
                            "confusion": metrics["confusion"] if metrics else None,
                            "passes_gate": bool(der is not None and der <= DER_GATE
                                                and abs(drift) <= DRIFT_GATE),
                        })
                        print(f"    emb {emb:.2f} merge {mrg:.2f}: "
                              f"DER {der:.4f}, {speakers} spk "
                              f"(drift {drift:+.1%}) "
                              f"{'PASS' if rows[-1]['passes_gate'] else 'fail'}",
                              flush=True)

    print("\n==================== SWEEP SUMMARY ====================")
    print(f"{'meeting':<42} {'chunk':>5} {'emb':>5} {'merge':>6} {'DER':>8} "
          f"{'spk':>4} {'base':>5} {'drift':>7} gate")
    for r in sorted(rows, key=lambda r: (r["meeting"], r["chunk_minutes"],
                                         -r["embedding_threshold"])):
        der = f"{r['der']:.4f}" if r["der"] is not None else "n/a"
        print(f"{r['meeting']:<42} {r['chunk_minutes']:>5} "
              f"{r['embedding_threshold']:>5.2f} {r['merge_threshold']:>6.2f} "
              f"{der:>8} {r['speakers']:>4} {r['base_speakers']:>5} "
              f"{r['speaker_drift']:>+7.1%} "
              f"{'PASS' if r['passes_gate'] else 'fail'}")
    passing = [r for r in rows if r["passes_gate"]]
    if passing:
        best = min(passing, key=lambda r: r["der"])
        print(f"\nBEST PASSING: chunk {best['chunk_minutes']}min, "
              f"embedding {best['embedding_threshold']:.2f}, "
              f"merge {best['merge_threshold']:.2f} → DER {best['der']:.4f}, "
              f"{best['speakers']} speakers (drift {best['speaker_drift']:+.1%})")
    else:
        print("\nNO SETTING PASSES BOTH GATES on this meeting.")
    print("=======================================================")
    print("JSON " + json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
