# GovInfo CREC Fetch (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/govinfo.py` — turn a `(date, chamber)` into the Congressional Record's ordered speaker turns for that chamber's floor proceedings, fetched from the GovInfo API.

**Architecture:** Pure parsing with network behind an injected `fetch` (mirrors `src/podcast.py` / `src/brightspot.py`). A `CrecTurn` dataclass carries `{speaker_raw, text, granule_id, order}`. Public entry `fetch_congressional_record_turns` pages the granules list (filtered by chamber via `granuleClass`), fetches each granule's htm, and parses it into ordered turns. This is Phase 1 of the design in `docs/superpowers/specs/2026-07-18-congressional-record-speaker-oracle-design.md`; the normalizer, `crec_align.py`, and Stage-4 wiring are separate later plans.

**Tech Stack:** Python 3.14 (`.venv/bin/python`), pytest, BeautifulSoup4 (already a dep), `requests` (already a dep). No new dependencies.

**Real API facts (verified 2026-07-18 against `api.govinfo.gov`):**
- Package id for a day: `CREC-YYYY-MM-DD`.
- Granules list: `GET /packages/{packageId}/granules?offsetMark=*&pageSize=100&api_key=KEY` → `{count, granules:[{granuleId, granuleClass, title}], nextPage}`. Paginate by following `nextPage` (an absolute URL) until it is absent/null.
- `granuleClass` ∈ `HOUSE | SENATE | DIGEST | EXTENSIONS`.
- Granule text: `GET /packages/{packageId}/granules/{granuleId}/htm?api_key=KEY` → `<html><body><pre>…plain text…</pre></body></html>`.
- Auth: `api.data.gov` key; `DEMO_KEY` works but is aggressively rate-limited (hit the cap during design) — hence **all unit tests use committed fixtures and never touch the network**; the live CLI/smoke needs a real `GOVINFO_API_KEY`.

**Real htm format (confirmed):** inside `<pre>` — a header block (`[Congressional Record Volume …]`, a `[Senate]`/`[House of Representatives]` line, `[Page S6735]`, then `From the Congressional Record Online through the Government Publishing Office [www.gpo.gov]`), then a centered ALL-CAPS title, then body paragraphs. A paragraph indented 2 spaces that begins with a speaker designation (`Mr. Cotton.`, `The PRESIDING OFFICER (Mr. Cotton).`, `The SPEAKER pro tempore.`) starts a new speaker turn; wrapped continuation lines are flush-left; `____________________` and blank lines separate blocks.

---

## File Structure

- Create: `src/govinfo.py` — the whole Phase-1 module (dataclass, helpers, public entry, CLI).
- Create: `tests/test_govinfo.py` — all unit tests.
- Create: `tests/fixtures/govinfo/granules_page1.json` — first granules page (mixed classes + `nextPage`).
- Create: `tests/fixtures/govinfo/granules_page2.json` — second page (no `nextPage`, terminates pagination).
- Create: `tests/fixtures/govinfo/granule_presiding.htm` — a real captured granule (one procedural turn).
- Create: `tests/fixtures/govinfo/granule_debate.htm` — a multi-speaker granule (member turns + continuation reflow).

---

### Task 1: Module scaffold — `CrecTurn`, `_package_id`, key resolution

