# Federal Adapter — Slice 1 (CREC floor structure + roll-call votes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From `(date, chamber)`, turn a day's Congressional Record into structured floor output — legislative vs. one-minute vs. discarded granules, plus roll-call votes parsed from text and enriched with member bioguide IDs — with **no timestamps** (Slice 2) and **no essentials `politician_id` join** (follow-on).

**Architecture:** A structure-preserving layer on top of the existing `src/govinfo.py` CREC fetch. `govinfo` flattens granules into a speaker-turn stream and discards class/title; Slice 1 keeps each granule whole (`CrecGranule`), classifies it (`crec_classify`), parses roll-call votes from legislative granules' text (`crec_votes` — text is authoritative because MODS votes are flat/ungrouped across all roll calls), and joins surnames→bioguide via each granule's MODS (`crec_members`). `crec_floor.extract_floor_structure` orchestrates. Grounded in the 2026-07-18 spike findings (`docs/superpowers/specs/2026-07-18-federal-crec-spike-findings.md`).

**Tech Stack:** Python 3, `pytest`, `beautifulsoup4` (already used by `govinfo.html_to_text`), stdlib `re`/`json`/`dataclasses`. Network is an injected `fetch` callable (never called in tests). Use `.venv/bin/python` and `.venv/bin/pytest`.

**Scope note:** This plan produces a working `extract_floor_structure(date, chamber) -> FloorStructure` — independently valuable (structured votes) and fully offline-testable. Explicitly OUT of scope (later plans): per-item/vote **timestamps** (Slice 2, ASR-anchor mechanism), the **bioguide→`politician_id`** essentials join (reuses `congress_roster` + `crec_essentials`), and wiring into the meeting publish output.

---

## File Structure

- Create `src/crec_structure.py` — `CrecGranule` + `fetch_granules` (structure-preserving fetch; reuses `govinfo` primitives).
- Create `src/crec_classify.py` — `GranuleKind` + `classify` (floor granule taxonomy).
- Create `src/crec_votes.py` — `RollCallVote` + `parse_votes` (text-authoritative roll-call parsing).
- Create `src/crec_members.py` — `build_bioguide_index` + `MemberVote` + `enrich_vote` (MODS surname→bioguide).
- Create `src/crec_floor.py` — `FloorStructure` + `GranuleVotes` + `extract_floor_structure` (orchestration).
- Create fixtures under `tests/fixtures/govinfo/`: `granule_vote_block.txt`, `granule_vote_mods.xml`, `granule_backmatter.txt`.
- Create tests: `tests/test_crec_structure.py`, `tests/test_crec_classify.py`, `tests/test_crec_votes.py`, `tests/test_crec_members.py`, `tests/test_crec_floor.py`.

---

## Task 1: Ground-truth fixtures

Real `CREC-2019-07-11` content (trimmed to 5 names per position; tally header numbers set to match the included names so the sample is internally consistent).

**Files:**
- Create: `tests/fixtures/govinfo/granule_vote_block.txt`
- Create: `tests/fixtures/govinfo/granule_vote_mods.xml`
- Create: `tests/fixtures/govinfo/granule_backmatter.txt`

- [ ] **Step 1: Create the vote-block fixture (real Roll No. 438 structure, trimmed)**

Write `tests/fixtures/govinfo/granule_vote_block.txt`:

```
  The Acting CHAIR. The unfinished business is the demand for a
recorded vote on the amendment offered by the gentleman from Washington
(Mr. Smith) on which further proceedings were postponed.
  The Clerk redesignated the amendment.


                             Recorded Vote

  The Acting CHAIR. A recorded vote has been demanded.
  The vote was taken by electronic device, and there were--ayes 5,
noes 5, not voting 5, as follows:

                             [Roll No. 438]

                               AYES--5

     Adams
     Aguilar
     Allred
     Amash
     Axne

                               NOES--5

     Abraham
     Aderholt
     Allen
     Amodei
     Armstrong

                             NOT VOTING--5

     Fudge
     Gabbard
     Higgins (LA)
     McNerney
     Norton

  The result of the vote was announced as above recorded.
```

- [ ] **Step 2: Create the MODS fixture (real bioguides)**

Write `tests/fixtures/govinfo/granule_vote_mods.xml`:

