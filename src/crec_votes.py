"""Parse roll-call votes from a legislative granule's text (Slice 1, Federal adapter).

MODS carries per-member votes but FLAT and ungrouped across every roll call in the
granule (no roll-number key), so it cannot reconstruct per-vote tallies. The text
IS authoritative: each vote is a '[Roll No. NNN]' block with 'AYES--n / NOES--n /
NOT VOTING--n / ANSWERED "PRESENT"--n' headers followed by member surname lists,
preceded by the 'question is on the amendment offered by ...' context. Pure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_ROLL_RE = re.compile(r"\[Roll No\.?\s*(\d+)\]")
_TALLY_RE = re.compile(
    r'^\s*(AYES|YEAS|NOES|NAYS|NOT VOTING|ANSWERED\s+["“]PRESENT["”])\s*--\s*\d+\s*$')
_POSITION = {"AYES": "YEA", "YEAS": "YEA", "NOES": "NAY", "NAYS": "NAY",
             "NOT VOTING": "NOT_VOTING"}
# Title abbreviations (e.g. "(Mr. Smith)") contain a period that is NOT a sentence
# boundary; the tempered-greedy-token alternation below consumes a whole
# "Mr."-style abbreviation as one unit so the final `\.` finds the real sentence
# end instead of stopping mid-abbreviation.
_ABBR = r"(?:Mr|Mrs|Ms|Dr|Rep|Sen|St|No|Jr|Sr)"
_QUESTION_RE = re.compile(
    r"(The question is on(?:\b" + _ABBR + r"\.|[^.])*\."
    r"|recorded vote on the amendment offered by(?:\b" + _ABBR + r"\.|[^.])*\.)",
    re.S)


@dataclass
class RollCallVote:
    roll_number: int
    question: str
    positions: dict = field(default_factory=dict)  # "YEA"/"NAY"/"PRESENT"/"NOT_VOTING" -> [surname]


def _position_of(header: str) -> str:
    h = header.strip().upper()
    if h.startswith("ANSWERED"):
        return "PRESENT"
    return _POSITION[h]


def _question_before(text: str, idx: int) -> str:
    pre = text[max(0, idx - 800):idx]
    hits = list(_QUESTION_RE.finditer(pre))
    return hits[-1].group(1).strip().replace("\n", " ") if hits else ""


def parse_votes(text: str) -> list[RollCallVote]:
    marks = [(m.start(), int(m.group(1))) for m in _ROLL_RE.finditer(text)]
    votes: list[RollCallVote] = []
    for i, (start, roll) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        block = text[start:end]
        positions: dict = {}
        current: Optional[str] = None
        for line in block.splitlines():
            tm = _TALLY_RE.match(line)
            if tm:
                current = _position_of(tm.group(1))
                positions[current] = []
                continue
            stripped = line.strip()
            if current and stripped:
                if stripped.lower().startswith("the "):  # prose ends the name list
                    current = None
                    continue
                positions[current].append(stripped)
        votes.append(RollCallVote(roll, _question_before(text, start), positions))
    return votes
