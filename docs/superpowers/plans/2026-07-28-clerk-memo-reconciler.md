# Clerk Memorandum Reconciler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse the clerk's post-meeting Memorandum (OnBoard file type `Memorandum`) deterministically and reconcile it into the DB: overwrite `agenda_items.outcome` (memo is authoritative over LLM extraction), write `meetings.votes` rows per dispositive motion, and write `meetings.vote_records` per member on named split votes — first writer of that table pipeline-wide.

**Architecture:** Three units per the spec (`docs/superpowers/specs/2026-07-28-clerk-memo-reconciler-design.md`): fetcher (memo properties on `OnBoardMeeting`), pure deterministic parser (`src/memo_parse.py` — motions attribute by memo subsection, disposition = last motion with a recorded roll-call that carried), pure reconcile planner (`src/memo_reconcile.py`) + cursor-bound writer `publish.reconcile_memo` shaped like `align_and_flip`. No LLM anywhere. Abstain-don't-guess: unparseable motion → no outcome change, loud note.

**Tech Stack:** Python (`.venv/bin/python` ONLY — the venv lives at the main repo root `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv`; from the worktree use that absolute path), psycopg2, requests, pdfplumber (existing). Tests: `.venv/bin/pytest`, flat `tests/test_*.py`, injected `fetch`, fixtures committed verbatim, `conftest.py` strips DATABASE_URL. Cursor-bound DB code stays thin and untested per house policy.

**Ground truth (July 22 memo, the committed calibration fixture):**

| ref | disposition | vote row result | records |
|---|---|---|---|
| Ordinance 2026-15 | continued (postpone → 2026-07-29) | Continued 8–0 | none (unnamed tally) |
| Resolution 2026-12 | continued (tabled indefinitely; its adoption motion was moved but never voted) | Continued 8–0 | none |
| Resolution 2026-13 | passed (adopt carried 8–0) | Passed 8–0 | none |
| Ordinance 2026-12 | failed (FAILED tag, 4–4) | Failed 4–4 | 8 named: 4 aye, 4 nay |

Memo traps the parser must survive: the clerk typo "The motion to discuss Ordinance **2026-13** received…" INSIDE the 2026-15 subsection (motions attribute by subsection, never by refs in prose); the `Actions on Legislation:` history block in 7.3 ("Council Action (June 10, 2026): Passed Ayes: 5 (…)") which must yield no motions; both roll-call forms ("roll call vote of Ayes: 8, Nays: 0, Abstain: 0" and "roll call vote: Ayes: 4 (names); Nays: 4 (names); Abstain: 0. FAILED").

---

### Task 1: Branch, fixtures, and OnBoard memo properties

**Files:**
- Create: `tests/fixtures/onboard/memo_2026-07-22.pdf`, `tests/fixtures/onboard/memo_2026-07-22.txt`
- Modify: `src/onboard.py` (three properties on `OnBoardMeeting`)
- Test: extend `tests/test_onboard.py`

- [ ] **Step 1: Branch.** `git branch -m feat/clerk-memo-reconciler` (the worktree branch is fresh off main; rename in place).

- [ ] **Step 2: Capture fixtures.**

```bash
curl -sL -A "Mozilla/5.0" "https://bloomington.in.gov/onboard/meetingFiles/17196/download" -o tests/fixtures/onboard/memo_2026-07-22.pdf
file tests/fixtures/onboard/memo_2026-07-22.pdf   # must say "PDF document"
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -c "
from pathlib import Path
from src.pdf_text import extract_text
Path('tests/fixtures/onboard/memo_2026-07-22.txt').write_text(extract_text(Path('tests/fixtures/onboard/memo_2026-07-22.pdf')))
"
```

Sanity-check the `.txt`: it must contain "Regular Session Memorandum", "[7:01pm]", "FAILED", and "postpone consideration of Ordinance 2026-15".

- [ ] **Step 3: Write failing tests** for the memo properties (append to `tests/test_onboard.py`, matching its existing style):

```python
def _mk_file(file_type, url, created):
    from src.onboard import OnBoardFile
    return OnBoardFile(file_type=file_type, url=url, filename="f.pdf", created=created)


def test_memo_url_picks_latest_created_memorandum():
    from src.onboard import OnBoardMeeting
    m = OnBoardMeeting(onboard_id="1", title="T", start="2026-07-22 18:30", files=[
        _mk_file("Agenda", "https://x/agenda", "2026-07-17"),
        _mk_file("Memorandum", "https://x/memo-old", "2026-07-23"),
        _mk_file("Memorandum", "https://x/memo-new", "2026-07-24"),
    ])
    assert m.memo_url == "https://x/memo-new"
    assert m.memo_created == "2026-07-24"
    assert m.memo_updated_marker == "https://x/memo-new|2026-07-24"


def test_memo_properties_none_when_absent():
    from src.onboard import OnBoardMeeting
    m = OnBoardMeeting(onboard_id="1", title="T", start="2026-07-22 18:30", files=[
        _mk_file("Agenda", "https://x/agenda", "2026-07-17"),
    ])
    assert m.memo_url is None
    assert m.memo_created is None
    assert m.memo_updated_marker == ""
```

- [ ] **Step 4: Run to verify failure.** `.venv/bin/pytest tests/test_onboard.py -k memo -v` → FAIL (`AttributeError: memo_url`).

- [ ] **Step 5: Implement** — add to `OnBoardMeeting` in `src/onboard.py`, directly below `agenda_updated_marker`, reusing `_latest_file` (mirror the agenda properties exactly):