```xml
<congMember bioGuideId="A000370" chamber="H" congress="116" party="D" role="VOTED YES" state="NC">
  <name type="parsed">Adams</name>
  <name type="authority-fnf">Alma S. Adams</name>
  <name type="authority-lnf">Adams, Alma S.</name>
</congMember>
<congMember bioGuideId="A000371" chamber="H" congress="116" party="D" role="VOTED YES" state="CA">
  <name type="parsed">Aguilar</name>
  <name type="authority-fnf">Pete Aguilar</name>
</congMember>
```

- [ ] **Step 3: Create the back-matter fixture (real Constitutional Authority Statement, 617 chars)**

Write `tests/fixtures/govinfo/granule_backmatter.txt`:

```
[Congressional Record Volume 165, Number 116 (Thursday, July 11, 2019)]
[House]
[Page H5727]
From the Congressional Record Online through the Government Publishing Office [www.gpo.gov]

           By Mrs. LESKO:
       H.R. 3694.
       Congress has the power to enact this legislation pursuant
     to the following:
       Article 1, Section 8, Clause 18--To make all Laws which
     shall be necessary and proper for carrying into Execution the
     foregoing Powers, and all other Powers vested by this
     Constitution in the Government of the United States or in any
     Department or Officer thereof.
```

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/govinfo/granule_vote_block.txt tests/fixtures/govinfo/granule_vote_mods.xml tests/fixtures/govinfo/granule_backmatter.txt
git commit -m "test(crec): add ground-truth CREC vote/mods/back-matter fixtures"
```

---

## Task 2: `crec_structure` — structure-preserving granule fetch

**Files:**
- Create: `src/crec_structure.py`
- Test: `tests/test_crec_structure.py`

- [ ] **Step 1: Write the failing test**

`tests/test_crec_structure.py`:

```python
from pathlib import Path
from src.crec_structure import CrecGranule, parse_granule_records, fetch_granules

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def test_parse_granule_records_keeps_class_and_title():
    page = (FIX / "granules_page1.json").read_text()
    recs = parse_granule_records(page, "house")
    assert recs == [("CREC-2018-10-10-pt1-PgH1-1", "HOUSE", "MORNING-HOUR DEBATE")]


def test_fetch_granules_builds_units_with_text():
    # Inline single-page list (NO nextPage) so pagination terminates. Do NOT
    # reuse granules_page1.json here — that fixture carries a nextPage pointing
    # at page 2, and a fake_fetch that answers every "/granules?" url with it
    # would loop forever.
    list_json = (
        '{"granules": [{"granuleClass": "HOUSE", '
        '"granuleId": "CREC-2018-10-10-pt1-PgH1-1", "title": "MORNING-HOUR DEBATE"}]}'
    )
    body = "<html><body><pre>Mr. SMITH. I yield.</pre></body></html>"

    def fake_fetch(url: str) -> str:
        if "/granules?" in url:
            return list_json  # single page, no nextPage -> loop ends
        return body           # any granule htm

    gs = fetch_granules("2018-10-10", "house", fetch=fake_fetch, api_key="k")
    assert len(gs) == 1
    g = gs[0]
    assert isinstance(g, CrecGranule)
    assert g.granule_id == "CREC-2018-10-10-pt1-PgH1-1"
    assert g.granule_class == "HOUSE"
    assert g.title == "MORNING-HOUR DEBATE"
    assert g.text.strip() == "Mr. SMITH. I yield."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_structure.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.crec_structure'`

- [ ] **Step 3: Write the implementation**

`src/crec_structure.py`:

```python
"""Structure-preserving CREC granule fetch (Slice 1 of the Federal adapter).

Unlike govinfo.fetch_congressional_record_turns (which flattens granules into a
speaker-turn stream and discards granule class/title), this keeps each granule as
a unit — id, class, title, and extracted text — the substrate the Federal
structure branch classifies and mines for votes. Reuses govinfo's URL builders,
pagination, and html_to_text. Pure parsing + injected fetch.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

from . import govinfo


@dataclass
class CrecGranule:
    granule_id: str
    granule_class: str   # "HOUSE" | "SENATE" | "DIGEST" | "EXTENSIONS" | ...
    title: str
    text: str            # extracted from the granule htm via govinfo.html_to_text


def parse_granule_records(json_text: str, chamber: str) -> list[tuple[str, str, str]]:
    """(granule_id, granule_class, title) for chamber granules, in document order.

    Like govinfo.parse_granule_list but KEEPS class + title (the item metadata).
    """
    target = govinfo._CHAMBER_TO_CLASS[chamber.lower()]
    data = json.loads(json_text)
    out: list[tuple[str, str, str]] = []
    for g in data.get("granules", []):
        klass = g.get("granuleClass") or g.get("docClass")
        if klass == target and g.get("granuleId"):
            out.append((g["granuleId"], klass, g.get("title") or ""))
    return out


def fetch_granules(
    date: str,
    chamber: str,
    *,
    fetch: Callable[[str], str] = govinfo._default_fetch,
    api_key: Optional[str] = None,
    max_granules: Optional[int] = None,
) -> Optional[list[CrecGranule]]:
    """Structure-preserving granules for a chamber on a day; None if no Record."""
    key = govinfo._resolve_api_key(api_key)
    pkg = govinfo._package_id(date)

    url = govinfo._granules_url(pkg, "*", key)
    records: list[tuple[str, str, str]] = []
    first = True
    while url:
        try:
            page = fetch(url)
        except Exception:
            if first:
                return None
            break
        first = False
        records.extend(parse_granule_records(page, chamber))
        url = govinfo._with_api_key(govinfo._next_offset_mark(page), key)

    if not records:
        return None
    if max_granules is not None and len(records) > max_granules:
        records = records[:max_granules]

    granules: list[CrecGranule] = []
    for gid, klass, title in records:
        try:
            htm = fetch(govinfo._granule_text_url(pkg, gid, key))
        except Exception:
            continue
        granules.append(CrecGranule(gid, klass, title, govinfo.html_to_text(htm)))
    return granules or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_structure.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/crec_structure.py tests/test_crec_structure.py
git commit -m "feat(crec): structure-preserving granule fetch (CrecGranule)"
```

---

## Task 3: `crec_classify` — floor granule taxonomy

**Files:**
- Create: `src/crec_classify.py`
- Test: `tests/test_crec_classify.py`

- [ ] **Step 1: Write the failing test**

`tests/test_crec_classify.py`:

```python
from pathlib import Path
from src.crec_structure import CrecGranule
from src.crec_classify import GranuleKind, classify

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def _g(title, text):
    return CrecGranule("id", "HOUSE", title, text)


def test_vote_bearing_granule_is_legislative():
    text = (FIX / "granule_vote_block.txt").read_text()
    assert classify(_g("NATIONAL DEFENSE AUTHORIZATION ACT", text)) is GranuleKind.LEGISLATIVE


def test_consideration_title_is_legislative_without_vote():
    assert classify(_g("PROVIDING FOR CONSIDERATION OF H.R. 962",
                       "Mr. Speaker, I yield.")) is GranuleKind.LEGISLATIVE


def test_constitutional_authority_statement_is_back_matter():
    text = (FIX / "granule_backmatter.txt").read_text()
    assert classify(_g("Constitutional Authority Statement for H.R. 3694",
                       text)) is GranuleKind.BACK_MATTER


def test_the_journal_is_procedural():
    assert classify(_g("THE JOURNAL", "The SPEAKER pro tempore. The Journal stands approved.")) is GranuleKind.PROCEDURAL


def test_one_minute_speech_is_attention():
    text = "The SPEAKER pro tempore. The gentleman is recognized.\n  Mr. SMITH. Mr. Speaker, I rise today to honor..."
    assert classify(_g("HONORING WILLIAM HENRY WARD", text)) is GranuleKind.ONE_MINUTE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_classify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.crec_classify'`

- [ ] **Step 3: Write the implementation**

`src/crec_classify.py`:

