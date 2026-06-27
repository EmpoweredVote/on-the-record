# Bulk Unlinked-Speaker Review Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-command bulk flow (`--bulk-relink-scan` / `--bulk-relink-apply`) that enumerates every unlinked named speaker across all meetings into an editable YAML review file with pre-filled suggestions, then applies the operator's approved links through the existing relink engine (relink → fold profile → publish), auto-resolving `race_id` for debate meetings.

**Architecture:** Pure logic + YAML (de)serialization live in a new `src/bulk_relink.py` (mirroring how `src/relink.py` separates logic from the `run_local.py` orchestrator). A small `race_id` lookup reuses the publish Postgres connection. Two new `run_local.py` subcommand handlers do the file/DB I/O and reuse the shipped engine functions (`resolve_link_target`, `relink_in_meeting`, `rekey_profile_for_link`) and the existing `_publish_meeting_standalone` / `_trigger_render_deploy`.

**Tech Stack:** Python 3, pytest, PyYAML, psycopg2 (already used by `src/publish.py`). Run all commands with the repo venv: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python` (the worktree shares it; system `python3` lacks deps).

**Spec:** `docs/superpowers/specs/2026-06-26-bulk-relink-surface-design.md`

---

## File Structure

- **Create `src/bulk_relink.py`** — pure logic + YAML doc (de)serialization: `UnlinkedSpeaker` dataclass, `enumerate_unlinked`, `suggest_link`, `build_review_doc`, `parse_review_doc` (+ `ReviewDecision`), and the `DECISION_*` constants.
- **Modify `src/publish.py`** — add `resolve_race_id_for_politicians(cur, politician_ids)` (reuses the publish DB connection shape; lives beside `_resolve_chamber_id`).
- **Modify `run_local.py`** — add `_bulk_relink_scan(args)` and `_bulk_relink_apply(args)` handlers; register `--bulk-relink-scan` / `--bulk-relink-apply` / `--out` argparse options; dispatch them in `main`.
- **Modify `requirements.txt`** — declare `PyYAML` (already installed, currently undeclared).
- **Create `tests/test_bulk_relink.py`** — unit tests for the pure module + race resolver.
- **Create `tests/test_bulk_relink_apply.py`** — integration test for the apply orchestrator over temp meeting dirs with mocks.

Convention note: the repo imports modules as `from src.foo import bar` and tests import `from src.foo import bar` (see `tests/test_relink.py`). Follow that exactly.

---

## Task 1: Declare PyYAML dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Inspect the current file**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -c "import yaml; print(yaml.__version__)"`
Expected: prints `6.0.3` (confirms it is installed in the venv).

Then read `requirements.txt` to find where to add the line (keep alphabetical/grouped ordering if the file uses it; otherwise append).

- [ ] **Step 2: Add the dependency**

Add this line to `requirements.txt` (place it near other top-level libs, keeping any existing ordering):

```
PyYAML>=6.0
```

- [ ] **Step 3: Verify the import resolves under the venv**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -c "import yaml; print('ok')"`
Expected: prints `ok`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build: declare PyYAML dependency (used by bulk-relink review file)"
```

---

## Task 2: `UnlinkedSpeaker` dataclass + decision constants

**Files:**
- Create: `src/bulk_relink.py`
- Test: `tests/test_bulk_relink.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bulk_relink.py` with:

```python
from __future__ import annotations

from src.bulk_relink import (
    DECISION_LINK,
    DECISION_REVIEW,
    DECISION_SKIP,
    UnlinkedSpeaker,
)


def test_decision_constants_have_expected_string_values():
    assert DECISION_LINK == "link"
    assert DECISION_REVIEW == "review"
    assert DECISION_SKIP == "skip"


def test_unlinked_speaker_defaults():
    s = UnlinkedSpeaker(display_name="Steve Hilton", normalized_name="steve hilton")
    assert s.appearances == []
    assert s.meeting_count == 0
    assert s.has_voice_profile is False
    assert s.known_id is None
    assert s.decision == DECISION_REVIEW
    assert s.candidates == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bulk_relink'`

- [ ] **Step 3: Write minimal implementation**

Create `src/bulk_relink.py`:

```python
"""Bulk review of unlinked named speakers across all meetings.

Pure logic + YAML (de)serialization backing `run_local.py --bulk-relink-scan`
and `--bulk-relink-apply`. No file or network I/O except the essentials name
search injected into `suggest_link`; the orchestrators in run_local.py do the
directory walk, file writes, profile DB, publish, and deploy. Mirrors how
`src/relink.py` keeps logic separate from the run_local orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DECISION_LINK = "link"
DECISION_REVIEW = "review"
DECISION_SKIP = "skip"
VALID_DECISIONS = (DECISION_LINK, DECISION_REVIEW, DECISION_SKIP)


@dataclass
class UnlinkedSpeaker:
    display_name: str
    normalized_name: str
    appearances: list[tuple[str, str]] = field(default_factory=list)  # (meeting_id, label)
    meeting_count: int = 0
    has_voice_profile: bool = False
    known_id: Optional[str] = None
    decision: str = DECISION_REVIEW
    candidates: list[dict] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/bulk_relink.py tests/test_bulk_relink.py
git commit -m "feat(bulk-relink): UnlinkedSpeaker dataclass + decision constants"
```

---

## Task 3: `enumerate_unlinked` — group unlinked speakers, capture `known_id`

**Files:**
- Modify: `src/bulk_relink.py`
- Test: `tests/test_bulk_relink.py`

