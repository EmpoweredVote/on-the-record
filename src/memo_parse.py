"""Deterministic parser for the clerk's post-meeting Memorandum.

The memo is highly templated prose (verified on the July 22, 2026 fixture):
numbered sections with wall-clock stamps ("7. Legislation ... [7:01pm]"),
ref-titled subsections ("7.2. Resolution 2026-13"), and motion sentences
("X moved, and Y seconded that <ref> be <action>. The motion received a
roll call vote of Ayes: N, Nays: N, Abstain: N." — split votes name the
members per side and append FAILED/PASSED).

Rules calibrated on that fixture:

- Motions attribute to the ENCLOSING SUBSECTION, never to refs inside
  motion prose — the July 22 memo itself has a clerk typo ("The motion to
  discuss Ordinance 2026-13" inside the 2026-15 subsection) that would
  misattribute under ref-scanning.
- Disposition = the LAST motion in the item's scope that has a recorded
  roll-call vote and carried. A moved-but-unvoted motion is a non-event
  (Res 2026-12's adoption motion was superseded by the table motion).
- The "Actions on Legislation:" history block never matches the motion
  grammar, so prior-meeting actions are naturally excluded.
- Abstain-don't-guess: an unrecognized action clause is kind "unknown" and
  can never be dispositive; an adoption vote that neither carried nor bears
  a FAILED tag yields no disposition. Both leave loud notes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

#: Outcome vocabulary shared with agenda_align / legislation_oracle.
OUTCOME_VOCABULARY = ("passed", "failed", "continued", "pulled")

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
_ROLL_CALL_RE = re.compile(
    r"roll\s+call\s+vote(?:\s+of|:)?\s*"
    r"Ayes:\s*(?P<ayes>\d+)\s*(?:\((?P<ayes_names>[^)]*)\))?\s*[;,.]?\s*"
    r"Nays:\s*(?P<nays>\d+)\s*(?:\((?P<nays_names>[^)]*)\))?\s*[;,.]?\s*"
    r"Abstain:\s*(?P<abstain>\d+)\s*(?:\((?P<abstain_names>[^)]*)\))?\s*\.?"
    r"\s*(?P<tag>FAILED|PASSED)?",
    re.IGNORECASE,
)
_CONTINUED_DATE_RE = re.compile(r"until\b.*?\b([A-Z][a-z]+ \d{1,2}, \d{4})")


@dataclass
class MemoTally:
    ayes: int
    nays: int
    abstain: int


@dataclass
class MemoMotion:
    mover: str
    seconder: str
    kind: str  # 'procedural' | 'adopt' | 'continue' | 'pull' | 'unknown'
    raw_text: str
    tally: Optional[MemoTally] = None
    ayes_names: list[str] = field(default_factory=list)
    nays_names: list[str] = field(default_factory=list)
    abstain_names: list[str] = field(default_factory=list)
    failed_tag: bool = False   # trailing FAILED tag on the roll call
    passed_tag: bool = False   # trailing PASSED tag
    continued_to_date: Optional[str] = None  # ISO date from "until <date>"


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
    if "read by title" in lowered or "be introduced" in lowered:
        return "procedural"
    if lowered.startswith("discuss ") or " discuss " in f" {lowered} ":
        return "procedural"
    if "be adopted" in lowered or "be approved" in lowered:
        return "adopt"
    if "postpone" in lowered or "table" in lowered:
        return "continue"
    if "withdraw" in lowered:
        return "pull"
    return "unknown"


def _parse_motions(scope_text: str, notes: list[str]) -> list[MemoMotion]:
    starts = list(_MOTION_START_RE.finditer(scope_text))
    motions: list[MemoMotion] = []
    for idx, start in enumerate(starts):
        block_end = starts[idx + 1].start() if idx + 1 < len(starts) else len(scope_text)
        block = scope_text[start.start():block_end]
        # The action clause runs from "seconded that/to" to the result
        # sentence ("The motion ...") or the block end.
        clause_start = start.end() - start.start()
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
        roll = _ROLL_CALL_RE.search(block)
        if roll:
            motion.tally = MemoTally(
                ayes=int(roll.group("ayes")),
                nays=int(roll.group("nays")),
                abstain=int(roll.group("abstain")),
            )
            motion.ayes_names = _split_names(roll.group("ayes_names"))
            motion.nays_names = _split_names(roll.group("nays_names"))
            motion.abstain_names = _split_names(roll.group("abstain_names"))
            tag = (roll.group("tag") or "").upper()
            motion.failed_tag = tag == "FAILED"
            motion.passed_tag = tag == "PASSED"
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
    return motions


def _carried(motion: MemoMotion) -> bool:
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
        if m.tally is None or m.kind in ("procedural", "unknown"):
            continue
        if m.kind == "adopt":
            if m.failed_tag:
                result = ("failed", i)
            elif _carried(m):
                result = ("passed", i)
            else:
                notes.append(
                    f"adoption vote neither carried nor tagged FAILED "
                    f"(Ayes {m.tally.ayes}, Nays {m.tally.nays}) — abstaining"
                )
        elif m.kind == "continue" and _carried(m):
            result = ("continued", i)
        elif m.kind == "pull" and _carried(m):
            result = ("pulled", i)
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
