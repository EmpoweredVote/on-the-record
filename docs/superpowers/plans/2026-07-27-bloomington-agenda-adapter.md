# Bloomington Agenda Adapter (Pass A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch upcoming Bloomington City Council agendas from the city's OnBoard JSON API, parse the templated agenda PDF into structured items, LLM-interpret each item behind a groundedness gate, and publish scheduled meetings + agenda items to `meetings.*`.

**Architecture:** Follows the repo's house style throughout: pure parsing modules with one injected `fetch` callable (like `src/govinfo.py`/`src/house_cdn.py`), fixtures committed verbatim, LLM stage as `_SYSTEM` + `build_*_prompt()` pure functions with an anchoring-style gate (like `src/llm_utils.py`), publish as a thin cursor-bound delete-then-insert mirroring `_replace_votes`. New entry point `scripts/poll_agendas.py`. Body-specific facts (section→stage/public-comment mapping) live in a config dataclass, never in prompts.

**Tech Stack:** Python 3 (`.venv/bin/python` ONLY), requests, pdfplumber (new dep), anthropic, psycopg2. Repo: `/Users/chrisandrews/Documents/GitHub/on-the-record`. Spec: `docs/superpowers/specs/2026-07-27-bloomington-item-centric-civic-coverage-design.md`; source facts: `docs/superpowers/specs/2026-07-27-bloomington-publishing-spike-findings.md`.

**Prerequisite:** ev-accounts migration 1476 applied (companion plan `2026-07-27-agenda-items-schema-api.md`) before the live E2E task — everything else is testable without the DB.

**Conventions (from repo recon 2026-07-27):**
- Tests: flat `tests/test_*.py`, run `.venv/bin/pytest --tb=short -q`. `tests/conftest.py` autouse-deletes `DATABASE_URL` — DB code must be pure-helper-testable; keep cursor-bound functions thin and untested (documented pattern in `tests/test_publish.py:1-9`).
- Fetchers: `def f(..., *, fetch=_default_fetch)`; tests pass `fetch=lambda url: FIXTURE.read_text()`.
- Models: `@dataclass` with symmetric `to_dict()`/`from_dict()`, optional fields omitted from `to_dict` when None (see `FloorVote`, `src/models.py:255-290`).
- LLM: model names in `src/config.py`, never inline; injected client; tolerant JSON extraction; abstain-don't-guess.
- Branch: `feat/bloomington-agenda-adapter` off main.

---

### Task 1: Capture live fixtures

**Files:**
- Create: `tests/fixtures/onboard/meetings_window_2026.json`
- Create: `tests/fixtures/onboard/agenda_2026-07-29.pdf`
- Create: `tests/fixtures/onboard/agenda_2026-07-29.txt` (extracted text, made in Task 2)

- [ ] **Step 1: Create branch**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record && git checkout main && git pull && git checkout -b feat/bloomington-agenda-adapter
```

- [ ] **Step 2: Capture the OnBoard meetings JSON**

```bash
mkdir -p tests/fixtures/onboard
curl -s "https://bloomington.in.gov/onboard/meetings?format=json&start=2026-07-01&end=2026-08-31" -o tests/fixtures/onboard/meetings_window_2026.json
python3 -c "import json;d=json.load(open('tests/fixtures/onboard/meetings_window_2026.json'));print(type(d).__name__, len(d))"
```

Expected: valid JSON. **Read the fixture before writing any parser code** — the spike says meetings are keyed by date/time with `id`, `title`, `location`, ISO-8601 `start`/`end`, and a `files` map keyed by type (`Agenda`, `Packet`, `Minutes`, `Memorandum`) where each file has `url`, `filename`, `mime_type`, `created`/`indexed`/`updated`. Confirm the exact JSON shape (top-level dict vs list, the exact `title` string for council meetings — expected to start with "Common Council") and note it in a comment at the top of `src/onboard.py`. All parser code in Task 3 must match the REAL shape, not this plan's assumption.

- [ ] **Step 3: Capture the July 29 agenda PDF**

```bash
curl -sL "https://bloomington.in.gov/onboard/meetingFiles/17202/download" -o tests/fixtures/onboard/agenda_2026-07-29.pdf
file tests/fixtures/onboard/agenda_2026-07-29.pdf
```

Expected: `PDF document`. (~2 pages. If the URL 404s, pull the current Agenda URL for the next Regular Session out of the JSON fixture instead and rename the fixture accordingly — keep all downstream test expectations in sync with what the real agenda contains.)

- [ ] **Step 4: Commit fixtures**

```bash
git add tests/fixtures/onboard/ && git commit -m "test: capture live OnBoard fixtures (meetings JSON + agenda PDF)"
```

---

### Task 2: PDF text extraction — `src/pdf_text.py`

**Files:**
- Modify: `requirements.txt` (add `pdfplumber>=0.11`)
- Create: `src/pdf_text.py`
- Test: `tests/test_pdf_text.py`
- Create: `tests/fixtures/onboard/agenda_2026-07-29.txt`

- [ ] **Step 1: Install the dependency**

```bash
echo "pdfplumber>=0.11" >> requirements.txt && .venv/bin/pip install pdfplumber
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_pdf_text.py`:

```python
from pathlib import Path

from src.pdf_text import extract_text

FIX = Path(__file__).parent / "fixtures" / "onboard"


def test_extracts_agenda_text_with_line_structure():
    text = extract_text(FIX / "agenda_2026-07-29.pdf")
    # Section headers from the stable 10-section template survive extraction.
    assert "Legislation for Second Readings and Resolutions" in text
    assert "Adjournment" in text
    # Line breaks are preserved (the section parser is line-oriented).
    assert text.count("\n") > 10


def test_missing_file_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        extract_text(FIX / "nope.pdf")
```

(If the captured agenda's real text differs — e.g. no second-readings section that week — adjust the asserted strings to two section headers that ARE present. Verify by eye first: `.venv/bin/python -c "import pdfplumber; print(pdfplumber.open('tests/fixtures/onboard/agenda_2026-07-29.pdf').pages[0].extract_text())"`.)

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pdf_text.py -q`
Expected: FAIL — no module `src.pdf_text`.

- [ ] **Step 4: Implement**

Create `src/pdf_text.py`:

```python
"""Text extraction for city agenda/packet PDFs.

Bloomington's OnBoard PDFs are digitally generated (not scanned), so plain
text extraction is reliable; no OCR path. Kept as its own module so the
agenda parser stays pure-text and other adapters (county SharePoint PDFs)
can reuse it.
"""
from pathlib import Path

import pdfplumber


def extract_text(pdf_path: Path) -> str:
    """Return the PDF's text, pages joined by newlines, line structure kept."""
    if not Path(pdf_path).exists():
        raise FileNotFoundError(pdf_path)
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)
```

- [ ] **Step 5: Run test to verify it passes, then freeze the extracted text as a fixture**

Run: `.venv/bin/pytest tests/test_pdf_text.py -q` → PASS. Then:

```bash
.venv/bin/python -c "from pathlib import Path; from src.pdf_text import extract_text; Path('tests/fixtures/onboard/agenda_2026-07-29.txt').write_text(extract_text(Path('tests/fixtures/onboard/agenda_2026-07-29.pdf')))"
```

The `.txt` fixture is what the parser tests (Task 4) run against, so parser tests don't re-extract on every run.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt src/pdf_text.py tests/test_pdf_text.py tests/fixtures/onboard/agenda_2026-07-29.txt && git commit -m "feat: pdfplumber-based agenda PDF text extraction"
```

---

### Task 3: OnBoard client — `src/onboard.py`

**Files:**
- Create: `src/onboard.py`
- Test: `tests/test_onboard.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_onboard.py` (adjust field access to the REAL fixture shape noted in Task 1 — the assertions below assume the spike's description):

```python
import json
from pathlib import Path

from src.onboard import OnBoardMeeting, fetch_meetings_window

FIX = Path(__file__).parent / "fixtures" / "onboard"


def _fake_fetch(url: str) -> str:
    return (FIX / "meetings_window_2026.json").read_text()


def test_fetch_meetings_window_returns_council_meetings():
    meetings = fetch_meetings_window(
        "2026-07-01", "2026-08-31", title_prefix="Common Council", fetch=_fake_fetch
    )
    assert meetings, "expected at least one Common Council meeting in the window"
    m = meetings[0]
    assert isinstance(m, OnBoardMeeting)
    assert m.title.startswith("Common Council")
    assert m.start.startswith("2026-")          # ISO-8601 with offset
    assert m.onboard_id                          # OnBoard's meeting id
    # File attachments keyed by type; Agenda carries url + created timestamp.
    if m.agenda_url is not None:
        assert m.agenda_url.startswith("https://bloomington.in.gov/onboard/")
        assert m.agenda_created is not None


def test_title_prefix_filters_out_other_bodies():
    all_meetings = fetch_meetings_window("2026-07-01", "2026-08-31", title_prefix="", fetch=_fake_fetch)
    council = fetch_meetings_window("2026-07-01", "2026-08-31", title_prefix="Common Council", fetch=_fake_fetch)
    assert len(council) <= len(all_meetings)


def test_url_construction():
    seen = {}
    def capture(url):
        seen["url"] = url
        return (FIX / "meetings_window_2026.json").read_text()
    fetch_meetings_window("2026-07-01", "2026-08-31", title_prefix="Common Council", fetch=capture)
    assert "format=json" in seen["url"]
    assert "start=2026-07-01" in seen["url"]
    assert "end=2026-08-31" in seen["url"]


def test_malformed_json_returns_empty():
    assert fetch_meetings_window("2026-07-01", "2026-08-31", title_prefix="X", fetch=lambda u: "not json") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_onboard.py -q`
Expected: FAIL — no module `src.onboard`.

- [ ] **Step 3: Implement**

Create `src/onboard.py`. **First read the real fixture and document its exact shape in the module docstring.** Skeleton (adapt traversal to the real shape):

```python
"""Client for Bloomington's OnBoard meetings JSON API.

Endpoint: https://bloomington.in.gov/onboard/meetings?format=json&start=&end=
Shape (captured 2026-07-27, tests/fixtures/onboard/meetings_window_2026.json):
    <DOCUMENT THE REAL TOP-LEVEL SHAPE HERE — dict keyed by date vs list>
Each meeting: id, title, location, start/end (ISO-8601 with offset), and a
`files` map keyed by type (Agenda, Packet, Minutes, Memorandum, ...), each
file carrying url, filename, mime_type, created/indexed/updated timestamps.

House style: pure parsing + injected fetch (see src/house_cdn.py).
"""
from dataclasses import dataclass, field
from typing import Callable, Optional
import json
import urllib.request

BASE_URL = "https://bloomington.in.gov/onboard/meetings"


def _default_fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


@dataclass
class OnBoardFile:
    file_type: str          # "Agenda", "Packet", ...
    url: str
    filename: str
    created: Optional[str] = None
    updated: Optional[str] = None


@dataclass
class OnBoardMeeting:
    onboard_id: str
    title: str
    start: str              # ISO-8601 with offset
    end: Optional[str] = None
    location: Optional[str] = None
    files: list[OnBoardFile] = field(default_factory=list)

    def _file(self, file_type: str) -> Optional[OnBoardFile]:
        for f in self.files:
            if f.file_type == file_type:
                return f
        return None

    @property
    def agenda_url(self) -> Optional[str]:
        f = self._file("Agenda")
        return f.url if f else None

    @property
    def agenda_created(self) -> Optional[str]:
        f = self._file("Agenda")
        return f.created if f else None

    @property
    def packet_url(self) -> Optional[str]:
        f = self._file("Packet")
        return f.url if f else None

    @property
    def agenda_updated_marker(self) -> str:
        """Change-detection key: agenda url + latest created/updated stamp."""
        f = self._file("Agenda")
        if f is None:
            return ""
        return f"{f.url}|{f.updated or f.created or ''}"


def fetch_meetings_window(
    start: str,
    end: str,
    *,
    title_prefix: str,
    fetch: Callable[[str], str] = _default_fetch,
) -> list[OnBoardMeeting]:
    """Meetings in [start, end] whose title starts with title_prefix.

    Returns [] on malformed payloads (logged upstream as a coverage event).
    """
    url = f"{BASE_URL}?format=json&start={start}&end={end}"
    try:
        payload = json.loads(fetch(url))
    except (ValueError, OSError):
        return []
    meetings: list[OnBoardMeeting] = []
    for raw in _iter_raw_meetings(payload):   # handles the real top-level shape
        title = raw.get("title", "")
        if title_prefix and not title.startswith(title_prefix):
            continue
        meetings.append(_parse_meeting(raw))
    meetings.sort(key=lambda m: m.start)
    return meetings
```

Implement `_iter_raw_meetings` and `_parse_meeting` against the captured fixture's actual structure (including the `files` map traversal). Keep both pure.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_onboard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/onboard.py tests/test_onboard.py && git commit -m "feat: OnBoard meetings JSON client (injected fetch, fixture-tested)"
```

---

### Task 4: Agenda parser — `src/agenda_parse.py`

**Files:**
- Create: `src/agenda_parse.py`
- Test: `tests/test_agenda_parse.py`

The Bloomington agenda template (spike, verified on the real PDF): numbered
sections `1.`–`10.`; lettered items `A.`/`B.` under sections; legislation lines
`Ordinance 2026-16 – Title` / `Resolution 2026-14 – Title` with a following
`Council Sponsor:` line. The parser is body-agnostic text mechanics; the
*meaning* of sections (stage, public comment) is Task 5's config.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agenda_parse.py`:

```python
from pathlib import Path

from src.agenda_parse import ParsedItem, parse_agenda

FIX = Path(__file__).parent / "fixtures" / "onboard"


def _agenda_text() -> str:
    return (FIX / "agenda_2026-07-29.txt").read_text()


def test_parses_sections_and_items_in_order():
    items = parse_agenda(_agenda_text())
    assert items, "expected items from the real agenda"
    # position is 1-based and strictly increasing
    assert [i.position for i in items] == list(range(1, len(items) + 1))
    # every item knows its section header verbatim
    assert all(i.section for i in items)


def test_legislation_items_get_ref_and_number():
    items = parse_agenda(_agenda_text())
    legislation = [i for i in items if i.legislation_ref]
    assert legislation, "the 2026-07-29 agenda has legislation items"
    ref = legislation[0].legislation_ref
    assert ref.split()[0] in ("Ordinance", "Resolution", "Appropriation")
    assert "–" not in ref  # ref is just "Ordinance 2026-16", title separated


def test_sponsor_extracted_when_present():
    items = parse_agenda(_agenda_text())
    sponsored = [i for i in items if i.sponsor]
    # The template prints "Council Sponsor:" under each legislation item.
    assert len(sponsored) >= 1


def test_synthetic_template_full_shape():
    text = (
        "1. ROLL CALL\n"
        "2. AGENDA SUMMATION\n"
        "3. MINUTES FOR APPROVAL\n"
        "A. Regular Session June 3, 2026\n"
        "6. LEGISLATION FOR FIRST READINGS\n"
        "A. Ordinance 2026-16 – To Amend an Ordinance Fixing Salaries\n"
        "Council Sponsor: Cm. Piedmont-Smith\n"
        "7. LEGISLATION FOR SECOND READINGS AND RESOLUTIONS\n"
        "A. Resolution 2026-14 – To Approve an Interlocal Agreement\n"
        "Council Sponsor: Cm. Rosenbarger\n"
        "10. ADJOURNMENT\n"
    )
    items = parse_agenda(text)
    by_number = {i.item_number: i for i in items}
    minutes = by_number["3A"]
    assert minutes.section.startswith("MINUTES")
    assert minutes.legislation_ref is None
    first = by_number["6A"]
    assert first.legislation_ref == "Ordinance 2026-16"
    assert first.title_raw.startswith("Ordinance 2026-16")
    assert first.sponsor == "Cm. Piedmont-Smith"
    second = by_number["7A"]
    assert second.legislation_ref == "Resolution 2026-14"
    # Headline-only sections (Roll Call, Adjournment) become single items too:
    assert any(i.section.startswith("ROLL CALL") for i in items)


def test_empty_text_returns_empty():
    assert parse_agenda("") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_agenda_parse.py -q`
Expected: FAIL — no module.

- [ ] **Step 3: Implement**

Create `src/agenda_parse.py`:

```python
"""Line-oriented parser for Bloomington-style templated agenda text.

Input is extract_text() output (src/pdf_text.py). Mechanics only — section
MEANING (stage / public comment) is adapter config (src/bodies.py). Anchors:
  section:  ^\\s*(\\d{1,2})\\.\\s+(HEADER)
  item:     ^\\s*([A-Z])\\.\\s+(text)
  ref:      (Appropriation Ordinance|Ordinance|Resolution)\\s+\\d{4}-\\d+
  sponsor:  Council Sponsor[s]?: name
Sections with no lettered items (Roll Call, Adjournment) become one item so
`position` covers the whole agenda and nothing is silently dropped.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

_SECTION_RE = re.compile(r"^\s*(\d{1,2})\.\s+(.+?)\s*$")
_ITEM_RE = re.compile(r"^\s*([A-Z])\.\s+(.+?)\s*$")
_REF_RE = re.compile(
    r"\b((?:Appropriation\s+)?(?:Ordinance|Resolution))\s+(\d{4}-\d+)"
)
_SPONSOR_RE = re.compile(r"Council\s+Sponsors?\s*:?\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class ParsedItem:
    position: int              # 1-based across the whole agenda
    item_number: str           # "6A", or "1" for a section with no letters
    section: str               # verbatim section header
    section_number: int
    title_raw: str             # verbatim item line (continuation lines joined)
    legislation_ref: Optional[str] = None   # "Ordinance 2026-16"
    sponsor: Optional[str] = None
    extra_lines: list[str] = field(default_factory=list)


def parse_agenda(text: str) -> list[ParsedItem]:
    if not text.strip():
        return []
    items: list[ParsedItem] = []
    section_header = ""
    section_number = 0
    section_has_items = False
    position = 0

    def close_headline_section():
        # A section that ended with no lettered items is itself one item.
        nonlocal position
        if section_header and not section_has_items:
            position += 1
            items.append(
                ParsedItem(
                    position=position,
                    item_number=str(section_number),
                    section=section_header,
                    section_number=section_number,
                    title_raw=section_header,
                )
            )

    for line in text.splitlines():
        sec = _SECTION_RE.match(line)
        # Guard: a legislation line like "6A. ..." must not match _SECTION_RE;
        # section headers are ALL-CAPS-ish and short. Prefer the item match
        # when a current section exists and the line starts with a letter.
        item = _ITEM_RE.match(line)
        if sec and not item:
            close_headline_section()
            section_number = int(sec.group(1))
            section_header = sec.group(2)
            section_has_items = False
            continue
        if item and section_header:
            section_has_items = True
            position += 1
            title = item.group(2)
            ref_m = _REF_RE.search(title)
            ref = f"{ref_m.group(1)} {ref_m.group(2)}" if ref_m else None
            items.append(
                ParsedItem(
                    position=position,
                    item_number=f"{section_number}{item.group(1)}",
                    section=section_header,
                    section_number=section_number,
                    title_raw=title,
                    legislation_ref=ref,
                )
            )
            continue
        # Continuation / metadata lines attach to the last item.
        if items and line.strip():
            sponsor_m = _SPONSOR_RE.search(line)
            if sponsor_m:
                items[-1].sponsor = sponsor_m.group(1)
            else:
                items[-1].extra_lines.append(line.strip())

    close_headline_section()
    return items
```

Iterate against the REAL agenda fixture until the real-fixture tests pass —
the synthetic test pins the contract; the real fixture is the truth. Watch for:
wrapped title lines (join into `title_raw`), the en-dash vs hyphen after the
ref, page-break artifacts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_agenda_parse.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agenda_parse.py tests/test_agenda_parse.py && git commit -m "feat: templated agenda parser (sections, lettered items, legislation refs, sponsors)"
```

---

### Task 5: Body config — `src/bodies.py`

**Files:**
- Create: `src/bodies.py`
- Test: `tests/test_bodies.py`

Jurisdiction facts are ADAPTER KNOWLEDGE, never LLM output (spec rule). This
module encodes Bloomington Common Council's section semantics from the spike +
the council's public-comment rules PDF. A second body later = a second
`BodyConfig` instance, zero new code.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bodies.py`:

```python
from src.bodies import BLOOMINGTON_COMMON_COUNCIL, classify_item
from src.agenda_parse import ParsedItem


def _item(section_number: int, section: str, title: str, ref=None) -> ParsedItem:
    return ParsedItem(
        position=1, item_number="X", section=section,
        section_number=section_number, title_raw=title, legislation_ref=ref,
    )


def test_first_reading_ordinance():
    it = _item(6, "LEGISLATION FOR FIRST READINGS",
               "Ordinance 2026-16 – To Amend Salaries", ref="Ordinance 2026-16")
    c = classify_item(it, BLOOMINGTON_COMMON_COUNCIL)
    assert c.kind == "ordinance"
    assert c.stage == "First reading"
    assert c.public_comment is False


def test_second_reading_gets_public_comment():
    it = _item(7, "LEGISLATION FOR SECOND READINGS AND RESOLUTIONS",
               "Resolution 2026-14 – Interlocal Agreement", ref="Resolution 2026-14")
    c = classify_item(it, BLOOMINGTON_COMMON_COUNCIL)
    assert c.kind == "resolution"
    assert c.stage == "Second reading — final vote"
    assert c.public_comment is True
    assert "comment" in (c.public_comment_note or "").lower()


def test_public_report_sections_are_comment_periods():
    it = _item(8, "ADDITIONAL PUBLIC COMMENT", "ADDITIONAL PUBLIC COMMENT")
    c = classify_item(it, BLOOMINGTON_COMMON_COUNCIL)
    assert c.kind == "public-comment"
    assert c.public_comment is True


def test_minutes_and_appointments():
    m = classify_item(_item(3, "MINUTES FOR APPROVAL", "Regular Session June 3, 2026"),
                      BLOOMINGTON_COMMON_COUNCIL)
    assert m.kind == "minutes"
    a = classify_item(_item(5, "APPOINTMENTS TO BOARDS AND COMMISSIONS", "Plan Commission vacancy"),
                      BLOOMINGTON_COMMON_COUNCIL)
    assert a.kind == "appointment"


def test_unknown_section_falls_back_to_other():
    c = classify_item(_item(99, "SOMETHING NEW", "Mystery item"), BLOOMINGTON_COMMON_COUNCIL)
    assert c.kind == "other"
    assert c.stage is None
    assert c.public_comment is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bodies.py -q` → FAIL (no module).

- [ ] **Step 3: Implement**

Create `src/bodies.py`:

```python
"""Per-body adapter config: what agenda sections MEAN for a given body.

Stage and public-comment facts come from here (encoded from official council
rules), never from an LLM. Sources for Bloomington: spike findings doc
2026-07-27 + "Rules for Making Public Comment" (adopted 2024-06-05, amended
2025-08-06). Section matching is by header keyword, not number, so agenda
renumbering doesn't break classification.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SectionRule:
    header_keyword: str            # matched case-insensitively against the header
    kind: str                      # default kind for items in this section
    stage: Optional[str] = None
    public_comment: bool = False
    public_comment_note: Optional[str] = None


@dataclass(frozen=True)
class BodyConfig:
    slug: str                      # body slug, also used in meeting slugs
    city: str
    state: str
    meeting_title_prefix: str      # OnBoard title filter
    meeting_type: str
    event_kind: str
    timezone: str                  # IANA
    section_rules: tuple[SectionRule, ...]
    source_note: str


_GENERAL_COMMENT_NOTE = (
    "General public comment period: one comment of up to 3 minutes, at either "
    "this period or the other general period (not both), from the podium — no "
    "advance sign-up."
)

BLOOMINGTON_COMMON_COUNCIL = BodyConfig(
    slug="bloomington-city-council",
    city="Bloomington",
    state="IN",
    meeting_title_prefix="Common Council",
    meeting_type="Regular Session",
    event_kind="council",
    timezone="America/Indiana/Indianapolis",
    section_rules=(
        SectionRule("ROLL CALL", kind="procedural"),
        SectionRule("AGENDA SUMMATION", kind="procedural"),
        SectionRule("MINUTES", kind="minutes"),
        SectionRule("REPORTS", kind="report"),
        SectionRule("APPOINTMENTS", kind="appointment"),
        SectionRule(
            "FIRST READING", kind="legislation", stage="First reading",
            public_comment=False,
            public_comment_note=(
                "First readings are typically read by title only; public comment "
                "on this item comes at its second reading."
            ),
        ),
        SectionRule(
            "SECOND READING", kind="legislation",
            stage="Second reading — final vote",
            public_comment=True,
            public_comment_note=(
                "Public comment is taken on this item during the meeting before "
                "the vote."
            ),
        ),
        SectionRule(
            "PUBLIC COMMENT", kind="public-comment",
            public_comment=True, public_comment_note=_GENERAL_COMMENT_NOTE,
        ),
        SectionRule("COUNCIL SCHEDULE", kind="procedural"),
        SectionRule("ADJOURNMENT", kind="procedural"),
    ),
    source_note=(
        "Section semantics: Bloomington Common Council agenda template + Rules "
        "for Making Public Comment (2024-06-05, am. 2025-08-06); see spike "
        "findings 2026-07-27."
    ),
)


@dataclass(frozen=True)
class ItemClassification:
    kind: str
    stage: Optional[str]
    public_comment: bool
    public_comment_note: Optional[str]


def classify_item(item, body: BodyConfig) -> ItemClassification:
    """Map a ParsedItem to kind/stage/comment via the body's section rules."""
    header = item.section.upper()
    rule = None
    for r in body.section_rules:
        if r.header_keyword in header:
            rule = r
            break
    if rule is None:
        return ItemClassification("other", None, False, None)
    kind = rule.kind
    if kind == "legislation":
        # Refine by the legislation ref type parsed from the title.
        ref = (item.legislation_ref or "").lower()
        kind = "resolution" if "resolution" in ref else "ordinance"
    # The "Reports ... D. Public" sub-item is a general comment period.
    if rule.kind == "report" and item.title_raw.strip().upper() in ("PUBLIC", "REPORTS FROM THE PUBLIC"):
        return ItemClassification("public-comment", None, True, _GENERAL_COMMENT_NOTE)
    return ItemClassification(kind, rule.stage, rule.public_comment, rule.public_comment_note)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bodies.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bodies.py tests/test_bodies.py && git commit -m "feat: per-body adapter config; Bloomington Common Council section semantics"
```

---### Task 6: AgendaItem model — `src/models.py`

**Files:**
- Modify: `src/models.py` (add `AgendaItem` after `FloorVote`, ~line 290)
- Test: `tests/test_models.py` (extend; if per-class test files are the norm, follow the existing file for FloorVote tests)

- [ ] **Step 1: Write the failing test**