Context: `SpeakerMapping` (`src/models.py:67`) has `speaker_name`, `politician_id`, `speaker_status` (`None` normal / `'unidentified'` / `'non_speaker'`), and `local_slug`. `Meeting` (`src/models.py:235`) has `meeting_id` and `speakers: dict[label -> SpeakerMapping]`. `ProfileDB` (`src/enroll.py:71`) has `.profiles: dict[key -> StoredProfile]`. `enroll._name_to_slug(name)` produces the name-slug key. Normalization = `name.strip().lower()` (same as `relink_in_meeting`, `src/relink.py:44`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bulk_relink.py`:

```python
import numpy as np

from src.bulk_relink import enumerate_unlinked
from src.enroll import EmbeddingRecord, ProfileDB, StoredProfile
from src.models import Meeting, SpeakerMapping


def _meeting(mid, speakers):
    return Meeting(meeting_id=mid, city="X", date="2026-04-01", speakers=speakers)


def _unlinked(label, name, status=None, local_slug=None):
    return SpeakerMapping(speaker_label=label, speaker_name=name,
                          speaker_status=status, local_slug=local_slug)


def _linked(label, name, pid):
    return SpeakerMapping(speaker_label=label, speaker_name=name, politician_id=pid)


def _profile_db(*name_slugs):
    profiles = {
        slug: StoredProfile(
            speaker_id=slug, display_name=slug,
            embeddings=[EmbeddingRecord(np.array([1.0]), "m", 1)],
        )
        for slug in name_slugs
    }
    return ProfileDB(profiles=profiles)


def test_enumerate_groups_by_name_and_counts_meetings():
    meetings = [
        _meeting("m1", {"S0": _unlinked("S0", "Katie Porter")}),
        _meeting("m2", {"S0": _unlinked("S0", "katie porter"), "S1": _unlinked("S1", "Tom Steyer")}),
    ]
    rows = enumerate_unlinked(meetings, ProfileDB(profiles={}))
    by_name = {r.normalized_name: r for r in rows}
    assert set(by_name) == {"katie porter", "tom steyer"}
    porter = by_name["katie porter"]
    assert porter.meeting_count == 2
    assert sorted(porter.appearances) == [("m1", "S0"), ("m2", "S0")]


def test_enumerate_excludes_linked_unidentified_nonspeaker_and_local():
    meetings = [_meeting("m1", {
        "S0": _linked("S0", "Already Linked", "uuid-x"),
        "S1": _unlinked("S1", "Ghost", status="unidentified"),
        "S2": _unlinked("S2", "Applause", status="non_speaker"),
        "S3": _unlinked("S3", "Local Person", local_slug="local-person"),
        "S4": _unlinked("S4", "Real Candidate"),
    })]
    rows = enumerate_unlinked(meetings, ProfileDB(profiles={}))
    assert [r.normalized_name for r in rows] == ["real candidate"]


def test_enumerate_sets_has_voice_profile_from_name_slug():
    from src.enroll import _name_to_slug
    meetings = [_meeting("m1", {"S0": _unlinked("S0", "Steve Hilton")})]
    db = _profile_db(_name_to_slug("Steve Hilton"))
    rows = enumerate_unlinked(meetings, db)
    assert rows[0].has_voice_profile is True


def test_enumerate_known_id_from_linked_appearance_elsewhere():
    # Steve linked in his interview (m1), unlinked in a debate (m2).
    meetings = [
        _meeting("m1", {"S0": _linked("S0", "Steve Hilton", "uuid-hilton")}),
        _meeting("m2", {"S0": _unlinked("S0", "Steve Hilton")}),
    ]
    rows = enumerate_unlinked(meetings, ProfileDB(profiles={}))
    assert len(rows) == 1
    assert rows[0].known_id == "uuid-hilton"


def test_enumerate_known_id_none_when_conflicting_ids():
    meetings = [
        _meeting("m1", {"S0": _linked("S0", "Jane Roe", "uuid-a")}),
        _meeting("m2", {"S0": _linked("S0", "Jane Roe", "uuid-b")}),
        _meeting("m3", {"S0": _unlinked("S0", "Jane Roe")}),
    ]
    rows = enumerate_unlinked(meetings, ProfileDB(profiles={}))
    assert rows[0].known_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k enumerate`
Expected: FAIL with `ImportError: cannot import name 'enumerate_unlinked'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/bulk_relink.py` (top imports + function):

```python
def _normalize(name: str) -> str:
    return (name or "").strip().lower()


def enumerate_unlinked(meetings, profile_db) -> list[UnlinkedSpeaker]:
    """Group unlinked named speaker mappings across meetings, by normalized name.

    Includes a mapping when speaker_name is set, politician_id is None,
    speaker_status is normal (not 'unidentified'/'non_speaker'), and local_slug
    is None. Captures known_id = the politician_id the same name is already
    linked to elsewhere (None if zero or several distinct ids). Pure; the caller
    supplies loaded Meeting objects and the profile DB.
    """
    from src.enroll import _name_to_slug

    # First pass: ids each name is already linked to (from linked mappings).
    linked_ids: dict[str, set[str]] = {}
    for meeting in meetings:
        for mapping in meeting.speakers.values():
            if mapping.politician_id and mapping.speaker_name:
                linked_ids.setdefault(_normalize(mapping.speaker_name), set()).add(
                    mapping.politician_id
                )

    rows: dict[str, UnlinkedSpeaker] = {}
    for meeting in meetings:
        for mapping in meeting.speakers.values():
            if not mapping.speaker_name:
                continue
            if mapping.politician_id is not None:
                continue
            if mapping.speaker_status in ("unidentified", "non_speaker"):
                continue
            if mapping.local_slug is not None:
                continue
            key = _normalize(mapping.speaker_name)
            row = rows.get(key)
            if row is None:
                ids = linked_ids.get(key, set())
                row = UnlinkedSpeaker(
                    display_name=mapping.speaker_name.strip(),
                    normalized_name=key,
                    has_voice_profile=_name_to_slug(mapping.speaker_name) in profile_db.profiles,
                    known_id=next(iter(ids)) if len(ids) == 1 else None,
                )
                rows[key] = row
            row.appearances.append((meeting.meeting_id, mapping.speaker_label))
            row.meeting_count = len({m for m, _ in row.appearances})
    return list(rows.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k enumerate`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/bulk_relink.py tests/test_bulk_relink.py
git commit -m "feat(bulk-relink): enumerate_unlinked groups speakers + captures known_id"
```

---

## Task 4: `suggest_link` — known-id fast path, else essentials search

**Files:**
- Modify: `src/bulk_relink.py`
- Test: `tests/test_bulk_relink.py`

Context: `search_politicians(q)` (`src/essentials_client.py:143`) returns a list of normalized dicts with keys `politician_id`, `politician_slug`, `full_name`, `office_title`, `district_label`, `is_incumbent`, `government_name`; raises `EssentialsClientError` on transport/HTTP/parse failure. Mirror `resolve_link_target` (`src/relink.py:57`): exactly-one match auto-resolves, zero/several do not.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bulk_relink.py`:

```python
import pytest

from src.bulk_relink import suggest_link
from src.essentials_client import EssentialsClientError


def _cand(pid, name="Cand"):
    return {"politician_id": pid, "politician_slug": None, "full_name": name,
            "office_title": "", "district_label": "", "is_incumbent": False,
            "government_name": ""}


def _speaker(name, known_id=None):
    return UnlinkedSpeaker(display_name=name, normalized_name=name.lower(), known_id=known_id)


def test_suggest_known_id_skips_search():
    calls = []

    def search(q, **kw):
        calls.append(q)
        return []

    decision, candidates = suggest_link(_speaker("Steve Hilton", known_id="uuid-h"), search=search)
    assert decision == DECISION_LINK
    assert candidates[0]["politician_id"] == "uuid-h"
    assert calls == []  # fast path: search never called


def test_suggest_single_match_links():
    decision, candidates = suggest_link(
        _speaker("Steve Hilton"), search=lambda q, **kw: [_cand("uuid-1", "Steve Hilton")])
    assert decision == DECISION_LINK
    assert candidates == [_cand("uuid-1", "Steve Hilton")]


def test_suggest_zero_matches_reviews():
    decision, candidates = suggest_link(_speaker("Nobody"), search=lambda q, **kw: [])
    assert decision == DECISION_REVIEW
    assert candidates == []


def test_suggest_multiple_matches_reviews_with_candidates():
    cands = [_cand("uuid-1"), _cand("uuid-2")]
    decision, candidates = suggest_link(_speaker("John Smith"), search=lambda q, **kw: cands)
    assert decision == DECISION_REVIEW
    assert candidates == cands


def test_suggest_propagates_api_error():
    def boom(q, **kw):
        raise EssentialsClientError("down")

    with pytest.raises(EssentialsClientError):
        suggest_link(_speaker("Steve Hilton"), search=boom)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k suggest`
Expected: FAIL with `ImportError: cannot import name 'suggest_link'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/bulk_relink.py`. Put the default import at module top (`from src.essentials_client import search_politicians as _search_politicians`) and reference it as the default arg:

```python
from src.essentials_client import search_politicians as _search_politicians


def suggest_link(speaker, *, search=_search_politicians) -> tuple[str, list[dict]]:
    """Suggest a decision + candidates for an UnlinkedSpeaker.

    Fast path: a known_id (the name is already linked elsewhere) auto-resolves to
    DECISION_LINK with a stub candidate carrying that id, no network call.
    Otherwise mirror resolve_link_target: exactly one search match -> LINK; zero
    or several -> REVIEW. EssentialsClientError propagates (an outage must not be
    silently rendered as 'no matches').
    """
    if speaker.known_id:
        stub = {"politician_id": speaker.known_id, "politician_slug": None,
                "full_name": speaker.display_name, "office_title": "",
                "district_label": "", "is_incumbent": False, "government_name": ""}
        return DECISION_LINK, [stub]

    matches = search(speaker.display_name)
    if len(matches) == 1:
        return DECISION_LINK, matches
    return DECISION_REVIEW, matches
```

Note: place the `from src.essentials_client import ...` line with the other top-of-file imports, not inside the function.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k suggest`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/bulk_relink.py tests/test_bulk_relink.py
git commit -m "feat(bulk-relink): suggest_link known-id fast path + essentials search"
```

---

## Task 5: `build_review_doc` — speakers → YAML-serializable dict

**Files:**
- Modify: `src/bulk_relink.py`
- Test: `tests/test_bulk_relink.py`

Design of the doc: a top-level mapping `{"speakers": [ {...}, ... ]}`. Each entry: `name`, `meeting_count`, `has_voice_profile`, `decision`, `politician_id` (the single candidate's id for `link`, else `None`), and — only for `review` rows — `candidates` (a terse list of `{id, name, office, district}`). `link` rows omit `candidates` to stay terse.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bulk_relink.py`:

```python
from src.bulk_relink import build_review_doc


def _row(name, decision, candidates, **kw):
    s = UnlinkedSpeaker(display_name=name, normalized_name=name.lower(),
                        decision=decision, candidates=candidates, **kw)
    return s


def test_build_doc_link_row_has_id_and_no_candidates():
    rows = [_row("Steve Hilton", DECISION_LINK,
                 [{"politician_id": "uuid-h", "full_name": "Steve Hilton",
                   "office_title": "Candidate", "district_label": "CA"}],
                 meeting_count=4, has_voice_profile=True)]
    doc = build_review_doc(rows)
    entry = doc["speakers"][0]
    assert entry["name"] == "Steve Hilton"
    assert entry["meeting_count"] == 4
    assert entry["has_voice_profile"] is True
    assert entry["decision"] == "link"
    assert entry["politician_id"] == "uuid-h"
    assert "candidates" not in entry


def test_build_doc_review_row_has_null_id_and_candidate_hints():
    rows = [_row("Katie Porter", DECISION_REVIEW, [
        {"politician_id": "uuid-1", "full_name": "Katie Porter",
         "office_title": "U.S. Representative", "district_label": "CA-47"},
        {"politician_id": "uuid-2", "full_name": "Katie Porter",
         "office_title": "Senator", "district_label": "CA"},
    ])]
    doc = build_review_doc(rows)
    entry = doc["speakers"][0]
    assert entry["decision"] == "review"
    assert entry["politician_id"] is None
    assert entry["candidates"] == [
        {"id": "uuid-1", "name": "Katie Porter", "office": "U.S. Representative", "district": "CA-47"},
        {"id": "uuid-2", "name": "Katie Porter", "office": "Senator", "district": "CA"},
    ]


def test_build_doc_is_yaml_round_trippable():
    import yaml
    rows = [_row("Katie Porter", DECISION_REVIEW,
                 [{"politician_id": "uuid-1", "full_name": "Katie Porter",
                   "office_title": "Rep", "district_label": "CA-47"}])]
    doc = build_review_doc(rows)
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    assert yaml.safe_load(text) == doc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k build_doc`
Expected: FAIL with `ImportError: cannot import name 'build_review_doc'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/bulk_relink.py`:

```python
def build_review_doc(speakers) -> dict:
    """Build a YAML-serializable review document from UnlinkedSpeaker rows.

    link rows carry the chosen politician_id and omit candidates (terse);
    review rows carry politician_id=None plus a compact candidates hint list.
    """
    out = []
    for s in speakers:
        entry = {
            "name": s.display_name,
            "meeting_count": s.meeting_count,
            "has_voice_profile": s.has_voice_profile,
            "decision": s.decision,
        }
        if s.decision == DECISION_LINK and s.candidates:
            entry["politician_id"] = s.candidates[0]["politician_id"]
        else:
            entry["politician_id"] = None
            entry["candidates"] = [
                {
                    "id": c.get("politician_id"),
                    "name": c.get("full_name", ""),
                    "office": c.get("office_title", ""),
                    "district": c.get("district_label", ""),
                }
                for c in s.candidates
            ]
        out.append(entry)
    return {"speakers": out}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k build_doc`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/bulk_relink.py tests/test_bulk_relink.py
git commit -m "feat(bulk-relink): build_review_doc serializes speakers to YAML doc"
```

---

## Task 6: `parse_review_doc` — validate + extract operator decisions

**Files:**
- Modify: `src/bulk_relink.py`
- Test: `tests/test_bulk_relink.py`

Design: `ReviewDecision = (name, decision, politician_id)`. Validation rules: `decision` must be in `VALID_DECISIONS`; a `link` row must carry a syntactically valid UUID `politician_id` (use `uuid.UUID(str(...))`); `review`/`skip` rows need no id (id ignored). On any violation, raise `BulkRelinkParseError` naming the offending row. Missing/empty `speakers` key → empty list (not an error).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bulk_relink.py`:

```python
from src.bulk_relink import BulkRelinkParseError, ReviewDecision, parse_review_doc

_UUID = "9a60d603-194d-410f-ae01-85bd6293f1a7"


def test_parse_extracts_link_review_skip():
    doc = {"speakers": [
        {"name": "Steve Hilton", "decision": "link", "politician_id": _UUID},
        {"name": "Katie Porter", "decision": "review", "politician_id": None},
        {"name": "Moderator", "decision": "skip"},
    ]}
    rows = parse_review_doc(doc)
    assert rows == [
        ReviewDecision("Steve Hilton", "link", _UUID),
        ReviewDecision("Katie Porter", "review", None),
        ReviewDecision("Moderator", "skip", None),
    ]


def test_parse_empty_or_missing_speakers_returns_empty():
    assert parse_review_doc({}) == []
    assert parse_review_doc({"speakers": []}) == []


def test_parse_rejects_unknown_decision():
    with pytest.raises(BulkRelinkParseError) as ei:
        parse_review_doc({"speakers": [{"name": "X", "decision": "approve"}]})
    assert "X" in str(ei.value)


def test_parse_rejects_link_without_uuid():
    with pytest.raises(BulkRelinkParseError) as ei:
        parse_review_doc({"speakers": [{"name": "Steve", "decision": "link", "politician_id": None}]})
    assert "Steve" in str(ei.value)


def test_parse_rejects_link_with_malformed_uuid():
    with pytest.raises(BulkRelinkParseError) as ei:
        parse_review_doc({"speakers": [{"name": "Steve", "decision": "link", "politician_id": "not-a-uuid"}]})
    assert "Steve" in str(ei.value)


def test_parse_rejects_row_without_name():
    with pytest.raises(BulkRelinkParseError):
        parse_review_doc({"speakers": [{"decision": "skip"}]})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k parse`
Expected: FAIL with `ImportError: cannot import name 'BulkRelinkParseError'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/bulk_relink.py` (add `import uuid` and `from typing import NamedTuple` at top, or use a dataclass — use a `NamedTuple` so tuple equality in tests works):

```python
import uuid
from typing import NamedTuple


class ReviewDecision(NamedTuple):
    name: str
    decision: str
    politician_id: Optional[str]


class BulkRelinkParseError(Exception):
    """A review file row failed validation; message names the offending row."""


def parse_review_doc(data) -> list[ReviewDecision]:
    """Validate a parsed review doc and return the operator's decisions.

    decision must be one of VALID_DECISIONS; a 'link' row must carry a valid
    UUID politician_id; 'review'/'skip' rows ignore the id. Raises
    BulkRelinkParseError (naming the row) on any violation.
    """
    rows: list[ReviewDecision] = []
    for raw in (data or {}).get("speakers", []) or []:
        name = raw.get("name")
        if not name:
            raise BulkRelinkParseError(f"review row missing 'name': {raw!r}")
        decision = raw.get("decision")
        if decision not in VALID_DECISIONS:
            raise BulkRelinkParseError(
                f"{name}: invalid decision {decision!r} (expected one of {VALID_DECISIONS})")
        pid = raw.get("politician_id")
        if decision == DECISION_LINK:
            if pid is None:
                raise BulkRelinkParseError(f"{name}: decision 'link' requires a politician_id")
            try:
                uuid.UUID(str(pid))
            except (ValueError, AttributeError, TypeError):
                raise BulkRelinkParseError(f"{name}: politician_id {pid!r} is not a valid UUID")
        else:
            pid = None
        rows.append(ReviewDecision(name, decision, pid))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k parse`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full module test file**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v`
Expected: PASS (all tests from Tasks 2–6)

- [ ] **Step 6: Commit**

```bash
git add src/bulk_relink.py tests/test_bulk_relink.py
git commit -m "feat(bulk-relink): parse_review_doc validates + extracts decisions"
```

---

## Task 7: `resolve_race_id_for_politicians` — DB lookup for debate meetings

**Files:**
- Modify: `src/publish.py`
- Test: `tests/test_bulk_relink.py`

Context: `src/publish.py` connects via `DATABASE_URL` (psycopg2) and already queries `essentials.*` (see `_resolve_chamber_id`, `src/publish.py:118`, which does `cur.execute(...); rows = cur.fetchall()` and treats `len(rows) != 1` as "can't pin"). Follow that exact shape. The function takes an open cursor `cur` (so it's unit-testable with a fake cursor) and a list of linked `politician_id`s; queries `essentials.race_candidates` for the distinct `race_id`(s) those candidates belong to; returns the id only if exactly one distinct race results.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bulk_relink.py`:

```python
from src.publish import resolve_race_id_for_politicians


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None
        self.params = None

    def execute(self, sql, params=None):
        self.executed = sql
        self.params = params

    def fetchall(self):
        return self._rows


def test_resolve_race_single_distinct_race():
    cur = _FakeCursor([("race-1",)])
    assert resolve_race_id_for_politicians(cur, ["pol-a", "pol-b"]) == "race-1"


def test_resolve_race_none_when_no_rows():
    cur = _FakeCursor([])
    assert resolve_race_id_for_politicians(cur, ["pol-a"]) is None


def test_resolve_race_none_when_multiple_distinct_races():
    cur = _FakeCursor([("race-1",), ("race-2",)])
    assert resolve_race_id_for_politicians(cur, ["pol-a", "pol-b"]) is None


def test_resolve_race_none_when_empty_politician_list():
    cur = _FakeCursor([("race-1",)])
    # No politician ids -> no query, no race.
    assert resolve_race_id_for_politicians(cur, []) is None
    assert cur.executed is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k resolve_race`
Expected: FAIL with `ImportError: cannot import name 'resolve_race_id_for_politicians'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/publish.py`, directly after `_resolve_chamber_id` (around `src/publish.py:138`):

```python
def resolve_race_id_for_politicians(cur, politician_ids) -> Optional[str]:
    """Find the single essentials race a set of linked politicians belong to.

    Used to unblock debate publishing: event_kind='debate' meetings require a
    race_id, but older imports left it NULL. Given the politician_ids linked in
    a meeting, look up their race_candidates rows. Returns the race_id only when
    exactly one distinct race results; zero or several -> None (the caller
    reports it and skips, as ambiguity must not be auto-picked). Mirrors the
    _resolve_chamber_id "exactly one or give up" shape.
    """
    ids = [pid for pid in (politician_ids or []) if pid]
    if not ids:
        return None
    cur.execute(
        """
        SELECT DISTINCT race_id
        FROM essentials.race_candidates
        WHERE politician_id = ANY(%s)
        LIMIT 2
        """,
        (ids,),
    )
    rows = cur.fetchall()
    if len(rows) != 1:
        return None
    return str(rows[0][0])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -v -k resolve_race`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/publish.py tests/test_bulk_relink.py
git commit -m "feat(bulk-relink): resolve_race_id_for_politicians for debate publishing"
```

---

## Task 8: `_bulk_relink_scan` orchestrator + argparse wiring

**Files:**
- Modify: `run_local.py` (add handler near `_relink_person` ~line 1886; register args near line 3161; dispatch near line 3377)
- Test: manual smoke (orchestrator does file/dir I/O; pure logic already covered by Tasks 2–6)

Context: `_relink_person` (`run_local.py:1886`) shows the canonical walk: `dirs = sorted(d for d in config.MEETINGS_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))`, then `Meeting.from_dict(json.load(f))` on each `transcript_named.json`. `load_profiles()` is imported from `src.enroll`. argparse options are registered in the big block ending ~line 3161; dispatch happens in `main` as `if args.X: _handler(); return` (~line 3375).

- [ ] **Step 1: Add the argparse options**

In `run_local.py`, immediately after the `--deploy` argument (`run_local.py:3160-3161`), add:

```python
    parser.add_argument("--bulk-relink-scan", action="store_true",
                        help="Enumerate every unlinked named speaker across all meetings into "
                             "an editable YAML review file with suggested essentials matches")
    parser.add_argument("--bulk-relink-apply", metavar="REVIEW_FILE",
                        help="Apply approved links from a bulk-relink review file: relink "
                             "transcripts, fold voice profiles, re-publish (auto-resolving "
                             "debate race_id), and optionally redeploy")
    parser.add_argument("--out", metavar="PATH", default="bulk_relink_review.yaml",
                        help="Output path for --bulk-relink-scan (default: ./bulk_relink_review.yaml)")
```

- [ ] **Step 2: Add the scan handler**

In `run_local.py`, add directly above `_relink_person` (before `run_local.py:1886`):

```python
def _bulk_relink_scan(args) -> None:
    """Enumerate unlinked named speakers into an editable YAML review file."""
    import yaml

    from src import config
    from src.bulk_relink import (
        DECISION_LINK, build_review_doc, enumerate_unlinked, suggest_link,
    )
    from src.enroll import load_profiles
    from src.essentials_client import EssentialsClientError
    from src.models import Meeting

    db = load_profiles()
    meetings = []
    dirs = sorted(d for d in config.MEETINGS_DIR.iterdir()
                  if d.is_dir() and not d.name.startswith("."))
    for mdir in dirs:
        named = mdir / "transcript_named.json"
        if not named.exists():
            continue
        with open(named, "r", encoding="utf-8") as f:
            meetings.append(Meeting.from_dict(json.load(f)))

    speakers = enumerate_unlinked(meetings, db)
    speakers.sort(key=lambda s: (-s.meeting_count, s.normalized_name))

    try:
        for s in speakers:
            s.decision, s.candidates = suggest_link(s)
    except EssentialsClientError as exc:
        print(f"Essentials search failed ({exc}); aborting scan (no file written).")
        sys.exit(2)

    doc = build_review_doc(speakers)
    header = (
        "# Bulk relink review — edit then apply with:\n"
        f"#   python run_local.py --bulk-relink-apply {args.out}\n"
        "# For rows marked `review`: pick a candidate, set politician_id, and\n"
        "# change decision to `link`. Use `skip` to leave a speaker unlinked\n"
        "# (e.g. a moderator or a non-essentials local person).\n"
    )
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)

    linked = sum(1 for s in speakers if s.decision == DECISION_LINK)
    print(f"Wrote {len(speakers)} unlinked speaker(s) to {args.out}")
    print(f"  {linked} auto-approved (link), {len(speakers) - linked} need review")
