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


def identity_report(
    hypothesis: Turns, reference: Turns, min_seconds: float = 3.0
) -> IdentityReport:
    """Fragmentation and conflation against a human-reviewed reference.

    `min_seconds` floors both error modes: a sub-floor overlap between a label
    and a person is boundary noise (diarization routinely bleeds a word across
    a turn edge), not evidence of a second identity.
    """
    matrix = _cross_seconds(hypothesis, reference)

    conflation: list[Conflation] = []
    for label in sorted(matrix):
        people = {p: s for p, s in matrix[label].items() if s >= min_seconds}
        if len(people) > 1:
            conflation.append(Conflation(
                label=label,
                people=sorted(people),
                seconds={p: round(s, 1) for p, s in sorted(people.items())},
            ))

    by_person: dict[str, dict[str, float]] = {}
    for label, row in matrix.items():
        for person, seconds in row.items():
            if seconds >= min_seconds:
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