```python
"""Classify a CREC granule into a floor 'kind' (Slice 1 of the Federal adapter).

A House floor day is a HYBRID: ~half the granules are printed back-matter never
spoken on the floor, most spoken granules are one-minute / special-order speeches,
a few are legislative business, the rest procedural. Detection is NOT title bill-
number regex (that flags dozens of paperwork granules). Kinds route to branches:
  LEGISLATIVE -> agenda branch;  ONE_MINUTE -> attention branch;
  PROCEDURAL  -> holes;          BACK_MATTER -> discard (won't align to audio).
"""
from __future__ import annotations

import re
from enum import Enum

from .crec_structure import CrecGranule


class GranuleKind(str, Enum):
    LEGISLATIVE = "legislative"
    ONE_MINUTE = "one_minute"
    PROCEDURAL = "procedural"
    BACK_MATTER = "back_matter"


_PROCEDURAL_TITLES = frozenset({
    "PRAYER", "THE JOURNAL", "PLEDGE OF ALLEGIANCE", "RECESS", "AFTER RECESS",
    "ADJOURNMENT", "DESIGNATION OF SPEAKER PRO TEMPORE",
    "ANNOUNCEMENT BY THE SPEAKER PRO TEMPORE", "CONGRESSIONAL RECORD",
    "HOUSE OF REPRESENTATIVES",
})

_BACK_MATTER_TITLE_RE = re.compile(
    r"constitutional authority statement|executive communications|"
    r"reports? of committees|public bills and resolutions|memorials|"
    r"additional (co)?sponsors|senate bill referred|"
    r"communication from the clerk|reported bill", re.I)

# Reliable "a bill was taken up" title signals (no vote required).
_LEGIS_TITLE_RE = re.compile(
    r"providing for consideration of|request to consider", re.I)

# Spoken-floor markers: presiding-officer address / recognition / vote machinery.
_SPOKEN_RE = re.compile(
    r"\bThe (Acting )?(CHAIR|SPEAKER|PRESIDING OFFICER)\b|"
    r"\bMr\. Speaker\b|\bI yield\b|\brecorded vote\b|\[Roll No", re.I)

_VOTE_RE = re.compile(r"\[Roll No\.?\s*\d+\]")


def classify(g: CrecGranule) -> GranuleKind:
    title = g.title or ""
    if _VOTE_RE.search(g.text) or _LEGIS_TITLE_RE.search(title):
        return GranuleKind.LEGISLATIVE
    if _BACK_MATTER_TITLE_RE.search(title):
        return GranuleKind.BACK_MATTER
    if title.strip().upper() in _PROCEDURAL_TITLES:
        return GranuleKind.PROCEDURAL
    if not _SPOKEN_RE.search(g.text):
        # unspoken printed matter (e.g. a Constitutional Authority Statement)
        return GranuleKind.BACK_MATTER
    return GranuleKind.ONE_MINUTE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_classify.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/crec_classify.py tests/test_crec_classify.py
git commit -m "feat(crec): classify floor granules (legislative/one-minute/procedural/back-matter)"
```

---

## Task 4: `crec_votes` — text-authoritative roll-call parsing

**Files:**
- Create: `src/crec_votes.py`
- Test: `tests/test_crec_votes.py`

- [ ] **Step 1: Write the failing test**

`tests/test_crec_votes.py`:

```python
from pathlib import Path
from src.crec_votes import RollCallVote, parse_votes

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def test_parse_single_vote_block():
    text = (FIX / "granule_vote_block.txt").read_text()
    votes = parse_votes(text)
    assert len(votes) == 1
    v = votes[0]
    assert isinstance(v, RollCallVote)
    assert v.roll_number == 438
    assert v.positions["YEA"] == ["Adams", "Aguilar", "Allred", "Amash", "Axne"]
    assert v.positions["NAY"] == ["Abraham", "Aderholt", "Allen", "Amodei", "Armstrong"]
    assert v.positions["NOT_VOTING"] == ["Fudge", "Gabbard", "Higgins (LA)", "McNerney", "Norton"]
    assert "Smith" in v.question


def test_parse_two_votes_splits_on_roll_markers():
    text = (
        "The question is on agreeing to amendment A.\n"
        "                             [Roll No. 100]\n"
        "                               AYES--1\n"
        "     Adams\n"
        "  The result of the vote was announced as above recorded.\n"
        "The question is on agreeing to amendment B.\n"
        "                             [Roll No. 101]\n"
        "                               NOES--1\n"
        "     Abraham\n"
    )
    votes = parse_votes(text)
    assert [v.roll_number for v in votes] == [100, 101]
    assert votes[0].positions == {"YEA": ["Adams"]}
    assert votes[1].positions == {"NAY": ["Abraham"]}


def test_no_votes_returns_empty():
    assert parse_votes("Mr. SMITH. Mr. Speaker, I yield back.") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_votes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.crec_votes'`

