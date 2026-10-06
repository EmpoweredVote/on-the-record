#!/usr/bin/env python3
"""Re-run Whisper (large-v3, Modal GPU) on already-reviewed meetings.

For meetings whose Whisper text is bad (e.g. the repetition loops fixed by
WHISPER_DECODE_OPTIONS) without redoing diarization or speaker review. The
reviewed turns in transcript_named.json are sent as-is to the Modal
pipeline_transcribe function; only each turn's text and words change. Segment
count, ids, start/end times, speaker labels and names are kept, so summary
section indices stay valid. transcript_raw.json gets the same words, re-assigned
to its own (pre-merge) turns.

Default is a dry run: the new transcripts are written beside the originals as
*.retranscribed.json and a before/after report is printed. --apply backs the
originals up to backup-pre-retranscribe/ and replaces them. It does NOT
re-publish; use run_local.py --publish-meeting <id> afterwards.

Run from the repo root: the Modal image bundles ./src, so the decode options in
your checkout are the ones used.

Usage:
    .venv/bin/python retranscribe_meeting.py --meeting <id> [--meeting <id> ...]
    .venv/bin/python retranscribe_meeting.py --meeting <id> --apply
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from src import config
from src.models import Meeting, Segment, Word


def repetition_runs(text: str, min_rep: int = 5) -> list[tuple[str, int]]:
    """(phrase, count) for each 1-4 word phrase repeated >= min_rep times in a
    row. Whisper loops look like this; real speech rarely does."""
    words = text.split()
    norm = [re.sub(r"[^\w']", "", w.lower()) for w in words]
    runs, i = [], 0
    while i < len(norm):
        best = None
        for n in (1, 2, 3, 4):
            gram = norm[i:i + n]
            if len(gram) < n or not all(gram):
                continue
            k = 1
            while norm[i + k * n:i + (k + 1) * n] == gram:
                k += 1
            if k >= min_rep and (best is None or k * n > best[0] * best[1]):
                best = (n, k)
        if best:
            n, k = best
            runs.append((" ".join(words[i:i + n]), k))
            i += n * k
        else:
            i += 1
    return runs


def apply_new_text(segments: list[Segment], new_data: list[dict]) -> None:
    """Copy text/words from the Modal result onto the reviewed segments.

    Matches by segment_id and refuses any result that does not cover exactly
    the same ids; times, labels and names on `segments` are left untouched."""
    by_id = {d["segment_id"]: d for d in new_data}
    ids = [s.segment_id for s in segments]
    if sorted(by_id) != sorted(ids):
        raise ValueError("Modal result does not cover the same segment ids")
    for seg in segments:
        fresh = Segment.from_dict(by_id[seg.segment_id])
        seg.words = fresh.words
        seg.text = fresh.text


def reassign_raw(raw_segments: list[Segment], named: list[Segment]) -> None:
    """Give the pre-merge raw turns the same words, by timestamp."""
    from src.word_assign import assign_words_to_segments

    words: list[Word] = sorted(
        (w for s in named for w in s.words), key=lambda w: (w.start, w.end))
    assign_words_to_segments(words, raw_segments)


def _opus_to_wav(meeting_dir: Path) -> Path:
    wav = meeting_dir / "audio.wav"
    if wav.exists():
        return wav
    opus = meeting_dir / "audio.opus"
    if not opus.exists():
        raise FileNotFoundError(f"no audio.wav or audio.opus in {meeting_dir}")
    tmp = meeting_dir / "audio.retranscribe.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(opus),
                    "-ac", "1", "-ar", "16000", str(tmp)], check=True)
    return tmp


def retranscribe(meeting_id: str, *, apply: bool) -> None:
    from src.atomic_io import atomic_write_json
    from src.modal_compute import run_transcription, upload_audio

    mdir = config.MEETINGS_DIR / meeting_id
    named_path, raw_path = mdir / "transcript_named.json", mdir / "transcript_raw.json"
    meeting = Meeting.from_dict(json.loads(named_path.read_text(encoding="utf-8")))
    raw_json = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_list = raw_json["segments"] if isinstance(raw_json, dict) else raw_json
    raw_segments = [Segment.from_dict(d) for d in raw_list]

    before = " ".join(s.text for s in meeting.segments)
    wav = _opus_to_wav(mdir)
    try:
        upload_audio(wav, meeting_id)
        new_data = run_transcription(meeting_id, [s.to_dict() for s in meeting.segments])
    finally:
        if wav.name == "audio.retranscribe.wav":
            wav.unlink(missing_ok=True)

    apply_new_text(meeting.segments, new_data)
    reassign_raw(raw_segments, meeting.segments)
    after = " ".join(s.text for s in meeting.segments)

    print(f"\n{meeting_id}: words {len(before.split())} -> {len(after.split())}")
    print(f"  repetition runs before: {repetition_runs(before)[:6]}")
    print(f"  repetition runs after:  {repetition_runs(after)[:6]}")

    raw_out = [s.to_dict() for s in raw_segments]
    if isinstance(raw_json, dict):
        raw_out = {**raw_json, "segments": raw_out}
    if not apply:
        atomic_write_json(mdir / "transcript_named.retranscribed.json", meeting.to_dict())
        atomic_write_json(mdir / "transcript_raw.retranscribed.json", raw_out)
        print("  [dry-run] wrote *.retranscribed.json beside the originals")
        return

    backup = mdir / "backup-pre-retranscribe"
    backup.mkdir(exist_ok=True)
    for p in (named_path, raw_path):
        if not (backup / p.name).exists():
            shutil.copy2(p, backup / p.name)
    atomic_write_json(named_path, meeting.to_dict())
    atomic_write_json(raw_path, raw_out)
    for side in ("transcript_named.retranscribed.json", "transcript_raw.retranscribed.json"):
        (mdir / side).unlink(missing_ok=True)
    try:
        from src.export import export_all
        export_all(meeting, mdir / "exports")
    except Exception as exc:  # exports regenerate at publish; never block
        print(f"  (export refresh skipped: {exc})")
    print(f"  applied; originals in {backup.name}/ — re-publish with "
          f"run_local.py --publish-meeting {meeting_id}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--meeting", action="append", required=True, metavar="ID")
    ap.add_argument("--apply", action="store_true",
                    help="replace the transcripts (default: dry run to side files)")
    args = ap.parse_args()
    if "WHISPER_DECODE_OPTIONS" not in Path("src/transcribe.py").read_text():
        raise SystemExit("run from a repo root whose src/ has WHISPER_DECODE_OPTIONS")
    for mid in args.meeting:
        retranscribe(mid, apply=args.apply)


if __name__ == "__main__":
    main()
