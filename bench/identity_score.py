"""Score diarization identity against a human-reviewed named transcript.

DER against single-pass output answers "did chunking change what we ship",
which is the honest question when there is no ground truth — but it is a
SIMILARITY measure, and single-pass is not verified correct. Two Bloomington
meetings do have a human-reviewed `transcript_named.json` (June 10: 871
voice_profile + 184 human_review segments over 40 people; July 29: 13
people), which supports the measure that actually matters:

* **fragmentation** — one real person split across two or more labels. This
  is the error chunked diarization makes, and it is not cosmetic:
  `identify._dedupe_identities` treats two labels resolving to one person as
  a mis-identification and demotes all but the highest-confidence one to
  unnamed + needs_review, so an unmerged fragment publishes a real person's
  remarks attributed to nobody if a reviewer misses it.
* **conflation** — one label spanning two real people. Silent quote
  misattribution; strictly worse, and the reason every threshold judgment in
  this pipeline errs toward fewer merges.

Both error modes are floored by seconds AND by share of the other side's
total attributed speech (see `identity_report`'s docstring) so a 3-14s
boundary bleed against a person holding 500-1700s of the same label does not
read as a real merge — a fixed-seconds floor alone cannot tell the two apart
at meeting scale, and single-pass itself proved that on real June 10 output.

Pure: no torch, no Modal, no I/O beyond what the caller passes in.
"""

from __future__ import annotations

from dataclasses import dataclass

Turns = list[tuple[float, float, str]]


@dataclass(frozen=True)
class LabelMapping:
    person: str
    seconds: float
    purity: float


@dataclass(frozen=True)
class Fragmentation:
    person: str
    labels: list[str]
    seconds: dict[str, float]


@dataclass(frozen=True)
class Conflation:
    label: str
    people: list[str]
    seconds: dict[str, float]


@dataclass(frozen=True)
class IdentityReport:
    speakers: int
    reference_people: int
    fragmentation: list[Fragmentation]
    conflation: list[Conflation]
    mapping: dict[str, LabelMapping]
    fragmentation_summary: str
    conflation_summary: str


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _cross_seconds(hypothesis: Turns, reference: Turns) -> dict[str, dict[str, float]]:
    matrix: dict[str, dict[str, float]] = {}
    for h_start, h_end, label in hypothesis:
        row = matrix.setdefault(label, {})
        for r_start, r_end, person in reference:
            shared = _overlap(h_start, h_end, r_start, r_end)
            if shared > 0:
                row[person] = row.get(person, 0.0) + shared
    return matrix


def map_labels_to_reference(
    hypothesis: Turns, reference: Turns
) -> dict[str, LabelMapping]:
    """Map each hypothesis label to the reviewed person owning most of its speech."""
    mapping: dict[str, LabelMapping] = {}
    for label, row in _cross_seconds(hypothesis, reference).items():
        total = sum(row.values())
        person, seconds = max(row.items(), key=lambda item: item[1])
        mapping[label] = LabelMapping(
            person=person, seconds=seconds, purity=seconds / total if total else 0.0
        )
    return mapping


DEFAULT_MIN_SECONDS = 3.0
DEFAULT_MIN_FRACTION = 0.02


def _largest_minority(
    groups: dict[str, dict[str, float]]
) -> tuple[str, str, float, float, float] | None:
    """Across `groups` (key -> {subkey: seconds}), find the biggest share held
    by a non-dominant subkey. This is the "how close to real" readout: a
    group whose largest minority share is 0.8% is a boundary bleed, one
    whose largest minority share is 45% is a real second identity.
    """
    best: tuple[str, str, float, float, float] | None = None
    for key, seconds in groups.items():
        total = sum(seconds.values())
        if total <= 0:
            continue
        ranked = sorted(seconds.items(), key=lambda kv: kv[1], reverse=True)
        for subkey, secs in ranked[1:]:
            share = secs / total
            if best is None or share > best[3]:
                best = (key, subkey, secs, share, total)
    return best


def _conflation_summary(conflation: list[Conflation]) -> str:
    best = _largest_minority({c.label: c.seconds for c in conflation})
    if best is None:
        return "no conflation"
    label, person, secs, share, total = best
    return (f"largest conflation minority share: {share:.1%} "
            f"({person} holds {secs:.1f}s of {total:.1f}s under label {label})")