- [ ] **Step 3: Write the implementation**

`src/crec_votes.py`:

```python
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
# NOTE (fixed during execution): a plain [^.]*\. stops at the first period, which
# lands on abbreviations like "Mr." in "(Mr. Smith)" and truncates the question.
# Consume common title abbreviations as units so their internal period doesn't end
# the match. Known best-effort limitation: multi-period refs like "H.R." or single-
# letter initials can still truncate the question (auxiliary metadata in Slice 1;
# robust measure parsing is the follow-on vote->measure task).
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_votes.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/crec_votes.py tests/test_crec_votes.py
git commit -m "feat(crec): parse roll-call votes from granule text"
```

---

## Task 5: `crec_members` — MODS surname→bioguide enrichment

**Files:**
- Create: `src/crec_members.py`
- Test: `tests/test_crec_members.py`

- [ ] **Step 1: Write the failing test**

`tests/test_crec_members.py`:

```python
from pathlib import Path
from src.crec_votes import RollCallVote
from src.crec_members import build_bioguide_index, MemberVote, enrich_vote

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def test_build_bioguide_index_from_mods():
    mods = (FIX / "granule_vote_mods.xml").read_text()
    idx = build_bioguide_index(mods)
    assert idx == {"Adams": "A000370", "Aguilar": "A000371"}


def test_ambiguous_surname_is_dropped():
    mods = (
        '<congMember bioGuideId="X1"><name type="parsed">Smith</name></congMember>'
        '<congMember bioGuideId="X2"><name type="parsed">Smith</name></congMember>'
        '<congMember bioGuideId="Y1"><name type="parsed">Jones</name></congMember>'
    )
    idx = build_bioguide_index(mods)
    assert "Smith" not in idx           # ambiguous -> unresolved, never mislinked
    assert idx["Jones"] == "Y1"


def test_enrich_vote_resolves_known_and_leaves_unknown():
    idx = {"Adams": "A000370", "Aguilar": "A000371"}
    v = RollCallVote(438, "q", {"YEA": ["Adams", "Aguilar"], "NAY": ["Abraham"]})
    members = enrich_vote(v, idx)
    assert MemberVote("Adams", "YEA", "A000370") in members
    assert MemberVote("Aguilar", "YEA", "A000371") in members
    assert MemberVote("Abraham", "NAY", None) in members
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_members.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.crec_members'`

- [ ] **Step 3: Write the implementation**

`src/crec_members.py`:

```python
"""Map CREC roll-call surnames to bioguide IDs via a granule's MODS (Slice 1).

MODS <congMember> carries bioGuideId + a <name type="parsed"> that matches the
surname form used in the text vote lists ('Adams', 'Higgins (LA)'). Build a
parsed-name -> bioguide index and enrich a RollCallVote. Best-effort: a surname
absent OR ambiguous in MODS stays unresolved (bioguide=None) — never link the
wrong member, mirroring crec_essentials. Pure parsing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .crec_votes import RollCallVote

_CONGMEMBER_RE = re.compile(
    r'<congMember\b[^>]*\bbioGuideId="(?P<bio>[^"]+)"[^>]*>(?P<body>.*?)</congMember>',
    re.S)
_PARSED_NAME_RE = re.compile(r'<name type="parsed">(?P<n>[^<]+)</name>')


def build_bioguide_index(mods_text: str) -> dict:
    """{parsed_surname: bioguide}; a surname mapping to >1 bioguide is dropped."""
    seen: dict[str, set] = {}
    for m in _CONGMEMBER_RE.finditer(mods_text):
        pn = _PARSED_NAME_RE.search(m.group("body"))
        if not pn:
            continue
        seen.setdefault(pn.group("n").strip(), set()).add(m.group("bio"))
    return {name: next(iter(bios)) for name, bios in seen.items() if len(bios) == 1}


@dataclass
class MemberVote:
    surname: str
    position: str            # YEA | NAY | PRESENT | NOT_VOTING
    bioguide: Optional[str]  # None when unresolved


def enrich_vote(vote: RollCallVote, index: dict) -> list:
    out: list = []
    for position, surnames in vote.positions.items():
        for s in surnames:
            out.append(MemberVote(s, position, index.get(s)))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_members.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/crec_members.py tests/test_crec_members.py
git commit -m "feat(crec): MODS surname->bioguide enrichment for roll-call votes"
```

