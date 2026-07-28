"""Anchor-first alignment of agenda items to transcript segments (Pass B).

Mechanical layer of the structure-alignment design (2026-07-18): legislation
refs spoken in segments are hard anchors, outcome phrases are hard evidence,
and `validate_spans` gates whatever the LLM proposes — abstain-don't-guess.
Holes are modeled: an item without a span is "not reached", a stretch of
transcript without an item is procedural.

Matching rules (calibrated on the July 22 fixtures):

- Full-form ref match (type word + number) always anchors. The transcript
  renders mid-sentence pauses as " - ", so "ordinance - 2026-15" is a real
  spoken form (segs 46, 395 of the July 22 fixture); the separator between
  type word and number therefore tolerates spaces and dashes.
- A bare number ("2026-15") anchors ONLY when that number is unique across
  the meeting's items. July 22 has both Resolution 2026-12 and Ordinance
  2026-12, so bare "2026-12" is ambiguous and anchors neither.
- Spoken-out numbers ("2026 dash 12", "2026. - dash 12") deliberately do
  NOT match in v1 — the one July 22 utterance of "ordinance 2026 dash 12"
  is in fact a misstatement (the speaker meant Resolution 2026-12), which
  is exactly why we don't loosen this.

Outcome evidence is necessary-not-sufficient: a phrase table plus vote-tally
patterns ("seven to two", "7-2" — tallies count for passed/failed only,
since a tally alone carries no direction; direction is cross-checked by the
legislation-page oracle in `apply_oracle`). Negations are not modeled.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Optional

from .agenda_parse import ParsedItem

#: Publish vocabulary for item outcomes (matches src/legislation_oracle.py).
OUTCOME_VOCABULARY = ("passed", "failed", "continued", "pulled")

#: Phrases that count as on-the-record evidence for each outcome. Substring
#: match, lowercased. Weak by design (a motion "be adopted" is not itself an
#: adoption) — the gate rejects fabricated outcomes, not optimistic ones;
#: the oracle handles direction.
OUTCOME_PHRASES = {
    "passed": (
        "adopted", "passes", "passed", "carries", "carried",
        "is approved", "ayes have it", "so ordered",
    ),
    "failed": (
        "does not carry", "does not pass", "fails", "failed",
        "defeated", "rejected", "nays have it",
    ),
    "continued": ("postponed", "continued", "tabled"),
    "pulled": ("withdrawn", "withdraw"),
}

#: How far past a span's end the outcome evidence segment may sit (the
#: chair often announces the result just after the clerk's roll call).
OUTCOME_EVIDENCE_SLACK = 5

_NUMBER_WORD = r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
# Digit tallies capped at 2 digits per side so a legislation number
# ("2026-15") can never read as a vote tally.
_TALLY_RE = re.compile(
    rf"\b(?:\d{{1,2}}\s*(?:-|–|to)\s*\d{{1,2}}|{_NUMBER_WORD}\s+to\s+{_NUMBER_WORD})\b",
    re.IGNORECASE,
)

# Separator the transcript uses between a ref's type word and number:
# whitespace and/or pause dashes ("ordinance - 2026-15", "ordinance–2026-15").
_REF_SEP = r"[\s\-–—]+"


@dataclass
class SegmentRef:
    """One transcript segment of the compact alignment index."""

    i: int
    start: float
    end: float
    speaker: str
    text: str


@dataclass
class ItemSpan:
    """An agenda item's claimed run of segments, post-validation."""

    position: int
    start_segment: Optional[int] = None  # None = not reached / abstained
    end_segment: Optional[int] = None
    outcome: Optional[str] = None  # OUTCOME_VOCABULARY or None
    outcome_evidence_segment: Optional[int] = None
    rejected_reason: Optional[str] = None  # why validation zeroed span/outcome


def _split_ref(legislation_ref: str) -> tuple[str, str]:
    """"Appropriation Ordinance 2026-3" -> ("Appropriation Ordinance", "2026-3")."""
    kind, number = legislation_ref.rsplit(" ", 1)
    return kind, number


def _full_form_re(legislation_ref: str) -> re.Pattern:
    kind, number = _split_ref(legislation_ref)
    kind_pat = _REF_SEP.join(re.escape(word) for word in kind.split())
    return re.compile(kind_pat + _REF_SEP + re.escape(number), re.IGNORECASE)


def _bare_number_re(number: str) -> re.Pattern:
    # "2026-1" must not hit inside "2026-15" (or "12026-1").
    return re.compile(rf"(?<![\d-]){re.escape(number)}(?![\d-])")


def _ambiguous_numbers(items: list[ParsedItem]) -> set[str]:
    counts = Counter(
        _split_ref(item.legislation_ref)[1] for item in items if item.legislation_ref
    )
    return {number for number, n in counts.items() if n > 1}


def _ref_matches(legislation_ref: str, text: str, ambiguous: set[str]) -> bool:
    if _full_form_re(legislation_ref).search(text):
        return True
    number = _split_ref(legislation_ref)[1]
    return number not in ambiguous and bool(_bare_number_re(number).search(text))


