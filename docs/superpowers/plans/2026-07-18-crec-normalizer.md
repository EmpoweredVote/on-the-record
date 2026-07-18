# CREC Normalizer (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve a Congressional Record speaker designation (`"Mr. McCONNELL"`, `"Ms. BALDWIN of Wisconsin"`, `"The PRESIDING OFFICER (Mrs. Ernst)"`) to a congressional member identity, a procedural role, or "unresolved" — using the public `congress-legislators` dataset.

**Architecture:** Two pure modules mirroring the repo's data/matching split. `src/congress_roster.py` fetches + caches `legislators-current.json` (network behind an injected `fetch`, like `src/govinfo.py`) and builds a chamber-scoped roster indexed by surname. `src/crec_normalize.py` matches a designation string against that roster with **exact case-insensitive full-surname matching** and state disambiguation. Feeds Phase 3 (`crec_align.py`).

**Tech Stack:** Python 3.14 (`.venv/bin/python`), pytest, stdlib `json`, `requests` (already a dep). No new dependencies (uses the JSON edition of the dataset — no YAML).

**Spec:** `docs/superpowers/specs/2026-07-18-crec-normalizer-design.md`.

**Deviation from spec (intentional):** the spec said "reuse `roster.extract_surname`". `extract_surname` returns a single last token (built for council ASR correction) and would mishandle compound surnames like "Van Hollen"/"Cortez Masto". Since CREC and the dataset are both canonical digital text (no transcription errors), this plan uses exact case-insensitive matching of the **full** captured surname against `name.last` — more precise, and compound-surname safe. Also, `normalize_designation(speaker_raw, roster)` drops the spec's redundant `chamber` param (the roster is already chamber-scoped).

**Data source (verified 2026-07-18):** `https://unitedstates.github.io/congress-legislators/legislators-current.json` — 537 members; per member `id.bioguide`, `name.{first,last,official_full}`, `terms[-1].{type:'sen'|'rep', state, district, party}`.

---

## File Structure

- Create: `src/congress_roster.py` — fetch/cache/build the chamber-scoped roster.
- Create: `src/crec_normalize.py` — designation string → `ResolvedSpeaker`.
- Create: `tests/test_congress_roster.py`
- Create: `tests/test_crec_normalize.py`
- Create: `tests/fixtures/congress/legislators-current.sample.json` — 5 real members (see Task 1).

---

### Task 1: `congress_roster.py` scaffold — models + `_member_from_raw` + fixture

**Files:**
- Create: `src/congress_roster.py`
- Create: `tests/fixtures/congress/legislators-current.sample.json`
- Test: `tests/test_congress_roster.py`

- [ ] **Step 1: Create the fixture** `tests/fixtures/congress/legislators-current.sample.json` (5 real current members — 3 senators, a same-surname House pair in different states):

```json
[
  {"id":{"bioguide":"E000295"},"name":{"first":"Joni","last":"Ernst","official_full":"Joni Ernst"},"terms":[{"type":"sen","state":"IA","district":null,"party":"Republican","start":"2021-01-03","end":"2027-01-03"}]},
  {"id":{"bioguide":"B001230"},"name":{"first":"Tammy","last":"Baldwin","official_full":"Tammy Baldwin"},"terms":[{"type":"sen","state":"WI","district":null,"party":"Democrat","start":"2025-01-03","end":"2031-01-03"}]},
  {"id":{"bioguide":"M000355"},"name":{"first":"Mitch","last":"McConnell","official_full":"Mitch McConnell"},"terms":[{"type":"sen","state":"KY","district":null,"party":"Republican","start":"2021-01-03","end":"2027-01-03"}]},
  {"id":{"bioguide":"S000510"},"name":{"first":"Adam","last":"Smith","official_full":"Adam Smith"},"terms":[{"type":"rep","state":"WA","district":9,"party":"Democrat","start":"2025-01-03","end":"2027-01-03"}]},
  {"id":{"bioguide":"S001172"},"name":{"first":"Adrian","last":"Smith","official_full":"Adrian Smith"},"terms":[{"type":"rep","state":"NE","district":3,"party":"Republican","start":"2025-01-03","end":"2027-01-03"}]}
]
```

- [ ] **Step 2: Write the failing test** — create `tests/test_congress_roster.py`:

