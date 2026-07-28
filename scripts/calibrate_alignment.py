#!/usr/bin/env python
"""Calibrate Pass B alignment on the July 22 fixtures with a REAL LLM call.

Loads the captured agenda + segment-index fixtures, runs align_items against
the live Anthropic API, cross-checks outcomes against the real legislation-
page oracle, prints the per-item mapping table for hand-checking, and saves
the raw LLM reply to tests/fixtures/alignment/llm_reply_2026-07-22.json so
the run can be pinned as a replay test.

NO database access anywhere — this is a read-fixtures, call-API, print job.

Usage:
  .venv/bin/python scripts/calibrate_alignment.py

Requires ANTHROPIC_API_KEY in .env.local (gui.env loader pattern, same as
scripts/poll_agendas.py).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from gui.env import load_env_local

load_env_local()  # before src.config so ANTHROPIC_API_KEY is visible

import anthropic

from src.agenda_align import SegmentRef, align_items, apply_oracle, find_ref_anchors
from src.agenda_parse import parse_agenda
from src.legislation_oracle import _default_fetch

FIXTURES = REPO / "tests" / "fixtures"
AGENDA_TXT = FIXTURES / "onboard" / "agenda_2026-07-22.txt"
SEGMENTS_JSON = FIXTURES / "alignment" / "segments_2026-07-22.json"
REPLY_OUT = FIXTURES / "alignment" / "llm_reply_2026-07-22.json"


class RecordingClient:
    """Wraps the real anthropic client, capturing the raw reply text."""

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            response = self._outer.real.messages.create(**kwargs)
            self._outer.raw_reply = response.content[0].text
            return response

    def __init__(self, real):
        self.real = real
        self.raw_reply = None
        self.messages = RecordingClient._Messages(self)


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def main() -> int:
    items = parse_agenda(AGENDA_TXT.read_text())
    segments = [SegmentRef(**r) for r in json.loads(SEGMENTS_JSON.read_text())]
    print(f"{len(items)} agenda items, {len(segments)} segments")

    anchors = find_ref_anchors(items, segments)
    print("Anchors:", {p: hits for p, hits in sorted(anchors.items())})
    print()

    client = RecordingClient(anthropic.Anthropic())
    spans = align_items(client, items, segments)

    REPLY_OUT.write_text(client.raw_reply)
    print(f"Raw LLM reply saved to {REPLY_OUT}")
    print()

    print("Oracle cross-check (real fetch; pending pages 404 -> no-op)...")
    spans = apply_oracle(spans, items, fetch=_default_fetch)
    print()

    by_pos = {item.position: item for item in items}
    header = (
        f"{'pos':>3} | {'item':>4} | {'title':<38} | {'span':<12} | "
        f"{'times':<17} | {'outcome':<9} | rejected_reason"
    )
    print(header)
    print("-" * len(header))
    for span in spans:
        item = by_pos[span.position]
        if span.start_segment is not None:
            seg_span = f"{span.start_segment}..{span.end_segment}"
            times = (
                f"{_mmss(segments[span.start_segment].start)}"
                f"-{_mmss(segments[span.end_segment].end)}"
            )
        else:
            seg_span, times = "-", "-"
        outcome = span.outcome or "-"
        if span.outcome_evidence_segment is not None:
            outcome += f"@{span.outcome_evidence_segment}"
        print(
            f"{span.position:>3} | {item.item_number:>4} | "
            f"{item.title_raw[:38]:<38} | {seg_span:<12} | {times:<17} | "
            f"{outcome:<9} | {span.rejected_reason or '-'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
