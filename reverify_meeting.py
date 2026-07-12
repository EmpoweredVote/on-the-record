#!/usr/bin/env python
"""Scoped acoustic re-verification of short "wedged" turns for a meeting.

Re-checks short turns that diarization wedged inside another speaker's run
(where Whisper's per-segment slicing tends to steal a word) by embedding each
turn's audio and comparing it to the two neighbouring speakers' voice
centroids. Dry-run by default: prints proposed reassignments and changes
nothing. Pass --apply to relabel and rewrite transcript_named.json.

Usage:
    .venv/bin/python reverify_meeting.py <meeting_id> [--apply] [--margin 0.1]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Load HF token from .env.local (matches run_local.py / reenroll_profiles.py).
_env_path = ROOT / ".env.local"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from src import config
from src.audio_utils import load_wav, slice_audio
from src.models import Segment
from src.reverify import MIN_EMBED_DUR, apply_proposals, find_wedged_turns, reverify


def _make_embedder(wav_path: Path, hf_token: str, min_dur: float):
    """Return embed_fn(start, end) -> np.ndarray | None using pyannote."""
    import torch
    from pyannote.audio import Inference, Model

    from src.diarize import _get_torch_device

    device = _get_torch_device()
    model = Model.from_pretrained(config.EMBEDDING_MODEL, token=hf_token)
    inference = Inference(model, window="whole", device=device)
    samples, sr = load_wav(wav_path)

    def embed_fn(start: float, end: float):
        audio_slice = slice_audio(samples, sr, start, end)
        if len(audio_slice) < sr * min_dur:
            return None
        waveform = torch.tensor(audio_slice).unsqueeze(0).to(device)
        return np.asarray(inference({"waveform": waveform, "sample_rate": sr}))

    return embed_fn


def _snip(text: str, n: int = 42, tail: bool = False) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return ("…" + text[-n:]) if tail else (text[:n] + "…")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("meeting_id")
    ap.add_argument("--apply", action="store_true", help="rewrite transcript_named.json")
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--dur-max", type=float, default=0.7)
    ap.add_argument("--words-max", type=int, default=2)
    ap.add_argument("--min-embed-dur", type=float, default=MIN_EMBED_DUR)
    args = ap.parse_args()

    mdir = config.MEETINGS_DIR / args.meeting_id
    named_path = mdir / "transcript_named.json"
    emb_path = mdir / "embeddings.json"
    wav_path = mdir / "audio.wav"
    for p in (named_path, emb_path, wav_path):
        if not p.exists():
            print(f"ERROR: missing {p}", file=sys.stderr)
            return 1

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not hf_token:
        print("ERROR: HF_TOKEN not set (check .env.local)", file=sys.stderr)
        return 1

    data = json.load(open(named_path))
    segments = [Segment.from_dict(s) for s in data["segments"]]
    centroids = {k: np.asarray(v) for k, v in json.load(open(emb_path)).items()}

    wedged = find_wedged_turns(
        segments, dur_max=args.dur_max, words_max=args.words_max
    )
    print(f"Meeting: {args.meeting_id}  ({data.get('event_kind','?')})")
    print(f"Segments: {len(segments)}   Wedged short turns: {len(wedged)}\n")
    if not wedged:
        print("No wedged turns to re-verify.")
        return 0

    print("Embedding wedged turns (loading voice model)…\n", flush=True)
    embed_fn = _make_embedder(wav_path, hf_token, args.min_embed_dur)
    proposals = reverify(
        segments, centroids, embed_fn,
        dur_max=args.dur_max, words_max=args.words_max,
        margin=args.margin, min_embed_dur=args.min_embed_dur,
    )

    counts = {"reassign": 0, "keep": 0, "flag": 0}
    icon = {"reassign": "→DOM", "keep": "keep", "flag": "flag?"}
    for p in proposals:
        counts[p.action] += 1
        seg = segments[p.index]
        prev_txt = _snip(segments[p.index - 1].text, tail=True)
        sims = (
            f"dom={p.sim_dominant:.3f} int={p.sim_interrupter:.3f}"
            if p.sim_dominant is not None else "(unembeddable)"
        )
        when = f"{int(seg.start_time // 60):02d}:{int(seg.start_time % 60):02d}"
        print(
            f"  [{when}] {icon[p.action]:5} {p.decision:12} "
            f"{p.interrupter_label}→{p.dominant_label}  {sims}\n"
            f"         …{prev_txt!r}  ||  {seg.text.strip()!r}"
        )

    print(
        f"\nSummary: {counts['reassign']} reassign, "
        f"{counts['keep']} keep, {counts['flag']} flag-for-review"
    )

    if args.apply:
        n = apply_proposals(segments, proposals)
        bak = named_path.with_suffix(".json.prereverify.bak")
        if not bak.exists():
            shutil.copy2(named_path, bak)
        data["segments"] = [s.to_dict() for s in segments]
        json.dump(data, open(named_path, "w"), indent=2)
        print(f"\nApplied {n} reassignments → {named_path} (backup: {bak.name})")
        print("NOTE: re-run merge/publish to stitch reassigned turns into the run.")
    else:
        print("\n(dry run — nothing changed; pass --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