Add (to wherever FloorVote's round-trip tests live; otherwise `tests/test_models.py`):

```python
from src.models import AgendaItem


def test_agenda_item_round_trip():
    item = AgendaItem(
        position=6, item_number="6A",
        title_raw="Ordinance 2026-16 – To Amend Salaries",
        kind="ordinance", legislation_ref="Ordinance 2026-16",
        summary_plain="Adjusts police and fire salaries.",
        decision_plain="First of two votes.",
        stage="First reading", public_comment=False,
        public_comment_note="Comment comes at second reading.",
        source_url="https://bloomington.in.gov/onboard/meetingFiles/17202/download",
    )
    d = item.to_dict()
    assert AgendaItem.from_dict(d) == item


def test_agenda_item_omits_none_fields_in_to_dict():
    item = AgendaItem(
        position=1, item_number="1", title_raw="ROLL CALL", kind="procedural",
        source_url="https://example.gov/agenda.pdf",
    )
    d = item.to_dict()
    assert "legislation_ref" not in d
    assert "summary_plain" not in d
    assert d["public_comment"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_models.py -q` → FAIL (import error).

- [ ] **Step 3: Implement**

Add to `src/models.py` after `FloorVote` (mirror its style exactly — dataclass, symmetric `to_dict`/`from_dict`, None-fields omitted):

```python
@dataclass
class AgendaItem:
    """One agenda item published pre-meeting (Pass A of the Bloomington
    item-centric coverage design). segment bounds/outcome stay None until the
    post-meeting alignment pass (Pass B) fills them."""

    position: int
    item_number: str
    title_raw: str
    kind: str
    source_url: str
    legislation_ref: Optional[str] = None
    summary_plain: Optional[str] = None
    decision_plain: Optional[str] = None
    stage: Optional[str] = None
    public_comment: bool = False
    public_comment_note: Optional[str] = None
    outcome: Optional[str] = None
    segment_start_seconds: Optional[float] = None
    segment_end_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        d = {
            "position": self.position,
            "item_number": self.item_number,
            "title_raw": self.title_raw,
            "kind": self.kind,
            "source_url": self.source_url,
            "public_comment": self.public_comment,
        }
        for key in ("legislation_ref", "summary_plain", "decision_plain",
                    "stage", "public_comment_note", "outcome",
                    "segment_start_seconds", "segment_end_seconds"):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AgendaItem":
        return cls(
            position=d["position"],
            item_number=d["item_number"],
            title_raw=d["title_raw"],
            kind=d["kind"],
            source_url=d["source_url"],
            public_comment=d.get("public_comment", False),
            legislation_ref=d.get("legislation_ref"),
            summary_plain=d.get("summary_plain"),
            decision_plain=d.get("decision_plain"),
            stage=d.get("stage"),
            public_comment_note=d.get("public_comment_note"),
            outcome=d.get("outcome"),
            segment_start_seconds=d.get("segment_start_seconds"),
            segment_end_seconds=d.get("segment_end_seconds"),
        )
```

- [ ] **Step 4: Run to verify pass** → `.venv/bin/pytest tests/test_models.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py && git commit -m "feat: AgendaItem model (FloorVote-style slim projection)"
```

---

### Task 7: Interpretation + groundedness gate — `src/agenda_interpret.py`

**Files:**
- Modify: `src/config.py` (add `AGENDA_INTERPRET_MODEL = "claude-sonnet-4-5"`, `AGENDA_INTERPRET_MAX_TOKENS = 600` next to the SUMMARY_* block ~line 42)
- Create: `src/agenda_interpret.py`
- Test: `tests/test_agenda_interpret.py`

Failure philosophy (spec): a mis-summarized PENDING ordinance is worse than no
summary. The gate rejects any output whose numbers or legislation refs aren't
anchored in the source text; rejected items publish with `title_raw` only
(summary fields None) — abstain, don't guess. Mirrors `_name_is_anchored`
(`src/llm_utils.py:37-52`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agenda_interpret.py`:

```python
import json

from src.agenda_interpret import (
    build_interpret_prompt,
    interpret_item,
    ungrounded_tokens,
)
from src.agenda_parse import ParsedItem


def _item(title="Ordinance 2026-16 – To Amend an Ordinance Fixing Salaries",
          ref="Ordinance 2026-16"):
    return ParsedItem(position=6, item_number="6A",
                      section="LEGISLATION FOR FIRST READINGS", section_number=6,
                      title_raw=title, legislation_ref=ref)


SOURCE = (
    "Ordinance 2026-16 – To Amend an Ordinance Fixing the Salaries of Officers "
    "and Employees of the Police and Fire Departments for 2027. Increases base "
    "pay by 4 percent effective January 1, 2027."
)


class FakeClient:
    def __init__(self, reply: str):
        self._reply = reply
        self.last_kwargs = None

    class _Msg:
        def __init__(self, text):
            self.content = [type("B", (), {"text": text})()]

    @property
    def messages(self):
        outer = self
        class _M:
            def create(self, **kwargs):
                outer.last_kwargs = kwargs
                return outer._Msg(outer._reply)
        return _M()


def test_prompt_contains_title_and_source():
    prompt = build_interpret_prompt(_item(), SOURCE)
    assert "Ordinance 2026-16" in prompt
    assert "4 percent" in prompt


def test_grounded_output_is_kept():
    reply = json.dumps({
        "summary_plain": "Raises police and fire base pay by 4 percent starting January 1, 2027.",
        "decision_plain": "Whether to advance the pay change; final vote comes at second reading.",
    })
    result = interpret_item(FakeClient(reply), _item(), SOURCE)
    assert result.summary_plain.startswith("Raises police and fire")
    assert result.decision_plain


def test_ungrounded_number_is_rejected():
    reply = json.dumps({
        "summary_plain": "Raises pay by 12 percent for 500 employees.",
        "decision_plain": "Whether to advance the pay change.",
    })
    result = interpret_item(FakeClient(reply), _item(), SOURCE)
    assert result.summary_plain is None       # abstained
    assert result.decision_plain is None
    assert result.rejected_reason            # logged upstream


def test_invented_legislation_ref_is_rejected():
    reply = json.dumps({
        "summary_plain": "Companion to Ordinance 2026-99, raises base pay by 4 percent.",
        "decision_plain": "Whether to advance the pay change.",
    })
    result = interpret_item(FakeClient(reply), _item(), SOURCE)
    assert result.summary_plain is None


def test_malformed_json_abstains():
    result = interpret_item(FakeClient("i am not json"), _item(), SOURCE)
    assert result.summary_plain is None
    assert result.rejected_reason


def test_ungrounded_tokens_helper():
    assert ungrounded_tokens("pay rises 4 percent in 2027", SOURCE) == []
    bad = ungrounded_tokens("pay rises 12 percent for 500 people", SOURCE)
    assert "12" in bad and "500" in bad
```

- [ ] **Step 2: Run to verify failure** → `.venv/bin/pytest tests/test_agenda_interpret.py -q` → FAIL.

- [ ] **Step 3: Implement**

Add to `src/config.py` (next to the SUMMARY_* block):

```python
# Agenda interpretation (Pass A of item-centric coverage). Sonnet: citizens
# act on these summaries; the groundedness gate rejects rather than repairs.
AGENDA_INTERPRET_MODEL = "claude-sonnet-4-5"
AGENDA_INTERPRET_MAX_TOKENS = 600
```

Create `src/agenda_interpret.py`:

```python
"""LLM plain-language interpretation of agenda items, behind a groundedness gate.

Contract: the model explains WHAT an item is and WHAT IS BEING DECIDED, in
plain language, from the agenda title + attached legislation/staff text. It
never states procedure (stage/public comment — that's src/bodies.py) and its
output is rejected wholesale if any number or legislation ref it emits is not
present in the source text. Abstain-don't-guess, like llm_utils._name_is_anchored.
"""
import json
import re
from dataclasses import dataclass
from typing import Optional

from . import config
from .agenda_parse import ParsedItem

_SYSTEM = (
    "You explain city-council agenda items to ordinary residents. Plain, "
    "neutral language; no government jargon; no opinions. Use ONLY the "
    "provided source text — if it does not say something, do not say it. "
    "Reply with JSON only: {\"summary_plain\": one or two sentences on what "
    "this item is, \"decision_plain\": one sentence on what the council is "
    "actually deciding, or null if nothing is being decided}."
)

_NUM_RE = re.compile(r"\d[\d,.]*")
_REF_RE = re.compile(r"\b(?:Appropriation\s+)?(?:Ordinance|Resolution)\s+\d{4}-\d+")


@dataclass
class InterpretResult:
    summary_plain: Optional[str]
    decision_plain: Optional[str]
    rejected_reason: Optional[str] = None


def build_interpret_prompt(item: ParsedItem, source_text: str) -> str:
    return (
        f"Agenda item (verbatim): {item.title_raw}\n"
        f"Section: {item.section}\n\n"
        f"Source text (agenda + attached legislation/staff memo excerpts):\n"
        f"{source_text}"
    )


def ungrounded_tokens(generated: str, source: str) -> list[str]:
    """Numbers or legislation refs in `generated` that are absent from `source`."""
    source_norm = source.lower()
    bad: list[str] = []
    for tok in _NUM_RE.findall(generated):
        if tok.strip(",.").lower() not in source_norm:
            bad.append(tok.strip(",."))
    for m in _REF_RE.finditer(generated):
        if m.group(0).lower() not in source_norm:
            bad.append(m.group(0))
    return bad


def interpret_item(client, item: ParsedItem, source_text: str) -> InterpretResult:
    response = client.messages.create(
        model=config.AGENDA_INTERPRET_MODEL,
        max_tokens=config.AGENDA_INTERPRET_MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": build_interpret_prompt(item, source_text)}],
    )
    text = response.content[0].text
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return InterpretResult(None, None, rejected_reason="no JSON in reply")
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return InterpretResult(None, None, rejected_reason="malformed JSON")
    summary = payload.get("summary_plain") or None
    decision = payload.get("decision_plain") or None
    combined = " ".join(filter(None, [summary, decision]))
    bad = ungrounded_tokens(combined, f"{item.title_raw}\n{source_text}")
    if bad:
        return InterpretResult(
            None, None,
            rejected_reason=f"ungrounded tokens: {', '.join(sorted(set(bad)))}",
        )
    return InterpretResult(summary, decision)
```

- [ ] **Step 4: Run to verify pass** → `.venv/bin/pytest tests/test_agenda_interpret.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/agenda_interpret.py tests/test_agenda_interpret.py && git commit -m "feat: agenda item interpretation with groundedness gate (abstain-don't-guess)"
```

---

### Task 8: Scheduled publish — `src/publish.py`

**Files:**
- Modify: `src/publish.py` (new functions after `_replace_votes`, ~line 539; export `publish_scheduled_meeting` near `publish_meeting`, ~line 611)
- Test: `tests/test_publish.py` (extend — pure helpers only, per the file's own doc)

Design decisions encoded here:
- **Slug = the join key between Pass A and Pass B.** Scheduled slug format:
  `{body.slug}-{date}` (e.g. `bloomington-city-council-2026-07-29`). When the
  video is later processed, the operator/GUI must reuse this slug (the GUI's
  `derive_meeting_id` composes from kind/city/date similarly; a follow-up
  aligns them — for now the poller prints the slug loudly, and the meeting
  upsert is slug-keyed so a match flips the same row).
- **Never downgrade a published meeting.** If the slug's row has
  `status='published'`, the scheduled publisher must NOT touch the meeting row
  or its agenda items (the video pass owns them now).
- **Delete-then-insert for items** (like `_replace_votes`) keyed on meeting UUID.

- [ ] **Step 1: Write the failing tests (pure helpers only)**

Add to `tests/test_publish.py`:

```python
from src.models import AgendaItem
from src.publish import build_agenda_item_rows, scheduled_slug
from src.bodies import BLOOMINGTON_COMMON_COUNCIL


def test_scheduled_slug_is_body_plus_date():
    assert (
        scheduled_slug(BLOOMINGTON_COMMON_COUNCIL, "2026-07-29")
        == "bloomington-city-council-2026-07-29"
    )


def test_build_agenda_item_rows_orders_and_nulls():
    items = [
        AgendaItem(position=1, item_number="1", title_raw="ROLL CALL",
                   kind="procedural", source_url="https://x.gov/a.pdf"),
        AgendaItem(position=6, item_number="6A",
                   title_raw="Ordinance 2026-16 – Salaries", kind="ordinance",
                   legislation_ref="Ordinance 2026-16",
                   summary_plain="Raises pay 4 percent.",
                   stage="First reading", public_comment=False,
                   source_url="https://x.gov/a.pdf"),
    ]
    rows = build_agenda_item_rows("uuid-123", items)
    assert rows[0][0] == "uuid-123"          # meeting_id first
    assert rows[0][1] == 1                    # position
    assert rows[0][5] is None                 # legislation_ref null for roll call
    assert rows[1][2] == "6A"
    assert rows[1][12] == "upcoming"          # status literal
```

- [ ] **Step 2: Run to verify failure** → `.venv/bin/pytest tests/test_publish.py -q` → FAIL.

- [ ] **Step 3: Implement**

Add to `src/publish.py`:

```python
def scheduled_slug(body, date: str) -> str:
    """Slug for an agenda-published (Pass A) meeting: '{body.slug}-{date}'.

    This is the join key the video pass must reuse so its upsert flips this
    row to published instead of creating a duplicate."""
    return f"{body.slug}-{date}"


# Column order matches the INSERT in _replace_agenda_items below.
def build_agenda_item_rows(meeting_uuid: str, items) -> list[tuple]:
    rows = []
    for it in items:
        rows.append((
            meeting_uuid,
            it.position,
            it.item_number,
            it.title_raw,
            it.kind,
            it.legislation_ref,
            it.summary_plain,
            it.decision_plain,
            it.stage,
            it.public_comment,
            it.public_comment_note,
            it.source_url,
            "upcoming",
        ))
    return rows


def _replace_agenda_items(cur, meeting_uuid: str, items) -> int:
    """Delete-then-insert, like _replace_votes. Caller must have verified the
    meeting is NOT status='published' (the video pass owns items after that)."""
    cur.execute(
        "DELETE FROM meetings.agenda_items WHERE meeting_id = %s", (meeting_uuid,)
    )
    rows = build_agenda_item_rows(meeting_uuid, items)
    if rows:
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO meetings.agenda_items
               (meeting_id, position, item_number, title_raw, kind,
                legislation_ref, summary_plain, decision_plain, stage,
                public_comment, public_comment_note, source_url, status)
               VALUES %s""",
            rows,
        )
    return len(rows)


def publish_scheduled_meeting(body, date: str, title: str, starts_at: str,
                              source_url: str, items) -> Optional[str]:
    """Publish a future meeting + its agenda items (Pass A).

    Returns the meeting slug on success, None when skipped because the row is
    already published (video pass owns it). Idempotent: re-polls re-run this
    and delete-then-insert refreshes the items (agenda revisions/addenda).
    """
    _validate_date(date)
    slug = scheduled_slug(body, date)
    conn = psycopg2.connect(_require_db_url())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, status FROM meetings.meetings WHERE slug = %s",
                    (slug,),
                )
                row = cur.fetchone()
                if row and row[1] == "published":
                    return None
                if row:
                    meeting_uuid = row[0]
                    cur.execute(
                        """UPDATE meetings.meetings
                           SET title = %s, starts_at = %s, source_url = %s,
                               updated_at = now()
                           WHERE id = %s""",
                        (title, starts_at, source_url, meeting_uuid),
                    )
                else:
                    cur.execute(
                        """INSERT INTO meetings.meetings
                           (city, state, date, meeting_type, title, event_kind,
                            status, slug, source_url, starts_at,
                            created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s, 'scheduled', %s, %s, %s,
                                   now(), now())
                           RETURNING id""",
                        (body.city, body.state, date, body.meeting_type, title,
                         body.event_kind, slug, source_url, starts_at),
                    )
                    meeting_uuid = cur.fetchone()[0]
                _replace_agenda_items(cur, meeting_uuid, items)
    finally:
        conn.close()
    return slug
```

(Match the module's existing import style — `psycopg2.extras` is already imported for `_replace_votes`. If `meetings.meetings` INSERT column availability differs — e.g. `state` — mirror the columns `_upsert_meeting` actually uses at lines 294-331 and only add `starts_at`.)

- [ ] **Step 4: Run to verify pass** → `.venv/bin/pytest tests/test_publish.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/publish.py tests/test_publish.py && git commit -m "feat: publish_scheduled_meeting + _replace_agenda_items (Pass A publish)"
```

---

### Task 9: Poller entry point — `scripts/poll_agendas.py`

**Files:**
- Create: `scripts/poll_agendas.py`
- Create: `src/agenda_pipeline.py` (the testable orchestration)
- Test: `tests/test_agenda_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agenda_pipeline.py`:

```python
import json
from pathlib import Path

from src.agenda_pipeline import PollState, plan_work


def test_poll_state_round_trip(tmp_path):
    state = PollState(tmp_path / "state.json")
    assert state.marker_for("bloomington-city-council-2026-07-29") is None
    state.record("bloomington-city-council-2026-07-29", "https://x/17202|2026-07-27T10:02")
    state2 = PollState(tmp_path / "state.json")
    assert state2.marker_for("bloomington-city-council-2026-07-29") == "https://x/17202|2026-07-27T10:02"


def test_plan_work_skips_unchanged_and_agendaless():
    class M:  # minimal OnBoardMeeting stand-in
        def __init__(self, start, marker):
            self.start = start
            self.agenda_url = "https://x/a.pdf" if marker else None
            self.agenda_updated_marker = marker or ""
            self.title = "Common Council"

    meetings = [M("2026-07-29T18:30:00-04:00", "https://x/a.pdf|v1"),
                M("2026-08-05T18:30:00-04:00", None)]
    seen = {"bloomington-city-council-2026-07-29": "https://x/a.pdf|v1"}
    work, skipped = plan_work(meetings, seen, body_slug="bloomington-city-council")
    assert work == []                      # unchanged agenda → no work
    assert len(skipped) == 2               # one unchanged, one agenda-less (logged)

    work, _ = plan_work(meetings, {}, body_slug="bloomington-city-council")
    assert len(work) == 1                  # new agenda → work
    assert work[0].slug == "bloomington-city-council-2026-07-29"
```

- [ ] **Step 2: Run to verify failure** → `.venv/bin/pytest tests/test_agenda_pipeline.py -q` → FAIL.

- [ ] **Step 3: Implement `src/agenda_pipeline.py`**

```python
"""Orchestration for the agenda poller (pure parts; I/O stays in the script).

Change detection: OnBoard file `created`/`updated` timestamps form a marker
per meeting; unchanged marker → skip. State lives in a JSON file under the
CouncilScribe drive (atomic tempfile+replace, like src/checkpoint.py).
Failed fetch/parse/interpret must be LOGGED WORK, never a silent skip — the
coverage metric depends on it (spec: quality gates)."""
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WorkItem:
    slug: str
    meeting: object          # OnBoardMeeting
    date: str                # YYYY-MM-DD (from meeting.start, body-local)


class PollState:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._seen: dict[str, str] = {}
        if self._path.exists():
            self._seen = json.loads(self._path.read_text())

    def marker_for(self, slug: str) -> Optional[str]:
        return self._seen.get(slug)

    def record(self, slug: str, marker: str) -> None:
        self._seen[slug] = marker
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent)
        with os.fdopen(fd, "w") as fh:
            json.dump(self._seen, fh, indent=2)
        os.replace(tmp, self._path)


def plan_work(meetings, seen: dict, *, body_slug: str):
    """Split fetched meetings into (work, skipped-with-reasons)."""
    work: list[WorkItem] = []
    skipped: list[tuple[str, str]] = []
    for m in meetings:
        date = m.start[:10]
        slug = f"{body_slug}-{date}"
        if not m.agenda_url:
            skipped.append((slug, "no agenda posted yet"))
            continue
        if seen.get(slug) == m.agenda_updated_marker:
            skipped.append((slug, "agenda unchanged"))
            continue
        work.append(WorkItem(slug=slug, meeting=m, date=date))
    return work, skipped
```

- [ ] **Step 4: Implement `scripts/poll_agendas.py`**

```python
"""Poll upcoming Bloomington Common Council agendas and publish them (Pass A).

Usage:
    .venv/bin/python scripts/poll_agendas.py                # poll + publish
    .venv/bin/python scripts/poll_agendas.py --days 10      # wider window
    .venv/bin/python scripts/poll_agendas.py --dry-run      # no DB writes
    .venv/bin/python scripts/poll_agendas.py --no-interpret # skip LLM stage

Requires DATABASE_URL (+ ANTHROPIC_API_KEY unless --no-interpret) in .env.local.
Run cadence: agendas post the Friday before (sometimes 48h out) and addenda land
through meeting day — daily runs from ~6 days out are right (see spike findings
2026-07-27)."""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # the shared .env.local loader

load_env_local()

from src import config  # noqa: E402  (must follow env load)
from src.agenda_interpret import interpret_item
from src.agenda_parse import parse_agenda
from src.agenda_pipeline import PollState, plan_work
from src.bodies import BLOOMINGTON_COMMON_COUNCIL
from src.bodies import classify_item
from src.models import AgendaItem
from src.onboard import fetch_meetings_window
from src.pdf_text import extract_text
from src.publish import publish_scheduled_meeting


def _download(url: str, dest: Path) -> Path:
    import requests
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=(30, 120),
                        headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-interpret", action="store_true")
    args = parser.parse_args()

    body = BLOOMINGTON_COMMON_COUNCIL
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=args.days)).isoformat()
    meetings = fetch_meetings_window(start, end,
                                     title_prefix=body.meeting_title_prefix)
    drive = Path.home() / "CouncilScribe" / "agendas" / body.slug
    state = PollState(drive / "poll_state.json")
    seen = {m_slug: state.marker_for(m_slug) for m_slug in []}  # marker lookups happen per-slug below

    work, skipped = plan_work(
        meetings,
        {f"{body.slug}-{m.start[:10]}": state.marker_for(f"{body.slug}-{m.start[:10]}") for m in meetings},
        body_slug=body.slug,
    )
    for slug, reason in skipped:
        print(f"SKIP {slug}: {reason}")
    if not work:
        print(f"No new/changed agendas in {start}..{end}.")
        return 0

    client = None
    if not args.no_interpret:
        import anthropic
        client = anthropic.Anthropic()

    failures = 0
    for w in work:
        try:
            pdf = _download(w.meeting.agenda_url, drive / w.slug / "agenda.pdf")
            text = extract_text(pdf)
            parsed = parse_agenda(text)
            if not parsed:
                raise ValueError("agenda parsed to zero items")
            items = []
            for p in parsed:
                cls = classify_item(p, body)
                summary = decision = None
                if client is not None and cls.kind in ("ordinance", "resolution", "appointment"):
                    result = interpret_item(client, p, text)
                    if result.rejected_reason:
                        print(f"  GATE {w.slug} item {p.item_number}: {result.rejected_reason}")
                    summary, decision = result.summary_plain, result.decision_plain
                items.append(AgendaItem(
                    position=p.position, item_number=p.item_number,
                    title_raw=p.title_raw, kind=cls.kind,
                    legislation_ref=p.legislation_ref,
                    summary_plain=summary, decision_plain=decision,
                    stage=cls.stage, public_comment=cls.public_comment,
                    public_comment_note=cls.public_comment_note,
                    source_url=w.meeting.agenda_url,
                ))
            if args.dry_run:
                print(f"DRY-RUN {w.slug}: {len(items)} items "
                      f"({sum(1 for i in items if i.summary_plain)} interpreted)")
                continue
            title = f"{w.meeting.title} — {w.date}"
            published = publish_scheduled_meeting(
                body, w.date, title, w.meeting.start,
                w.meeting.agenda_url, items,
            )
            if published is None:
                print(f"SKIP {w.slug}: already published (video pass owns it)")
            else:
                print(f"PUBLISHED {published}: {len(items)} items")
                state.record(w.slug, w.meeting.agenda_updated_marker)
        except Exception as exc:  # coverage rule: loud, per-meeting, non-fatal
            failures += 1
            print(f"FAILED {w.slug}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

(Clean up the vestigial `seen = ...` line when implementing — build the marker dict once, as the `plan_work` call does.)

- [ ] **Step 5: Run the tests + a dry run**

Run: `.venv/bin/pytest tests/test_agenda_pipeline.py -q` → PASS.
Run: `.venv/bin/python scripts/poll_agendas.py --dry-run --no-interpret`
Expected: real OnBoard fetch; either "No new/changed agendas" (quiet week) or `DRY-RUN bloomington-city-council-YYYY-MM-DD: N items`. No DB writes.

- [ ] **Step 6: Commit**

```bash
git add scripts/poll_agendas.py src/agenda_pipeline.py tests/test_agenda_pipeline.py && git commit -m "feat: agenda poller entry point (change-detect, gate-logged, dry-run)"
```

---

### Task 10: Live E2E + PR

**Prerequisite: ev-accounts migration 1476 applied to prod (companion plan Task 6).**

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/pytest --tb=short -q`
Expected: all pass, no regressions.

- [ ] **Step 2: Live E2E — one real publish** (requires `DATABASE_URL` + `ANTHROPIC_API_KEY` in `.env.local`; **confirm with the user before the first prod write**)

```bash
.venv/bin/python scripts/poll_agendas.py --days 8
```

Expected: `PUBLISHED bloomington-city-council-YYYY-MM-DD: N items`. Verify via the API:

```bash
curl -s "$EV_ACCOUNTS_URL/api/meetings/upcoming" | python3 -m json.tool
```

Expected: the scheduled meeting with `startsAt`; then fetch its agenda items via `/api/meetings/<id>/agenda-items` and spot-check: legislation items carry `legislationRef`, `stage`, grounded `summaryPlain`; second-reading items have `publicComment: true`; no invented refs (compare against the real agenda PDF by eye).

- [ ] **Step 3: Idempotency check**

Re-run `scripts/poll_agendas.py --days 8`. Expected: `SKIP ...: agenda unchanged` — no duplicate rows (verify item count via the API is unchanged).

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin feat/bloomington-agenda-adapter
gh pr create --title "Bloomington agenda adapter: OnBoard poll -> parse -> interpret -> scheduled publish (Pass A)" --body "$(cat <<'EOF'
Pass A of the item-centric civic coverage spec (docs/superpowers/specs/2026-07-27-bloomington-item-centric-civic-coverage-design.md).

- src/onboard.py: OnBoard meetings JSON client (injected fetch, live-captured fixtures)
- src/pdf_text.py: pdfplumber extraction (new dep)
- src/agenda_parse.py: templated agenda parser (sections, lettered items, legislation refs, sponsors)
- src/bodies.py: per-body config; Bloomington section->stage/public-comment semantics encoded from council rules (never LLM)
- src/agenda_interpret.py: plain-language interpretation behind a groundedness gate (ungrounded numbers/refs -> abstain + logged)
- src/publish.py: publish_scheduled_meeting + _replace_agenda_items (slug-keyed, never downgrades a published row)
- scripts/poll_agendas.py: change-detecting poller (OnBoard file timestamps), dry-run mode, loud per-meeting failures

Live-validated: real agenda published end-to-end and served by /api/meetings/upcoming + /:id/agenda-items.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
