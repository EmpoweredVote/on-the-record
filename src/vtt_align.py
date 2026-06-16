"""Align VTT subtitle cues to diarized speaker segments.

Replaces Whisper transcription by mapping pre-existing VTT captions
(from CATS TV) onto diarized segments based on timestamp overlap.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Segment, Word


def _token_key(token: str) -> str:
    """Normalize a caption token for rolling-cue overlap comparisons."""
    return re.sub(r"\W+", "", token).casefold()


def _merge_rolling_lines(lines: list[str]) -> str:
    """Collapse expanding/repeated lines inside one caption cue."""
    merged: list[str] = []
    merged_keys: list[str] = []

    for line in lines:
        tokens = re.sub(r"<[^>]+>", "", line).strip().split()
        keys = [_token_key(token) for token in tokens]
        overlap_count = 0
        for count in range(min(len(merged_keys), len(keys)), 0, -1):
            if merged_keys[-count:] == keys[:count]:
                overlap_count = count
                break
        merged.extend(tokens[overlap_count:])
        merged_keys.extend(keys[overlap_count:])

    return " ".join(merged)


def parse_vtt(vtt_path: str | Path) -> list[dict]:
    """Parse a WebVTT file into a list of cue dicts.

    Each cue dict has: start (float), end (float), text (str).
    """
    content = Path(vtt_path).read_text(encoding="utf-8")
    cues = []

    # Split on blank lines to get blocks
    blocks = re.split(r"\n\s*\n", content)

    for block in blocks:
        lines = block.strip().split("\n")
        # Find the timestamp line
        ts_line = None
        text_lines = []
        for line in lines:
            if "-->" in line:
                ts_line = line
            elif ts_line is not None:
                # Everything after timestamp is text
                text_lines.append(line)

        if ts_line and text_lines:
            start, end = _parse_timestamp_line(ts_line)
            if start is not None:
                text = _merge_rolling_lines(text_lines)
                if text:
                    cues.append({"start": start, "end": end, "text": text})

    return cues


def _parse_timestamp_line(line: str) -> tuple[float | None, float | None]:
    """Parse a VTT timestamp line like '00:01:23.456 --> 00:01:25.789'."""
    match = re.search(
        r"(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})",
        line,
    )
    if not match:
        return None, None

    def to_seconds(h, m, s, ms):
        h = int(h.rstrip(":")) if h else 0
        return h * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    start = to_seconds(match.group(1), match.group(2), match.group(3), match.group(4))
    end = to_seconds(match.group(5), match.group(6), match.group(7), match.group(8))
    return start, end


def _overlap(seg_start: float, seg_end: float, cue_start: float, cue_end: float) -> float:
    """Compute overlap duration between a segment and a cue."""
    overlap_start = max(seg_start, cue_start)
    overlap_end = min(seg_end, cue_end)
    return max(0.0, overlap_end - overlap_start)


def _deduplicated_words(cues: list[dict]) -> list[Word]:
    """Convert rolling VTT cues into one chronological, non-repeating stream."""
    emitted_keys: list[str] = []
    timed_words: list[Word] = []
    previous_end: float | None = None

    for cue in cues:
        tokens = cue["text"].split()
        keys = [_token_key(token) for token in tokens]

        overlap_count = 0
        if previous_end is not None and cue["start"] <= previous_end + 0.25:
            max_overlap = min(len(emitted_keys), len(keys))
            for count in range(max_overlap, 0, -1):
                if emitted_keys[-count:] == keys[:count]:
                    overlap_count = count
                    break

        duration = cue["end"] - cue["start"]
        if duration <= 0 or not tokens:
            continue

        word_duration = duration / len(tokens)
        for index in range(overlap_count, len(tokens)):
            timed_words.append(
                Word(
                    word=tokens[index],
                    start=round(cue["start"] + index * word_duration, 3),
                    end=round(cue["start"] + (index + 1) * word_duration, 3),
                )
            )

        emitted_keys.extend(keys[overlap_count:])
        previous_end = cue["end"]

    return timed_words


def align_vtt_to_segments(
    vtt_path: str | Path,
    diarized_segments: list[Segment],
) -> list[Segment]:
    """Align VTT cues to diarized segments by timestamp overlap.

    For each diarized segment, finds overlapping VTT cues and assigns
    the text proportionally. This replaces Whisper transcription.

    Args:
        vtt_path: Path to the VTT subtitle file.
        diarized_segments: Segments from diarization (no text yet).

    Returns:
        The same segments list, now with text populated from VTT.
    """
    cues = parse_vtt(vtt_path)
    if not cues:
        print("  Warning: VTT file contains no cues")
        return diarized_segments
    if not diarized_segments:
        return diarized_segments

    for seg in diarized_segments:
        seg.text = ""
        seg.words = []

    for word in _deduplicated_words(cues):
        midpoint = (word.start + word.end) / 2
        target = next(
            (
                seg
                for seg in diarized_segments
                if seg.start_time <= midpoint < seg.end_time
            ),
            None,
        )
        if target is None:
            candidates = [
                (
                    _overlap(
                        seg.start_time,
                        seg.end_time,
                        word.start,
                        word.end,
                    ),
                    seg,
                )
                for seg in diarized_segments
            ]
            overlap_dur, target = max(candidates, key=lambda item: item[0])
            if overlap_dur <= 0:
                continue

        target.words.append(word)

    for seg in diarized_segments:
        seg.text = " ".join(word.word for word in seg.words)

    aligned_count = sum(1 for s in diarized_segments if s.text)
    total = len(diarized_segments)
    print(f"  VTT alignment: {aligned_count}/{total} segments received text")

    return diarized_segments