---

## Task 6: `crec_floor` — orchestration

**Files:**
- Create: `src/crec_floor.py`
- Test: `tests/test_crec_floor.py`

- [ ] **Step 1: Write the failing test**

`tests/test_crec_floor.py`:

```python
from pathlib import Path
from src.crec_floor import FloorStructure, GranuleVotes, extract_floor_structure

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def test_extract_floor_structure_classifies_and_parses_votes():
    vote_text = (FIX / "granule_vote_block.txt").read_text()
    back_text = (FIX / "granule_backmatter.txt").read_text()
    mods = (FIX / "granule_vote_mods.xml").read_text()

    # One HOUSE granule list with two granules: a legislative (vote) one and a
    # back-matter one. granuleId encodes which htm/mods body to return.
    list_json = (
        '{"granules": ['
        '{"granuleClass": "HOUSE", "granuleId": "G-VOTE", "title": "NDAA"},'
        '{"granuleClass": "HOUSE", "granuleId": "G-BACK", '
        '"title": "Constitutional Authority Statement for H.R. 3694"}'
        ']}'
    )

    def fake_fetch(url: str) -> str:
        if "/granules?" in url:
            return list_json
        if "G-VOTE/mods" in url:
            return mods
        if "G-VOTE/htm" in url:
            return f"<pre>{vote_text}</pre>"
        if "G-BACK/htm" in url:
            return f"<pre>{back_text}</pre>"
        return "<pre></pre>"

    fs = extract_floor_structure("2019-07-11", "house", fetch=fake_fetch, api_key="k")
    assert isinstance(fs, FloorStructure)
    assert [g.granule_id for g in fs.agenda_granules] == ["G-VOTE"]
    assert fs.attention_granules == []
    assert fs.discarded == 1                      # the back-matter granule
    assert len(fs.votes) == 1
    gv = fs.votes[0]
    assert isinstance(gv, GranuleVotes)
    assert gv.votes[0].roll_number == 438
    # bioguide join: Adams (YEA) resolved from MODS
    adams = next(m for m in gv.members if m.surname == "Adams")
    assert adams.bioguide == "A000370"
    assert adams.position == "YEA"


def test_no_record_returns_none():
    def fake_fetch(url: str) -> str:
        raise RuntimeError("no package")

    assert extract_floor_structure("2018-10-13", "house", fetch=fake_fetch, api_key="k") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_floor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.crec_floor'`

- [ ] **Step 3: Write the implementation**

`src/crec_floor.py`:

```python
"""Top-level Federal floor structure extraction (Slice 1, no timestamps).

(date, chamber) -> FloorStructure: legislative + one-minute granules split out,
roll-call votes parsed from legislative granules and enriched with member bioguide
IDs via each granule's MODS, back-matter/procedural discarded. Timestamps (Slice 2)
and the essentials politician_id join (follow-on) are out of scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import govinfo
from .crec_structure import CrecGranule, fetch_granules
from .crec_classify import GranuleKind, classify
from .crec_votes import RollCallVote, parse_votes
from .crec_members import build_bioguide_index, enrich_vote


@dataclass
class GranuleVotes:
    granule: CrecGranule
    votes: list          # list[RollCallVote]
    members: list        # flat list[MemberVote] across the granule's votes


@dataclass
class FloorStructure:
    date: str
    chamber: str
    agenda_granules: list = field(default_factory=list)     # LEGISLATIVE CrecGranule
    attention_granules: list = field(default_factory=list)  # ONE_MINUTE CrecGranule
    votes: list = field(default_factory=list)               # list[GranuleVotes]
    discarded: int = 0                                       # back-matter + procedural


def _fetch_mods(date: str, granule_id: str, key: str, fetch: Callable[[str], str]) -> str:
    url = (f"{govinfo._API_ROOT}/packages/{govinfo._package_id(date)}"
           f"/granules/{granule_id}/mods?api_key={key}")
    try:
        return fetch(url)
    except Exception:
        return ""


def extract_floor_structure(
    date: str,
    chamber: str,
    *,
    fetch: Callable[[str], str] = govinfo._default_fetch,
    api_key: Optional[str] = None,
    max_granules: Optional[int] = None,
) -> Optional[FloorStructure]:
    key = govinfo._resolve_api_key(api_key)
    granules = fetch_granules(date, chamber, fetch=fetch, api_key=key, max_granules=max_granules)
    if granules is None:
        return None

    out = FloorStructure(date=date, chamber=chamber)
    for g in granules:
        kind = classify(g)
        if kind is GranuleKind.LEGISLATIVE:
            out.agenda_granules.append(g)
            votes = parse_votes(g.text)
            if votes:
                index = build_bioguide_index(_fetch_mods(date, g.granule_id, key, fetch))
                members = [mv for v in votes for mv in enrich_vote(v, index)]
                out.votes.append(GranuleVotes(granule=g, votes=votes, members=members))
        elif kind is GranuleKind.ONE_MINUTE:
            out.attention_granules.append(g)
        else:
            out.discarded += 1
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_floor.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full CREC suite**

Run: `.venv/bin/pytest tests/test_crec_structure.py tests/test_crec_classify.py tests/test_crec_votes.py tests/test_crec_members.py tests/test_crec_floor.py tests/test_govinfo.py -v`
Expected: PASS (all green; `test_govinfo.py` still passes — `govinfo` is unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/crec_floor.py tests/test_crec_floor.py
git commit -m "feat(crec): orchestrate floor structure extraction (Slice 1)"
```

