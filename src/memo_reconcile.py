"""Plan DB writes from a parsed clerk memorandum (pure; no cursors).

The memo is authoritative for outcomes and votes: dispositions OVERWRITE
agenda_items.outcome (this is the fix for the pass-abstention limitation —
the chair never says "motion carries", so the LLM pass abstains on passes),
and every substantive motion (adopt/amend/continue/pull kinds) with a
recorded roll call — including ones that did not carry — becomes a
meetings.votes row. Named split votes additionally plan per-member
meetings.vote_records rows. Amendment ('amend') motions get vote rows but
never outcome updates — outcomes come only from item.disposition, which
memo_parse keeps amendment-free.

Memo items match agenda items by exact legislation_ref. When the exact ref
misses, a guarded fallback matches by bare number ("Resolution 2026-15" ->
"Ordinance 2026-15" for a clerk ref-type mislabel) ONLY when that number is
unique across both all memo item refs and all agenda item refs; anything
less unique is refused with a loud note (June 10 holds both Ordinance
2026-12 and Resolution 2026-12 in one memo). Agenda-side uniqueness is
counted per row, so an agenda item excluded by the duplicate-refs guard
can never come back into reach through the fallback.

vote_records.speaker_id is NOT NULL and speakers are diarization-owned, so
a memo name with no (or an ambiguous) speaker match SKIPS that record with
a loud note — we never fabricate speaker rows. Unnamed unanimous tallies
plan no records (deriving members from attendance would be a guess).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .memo_parse import MemoMotion, ParsedMemo, carried

#: Vote-eligible motion kinds -> the word used in the vote result string
#: when the motion carried. (A FAILED-tagged or not-carried motion reads
#: "Failed".) 'amend' is vote-eligible but never dispositive — it can only
#: appear here because outcomes come from item.disposition, not this map.
_CARRIED_WORDS = {
    "adopt": "Passed", "amend": "Passed",
    "continue": "Continued", "pull": "Pulled",
}

#: The bare number of a legislation ref ("Ordinance 2026-15" -> "2026-15"),
#: mirroring memo_parse._REF_RE's number shape.
_REF_NUMBER_RE = re.compile(r"(\d{4}-\d{1,3})\s*$")


def _ref_number(ref: Optional[str]) -> Optional[str]:
    match = _REF_NUMBER_RE.search(ref or "")
    return match.group(1) if match else None


@dataclass
class AgendaItemRow:
    """Snapshot of a meetings.agenda_items row (id as str)."""
    id: str
    position: int
    legislation_ref: Optional[str]
    outcome: Optional[str]


@dataclass
class SpeakerRow:
    """Snapshot of a meetings.speakers row (id as str)."""
    id: str
    display_name: str


@dataclass
class PlannedVote:
    resolution: str            # legislation ref
    description: str           # the motion sentence, verbatim (trimmed)
    result: str                # "Passed 8–0" style (NOT NULL in the DB)
    agenda_item_id: Optional[str]
    records: list[tuple[str, str]] = field(default_factory=list)  # (speaker_id, position)


@dataclass
class ReconcilePlan:
    outcome_updates: list[tuple[str, str]] = field(default_factory=list)  # (outcome, item_id)
    votes: list[PlannedVote] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def diff_plan_against_db(
    plan: ReconcilePlan,
    agenda_items: list[AgendaItemRow],
    existing_votes: list[tuple[str, str, int]],  # (resolution, result, record_count)
) -> list[str]:
    """Drift between what the memo says and what the DB holds (read-only).

    Returns human-readable drift lines; empty list = DB matches the memo.
    Votes compare as multisets of (resolution, result, record_count);
    outcomes compare each planned update against the snapshot's outcome.
    """
    drift: list[str] = []
    expected = Counter((v.resolution, v.result, len(v.records)) for v in plan.votes)
    actual = Counter(existing_votes)
    for key in sorted((expected - actual).elements()):
        drift.append(f"vote missing from DB: {key[0]} | {key[1]} | {key[2]} record(s)")
    for key in sorted((actual - expected).elements()):
        drift.append(f"unexpected vote in DB: {key[0]} | {key[1]} | {key[2]} record(s)")

    outcome_by_id = {i.id: i.outcome for i in agenda_items}
    ref_by_id = {i.id: i.legislation_ref for i in agenda_items}
    for outcome, item_id in plan.outcome_updates:
        if outcome_by_id.get(item_id) != outcome:
            drift.append(
                f"outcome drift on {ref_by_id.get(item_id) or item_id}: "
                f"memo says {outcome!r}, DB has {outcome_by_id.get(item_id)!r}"
            )
    return drift


def match_speaker(
    member_name: str, speakers: list[SpeakerRow]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a memo last name to exactly one speaker id, by case-insensitive
    last-name-suffix match on display_name ("Asare" -> "Isak Nti Asare").
    Returns (speaker_id, None) or (None, note)."""
    target = member_name.strip().lower()
    hits = [
        s for s in speakers
        if s.display_name
        and (
            s.display_name.lower() == target
            or s.display_name.lower().endswith(" " + target)
        )
    ]
    if len(hits) == 1:
        return hits[0].id, None
    kind = "ambiguous" if hits else "no"
    return None, f"{kind} speaker match for memo member {member_name!r} — record skipped"


