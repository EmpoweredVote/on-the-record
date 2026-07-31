"""Deterministic parser for the clerk's post-meeting Memorandum.

The memo is highly templated prose (verified on the July 22, June 10, and
July 29, 2026 fixtures): numbered sections with wall-clock stamps
("7. Legislation ... [7:01pm]"), ref-titled subsections ("7.2. Resolution
2026-13"), and motion sentences ("X moved, and Y seconded that <ref> be
<action>. The motion received a roll call vote of Ayes: N, Nays: N,
Abstain: N." — split votes name the members per side and append
FAILED/PASSED).

Rules calibrated on those fixtures:

- Motions attribute to the ENCLOSING SUBSECTION, never to refs inside
  motion prose — the July 22 memo itself has a clerk typo ("The motion to
  discuss Ordinance 2026-13" inside the 2026-15 subsection) that would
  misattribute under ref-scanning.
- ALL result sentences ("The motion[ <desc>] received a roll call vote
  ...") in a motion's block are collected; each is associated to a motion
  in the ITEM scope by classifying its own description clause, never by
  refs inside it (June 10's amendment blocks carry both the amendment vote
  and the deferred as-amended adoption vote in one block; the July 22
  clerk-typo desc stays with its block's discuss motion). An unmatchable
  description drops the result with a loud note; a result whose target
  already holds a tally is dropped, loudly, rather than overwritten.
- Kind "amend" ("to adopt Amendment NN to <ref>") gets a vote row but is
  NEVER dispositive; "amend the agenda ..." is procedural housekeeping.
- "for second reading" clauses (first-reading referral to the next
  session) are continuances; the continued-to date follows "until" or
  "to be held on".
- Names/count guard: the clerk annotates quorum changes as a parenthetical
  on a zero side ("Abstain: 0 (Rosenbarger, Ruff out of the room)"). When
  a side's name-list length differs from its count, the names are dropped
  (tally stands) with a note.
- Disposition = the LAST motion in the item's scope that has a recorded
  roll-call vote and either carried, or — for an adoption motion — was
  tagged FAILED (a failed adoption IS the disposition). A moved-but-unvoted
  motion is a non-event (Res 2026-12's adoption motion was superseded by
  the table motion).
- The "Actions on Legislation:" history block never matches the motion
  grammar (no "roll call vote" phrase, precedes the first motion), so
  prior-meeting actions are naturally excluded.
- Abstain-don't-guess: an unrecognized action clause is kind "unknown" and
  can never be dispositive; an adoption vote that neither carried nor bears
  a FAILED tag yields no disposition. Both leave loud notes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .agenda_align import OUTCOME_VOCABULARY  # single source of truth

_SECTION_RE = re.compile(r"^(\d+)\.\s+(.+?)\s*(?:\[(\d{1,2}:\d{2}\s*[ap]m)\])?\s*$")
_SUBSECTION_RE = re.compile(r"^(\d+)\.(\d+)\.\s*(.*)$")
_REF_RE = re.compile(
    r"\b((?:Appropriation\s+)?(?:Ordinance|Resolution))\s+(\d{4}-\d{1,3})\b"
)
_FOOTER_RE = re.compile(r"^Memorandum prepared by", re.IGNORECASE)

_NAME = r"[A-Z][\w'’.-]+(?:\s[A-Z][\w'’.-]+)?"
_MOTION_START_RE = re.compile(
    rf"(?P<mover>{_NAME})\s+moved,?\s+and\s+(?P<seconder>{_NAME})\s+seconded\s+(?:that|to)\b"
)
# A result sentence carries its own description clause between "The motion"
# and "received" ("to adopt Amendment 01 to ...", or empty). Descriptions
# never contain a period, which bounds the non-greedy capture.
_RESULT_RE = re.compile(
    r"The\s+motion\b(?P<desc>[^.]*?)\s*received\s+a\s+"
    r"roll\s+call\s+vote(?:\s+of|:)?\s*"
    r"Ayes:\s*(?P<ayes>\d+)\s*(?:\((?P<ayes_names>[^)]*)\))?\s*[;,.]?\s*"
    r"Nays:\s*(?P<nays>\d+)\s*(?:\((?P<nays_names>[^)]*)\))?\s*[;,.]?\s*"
    r"Abstain:\s*(?P<abstain>\d+)\s*(?:\((?P<abstain_names>[^)]*)\))?\s*\.?"
    r"\s*(?P<tag>FAILED|PASSED)?",
    re.IGNORECASE,
)
# "until <date>" or "... to be held on <date>"; July 22's postpone contains
# both anchors ("until the next Regular Session to be held on July 29,
# 2026") — the leftmost anchor and the non-greedy hop to the FIRST date
# keep that yielding July 29.
_CONTINUED_DATE_RE = re.compile(
    r"(?:until|to\s+be\s+held\s+on)\b.*?\b([A-Z][a-z]+ \d{1,2}, \d{4})"
)


@dataclass
class MemoTally:
    ayes: int
    nays: int
    abstain: int


@dataclass
class MemoMotion:
    mover: str
    seconder: str
    # 'procedural' | 'adopt' | 'amend' | 'continue' | 'pull' | 'unknown'
    # — 'amend' (adopt an Amendment NN to the legislation) gets a vote row
    # but is never dispositive for the item itself.
    kind: str
    raw_text: str
    tally: Optional[MemoTally] = None
    ayes_names: list[str] = field(default_factory=list)
    nays_names: list[str] = field(default_factory=list)
    abstain_names: list[str] = field(default_factory=list)
    failed_tag: bool = False   # trailing FAILED tag on the roll call
    passed_tag: bool = False   # trailing PASSED tag
    continued_to_date: Optional[str] = None  # ISO date from "until"/"to be held on"


@dataclass
class MemoItem:
    legislation_ref: str
    section_wallclock: Optional[str]  # e.g. "7:01pm", from the enclosing section
    motions: list[MemoMotion] = field(default_factory=list)
    disposition: Optional[str] = None  # OUTCOME_VOCABULARY or None
    disposition_motion: Optional[int] = None  # index into motions
    notes: list[str] = field(default_factory=list)


@dataclass
class ParsedMemo:
    items: list[MemoItem]
    notes: list[str] = field(default_factory=list)


def _split_names(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [n.strip() for n in raw.split(",") if n.strip()]


def _classify_action(clause: str) -> str:
    lowered = clause.lower()
    # "amend the agenda ..." is meeting housekeeping, not an amendment to
    # the legislation — must outrank the Amendment check.
    if "amend the agenda" in lowered:
        return "procedural"
    if "read by title" in lowered or "be introduced" in lowered:
        return "procedural"
    if " discuss " in f" {lowered} ":
        return "procedural"
    # "Amendment NN" is capital-A in the memo but keep this case-blind;
    # \bamendment\b never matches the "as amended" adoption phrasing.
    if re.search(r"\bamendment\b", lowered):
        return "amend"
    if "be adopted" in lowered or "be approved" in lowered:
        return "adopt"
    # First-reading referral to the next session is a continuance.
    if "for second reading" in lowered:
        return "continue"
    if "postpone" in lowered or re.search(r"\btabled?\b", lowered):
        return "continue"
    if "withdraw" in lowered:
        return "pull"
    return "unknown"


def _classify_result_desc(desc: str) -> str:
    """Classify a result sentence's own description clause. Descs use the
    infinitive ("The motion to adopt X received ...") where motion clauses
    use the passive ("that X be adopted"), so fall back on the bare verbs."""
    kind = _classify_action(desc)
    if kind != "unknown":
        return kind
    lowered = desc.lower()
    if re.search(r"\badopt\b|\bapprove\b", lowered):
        return "adopt"
    if re.search(r"\bintroduce\b", lowered):
        return "procedural"
    return "unknown"


def _associate_result(
    desc: str, owner: MemoMotion, motions: list[MemoMotion]
) -> Optional[MemoMotion]:
    """Pick the motion a result sentence belongs to — by classifying its
    description, NEVER by refs inside it (clerk typos put wrong refs there).
    Returns None when no target can be defended (abstain-don't-guess)."""
    if not desc:
        return owner
    kind = _classify_result_desc(desc)
    if kind == "amend":
        unvoted = [m for m in motions if m.kind == "amend" and m.tally is None]
        return unvoted[-1] if unvoted else None
    # Same kind as the block owner and the owner still unvoted → the owner,
    # whatever ref the desc names (July 22's "to discuss Ordinance 2026-13"
    # typo inside 2026-15's discuss block). "unknown" never equals anything.
    if kind != "unknown" and kind == owner.kind and owner.tally is None:
        return owner
    if kind == "adopt":
        # "to adopt <ref> as amended" lands after the amendment vote, in the
        # amendment's block — it settles the pending unvoted adoption motion.
        unvoted = [m for m in motions if m.kind == "adopt" and m.tally is None]
        return unvoted[-1] if unvoted else None
    return None


def _guarded_names(names: list[str], count: int, side: str, notes: list[str]) -> list[str]:
    # Quorum annotations ride zero sides ("Abstain: 0 (Rosenbarger, Ruff
    # out of the room)") — a name list that disagrees with its count is not
    # a vote record. Keep the tally, drop the names.
    if names and len(names) != count:
        notes.append(
            f"{side} names {names!r} disagree with count ({count}) — names dropped"
        )
        return []
    return names


def _apply_result(motion: MemoMotion, roll: re.Match, notes: list[str]) -> None:
    motion.tally = MemoTally(
        ayes=int(roll.group("ayes")),
        nays=int(roll.group("nays")),
        abstain=int(roll.group("abstain")),
    )
    for side in ("ayes", "nays", "abstain"):
        names = _guarded_names(
            _split_names(roll.group(f"{side}_names")),
            int(roll.group(side)), side, notes,
        )
        setattr(motion, f"{side}_names", names)
    tag = (roll.group("tag") or "").upper()
    motion.failed_tag = tag == "FAILED"
    motion.passed_tag = tag == "PASSED"


def _parse_motions(scope_text: str, notes: list[str]) -> list[MemoMotion]:
    starts = list(_MOTION_START_RE.finditer(scope_text))
    motions: list[MemoMotion] = []
    blocks: list[str] = []
    for idx, start in enumerate(starts):
        block_end = starts[idx + 1].start() if idx + 1 < len(starts) else len(scope_text)
        block = scope_text[start.start():block_end]
        # The action clause runs from "seconded that/to" to the result
        # sentence ("The motion ...") or the block end.
        clause_start = start.end() - start.start()
        # The literal "The motion" anchor is template-calibrated; if the
        # clerk's wording drifts, the clause simply absorbs the result
        # sentence too (harmless for classification today).
        cut = block.find("The motion")
        clause = block[clause_start:cut if cut != -1 else len(block)].strip(" .")
        kind = _classify_action(clause)
        motion = MemoMotion(
            mover=start.group("mover"),
            seconder=start.group("seconder"),
            kind=kind,
            raw_text=" ".join(block.split()),
        )
        if kind == "unknown":
            notes.append(f"unrecognized motion action (abstained): {clause[:120]!r}")
        if kind == "continue":
            date_match = _CONTINUED_DATE_RE.search(clause)
            if date_match:
                try:
                    motion.continued_to_date = (
                        datetime.strptime(date_match.group(1), "%B %d, %Y")
                        .date().isoformat()
                    )
                except ValueError:
                    notes.append(f"unparseable continuance date: {date_match.group(1)!r}")
        motions.append(motion)
        blocks.append(block)

    # Second pass: a block may hold several result sentences (June 10's
    # amendment blocks hold the amendment vote AND the as-amended adoption
    # vote). Results are matched in scope order so earlier ones consume
    # their targets before later ones look for pending unvoted motions.
    for owner, block in zip(motions, blocks):
        for roll in _RESULT_RE.finditer(block):
            desc = roll.group("desc").strip()
            target = _associate_result(desc, owner, motions)
            if target is None:
                notes.append(
                    f"result sentence matches no pending motion (dropped): {desc[:120]!r}"
                )
                continue
            if target.tally is not None:
                notes.append(
                    f"result sentence would overwrite a recorded vote (dropped): "
                    f"{desc[:120]!r}"
                )
                continue
            _apply_result(target, roll, notes)
    return motions


def carried(motion: MemoMotion) -> bool:
    return (
        motion.tally is not None
        and not motion.failed_tag
        and motion.tally.ayes > motion.tally.nays
    )


def _disposition(motions: list[MemoMotion], notes: list[str]) -> tuple[Optional[str], Optional[int]]:
    """Last motion with a recorded roll call that carried (or, for adoption,
    was tagged FAILED — a failed adoption IS the item's disposition)."""
    result: tuple[Optional[str], Optional[int]] = (None, None)
    for i, m in enumerate(motions):
        # 'amend' settles an amendment, never the item — non-dispositive.
        if m.tally is None or m.kind in ("procedural", "unknown", "amend"):
            continue
        if m.kind == "adopt":
            if m.failed_tag:
                result = ("failed", i)
            elif carried(m):
                result = ("passed", i)
            else:
                notes.append(
                    f"adoption vote neither carried nor tagged FAILED "
                    f"(Ayes {m.tally.ayes}, Nays {m.tally.nays}) — abstaining"
                )
        elif m.kind == "continue":
            if carried(m):
                result = ("continued", i)
            else:
                notes.append(
                    f"continuance motion did not carry "
                    f"(Ayes {m.tally.ayes}, Nays {m.tally.nays}) — abstaining"
                )
        elif m.kind == "pull":
            if carried(m):
                result = ("pulled", i)
            else:
                notes.append(
                    f"withdrawal motion did not carry "
                    f"(Ayes {m.tally.ayes}, Nays {m.tally.nays}) — abstaining"
                )
    assert result[0] is None or result[0] in OUTCOME_VOCABULARY
    return result


def parse_memo(text: str) -> ParsedMemo:
    """Parse memorandum text (from pdf_text.extract_text) into items with
    motions and dispositions. Pure; never raises on template drift — it
    just finds fewer items/motions and leaves notes."""
    items: list[MemoItem] = []
    memo_notes: list[str] = []
    current_wallclock: Optional[str] = None
    current_item: Optional[MemoItem] = None
    buffer: list[str] = []

    def close_item() -> None:
        nonlocal current_item, buffer
        if current_item is not None:
            scope_text = " ".join(" ".join(buffer).split())
            current_item.motions = _parse_motions(scope_text, current_item.notes)
            current_item.disposition, current_item.disposition_motion = _disposition(
                current_item.motions, current_item.notes
            )
            items.append(current_item)
        current_item = None
        buffer = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _FOOTER_RE.match(line):
            close_item()
            break
        sub = _SUBSECTION_RE.match(line)
        if sub:
            close_item()
            ref_match = _REF_RE.search(sub.group(3))
            if ref_match:
                kind = re.sub(r"\s+", " ", ref_match.group(1))
                current_item = MemoItem(
                    legislation_ref=f"{kind} {ref_match.group(2)}",
                    section_wallclock=current_wallclock,
                )
            continue
        section = _SECTION_RE.match(line)
        if section:
            close_item()
            if section.group(3):
                current_wallclock = section.group(3).replace(" ", "")
            continue
        if current_item is not None:
            buffer.append(line)
    close_item()

    if not items:
        memo_notes.append("no legislation items found — template drift?")
    return ParsedMemo(items=items, notes=memo_notes)
