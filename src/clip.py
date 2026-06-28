"""Clip-window helpers: time-format parsing and the source-absolute offset transform.

A clip window is a single contiguous slice of a source recording that was
transcribed (e.g. an interview inside a longer podcast). The pipeline runs
clip-local (0-based) internally; these helpers convert published timestamps
back into the full source's timeline. See docs/adr/0001-clip-window-ingest-time-only.md.
"""

from __future__ import annotations

import copy
import math

from .models import Meeting


def parse_clip_time(text: str) -> float:
    """Parse a clip boundary as seconds, HH:MM:SS, or MM:SS into float seconds.

    Accepts: "1380", "1380.5", "23:00" (MM:SS), "1:05:00" (HH:MM:SS).
    Raises ValueError on empty/malformed input, negative values, or a
    minutes/seconds field >= 60.
    """
    s = (text or "").strip()
    if not s:
        raise ValueError("empty clip time")

    if ":" not in s:
        value = float(s)
        if not math.isfinite(value):
            raise ValueError(f"clip time must be a finite number: {text!r}")
        if value < 0:
            raise ValueError(f"clip time cannot be negative: {text!r}")
        return value

    parts = s.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid clip time {text!r} — use SS, MM:SS, or HH:MM:SS")

    nums = [float(p) for p in parts]
    if any(not math.isfinite(n) for n in nums):
        raise ValueError(f"clip time must be a finite number: {text!r}")
    if any(n < 0 for n in nums):
        raise ValueError(f"clip time cannot be negative: {text!r}")
    if any(n >= 60 for n in nums[1:]):
        raise ValueError(f"invalid clip time {text!r} — minutes/seconds must be < 60")

    if len(nums) == 2:
        minutes, seconds = nums
        return minutes * 60 + seconds
    hours, minutes, seconds = nums
    return hours * 3600 + minutes * 60 + seconds


def absolutize_meeting_times(meeting: Meeting) -> Meeting:
    """Return a deep copy of `meeting` with all timestamps shifted into the
    full source's timeline.

    Adds `clip_start_seconds` (the offset) to every segment start/end and every
    summary-section start/end. Lengths (`duration_seconds`) and the clip-window
    fields themselves are left untouched. A meeting with no clip window
    (`clip_start_seconds` falsy) is returned as an unchanged copy.
    """
    offset = meeting.clip_start_seconds or 0.0
    out = copy.deepcopy(meeting)
    if not offset:
        return out

    for seg in out.segments:
        seg.start_time += offset
        seg.end_time += offset
        for word in seg.words:
            word.start += offset
            word.end += offset
    if out.summary:
        for sec in out.summary.sections:
            sec.start_time += offset
            sec.end_time += offset
    return out