def find_ref_anchors(
    items: list[ParsedItem], segments: list[SegmentRef]
) -> dict[int, list[int]]:
    """position -> sorted segment indices whose text anchors the item's ref.

    Every ref-bearing item gets a key; an empty list means the ref was never
    spoken recognizably (worth a look before trusting the LLM's span).
    """
    ambiguous = _ambiguous_numbers(items)
    anchors: dict[int, list[int]] = {}
    for item in items:
        if not item.legislation_ref:
            continue
        anchors[item.position] = [
            seg.i
            for seg in segments
            if _ref_matches(item.legislation_ref, seg.text, ambiguous)
        ]
    return anchors


def outcome_evidence_ok(outcome: str, segment_text: str) -> bool:
    """Does `segment_text` contain a phrase consistent with `outcome`?

    Tallies ("seven to two", "7-2") count for passed/failed only — they show
    a vote concluded but not its direction; `apply_oracle` checks direction.
    """
    phrases = OUTCOME_PHRASES.get(outcome)
    if phrases is None:
        return False
    lowered = segment_text.lower()
    if any(phrase in lowered for phrase in phrases):
        return True
    return outcome in ("passed", "failed") and bool(_TALLY_RE.search(lowered))


def _title_tokens(title_raw: str) -> set[str]:
    """Distinctive title tokens: words longer than 5 characters, lowercased."""
    return {w for w in re.findall(r"[a-z]+", title_raw.lower()) if len(w) > 5}


def _containment_ok(item: ParsedItem, span_text: str, ambiguous: set[str]) -> bool:
    if item.legislation_ref:
        return _ref_matches(item.legislation_ref, span_text, ambiguous)
    tokens = _title_tokens(item.title_raw)
    lowered = span_text.lower()
    return sum(1 for token in tokens if token in lowered) >= 2


def _zero_span(span: ItemSpan, reason: str) -> ItemSpan:
    return ItemSpan(position=span.position, rejected_reason=reason)


def _strip_outcome(span: ItemSpan, reason: str) -> ItemSpan:
    return replace(span, outcome=None, outcome_evidence_segment=None, rejected_reason=reason)


def validate_spans(
    items: list[ParsedItem], spans: list[ItemSpan], segments: list[SegmentRef]
) -> list[ItemSpan]:
    """Mechanically gate proposed spans; zero anything that fails, with a reason.

    Span-level failures (range, monotonicity, containment) zero the whole
    span. Outcome-level failures (vocabulary, evidence) strip only the
    outcome and keep the span — same shape as the oracle gate. v1 rejects
    out-of-order spans rather than accepting out-of-order discussion.
    """
    by_position = {item.position: item for item in items}
    ambiguous = _ambiguous_numbers(items)
    n = len(segments)
    result: list[ItemSpan] = []
    prev_start: Optional[int] = None  # max accepted start so far, position order

    for span in sorted(spans, key=lambda s: s.position):
        item = by_position.get(span.position)
        if item is None:
            result.append(_zero_span(span, f"no agenda item at position {span.position}"))
            continue

        start, end = span.start_segment, span.end_segment
        if start is None and end is None:
            if span.outcome is not None or span.outcome_evidence_segment is not None:
                span = _strip_outcome(span, "outcome claimed without a span")
            result.append(span)
            continue

        if (
            start is None
            or end is None
            or not (0 <= start < n)
            or not (0 <= end < n)
            or end < start
        ):
            result.append(_zero_span(span, f"segment range invalid: {start}..{end}"))
            continue

        if prev_start is not None and start < prev_start:
            result.append(
                _zero_span(
                    span,
                    f"non-monotonic: starts at {start}, before an earlier item's span",
                )
            )
            continue

        span_text = " ".join(seg.text for seg in segments[start : end + 1])
        if not _containment_ok(item, span_text, ambiguous):
            result.append(
                _zero_span(span, "containment gate failed: span text names neither "
                                 "the item's ref nor 2+ distinctive title tokens")
            )
            continue

        prev_start = start  # only accepted spans raise the monotonicity bar

        if span.outcome is not None or span.outcome_evidence_segment is not None:
            evidence = span.outcome_evidence_segment
            if span.outcome not in OUTCOME_VOCABULARY:
                span = _strip_outcome(span, f"outcome not in vocabulary: {span.outcome!r}")
            elif evidence is None:
                span = _strip_outcome(span, "outcome claimed without an evidence segment")
            elif not (start <= evidence <= min(end + OUTCOME_EVIDENCE_SLACK, n - 1)):
                span = _strip_outcome(
                    span,
                    f"evidence segment {evidence} outside span "
                    f"[{start}..{end}]+{OUTCOME_EVIDENCE_SLACK}",
                )
            elif not outcome_evidence_ok(span.outcome, segments[evidence].text):
                span = _strip_outcome(
                    span,
                    f"segment {evidence} does not evidence outcome {span.outcome!r}",
                )
        result.append(span)

    return result
