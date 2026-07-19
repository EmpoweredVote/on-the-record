"""Parse roll-call votes from a legislative granule's text (Slice 1, Federal adapter).

MODS carries per-member votes but FLAT and ungrouped across every roll call in the
granule (no roll-number key), so it cannot reconstruct per-vote tallies. The text
IS authoritative: each vote is a '[Roll No. NNN]' block with tally headers
(AYES/YEAS, NOES/NAYS, ANSWERED ``PRESENT'', NOT VOTING) each followed by a list of
member surnames, preceded by the 'question is on the amendment offered by ...'
context. Real CREC interleaves page markers ([[Page ...]]), {time} stamps, and
vote-change prose ("Messrs. X changed their vote", "So the amendment was agreed
to.") around the name lists, so surnames are whitelisted (not collected until a
terminator) and the ``PRESENT'' header is matched tolerant of CREC's ``'' quotes.
Pure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_ROLL_RE = re.compile(r"\[Roll No\.?\s*(\d+)\]")
# A tally header: an ALL-CAPS label (may include the ``'' / quote glyphs CREC wraps
# PRESENT in) ending in '--<count>'. Classified by keyword in _position_of.
_TALLY_RE = re.compile(r"^\s*([A-Z][A-Z '`\"“”]*?)\s*--\s*\d+\s*$")

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
    timestamp: Optional[float] = None               # transcript-relative time of the result announcement (Slice 2)
    outcome: Optional[str] = None                   # display phrase, e.g. "Agreed to"/"Rejected"/"Not agreed to"
    passed: Optional[bool] = None                   # normalized pass/fail; None when no outcome line parses


def _position_of(label: str) -> Optional[str]:
    u = label.upper()
    if "NOT VOTING" in u:
        return "NOT_VOTING"
    if "PRESENT" in u:            # ANSWERED ``PRESENT''
        return "PRESENT"
    if u.startswith("AYES") or u.startswith("YEAS"):
        return "YEA"
    if u.startswith("NOES") or u.startswith("NAYS"):
        return "NAY"
    return None


def _is_name_line(s: str) -> bool:
    """A voter surname line: short, no digits/braces/backticks, every token
    capitalized. Rejects CREC's interleaved prose, {time} stamps, [[Page]] markers,
    and ``aye''->``no'' vote-change lines while keeping 'Higgins (LA)',
    'Boyle, Brendan F.', 'Blunt Rochester'."""
    if not s or len(s) > 45 or any(c in s for c in "{}`0123456789"):
        return False
    for tok in s.replace(",", " ").split():
        t = tok.strip("().")
        if t and not t[0].isupper():
            return False
    return True


def _question_before(text: str, idx: int) -> str:
    pre = text[max(0, idx - 800):idx]
    hits = list(_QUESTION_RE.finditer(pre))
    return hits[-1].group(1).strip().replace("\n", " ") if hits else ""


# CREC announces each roll's outcome as "So the <subject> was/were [not] <verb>."
# The subject varies (amendment/bill/motion/resolution/…) so we anchor on the verb.
# "So (two-thirds …) the rules were suspended and the bill was passed." → final verb.
_OUTCOME_RE = re.compile(
    r"\b(?:was|were)\s+(not\s+)?"
    r"(agreed to|rejected|passed|adopted|confirmed|ordered|sustained|failed|lost)\b",
    re.I)
_PASS_VERBS = {"agreed to", "passed", "adopted", "confirmed", "ordered", "sustained"}
_FAIL_VERBS = {"rejected", "failed", "lost"}


def _outcome_of(block: str):
    """(display_phrase, passed) from a roll block, or (None, None). Prefers the
    canonical line beginning 'So '; among matches on that line, the last verb wins
    (handles 'the rules were suspended and the bill was passed')."""
    best = None  # (is_so_line, match) — prefer 'So …' lines; otherwise latest match wins
    for line in block.splitlines():
        s = line.strip()
        is_so = s.startswith("So ")
        for m in _OUTCOME_RE.finditer(s):
            if best is None or is_so >= best[0]:  # never let a non-'So' line override a 'So' line
                best = (is_so, m)
    if best is None:
        return None, None
    negated = bool(best[1].group(1))
    verb = best[1].group(2).lower()
    passed = (verb in _PASS_VERBS) != negated  # XOR: negation flips pass/fail
    phrase = ("not " + verb) if negated else verb
    return phrase[0].upper() + phrase[1:], passed


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
            if tm and _position_of(tm.group(1)):
                current = _position_of(tm.group(1))
                positions.setdefault(current, [])
                continue
            stripped = line.strip()
            if stripped.lower().startswith("the result"):  # end of this tally
                current = None
                continue
            if current and _is_name_line(stripped):
                positions[current].append(stripped)
        outcome, passed = _outcome_of(block)
        votes.append(RollCallVote(
            roll, _question_before(text, start), positions,
            outcome=outcome, passed=passed))
    return votes