```python
    @property
    def memo_url(self) -> Optional[str]:
        f = self._latest_file("Memorandum")
        return f.url if f else None

    @property
    def memo_created(self) -> Optional[str]:
        f = self._latest_file("Memorandum")
        return f.created if f else None

    @property
    def memo_updated_marker(self) -> str:
        """Change-detection key for the clerk memorandum, same shape as
        agenda_updated_marker."""
        f = self._latest_file("Memorandum")
        if f is None:
            return ""
        return f"{f.url}|{f.updated or f.created or ''}"
```

- [ ] **Step 6: Run tests.** `.venv/bin/pytest tests/test_onboard.py -v` → all PASS.

- [ ] **Step 7: Commit.**

```bash
git add tests/fixtures/onboard/memo_2026-07-22.pdf tests/fixtures/onboard/memo_2026-07-22.txt src/onboard.py tests/test_onboard.py
git commit -m "feat: OnBoard clerk-memorandum file properties + July 22 memo fixture"
```

### Task 2: Deterministic memo parser — `src/memo_parse.py`

**Files:**
- Create: `src/memo_parse.py`
- Test: `tests/test_memo_parse.py`

The parser is pure text → dataclasses. Scope rules: a top-level section header (`7. Legislation for Second Readings and Resolutions [7:01pm]`) carries the wall-clock; a subsection header whose text contains a legislation ref (`7.2. Resolution 2026-13`) opens an item scope; any subsequent section/subsection header (ref or not) or the `Memorandum prepared by:` footer closes it. Motions parse only inside item scopes.

- [ ] **Step 1: Write failing tests** — `tests/test_memo_parse.py` (real-fixture pins first, synthetic edge cases after):

```python
"""Calibration tests for the deterministic clerk-memorandum parser.

Pinned against the REAL July 22, 2026 memo fixture (the first benchmark
memo). Ground truth was hand-checked from the memo text; see the design
spec 2026-07-28-clerk-memo-reconciler-design.md.
"""
from pathlib import Path

import pytest

from src.memo_parse import parse_memo

FIXTURE = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-07-22.txt"


@pytest.fixture(scope="module")
def memo():
    return parse_memo(FIXTURE.read_text())


def _item(memo, ref):
    matches = [i for i in memo.items if i.legislation_ref == ref]
    assert len(matches) == 1, f"expected exactly one item for {ref}"
    return matches[0]


def test_finds_all_four_legislation_items(memo):
    refs = [i.legislation_ref for i in memo.items]
    assert refs == [
        "Ordinance 2026-15", "Resolution 2026-12",
        "Resolution 2026-13", "Ordinance 2026-12",
    ]


def test_ord_2026_15_continued_with_date(memo):
    item = _item(memo, "Ordinance 2026-15")
    assert item.disposition == "continued"
    motion = item.motions[item.disposition_motion]
    assert motion.kind == "continue"
    assert motion.continued_to_date == "2026-07-29"
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (8, 0, 0)


def test_clerk_typo_stays_in_2026_15_scope(memo):
    # "The motion to discuss Ordinance 2026-13 received..." is a clerk typo
    # INSIDE the 2026-15 subsection. Attribution is by subsection, so
    # 2026-15 has all three motions and 2026-13 has none of them.
    item = _item(memo, "Ordinance 2026-15")
    assert len(item.motions) == 3  # introduce, discuss, postpone
    assert [m.kind for m in item.motions] == ["procedural", "procedural", "continue"]


def test_res_2026_12_tabled_unvoted_adoption_is_not_dispositive(memo):
    item = _item(memo, "Resolution 2026-12")
    assert item.disposition == "continued"
    kinds = [m.kind for m in item.motions]
    assert kinds == ["procedural", "adopt", "continue"]
    assert item.motions[1].tally is None            # moved, never voted
    assert item.disposition_motion == 2             # the table motion


def test_res_2026_13_passed(memo):
    item = _item(memo, "Resolution 2026-13")
    assert item.disposition == "passed"
    motion = item.motions[item.disposition_motion]
    assert motion.kind == "adopt"
    assert (motion.tally.ayes, motion.tally.nays) == (8, 0)
    assert motion.ayes_names == []                  # unnamed unanimous tally


def test_ord_2026_12_failed_with_named_sides(memo):
    item = _item(memo, "Ordinance 2026-12")
    assert item.disposition == "failed"
    motion = item.motions[item.disposition_motion]
    assert motion.failed_tag is True
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (4, 4, 0)
    assert motion.ayes_names == ["Asare", "Daily", "Flaherty", "Rosenbarger"]
    assert motion.nays_names == ["Stosberg", "Piedmont-Smith", "Rollo", "Ruff"]
    assert motion.abstain_names == []


def test_action_history_block_yields_no_motions(memo):
    # 7.3's "Actions on Legislation: Council Action (June 10, 2026): Passed
    # Ayes: 5 (...)" is history, not a motion at this meeting.
    item = _item(memo, "Ordinance 2026-12")
    assert len(item.motions) == 2  # introduce + adopt only


def test_section_wallclocks(memo):
    by_ref = {i.legislation_ref: i.section_wallclock for i in memo.items}
    assert by_ref["Ordinance 2026-15"] == "6:54pm"   # First Readings section
    assert by_ref["Resolution 2026-12"] == "7:01pm"  # Second Readings section


# --- synthetic edge cases -------------------------------------------------

def test_unparseable_motion_abstains_with_note():
    text = (
        "5. Legislation for Second Readings and Resolutions [7:00pm]\n"
        "5.1. Ordinance 2026-99\n"
        "Something About Streets\n"
        "Daily moved, and Ruff seconded that Ordinance 2026-99 be frobnicated. "
        "The motion received a roll call vote of Ayes: 8, Nays: 0, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert item.disposition is None
    assert item.motions[0].kind == "unknown"
    assert any("frobnicated" in n or "unknown" in n for n in item.notes)


def test_adopt_tie_without_failed_tag_abstains():
    text = (
        "5. Legislation for Second Readings and Resolutions [7:00pm]\n"
        "5.1. Ordinance 2026-98\n"
        "Daily moved and Ruff seconded that Ordinance 2026-98 be adopted. "
        "The motion received a roll call vote of Ayes: 4, Nays: 4, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert item.disposition is None
    assert any("neither" in n.lower() or "tie" in n.lower() for n in item.notes)


def test_passed_tag_variant_and_withdraw():
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Resolution 2026-97\n"
        "Daily moved and Ruff seconded that Resolution 2026-97 be adopted. "
        "The motion received a roll call vote: Ayes: 5 (A, B, C, D, E); "
        "Nays: 3 (F, G, H); Abstain: 0. PASSED\n"
        "5.2. Resolution 2026-96\n"
        "Daily moved and Ruff seconded to withdraw Resolution 2026-96. "
        "The motion received a roll call vote of Ayes: 8, Nays: 0, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    assert memo.items[0].disposition == "passed"
    assert memo.items[0].motions[0].ayes_names == ["A", "B", "C", "D", "E"]
    assert memo.items[1].disposition == "pulled"
```

