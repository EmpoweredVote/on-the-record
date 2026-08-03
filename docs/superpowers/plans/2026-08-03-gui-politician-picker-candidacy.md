# GUI Politician Picker Candidacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the GUI speaker-link picker show each person once, with the races they are actually a candidate in, so a curator can never silently link a meeting or quote to a person row that has no race edge.

**Architecture:** A new `gui/politicians.py` searches `essentials.politicians` directly over `DATABASE_URL` + `psycopg2`, exactly as the existing `gui/races.py` does for the race picker. Pure label-composition functions (`politician_display`, `candidacy_display`, `mark_duplicate_names`, `parse_name_query`) hold all the behaviour worth asserting; a single thin `search_politicians_safe` does the SQL. `gui/review_api.py` delegates to it and falls back to today's HTTP client when no `DATABASE_URL` is set. `gui/static/workspace.js` renders the server-composed label instead of re-joining fields client-side.

**Tech Stack:** Python 3.14, psycopg2, pytest, FastAPI + Jinja2, vanilla JS, PostgreSQL (Supabase `essentials` schema)

**Spec:** `docs/superpowers/specs/2026-08-03-gui-politician-picker-candidacy-design.md`

**Python interpreter:** this worktree has no `.venv`. Use the main checkout's:
`/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`
Export it once per shell: `export VP=/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`

**Baseline:** 1718 tests pass before any change (`$VP -m pytest tests/ -q`).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `gui/politicians.py` | Create | Name-query parsing, label composition, duplicate marking, and the direct-DB search. Mirrors `gui/races.py` in shape and error posture. |
| `tests/test_gui_politicians.py` | Create | Unit tests for the pure functions; `_FakeConn`-driven tests for the query. Mirrors `tests/test_gui_races.py`. |
| `gui/review_api.py` | Modify (`search_politicians_safe`, lines 174-195) | Delegate to `gui.politicians`; keep the HTTP path as a no-DB fallback. |
| `gui/static/workspace.js` | Modify (lines 152-159) | Render the two-line, server-composed label. |
| `gui/static/style.css` | Modify (after line 59) | Styles for the candidacy line and the warning treatments. |

`gui/politicians.py` stays a single focused module: it is one concern (find a
person and describe them well enough to pick), and `gui/races.py` — its direct
sibling — is 300 lines doing the same job for races. No split needed.

Nothing else changes. `src/essentials_client.py` is deliberately untouched:
`src/crec_essentials.py` depends on its HTTP `search_politicians` for the
automated CREC bridge, which must not require a database handle.

---

## Task 1: Query parsing and the primary label line

**Files:**
- Create: `gui/politicians.py`
- Test: `tests/test_gui_politicians.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_politicians.py`:

```python
"""Tests for gui.politicians — the speaker-link politician picker.

Mirrors tests/test_gui_races.py: pure label/parse functions tested directly,
the DB query tested through a fake cursor.
"""
from gui import politicians
from gui.politicians import parse_name_query, politician_display


def test_parse_name_query_splits_tokens():
    assert parse_name_query("Thomas Tiffany") == ["thomas", "tiffany"]


def test_parse_name_query_drops_punctuation_and_extra_space():
    assert parse_name_query("  O'Brien,  Mary-Kate ") == ["brien", "mary", "kate"]


def test_parse_name_query_empty():
    assert parse_name_query("") == []
    assert parse_name_query("   ") == []


def test_parse_name_query_drops_a_bare_middle_initial():
    # Every token becomes an AND-ed clause, so keeping "f" would require the
    # stored row to contain an F somewhere: "John F Kennedy" returns ZERO against
    # the stored "John Kennedy" (verified against prod). Dropping 1-char tokens
    # is what makes the query survive an initial the record doesn't carry.
    assert parse_name_query("John F Kennedy") == ["john", "kennedy"]
    assert parse_name_query("Thomas P. Tiffany") == ["thomas", "tiffany"]


def test_parse_name_query_drops_generational_suffixes():
    # name_suffix is its own column and is not searched, so "Wesley Hunt Jr"
    # returns zero against the stored "Wesley Hunt" (verified against prod).
    assert parse_name_query("Wesley Hunt Jr.") == ["wesley", "hunt"]
    assert parse_name_query("Harold Ford III") == ["harold", "ford"]


def test_politician_display_name_only():
    assert politician_display({"full_name": "Mandela Barnes"}) == "Mandela Barnes"


def test_politician_display_name_and_office():
    rec = {"full_name": "Francesca Hong", "office_title": "Representative to the Assembly"}
    assert politician_display(rec) == "Francesca Hong · Representative to the Assembly"


def test_politician_display_all_fields():
    rec = {
        "full_name": "Thomas P. Tiffany",
        "office_title": "U.S. Representative",
        "district_label": "Congressional District 7",
        "government_name": "United States Federal Government",
    }
    assert politician_display(rec) == (
        "Thomas P. Tiffany · U.S. Representative · Congressional District 7 "
        "· United States Federal Government"
    )


def test_politician_display_omits_empty_without_stray_separators():
    rec = {"full_name": "Janet Hong", "office_title": "", "district_label": "",
           "government_name": ""}
    assert politician_display(rec) == "Janet Hong"


def test_politician_display_skips_district_that_repeats_the_office():
    # essentials stores d.label == o.title for many single-seat offices
    # ("Texas Attorney General" twice); printing it twice is noise.
    rec = {"full_name": "Ken Paxton", "office_title": "Texas Attorney General",
           "district_label": "Texas Attorney General", "government_name": ""}
    assert politician_display(rec) == "Ken Paxton · Texas Attorney General"


def test_politician_display_drops_a_district_contained_in_the_office():
    # 1141 prod rows are redundant by containment, not exact equality.
    rec = {"full_name": "Tara T. Hong",
           "office_title": "Representative, 18th Middlesex District",
           "district_label": "18th Middlesex District", "government_name": ""}
    assert politician_display(rec) == (
        "Tara T. Hong · Representative, 18th Middlesex District")


def test_politician_display_keeps_the_longer_side_when_office_is_contained():
    # When they differ, the district is often the MORE informative side.
    rec = {"full_name": "Ken Paxton", "office_title": "Attorney General",
           "district_label": "Texas Attorney General", "government_name": ""}
    assert politician_display(rec) == "Ken Paxton · Texas Attorney General"


def test_politician_display_keeps_both_when_neither_contains_the_other():
    rec = {"full_name": "Thomas P. Tiffany", "office_title": "U.S. Representative",
           "district_label": "Congressional District 7", "government_name": ""}
    assert politician_display(rec) == (
        "Thomas P. Tiffany · U.S. Representative · Congressional District 7")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
export VP=/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python
$VP -m pytest tests/test_gui_politicians.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'gui.politicians'`

- [ ] **Step 3: Write the minimal implementation**

Create `gui/politicians.py`:

```python
"""Search essentials.politicians for the GUI speaker-link picker (via
DATABASE_URL + psycopg2, like gui.races and gui.publish_api). Best-effort: when
the DB isn't configured or a query fails, search returns no results rather than
raising — mirroring gui.races.search_races_safe.

Why this exists rather than calling ev-accounts /candidates/search-by-name:

essentials.politicians is the *person* table and candidacy is an edge in
essentials.race_candidates, so a picker row that shows only name + office cannot
tell you whether the person you're about to link is in the race you care about.
For Thomas P. Tiffany the office-holding row IS the WI Governor candidate; for
Francesca Hong the office-holding row is NOT (a hand-add minted a second person
row and the race edge landed on that one). Picking wrong is silent and costly:
publish._reconcile_event_races derives a meeting's races solely from
politician_id -> race_candidates, and Read & Rank reaches quotes only through the
same edge. So every row carries its candidacies, and duplicate names are flagged.

Two more upstream sharp edges this module files down: office_current_holder
returns one row per office, so someone holding both a real office and a
"Candidate for ..." placeholder fans out to two rows with the same politician_id
(DISTINCT ON collapses it); and matching the whole query as one substring means
"Thomas Tiffany" misses "Thomas P. Tiffany" (per-token matching fixes it).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import psycopg2

from .races import race_display

# Name fields a query token is matched against, in the order the OR is built.
_NAME_FIELDS = ("full_name", "preferred_name", "first_name", "last_name")

# Candidacies shown in full before collapsing the tail into "+N more".
_MAX_CANDIDACIES = 3

# candidate_status values that mean the person is actually contesting the race.
# essentials uses {active, filed, withdrawn}; "filed" is 111 live rows and means
# the paperwork is in, so it earns the "running:" lead exactly like "active".
# Note publish.resolve_races_for_politicians ignores status entirely, so ALL
# three resolve the race on publish — the distinction here is informational.
_RUNNING_STATUSES = frozenset({"active", "filed"})

# The label for a person row with no race_candidates edge. Public because it is
# the warning case, and callers assert on it.
NO_CANDIDACIES = "no candidacies"

# Query tokens that carry no matchable signal. Generational suffixes live in
# their own `name_suffix` column, which _NAME_FIELDS doesn't search, so keeping
# them would AND in a clause nothing can satisfy.
_NOISE_TOKENS = frozenset({"jr", "sr", "ii", "iii", "iv"})


def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens worth matching on: bare initials and
    generational suffixes dropped. Shared by parse_name_query (which needs tokens
    a stored row could plausibly contain) and _dupe_key (which needs tokens that
    carry identity) — the two want the same normalization for the same reason."""
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) > 1 and t not in _NOISE_TOKENS]


def parse_name_query(q: str) -> list[str]:
    """Matchable lowercase tokens from a raw picker query.

    Each token becomes its own AND-ed clause, which is what lets "Thomas
    Tiffany" match the stored "Thomas P. Tiffany" — but it cuts both ways, so
    anything the stored row can't possibly carry has to be dropped or the whole
    query returns nothing. Two such cases, both verified against prod:

      "John F Kennedy"  -> stored "John Kennedy" has no F   -> 0 rows
      "Wesley Hunt Jr"  -> name_suffix isn't in _NAME_FIELDS -> 0 rows

    So single-character tokens (bare middle initials) and generational suffixes
    are dropped. Word order never mattered either way.
    """
    return _tokens(q)


def politician_display(rec: dict) -> str:
    """'Thomas P. Tiffany · U.S. Representative · Congressional District 7 ·
    United States Federal Government' — identity, empty parts omitted.

    office_title and district_label are frequently redundant: 112 prod rows store
    d.label == o.title outright ("Texas Attorney General" twice) and another 1141
    have one contained in the other ("Representative, 18th Middlesex District" /
    "18th Middlesex District"). Printing both crowds out the part that actually
    disambiguates, so when one contains the other only the longer survives —
    which side that is varies ("Attorney General" vs "Texas Attorney General"
    keeps the district).
    """
    office = (rec.get("office_title") or "").strip()
    district = (rec.get("district_label") or "").strip()
    if office and district:
        lower_office, lower_district = office.lower(), district.lower()
        if lower_district in lower_office:
            district = ""
        elif lower_office in lower_district:
            office, district = district, ""
    parts = [
        (rec.get("full_name") or "").strip(),
        office,
        district,
        (rec.get("government_name") or "").strip(),
    ]
    return " · ".join(p for p in parts if p)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
$VP -m pytest tests/test_gui_politicians.py -q
```

Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add gui/politicians.py tests/test_gui_politicians.py
git commit -m "feat(gui): add politician query parsing and identity label

Per-token parsing so 'Thomas Tiffany' can match the stored 'Thomas P. Tiffany'.
The identity line adds district_label to today's name/office/government, and
drops it when it merely repeats the office title (essentials stores d.label ==
o.title for many single-seat offices).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: The candidacy line

**Files:**
- Modify: `gui/politicians.py`
- Test: `tests/test_gui_politicians.py`

The candidacy line is what would have caught the Francesca Hong case. Labels are
composed with `races.race_display()` so a candidacy reads identically to the race
picker's own label for the same race.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_politicians.py`:

```python
from gui.politicians import candidacy_display
from gui.races import race_display


def _cand(position_name="Governor", state="WI", primary_party="Republican",
          election_type="primary", year=2026, status="active"):
    return {"position_name": position_name, "state": state,
            "primary_party": primary_party, "election_type": election_type,
            "year": year, "status": status}


def test_candidacy_display_none_is_a_warning_string():
    assert candidacy_display([]) == "no candidacies"
    assert candidacy_display(None) == "no candidacies"


def test_candidacy_display_one_matches_race_display():
    expected = race_display("Governor", 2026, "WI", "Republican", "primary")
    assert candidacy_display([_cand()]) == f"running: {expected}"
    assert "WI · Governor · Republican primary · 2026" in candidacy_display([_cand()])


def test_candidacy_display_joins_several_with_semicolons():
    out = candidacy_display([_cand(), _cand(election_type="general", primary_party="")])
    assert out.startswith("running: ")
    assert out.count("; ") == 1
    assert "Republican primary" in out and "General" in out


def test_candidacy_display_prefixes_non_active_status():
    # No "running:" lead when nothing is active — "running: withdrawn: ..." reads
    # as nonsense, and a withdrawn-only person is exactly the case a curator
    # needs to notice.
    out = candidacy_display([_cand(status="withdrawn")])
    assert out == "withdrawn: WI · Governor · Republican primary · 2026"


def test_candidacy_display_treats_filed_as_running():
    # candidate_status is {active, filed, withdrawn} and "filed" is 111 live rows.
    # A filed candidate IS contesting the race, so prefixing it like a withdrawal
    # and dropping the "running:" lead would misread the data.
    assert candidacy_display([_cand(status="filed")]) == (
        "running: WI · Governor · Republican primary · 2026")


def test_candidacy_display_prefix_is_the_actual_status():
    # Pins that the prefix comes from the data, not a hardcoded "withdrawn".
    assert candidacy_display([_cand(status="disqualified")]).startswith("disqualified: ")


def test_candidacy_display_missing_status_counts_as_running():
    c = _cand()
    del c["status"]
    assert candidacy_display([c]).startswith("running: ")


def test_candidacy_display_normalizes_status_case_and_whitespace():
    assert candidacy_display([_cand(status="  ACTIVE ")]).startswith("running: ")


def test_candidacy_display_exactly_three_has_no_tail():
    cands = [_cand(position_name=f"Office {i}") for i in range(3)]
    out = candidacy_display(cands)
    assert "more" not in out
    assert out.count("; ") == 2


def test_candidacy_display_leads_with_running_when_any_is_active():
    out = candidacy_display([_cand(status="withdrawn"),
                             _cand(position_name="Senate")])
    assert out.startswith("running: ")
    assert "withdrawn: WI · Governor" in out


def test_candidacy_display_caps_at_three_and_counts_the_rest():
    cands = [_cand(position_name=f"Office {i}") for i in range(5)]
    out = candidacy_display(cands)
    # 3 shown => 2 separators between them, plus one before the "+N more" tail,
    # which is itself just another item in the list.
    assert out.count("; ") == 3
    assert out.endswith("; +2 more")
    assert "Office 3" not in out


def test_candidacy_display_skips_malformed_entries():
    out = candidacy_display(["not-a-dict", _cand(), None])
    assert out == "running: WI · Governor · Republican primary · 2026"


def test_candidacy_display_all_malformed_reads_as_none():
    assert candidacy_display(["not-a-dict", None]) == "no candidacies"

```

