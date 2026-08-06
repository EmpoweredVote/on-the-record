"""Re-derive summary section segment boundaries from their (stable) times.

A ``SummarySection`` carries both a time range and a segment-index range for the
same span. The times are stable; the indices are not. Anything that renumbers a
meeting's segments — the adjacent-same-speaker merge, most of all — invalidates
every stored ``start_segment``/``end_segment`` while leaving the times untouched.

Times are therefore the source of truth, and the mapping back is not a guess.
``summarize.py`` builds a section's times *from* its boundary segments::

    start_time = seg_start_map[start_seg]   # that segment's start
    end_time   = seg_end_map[end_seg]       # that segment's end

so the inverse is: find the segment that *starts* at ``start_time``, and the
segment that *ends* at ``end_time``. Whenever such a segment exists the recovered
boundary is exact — the same content the summariser saw, not an approximation.
Note the asymmetry: matching the end boundary against segment *start* times
instead (``start_time <= sec.end_time``) walks it one segment too far wherever a
section ends at the instant the next one begins, which is common.

Two tiers handle the boundaries with no exact match:

*Containment* — a merged segment can straddle a topic boundary, having absorbed
fragments from both sides. Then one section ends inside it and the next starts
inside it, and both must name it. The resulting single-segment overlap is
correct, not sloppy: that segment's text genuinely belongs to both sections.
Containment is checked before proximity, because a section starting inside a long
segment must not skip to the following segment just because that segment's start
happens to sit closer in absolute time.

*Clamping* — a boundary landing in silence, or outside the transcript, falls back
to the last segment already under way (or already finished, for an end boundary).
Clamping backwards rather than to the nearest segment in either direction keeps a
section from reaching forward over content it does not cover, which would show up
as a mis-attribution; at worst it repeats a span an earlier section already had.
This is also the direction the pre-existing reindex clamped in.

Segments may overlap each other in time (interleaved diarization turns are common
in this corpus), so every tier breaks ties deterministically rather than assuming
a clean partition.
"""
from __future__ import annotations

from typing import Iterable, Sequence

EPS = 1e-6


def _resolve_start(t: float, segs: Sequence) -> int:
    """The segment id a section beginning at ``t`` starts on."""
    exact = [s for s in segs if abs(s.start_time - t) <= EPS]
    if exact:
        return min(s.segment_id for s in exact)

    containing = [s for s in segs if s.start_time <= t + EPS <= s.end_time + EPS]
    if containing:
        # Latest-starting container: the closest thing to "the section's own start".
        return max(containing, key=lambda s: (s.start_time, s.segment_id)).segment_id

    # Silence or out of range: clamp back to the last segment already under way.
    begun = [s for s in segs if s.start_time <= t + EPS]
    if not begun:
        return min(s.segment_id for s in segs)  # boundary precedes the transcript
    return max(begun, key=lambda s: (s.start_time, s.segment_id)).segment_id


def _resolve_end(t: float, segs: Sequence) -> int:
    """The segment id a section ending at ``t`` finishes on."""
    exact = [s for s in segs if abs(s.end_time - t) <= EPS]
    if exact:
        # Latest id: a trailing zero-length turn sharing the previous segment's
        # end time is still inside the section that ran to that instant.
        return max(s.segment_id for s in exact)

    containing = [s for s in segs if s.start_time <= t + EPS <= s.end_time + EPS]
    if containing:
        # Earliest-ending container: the section stops as soon as it can.
        return min(containing, key=lambda s: (s.end_time, s.segment_id)).segment_id

    # Silence or out of range: clamp back to the last segment already finished.
    ended = [s for s in segs if s.end_time <= t + EPS]
    if not ended:
        return min(s.segment_id for s in segs)  # boundary precedes the transcript
    return max(ended, key=lambda s: (s.end_time, s.segment_id)).segment_id


def reindex_sections_from_times(sections: Iterable, segments: Sequence) -> int:
    """Recompute every section's ``start_segment``/``end_segment`` from its
    ``start_time``/``end_time`` against ``segments``.

    Mutates the sections in place and returns how many of them changed. A no-op
    without sections or segments. ``segments`` should be the meeting's full
    segment list, including empty-text ones — a boundary can legitimately land on
    a segment that publish later drops.
    """
    sections = list(sections or [])
    segs = list(segments or [])
    if not sections or not segs:
        return 0

    changed = 0
    for sec in sections:
        start = _resolve_start(sec.start_time, segs)
        end = max(start, _resolve_end(sec.end_time, segs))
        if (sec.start_segment, sec.end_segment) != (start, end):
            sec.start_segment, sec.end_segment = start, end
            changed += 1
    return changed


