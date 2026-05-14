"""Score diarization benchmark outputs.

We don't have hand-labeled ground truth, so this is a "good enough to pick
a winner" scorer rather than an academic DER measurement. It produces:

    - speaker count vs. expected (fragmentation / under-segmentation signal)
    - average turn duration (very short turns suggest fragmentation)
    - silence coverage (% of audio not assigned to any speaker)
    - wall-clock + cost per meeting
    - 5 random 60-second spot-check clips per (model, meeting), each with a
      mini per-second speaker-label strip you can listen to and eyeball

Outputs:
    <run_dir>/scores.csv               — one row per (meeting, model)
    <run_dir>/spot_checks/<...>.wav    — clips for manual review
    <run_dir>/spot_checks/<...>.txt    — second-by-second labels next to each clip
"""

from __future__ import annotations

import csv
import json
import random
import subprocess
from pathlib import Path

# Deterministic spot-checks across re-runs
SPOT_CHECK_SEED = 42
SPOT_CHECK_COUNT = 5
SPOT_CHECK_SECONDS = 60


def parse_rttm(rttm_path: Path) -> list[tuple[float, float, str]]:
    """Parse RTTM file → list of (start, end, speaker)."""
    turns = []
    for line in rttm_path.read_text().splitlines():
        if not line.startswith("SPEAKER"):
            continue
        parts = line.split()
        # SPEAKER <file> 1 <onset> <duration> <NA> <NA> <speaker> <NA> <NA>
        start = float(parts[3])
        duration = float(parts[4])
        speaker = parts[7]
        turns.append((start, start + duration, speaker))
    turns.sort()
    return turns


def silence_fraction(turns: list[tuple[float, float, str]], total_duration: float) -> float:
    """Fraction of total_duration not covered by any speaker turn."""
    if total_duration <= 0:
        return 0.0
    # Union of intervals
    if not turns:
        return 1.0
    sorted_t = sorted(turns)
    merged = [list(sorted_t[0][:2])]
    for s, e, _ in sorted_t[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    covered = sum(e - s for s, e in merged)
    return max(0.0, 1.0 - covered / total_duration)


def label_strip(turns: list[tuple[float, float, str]], start: float, end: float) -> str:
    """One char per second showing which speaker was active.

    Maps each distinct speaker label in the window to a 1-char tag (A, B, C…).
    Overlap → '*'. Silence → '.'.
    """
    duration = int(end - start)
    chars = ["."] * duration
    seen_speakers: dict[str, str] = {}

    def tag(spk: str) -> str:
        if spk not in seen_speakers:
            seen_speakers[spk] = chr(ord("A") + len(seen_speakers)) \
                if len(seen_speakers) < 26 else "?"
        return seen_speakers[spk]

    for ts, te, spk in turns:
        if te <= start or ts >= end:
            continue
        ovl_start = max(ts, start)
        ovl_end = min(te, end)
        for sec in range(int(ovl_start - start), int(ovl_end - start) + 1):
            if 0 <= sec < duration:
                chars[sec] = "*" if chars[sec] not in (".", tag(spk)) else tag(spk)

    legend = "  ".join(f"{c}={s}" for s, c in seen_speakers.items())
    return f"  {start:6.0f}s [{''.join(chars)}] {end:6.0f}s\n  legend: {legend}\n"


def write_spot_checks(
    run_dir: Path,
    meeting_id: str,
    model: str,
    wav_path: Path | None,
    turns: list[tuple[float, float, str]],
    audio_duration: float,
) -> list[Path]:
    """Cut 5 random 60s clips and write a label strip next to each.

    Skips actual audio extraction if `wav_path` is None (e.g., audio still
    only lives in the Modal volume) — the label strips alone are still useful.
    """
    spot_dir = run_dir / "spot_checks" / meeting_id / model
    spot_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(f"{meeting_id}:{SPOT_CHECK_SEED}")
    written: list[Path] = []
    if audio_duration < SPOT_CHECK_SECONDS:
        return written

    starts = sorted(
        rng.uniform(0, audio_duration - SPOT_CHECK_SECONDS)
        for _ in range(SPOT_CHECK_COUNT)
    )

    for i, start in enumerate(starts):
        end = start + SPOT_CHECK_SECONDS
        label_path = spot_dir / f"clip_{i:02d}_t{int(start):05d}.txt"
        label_path.write_text(label_strip(turns, start, end))
        written.append(label_path)

        if wav_path and wav_path.exists():
            clip_path = spot_dir / f"clip_{i:02d}_t{int(start):05d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-ss", str(start), "-t", str(SPOT_CHECK_SECONDS),
                 "-i", str(wav_path), "-c", "copy", str(clip_path)],
                check=False,
            )

    return written