- [ ] **Step 2: Run to verify failure.** `.venv/bin/pytest tests/test_memo_parse.py -v` → FAIL (`ModuleNotFoundError: src.memo_parse`).

- [ ] **Step 3: Implement `src/memo_parse.py`:**

```python
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
```

- [ ] **Step 4: Run tests.** `.venv/bin/pytest tests/test_memo_parse.py -v` → all PASS. If a real-fixture pin fails, debug against the actual fixture text (read the relevant lines of `memo_2026-07-22.txt`) — fix the PARSER (or, only if the memo text genuinely differs from the ground-truth table, report it in your task report). Do not weaken a pin to make it pass.

- [ ] **Step 5: Commit.**

```bash
git add src/memo_parse.py tests/test_memo_parse.py
git commit -m "feat: deterministic clerk-memorandum parser (motions, tallies, dispositions)"
```

### Task 3: Reconcile planner — `src/memo_reconcile.py`

**Files:**
- Create: `src/memo_reconcile.py`
- Test: `tests/test_memo_reconcile.py`

Pure planning: parsed memo + DB row snapshots in → outcome updates, planned votes, planned records out. No cursors here.

- [ ] **Step 1: Write failing tests** — `tests/test_memo_reconcile.py`:

```python
"""Reconcile-planner tests: parsed memo + row snapshots -> planned writes.

The end-to-end test runs the REAL July 22 memo fixture through parse_memo
and asserts the full plan against hand-checked ground truth.
"""
from pathlib import Path

from src.memo_parse import parse_memo
from src.memo_reconcile import (
    AgendaItemRow, SpeakerRow, build_reconcile_plan, match_speaker,
)

FIXTURE = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-07-22.txt"

# July 22's real council speakers (display names as published).
SPEAKERS = [
    SpeakerRow("s-asare", "Isak Nti Asare"),
    SpeakerRow("s-daily", "Courtney Daily"),
    SpeakerRow("s-flaherty", "Matt Flaherty"),
    SpeakerRow("s-rosenbarger", "Kate Rosenbarger"),
    SpeakerRow("s-stosberg", "Hopi Stosberg"),
    SpeakerRow("s-piedmont", "Isabel Piedmont-Smith"),
    SpeakerRow("s-rollo", "Dave Rollo"),
    SpeakerRow("s-ruff", "Andy Ruff"),
    SpeakerRow("s-bolden", "Nicole Bolden"),
]


def test_match_speaker_last_name_suffix():
    assert match_speaker("Asare", SPEAKERS)[0] == "s-asare"          # multi-word surname
    assert match_speaker("Piedmont-Smith", SPEAKERS)[0] == "s-piedmont"
    assert match_speaker("piedmont-smith", SPEAKERS)[0] == "s-piedmont"


def test_match_speaker_missing_and_ambiguous():
    sid, note = match_speaker("Zulich", SPEAKERS)   # absent that night
    assert sid is None and "Zulich" in note
    two_smiths = SPEAKERS + [SpeakerRow("s-x", "Jane Piedmont-Smith")]
    sid, note = match_speaker("Piedmont-Smith", two_smiths)
    assert sid is None and "ambiguous" in note


def test_july22_plan_end_to_end():
    memo = parse_memo(FIXTURE.read_text())
    agenda_items = [
        AgendaItemRow("i-15", 10, "Ordinance 2026-15", None),
        AgendaItemRow("i-r12", 11, "Resolution 2026-12", None),
        AgendaItemRow("i-r13", 12, "Resolution 2026-13", None),
        AgendaItemRow("i-o12", 13, "Ordinance 2026-12", "passed"),  # wrong; memo overwrites
        AgendaItemRow("i-noref", 1, None, None),
    ]
    plan = build_reconcile_plan(memo, agenda_items, SPEAKERS)

    assert sorted(plan.outcome_updates) == sorted([
        ("continued", "i-15"), ("continued", "i-r12"),
        ("passed", "i-r13"), ("failed", "i-o12"),
    ])

    assert [(v.resolution, v.result, v.agenda_item_id) for v in plan.votes] == [
        ("Ordinance 2026-15", "Continued 8–0", "i-15"),
        ("Resolution 2026-12", "Continued 8–0", "i-r12"),
        ("Resolution 2026-13", "Passed 8–0", "i-r13"),
        ("Ordinance 2026-12", "Failed 4–4", "i-o12"),
    ]
    for v in plan.votes:
        assert "moved" in v.description and "seconded" in v.description

    records = {v.resolution: v.records for v in plan.votes}
    assert records["Ordinance 2026-15"] == []
    assert records["Resolution 2026-13"] == []
    assert sorted(records["Ordinance 2026-12"]) == sorted([
        ("s-asare", "aye"), ("s-daily", "aye"),
        ("s-flaherty", "aye"), ("s-rosenbarger", "aye"),
        ("s-stosberg", "nay"), ("s-piedmont", "nay"),
        ("s-rollo", "nay"), ("s-ruff", "nay"),
    ])


def test_memo_ref_without_agenda_item_still_votes_no_update():
    memo = parse_memo(FIXTURE.read_text())
    plan = build_reconcile_plan(memo, [], SPEAKERS)   # July 22 reality: no items
    assert plan.outcome_updates == []
    assert len(plan.votes) == 4
    assert all(v.agenda_item_id is None for v in plan.votes)
    assert any("no agenda item" in n for n in plan.notes)


def test_unmatched_member_skipped_with_note():
    memo = parse_memo(FIXTURE.read_text())
    thin = [s for s in SPEAKERS if s.id != "s-ruff"]  # Ruff never spoke
    plan = build_reconcile_plan(memo, [], thin)
    split = [v for v in plan.votes if v.resolution == "Ordinance 2026-12"][0]
    assert len(split.records) == 7
    assert any("Ruff" in n for n in plan.notes)
```

