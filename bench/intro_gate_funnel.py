"""Gate-by-gate census of _snap_straddling_intro, by tracing the shipped function.

Answers "how many segment pairs does each gate reject?" without restating a
single gate. It runs the real snap_segment_boundaries pass and traces
_snap_straddling_intro, recording the source line of the `return` that fired
for each pair. Gate labels are the source lines themselves, read back from
src/word_assign.py, so this tool cannot drift from the code it measures.

That property is the point. This rule's calibration was originally done with a
hand-written re-implementation of the gates, which differed from the shipped
walk-back by one loop bound. Every figure downstream was wrong in the unsafe
direction: the population before the length cap was reported as 6 when it is 9,
and the nearest false positive as a 180-word tail when it is 21 — a real
candidate closing statement that a slightly wider cap would have republished
under another candidate's name. Two independent re-runs missed it, because both
re-ran the same wrong script.

    .venv/bin/python bench/intro_gate_funnel.py [--corpus DIR] [--verbose]

On the 172-meeting corpus as of 2026-09-09 the standing column reads
104 -> 63 -> 47 -> 14 -> 11 -> 9 -> 1, sole survivor
bloomington-city-council-2026-05-06 seg 793. The spec quotes the same funnel
with the two continuation returns collapsed: 104 -> 63 -> 47 -> 11 -> 9 -> 1.
A different shape here is not necessarily a defect — the corpus grows — but a
figure that disagrees with the spec means one of them needs re-deriving, and
this tool is the one to trust, because it cannot restate a gate wrongly.

Read-only: it never writes to a transcript.
"""
from __future__ import annotations

import argparse
import collections
import copy
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
    return [
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


def source_lines() -> dict[int, str]:
    """Every line of src/word_assign.py, 1-indexed, for labelling exit points."""
    src = pathlib.Path(word_assign.__file__).read_text().splitlines()
    return {i: line.strip() for i, line in enumerate(src, start=1)}


def traced(func):
    """Wrap `func` so each call records the source line of the return that fired.

    Returns (wrapper, exits) where exits is a list of (lineno, returned_value,
    context) appended to on every call. The wrapper delegates to the real
    function and returns its real result, so the surrounding pass behaves
    exactly as it does in production.
    """
    exits: list[tuple[int, object, dict]] = []
    code = func.__code__
    context: dict = {}

    def wrapper(a, b):
        last = {"line": None}

        def local_tracer(frame, event, arg):
            if event == "line":
                last["line"] = frame.f_lineno
            elif event == "return":
                exits.append((last["line"], arg, dict(context, a=a, b=b)))
            return local_tracer

        def global_tracer(frame, event, arg):
            if event == "call" and frame.f_code is code:
                return local_tracer
            return None

        previous = sys.gettrace()
        sys.settrace(global_tracer)
        try:
            return func(a, b)
        finally:
            sys.settrace(previous)

    return wrapper, exits, context


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default="~/CouncilScribe/meetings",
                    help="directory of meeting folders (default: %(default)s)")
    ap.add_argument("--verbose", action="store_true",
                    help="list the pairs that reach the final gate")
    args = ap.parse_args()

    root = pathlib.Path(args.corpus).expanduser()
    meetings = sorted(p for p in root.glob("*/transcript_named.json"))
    if not meetings:
        sys.exit(f"no transcripts under {root}")

    real = word_assign._snap_straddling_intro
    wrapper, exits, context = traced(real)
    word_assign._snap_straddling_intro = wrapper

    processed = skipped = 0
    try:
        for path in meetings:
            context["meeting"] = path.parent.name
            try:
                segs = load(path)
            except (OSError, json.JSONDecodeError) as exc:
                print(f"  !! {path.parent.name}: unreadable ({exc})")
                skipped += 1
                continue
            if not segs:
                skipped += 1
                continue
            processed += 1
            word_assign.snap_segment_boundaries(copy.deepcopy(segs))
    finally:
        word_assign._snap_straddling_intro = real

    # snap_segment_boundaries runs to a fixpoint, so one pair is evaluated
    # several times. Count each pair once, at its first evaluation, so the
    # figures are a census of the corpus rather than of the loop.
    lines = source_lines()
    first: dict[tuple[str, int], tuple[int, object]] = {}
    survivors = []
    for line, ret, ctx in exits:
        key = (ctx["meeting"], ctx["a"].segment_id)
        if key in first:
            continue
        first[key] = (line, ret)
        if ret:
            survivors.append(ctx)

    counts: collections.Counter = collections.Counter(l for l, _ in first.values())
    print(f"scanned {processed} meetings ({skipped} skipped)")
    print(f"{len(first)} unique segment pairs evaluated "
          f"({len(exits)} calls across fixpoint passes)\n")

    # Cumulative funnel: each gate is reached only by what earlier gates passed,
    # so "still standing" is the population the NEXT gate sees.
    print(f"{'line':>6}  {'rejects':>8}  {'standing':>9}  gate (source line)")
    print(f"{'-'*6}  {'-'*8}  {'-'*9}  {'-'*52}")
    standing = len(first)
    for line in sorted(counts):
        text = lines.get(line, "?")
        if text.startswith("return _move"):
            continue
        standing -= counts[line]
        print(f"{line:>6}  {counts[line]:>8}  {standing:>9}  {text[:52]}")
    print(f"\n  moved (rule fired): {len(survivors)}")

    if args.verbose:
        print("\nsurviving pairs:")
        for ctx in survivors:
            print(f"  {ctx['meeting']} seg {ctx['a'].segment_id}")


if __name__ == "__main__":
    main()