def _result_string(motion: MemoMotion, notes: list[str]) -> str:
    """"Passed 8–0" style. A motion that neither carried nor bears the
    clerk's FAILED tag gets a bare tally — the roll call is fact, its
    verdict is not ours to call (abstain-don't-guess)."""
    t = motion.tally
    if carried(motion):
        word = _CARRIED_WORDS[motion.kind]
    elif motion.failed_tag or t.ayes < t.nays:
        word = "Failed"
    else:
        word = ""
        notes.append(
            f"motion tallied {t.ayes}–{t.nays} without a FAILED tag — "
            "recording bare tally, no verdict"
        )
    result = f"{word} {t.ayes}–{t.nays}".strip()
    if t.abstain:
        result += f", {t.abstain} abstaining"
    return result


def _planned_records(
    motion: MemoMotion, speakers: list[SpeakerRow], notes: list[str]
) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for names, position in (
        (motion.ayes_names, "aye"),
        (motion.nays_names, "nay"),
        (motion.abstain_names, "abstain"),
    ):
        for name in names:
            speaker_id, note = match_speaker(name, speakers)
            if speaker_id is None:
                notes.append(note)
            else:
                records.append((speaker_id, position))
    return records


def _number_fallback(
    memo_ref: str,
    memo_number_counts: Counter,
    agenda_items: list[AgendaItemRow],
) -> tuple[Optional[AgendaItemRow], str]:
    """Bare-number match for a memo ref with no exact agenda match (clerk
    ref-type mislabels: "Resolution 2026-15" for Ordinance 2026-15).
    Fires ONLY when the number is unique across both the memo item refs and
    the agenda item refs — anything less is refused (abstain-don't-guess).
    Returns (matched agenda item or None, loud note)."""
    number = _ref_number(memo_ref)
    # Candidates count agenda ROWS, not distinct refs, so a unique number
    # also proves the matched ref isn't duplicated (the duplicate-refs
    # guard stays airtight); two different-typed rows sharing a number is
    # exactly the ambiguity refused below.
    candidates = [
        i for i in agenda_items
        if i.legislation_ref and _ref_number(i.legislation_ref) == number
    ] if number else []
    if not candidates:
        return None, (
            f"{memo_ref}: no agenda item with this ref — "
            "votes written unattached, no outcome update"
        )
    if memo_number_counts[number] == 1 and len(candidates) == 1:
        hit = candidates[0]
        return hit, (
            f"{memo_ref}: no exact agenda match; matched "
            f"{hit.legislation_ref} by bare number (clerk ref-type mislabel?)"
        )
    crowd = []
    if memo_number_counts[number] > 1:
        crowd.append(f"{memo_number_counts[number]} memo refs")
    if len(candidates) > 1:
        crowd.append(f"{len(candidates)} agenda refs")
    return None, (
        f"{memo_ref}: no exact agenda match and bare number {number} is "
        f"not unique ({' and '.join(crowd)} share it) — refusing to guess, "
        "votes written unattached, no outcome update"
    )


def build_reconcile_plan(
    memo: ParsedMemo,
    agenda_items: list[AgendaItemRow],
    speakers: list[SpeakerRow],
) -> ReconcilePlan:
    plan = ReconcilePlan(notes=list(memo.notes))

    ref_counts = Counter(i.legislation_ref for i in agenda_items if i.legislation_ref)
    duplicate_refs = {ref for ref, n in ref_counts.items() if n > 1}
    for ref in sorted(duplicate_refs):
        plan.notes.append(
            f"{ref}: {ref_counts[ref]} agenda items share this ref — ambiguous, "
            "votes written unattached, no outcome update"
        )
    by_ref = {
        i.legislation_ref: i
        for i in agenda_items
        if i.legislation_ref and i.legislation_ref not in duplicate_refs
    }
    memo_number_counts = Counter(
        _ref_number(item.legislation_ref) for item in memo.items
    )

    for item in memo.items:
        plan.notes.extend(f"{item.legislation_ref}: {n}" for n in item.notes)
        agenda_item = by_ref.get(item.legislation_ref)
        if agenda_item is None:
            # Guarded bare-number fallback; the note is loud either way
            # (fallback fired, refused as ambiguous, or nothing to match).
            agenda_item, note = _number_fallback(
                item.legislation_ref, memo_number_counts, agenda_items
            )
            plan.notes.append(note)
        if item.disposition is not None and agenda_item is not None:
            if agenda_item.outcome and agenda_item.outcome != item.disposition:
                plan.notes.append(
                    f"{item.legislation_ref}: overwriting outcome "
                    f"{agenda_item.outcome!r} -> {item.disposition!r} (memo authoritative)"
                )
            plan.outcome_updates.append((item.disposition, agenda_item.id))

        if item.disposition == "continued":
            disposition_motion = (
                item.motions[item.disposition_motion]
                if item.disposition_motion is not None else None
            )
            if disposition_motion is not None and disposition_motion.continued_to_date:
                plan.notes.append(
                    f"{item.legislation_ref}: continued to "
                    f"{disposition_motion.continued_to_date}"
                )

        for motion in item.motions:
            if motion.kind not in _CARRIED_WORDS or motion.tally is None:
                continue  # procedural/unknown or moved-but-unvoted
            plan.votes.append(PlannedVote(
                resolution=item.legislation_ref,
                description=motion.raw_text,
                result=_result_string(motion, plan.notes),
                agenda_item_id=agenda_item.id if agenda_item else None,
                records=_planned_records(motion, speakers, plan.notes),
            ))
    return plan