- [ ] **Step 2: Run to verify failure.** `.venv/bin/pytest tests/test_memo_reconcile.py -v` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `src/memo_reconcile.py`:**

```python
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
```

- [ ] **Step 4: Run tests.** `.venv/bin/pytest tests/test_memo_reconcile.py tests/test_memo_parse.py -v` → all PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/memo_reconcile.py tests/test_memo_reconcile.py
git commit -m "feat: memo reconcile planner (outcome overwrites, votes, member records)"
```

### Task 4: DB writer + CLI — `publish.reconcile_memo`, `run_local.py --reconcile-memo`

**Files:**
- Modify: `src/publish.py` (new top-level function after `align_and_flip`, ~line 848)
- Modify: `run_local.py` (argparse near `--align-agenda` ~line 3755; dispatch after the `args.align_agenda` block ~line 4061)

Cursor-bound and thin — no new tests per house policy (all logic already tested in Tasks 2–3).

- [ ] **Step 1: Implement `reconcile_memo` in `src/publish.py`** (place directly after `align_and_flip`):

```python
def reconcile_memo(meeting_id: str) -> dict:
    """Reconcile a meeting's item outcomes and votes from the clerk's
    post-meeting Memorandum (OnBoard file type 'Memorandum').

    ``meeting_id`` is the meeting SLUG. The memo is authoritative: item
    dispositions OVERWRITE agenda_items.outcome, and each dispositive motion
    with a recorded roll call becomes a meetings.votes row ('roll call',
    timestamp NULL) — with per-member meetings.vote_records rows when the
    memo names the sides. Idempotent: this meeting's votes are
    delete-then-inserted (records first — FK). NOTE a later re-publish of
    the meeting wipes these votes via _replace_votes; re-run this after any
    re-publish (see the runbook).
    """
    import requests

    from . import config
    from .bodies import BLOOMINGTON_COMMON_COUNCIL as body  # single body today
    from .memo_parse import parse_memo
    from .memo_reconcile import AgendaItemRow, SpeakerRow, build_reconcile_plan
    from .onboard import fetch_meetings_window
    from .pdf_text import extract_text

    conn = psycopg2.connect(_require_db_url())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, date FROM meetings.meetings WHERE slug = %s",
                    (meeting_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError(f"No meeting with slug {meeting_id!r}.")
                meeting_uuid, meeting_date = row
                cur.execute(
                    """
                    SELECT id, position, legislation_ref, outcome
                    FROM meetings.agenda_items
                    WHERE meeting_id = %s ORDER BY position
                    """,
                    (meeting_uuid,),
                )
                agenda_items = [
                    AgendaItemRow(str(i), p, ref, out)
                    for (i, p, ref, out) in cur.fetchall()
                ]
                cur.execute(
                    "SELECT id, display_name FROM meetings.speakers WHERE meeting_id = %s",
                    (meeting_uuid,),
                )
                speakers = [SpeakerRow(str(i), dn or "") for (i, dn) in cur.fetchall()]

        # Network (OnBoard + PDF) runs outside any transaction. A single-day
        # start==end window returns [] (verified live), so span ±1 day and
        # filter back to the exact date.
        from datetime import timedelta

        day = timedelta(days=1)
        window = fetch_meetings_window(
            (meeting_date - day).isoformat(),
            (meeting_date + day).isoformat(),
            title_prefix=body.meeting_title_prefix,
        )
        matches = [m for m in window if m.start[:10] == meeting_date.isoformat()]
        if not matches:
            raise RuntimeError(
                f"OnBoard has no {body.meeting_title_prefix!r} meeting on "
                f"{meeting_date.isoformat()} — cannot locate a memorandum."
            )
        memo_url = matches[0].memo_url
        if memo_url is None:
            print(f"\n=== Memo reconcile: {meeting_id} ===")
            print("  No Memorandum posted yet (clerk posts within ~a week). "
                  "Re-run when it appears.")
            return {"meeting_id": meeting_id, "memo": None}

        pdf_path = config.DRIVE_ROOT / "agendas" / body.slug / meeting_id / "memo.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(memo_url, timeout=(30, 120),
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        pdf_path.write_bytes(resp.content)

        memo = parse_memo(extract_text(pdf_path))
        plan = build_reconcile_plan(memo, agenda_items, speakers)

        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE meetings.agenda_items
                    SET outcome = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    plan.outcome_updates,
                )
                cur.execute(
                    """
                    DELETE FROM meetings.vote_records
                    WHERE vote_id IN
                      (SELECT id FROM meetings.votes WHERE meeting_id = %s)
                    """,
                    (meeting_uuid,),
                )
                cur.execute(
                    "DELETE FROM meetings.votes WHERE meeting_id = %s",
                    (meeting_uuid,),
                )
                record_count = 0
                for vote in plan.votes:
                    cur.execute(
                        """
                        INSERT INTO meetings.votes
                          (meeting_id, resolution, description, result,
                           vote_type, timestamp, agenda_item_id)
                        VALUES (%s, %s, %s, %s, 'roll call', NULL, %s)
                        RETURNING id
                        """,
                        (meeting_uuid, vote.resolution, vote.description,
                         vote.result, vote.agenda_item_id),
                    )
                    vote_uuid = cur.fetchone()[0]
                    if vote.records:
                        psycopg2.extras.execute_values(
                            cur,
                            """
                            INSERT INTO meetings.vote_records
                              (vote_id, speaker_id, position)
                            VALUES %s
                            """,
                            [(vote_uuid, sid, pos) for (sid, pos) in vote.records],
                        )
                        record_count += len(vote.records)
    finally:
        conn.close()

    print(f"\n=== Memo reconcile: {meeting_id} ===")
    print(f"  {len(memo.items)} memo item(s); {len(plan.outcome_updates)} outcome "
          f"update(s); {len(plan.votes)} vote(s); {record_count} member record(s).")
    for vote in plan.votes:
        attached = "" if vote.agenda_item_id else "  (no agenda item)"
        print(f"  [{vote.resolution}] {vote.result} — "
              f"{len(vote.records)} record(s){attached}")
    for note in plan.notes:
        print(f"  NOTE: {note}")
    print("=" * 40)

    return {
        "meeting_id": meeting_id,
        "memo": memo_url,
        "outcome_updates": len(plan.outcome_updates),
        "votes": len(plan.votes),
        "records": record_count,
        "notes": plan.notes,
    }