**Files:**
- Create: `src/govinfo.py`
- Test: `tests/test_govinfo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_govinfo.py
from __future__ import annotations

from src.govinfo import CrecTurn, _package_id, _resolve_api_key


def test_crec_turn_fields():
    t = CrecTurn(speaker_raw="Mr. Cotton", text="The majority leader is recognized.",
                 granule_id="CREC-2018-10-10-pt1-PgS6735-6", order=0)
    assert t.speaker_raw == "Mr. Cotton"
    assert t.text == "The majority leader is recognized."
    assert t.granule_id == "CREC-2018-10-10-pt1-PgS6735-6"
    assert t.order == 0


def test_package_id():
    assert _package_id("2018-10-10") == "CREC-2018-10-10"


def test_resolve_api_key_prefers_arg(monkeypatch):
    monkeypatch.setenv("GOVINFO_API_KEY", "envkey")
    assert _resolve_api_key("argkey") == "argkey"


def test_resolve_api_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GOVINFO_API_KEY", "envkey")
    assert _resolve_api_key(None) == "envkey"


def test_resolve_api_key_falls_back_to_demo(monkeypatch):
    monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
    assert _resolve_api_key(None) == "DEMO_KEY"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.govinfo'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/govinfo.py
"""GovInfo Congressional Record (CREC) fetch.

Turns a (date, chamber) into the official Congressional Record's ordered speaker
turns for that chamber's floor proceedings. This is a *speaker-identity* source,
not a transcript substitute: CREC is non-verbatim and has no timestamps, but it
records exactly who spoke, in what order.

Parsing is pure and network-free; the only network primitive is an injected
`fetch`. Mirrors src/podcast.py and src/brightspot.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

_API_ROOT = "https://api.govinfo.gov"
_CHAMBER_TO_CLASS = {"house": "HOUSE", "senate": "SENATE"}


@dataclass
class CrecTurn:
    speaker_raw: str      # "Mr. Cotton", "The PRESIDING OFFICER (Mr. Cotton)"
    text: str             # remarks attributed to that speaker
    granule_id: str       # source granule (provenance)
    order: int            # 0-based position across the day's matched granules


def _package_id(date: str) -> str:
    """'YYYY-MM-DD' -> 'CREC-YYYY-MM-DD'."""
    return f"CREC-{date}"


def _resolve_api_key(api_key: Optional[str]) -> str:
    """arg -> GOVINFO_API_KEY env -> 'DEMO_KEY'."""
    return api_key or os.environ.get("GOVINFO_API_KEY") or "DEMO_KEY"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/govinfo.py tests/test_govinfo.py
git commit -m "feat(govinfo): scaffold CrecTurn + package id + api key resolution"
```

---

### Task 2: Granules-list parsing + pagination — `parse_granule_list`, `_next_offset_mark`

**Files:**
- Modify: `src/govinfo.py`
- Create: `tests/fixtures/govinfo/granules_page1.json`
- Create: `tests/fixtures/govinfo/granules_page2.json`
- Test: `tests/test_govinfo.py`

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/govinfo/granules_page1.json`:

```json
{
  "count": 4,
  "granules": [
    {"granuleId": "CREC-2018-10-10-pt1-PgH1-1", "granuleClass": "HOUSE", "title": "MORNING-HOUR DEBATE"},
    {"granuleId": "CREC-2018-10-10-pt1-PgS6735-6", "granuleClass": "SENATE", "title": "RECOGNITION OF THE MAJORITY LEADER"},
    {"granuleId": "CREC-2018-10-10-pt1-PgD1124", "granuleClass": "DIGEST", "title": "Daily Digest"},
    {"granuleId": "CREC-2018-10-10-pt1-PgE1-1", "granuleClass": "EXTENSIONS", "title": "PERSONAL EXPLANATION"}
  ],
  "nextPage": "https://api.govinfo.gov/packages/CREC-2018-10-10/granules?offsetMark=PAGE2&pageSize=100"
}
```

`tests/fixtures/govinfo/granules_page2.json`:

```json
{
  "count": 1,
  "granules": [
    {"granuleId": "CREC-2018-10-10-pt1-PgH2-1", "granuleClass": "HOUSE", "title": "SECOND HOUSE ITEM"}
  ],
  "nextPage": null
}
```

- [ ] **Step 2: Write the failing test**

```python
# add to tests/test_govinfo.py
from pathlib import Path

from src.govinfo import parse_granule_list, _next_offset_mark

_FIX = Path(__file__).parent / "fixtures" / "govinfo"


def _read(name: str) -> str:
    return (_FIX / name).read_text(encoding="utf-8")


def test_parse_granule_list_filters_house():
    ids = parse_granule_list(_read("granules_page1.json"), "house")
    assert ids == ["CREC-2018-10-10-pt1-PgH1-1"]


def test_parse_granule_list_filters_senate():
    ids = parse_granule_list(_read("granules_page1.json"), "senate")
    assert ids == ["CREC-2018-10-10-pt1-PgS6735-6"]


