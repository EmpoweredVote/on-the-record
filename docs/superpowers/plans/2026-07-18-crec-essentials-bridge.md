# CREC → essentials politician_id Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link a CREC-resolved congressional member to its essentials `politician_id` (read-only, single-unambiguous-match only), so floor speakers surface on the real politician profile/quotes.

**Architecture:** New pure module `src/crec_essentials.py` (`resolve_politician_id` — last-name search via the existing `search_politicians`, filtered to federal + chamber + district, single match only). `crec_speaker_mappings` threads an injectable `search` and enriches confident-member mappings with the `politician_id`. Read-only, best-effort (API error → name-only), transparent (default `search` is the real API, so no `run_local.py` change).

**Tech Stack:** Python 3.14 (`.venv/bin/python`), pytest, stdlib. Reuses `src/essentials_client.py` (`search_politicians`), `src/congress_roster.py` (`CongressMember`), `src/crec_identify.py`. No new deps.

**Spec:** `docs/superpowers/specs/2026-07-18-crec-essentials-bridge-design.md`.

**Key facts:**
- `search_politicians(q, *, limit=10, base_url=None) -> list[dict]`; each dict has `politician_id`, `politician_slug`, `full_name`, `office_title`, `district_label`, `is_incumbent`, `government_name`.
- `CongressMember(bioguide, full_name, last_name, state, district, chamber, party)` (`src/congress_roster.py`).
- `crec_speaker_mappings(date, chamber, segments, *, fetch=None, min_confidence=0.5, cache_path=None)` (`src/crec_identify.py`) — current body converts each `LabelResolution` via `label_resolution_to_mapping`, dropping `None`s.

---

## File Structure

- Create: `src/crec_essentials.py` — `resolve_politician_id` + helpers.
- Create: `tests/test_crec_essentials.py`
- Modify: `src/crec_identify.py` — thread `search`, enrich member mappings.
- Modify: `tests/test_crec_identify.py` — inject `search` in the existing orchestration test; add bridge tests.

---

### Task 1: `crec_essentials.py` — `resolve_politician_id` + helpers

**Files:**
- Create: `src/crec_essentials.py`
- Test: `tests/test_crec_essentials.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_crec_essentials.py`:

```python
# tests/test_crec_essentials.py
from __future__ import annotations

from src.congress_roster import CongressMember
from src.crec_essentials import (
    resolve_politician_id, _is_federal, _chamber_matches, _district_number,
)


def _mem(last, chamber, district=None, bio="X000001"):
    return CongressMember(bio, f"First {last}", last, "XX", district, chamber, "Party")


def _rec(name, office, *, gov="United States Federal Government",
         district_label="", pid="id1", slug=None):
    return {"politician_id": pid, "politician_slug": slug, "full_name": name,
            "office_title": office, "district_label": district_label,
            "is_incumbent": True, "government_name": gov}


def test_helpers():
    assert _is_federal({"government_name": "United States Federal Government"})
    assert not _is_federal({"government_name": "City of Cambridge, MA"})
    assert not _is_federal({})
    assert _chamber_matches({"office_title": "Senator"}, "senate")
    assert _chamber_matches({"office_title": "U.S. Representative"}, "house")
    assert not _chamber_matches({"office_title": "Senator"}, "house")
    assert _district_number("Congressional District 9") == 9
    assert _district_number("At-Large") is None
    assert _district_number("") is None


def test_resolve_single_federal_rep():
    search = lambda q, **kw: [_rec("Bryan Steil", "U.S. Representative",
                                   district_label="Congressional District 1", pid="P1")]
    assert resolve_politician_id(_mem("Steil", "house", 1), search=search) == ("P1", None)


def test_resolve_single_senator():
    search = lambda q, **kw: [_rec("John Thune", "Senator", pid="THUNE")]
    assert resolve_politician_id(_mem("Thune", "senate"), search=search) == ("THUNE", None)


def test_resolve_filters_out_local_namesake():
    # 'James P. McGovern'-style nickname miss is fixed by last-name search; the
    # local same-surname councillor must be filtered out by government_name.
    search = lambda q, **kw: [
        _rec("Jim McGovern", "Representative", district_label="Congressional District 2", pid="FED"),
        _rec("Marc McGovern", "City Councillor", gov="City of Cambridge, MA",
             district_label="Cambridge", pid="LOCAL"),
    ]
    assert resolve_politician_id(_mem("McGovern", "house", 2), search=search) == ("FED", None)


def test_resolve_same_surname_reps_by_district():
    search = lambda q, **kw: [
        _rec("Adam Smith", "U.S. Representative", district_label="Congressional District 9", pid="WA9"),
        _rec("Adrian Smith", "U.S. Representative", district_label="Congressional District 3", pid="NE3"),
    ]
    assert resolve_politician_id(_mem("Smith", "house", 3), search=search) == ("NE3", None)


def test_resolve_chamber_filter_excludes_wrong_house():
    search = lambda q, **kw: [_rec("Some Smith", "U.S. Representative",
                                   district_label="Congressional District 1", pid="R1")]
    assert resolve_politician_id(_mem("Smith", "senate"), search=search) is None


def test_resolve_ambiguous_returns_none():
    search = lambda q, **kw: [
        _rec("A Smith", "U.S. Representative", district_label="Congressional District 5", pid="X"),
        _rec("B Smith", "U.S. Representative", district_label="Congressional District 5", pid="Y"),
    ]
    assert resolve_politician_id(_mem("Smith", "house", 5), search=search) is None


def test_resolve_no_match_returns_none():
    assert resolve_politician_id(_mem("Nobody", "house", 1), search=lambda q, **kw: []) is None


def test_resolve_search_error_returns_none():
    def boom(q, **kw):
        raise RuntimeError("essentials api down")
    assert resolve_politician_id(_mem("Steil", "house", 1), search=boom) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_essentials.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.crec_essentials'`.