```

- [ ] **Step 2: Wire `run_local.py`.** Add the argparse option directly after the `--align-agenda` `add_argument` (~line 3755):

```python
    parser.add_argument("--reconcile-memo", metavar="MEETING_ID",
                        help="Reconcile a published meeting's item outcomes and "
                             "votes from the clerk's post-meeting Memorandum, "
                             "then exit (MEETING_ID is the meeting slug; re-run "
                             "after any re-publish — republishing wipes memo votes)")
```

Add the dispatch directly after the `if args.align_agenda:` block (~line 4066), same shape:

```python
    if args.reconcile_memo:
        from src.publish import reconcile_memo

        reconcile_memo(args.reconcile_memo)
        return
```

- [ ] **Step 3: Full suite + smoke.** `.venv/bin/pytest tests/ -q` → green (conftest strips DATABASE_URL, so nothing here touches the real DB). Then smoke the CLI plumbing without a DB: `DATABASE_URL= .venv/bin/python run_local.py --reconcile-memo nonexistent-slug` → expect the loud `RuntimeError` from `_require_db_url` (proves dispatch reaches `reconcile_memo`).

- [ ] **Step 4: Commit.**

```bash
git add src/publish.py run_local.py
git commit -m "feat: reconcile-memo publish step + run_local --reconcile-memo"
```

### Task 5: Poller hook — `scripts/poll_agendas.py --reconcile-memos`

**Files:**
- Modify: `scripts/poll_agendas.py`

Opt-in lookback pass, marker-keyed via a separate `memo_state.json` `PollState`. Thin script glue over tested code — no new tests (matches the poller's existing pattern).

- [ ] **Step 1: Add args** in `main()` next to the existing ones:

```python
    ap.add_argument("--reconcile-memos", action="store_true",
                    help="also reconcile clerk memoranda for recent past meetings")
    ap.add_argument("--lookback-days", type=int, default=10,
                    help="memo lookback window in days (default 10)")
```

- [ ] **Step 2: Add the lookback function** (module level, after `build_items`):

```python
def reconcile_memos(body, agendas_dir: Path, *, lookback_days: int, dry_run: bool) -> int:
    """Reconcile clerk memoranda for recent past meetings. Change-detects on
    the memo file marker (separate memo_state.json); a meeting whose slug
    isn't in the DB fails loudly WITHOUT recording the marker, so it retries
    daily until the meeting publishes or ages out of the window. Returns the
    failure count."""
    from datetime import timedelta

    from src.publish import reconcile_memo, scheduled_slug

    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    end = date.today().isoformat()
    print(f"Checking {body.slug} memoranda {start} .. {end}")
    meetings = fetch_meetings_window(start, end, title_prefix=body.meeting_title_prefix)
    state = PollState(agendas_dir / "memo_state.json")
    failures = 0
    for m in meetings:
        marker = m.memo_updated_marker
        if not marker:
            continue  # memo not posted yet
        slug = scheduled_slug(body, m.start[:10])
        if state.marker_for(slug) == marker:
            print(f"  MEMO SKIP {slug}: unchanged")
            continue
        if dry_run:
            print(f"  MEMO DRY-RUN {slug}: memo present, would reconcile")
            continue
        try:
            result = reconcile_memo(slug)
        except Exception as exc:
            failures += 1
            print(f"MEMO FAILED {slug}: {exc}", file=sys.stderr)
            continue
        if result.get("memo") is not None:
            state.record(slug, marker)
    return failures
```

- [ ] **Step 3: Call it from `main()`** just before the final `if failures:` block:

```python
    if args.reconcile_memos:
        failures += reconcile_memos(
            body, agendas_dir,
            lookback_days=args.lookback_days, dry_run=args.dry_run,
        )