def _fragmentation_summary(fragmentation: list[Fragmentation]) -> str:
    best = _largest_minority({f.person: f.seconds for f in fragmentation})
    if best is None:
        return "no fragmentation"
    person, label, secs, share, total = best
    return (f"largest fragmentation minority share: {share:.1%} "
            f"(label {label} holds {secs:.1f}s of {total:.1f}s for {person})")


def identity_report(
    hypothesis: Turns,
    reference: Turns,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    min_fraction: float = DEFAULT_MIN_FRACTION,
) -> IdentityReport:
    """Fragmentation and conflation against a human-reviewed reference.

    A person/label only counts toward the OTHER side's identity if its
    overlap clears BOTH an absolute floor (`min_seconds`) and a proportional
    one (`min_fraction` of the other side's total attributed speech). The
    absolute floor alone is meaningless at meeting scale: measured on real
    June 10 output, single-pass itself -- the REFERENCE arm, not a new path --
    scored 5 "conflated" labels under min_seconds=3.0 alone, and 4 of the 5
    were 3-14s bleeds against a dominant person holding 500-1700s under the
    same label. Every configuration, including the reference, read
    "conflated" against a floor that could not tell a boundary bleed from a
    genuine two-person merge.

    Two things make `min_fraction` (default 0.02) legitimate rather than
    goalpost-moving:

    1. It was chosen from the REFERENCE's (single-pass's) own behaviour,
       before any new-path (chunked+global) result existed.
    2. Single-pass is scored with this function using the IDENTICAL floor
       whenever it is measured, so a new configuration cannot look better
       than single-pass merely by being judged against a laxer bar.

    The rule is applied symmetrically so both error modes are measured on
    the same basis: a person only counts toward a LABEL's conflation tally
    if their share clears the floor against that label's total attributed
    speech; a label only counts toward a PERSON's fragmentation tally if its
    share clears the floor against that person's total attributed speech.

    This is added reporting; it does not touch the DER/speaker-count gate.
    """
    matrix = _cross_seconds(hypothesis, reference)

    label_totals = {label: sum(row.values()) for label, row in matrix.items()}
    person_totals: dict[str, float] = {}
    for row in matrix.values():
        for person, seconds in row.items():
            person_totals[person] = person_totals.get(person, 0.0) + seconds

    conflation: list[Conflation] = []
    for label in sorted(matrix):
        floor = max(min_seconds, min_fraction * label_totals[label])
        people = {p: s for p, s in matrix[label].items() if s >= floor}
        if len(people) > 1:
            conflation.append(Conflation(
                label=label,
                people=sorted(people),
                seconds={p: round(s, 1) for p, s in sorted(people.items())},
            ))

    by_person: dict[str, dict[str, float]] = {}
    for label, row in matrix.items():
        for person, seconds in row.items():
            floor = max(min_seconds, min_fraction * person_totals[person])
            if seconds >= floor:
                by_person.setdefault(person, {})[label] = seconds
    fragmentation = [
        Fragmentation(
            person=person,
            labels=sorted(labels),
            seconds={l: round(s, 1) for l, s in sorted(labels.items())},
        )
        for person, labels in sorted(by_person.items())
        if len(labels) > 1
    ]

    return IdentityReport(
        speakers=len({label for _, _, label in hypothesis}),
        reference_people=len({person for _, _, person in reference}),
        fragmentation=fragmentation,
        conflation=conflation,
        mapping=map_labels_to_reference(hypothesis, reference),
        fragmentation_summary=_fragmentation_summary(fragmentation),
        conflation_summary=_conflation_summary(conflation),
    )


def named_reference_turns(transcript_named: dict) -> Turns:
    """Reference turns from a reviewed transcript_named.json payload.

    Segments with no `speaker_name` keep their diarized label so they still
    occupy their audio rather than silently vanishing from the reference.
    """
    return [
        (
            float(segment["start_time"]),
            float(segment["end_time"]),
            segment.get("speaker_name") or f"UNNAMED::{segment['speaker_label']}",
        )
        for segment in transcript_named["segments"]
        if float(segment["end_time"]) > float(segment["start_time"])
    ]
