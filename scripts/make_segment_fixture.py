#!/usr/bin/env python
"""Freeze a processed meeting's transcript into a compact segment-index fixture.

Reads a local meeting's transcript_named.json and writes a JSON list of
`{"i": <list index>, "start": <s>, "end": <s>, "speaker": <name or label>,
"text": <full text>}` — the shape the agenda-alignment code consumes.
Full segment text is kept verbatim (legislation-ref and outcome anchors live
in it).

Usage:
  .venv/bin/python scripts/make_segment_fixture.py \
      ~/CouncilScribe/meetings/2026-07-22-bloomington-regular-session/transcript_named.json \
      tests/fixtures/alignment/segments_2026-07-22.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_segment_index(transcript: dict) -> list[dict]:
    """Map transcript_named segments to the compact alignment shape."""
    out = []
    for i, seg in enumerate(transcript.get("segments", [])):
        out.append(
            {
                "i": i,
                "start": seg["start_time"],
                "end": seg["end_time"],
                "speaker": seg.get("speaker_name") or seg.get("speaker_label") or "",
                "text": seg.get("text", ""),
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("transcript", type=Path, help="path to transcript_named.json")
    ap.add_argument("output", type=Path, help="path to write the segment index JSON")
    args = ap.parse_args()

    transcript = json.loads(args.transcript.expanduser().read_text())
    index = build_segment_index(transcript)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {len(index)} segments -> {args.output}")


if __name__ == "__main__":
    main()