```

- [ ] **Step 4: Dry-run it for real.** `.venv/bin/python scripts/poll_agendas.py --reconcile-memos --dry-run` → expect `MEMO DRY-RUN bloomington-city-council-2026-07-22: memo present, would reconcile` (July 22's memo exists; its scheduled-slug meeting doesn't — the dry run only proves detection). No DB writes.

Known and accepted: July 22 published under the legacy slug `2026-07-22-bloomington-regular-session`, so a NON-dry poller run would print `MEMO FAILED bloomington-city-council-2026-07-22: No meeting with slug ...` daily until it ages out of the window — the launchd job does not pass `--reconcile-memos` yet, and the runbook (Task 6) notes this. From July 29 onward the scheduled slug is the published slug and the flow just works.

- [ ] **Step 5: Full suite.** `.venv/bin/pytest tests/ -q` → green.

- [ ] **Step 6: Commit.**

```bash
git add scripts/poll_agendas.py
git commit -m "feat: poller --reconcile-memos lookback (marker-keyed, opt-in)"
```

### Task 6: Live E2E on July 22, runbook, PR

**Files:**
- Modify: `docs/runbooks/bloomington-meeting-day.md`

- [ ] **Step 1: Live run (real DB write — deliberate).** Run the worktree's code with the main repo's `DATABASE_URL`:

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/.claude/worktrees/angry-torvalds-c3fe0c
env $(grep -E '^DATABASE_URL=' /Users/chrisandrews/Documents/GitHub/on-the-record/.env.local) \
  /Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python run_local.py \
  --reconcile-memo 2026-07-22-bloomington-regular-session
```

Expected summary: 4 memo items; **0 outcome updates** (July 22 predates the poller — no agenda_items rows, each ref notes "no agenda item"); **4 votes**; **8 member records** on Ordinance 2026-12. If the run errors or the counts differ, STOP and report — do not retry blindly.

- [ ] **Step 2: Verify in the DB** (read-only):

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python - <<'EOF'
import os, psycopg2
for line in open('/Users/chrisandrews/Documents/GitHub/on-the-record/.env.local'):
    if line.startswith('DATABASE_URL='):
        os.environ['DATABASE_URL'] = line.split('=', 1)[1].strip().strip('"').strip("'")
conn = psycopg2.connect(os.environ['DATABASE_URL']); cur = conn.cursor()
cur.execute("""SELECT v.resolution, v.result, v.vote_type, v.timestamp, v.agenda_item_id,
                      count(r.id) AS records
               FROM meetings.votes v
               LEFT JOIN meetings.vote_records r ON r.vote_id = v.id
               WHERE v.meeting_id = '0f8f5333-673f-4ccc-b4bc-306d052084ae'
               GROUP BY v.id ORDER BY v.resolution""")
for row in cur.fetchall(): print(row)
cur.execute("""SELECT s.display_name, r.position
               FROM meetings.vote_records r
               JOIN meetings.speakers s ON s.id = r.speaker_id
               JOIN meetings.votes v ON v.id = r.vote_id
               WHERE v.meeting_id = '0f8f5333-673f-4ccc-b4bc-306d052084ae'
               ORDER BY r.position, s.display_name""")
for row in cur.fetchall(): print(row)
conn.close()
EOF
```

Expected: 4 vote rows (Ordinance 2026-12 → "Failed 4–4" with 8 records; Ordinance 2026-15 → "Continued 8–0", 0 records; Resolution 2026-12 → "Continued 8–0", 0; Resolution 2026-13 → "Passed 8–0", 0; all timestamps NULL, all agenda_item_id NULL) and 8 record rows: aye = Asare, Daily, Flaherty, Rosenbarger; nay = Piedmont-Smith, Rollo, Ruff, Stosberg.

- [ ] **Step 3: Verify the API serves records.** The votes endpoint is `GET /api/meetings/:id/votes` (verified in ev-accounts `backend/src/routes/meetings.ts`; base URL from `web/.env.local.example`):

```bash
curl -s "https://ev-accounts.onrender.com/api/meetings/0f8f5333-673f-4ccc-b4bc-306d052084ae/votes" | /Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -c "
import json, sys
for v in json.load(sys.stdin):
    print(v.get('resolution'), '|', v.get('result'), '|', len(v.get('records', [])), 'records')
"
```

Expected: the 4 votes, with 8 records on Ordinance 2026-12. (If records come back empty despite the DB rows, the deployed API predates records support — note it in the report; the code is merged per the design check, so only deployment lag is possible.)

- [ ] **Step 4: Re-run idempotency check.** Run the Step 1 command again → identical summary, and the Step 2 query still shows exactly 4 votes / 8 records (delete-then-insert, no duplication).

- [ ] **Step 5: Runbook.** In `docs/runbooks/bloomington-meeting-day.md`, append a step after the `--align-agenda` step:

```markdown
6. **When the clerk's Memorandum posts** (OnBoard file type "Memorandum" —
   observed next-day for July 22, allow up to ~a week):

   ```bash
   .venv/bin/python run_local.py --reconcile-memo bloomington-city-council-YYYY-MM-DD
   ```

   Overwrites item outcomes from the memo (authoritative — fixes the
   pass-abstention gap: the chair never says "motion carries"), writes one
   meetings.votes row per dispositive motion ("Passed 8–0" style), and
   per-member vote_records on named split votes. Unparseable motions
   abstain loudly — read the NOTE lines.

   - **Re-run this after any `--publish-meeting` re-publish** — re-publishing
     wipes memo votes (`_replace_votes` delete-then-inserts).
   - Or let the daily poller do it: `poll_agendas.py --reconcile-memos`
     (opt-in flag; not yet on the launchd job — enable after July 22 ages
     out of the lookback window, since its legacy slug fails loudly there).