def normalize_raw_sections(raw_sections: Sequence[dict]) -> tuple[list[dict], int, bool]:
    """Sort classifier output chronologically and repair inverted ranges.

    Returns ``(sections, clamped, moved)`` — the normalized list, how many ranges
    were clamped, and whether the order changed. The input dicts are not mutated;
    a clamped section is replaced by a copy.

    The classifier's JSON is otherwise consumed exactly as returned, and neither
    prompt requires sections to be ordered or well-formed. Both failures have
    reached production:

    *Out of order* — a section emitted after the final one but covering the middle
    of the transcript. Everything downstream assumes list order is document order
    (the web outline says so explicitly), so the meeting page's topic list ends by
    jumping backwards. Sorting by ``(start_segment, end_segment)`` puts a section
    before any section that starts later, and before a wider section starting at
    the same place.

    *Inverted range* (``end_segment < start_segment``) — the section transcript
    comes back empty, so the section is summarised from nothing and is published
    with a title and no content. Clamping here, before Pass 2, means it is handed
    a real transcript instead.

    Neither guard touches overlap. Sections legitimately overlap when a merged
    segment straddles a topic boundary, and topics in a compilation interview —
    the same question put to candidate after candidate — genuinely interleave and
    cannot be partitioned into contiguous spans.
    """
    clamped = 0
    normalized = []
    for sec in raw_sections or []:
        start = sec.get("start_segment", 0)
        end = sec.get("end_segment", start)
        if end < start:
            end = start
            clamped += 1
        if sec.get("end_segment") != end or "start_segment" not in sec:
            # Materialize the effective range so the sort key can rely on it —
            # an absent end_segment would otherwise sort as 0.
            sec = {**sec, "start_segment": start, "end_segment": end}
        normalized.append(sec)

    ordered = sorted(normalized,
                     key=lambda s: (s["start_segment"], s["end_segment"]))
    moved = any(a is not b for a, b in zip(normalized, ordered))
    return ordered, clamped, moved


def normalize_sections(sections: Sequence) -> tuple[list, int, bool]:
    """The same two repairs on already-built ``SummarySection`` objects, for
    summaries generated before the guards existed.

    Clamps in place and returns ``(ordered, clamped, moved)``. Only the segment
    boundaries are touched — a section's times, title and content are left as
    generated, so this cannot alter what a summary says. Note that clamping an
    inverted range after the fact does not backfill the content the section never
    got; only a fresh summary run can do that.
    """
    sections = list(sections or [])
    clamped = 0
    for sec in sections:
        if sec.end_segment < sec.start_segment:
            sec.end_segment = sec.start_segment
            clamped += 1
    ordered = sorted(sections, key=lambda s: (s.start_segment, s.end_segment))
    moved = any(a is not b for a, b in zip(sections, ordered))
    return ordered, clamped, moved


def sections_index_into(sections: Iterable, segments: Sequence) -> bool:
    """True when every section boundary is a segment id present in ``segments``."""
    ids = {s.segment_id for s in segments or []}
    return all(sec.start_segment in ids and sec.end_segment in ids
               for sec in sections or [])


def stale_sections(summary, segments: Sequence) -> str | None:
    """Why ``summary``'s section boundaries don't fit ``segments``, else None.

    Detection deliberately measures against the transcript's own segment ids.
    The DB is not a usable yardstick: publish skips empty-text segments when
    inserting rows, so a *correct* summary can legitimately name a boundary above
    ``max(segment_index)`` in ``meetings.segments``.
    """
    sections = getattr(summary, "sections", None) if summary else None
    if not sections or not segments:
        return None
    ids = {s.segment_id for s in segments}
    offenders = sorted(
        {b for sec in sections for b in (sec.start_segment, sec.end_segment)
         if b not in ids}
    )
    if not offenders:
        return None
    return (f"section boundaries {offenders} are not segment ids in this "
            f"transcript (ids {min(ids)}-{max(ids)})")
