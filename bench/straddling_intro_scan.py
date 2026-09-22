"""Corpus isolation diff for the straddling self-introduction rule.

Runs snap_segment_boundaries over every local meeting twice — once with
_snap_straddling_intro suppressed, once with it live — and reports every
segment whose word list differs. Also re-runs the pass over its own output to
prove idempotence, which the backfill relies on.

    .venv/bin/python bench/straddling_intro_scan.py [--corpus DIR]

Read-only: it never writes to a transcript.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src import word_assign
from src.models import Segment, Word


def load(path: pathlib.Path) -> list[Segment]:
    data = json.loads(path.read_text())
    raw = data["segments"] if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    segs = [
        Segment(
            segment_id=s.get("segment_id", 0),
            start_time=s.get("start_time", 0.0),
            end_time=s.get("end_time", 0.0),
            speaker_label=s.get("speaker_label") or "",
            text=s.get("text") or "",
            words=[
                Word(w.get("word", ""), w.get("start", 0.0), w.get("end", 0.0))
                for w in (s.get("words") or [])
            ],
            speaker_name=s.get("speaker_name"),
        )
        for s in raw
        if isinstance(s, dict)
    ]
    return segs


def fingerprint(segs: list[Segment]) -> dict[int, list[str]]:
    return {s.segment_id: [w.word for w in s.words] for s in segs}


def run(segs: list[Segment], *, with_rule: bool) -> list[Segment]:
    """Run the pass with the straddling rule live or suppressed."""
    if with_rule:
        return word_assign.snap_segment_boundaries(segs)
    original = word_assign._snap_straddling_intro
    word_assign._snap_straddling_intro = lambda a, b: False
    try:
        return word_assign.snap_segment_boundaries(segs)
    finally:
        word_assign._snap_straddling_intro = original


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default="~/CouncilScribe/meetings",
                    help="directory of meeting folders (default: %(default)s)")
    args = ap.parse_args()

    root = pathlib.Path(args.corpus).expanduser()
    meetings = sorted(p for p in root.glob("*/transcript_named.json"))
    if not meetings:
        sys.exit(f"no transcripts under {root}")

    changed: list[tuple[str, int, list[str], list[str]]] = []
    non_idempotent: list[str] = []
    processed = 0
    # A fingerprint over the bytes actually read, so a later reader can tell
    # whether a reported figure was measured on the same corpus. Unreadable and
    # empty transcripts are skipped above and contribute nothing, exactly as
    # they contribute nothing to the counts.
    corpus_digest = hashlib.md5()
    for path in meetings:
        name = path.parent.name
        try:
            raw_bytes = path.read_bytes()
            base = load(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  !! {name}: unreadable ({exc})")
            continue
        if not base:
            continue
        processed += 1
        corpus_digest.update(hashlib.md5(raw_bytes).hexdigest().encode())
        without = fingerprint(run(copy.deepcopy(base), with_rule=False))
        after = run(copy.deepcopy(base), with_rule=True)
        with_ = fingerprint(after)
        for sid, words in without.items():
            if words != with_.get(sid):
                changed.append((name, sid, words, with_.get(sid, [])))
        if with_ != fingerprint(run(copy.deepcopy(after), with_rule=True)):
            non_idempotent.append(name)

    skipped = len(meetings) - processed
    print(f"scanned {processed} meetings"
          + (f" ({skipped} of {len(meetings)} skipped: unreadable or empty)" if skipped else ""))
    print(f"corpus fingerprint: {corpus_digest.hexdigest()}\n")
    print(f"segments changed by the straddling rule: {len(changed)}")
    for name, sid, before, after_words in changed:
        print(f"  {name} seg {sid}")
        print(f"      without rule: {before[-8:]}")
        print(f"      with rule   : {after_words[:8]}")
    print(f"\nnon-idempotent meetings: {non_idempotent or 'NONE'}")
    if non_idempotent:
        sys.exit(1)


if __name__ == "__main__":
    main()