```python
# tests/test_congress_roster.py
from __future__ import annotations

import json
from pathlib import Path

from src.congress_roster import CongressMember, CongressRoster, _member_from_raw

_FIX = Path(__file__).parent / "fixtures" / "congress" / "legislators-current.sample.json"


def _raw() -> list[dict]:
    return json.loads(_FIX.read_text(encoding="utf-8"))


def test_member_from_raw_senator():
    ernst = next(e for e in _raw() if e["name"]["last"] == "Ernst")
    m = _member_from_raw(ernst, "senate")
    assert m == CongressMember(
        bioguide="E000295", full_name="Joni Ernst", last_name="Ernst",
        state="IA", district=None, chamber="senate", party="Republican")


def test_member_from_raw_representative():
    adam = next(e for e in _raw() if e["name"]["last"] == "Smith" and e["terms"][-1]["state"] == "WA")
    m = _member_from_raw(adam, "house")
    assert m.bioguide == "S000510"
    assert m.district == 9
    assert m.chamber == "house"


def test_member_from_raw_returns_none_for_wrong_chamber():
    ernst = next(e for e in _raw() if e["name"]["last"] == "Ernst")  # a senator
    assert _member_from_raw(ernst, "house") is None


def test_congress_roster_by_surname_accessor():
    m = CongressMember("X0", "Jane Doe", "Doe", "CA", None, "senate", "Democrat")
    roster = CongressRoster(chamber="senate", members=[m], _by_surname={"doe": [m]})
    assert roster.by_surname("DOE") == [m]      # case-insensitive
    assert roster.by_surname("nope") == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_congress_roster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.congress_roster'`.

- [ ] **Step 4: Write minimal implementation** — create `src/congress_roster.py`:

```python
# src/congress_roster.py
"""Current-Congress member roster from the congress-legislators dataset.

Fetches (and caches) the public `legislators-current.json` and builds a
chamber-scoped roster indexed by surname, for resolving Congressional Record
speaker designations to member identities. Pure parsing; network behind an
injected `fetch` (mirrors src/govinfo.py).

Source: https://unitedstates.github.io/congress-legislators/legislators-current.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import config

_LEGISLATORS_URL = (
    "https://unitedstates.github.io/congress-legislators/legislators-current.json"
)
_CHAMBER_TO_TERM_TYPE = {"house": "rep", "senate": "sen"}


@dataclass
class CongressMember:
    bioguide: str
    full_name: str
    last_name: str
    state: str
    district: Optional[int]
    chamber: str
    party: Optional[str]


@dataclass
class CongressRoster:
    chamber: str
    members: list[CongressMember] = field(default_factory=list)
    _by_surname: dict[str, list[CongressMember]] = field(default_factory=dict)

    def by_surname(self, surname: str) -> list[CongressMember]:
        """Members whose last name matches `surname` (case-insensitive)."""
        return self._by_surname.get(surname.lower(), [])


def _member_from_raw(entry: dict, chamber: str) -> Optional[CongressMember]:
    """Build a CongressMember from a legislators-current entry when its latest
    term matches the chamber; else None."""
    terms = entry.get("terms") or []
    if not terms:
        return None
    term = terms[-1]
    if term.get("type") != _CHAMBER_TO_TERM_TYPE[chamber.lower()]:
        return None
    name = entry.get("name") or {}
    last = name.get("last")
    if not last:
        return None
    full = name.get("official_full") or f"{name.get('first', '')} {last}".strip()
    return CongressMember(
        bioguide=(entry.get("id") or {}).get("bioguide", ""),
        full_name=full,
        last_name=last,
        state=term.get("state", ""),
        district=term.get("district"),
        chamber=chamber.lower(),
        party=term.get("party"),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_congress_roster.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add src/congress_roster.py tests/test_congress_roster.py tests/fixtures/congress/legislators-current.sample.json
git commit -m "feat(congress_roster): scaffold CongressMember/Roster + _member_from_raw"
```

---

### Task 2: `build_roster` — chamber filter + surname index

**Files:**
- Modify: `src/congress_roster.py`
- Test: `tests/test_congress_roster.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_congress_roster.py`:

```python
# add to tests/test_congress_roster.py
from src.congress_roster import build_roster


def test_build_roster_senate_filters_and_indexes():
    roster = build_roster(_raw(), "senate")
    assert roster.chamber == "senate"
    assert sorted(m.last_name for m in roster.members) == ["Baldwin", "Ernst", "McConnell"]
    assert [m.bioguide for m in roster.by_surname("mcconnell")] == ["M000355"]


def test_build_roster_house_keeps_same_surname_pair():
    roster = build_roster(_raw(), "house")
    smiths = roster.by_surname("smith")
    assert {m.state for m in smiths} == {"WA", "NE"}   # both House Smiths indexed together
    assert len(roster.members) == 2


def test_build_roster_excludes_other_chamber():
    senate = build_roster(_raw(), "senate")
    assert all(m.chamber == "senate" for m in senate.members)
    assert "smith" not in senate._by_surname   # House Smiths excluded from senate roster
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_congress_roster.py -k build_roster -v`
Expected: FAIL — `ImportError: cannot import name 'build_roster'`.