def score_run(run_dir: Path, config: dict) -> None:
    """Score every meta.json in run_dir and write scores.csv."""
    results_root = run_dir / "results"  # populated by `modal volume get`
    if not results_root.exists():
        print(f"  No results directory at {results_root} — nothing to score.")
        return

    by_meeting_expected = {m["id"]: m.get("expected_speakers") for m in config["meetings"]}

    rows: list[dict] = []
    for meta_path in sorted(results_root.rglob("*.meta.json")):
        meta = json.loads(meta_path.read_text())
        meeting_id = meta["meeting_id"]
        model = meta["model"]
        rttm_path = meta_path.with_suffix("").with_suffix(".rttm")
        # rttm_path is now <meeting_id>/<model>.rttm — but `.with_suffix("")`
        # strips only one suffix, so guard manually:
        rttm_path = meta_path.parent / f"{model}.rttm"
        if not rttm_path.exists():
            print(f"  ! missing rttm: {rttm_path}")
            continue

        turns = parse_rttm(rttm_path)
        audio_duration = meta.get("audio_duration_seconds", 0.0)
        expected = by_meeting_expected.get(meeting_id)

        avg_turn = (sum(e - s for s, e, _ in turns) / len(turns)) if turns else 0.0
        silence = silence_fraction(turns, audio_duration)

        row = {
            "meeting_id": meeting_id,
            "model": model,
            "audio_duration_s": round(audio_duration, 1),
            "elapsed_s": meta.get("elapsed_seconds"),
            "realtime_factor": meta.get("realtime_factor"),
            "cost_usd": meta.get("cost_usd"),
            "num_turns": len(turns),
            "num_speakers": meta.get("num_distinct_speakers"),
            "expected_speakers": expected,
            "speaker_delta": (meta.get("num_distinct_speakers", 0) - expected)
                if expected is not None else None,
            "avg_turn_s": round(avg_turn, 2),
            "silence_fraction": round(silence, 3),
        }
        rows.append(row)

        # Spot-checks: try to find audio in the volume mirror; degrade
        # gracefully to label-only.
        wav_candidate = run_dir / "results" / "meetings" / meeting_id / "audio.wav"
        wav_path = wav_candidate if wav_candidate.exists() else None
        write_spot_checks(run_dir, meeting_id, model, wav_path, turns, audio_duration)

    if not rows:
        print("  No scored rows.")
        return

    csv_path = run_dir / "scores.csv"
    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    _print_table(rows)
    print(f"\n  scores.csv:       {csv_path}")
    print(f"  spot checks:      {run_dir / 'spot_checks'}")


def _print_table(rows: list[dict]) -> None:
    """Print a compact comparison table grouped by meeting."""
    print()
    print(f"  {'meeting':<22} {'model':<18} "
          f"{'spkrs':>6} {'Δexp':>5} {'turns':>6} {'avg_t':>6} "
          f"{'sil%':>5} {'elapsed':>9} {'$':>6}")
    print(f"  {'─'*22} {'─'*18} "
          f"{'─'*6} {'─'*5} {'─'*6} {'─'*6} {'─'*5} {'─'*9} {'─'*6}")
    rows_sorted = sorted(rows, key=lambda r: (r["meeting_id"], r["model"]))
    last_meeting = None
    for r in rows_sorted:
        meeting = r["meeting_id"] if r["meeting_id"] != last_meeting else ""
        delta = r["speaker_delta"]
        delta_str = f"{delta:+d}" if delta is not None else "—"
        elapsed = f"{r['elapsed_s']:.0f}s" if r["elapsed_s"] else "—"
        print(
            f"  {meeting:<22} {r['model']:<18} "
            f"{r['num_speakers']:>6} {delta_str:>5} {r['num_turns']:>6} "
            f"{r['avg_turn_s']:>6.1f} {r['silence_fraction']*100:>4.0f}% "
            f"{elapsed:>9} ${r['cost_usd']:>5.3f}"
        )
        last_meeting = r["meeting_id"]


if __name__ == "__main__":
    import sys
    import yaml
    if len(sys.argv) != 2:
        print("Usage: python bench/score.py <run_dir>", file=sys.stderr)
        sys.exit(1)
    run_dir = Path(sys.argv[1])
    config = yaml.safe_load((Path(__file__).parent / "meetings.yaml").read_text())
    score_run(run_dir, config)