```

- [ ] **Step 3: Dispatch the handler**

In `main`, immediately after the `--relink-person` dispatch block (`run_local.py:3375-3377`), add:

```python
    if args.bulk_relink_scan:
        _bulk_relink_scan(args)
        return
```

- [ ] **Step 4: Verify CLI parses and the handler is reachable (no meetings needed)**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python run_local.py --help`
Expected: help text includes `--bulk-relink-scan`, `--bulk-relink-apply`, `--out`.

- [ ] **Step 5: Smoke against real meetings (writes a file to scratchpad, not cwd)**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/.claude/worktrees/funny-yonath-7e9bd7
set -a; . ./.env.local 2>/dev/null; set +a
.venv/bin/python run_local.py --bulk-relink-scan --out /tmp/bulk_relink_smoke.yaml || \
  /Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python run_local.py --bulk-relink-scan --out /tmp/bulk_relink_smoke.yaml
```
Expected: prints a count summary and writes `/tmp/bulk_relink_smoke.yaml`. Open the file: it should list real unlinked speakers (e.g. CA debate candidates), with `link` rows carrying a `politician_id` and `review` rows carrying `candidates`. (If essentials is unreachable it exits 2 with a clear message — that's correct behavior, retry when reachable.)

- [ ] **Step 6: Commit**

```bash
git add run_local.py
git commit -m "feat(bulk-relink): --bulk-relink-scan writes the YAML review file"
```

---

## Task 9: `_bulk_relink_apply` orchestrator + dispatch

**Files:**
- Modify: `run_local.py` (add handler after `_bulk_relink_scan`; dispatch after the scan dispatch)
- Test: `tests/test_bulk_relink_apply.py` (integration, with mocks)

Context: reuse `resolve_link_target` / `relink_in_meeting` / `rekey_profile_for_link` (`src/relink.py`), `load_profiles`/`save_profiles` (`src/enroll.py`), `_publish_meeting_standalone` and `_trigger_render_deploy` and `_may_publish` (already in `run_local.py`), and `PipelineState` (`src/checkpoint.py`, used in `_relink_person`). The apply loop closely mirrors `_relink_person` (`run_local.py:1886`) but iterates over multiple approved names and adds race_id resolution before publishing debate meetings.

This task is large; build the handler in one implementation step (it's a single coherent orchestrator), then cover it with an integration test that mocks the network/DB/publish boundaries.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_bulk_relink_apply.py`:

```python
from __future__ import annotations

import json

import yaml

import run_local
from src.enroll import ProfileDB
from src.models import Meeting, SpeakerMapping

_UUID = "9a60d603-194d-410f-ae01-85bd6293f1a7"


def _write_meeting(meeting_dir, meeting):
    meeting_dir.mkdir(parents=True, exist_ok=True)
    with open(meeting_dir / "transcript_named.json", "w", encoding="utf-8") as f:
        json.dump(meeting.to_dict(), f, indent=2)


def _meeting(mid, name):
    return Meeting(meeting_id=mid, city="X", date="2026-04-01",
                   speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name=name)})


def _args(review_file, **over):
    import argparse
    ns = argparse.Namespace(
        bulk_relink_apply=str(review_file), dry_run=False,
        publish_anyway=False, deploy=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_apply_links_approved_and_skips_review(tmp_path, monkeypatch):
    meetings_root = tmp_path / "meetings"
    _write_meeting(meetings_root / "m1", _meeting("m1", "Steve Hilton"))
    _write_meeting(meetings_root / "m2", _meeting("m2", "Katie Porter"))

    # Point the pipeline at the temp meetings dir (same module object as src.config).
    monkeypatch.setattr(run_local.config, "MEETINGS_DIR", meetings_root)

    # Stub the essentials display lookup used by resolve_link_target.
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [{"politician_id": _UUID, "politician_slug": None,
                                          "full_name": "Steve Hilton"}])

    # No-op the profile DB boundaries. The handler imports these locally from
    # src.enroll, so patch them THERE (not on run_local) or save_profiles would
    # write the real production profile DB.
    monkeypatch.setattr("src.enroll.load_profiles", lambda: ProfileDB(profiles={}))
    monkeypatch.setattr("src.enroll.save_profiles", lambda db: None)

    # Publish + deploy are module-level names in run_local, called bare.
    published = []
    monkeypatch.setattr(run_local, "_publish_meeting_standalone",
                        lambda mid, anyway=False: published.append(mid))
    monkeypatch.setattr(run_local, "_trigger_render_deploy", lambda: None)

    review = {"speakers": [
        {"name": "Steve Hilton", "decision": "link", "politician_id": _UUID},
        {"name": "Katie Porter", "decision": "review", "politician_id": None},
    ]}
    review_file = tmp_path / "review.yaml"
    review_file.write_text(yaml.safe_dump(review))

    run_local._bulk_relink_apply(_args(review_file, publish_anyway=True))

    # m1 transcript now linked, m2 untouched, only m1 published.
    m1 = json.loads((meetings_root / "m1" / "transcript_named.json").read_text())
    assert m1["speakers"]["S0"]["politician_id"] == _UUID
    m2 = json.loads((meetings_root / "m2" / "transcript_named.json").read_text())
    assert m2["speakers"]["S0"].get("politician_id") is None
    assert published == ["m1"]


def test_apply_dry_run_writes_nothing(tmp_path, monkeypatch):
    meetings_root = tmp_path / "meetings"
    _write_meeting(meetings_root / "m1", _meeting("m1", "Steve Hilton"))
    monkeypatch.setattr(run_local.config, "MEETINGS_DIR", meetings_root)
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [{"politician_id": _UUID, "politician_slug": None,
                                          "full_name": "Steve Hilton"}])
    published = []
    monkeypatch.setattr(run_local, "_publish_meeting_standalone",
                        lambda mid, anyway=False: published.append(mid))

    review_file = tmp_path / "review.yaml"
    review_file.write_text(yaml.safe_dump(
        {"speakers": [{"name": "Steve Hilton", "decision": "link", "politician_id": _UUID}]}))

    run_local._bulk_relink_apply(_args(review_file, dry_run=True))

    m1 = json.loads((meetings_root / "m1" / "transcript_named.json").read_text())
    assert m1["speakers"]["S0"].get("politician_id") is None  # unchanged
    assert published == []  # nothing published
```