- [ ] **Step 3: Write minimal implementation** — append to `src/congress_roster.py`:

```python
def build_roster(raw: list[dict], chamber: str) -> CongressRoster:
    """Chamber-scoped roster from a parsed legislators-current list.

    Keeps members whose latest term matches the chamber, indexed by lowercased
    last name (a list per surname, so same-surname collisions are preserved for
    the normalizer to disambiguate).
    """
    members: list[CongressMember] = []
    by_surname: dict[str, list[CongressMember]] = {}
    for entry in raw:
        m = _member_from_raw(entry, chamber)
        if m is None:
            continue
        members.append(m)
        by_surname.setdefault(m.last_name.lower(), []).append(m)
    return CongressRoster(chamber=chamber.lower(), members=members, _by_surname=by_surname)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_congress_roster.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/congress_roster.py tests/test_congress_roster.py
git commit -m "feat(congress_roster): build_roster chamber filter + surname index"
```

---

### Task 3: fetch + cache — `fetch_current_legislators`, `load_current_roster`

**Files:**
- Modify: `src/congress_roster.py`
- Test: `tests/test_congress_roster.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_congress_roster.py` (injected fetch + `tmp_path` cache; never hits the network):

```python
# add to tests/test_congress_roster.py
from src.congress_roster import fetch_current_legislators, load_current_roster


def test_fetch_current_legislators_writes_cache(tmp_path):
    text = _FIX.read_text(encoding="utf-8")
    cache = tmp_path / "congress" / "legislators-current.json"
    data = fetch_current_legislators(fetch=lambda url: text, cache_path=cache)
    assert len(data) == 5
    assert cache.exists()
    assert json.loads(cache.read_text(encoding="utf-8"))[0]["id"]["bioguide"] == "E000295"


def test_load_current_roster_uses_cache_without_fetching(tmp_path):
    cache = tmp_path / "congress" / "legislators-current.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(_FIX.read_text(encoding="utf-8"), encoding="utf-8")

    def boom(url):
        raise AssertionError("should not fetch when cache is present")

    roster = load_current_roster("senate", fetch=boom, cache_path=cache)
    assert sorted(m.last_name for m in roster.members) == ["Baldwin", "Ernst", "McConnell"]


def test_load_current_roster_fetches_when_cache_absent(tmp_path):
    cache = tmp_path / "congress" / "legislators-current.json"
    text = _FIX.read_text(encoding="utf-8")
    roster = load_current_roster("house", fetch=lambda url: text, cache_path=cache)
    assert len(roster.members) == 2
    assert cache.exists()   # fetch populated the cache
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_congress_roster.py -k "fetch_current or load_current" -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_current_legislators'`.

- [ ] **Step 3: Write minimal implementation** — append to `src/congress_roster.py`:

```python
def _default_fetch(url: str) -> str:
    import requests

    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def _default_cache_path():
    return config.CONFIG_DIR / "congress" / "legislators-current.json"


def fetch_current_legislators(
    *, fetch: Callable[[str], str] = _default_fetch, cache_path=None
) -> list[dict]:
    """Fetch and parse legislators-current.json; write it to cache (best-effort)."""
    text = fetch(_LEGISLATORS_URL)
    data = json.loads(text)
    path = cache_path or _default_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except Exception:
        pass   # cache is an optimization; never fail the fetch on a write error
    return data


def load_current_roster(
    chamber: str, *, fetch: Callable[[str], str] = _default_fetch, cache_path=None
) -> CongressRoster:
    """Chamber roster: read a non-empty cache if present, else fetch + cache."""
    path = cache_path or _default_cache_path()
    raw = None
    try:
        if path.exists():
            txt = path.read_text(encoding="utf-8")
            raw = json.loads(txt) if txt.strip() else None
    except Exception:
        raw = None
    if not raw:
        raw = fetch_current_legislators(fetch=fetch, cache_path=path)
    return build_roster(raw, chamber)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_congress_roster.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add src/congress_roster.py tests/test_congress_roster.py
git commit -m "feat(congress_roster): fetch_current_legislators + load_current_roster (cache)"
```