- [ ] **Step 2: Run tests to verify they fail**

```bash
$VP -m pytest tests/test_gui_politicians.py -q
```

Expected: `ImportError: cannot import name 'candidacy_display' from 'gui.politicians'`

- [ ] **Step 3: Write the minimal implementation**

Append to `gui/politicians.py`:

```python
def _status(c: dict) -> str:
    """A candidacy's normalized candidate_status. Missing means 'active', matching
    publish.resolve_races_for_politicians, which doesn't filter on status at all."""
    return (c.get("status") or "active").strip().lower()


def _one_candidacy(c) -> Optional[tuple[str, bool]]:
    """('WI · Governor · Republican primary · 2026', True) — the label plus
    whether it's a live run. A non-running status is prefixed
    ('withdrawn: <that>', False). None when the entry isn't a usable dict.

    Returning both together keeps the dict validated and the status normalized
    exactly once per entry.
    """
    if not isinstance(c, dict):
        return None
    label = race_display(
        c.get("position_name") or "",
        c.get("year"),
        c.get("state"),
        c.get("primary_party"),
        c.get("election_type"),
    )
    if not label:
        return None
    status = _status(c)
    running = status in _RUNNING_STATUSES
    return (label if running else f"{status}: {label}", running)


def candidacy_display(candidacies) -> str:
    """'running: <race>; <race>' — or the literal 'no candidacies', which is the
    signal that this person row has no race_candidates edge and so cannot carry a
    meeting or a quote into a race.

    The 'running:' lead is dropped when nothing is running, because
    'running: withdrawn: ...' reads as nonsense and a withdrawn-only person is
    precisely the case a curator needs to notice.

    Capped at _MAX_CANDIDACIES with a '+N more' tail so one row can't run away.
    Malformed entries are skipped rather than breaking the whole label.
    """
    pairs = [p for p in (_one_candidacy(c) for c in (candidacies or [])) if p]
    if not pairs:
        return NO_CANDIDACIES
    shown = pairs[:_MAX_CANDIDACIES]
    text = "; ".join(label for label, _running in shown)
    extra = len(pairs) - len(shown)
    if extra:
        text += f"; +{extra} more"
    return f"running: {text}" if any(running for _label, running in pairs) else text
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
$VP -m pytest tests/test_gui_politicians.py -q
```

Expected: `26 passed`

- [ ] **Step 5: Commit**

```bash
git add gui/politicians.py tests/test_gui_politicians.py
git commit -m "feat(gui): compose the picker candidacy line

Reuses races.race_display() so a candidacy reads exactly like the race picker's
own label. 'no candidacies' is the signal that would have caught the Francesca
Hong case: a person row with no race_candidates edge cannot carry a meeting or a
quote into a race.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Duplicate-name marking

**Files:**
- Modify: `gui/politicians.py`
- Test: `tests/test_gui_politicians.py`

Flags the whole group rather than guessing which row is right — the candidacy
line already answers that. The marker's only job is to stop a curator picking on
autopilot.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_politicians.py`:

```python
from gui.politicians import mark_duplicate_names


def test_mark_duplicate_names_leaves_unique_names_alone():
    rows = [{"full_name": "Mandela Barnes"}, {"full_name": "Kelda Roys"}]
    out = mark_duplicate_names(rows)
    assert [r.get("duplicate_note", "") for r in out] == ["", ""]


def test_mark_duplicate_names_flags_both_rows_of_a_pair():
    rows = [{"full_name": "Francesca Hong"}, {"full_name": "Francesca Hong"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 2 results share this name" for r in out)


def test_mark_duplicate_names_ignores_middle_initials_and_case():
    rows = [{"full_name": "Thomas P. Tiffany"}, {"full_name": "thomas tiffany"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] for r in out)


def test_mark_duplicate_names_counts_the_whole_group():
    rows = [{"full_name": "Mike Rogers"} for _ in range(3)]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 3 results share this name" for r in out)


def test_mark_duplicate_names_flags_genuinely_different_people_too():
    # Two real distinct Mike Rogers still both get flagged — correct: the
    # curator must look, and we can't tell them apart from names alone.
    rows = [{"full_name": "Mike Rogers", "office_title": "U.S. Representative"},
            {"full_name": "Mike Rogers", "office_title": "Senator"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] for r in out)


def test_mark_duplicate_names_does_not_collide_two_juniors():
    # Without dropping the suffix both would key to "john jr" — prod has plenty
    # of these (John G. Roberts Jr., John P. Wiley Jr.).
    rows = [{"full_name": "John G. Roberts Jr."}, {"full_name": "John P. Wiley Jr."}]
    out = mark_duplicate_names(rows)
    assert [r["duplicate_note"] for r in out] == ["", ""]


def test_mark_duplicate_names_still_matches_across_a_suffix():
    rows = [{"full_name": "Harold Ford III"}, {"full_name": "Harold Ford"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 2 results share this name" for r in out)


def test_mark_duplicate_names_does_not_group_nameless_rows():
    # Two rows with no name are not "the same person" — without the empty-key
    # guard every nameless row would flag every other one.
    rows = [{"full_name": ""}, {"full_name": None}, {}]
    out = mark_duplicate_names(rows)
    assert [r["duplicate_note"] for r in out] == ["", "", ""]


def test_mark_duplicate_names_collapses_a_middle_name_on_purpose():
    # first+last for 3+ tokens means a full middle name is dropped, so these
    # collide. That is the intended bias: a needless second glance costs nothing,
    # a missed duplicate costs a silently detached meeting. Pinned so a later
    # "fix" to key on all tokens can't quietly reopen the false-negative case.
    rows = [{"full_name": "Mary Kate Olsen"}, {"full_name": "Mary Olsen"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 2 results share this name" for r in out)


def test_mark_duplicate_names_does_not_flag_a_lone_row():
    out = mark_duplicate_names([{"full_name": "Thomas P. Tiffany"}])
    assert out[0]["duplicate_note"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
$VP -m pytest tests/test_gui_politicians.py -q
```