---

## Task 7: Live smoke test (manual, needs GOVINFO_API_KEY)

**Files:** none (manual verification against live GovInfo).

- [ ] **Step 1: Run against the real spike day and eyeball the structure**

```bash
.venv/bin/python -c "
import os
from pathlib import Path
for line in Path('.env.local').read_text().splitlines():
    if line.startswith('GOVINFO_API_KEY'):
        os.environ['GOVINFO_API_KEY'] = line.split('=',1)[1].strip().strip('\"').strip(\"'\")
from src.crec_floor import extract_floor_structure
fs = extract_floor_structure('2019-07-11', 'house', max_granules=90)
print('agenda:', len(fs.agenda_granules), 'attention:', len(fs.attention_granules), 'discarded:', fs.discarded)
print('vote granules:', len(fs.votes))
for gv in fs.votes:
    print(' ', gv.granule.title, '->', len(gv.votes), 'roll-calls;', 
          sum(1 for m in gv.members if m.bioguide), 'of', len(gv.members), 'bioguide-resolved')
"
```
Expected: nonzero `agenda`/`attention`, `discarded` in the tens (back-matter dominates), the NDAA granule reporting ~21 roll-calls with the large majority of members bioguide-resolved. **This is a sanity eyeball, not an assertion** — record the numbers in the PR description.

- [ ] **Step 2: Note follow-ons in the PR description (do not implement here)**
  - Slice 2: per-item/vote **timestamps** via ASR vote-announcement anchoring (needs a processed House transcript).
  - Follow-on: **bioguide → `politician_id`** essentials join (`congress_roster` bioguide→member→`crec_essentials.resolve_politician_id`).
  - Follow-on: **vote→measure correlation** (which bill/amendment each roll-call was on) from the question context + granule bill refs.
  - Capture a real vote-granule htm/mods fixture from `CREC-2019-07-11` for a future full-granule regression test.

---

## Self-Review

**Spec coverage (against the spike findings' "Slice 1" definition):**
- Structure-preserving fetch → Task 2 ✓
- "What is a floor item" taxonomy (hybrid: legislative/one-minute/procedural/back-matter) → Task 3 ✓
- Text-authoritative roll-call parsing (MODS is flat/ungrouped) → Task 4 ✓
- MODS bioguide join → Task 5 ✓
- Orchestration to `FloorStructure` → Task 6 ✓
- Timestamps, `politician_id` join, vote→measure correlation → explicitly deferred (Task 7 notes) ✓

**Placeholder scan:** none — every step has runnable code/commands and real fixture content.

**Type consistency:** `CrecGranule` (Task 2) used in Tasks 3/6; `GranuleKind`/`classify` (Task 3) used in Task 6; `RollCallVote`/`parse_votes` (Task 4) used in Tasks 5/6; `MemberVote`/`build_bioguide_index`/`enrich_vote` (Task 5) used in Task 6; `FloorStructure`/`GranuleVotes`/`extract_floor_structure` (Task 6) used in Task 7. Signatures match across tasks.
