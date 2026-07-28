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

import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable, Optional

from . import config
from .agenda_parse import ParsedItem
from .legislation_oracle import fetch_final_action

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


# A plain "Ordinance NNNN-N" must not match INSIDE "Appropriation Ordinance
# NNNN-N" — with both types on one agenda that would anchor (and satisfy
# containment for) the wrong item. Checked against the text preceding each
# candidate match, since the separator run makes a lookbehind variable-width.
_APPROPRIATION_TAIL_RE = re.compile(rf"\bappropriation{_REF_SEP}$", re.IGNORECASE)


def _full_form_present(legislation_ref: str, text: str) -> bool:
    kind = _split_ref(legislation_ref)[0]
    plain_ordinance = kind.lower() == "ordinance"
    for match in _full_form_re(legislation_ref).finditer(text):
        if plain_ordinance and _APPROPRIATION_TAIL_RE.search(text, 0, match.start()):
            continue
        return True
    return False


def _ambiguous_numbers(items: list[ParsedItem]) -> set[str]:
    counts = Counter(
        _split_ref(item.legislation_ref)[1] for item in items if item.legislation_ref
    )
    return {number for number, n in counts.items() if n > 1}


def _ref_matches(legislation_ref: str, text: str, ambiguous: set[str]) -> bool:
    if _full_form_present(legislation_ref, text):
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

# ---------------------------------------------------------------------------
# LLM span bounding (Task 4) — the model proposes, the gates dispose.
# ---------------------------------------------------------------------------

_ALIGN_SYSTEM = (
    "You align a city-council meeting's agenda items to its transcript. For "
    "each agenda item, identify the contiguous run of transcript segments in "
    "which that item is actually taken up, using the mechanical anchor hints "
    "where given. Items never reached get null bounds. Report an outcome "
    "ONLY when it is announced on the record (motion carries / does not "
    "carry, adopted, roll-call result), and cite the segment where it is "
    "announced; do NOT infer or invent outcomes. Procedural stretches "
    "(roll-call reading, recesses, chatter between items) belong to no "
    "item. Spans must follow agenda order: no span may start before an "
    "earlier item's span starts. A ref is sometimes mentioned procedurally "
    "long before its item is taken up (e.g. an early motion to introduce); "
    "such early anchor mentions may fall outside the span — bound the span "
    "where the item is substantively taken up. Reply with JSON only: "
    "{\"spans\": [{\"position\": N, "
    "\"start_segment\": i or null, \"end_segment\": i or null, "
    "\"outcome\": \"passed\"|\"failed\"|\"continued\"|\"pulled\"|null, "
    "\"outcome_evidence_segment\": i or null}]} with one entry per agenda "
    "item position."
)


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def build_align_prompt(
    items: list[ParsedItem],
    segments: list[SegmentRef],
    anchors: dict[int, list[int]],
) -> str:
    lines = ["Agenda items (position | item | title | legislation ref):"]
    for item in items:
        lines.append(
            f"{item.position} | {item.item_number} | {item.title_raw} | "
            f"{item.legislation_ref or '-'}"
        )
    lines.append("")
    lines.append(
        "Mechanical anchors (segment indices whose text contains the item's "
        "legislation ref; early procedural mentions may precede the item's "
        "actual span):"
    )
    for position in sorted(anchors):
        hits = ", ".join(str(i) for i in anchors[position]) or "none found"
        lines.append(f"position {position}: {hits}")
    lines.append("")
    lines.append("Transcript segments (i | mm:ss | speaker | text):")
    for seg in segments:
        text = " ".join(seg.text.split())[:160]
        lines.append(f"{seg.i} | {_mmss(seg.start)} | {seg.speaker} | {text}")
    return "\n".join(lines)


def _all_abstain(items: list[ParsedItem], reason: str) -> list[ItemSpan]:
    return [ItemSpan(position=item.position, rejected_reason=reason) for item in items]


def _int_or_none(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def align_items(
    client, items: list[ParsedItem], segments: list[SegmentRef]
) -> list[ItemSpan]:
    """Anchor → prompt → tolerant JSON parse → validate_spans. Never raises
    on a bad reply: a reply we can't use means every item abstains, with the
    reason recorded."""
    anchors = find_ref_anchors(items, segments)
    response = client.messages.create(
        model=config.AGENDA_ALIGN_MODEL,
        max_tokens=config.AGENDA_ALIGN_MAX_TOKENS,
        system=_ALIGN_SYSTEM,
        messages=[
            {"role": "user", "content": build_align_prompt(items, segments, anchors)}
        ],
    )
    try:
        text = response.content[0].text
    except (AttributeError, IndexError, TypeError):
        return _all_abstain(items, "unusable reply content")
    if not isinstance(text, str):
        return _all_abstain(items, "unusable reply content")
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return _all_abstain(items, "no JSON in reply")
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return _all_abstain(items, "malformed JSON")
    raw_spans = payload.get("spans") if isinstance(payload, dict) else None
    if not isinstance(raw_spans, list):
        return _all_abstain(items, "no spans in reply")

    by_position: dict[int, ItemSpan] = {}
    duplicates: set[int] = set()
    for entry in raw_spans:
        if not isinstance(entry, dict):
            continue
        position = _int_or_none(entry.get("position"))
        if position is None:
            continue
        if position in by_position:
            # Two entries for one item conflict — trust neither.
            duplicates.add(position)
            continue
        outcome = entry.get("outcome")
        by_position[position] = ItemSpan(
            position=position,
            start_segment=_int_or_none(entry.get("start_segment")),
            end_segment=_int_or_none(entry.get("end_segment")),
            outcome=outcome if isinstance(outcome, str) else None,
            outcome_evidence_segment=_int_or_none(entry.get("outcome_evidence_segment")),
        )
    spans = []
    for item in items:
        if item.position in duplicates:
            spans.append(
                ItemSpan(position=item.position, rejected_reason="duplicate position in reply")
            )
        else:
            spans.append(
                by_position.get(
                    item.position,
                    ItemSpan(
                        position=item.position,
                        rejected_reason="position missing from reply",
                    ),
                )
            )
    return validate_spans(items, spans, segments)


def apply_oracle(
    spans: list[ItemSpan],
    items: list[ParsedItem],
    *,
    fetch: Callable[[str], str],
) -> list[ItemSpan]:
    """Cross-check claimed outcomes against the legislation-page oracle.

    Oracle has a final action that disagrees -> zero the OUTCOME (keep the
    span) with reason "oracle disagreement". Oracle agrees, or is pending
    (no page / no Final row / fetch error) -> keep the claim as-is.
    """
    by_position = {item.position: item for item in items}
    actions: dict[str, object] = {}  # ref -> FinalAction | None, fetched once
    result: list[ItemSpan] = []
    for span in spans:
        item = by_position.get(span.position)
        ref = item.legislation_ref if item else None
        if span.outcome is None or not ref:
            result.append(span)
            continue
        if ref not in actions:
            actions[ref] = fetch_final_action(ref, fetch=fetch)
        action = actions[ref]
        if action is not None and action.outcome != span.outcome:
            result.append(
                _strip_outcome(
                    span,
                    f"oracle disagreement: legislation page says "
                    f"{action.outcome!r}, span claimed {span.outcome!r}",
                )
            )
        else:
            result.append(span)
    return result