def test_parse_granule_list_is_case_insensitive():
    assert parse_granule_list(_read("granules_page1.json"), "HOUSE") == \
        ["CREC-2018-10-10-pt1-PgH1-1"]


def test_parse_granule_list_excludes_digest_and_extensions():
    ids = parse_granule_list(_read("granules_page1.json"), "house") + \
        parse_granule_list(_read("granules_page1.json"), "senate")
    assert "CREC-2018-10-10-pt1-PgD1124" not in ids
    assert "CREC-2018-10-10-pt1-PgE1-1" not in ids


def test_next_offset_mark_returns_url_then_none():
    assert _next_offset_mark(_read("granules_page1.json")) == \
        "https://api.govinfo.gov/packages/CREC-2018-10-10/granules?offsetMark=PAGE2&pageSize=100"
    assert _next_offset_mark(_read("granules_page2.json")) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -k "granule_list or offset_mark" -v`
Expected: FAIL — `ImportError: cannot import name 'parse_granule_list'`.

- [ ] **Step 4: Write minimal implementation**

```python
# add to src/govinfo.py
import json


def parse_granule_list(json_text: str, chamber: str) -> list[str]:
    """Granule ids whose granuleClass matches the chamber, in document order.

    Reads `granuleClass`, falling back to `docClass` for older payloads.
    DIGEST / EXTENSIONS are excluded by only matching the chamber's class.
    """
    target = _CHAMBER_TO_CLASS[chamber.lower()]
    data = json.loads(json_text)
    ids: list[str] = []
    for g in data.get("granules", []):
        klass = g.get("granuleClass") or g.get("docClass")
        if klass == target and g.get("granuleId"):
            ids.append(g["granuleId"])
    return ids


def _next_offset_mark(json_text: str) -> Optional[str]:
    """Absolute URL of the next granules page, or None when exhausted."""
    return json.loads(json_text).get("nextPage") or None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -k "granule_list or offset_mark" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/govinfo.py tests/test_govinfo.py tests/fixtures/govinfo/granules_page1.json tests/fixtures/govinfo/granules_page2.json
git commit -m "feat(govinfo): parse granules list by chamber + pagination"
```

---

### Task 3: htm → text — `html_to_text`

**Files:**
- Modify: `src/govinfo.py`
- Create: `tests/fixtures/govinfo/granule_presiding.htm`
- Test: `tests/test_govinfo.py`

- [ ] **Step 1: Create the real captured fixture**

`tests/fixtures/govinfo/granule_presiding.htm` (verbatim from `api.govinfo.gov`, granule `CREC-2018-10-10-pt1-PgS6735-6`):

```html
<html>
<head>
<title>Congressional Record, Volume 164 Issue 168 (Wednesday, October 10, 2018)</title>
</head>
<body><pre>
[Congressional Record Volume 164, Number 168 (Wednesday, October 10, 2018)]
[Senate]
[Page S6735]
From the Congressional Record Online through the Government Publishing Office [<a href="https://www.gpo.gov">www.gpo.gov</a>]




                   RECOGNITION OF THE MAJORITY LEADER

  The PRESIDING OFFICER (Mr. Cotton). The majority leader is 
recognized.

                          ____________________


</pre></body>
</html>
```

- [ ] **Step 2: Write the failing test**

```python
# add to tests/test_govinfo.py
from src.govinfo import html_to_text