---

### Task 4: `crec_normalize.py` scaffold — `ResolvedSpeaker`, state map, helpers

**Files:**
- Create: `src/crec_normalize.py`
- Test: `tests/test_crec_normalize.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_crec_normalize.py`:

```python
# tests/test_crec_normalize.py
from __future__ import annotations

import json
from pathlib import Path

from src.congress_roster import build_roster
from src.crec_normalize import ResolvedSpeaker, _resolve_surname, _role_slug

_FIX = Path(__file__).parent / "fixtures" / "congress" / "legislators-current.sample.json"


def _roster(chamber):
    return build_roster(json.loads(_FIX.read_text(encoding="utf-8")), chamber)


def test_role_slug():
    assert _role_slug("PRESIDING OFFICER") == "presiding_officer"
    assert _role_slug("SPEAKER pro tempore") == "speaker"
    assert _role_slug("VICE PRESIDENT") == "vice_president"
    assert _role_slug("Clerk") == "clerk"


def test_resolve_surname_unique_senate():
    res = _resolve_surname("McConnell", None, _roster("senate"))
    assert res.member.bioguide == "M000355"
    assert res.method == "surname"
    assert res.confidence == 1.0
    assert res.needs_review is False


def test_resolve_surname_with_state_disambiguates():
    res = _resolve_surname("Smith", "Nebraska", _roster("house"))
    assert res.member.bioguide == "S001172"
    assert res.method == "surname_state"


def test_resolve_surname_ambiguous_without_state():
    res = _resolve_surname("Smith", None, _roster("house"))
    assert res.member is None
    assert res.method == "ambiguous"
    assert res.needs_review is True


def test_resolve_surname_unknown():
    res = _resolve_surname("Nonesuch", None, _roster("senate"))
    assert res.member is None
    assert res.method == "unresolved"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.crec_normalize'`.

- [ ] **Step 3: Write minimal implementation** — create `src/crec_normalize.py`:

```python
# src/crec_normalize.py
"""Resolve a Congressional Record speaker designation to a congress member.

Consumes a CongressRoster (src/congress_roster.py). Pure; no network. Handles
member designations ("Mr. McCONNELL", "Ms. BALDWIN of Wisconsin"), presiding
officers with a parenthetical ("The PRESIDING OFFICER (Mrs. Ernst)"), and bare
procedural roles ("The PRESIDING OFFICER", "The SPEAKER").

Matching is exact and case-insensitive on the full surname (CREC and the dataset
are both canonical text — no fuzzy matching needed), with state disambiguation
for same-surname collisions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .congress_roster import CongressMember, CongressRoster

_STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR", "guam": "GU",
    "american samoa": "AS", "virgin islands": "VI",
    "northern mariana islands": "MP",
}


def _role_slug(role_raw: str) -> str:
    """Normalize a procedural-role phrase to a stable slug."""
    r = role_raw.lower()
    if "presiding" in r:
        return "presiding_officer"
    if "speaker" in r:
        return "speaker"
    if "president pro tempore" in r:
        return "president_pro_tempore"
    if "vice president" in r:
        return "vice_president"
    if "chief justice" in r:
        return "chief_justice"
    if "chair" in r:
        return "chair"
    if "clerk" in r:
        return "clerk"
    return r.replace(" ", "_")


def _resolve_surname(
    surname: str, state_name: Optional[str], roster: CongressRoster
) -> "ResolvedSpeaker":
    """Match a surname (+ optional 'of <State>') against the roster."""
    cands = roster.by_surname(surname)
    if not cands:
        return ResolvedSpeaker(method="unresolved")
    used_state = False
    if state_name:
        code = _STATE_NAME_TO_CODE.get(state_name.strip().lower())
        if code:
            filtered = [m for m in cands if m.state == code]
            if filtered:
                cands = filtered
                used_state = True
    if len(cands) == 1:
        return ResolvedSpeaker(
            member=cands[0],
            method="surname_state" if used_state else "surname",
            confidence=1.0,
        )
    return ResolvedSpeaker(method="ambiguous", needs_review=True)


@dataclass
class ResolvedSpeaker:
    member: Optional[CongressMember] = None
    role: Optional[str] = None
    method: str = "unresolved"   # surname|surname_state|presiding_parenthetical|role|ambiguous|unresolved
    confidence: float = 0.0
    needs_review: bool = False
```