Expected: `ImportError: cannot import name 'mark_duplicate_names'`

- [ ] **Step 3: Write the minimal implementation**

Append to `gui/politicians.py`:

```python
def _dupe_key(full_name: str) -> str:
    """'thomas tiffany' from 'Thomas P. Tiffany' — a loose identity key for
    spotting two rows that a curator would read as the same person.

    Bare middle initials and generational suffixes are dropped for the same
    reason parse_name_query drops them: they're display choices, not identity.
    Dropping the suffix is what stops the key collapsing to the suffix itself —
    'John G. Roberts Jr.' and 'John P. Wiley Jr.' would BOTH key to 'john jr'
    and get flagged as the same person, and prod is full of such names.
    """
    tokens = _tokens(full_name)
    if len(tokens) <= 2:
        return " ".join(tokens)
    return f"{tokens[0]} {tokens[-1]}"


def mark_duplicate_names(results: list[dict]) -> list[dict]:
    """Set `duplicate_note` on every row whose normalized name is shared with
    another row in the same response ('' on the rest). Mutates and returns the
    list.

    Deliberately flags the WHOLE group rather than guessing which row is right:
    two rows can be one person split by a bad hand-add, or two genuinely
    different people with the same name, and nothing in the name distinguishes
    those cases. The candidacy line is what tells the curator which to pick; this
    marker only says "look before you click".

    Known blind spot: nickname variants aren't caught, because "dan brotman" and
    "daniel brotman" are different keys. Prod has 21 such pairs and 9 of them have
    the dangerous shape — one row with a race edge, its twin with none (Dan/Daniel
    Brotman, Mike/Michael Thompson, Rick/Richard Bennett, ...). Closing it needs a
    nickname map; keying on the first initial instead would group "Angela Davis"
    with "Andrew Davis" and drown the signal on common surnames. The candidacy
    line still distinguishes those pairs, which is the safeguard that matters.

    The count is per-response, not global — the search is limited, so a name with
    more rows than fit can show a smaller number. Hence "results share this name"
    rather than "records exist".
    """
    keys = [_dupe_key(r.get("full_name") or "") for r in results]
    counts: dict[str, int] = {}
    for key in keys:
        if key:                      # two nameless rows aren't the same person
            counts[key] = counts.get(key, 0) + 1
    for r, key in zip(results, keys):
        n = counts.get(key, 0)
        r["duplicate_note"] = f"⚠ {n} results share this name" if n > 1 else ""
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
$VP -m pytest tests/test_gui_politicians.py -q
```

Expected: `36 passed`

- [ ] **Step 5: Commit**

```bash
git add gui/politicians.py tests/test_gui_politicians.py
git commit -m "feat(gui): flag duplicate names in the politician picker

85 duplicate-name groups exist among active politicians, 45 where exactly one
row is an active candidate. Flags the whole group rather than guessing which is
right — the candidacy line answers that; this only says 'look before you click'.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: The database search

**Files:**
- Modify: `gui/politicians.py`
- Test: `tests/test_gui_politicians.py`

The query was verified against production while writing the spec: Talarico and
Paxton collapse from two rows to one, both Francesca Hong rows come back
distinguishable, and worst case (`smith`) runs in 101 ms.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_politicians.py`:

```python
# --- end-to-end search wiring against a fake cursor ---
#
# Add `import json`, `import os` and `import pytest` to the imports at the top of
# the file — the candidacies-as-text test needs json, and the integration tests at
# the bottom need os and pytest.

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.executed = (sql, params)

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


# Row order matches the SELECT in _SEARCH_SQL:
# id, full_name, slug, office_title, district_label, government_name, candidacies
_TIFFANY_ROW = (
    "a8f96324-50ac-4fa1-b57b-47a998306fe8", "Thomas P. Tiffany", None,
    "U.S. Representative", "Congressional District 7",
    "United States Federal Government",
    [{"position_name": "Governor", "state": "WI", "primary_party": "Republican",
      "election_type": "primary", "year": 2026, "status": "active"}],
)


def _fake_db(monkeypatch, rows):
    cur = _FakeCursor(rows)
    monkeypatch.setattr(politicians, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(politicians.psycopg2, "connect", lambda url: _FakeConn(cur))
    return cur


def test_search_maps_a_row_to_a_labelled_result(monkeypatch):
    _fake_db(monkeypatch, [_TIFFANY_ROW])
    out = politicians.search_politicians_safe("thomas tiffany")
    assert out["error"] is None
    (r,) = out["results"]
    assert r["politician_id"] == "a8f96324-50ac-4fa1-b57b-47a998306fe8"
    assert r["politician_slug"] is None
    assert r["full_name"] == "Thomas P. Tiffany"
    assert r["display"] == (
        "Thomas P. Tiffany · U.S. Representative · Congressional District 7 "
        "· United States Federal Government"
    )
    assert r["candidacy_display"] == "running: WI · Governor · Republican primary · 2026"
    assert r["candidacy_warn"] is False
    assert r["duplicate_note"] == ""


def test_search_builds_one_and_ed_clause_per_token(monkeypatch):
    cur = _fake_db(monkeypatch, [])
    politicians.search_politicians_safe("thomas tiffany")
    sql, params = cur.executed
    # 4 name fields x 2 tokens, plus the limit
    assert len(params) == 9
    assert params[-1] == 10
    assert params.count("%thomas%") == 4 and params.count("%tiffany%") == 4


def test_search_dedupes_on_politician_id_and_ranks_outside_it(monkeypatch):
    cur = _fake_db(monkeypatch, [])
    politicians.search_politicians_safe("paxton")
    sql, _ = cur.executed
    # collapses the office_current_holder fan-out
    assert "DISTINCT ON (p.id)" in sql
    # a real office beats a "Candidate for ..." placeholder inside the DISTINCT.
    # Doubled % because the SQL still has to survive psycopg2's own parameter
    # binding after str.format has run — str.format leaves %% untouched.
    assert "ILIKE 'Candidate for%%'" in sql
    # ranking + LIMIT sit OUTSIDE the dedupe, so a candidate can't be truncated
    # away by non-candidates on a common surname
    assert "(candidacies IS NULL)" in sql
    outer = sql.rsplit(") t", 1)[1]
    assert "ORDER BY" in outer and "LIMIT" in outer


def test_search_flags_duplicate_names(monkeypatch):
    hong_cand = ("dfe4ad6a", "Francesca Hong", None, "", "", "",
                 [{"position_name": "Governor", "state": "WI",
                   "primary_party": "Democratic", "election_type": "primary",
                   "year": 2026, "status": "active"}])
    hong_office = ("f1212497", "Francesca Hong", None,
                   "Representative to the Assembly", "Assembly District 76", "", None)
    _fake_db(monkeypatch, [hong_cand, hong_office])
    out = politicians.search_politicians_safe("hong")
    assert [r["duplicate_note"] for r in out["results"]] == [
        "⚠ 2 results share this name"] * 2
    # the one with no race edge says so — the signal that was missing
    assert out["results"][1]["candidacy_display"] == "no candidacies"
    assert out["results"][1]["candidacy_warn"] is True
    assert out["results"][0]["candidacy_warn"] is False


def test_search_parses_candidacies_delivered_as_json_text(monkeypatch):
    # psycopg2 hands back json as str unless a typecaster is registered
    row = list(_TIFFANY_ROW)
    row[6] = json.dumps(_TIFFANY_ROW[6])
    _fake_db(monkeypatch, [tuple(row)])
    out = politicians.search_politicians_safe("tiffany")
    assert out["results"][0]["candidacy_display"].startswith("running: WI · Governor")


def test_search_treats_null_candidacies_as_none(monkeypatch):
    row = list(_TIFFANY_ROW)
    row[6] = None
    _fake_db(monkeypatch, [tuple(row)])
    out = politicians.search_politicians_safe("tiffany")
    assert out["results"][0]["candidacies"] == []
    assert out["results"][0]["candidacy_display"] == "no candidacies"
    assert out["results"][0]["candidacy_warn"] is True


def test_search_short_query_returns_empty_without_connecting(monkeypatch):
    cur = _fake_db(monkeypatch, [_TIFFANY_ROW])
    out = politicians.search_politicians_safe("x")
    assert out == {"results": [], "error": None}
    assert cur.executed is None


def test_search_query_with_no_usable_tokens_returns_empty(monkeypatch):
    cur = _fake_db(monkeypatch, [_TIFFANY_ROW])
    out = politicians.search_politicians_safe("!!!")     # >=2 chars, no tokens
    assert out == {"results": [], "error": None}
    assert cur.executed is None


def test_search_no_db_url_returns_empty(monkeypatch):
    monkeypatch.setattr(politicians, "_db_url", lambda: None)
    assert politicians.search_politicians_safe("tiffany") == {"results": [], "error": None}


def test_search_swallows_db_errors(monkeypatch):
    monkeypatch.setattr(politicians, "_db_url", lambda: "postgres://fake")

    def boom(url):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(politicians.psycopg2, "connect", boom)
    out = politicians.search_politicians_safe("tiffany")
    assert out["results"] == []
    assert out["error"]                       # a message, not a crash


def test_search_honours_an_explicit_limit(monkeypatch):
    cur = _fake_db(monkeypatch, [])
    politicians.search_politicians_safe("tiffany", limit=25)
    _sql, params = cur.executed
    assert params[-1] == 25


# --- integration: the two things a fake cursor structurally cannot prove -------
# The SQL-string assertions above would pass unchanged if the DISTINCT ON tie-break
# sorted the wrong way, or if the outer ranking never took effect — the fake cursor
# returns rows in the order given. These run only when DATABASE_URL is set.

_needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"),
                               reason="needs DATABASE_URL (live essentials schema)")


@_needs_db
def test_live_fanout_collapses_to_the_real_office():
    # Harriet M. Hageman holds BOTH "U.S. Representative" and the placeholder
    # "Candidate for U.S. Senate — Wyoming" in office_current_holder. One row must
    # come back, carrying the real office — if the boolean tie-break inverted, the
    # placeholder would win and nothing else would notice.
    out = politicians.search_politicians_safe("hageman")
    hers = [r for r in out["results"] if r["full_name"].startswith("Harriet")]
    assert len(hers) == 1, [r["display"] for r in hers]
    assert hers[0]["office_title"] == "U.S. Representative"
    assert "Candidate for" not in hers[0]["display"]
    assert "U.S. Senate Wyoming" in hers[0]["candidacy_display"]


@_needs_db
def test_live_candidate_row_outranks_its_office_holding_twin():
    # Two Francesca Hong person rows exist; only one carries the WI Governor edge,
    # and it must sort FIRST so the curator's eye lands on the right one.
    out = politicians.search_politicians_safe("hong")
    hongs = [r for r in out["results"] if r["full_name"] == "Francesca Hong"]
    assert len(hongs) == 2, [r["display"] for r in hongs]
    assert hongs[0]["candidacy_warn"] is False
    assert "Governor" in hongs[0]["candidacy_display"]
    assert hongs[1]["candidacy_warn"] is True
    assert hongs[1]["candidacy_display"] == politicians.NO_CANDIDACIES
    assert all(r["duplicate_note"] for r in hongs)


@_needs_db
def test_live_an_inactive_person_who_is_an_active_candidate_is_findable():
    # is_active = false but candidate_status = active (Murphy TX council 2026).
    # Before the IN clause this returned zero — a silent "no such person".
    out = politicians.search_politicians_safe("andrew chase")
    assert [r["full_name"] for r in out["results"]] == ["Andrew Chase"]
    assert out["results"][0]["candidacy_warn"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
$VP -m pytest tests/test_gui_politicians.py -q
```

Expected: failures with `AttributeError: module 'gui.politicians' has no attribute '_db_url'`

- [ ] **Step 3: Write the minimal implementation**

Append to `gui/politicians.py`:

```python
# DISTINCT ON requires its ORDER BY to lead with p.id, which is not the order we
# want to present. Limiting at that level would truncate an arbitrary N rows
# BEFORE ranking, so on a common surname the candidate could be cut entirely.
# Hence the wrap: dedupe inside, rank and LIMIT outside.
#
# is_active alone was too strict: three people are is_active = false while holding
# an ACTIVE candidacy (Andrew Chase / Laura Deel, Murphy TX council 2026; Gopal
# Ponangi, Frisco 2025), and the picker returned zero for them — indistinguishable
# from "no such person", the very failure this module exists to remove. The IN is
# deliberately UNCORRELATED so Postgres hashes it once: the correlated EXISTS form
# measured 1700ms against 160ms, while this measures 162ms. Rows that are inactive
# AND only ever withdrawn stay hidden.
_SEARCH_SQL = """
SELECT * FROM (
  SELECT DISTINCT ON (p.id)
         p.id, p.full_name, p.slug,
         COALESCE(o.title, '') AS office_title,
         COALESCE(d.label, '') AS district_label,
         COALESCE(g.name, '')  AS government_name,
         cand.candidacies
  FROM essentials.politicians p
  LEFT JOIN essentials.office_current_holder och ON och.politician_id = p.id
  LEFT JOIN essentials.offices     o  ON o.id  = och.office_id
  LEFT JOIN essentials.districts   d  ON d.id  = o.district_id
  LEFT JOIN essentials.chambers    ch ON ch.id = o.chamber_id
  LEFT JOIN essentials.governments g  ON g.id  = ch.government_id
  LEFT JOIN LATERAL (
    SELECT json_agg(json_build_object(
             'position_name',  r.position_name,
             'state',          e.state,
             'primary_party',  r.primary_party,
             'election_type',  e.election_type,
             'year',           EXTRACT(YEAR FROM e.election_date)::int,
             'status',         COALESCE(rc.candidate_status, 'active')
           ) ORDER BY e.election_date) AS candidacies
    FROM essentials.race_candidates rc
    JOIN essentials.races     r ON r.id = rc.race_id
    JOIN essentials.elections e ON e.id = r.election_id
    WHERE rc.politician_id = p.id
  ) cand ON true
  WHERE (p.is_active = true
         OR p.id IN (SELECT rc2.politician_id
                     FROM essentials.race_candidates rc2
                     WHERE COALESCE(rc2.candidate_status, 'active') <> 'withdrawn'))
    AND {token_clauses}
  ORDER BY p.id,
           (COALESCE(o.title, '') ILIKE 'Candidate for%%'),
           o.title NULLS LAST
) t
ORDER BY (candidacies IS NULL),
         (office_title = ''),
         full_name
LIMIT %s
"""


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def db_configured() -> bool:
    """Whether a direct-DB search is possible at all. Public because callers must
    pre-check: search_politicians_safe returns {"results": [], "error": None}
    with no DATABASE_URL, which is indistinguishable from "no matches", so the
    capability question can't be answered from its result."""
    return _db_url() is not None


def _token_clauses(tokens: list[str]) -> tuple[str, list]:
    """(sql, params) for the name filter: one AND-ed clause per token, each an OR
    over _NAME_FIELDS. Only field names — from a module constant — are
    interpolated; every user value is a bound parameter.

    Known cost: essentials has a trigram index on f_unaccent(lower(full_name)),
    which this predicate cannot use (no lower(), and the OR across three
    unindexed columns would defeat it anyway), so each search seq-scans the
    politicians table — 160ms today and O(table size). Recorded as a spec
    follow-up: fixing it needs matching expression indexes in ev-accounts'
    schema, and the secondary fields can't simply be dropped (23 active rows
    match only on preferred_name, which is the nickname recall).
    """
    clauses = []
    params: list = []
    for tok in tokens:
        ors = " OR ".join(
            f"public.f_unaccent(p.{f}) ILIKE public.f_unaccent(%s)"
            for f in _NAME_FIELDS
        )
        clauses.append(f"({ors})")
        params.extend([f"%{tok}%"] * len(_NAME_FIELDS))
    return " AND ".join(clauses), params


def _coerce_candidacies(raw) -> list:
    """psycopg2 gives json back as a list when a typecaster is registered and as
    text otherwise; NULL means no race_candidates rows at all."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return list(raw) if isinstance(raw, list) else []


def search_politicians_safe(q: str, *, limit: int = 10) -> dict:
    """Best-effort politician search for the link picker. Returns
    {"results": [...], "error": None|str} — never raises.

    Every result carries `display` (identity), `candidacy_display` (which races
    this person is actually a candidate in) and `duplicate_note`, so the curator
    can see which of two same-named rows holds the race edge.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": [], "error": None}
    tokens = parse_name_query(query)
    if not tokens:
        return {"results": [], "error": None}
    url = _db_url()
    if not url:
        return {"results": [], "error": None}

    where_sql, params = _token_clauses(tokens)
    params.append(limit)
    sql = _SEARCH_SQL.format(token_clauses=where_sql)

    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:  # DB down / auth / schema — stay best-effort
        return {"results": [], "error": f"politician search failed: {exc}"}

    results = []
    for pid, full_name, slug, office, district, government, cands in rows:
        rec = {
            "politician_id": str(pid) if pid else None,
            "politician_slug": slug,
            "full_name": full_name or "",
            "office_title": office or "",
            "district_label": district or "",
            "government_name": government or "",
            "candidacies": _coerce_candidacies(cands),
        }
        rec["display"] = politician_display(rec)
        rec["candidacy_display"] = candidacy_display(rec["candidacies"])
        # Explicit flag rather than letting the client match on the label text:
        # Task 6 styles this row as a warning, and a reworded label must not be
        # able to silently turn that styling off. Derived FROM the label rather
        # than from `candidacies`, so the two can never disagree — a row whose
        # candidacies all fail to render would otherwise say "no candidacies"
        # while telling the client not to warn.
        rec["candidacy_warn"] = rec["candidacy_display"] == NO_CANDIDACIES
        results.append(rec)
    return {"results": mark_duplicate_names(results), "error": None}
```

Note the `%%` in `ILIKE 'Candidate for%%'`: `_SEARCH_SQL` goes through
`str.format` for the token clauses and then through psycopg2's `%s` binding, so a
literal percent must be doubled or psycopg2 reads it as a placeholder.

- [ ] **Step 4: Run tests to verify they pass**

```bash
$VP -m pytest tests/test_gui_politicians.py -q
```

Expected: `47 passed, 3 skipped` without `DATABASE_URL` in the environment, or
`50 passed` with it. The three integration tests skip themselves when there's no
database.

- [ ] **Step 5: Verify the SQL against the real database**

This is the step the fake cursor cannot cover — that the SQL is valid and returns
what the spec claims. Requires `DATABASE_URL`.

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/.claude/worktrees/feat+gui-politician-picker-candidacy
set -a && . /Users/chrisandrews/Documents/GitHub/on-the-record/.env.local && set +a
$VP - <<'PY'
from gui import politicians
for q in ("thomas tiffany", "hong", "paxton", "talarico", "smith"):
    out = politicians.search_politicians_safe(q)
    print(f"--- {q}  error={out['error']}")
    for r in out["results"][:6]:
        print("   ", r["display"], "|", r["candidacy_display"], "|", r["duplicate_note"])
PY
```

Expected, matching the spec's verification table:
- `thomas tiffany` → exactly one row, `U.S. Representative · Congressional District 7 · …`, `running: WI · Governor · Republican primary · 2026`
- `hong` → **two** Francesca Hong rows, the first `running: WI · Governor · Democratic primary · 2026`, the second `no candidacies`, both marked `⚠ 2 results share this name`
- `paxton` → Ken Paxton **once** (not twice) with `Texas Attorney General` and a U.S. Senate candidacy; Angela Paxton separately with `no candidacies`
- `talarico` → James Talarico **once** (not twice), `Representative · TX House District 50`, with a U.S. Senate candidacy
- `smith` → every returned row carries a candidacy (non-candidates ranked out)

If `paxton` or `talarico` returns two rows, `DISTINCT ON` is not taking effect —
check that the outer `SELECT *` did not lose it.

- [ ] **Step 6: Commit**

```bash
git add gui/politicians.py tests/test_gui_politicians.py
git commit -m "feat(gui): direct-DB politician search with candidacy context

DISTINCT ON (p.id) collapses the office_current_holder fan-out that showed
Talarico and Paxton twice. Per-token matching makes 'Thomas Tiffany' find
'Thomas P. Tiffany'. A LATERAL over race_candidates gives every row the races
the person is actually a candidate in.

Ranking and LIMIT sit outside the deduplicating select on purpose: DISTINCT ON
forces ORDER BY p.id, so limiting there would truncate before ranking and could
drop the candidate entirely on a common surname.

Verified against production: 101ms worst case (smith).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Wire it into review_api with an HTTP fallback

**Files:**
- Modify: `gui/review_api.py:174-195`
- Test: `tests/test_gui_politicians.py`

Keeps today's HTTP path alive for a GUI run without `DATABASE_URL`, so the picker
degrades to name + office rather than returning nothing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_politicians.py`:

```python
# --- review_api delegation ---