- [ ] **Step 3: Write minimal implementation** — create `src/crec_essentials.py`:

```python
# src/crec_essentials.py
"""Bridge a CREC-resolved congress member to an essentials politician_id.

Read-only: reuses search_politicians (the ev-accounts search-by-name endpoint).
Attaches a politician_id ONLY on a single unambiguous federal match (chamber +,
for the House, district); any ambiguity or error -> None (the caller falls back
to a name-only mapping). Never writes to essentials, never links the wrong person.
"""
from __future__ import annotations

import re
from typing import Optional

from .congress_roster import CongressMember
from .essentials_client import search_politicians


def _is_federal(rec: dict) -> bool:
    return "united states federal" in (rec.get("government_name") or "").lower()


def _chamber_matches(rec: dict, chamber: str) -> bool:
    office = (rec.get("office_title") or "").lower()
    if chamber == "senate":
        return "senator" in office
    return "representative" in office   # house


def _district_number(district_label: str) -> Optional[int]:
    """First integer in a district_label ('Congressional District 9' -> 9), or None."""
    m = re.search(r"\d+", district_label or "")
    return int(m.group()) if m else None


def resolve_politician_id(
    member: CongressMember, *, search=search_politicians,
) -> Optional[tuple]:
    """Resolve a CongressMember to an essentials (politician_id, politician_slug).

    Searches by LAST NAME (essentials display names differ from congress-legislators
    official_full — nicknames, dropped middle initials), then filters to a single
    unambiguous federal member of the right chamber (and, for the House, district).
    Returns None on no match, ambiguity, or any search error (best-effort).
    """
    try:
        cands = search(member.last_name, limit=25)
    except Exception:
        return None

    matches = [c for c in cands if _is_federal(c) and _chamber_matches(c, member.chamber)]
    if member.chamber == "house" and member.district is not None:
        matches = [c for c in matches
                   if _district_number(c.get("district_label")) == member.district]

    if len(matches) == 1:
        c = matches[0]
        return (c.get("politician_id"), c.get("politician_slug"))
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_essentials.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/crec_essentials.py tests/test_crec_essentials.py
git commit -m "feat(crec_essentials): resolve congress member -> essentials politician_id"
```

---

### Task 2: `crec_identify.py` — thread `search` + enrich member mappings

**Files:**
- Modify: `src/crec_identify.py`
- Modify: `tests/test_crec_identify.py`

- [ ] **Step 1: Update the existing orchestration test + add bridge tests** — in `tests/test_crec_identify.py`:

(a) The existing `test_crec_speaker_mappings_resolves_members` resolves real members and would otherwise call the live essentials API. Inject a no-match `search` so it stays offline (its name/`local_slug` assertions are unaffected — no `politician_id` was asserted). Change its `crec_speaker_mappings(...)` call to add `search=lambda q, **kw: []`:

```python
    out = crec_speaker_mappings(
        "2025-01-10", "senate", segs,
        fetch=_fake_fetch, cache_path=tmp_path / "leg.json", min_confidence=0.4,
        search=lambda q, **kw: [])
```

(b) Append two new bridge tests (reuse the existing `_fake_fetch`, `_seg`, `_LEG_FIX`, `_GRANULES`, `_HTM` helpers already in the file):