def test_html_to_text_extracts_pre_and_unescapes():
    text = html_to_text(_read("granule_presiding.htm"))
    assert "RECOGNITION OF THE MAJORITY LEADER" in text
    assert "The PRESIDING OFFICER (Mr. Cotton)." in text
    # the <a> tag around the gpo.gov link is stripped, its text kept
    assert "<a" not in text
    assert "www.gpo.gov" in text
    # header bracket lines survive as text (stripped later by the turn parser)
    assert "[Senate]" in text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -k html_to_text -v`
Expected: FAIL — `ImportError: cannot import name 'html_to_text'`.

- [ ] **Step 4: Write minimal implementation**

```python
# add to src/govinfo.py
def html_to_text(htm: str) -> str:
    """Readable text from a CREC granule htm.

    The content is a single <pre> block; BeautifulSoup keeps its text (dropping
    the inline <a> tag) and unescapes entities. Falls back to whole-document text
    if no <pre> is present.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(htm, "html.parser")
    pre = soup.find("pre")
    return (pre.get_text() if pre else soup.get_text())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -k html_to_text -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/govinfo.py tests/test_govinfo.py tests/fixtures/govinfo/granule_presiding.htm
git commit -m "feat(govinfo): html_to_text pulls readable text from granule <pre>"
```

---

### Task 4: Turn parsing — `parse_granule_turns`

**Files:**
- Modify: `src/govinfo.py`
- Create: `tests/fixtures/govinfo/granule_debate.htm`
- Test: `tests/test_govinfo.py`

**Note on heuristics:** CREC turn detection is heuristic. The tests below pin the expected behavior against both a real single-turn granule and a representative multi-speaker granule that mirrors the exact `<pre>` format (2-space paragraph indent, flush-left wrapped continuations, `____` separators). If, when run against additional real granules later, turns mis-split, iterate `_DESIGNATION_RE` — that iteration is expected, not a failure.

- [ ] **Step 1: Create the multi-speaker fixture**

`tests/fixtures/govinfo/granule_debate.htm` (mirrors real CREC layout; two member turns with a wrapped continuation line and a `____` separator):

```html
<html>
<head><title>Congressional Record</title></head>
<body><pre>
[Congressional Record Volume 164, Number 168 (Wednesday, October 10, 2018)]
[House of Representatives]
[Pages H8100-H8101]
From the Congressional Record Online through the Government Publishing Office [<a href="https://www.gpo.gov">www.gpo.gov</a>]


                          A BILL TO DO THINGS

  Mr. SMITH of Michigan. Madam Speaker, I rise today in strong support 
of this measure, which will help my constituents.
  Mr. JONES. Madam Speaker, I thank the gentleman for yielding and I 
yield myself such time as I may consume.

                          ____________________


</pre></body>
</html>
```

- [ ] **Step 2: Write the failing test**

```python
# add to tests/test_govinfo.py
from src.govinfo import parse_granule_turns


def test_parse_granule_turns_single_procedural():
    text = html_to_text(_read("granule_presiding.htm"))
    turns = parse_granule_turns(text, "CREC-2018-10-10-pt1-PgS6735-6", start_order=0)
    assert len(turns) == 1
    assert turns[0].speaker_raw == "The PRESIDING OFFICER (Mr. Cotton)"
    assert turns[0].text == "The majority leader is recognized."
    assert turns[0].granule_id == "CREC-2018-10-10-pt1-PgS6735-6"
    assert turns[0].order == 0


def test_parse_granule_turns_multi_speaker_reflows_continuations():
    text = html_to_text(_read("granule_debate.htm"))
    turns = parse_granule_turns(text, "CREC-2018-10-10-pt1-PgH1-1", start_order=5)
    assert [t.speaker_raw for t in turns] == ["Mr. SMITH of Michigan", "Mr. JONES"]
    # wrapped continuation line is joined into one space-separated string
    assert turns[0].text == (
        "Madam Speaker, I rise today in strong support of this measure, "
        "which will help my constituents."
    )
    assert turns[1].text.startswith("Madam Speaker, I thank the gentleman")
    # start_order offsets the sequence
    assert [t.order for t in turns] == [5, 6]


def test_parse_granule_turns_empty_when_no_designations():
    turns = parse_granule_turns("just some floor boilerplate\nwith no speakers",
                                "CREC-x", start_order=0)
    assert turns == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -k granule_turns -v`
Expected: FAIL — `ImportError: cannot import name 'parse_granule_turns'`.

- [ ] **Step 4: Write minimal implementation**

```python
# add to src/govinfo.py
import re

# A paragraph-initial speaker designation, up to the period that ends it.
# Handles: "Mr./Mrs./Ms./Miss <Name>[ of <State>]" and "The <ROLE>[ (Mr. <Name>)]".
_DESIGNATION_RE = re.compile(
    r"^(?P<desig>"
    r"(?:Mr|Mrs|Ms|Miss)\.?\s+[A-Z][A-Za-z.'\-]+(?:\s+of\s+[A-Z][A-Za-z ]+?)?"
    r"|The\s+[A-Z][A-Za-z]+(?:\s+[A-Za-z]+)*?(?:\s+\(Mr\.\s+[A-Z][A-Za-z]+\)|\s+pro\s+tempore)?"
    r")\.\s+(?P<rest>.+)$",
    re.S,
)

# Lines that are pure page/header furniture, dropped before paragraph assembly.
_FURNITURE_RE = re.compile(
    r"^\s*(\[.*\]|From the Congressional Record Online.*|_{5,})\s*$"
)


def _paragraphs(text: str) -> list[str]:
    """Reflow CREC body text into paragraphs.

    A new paragraph starts on a line indented >=2 spaces; flush-left lines are
    wrapped continuations and join onto the current paragraph with a single
    space. Furniture lines and blank lines flush the current paragraph.
    """
    paras: list[str] = []
    cur: list[str] = []

    def flush():
        if cur:
            paras.append(" ".join(w.strip() for w in cur).strip())
            cur.clear()

    for line in text.splitlines():
        if not line.strip() or _FURNITURE_RE.match(line):
            flush()
            continue
        if re.match(r"^ {2,}\S", line):   # indented -> new paragraph
            flush()
            cur.append(line)
        else:                              # flush-left -> continuation
            cur.append(line)
    flush()
    return paras


def parse_granule_turns(text: str, granule_id: str, start_order: int) -> list[CrecTurn]:
    """Ordered speaker turns in one granule's text.

    Paragraphs that begin with a speaker designation start a new turn; the
    centered title and any non-designation paragraphs are skipped. `start_order`
    offsets `order` so ids stay unique across a day's granules.
    """
    turns: list[CrecTurn] = []
    order = start_order
    for para in _paragraphs(text):
        m = _DESIGNATION_RE.match(para)
        if not m:
            continue
        turns.append(CrecTurn(
            speaker_raw=m.group("desig").strip(),
            text=m.group("rest").strip(),
            granule_id=granule_id,
            order=order,
        ))
        order += 1
    return turns
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -k granule_turns -v`
Expected: PASS. If a turn mis-splits, adjust `_DESIGNATION_RE` / `_paragraphs` and re-run — heuristic iteration is expected here.

- [ ] **Step 6: Commit**

```bash
git add src/govinfo.py tests/test_govinfo.py tests/fixtures/govinfo/granule_debate.htm
git commit -m "feat(govinfo): parse granule text into ordered speaker turns"
```

---

### Task 5: Orchestration — `fetch_congressional_record_turns` + URL builders

**Files:**
- Modify: `src/govinfo.py`
- Test: `tests/test_govinfo.py`

- [ ] **Step 1: Write the failing test** (uses a fake `fetch` mapping URLs → fixture strings; never hits the network)

```python
# add to tests/test_govinfo.py
import pytest

from src.govinfo import (
    fetch_congressional_record_turns,
    _granules_url,
    _granule_text_url,
)


def test_granules_url_shape():
    url = _granules_url("CREC-2018-10-10", "*", "KEY")
    assert url == (
        "https://api.govinfo.gov/packages/CREC-2018-10-10/granules"
        "?offsetMark=*&pageSize=100&api_key=KEY"
    )


def test_granule_text_url_shape():
    url = _granule_text_url("CREC-2018-10-10", "CREC-2018-10-10-pt1-PgH1-1", "KEY")
    assert url == (
        "https://api.govinfo.gov/packages/CREC-2018-10-10/granules/"
        "CREC-2018-10-10-pt1-PgH1-1/htm?api_key=KEY"
    )


def _fake_fetch_factory():
    """URL -> fixture text, following the real two-page + htm topology."""
    page1 = _read("granules_page1.json")
    page2 = _read("granules_page2.json")
    debate = _read("granule_debate.htm")

    def fetch(url: str) -> str:
        if "/granules/" in url and url.endswith("/htm?api_key=KEY"):
            return debate  # every granule returns the multi-speaker fixture
        if "offsetMark=PAGE2" in url:
            return page2
        if "/granules?" in url:
            return page1
        raise AssertionError(f"unexpected url {url}")
    return fetch


def test_fetch_house_turns_paginates_and_orders():
    turns = fetch_congressional_record_turns(
        "2018-10-10", "house", fetch=_fake_fetch_factory(), api_key="KEY"
    )
    # page1 has one HOUSE granule, page2 has one -> both fetched (pagination works)
    assert turns is not None
    assert [t.granule_id for t in turns] == [
        "CREC-2018-10-10-pt1-PgH1-1", "CREC-2018-10-10-pt1-PgH2-1",
    ]
    # each granule fixture yields 2 turns; order is continuous across granules
    assert [t.order for t in turns] == [0, 1, 2, 3]
    assert turns[0].speaker_raw == "Mr. SMITH of Michigan"


def test_fetch_excludes_other_chamber():
    turns = fetch_congressional_record_turns(
        "2018-10-10", "senate", fetch=_fake_fetch_factory(), api_key="KEY"
    )
    # the SENATE granule also returns the debate fixture (2 turns), House excluded
    assert turns is not None
    assert all(t.granule_id == "CREC-2018-10-10-pt1-PgS6735-6" for t in turns)


def test_fetch_returns_none_on_missing_package():
    def fetch(url: str) -> str:
        raise RuntimeError("404")
    assert fetch_congressional_record_turns(
        "1900-01-01", "house", fetch=fetch, api_key="KEY") is None


def test_fetch_returns_none_when_all_granule_texts_fail():
    page1 = _read("granules_page1.json")
    page2 = _read("granules_page2.json")

    def fetch(url: str) -> str:
        if "/granules/" in url and url.endswith("/htm?api_key=KEY"):
            raise RuntimeError("granule text 500")
        if "offsetMark=PAGE2" in url:
            return page2
        return page1
    assert fetch_congressional_record_turns(
        "2018-10-10", "house", fetch=fetch, api_key="KEY") is None


def test_fetch_max_granules_truncates(capsys):
    turns = fetch_congressional_record_turns(
        "2018-10-10", "house", fetch=_fake_fetch_factory(), api_key="KEY",
        max_granules=1,
    )
    # only the first HOUSE granule is fetched
    assert turns is not None
    assert {t.granule_id for t in turns} == {"CREC-2018-10-10-pt1-PgH1-1"}
    # truncation is logged, never silent
    assert "truncat" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -k "url_shape or fetch_" -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_congressional_record_turns'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/govinfo.py
def _granules_url(package_id: str, offset_mark: str, api_key: str, page_size: int = 100) -> str:
    return (f"{_API_ROOT}/packages/{package_id}/granules"
            f"?offsetMark={offset_mark}&pageSize={page_size}&api_key={api_key}")


def _granule_text_url(package_id: str, granule_id: str, api_key: str) -> str:
    return f"{_API_ROOT}/packages/{package_id}/granules/{granule_id}/htm?api_key={api_key}"


def _default_fetch(url: str) -> str:
    import requests
    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def _list_matching_granule_ids(package_id, chamber, api_key, fetch) -> Optional[list[str]]:
    """All chamber granule ids across every page, or None if the first page fails
    (a recess day / missing package is normal, not an error)."""
    url = _granules_url(package_id, "*", api_key)
    ids: list[str] = []
    first = True
    while url:
        try:
            page = fetch(url)
        except Exception:
            if first:
                return None      # no Record for this day
            break                # partial pagination: stop, keep what we have
        first = False
        ids.extend(parse_granule_list(page, chamber))
        url = _next_offset_mark(page)
    return ids


def fetch_congressional_record_turns(
    date: str,
    chamber: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
    api_key: Optional[str] = None,
    max_granules: Optional[int] = None,
) -> Optional[list[CrecTurn]]:
    """Ordered speaker turns of the Congressional Record for a chamber on a day.

    Returns None when there is no Record (recess), no matching granules, or every
    granule text fetch fails. Never returns a silently-partial Record as if
    complete. `max_granules` caps requests (rate-limit / demo guard) and logs
    when it truncates.
    """
    key = _resolve_api_key(api_key)
    package_id = _package_id(date)

    ids = _list_matching_granule_ids(package_id, chamber, key, fetch)
    if not ids:
        return None

    if max_granules is not None and len(ids) > max_granules:
        print(f"  govinfo: truncating {len(ids)} granules to first {max_granules} "
              f"(max_granules guard).")
        ids = ids[:max_granules]

    turns: list[CrecTurn] = []
    any_ok = False
    for gid in ids:
        try:
            htm = fetch(_granule_text_url(package_id, gid, key))
        except Exception:
            print(f"  govinfo: skipping granule {gid} (text fetch failed).")
            continue
        any_ok = True
        turns.extend(parse_granule_turns(html_to_text(htm), gid, start_order=len(turns)))

    if not any_ok:
        return None
    return turns
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/govinfo.py tests/test_govinfo.py
git commit -m "feat(govinfo): fetch_congressional_record_turns orchestration"
```

---

### Task 6: CLI demo — `python -m src.govinfo`

**Files:**
- Modify: `src/govinfo.py`
- Test: `tests/test_govinfo.py`

- [ ] **Step 1: Write the failing test** (tests the pure formatter, not argparse/network)

```python
# add to tests/test_govinfo.py
from src.govinfo import format_turns_text


def test_format_turns_text():
    turns = [
        CrecTurn("Mr. SMITH of Michigan", "I rise in support.", "g1", 0),
        CrecTurn("Mr. JONES", "I yield myself time.", "g1", 1),
    ]
    out = format_turns_text(turns)
    assert out == (
        "Mr. SMITH of Michigan: I rise in support.\n\n"
        "Mr. JONES: I yield myself time."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -k format_turns_text -v`
Expected: FAIL — `ImportError: cannot import name 'format_turns_text'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/govinfo.py
def format_turns_text(turns: list[CrecTurn]) -> str:
    """Render turns as 'Speaker: text' blocks, blank-line separated."""
    return "\n\n".join(f"{t.speaker_raw}: {t.text}" for t in turns)


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch the Congressional Record's speaker turns for a "
                    "chamber on a date (needs GOVINFO_API_KEY for real use).")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("chamber", choices=["house", "senate"])
    parser.add_argument("--max-granules", type=int, default=None)
    parser.add_argument("--out", default=None,
                        help="write transcript text to this path instead of stdout")
    args = parser.parse_args(argv)

    turns = fetch_congressional_record_turns(
        args.date, args.chamber, max_granules=args.max_granules)
    if turns is None:
        print("No Congressional Record found for that date/chamber "
              "(recess day, not yet published, or rate-limited).")
        return 1

    text = format_turns_text(turns)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {len(turns)} turns to {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_govinfo.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Live smoke check (manual, needs a real key; not a committed test)**

Run (only if a real key is available — DEMO_KEY will likely rate-limit):
```bash
GOVINFO_API_KEY=your_real_key .venv/bin/python -m src.govinfo 2018-10-10 senate --max-granules 3
```
Expected: prints `Speaker: text` blocks (e.g. a `The PRESIDING OFFICER (Mr. Cotton): …` turn). Confirms the parser matches live data. If turns mis-split, capture the offending granule htm into `tests/fixtures/govinfo/`, add a test, and iterate the parser.

- [ ] **Step 6: Commit**

```bash
git add src/govinfo.py tests/test_govinfo.py
git commit -m "feat(govinfo): CLI demo + turn text formatter"
```

---

## Self-Review

**Spec coverage (Phase 1 scope only):**
- CREC fetch entry point, pure/injected fetch, chamber filter, pagination, htm→turns, error handling (None on missing/all-fail), `max_granules` with logged truncation, CLI demo, fixture-based offline tests — all covered by Tasks 1–6.
- Deliberately out of Phase 1 (own later plans): normalizer + congressional roster, `crec_align.py`, Stage-4 wiring, Senate media spike. Recorded in the spec's phasing section.

**Placeholder scan:** No TBD/TODO; every code and test step is complete. The "heuristic iteration expected" notes in Tasks 4/6 are explicit guidance, not deferred work.

**Type consistency:** `CrecTurn(speaker_raw, text, granule_id, order)` used identically across Tasks 1, 4, 5, 6. `parse_granule_turns(text, granule_id, start_order)`, `parse_granule_list(json_text, chamber)`, `fetch_congressional_record_turns(date, chamber, *, fetch, api_key, max_granules)` signatures are consistent between definition and call sites in tests.
