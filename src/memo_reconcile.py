"""Plan DB writes from a parsed clerk memorandum (pure; no cursors).

The memo is authoritative for outcomes and votes: dispositions OVERWRITE
agenda_items.outcome (this is the fix for the pass-abstention limitation —
the chair never says "motion carries", so the LLM pass abstains on passes),
and every dispositive motion with a recorded roll call becomes a
meetings.votes row. Named split votes additionally plan per-member
meetings.vote_records rows.

vote_records.speaker_id is NOT NULL and speakers are diarization-owned, so
a memo name with no (or an ambiguous) speaker match SKIPS that record with
a loud note — we never fabricate speaker rows. Unnamed unanimous tallies
plan no records (deriving members from attendance would be a guess).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .memo_parse import MemoMotion, ParsedMemo

#: Dispositive motion kinds -> the word used in the vote result string when
#: the motion carried. (A FAILED-tagged or not-carried motion reads "Failed".)
_CARRIED_WORDS = {"adopt": "Passed", "continue": "Continued", "pull": "Pulled"}


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


def _result_string(motion: MemoMotion) -> str:
    t = motion.tally
    carried = not motion.failed_tag and t.ayes > t.nays
    word = _CARRIED_WORDS[motion.kind] if carried else "Failed"
    result = f"{word} {t.ayes}–{t.nays}"
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


def build_reconcile_plan(
    memo: ParsedMemo,
    agenda_items: list[AgendaItemRow],
    speakers: list[SpeakerRow],
) -> ReconcilePlan:
    plan = ReconcilePlan(notes=list(memo.notes))
    by_ref = {i.legislation_ref: i for i in agenda_items if i.legislation_ref}

    for item in memo.items:
        plan.notes.extend(f"{item.legislation_ref}: {n}" for n in item.notes)
        agenda_item = by_ref.get(item.legislation_ref)
        if agenda_item is None:
            plan.notes.append(
                f"{item.legislation_ref}: no agenda item with this ref — "
                "votes written unattached, no outcome update"
            )
        if item.disposition is not None and agenda_item is not None:
            if agenda_item.outcome and agenda_item.outcome != item.disposition:
                plan.notes.append(
                    f"{item.legislation_ref}: overwriting outcome "
                    f"{agenda_item.outcome!r} -> {item.disposition!r} (memo authoritative)"
                )
            plan.outcome_updates.append((item.disposition, agenda_item.id))

        for motion in item.motions:
            if motion.kind not in _CARRIED_WORDS or motion.tally is None:
                continue  # procedural/unknown or moved-but-unvoted
            plan.votes.append(PlannedVote(
                resolution=item.legislation_ref,
                description=motion.raw_text,
                result=_result_string(motion),
                agenda_item_id=agenda_item.id if agenda_item else None,
                records=_planned_records(motion, speakers, plan.notes),
            ))
    return plan
