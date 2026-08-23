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

from bench.identity_score import identity_report, named_reference_turns  # noqa: E402
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


def _payload_cache(wav: Path, chunk_minutes: int, suffix: str) -> Path:
    stem = f"calibration_chunks_{chunk_minutes}min"
    return wav.parent / (f"{stem}_{suffix}.json" if suffix else f"{stem}.json")


def _payloads(app, wav: Path, meeting_id: str, chunk_minutes: int,
              suffix: str, embedders: list[str]) -> list[str]:
    """Chunk-worker payloads, from cache when available (this is the GPU cost)."""
    cache = _payload_cache(wav, chunk_minutes, suffix)
    if cache.exists():
        print(f"  reusing cached chunk payloads for {chunk_minutes} min "
              f"({cache.name})", flush=True)
        return json.loads(cache.read_text())
    payloads = fetch_chunk_payloads(
        app, wav, meeting_id, chunk_minutes, embedders=tuple(embedders)
    )
    cache.write_text(json.dumps(payloads))
    print(f"  cached {cache.name} ({cache.stat().st_size / 1e6:.1f} MB)", flush=True)
    return payloads


def _seam_report(payloads: list[str], segments: list[dict], window_s: float = 10.0) -> None:
    """Print label continuity across each chunk boundary.

    A person speaking across a seam must not change label there; this prints
    the turns on both sides so a human can see it rather than trusting an
    aggregate.

    The seam itself is NOT `window_start_s` — that is merely where a
    window's READ range begins (`start_s - overlap_s`), which sits a full
    overlap-length away from where label ownership actually changes hands.
    Ownership transfers at the MIDPOINT of two consecutive windows' overlap,
    exactly as `src.speaker_reconcile._ownership_bounds` computes it:
    `(next_window.start + this_window.end) / 2`. Using `window_start_s`
    would inspect audio a full 60s (the shipped overlap) away from the
    actual handover.
    """
    parsed = sorted((json.loads(p) for p in payloads), key=lambda p: p["window_index"])
    seams = [
        (previous["window_end_s"] + current["window_start_s"]) / 2
        for previous, current in zip(parsed, parsed[1:])
    ]
    turns = _turns(segments)
    for seam in seams:
        before = [t for t in turns if seam - window_s <= t[1] <= seam]
        after = [t for t in turns if seam <= t[0] <= seam + window_s]
        crossing = {t[2] for t in before} & {t[2] for t in after}
        print(f"  seam @ {seam / 60:.1f} min: {len(before)} turn(s) before, "
              f"{len(after)} after, {len(crossing)} label(s) continuous "
              f"across it: {sorted(crossing)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("meeting_ids", nargs="+")
    ap.add_argument("--chunks", type=int, nargs="+", default=[30, 45])
    ap.add_argument("--embedding", type=float, nargs="+",
                    default=[0.75, 0.70, 0.65, 0.60, 0.55, 0.50])
    ap.add_argument("--merge", type=float, nargs="+", default=[0.80],
                    help="post-reconcile merge_similar_speakers threshold(s)")
    ap.add_argument("--identity", choices=["global", "sequential", "both"],
                    default="global",
                    help="cross-window identity strategy to sweep")
    ap.add_argument("--cluster", type=float, nargs="+",
                    default=[0.70, 0.65, 0.60, 0.55, 0.50, 0.45],
                    help="global-identity cluster similarity threshold(s)")
    ap.add_argument("--linkage", nargs="+", default=["average"],
                    choices=["average", "complete", "centroid"])
    ap.add_argument("--embedder", nargs="+",
                    default=["pyannote/wespeaker-voxceleb-resnet34-LM",
                             "pyannote/embedding"],
                    help="per-turn embedder(s) to cluster; the worker computes "
                         "every one listed in ONE pass so segmentation GPU is "
                         "paid once")
    ap.add_argument("--payload-suffix", default="turnemb",
                    help="cache filename suffix; keeps pre-turn-embedding "
                         "caches intact for old-vs-new comparison")
    args = ap.parse_args()

    app = _modal_app()
    rows: list[dict] = []
    # Parallel to `rows`, so the best passing row's own segments can be handed
    # to `_seam_report` at the end without re-stitching or re-parsing payloads.
    row_segments: list[list[dict]] = []
    # Payloads are cheap to hold in memory (they are already local JSON once
    # fetched) and re-keying by (meeting, chunk_minutes) lets the seam
    # spot-check find the right cached payload set for the winning row.
    payloads_by_key: dict[tuple[str, int], list[str]] = {}

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

        named_path = wav.parent / "transcript_named.json"
        named_reference = None
        if named_path.exists():
            named_reference = named_reference_turns(json.loads(named_path.read_text()))
            base_report = identity_report(base_turns, named_reference)
            print(f"  human-reviewed reference: {base_report.reference_people} people; "
                  f"SINGLE-PASS itself fragments {len(base_report.fragmentation)} and "
                  f"conflates {len(base_report.conflation)} of them "
                  "(this is the bar to beat, not DER)", flush=True)
            print(f"    {base_report.fragmentation_summary}", flush=True)
            print(f"    {base_report.conflation_summary}", flush=True)

        with tempfile.TemporaryDirectory() as tmp:
            ref = _rttm(base_turns, meeting_id, Path(tmp) / "ref.rttm")
            for chunk_minutes in args.chunks:
                print(f"\n=== {meeting_id}: chunk payloads @ {chunk_minutes} min ===",
                      flush=True)
                payloads = _payloads(app, wav, meeting_id, chunk_minutes,
                                      args.payload_suffix, args.embedder)
                payloads_by_key[(meeting_id, chunk_minutes)] = payloads

                configs: list[dict] = []
                if args.identity in ("global", "both"):
                    configs += [
                        {"identity": "global", "embedder": embedder,
                         "linkage": linkage, "cluster_threshold": cluster,
                         "merge_threshold": mrg}
                        for embedder in args.embedder
                        for linkage in args.linkage
                        for cluster in args.cluster
                        for mrg in args.merge
                    ]
                if args.identity in ("sequential", "both"):
                    configs += [
                        {"identity": "sequential", "embedding_threshold": emb,
                         "merge_threshold": mrg}
                        for emb in args.embedding
                        for mrg in args.merge
                    ]

                for cfg in configs:
                    segments, centroids = stitch_chunk_payloads(
                        payloads, use_merge=True, **cfg
                    )
                    turns = _turns(segments)
                    speakers = len({t[2] for t in turns})
                    hyp = _rttm(turns, meeting_id, Path(tmp) / "hyp.rttm")
                    metrics = calculate_der(ref, hyp)
                    der = metrics["der"] if metrics else None
                    drift = ((speakers - base_speakers) / base_speakers
                             if base_speakers else 0.0)
                    row = {
                        "meeting": meeting_id, "chunk_minutes": chunk_minutes,
                        "der": der, "speakers": speakers,
                        "base_speakers": base_speakers,
                        "speaker_drift": round(drift, 3),
                        "confusion": metrics["confusion"] if metrics else None,
                        "passes_gate": bool(der is not None and der <= DER_GATE
                                            and abs(drift) <= DRIFT_GATE),
                        **cfg,
                    }
                    if named_reference:
                        report = identity_report(turns, named_reference)
                        row["named_fragmentation"] = len(report.fragmentation)
                        row["named_conflation"] = len(report.conflation)
                        row["named_people"] = report.reference_people
                        row["named_fragmentation_summary"] = report.fragmentation_summary
                        row["named_conflation_summary"] = report.conflation_summary
                    rows.append(row)
                    row_segments.append(segments)
                    label = (f"{cfg['identity']}"
                             + (f" {cfg['linkage']}@{cfg['cluster_threshold']:.2f}"
                                f" {cfg['embedder'].split('/')[-1][:12]}"
                                if cfg["identity"] == "global"
                                else f" emb@{cfg['embedding_threshold']:.2f}"))
                    extra = ""
                    if named_reference:
                        extra = (f" | vs reviewed names: "
                                 f"{row['named_fragmentation']} fragmented, "
                                 f"{row['named_conflation']} conflated "
                                 f"({row['named_fragmentation_summary']}; "
                                 f"{row['named_conflation_summary']})")
                    print(f"    {label}: DER {der:.4f}, {speakers} spk "
                          f"(drift {drift:+.1%}) "
                          f"{'PASS' if row['passes_gate'] else 'fail'}{extra}",
                          flush=True)

    print("\n==================== SWEEP SUMMARY ====================")
    print(f"{'meeting':<42} {'chunk':>5} {'identity':>10} {'config':>30} "
          f"{'DER':>8} {'spk':>4} {'base':>5} {'drift':>7} gate")
    for r in sorted(rows, key=lambda r: (r["meeting"], r["chunk_minutes"], r["identity"])):
        der = f"{r['der']:.4f}" if r["der"] is not None else "n/a"
        if r["identity"] == "global":
            cfg_str = (f"{r['linkage']}@{r['cluster_threshold']:.2f} "
                       f"{r['embedder'].split('/')[-1][:12]}")
        else:
            cfg_str = f"emb@{r['embedding_threshold']:.2f}"
        extra = ""
        if "named_fragmentation" in r:
            extra = (f" frag={r['named_fragmentation']} "
                     f"confl={r['named_conflation']}")
        print(f"{r['meeting']:<42} {r['chunk_minutes']:>5} {r['identity']:>10} "
              f"{cfg_str:>30} {der:>8} {r['speakers']:>4} {r['base_speakers']:>5} "
              f"{r['speaker_drift']:>+7.1%} "
              f"{'PASS' if r['passes_gate'] else 'fail'}{extra}")
        # A count alone can't distinguish a boundary bleed from a real
        # merge/split, so print the seconds/share behind it right under the
        # row it belongs to.
        if "named_fragmentation" in r:
            print(f"    {r['named_fragmentation_summary']}")
            print(f"    {r['named_conflation_summary']}")
    passing = [(i, r) for i, r in enumerate(rows) if r["passes_gate"]]
    if passing:
        best_idx, best = min(passing, key=lambda item: item[1]["der"])
        if best["identity"] == "global":
            cfg_desc = (f"identity global, {best['linkage']} linkage, "
                        f"cluster {best['cluster_threshold']:.2f}, "
                        f"embedder {best['embedder']}, "
                        f"merge {best['merge_threshold']:.2f}")
        else:
            cfg_desc = (f"identity sequential, embedding {best['embedding_threshold']:.2f}, "
                        f"merge {best['merge_threshold']:.2f}")
        print(f"\nBEST PASSING: chunk {best['chunk_minutes']}min, {cfg_desc} "
              f"→ DER {best['der']:.4f}, {best['speakers']} speakers "
              f"(drift {best['speaker_drift']:+.1%})")
        if "named_fragmentation" in best:
            print(f"  vs reviewed names: {best['named_fragmentation']} fragmented, "
                  f"{best['named_conflation']} conflated of {best['named_people']} people")
            print(f"    {best['named_fragmentation_summary']}")
            print(f"    {best['named_conflation_summary']}")
        print(f"\n  seam spot-check for the best passing config "
              f"({best['meeting']} @ {best['chunk_minutes']}min):")
        _seam_report(payloads_by_key[(best["meeting"], best["chunk_minutes"])],
                     row_segments[best_idx])
    else:
        print("\nNO SETTING PASSES BOTH GATES on this meeting.")
    print("=======================================================")
    print("JSON " + json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