```

- [ ] **Step 6: Full suite, push, PR.**

```bash
.venv/bin/pytest tests/ -q
git add docs/runbooks/bloomington-meeting-day.md
git commit -m "docs: memo-reconcile step in the Bloomington meeting-day runbook"
git push -u origin feat/clerk-memo-reconciler
gh pr create --title "Clerk memorandum reconciler: authoritative outcomes + first vote_records writer" --body "..."
```

PR body: summarize the three units, the July 22 calibration table (from the spec), the live E2E results (votes/records written + API check), the skip-with-loud-log vote_records policy, and the re-publish-wipes-votes runbook note. End the body with the standard Claude Code attribution line.

**Deferred (explicitly):** attendance extraction; action-history / `continued_from` edges (Phase 4 — `continued_to_date` is parsed and printed, not persisted); vote timestamp derivation from wall-clocks; web display of votes/records (separate task chip ad12978a); guarding `_replace_votes` by vote_type.

---

## Hardening addendum (2026-07-28, user-approved after final review)

**Goal:** Make memo-derived data survive pipeline re-runs and make drift detectable. Four measures: (1) vote ownership partitioned by `vote_type` so re-publish can never wipe memo votes; (2) outcome authority ladder — align FILLS `outcome` only when NULL, memo keeps overwrite rights; (3) poller self-heals when reconciled votes vanish; (4) `--check-memo` read-only drift audit.

### Task 7: Ownership partition + outcome authority ladder

**Files:**
- Modify: `src/publish.py` (`_replace_votes`, `reconcile_memo`, `_update_aligned_items`, `align_and_flip`, `build_alignment_updates` docstring)
- Modify: `run_local.py` (`--publish-meeting` help text)
- Modify: `docs/runbooks/bloomington-meeting-day.md`, `docs/superpowers/specs/2026-07-28-clerk-memo-reconciler-design.md`

- [ ] **Step 1: Vote-type constants + partitioned deletes.** In `src/publish.py`, add module-level constants near `SEGMENT_BATCH_SIZE`:

```python
# Ownership partition for meetings.votes: each writer deletes/inserts only
# its own vote_type stripe, so a re-publish can never wipe memo-reconciled
# votes and a re-reconcile can never wipe federal floor votes.
FLOOR_VOTE_TYPE = "recorded"     # written by _replace_votes (federal CREC)
MEMO_VOTE_TYPE = "roll call"     # written by reconcile_memo (clerk memo)
```

In `_replace_votes`: scope the delete to the floor stripe (records first for FK safety, even though the federal path never writes records today), use the constant in the insert row, and replace the stale "re-publishing wipes its rows too" docstring sentence:

```python
    cur.execute(
        """
        DELETE FROM meetings.vote_records
        WHERE vote_id IN (SELECT id FROM meetings.votes
                          WHERE meeting_id = %s AND vote_type = %s)
        """,
        (meeting_uuid, FLOOR_VOTE_TYPE),
    )
    cur.execute(
        "DELETE FROM meetings.votes WHERE meeting_id = %s AND vote_type = %s",
        (meeting_uuid, FLOOR_VOTE_TYPE),
    )
```

Docstring replacement for the last sentence: "Deletes/inserts only the FLOOR_VOTE_TYPE stripe: memo-reconciled votes (MEMO_VOTE_TYPE, written by reconcile_memo) are a separate ownership stripe and survive re-publish untouched."

In `reconcile_memo`: scope both deletes to `MEMO_VOTE_TYPE` the same way, and use the constant in the INSERT (replace the literal `'roll call'` with a `%s` param). Update its docstring: delete the "NOTE a later re-publish ... wipes these votes ..." sentence and replace with "Votes are partitioned by vote_type: this function owns the MEMO_VOTE_TYPE stripe and never touches federal floor votes; re-publish (_replace_votes) likewise cannot wipe memo votes."

- [ ] **Step 2: Outcome authority ladder.** In `_update_aligned_items`, change the SQL so alignment fills but never overwrites an existing outcome:

```sql
        UPDATE meetings.agenda_items
        SET status = %s,
            segment_start_seconds = %s,
            segment_end_seconds = %s,
            outcome = COALESCE(outcome, %s),
            updated_at = now()
        WHERE meeting_id = %s AND position = %s
```

Docstring addition: "outcome uses COALESCE(existing, new): alignment FILLS outcomes but never overwrites one already set (the memo reconciler is the only overwriter — authority ladder: align fills → memo overwrites → align never un-fills)."

- [ ] **Step 3: Operator visibility in align.** In `align_and_flip`, extend the item SELECT to include `outcome`, keep a `{position: existing_outcome}` dict, and in the summary print loop append ` [existing outcome {x!r} preserved]` to an item's line when its existing outcome is non-null and differs from what the span proposed (or when span proposed None). Update `align_and_flip`'s docstring with one sentence on the ladder. Keep `ParsedItem` construction unchanged (unpack the 5th column separately).

- [ ] **Step 4: Text sites.** `run_local.py` `--publish-meeting` help: replace the "; re-publishing wipes memo-reconciled votes — re-run --reconcile-memo after" suffix (added in the review round) with nothing (the hazard is gone). Runbook step 6: replace the "Re-run this after any --publish-meeting re-publish" bullet with: "Memo votes survive re-publish (vote-type ownership partition) and memo outcomes survive re-align (align fills, never overwrites). A --reconcile-memo re-run is only needed when the clerk re-posts the memo — the daily poller handles that." Spec: rewrite the "Known interaction" section to describe the partition + ladder as implemented (title it "Ownership hardening (2026-07-28)").

- [ ] **Step 5:** Full suite green; commit: `fix: vote-type ownership partition + outcome authority ladder (align fills, memo overwrites)`.

### Task 8: Poller self-heal + --check-memo drift audit

**Files:**
- Modify: `src/memo_reconcile.py` (+ `diff_plan_against_db`), `src/publish.py` (`memo_votes_present`, `reconcile_memo(check=...)`), `scripts/poll_agendas.py`, `run_local.py`
- Test: extend `tests/test_memo_reconcile.py`

- [ ] **Step 1 (TDD): pure drift diff.** Tests first in `tests/test_memo_reconcile.py`:

```python
def test_diff_clean_when_db_matches(memo):
    plan = build_reconcile_plan(memo, [], SPEAKERS)
    existing = [(v.resolution, v.result, len(v.records)) for v in plan.votes]
    assert diff_plan_against_db(plan, [], existing) == []