```python
# add to tests/test_crec_identify.py
def _fed_senator_search(q, **kw):
    # a federal-Senator match for any surname queried
    return [{"politician_id": f"pid-{q}", "politician_slug": None, "full_name": q,
             "office_title": "Senator", "district_label": "", "is_incumbent": True,
             "government_name": "United States Federal Government"}]


def test_crec_speaker_mappings_attaches_politician_id(tmp_path):
    segs = [
        _seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill today"),
        _seg(1, "SPEAKER_01", "I rise in strong support of this healthcare measure"),
    ]
    out = crec_speaker_mappings(
        "2025-01-10", "senate", segs,
        fetch=_fake_fetch, cache_path=tmp_path / "leg.json", min_confidence=0.4,
        search=_fed_senator_search)
    assert out["SPEAKER_00"].speaker_name == "Mitch McConnell"
    assert out["SPEAKER_00"].politician_id == "pid-McConnell"      # bridge attached the id
    assert out["SPEAKER_00"].local_slug == "congress-M000355"      # provenance kept
    assert out["SPEAKER_01"].politician_id == "pid-Baldwin"


def test_crec_speaker_mappings_no_essentials_match_stays_name_only(tmp_path):
    segs = [_seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill today")]
    out = crec_speaker_mappings(
        "2025-01-10", "senate", segs,
        fetch=_fake_fetch, cache_path=tmp_path / "leg.json", min_confidence=0.4,
        search=lambda q, **kw: [])
    assert out["SPEAKER_00"].speaker_name == "Mitch McConnell"
    assert out["SPEAKER_00"].politician_id is None                 # no match -> name-only
    assert out["SPEAKER_00"].local_slug == "congress-M000355"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_identify.py -k "attaches_politician_id or no_essentials_match" -v`
Expected: FAIL — `TypeError: crec_speaker_mappings() got an unexpected keyword argument 'search'`.

- [ ] **Step 3: Write implementation** — edit `src/crec_identify.py`:

(a) Add the import near the other local imports added in Phase 4 (`from .govinfo import ...` etc.):

```python
from .crec_essentials import resolve_politician_id
```

(b) Add the `search` parameter to `crec_speaker_mappings` and enrich member mappings. Replace the current function body's signature line and the final conversion loop:

```python
def crec_speaker_mappings(
    date: str,
    chamber: str,
    segments,
    *,
    fetch=None,
    min_confidence: float = 0.5,
    cache_path=None,
    search=None,
) -> dict:
    """Resolve diarized speaker labels via the Congressional Record for a session.

    Orchestrates Phases 1-3, then bridges each confident member to its essentials
    politician_id (read-only, single-unambiguous-match only; best-effort). `fetch`,
    `cache_path`, and `search` are injectable for testing. Returns {} when there is
    no Record.
    """
    fkw = {"fetch": fetch} if fetch is not None else {}
    turns = fetch_congressional_record_turns(date, chamber, **fkw)
    if not turns:
        return {}
    roster = load_current_roster(chamber, cache_path=cache_path, **fkw)
    annotated = annotate_turns(turns, roster)
    resolutions = align_crec_to_diarization(segments, annotated, min_confidence=min_confidence)

    mappings: dict = {}
    for label, res in resolutions.items():
        m = label_resolution_to_mapping(res)
        if m is None:
            continue
        if res.member is not None:
            link = resolve_politician_id(
                res.member,
                **({"search": search} if search is not None else {}),
            )
            if link:
                m.politician_id, m.politician_slug = link
        mappings[label] = m
    return mappings
```

Note: passing `search` through only when the caller supplied it means production (`search=None`) uses `resolve_politician_id`'s real default (`search_politicians`), so the bridge activates transparently with no `run_local.py` change.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_identify.py -v` — expect all pass (11 prior + 2 new = 13). Then the full suite: `.venv/bin/python -m pytest -q` — confirm no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/crec_identify.py tests/test_crec_identify.py
git commit -m "feat(crec_identify): bridge confident CREC members to essentials politician_id"
```

---

## Self-Review

**Spec coverage:**
- `resolve_politician_id` (last-name search, federal + chamber + district filter, single-match-only, best-effort) + helpers — Task 1.
- `crec_speaker_mappings` threads `search` and enriches member mappings; existing test made offline; bridge attach + no-match tests — Task 2.
- Transparent activation (default real `search`, no `run_local.py` change) — Task 2 Step 3 note.
- Deferred per spec: no essentials writes; senator same-surname state disambiguation; at-large edge — all resolve to name-only, documented.

**Placeholder scan:** No TBD/TODO; every code and test step is complete.

**Type consistency:** `resolve_politician_id(member, *, search) -> tuple | None` used identically in Task 1 tests and the Task 2 call site. `_is_federal`/`_chamber_matches`/`_district_number` signatures match between definition and tests. The `search` fake signature `(q, **kw)` matches `search_politicians(q, *, limit=..., ...)` (the `limit=25` kwarg is absorbed by `**kw`). `crec_speaker_mappings` gains `search=None`; the conditional pass-through keeps `resolve_politician_id`'s real default in production.

**Behavioral check:** the bridge runs only for `res.member is not None` (confident members) — role/ambiguous/unresolved never hit essentials. A successful link adds `politician_id`/`politician_slug` while keeping `speaker_name`, `id_method='congressional_record'`, and `local_slug` provenance. Any search error or non-single match → name-only, never a wrong link.