Note on patch targets (verified): `run_local` has a module-level `from src import config` (run_local.py:42), and `run_local.config` is the *same module object* as `src.config`, so `monkeypatch.setattr(run_local.config, "MEETINGS_DIR", ...)` is read by the handler's local `from src import config`. `_publish_meeting_standalone` and `_trigger_render_deploy` are module-level functions in `run_local` called as bare names, so patch them on `run_local`. `load_profiles`/`save_profiles` are imported *locally inside the handler* from `src.enroll`, so they MUST be patched as `"src.enroll.load_profiles"` / `"src.enroll.save_profiles"` (patching `run_local` would have no effect and the real `save_profiles` would clobber the production profile DB). The dry-run test returns before `load_profiles`, so it needn't patch them.

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink_apply.py -v`
Expected: FAIL with `AttributeError: module 'run_local' has no attribute '_bulk_relink_apply'`

- [ ] **Step 3: Write the apply handler**

In `run_local.py`, add after `_bulk_relink_scan`:

```python
def _bulk_relink_apply(args) -> None:
    """Apply approved links from a bulk-relink review file through the engine."""
    import yaml

    from src import config
    from src.bulk_relink import (
        DECISION_LINK, DECISION_REVIEW, DECISION_SKIP,
        BulkRelinkParseError, parse_review_doc,
    )
    from src.checkpoint import PipelineState
    from src.enroll import load_profiles, save_profiles
    from src.models import Meeting
    from src.relink import relink_in_meeting, rekey_profile_for_link, resolve_link_target

    with open(args.bulk_relink_apply, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    try:
        decisions = parse_review_doc(data)
    except BulkRelinkParseError as exc:
        print(f"Review file invalid: {exc}")
        sys.exit(2)

    links = [d for d in decisions if d.decision == DECISION_LINK]
    review = [d for d in decisions if d.decision == DECISION_REVIEW]
    skipped = [d for d in decisions if d.decision == DECISION_SKIP]

    print(f"Plan: {len(links)} to link, {len(review)} still need review (skipped), "
          f"{len(skipped)} marked skip.")
    if not links:
        print("Nothing approved to link. Edit the file (set decision: link + politician_id) and re-run.")
        if review:
            print("Unresolved review rows: " + ", ".join(d.name for d in review))
        return

    # Resolve each approved name to id/slug/full_name (display-only; id is authoritative).
    targets = {}
    for d in links:
        t = resolve_link_target(d.name, explicit_id=d.politician_id)
        targets[d.name] = t

    # Load every meeting once; compute relinks in memory.
    dirs = sorted(p for p in config.MEETINGS_DIR.iterdir()
                  if p.is_dir() and not p.name.startswith("."))
    loaded = []  # (mdir, Meeting)
    for mdir in dirs:
        named = mdir / "transcript_named.json"
        if not named.exists():
            continue
        with open(named, "r", encoding="utf-8") as f:
            loaded.append((mdir, Meeting.from_dict(json.load(f))))

    touched = {}  # mdir -> Meeting (meetings whose transcript changed)
    for d in links:
        t = targets[d.name]
        for mdir, meeting in loaded:
            changed = relink_in_meeting(meeting, d.name, t.politician_id, t.politician_slug)
            if changed:
                touched[mdir] = meeting

    if not touched:
        print("All approved speakers are already linked in transcripts (nothing to write).")
    print(f"Will relink {len(touched)} meeting(s); will fold {len(links)} voice profile(s).")
    if review:
        print("Skipping unresolved review rows: " + ", ".join(d.name for d in review))

    if args.dry_run:
        print("\n(dry run — no transcript, profile, publish, or deploy writes)")
        for mdir in sorted(touched, key=lambda p: p.name):
            print(f"  would relink + publish: {mdir.name}")
        print(f"  would fold profiles: {', '.join(d.name for d in links)}")
        print(f"  would deploy: {'yes' if args.deploy else 'no'}")
        if review:
            print("  To finish the review rows: edit the file, then re-run "
                  f"--bulk-relink-apply {args.bulk_relink_apply}")
        return

    # Persist changed transcripts.
    for mdir, meeting in touched.items():
        with open(mdir / "transcript_named.json", "w", encoding="utf-8") as f:
            json.dump(meeting.to_dict(), f, indent=2)

    # Fold each approved person's voice profile once.
    db = load_profiles()
    for d in links:
        t = targets[d.name]
        rekey_profile_for_link(db, d.name, politician_id=t.politician_id,
                               politician_slug=t.politician_slug, full_name=t.full_name)
    save_profiles(db)
    print(f"  Folded voice profiles for {len(links)} person(s).")

    # Publish each touched meeting; auto-resolve race_id for debates that lack one.
    blocked = []
    for mdir in sorted(touched, key=lambda p: p.name):
        meeting = touched[mdir]
        state = PipelineState(mdir)
        if meeting.event_kind == "debate" and not meeting.race_id:
            race = _resolve_debate_race_id(meeting)
            if race:
                meeting.race_id = race
                with open(mdir / "transcript_named.json", "w", encoding="utf-8") as f:
                    json.dump(meeting.to_dict(), f, indent=2)
                state.race_id = race
                state.save()
                print(f"  {mdir.name}: resolved debate race_id -> {race}")
            else:
                print(f"  skip publish {mdir.name}: debate meeting has no race_id and it "
                      f"could not be resolved (resolve the race manually).")
                blocked.append(mdir.name)
                continue
        if not _may_publish(state.review_status, args.publish_anyway):
            print(f"  skip publish {mdir.name}: gate verdict '{state.review_status}' "
                  f"(re-run with --publish-anyway)")
            blocked.append(mdir.name)
            continue
        _publish_meeting_standalone(mdir.name, args.publish_anyway)

    if args.deploy:
        _trigger_render_deploy()

    # Closing summary.
    print(f"\nDone: linked {len(links)} person(s) across {len(touched)} meeting(s).")
    if blocked:
        print(f"  {len(blocked)} meeting(s) not published: {', '.join(blocked)}")
    if review:
        print(f"  {len(review)} row(s) still need review: {', '.join(d.name for d in review)}")
        print(f"  Finish them: edit {args.bulk_relink_apply} (or re-run "
              f"--bulk-relink-scan for a fresh narrowed list), then --bulk-relink-apply again.")
```

- [ ] **Step 4: Add the race-id helper that opens the DB connection**

The handler calls `_resolve_debate_race_id(meeting)`, which wraps the DB lookup so the orchestrator owns the connection (and the pure `resolve_race_id_for_politicians` stays unit-testable). Add this helper just above `_bulk_relink_apply`:

```python
def _resolve_debate_race_id(meeting) -> str | None:
    """Open a DB connection and resolve a race_id from the meeting's linked politicians."""
    import psycopg2

    from src.publish import _require_db_url, resolve_race_id_for_politicians

    pol_ids = [m.politician_id for m in meeting.speakers.values() if m.politician_id]
    if not pol_ids:
        return None
    try:
        conn = psycopg2.connect(_require_db_url())
    except Exception as exc:  # noqa: BLE001 - surface connection failure, don't crash apply
        print(f"  race_id lookup skipped (DB connect failed: {exc})")
        return None
    try:
        with conn.cursor() as cur:
            return resolve_race_id_for_politicians(cur, pol_ids)
    finally:
        conn.close()
```

- [ ] **Step 5: Dispatch the handler**

In `main`, after the `--bulk-relink-scan` dispatch added in Task 8, add:

```python
    if args.bulk_relink_apply:
        _bulk_relink_apply(args)
        return
```

- [ ] **Step 6: Run the integration test**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink_apply.py -v`
Expected: PASS (2 tests). If the `MEETINGS_DIR` monkeypatch target is wrong, switch it to `"src.config.MEETINGS_DIR"` per the note in Step 1 and re-run.

- [ ] **Step 7: Commit**

```bash
git add run_local.py tests/test_bulk_relink_apply.py
git commit -m "feat(bulk-relink): --bulk-relink-apply links approved rows + publishes"
```

---

## Task 10: Full regression + dry-run smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q`
Expected: all tests pass (the prior baseline was 482 passing; this adds ~22 new tests and must not break existing ones).

- [ ] **Step 2: End-to-end dry-run smoke on a real review file**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/.claude/worktrees/funny-yonath-7e9bd7
set -a; . ./.env.local 2>/dev/null; set +a
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python run_local.py --bulk-relink-scan --out /tmp/bulk_relink_smoke.yaml
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python run_local.py --bulk-relink-apply /tmp/bulk_relink_smoke.yaml --dry-run
```
Expected: scan writes the file; apply `--dry-run` prints a plan (meetings it would relink/publish, profiles it would fold, deploy no) and writes nothing. Confirm the meeting transcripts are unchanged (`git status` shows no transcript edits under the meetings dir).

- [ ] **Step 3: Commit (if any cleanup was needed)**

If Steps 1–2 surfaced no changes, nothing to commit. Otherwise commit fixes with a descriptive message.

---

## Self-Review Notes (already reconciled against the spec)

- **Spec coverage:** enumeration (Task 3) · known_id offline enrichment (Task 3) · suggestion auto-approve unambiguous + known-id fast path (Task 4) · YAML build/parse + validation (Tasks 5–6) · race_id resolver (Task 7) · scan command (Task 8) · apply chain with relink+fold+publish, dry-run, publish-anyway, deploy, race_id auto-resolution, leftover-review warn-and-skip + finish guidance (Task 9) · PyYAML declared (Task 1). The web page and local-person routing remain explicitly out of scope.
- **Type consistency:** `UnlinkedSpeaker` fields (`display_name`, `normalized_name`, `appearances`, `meeting_count`, `has_voice_profile`, `known_id`, `decision`, `candidates`) are used identically across Tasks 2–8; `ReviewDecision(name, decision, politician_id)` is used identically in Tasks 6 and 9; `suggest_link(speaker, *, search=...)` and `resolve_race_id_for_politicians(cur, politician_ids)` signatures match between definition and call sites.
- **Decision vocabulary:** `link` / `review` / `skip` (the `DECISION_*` constants) are consistent across build, parse, and apply.