def test_diff_flags_missing_extra_and_outcome(memo):
    items = [AgendaItemRow("i-r13", 12, "Resolution 2026-13", "failed")]  # wrong outcome
    plan = build_reconcile_plan(memo, items, SPEAKERS)
    existing = [(v.resolution, v.result, len(v.records)) for v in plan.votes][1:]  # one missing
    existing.append(("Ordinance 9999-9", "Passed 1–0", 0))                          # one extra
    drift = diff_plan_against_db(plan, items, existing)
    assert any("missing" in d for d in drift)
    assert any("Ordinance 9999-9" in d and "unexpected" in d for d in drift)
    assert any("Resolution 2026-13" in d and "outcome" in d for d in drift)
```

Implementation in `src/memo_reconcile.py`:

```python
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
    from collections import Counter

    drift: list[str] = []
    expected = Counter((v.resolution, v.result, len(v.records)) for v in plan.votes)
    actual = Counter(existing_votes)
    for key in sorted(expected - actual):
        drift.append(f"vote missing from DB: {key[0]} | {key[1]} | {key[2]} record(s)")
    for key in sorted(actual - expected):
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
```

- [ ] **Step 2: `reconcile_memo(meeting_id, check=False)`.** In the same first transaction, ALSO fetch the meeting's existing memo-stripe votes:

```python
                cur.execute(
                    """
                    SELECT v.resolution, v.result, count(r.id)
                    FROM meetings.votes v
                    LEFT JOIN meetings.vote_records r ON r.vote_id = v.id
                    WHERE v.meeting_id = %s AND v.vote_type = %s
                    GROUP BY v.id
                    """,
                    (meeting_uuid, MEMO_VOTE_TYPE),
                )
                existing_votes = [(res, result, int(n)) for (res, result, n) in cur.fetchall()]
```

After building `plan`: when `check` is true, skip the write transaction entirely; compute `drift = diff_plan_against_db(plan, agenda_items, existing_votes)`; print a `=== Memo check: {meeting_id} ===` block with each drift line prefixed `DRIFT:` (or "no drift — DB matches the memo"), still print plan NOTEs; return dict gains `"drift": drift` (and `"checked": True`). Write path unchanged otherwise.

- [ ] **Step 3: `memo_votes_present(meeting_id)` helper + poller self-heal.** publish.py:

```python
def memo_votes_present(meeting_id: str) -> bool:
    """True when the meeting (by slug) has memo-stripe votes rows. Cheap
    probe for the poller's self-heal: marker says reconciled but votes
    vanished -> re-reconcile."""
    conn = psycopg2.connect(_require_db_url())
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM meetings.votes v
                JOIN meetings.meetings m ON m.id = v.meeting_id
                WHERE m.slug = %s AND v.vote_type = %s
                LIMIT 1
                """,
                (meeting_id, MEMO_VOTE_TYPE),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()
```

`scripts/poll_agendas.py` `reconcile_memos`, replace the unchanged-marker skip:

```python
        if state.marker_for(slug) == marker:
            if dry_run or memo_votes_present(slug):
                print(f"  MEMO SKIP {slug}: unchanged")
                continue
            print(f"  MEMO HEAL {slug}: marker recorded but roll-call votes "
                  "missing — re-reconciling")
```

(import `memo_votes_present` alongside `reconcile_memo`; falls through to the normal reconcile path. Known bounded edge, note it in the function docstring: a meeting whose memo genuinely yields zero substantive votes re-heals daily until it ages out of the lookback window — harmless, loud, bounded.)

- [ ] **Step 4: `run_local.py --check-memo MEETING_ID`.** argparse next to `--reconcile-memo` ("Read-only: recompute the memo reconcile plan and report drift against the DB, then exit non-zero on drift"); dispatch after the reconcile-memo block:

```python
    if args.check_memo:
        from src.publish import reconcile_memo

        result = reconcile_memo(args.check_memo, check=True)
        sys.exit(1 if result.get("drift") else 0)
```

(match the file's existing exit conventions — check how other dispatches exit; `sys` is already imported. Also add to the `_option_supplied` map like the siblings.)

- [ ] **Step 5:** Full suite green (expect +2 tests); poller docstring Usage block gains one `--reconcile-memos` self-heal sentence; commit: `feat: poller self-heal + --check-memo drift audit`.

### Task 9: Live verification + push

- [ ] **Step 1:** Live: `--check-memo 2026-07-22-bloomington-regular-session` → expect "no drift" and exit 0 (votes written earlier this session are still there and match the memo). Then re-run `--reconcile-memo` once more (idempotency under the partitioned deletes) and `--check-memo` again → no drift. Any drift → STOP and report.
- [ ] **Step 2:** Full suite; push; note the hardening in the PR body (edit via `gh pr edit --body-file` or append a PR comment via `gh pr comment` — comment preferred, keeps history).