def test_review_api_delegates_to_the_direct_db_search(monkeypatch):
    from gui import review_api
    sentinel = {"results": [{"politician_id": "x", "display": "X"}], "error": None}
    monkeypatch.setattr(politicians, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(politicians, "search_politicians_safe",
                        lambda q, limit=10: sentinel)
    assert review_api.search_politicians_safe("tiffany") is sentinel


def test_review_api_falls_back_to_http_without_a_db_url(monkeypatch):
    from gui import review_api
    monkeypatch.setattr(politicians, "_db_url", lambda: None)
    calls = []

    def fake_http(q, limit=10):
        calls.append(q)
        return [{"id": "http-id", "slug": "http-slug", "full_name": "Tom Tiffany",
                 "office_title": "U.S. Representative", "district_label": "",
                 "government_name": "United States Federal Government",
                 "is_incumbent": True}]

    monkeypatch.setattr("src.essentials_client.search_politicians", fake_http)
    out = review_api.search_politicians_safe("tiffany")
    assert calls == ["tiffany"]
    (r,) = out["results"]
    assert r["politician_id"] == "http-id"
    assert r["display"] == (
        "Tom Tiffany · U.S. Representative · United States Federal Government")
    # no DB means no candidacy data — the renderer must omit line 2, not lie
    assert r["candidacy_display"] == ""
    assert r["candidacy_warn"] is False
    assert r["duplicate_note"] == ""


def test_review_api_does_not_touch_http_when_a_db_url_is_set(monkeypatch):
    # The real regression surface: a wiring slip that still calls the HTTP client
    # would lose every row's candidacy data, and asserting only the return value
    # would not notice. Track the call as a side effect rather than relying on an
    # exception to propagate: _search_politicians_http's own `except Exception`
    # (which must stay broad so real transport errors stay best-effort) would
    # otherwise swallow a raise-on-call probe and this test would pass either way.
    from gui import review_api
    monkeypatch.setattr(politicians, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(politicians, "search_politicians_safe",
                        lambda q, limit=10: {"results": [], "error": None})
    calls = []

    def boom(q, limit=10):
        calls.append(q)
        raise AssertionError("HTTP search must not run when DATABASE_URL is set")

    monkeypatch.setattr("src.essentials_client.search_politicians", boom)
    review_api.search_politicians_safe("tiffany")
    assert calls == []


def test_review_api_fallback_swallows_http_errors(monkeypatch):
    from gui import review_api
    from src.essentials_client import EssentialsClientError
    monkeypatch.setattr(politicians, "_db_url", lambda: None)

    def boom(q, limit=10):
        raise EssentialsClientError("upstream down")

    monkeypatch.setattr("src.essentials_client.search_politicians", boom)
    out = review_api.search_politicians_safe("tiffany")
    assert out["results"] == []
    assert out["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
$VP -m pytest tests/test_gui_politicians.py -k review_api -q
```

Expected: FAIL — `review_api.search_politicians_safe` still calls the HTTP client
unconditionally, so the first test gets its own dict rather than the sentinel.

- [ ] **Step 3: Write the implementation**

Replace `gui/review_api.py` lines 174-195 (the whole `search_politicians_safe`
function) with:

```python
def search_politicians_safe(q: str, *, limit: int = 10) -> dict:
    """Best-effort politician search for the link picker.

    Prefers gui.politicians (direct DB), which is the only path that can show
    which races a person is actually a candidate in — the thing a curator needs,
    because publish derives a meeting's races solely from politician_id ->
    race_candidates. Falls back to the ev-accounts HTTP search when DATABASE_URL
    isn't set, so a GUI run without DB access degrades to name + office instead
    of returning nothing. Never raises.
    """
    from gui import politicians
    if politicians.db_configured():
        return politicians.search_politicians_safe(q, limit=limit)
    return _search_politicians_http(q, limit=limit)


def _search_politicians_http(q: str, *, limit: int = 10) -> dict:
    """The pre-direct-DB path: ev-accounts /candidates/search-by-name. Carries no
    candidacy data, so `candidacy_display` is '' and the renderer omits line 2."""
    from gui import politicians
    from src.essentials_client import EssentialsClientError, search_politicians
    try:
        raw = search_politicians(q, limit=limit)
    except EssentialsClientError as exc:
        return {"results": [], "error": str(exc)}
    except Exception as exc:  # transport/unexpected — stay best-effort
        return {"results": [], "error": f"search failed: {exc}"}
    results = []
    for r in raw:
        rec = {
            "politician_slug": r.get("politician_slug") or r.get("slug"),
            "politician_id": r.get("politician_id") or r.get("id"),
            "full_name": r.get("full_name") or "",
            "office_title": r.get("office_title") or "",
            "district_label": r.get("district_label") or "",
            "government_name": r.get("government_name") or "",
            "candidacies": [],
        }
        rec["display"] = politicians.politician_display(rec)
        rec["candidacy_display"] = ""
        # False, not True: without a DB we never looked, so we must not claim the
        # person has no candidacies.
        rec["candidacy_warn"] = False
        rec["duplicate_note"] = ""
        results.append(rec)
    return {"results": results, "error": None}
```

The fallback reads both `politician_id`/`id` and `politician_slug`/`slug` because
`_normalize_politician` renames them but a monkeypatched fake may not.

- [ ] **Step 4: Run tests to verify they pass**

```bash
$VP -m pytest tests/test_gui_politicians.py -q
```

Expected: `51 passed, 3 skipped` without `DATABASE_URL`, or `54 passed` with it

- [ ] **Step 5: Run the full suite to confirm no regression**

```bash
$VP -m pytest tests/ -q
```

Expected: `1769 passed, 3 skipped` (1718 baseline + 47 fake-cursor tests from Tasks 1-4 + 4 from this task; the 3 integration tests skip without DATABASE_URL)

- [ ] **Step 6: Commit**

```bash
git add gui/review_api.py tests/test_gui_politicians.py
git commit -m "feat(gui): route the link picker at the direct-DB search

Falls back to the ev-accounts HTTP search when DATABASE_URL is unset, so a GUI
run without DB access degrades to name + office rather than returning nothing.
The fallback sets candidacy_display='' so the renderer omits the line instead of
claiming a person has no candidacies when we simply couldn't look.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Render the two-line result

**Files:**
- Modify: `gui/static/workspace.js:152-159`
- Modify: `gui/static/style.css` (append after line 59)
- Test: `tests/test_gui_workspace.py`

- [ ] **Step 1: Write the failing test**

`tests/test_gui_workspace.py` already asserts the widget contract in
`test_workspace_js_wires_core_endpoints` (line 216), which reads the file from
disk rather than over HTTP and takes the `tmp_meetings_dir` fixture. Append a test
in exactly that style, immediately after it:

```python
def test_workspace_js_renders_server_composed_two_line_result(tmp_meetings_dir):
    from pathlib import Path
    js = Path("gui/static/workspace.js").read_text()
    # the server owns the label now — it's the only side that knows candidacies
    assert "r.display" in js
    assert "r.candidacy_display" in js
    assert "r.candidacy_warn" in js
    assert "r.duplicate_note" in js
    # the warning style must not hang off matching the label text
    assert '"no candidacies"' not in js
    # ...so the client must not re-join identity fields itself
    assert "r.office_title" not in js
    assert "r.government_name" not in js
```

- [ ] **Step 2: Run test to verify it fails**

```bash
$VP -m pytest tests/test_gui_workspace.py -k two_line -q
```

Expected: FAIL — `assert "r.display" in js`, since the JS still joins
`r.full_name, r.office_title, r.government_name`.

- [ ] **Step 3: Write the implementation**

In `gui/static/workspace.js`, replace lines 152-159 (the `results.innerHTML =
list.map(...)` block) with:

```javascript
      results.innerHTML = list.map((r) => {
        // The server composes both lines: it's the only side that knows the
        // candidacy data, and a wrong pick here silently detaches the meeting
        // from its race (publish derives races from politician_id alone).
        const cand = r.candidacy_display || "";
        const warn = !!r.candidacy_warn;
        let inner = '<span class="pr-name">' + esc(r.display || r.full_name) + "</span>";
        if (r.duplicate_note) {
          inner += '<span class="pr-warn pr-dupe">' + esc(r.duplicate_note) + "</span>";
        }
        if (cand) {
          inner += '<span class="pr-cand' + (warn ? " pr-warn" : "") + '">' + esc(cand) + "</span>";
        }
        return (
          '<form method="post" action="' + action + '">' +
          '<input type="hidden" name="politician_slug" value="' + esc(r.politician_slug) + '">' +
          '<input type="hidden" name="politician_id" value="' + esc(r.politician_id) + '">' +
          '<button type="submit" class="link-result">' + inner + "</button></form>"
        );
      }).join("");
```

`esc` already escapes `"` and `<` and is defined just above at line 151 — leave it
as is.

In `gui/static/style.css`, append after line 59 (the
`button.link-result:hover` rule):

```css
/* Two lines per result: identity, then which races the person is a candidate in.
   Warning colour and 0.78rem match the existing .thin ("short sample") caution
   chip rather than inventing a second one; #b32020 is reserved for errors. */
button.link-result { display: flex; flex-direction: column; gap: 0.1rem; }
.link-result .pr-name { display: block; }
.link-result .pr-cand { display: block; font-size: 0.78rem; color: #5c6b82; }
.link-result .pr-warn { color: #9a6a00; }
.link-result .pr-dupe { display: block; font-size: 0.78rem; font-weight: 600; }
```

`button.link-result` already sets `text-align: left; width: 100%`, so adding
`display: flex; flex-direction: column` stacks the lines without touching the rest.

- [ ] **Step 4: Run tests to verify they pass**

```bash
$VP -m pytest tests/test_gui_workspace.py -q
```

Expected: all pass, including the new test

- [ ] **Step 5: Commit**

```bash
git add gui/static/workspace.js gui/static/style.css tests/test_gui_workspace.py
git commit -m "feat(gui): render the picker result as identity + candidacy lines

The server composes both lines — it's the only side that knows the candidacy
data. 'no candidacies' and the duplicate marker get a warning colour, because
those are exactly the rows that silently detach a meeting from its race.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Verify in the running GUI and finish

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

```bash
$VP -m pytest tests/ -q
```

Expected: `1770 passed, 3 skipped` (1718 baseline + 51 from Tasks 1-5 + 1 from Task 6, 3 skipped without DATABASE_URL)

- [ ] **Step 2: Start the GUI and exercise the picker by hand**

The GUI needs `DATABASE_URL`; `.env.local` is auto-loaded by the hand-rolled
loader in `gui/asgi.py` (grepping for `load_dotenv` finds nothing — it is not
python-dotenv). This worktree has no `.env.local`, so symlink the main one:

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/.claude/worktrees/feat+gui-politician-picker-candidacy
ln -sf /Users/chrisandrews/Documents/GitHub/on-the-record/.env.local .env.local
$VP -m gui
```

Open any meeting's review panel and type into "Link politician…". Confirm:

| type | expect |
|---|---|
| `tiffany` | `Thomas P. Tiffany · U.S. Representative · Congressional District 7 · …` with `running: WI · Governor · Republican primary · 2026` |
| `thomas tiffany` | same single row (this returned nothing useful before) |
| `hong` | two `Francesca Hong` rows, both marked `⚠ 2 records for this name`; the candidate one shows the WI Governor primary, the other shows `no candidacies` in warning colour |
| `paxton` | Ken Paxton appears **once**, not twice |
| `barnes` | `Mandela Barnes` shows `running: WI · Governor · Democratic primary · 2026` instead of a bare name |

Then click the correct `Thomas P. Tiffany` row and confirm the card shows
`🔗 linked:` with his politician_id.

Note `.env.local` is gitignored, but confirm the symlink is not staged before
committing: `git status --short` should not list it.

- [ ] **Step 3: Confirm the link actually resolves the race**

Proves the whole point of the change end-to-end, read-only:

```bash
set -a && . /Users/chrisandrews/Documents/GitHub/on-the-record/.env.local && set +a
$VP - <<'PY'
import psycopg2, os
from src.publish import resolve_races_for_politicians
conn = psycopg2.connect(os.environ["DATABASE_URL"])
with conn.cursor() as cur:
    good = resolve_races_for_politicians(cur, ["a8f96324-50ac-4fa1-b57b-47a998306fe8"])
    bad  = resolve_races_for_politicians(cur, ["f1212497-7049-413a-ac19-81c5baba900d"])
print("Tiffany  ->", good)          # expect one race id (WI Governor R primary)
print("Hong wrong row ->", bad)     # expect [] — the case the picker now warns about
conn.close()
PY
```

Expected: Tiffany yields `['7823f3cc-fb86-426b-beab-92925cd6a34a']`; the
office-holding Hong row yields `[]`.

- [ ] **Step 4: Update the spec status**

In `docs/superpowers/specs/2026-08-03-gui-politician-picker-candidacy-design.md`,
change the header line `Status: design, awaiting review` to
`Status: implemented on branch worktree-feat+gui-politician-picker-candidacy`.

- [ ] **Step 5: Commit and finish**

```bash
git add docs/superpowers/specs/2026-08-03-gui-politician-picker-candidacy-design.md
git commit -m "docs: mark the politician picker spec implemented

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

Then use the `superpowers:finishing-a-development-branch` skill to decide on
merge vs. PR. Include the Step 2 hand-verification table and the Step 3 output in
the PR body — the SQL correctness claims are not covered by automated tests
(there is no test DB).

---

## Out of scope

Tracked separately, do not do these here:

1. Repairing the 16 quotes stranded on non-candidate person rows (11
   `readrank_selected`) and merging the duplicate person rows. Already running in
   its own session.
2. A guard against re-minting duplicate person rows in the hand-add tooling.
3. Nickname aliasing (`Tom` → `Thomas`) — explicitly declined.
4. The ev-accounts `search-by-name` endpoint, so the public site keeps the
   fan-out and the `Tom Tiffany` miss.
5. `resolve_races_for_politicians` ignoring `candidate_status`.