Note: `ResolvedSpeaker` is defined after the functions that reference it by name; that is fine in Python because the references are only evaluated at call time. (If you prefer, move the dataclass to the top — either works.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_normalize.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/crec_normalize.py tests/test_crec_normalize.py
git commit -m "feat(crec_normalize): ResolvedSpeaker + state map + surname/role helpers"
```

---

### Task 5: `normalize_designation` — full dispatch

**Files:**
- Modify: `src/crec_normalize.py`
- Test: `tests/test_crec_normalize.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_crec_normalize.py`:

```python
# add to tests/test_crec_normalize.py
from src.crec_normalize import normalize_designation


def test_normalize_plain_member_uppercase():
    res = normalize_designation("Mr. McCONNELL", _roster("senate"))
    assert res.member.bioguide == "M000355"
    assert res.method == "surname"


def test_normalize_member_of_state():
    res = normalize_designation("Ms. BALDWIN of Wisconsin", _roster("senate"))
    assert res.member.bioguide == "B001230"
    assert res.method == "surname_state"


def test_normalize_house_ambiguous_needs_review():
    res = normalize_designation("Mr. SMITH", _roster("house"))
    assert res.member is None
    assert res.needs_review is True
    assert res.method == "ambiguous"


def test_normalize_house_of_state_resolves():
    res = normalize_designation("Mr. SMITH of Washington", _roster("house"))
    assert res.member.bioguide == "S000510"


def test_normalize_presiding_parenthetical():
    res = normalize_designation("The PRESIDING OFFICER (Mrs. Ernst)", _roster("senate"))
    assert res.member.bioguide == "E000295"
    assert res.method == "presiding_parenthetical"


def test_normalize_bare_presiding_officer_is_role():
    res = normalize_designation("The PRESIDING OFFICER", _roster("senate"))
    assert res.member is None
    assert res.role == "presiding_officer"
    assert res.method == "role"


def test_normalize_bare_speaker_is_role():
    res = normalize_designation("The SPEAKER", _roster("house"))
    assert res.role == "speaker"
    assert res.method == "role"


def test_normalize_unknown_surname_unresolved():
    res = normalize_designation("Mr. NONESUCH", _roster("senate"))
    assert res.method == "unresolved"


def test_normalize_presiding_parenthetical_unknown_falls_back_to_role():
    res = normalize_designation("The PRESIDING OFFICER (Mr. Nonesuch)", _roster("senate"))
    assert res.member is None
    assert res.role == "presiding_officer"
    assert res.method == "role"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_normalize.py -k normalize -v`
Expected: FAIL — `ImportError: cannot import name 'normalize_designation'`.

- [ ] **Step 3: Write minimal implementation** — append to `src/crec_normalize.py`:

```python
# Member designation: "Mr./Mrs./Ms./Miss <surname>[ of <State>]".
# surname is non-greedy so a trailing " of <State>" is split off; it still
# captures compound surnames ("Van Hollen", "Cortez Masto").
_MEMBER_RE = re.compile(
    r"^(?:Mr|Mrs|Ms|Miss)\.?\s+(?P<surname>.+?)(?:\s+of\s+(?P<state>[A-Za-z .]+?))?$"
)

# "The PRESIDING OFFICER (Mr. <surname>)" — presiding officer named in a paren.
_PRESIDING_PAREN_RE = re.compile(
    r"^The\s+PRESIDING\s+OFFICER\s+\((?:Mr|Mrs|Ms|Miss)\.\s+(?P<surname>.+?)\)$",
    re.I,
)

# Bare procedural roles (no member named).
_ROLE_RE = re.compile(
    r"^The\s+(?P<role>PRESIDING\s+OFFICER|SPEAKER(?:\s+pro\s+tempore)?"
    r"|(?:ACTING\s+)?PRESIDENT\s+pro\s+tempore|VICE\s+PRESIDENT"
    r"|CHIEF\s+JUSTICE|CHAIR|CLERK)$",
    re.I,
)


def normalize_designation(speaker_raw: str, roster: CongressRoster) -> ResolvedSpeaker:
    """Resolve a CREC speaker designation against a chamber-scoped roster.

    Order matters: a parenthetical presiding officer is tried before the bare
    role, and member forms last. Roster is already chamber-scoped, so no chamber
    argument is needed.
    """
    s = (speaker_raw or "").strip()

    m = _PRESIDING_PAREN_RE.match(s)
    if m:
        res = _resolve_surname(m.group("surname"), None, roster)
        if res.member is not None:
            res.method = "presiding_parenthetical"
            return res
        return ResolvedSpeaker(role="presiding_officer", method="role", confidence=1.0)

    m = _ROLE_RE.match(s)
    if m:
        return ResolvedSpeaker(role=_role_slug(m.group("role")), method="role", confidence=1.0)

    m = _MEMBER_RE.match(s)
    if m:
        return _resolve_surname(m.group("surname"), m.group("state"), roster)

    return ResolvedSpeaker(method="unresolved")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_normalize.py -v`
Expected: PASS (all — 5 helper tests + 9 normalize tests).

- [ ] **Step 5: Commit**

```bash
git add src/crec_normalize.py tests/test_crec_normalize.py
git commit -m "feat(crec_normalize): normalize_designation dispatch (member/paren/role)"
```

---

### Task 6: `annotate_turns` — Phase-3 hand-off

**Files:**
- Modify: `src/crec_normalize.py`
- Test: `tests/test_crec_normalize.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_crec_normalize.py` (uses the real Phase-1 `CrecTurn`):

```python
# add to tests/test_crec_normalize.py
from src.govinfo import CrecTurn
from src.crec_normalize import annotate_turns


def test_annotate_turns_pairs_each_turn_with_resolution():
    turns = [
        CrecTurn("Mr. McCONNELL", "I move to proceed.", "g1", 0),
        CrecTurn("The PRESIDING OFFICER", "Without objection.", "g1", 1),
        CrecTurn("Ms. BALDWIN of Wisconsin", "I rise in support.", "g1", 2),
    ]
    pairs = annotate_turns(turns, _roster("senate"))
    assert [t.order for t, _ in pairs] == [0, 1, 2]
    assert pairs[0][1].member.bioguide == "M000355"
    assert pairs[1][1].role == "presiding_officer"
    assert pairs[2][1].member.bioguide == "B001230"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_normalize.py -k annotate -v`
Expected: FAIL — `ImportError: cannot import name 'annotate_turns'`.

- [ ] **Step 3: Write minimal implementation** — append to `src/crec_normalize.py`:

```python
def annotate_turns(turns, roster: CongressRoster) -> list[tuple]:
    """Pair each CrecTurn (from src/govinfo.py) with its ResolvedSpeaker.

    This is the hand-off to Phase 3 alignment: `[(turn, resolved), ...]` in the
    same order as the input turns.
    """
    return [(t, normalize_designation(t.speaker_raw, roster)) for t in turns]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_normalize.py -v`
Expected: PASS. Also run both new test files together:
`.venv/bin/python -m pytest tests/test_congress_roster.py tests/test_crec_normalize.py -v` — all pass.

- [ ] **Step 5: Commit**

```bash
git add src/crec_normalize.py tests/test_crec_normalize.py
git commit -m "feat(crec_normalize): annotate_turns for Phase 3 hand-off"
```

---

## Self-Review

**Spec coverage:**
- `congress_roster.py` (fetch/cache/build, chamber filter, surname index) — Tasks 1–3.
- `crec_normalize.py` (`ResolvedSpeaker`, state map, member/paren/role dispatch, degradation to unresolved, ambiguous→needs_review, `annotate_turns`) — Tasks 4–6.
- Fixture with the same-surname pair + female senator for the parenthetical — Task 1.
- Deferred per spec (own later plans): essentials `politician_id` linkage, historical Congresses, Stage-4 wiring, Phase-1 parser refinement. Not in this plan by design.

**Placeholder scan:** No TBD/TODO; every code and test step is complete, including the full `_STATE_NAME_TO_CODE` map.

**Type consistency:** `CongressMember(bioguide, full_name, last_name, state, district, chamber, party)` and `CongressRoster(chamber, members, _by_surname)` with `.by_surname()` are used identically across Tasks 1–6. `ResolvedSpeaker(member, role, method, confidence, needs_review)` fields match between definition (Task 4) and all assertions (Tasks 4–6). `normalize_designation(speaker_raw, roster)` and `_resolve_surname(surname, state_name, roster)` signatures are consistent between definition and call sites. `build_roster(raw, chamber)` and `load_current_roster(chamber, *, fetch, cache_path)` consistent throughout.

**Degradation check:** empty roster → `by_surname` returns `[]` → `_resolve_surname` returns `unresolved`; fetch failure inside `load_current_roster` propagates from `_default_fetch` only when there's no cache — acceptable (caller catches, matching the govinfo pattern). Ambiguous never emits a member.
