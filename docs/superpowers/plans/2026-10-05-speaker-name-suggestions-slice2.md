# Speaker Name Suggestions — Slice 2 (lookup + page verification) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each speaker candidate from slice 1, find the right spelling (and link) from the roster, the politicians table, past meetings, or a verified web page, and write `name_suggestions.json` — plus measure spelling accuracy on gold witnesses.

**Architecture:** `src/name_lookup.py` holds the lookup pieces: page verification, the Claude Code researcher (subprocess, injectable runner), a JSON cache, and roster / politician / local_people matchers over an injectable `NameDB`. `src/name_suggest.py` orchestrates one meeting and writes the output file; `run_local.py --suggest-names` exposes it. `scripts/eval_name_lookup.py` measures spelling on ~50 gold witnesses (web step only).

**Tech Stack:** Python 3 (repo `.venv`), `requests`, `beautifulsoup4` (both already in requirements), `psycopg2` (read-only), `subprocess` calling the `claude` CLI. pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md` (sections "Lookup", "Privacy rules", "Error handling", "Slice 2 decisions").

## Global Constraints

- When this plan's code and the spec differ, the spec governs (Chris, 2026-10-03).
- Lookup order, first hit wins: roster → politicians (titled names, by state) → local_people → Claude web researcher → page verification.
- A titled name (X6) is never searched on the web.
- Researcher command: `claude -p <prompt> --output-format json --json-schema <schema> --allowedTools WebSearch,WebFetch --max-turns 8 --no-session-persistence --strict-mcp-config --model sonnet`; 120-second timeout.
- Privacy: the prompt contains only the spoken name, the title, the stated affiliation and the meeting place (city/state) — never transcript text. The cache and output store only spelling, affiliation and one source URL.
- Never research a speaker with no affiliation and only a partial (single-token) name.
- A web result is `verified` only if our own fetch of the URL contains the exact name (case-insensitive, whitespace/punctuation-normalized). Unverified results never show the URL.
- CLI missing / not logged in / usage limit → stop web lookups for the run and report one clear warning; never stop the run.
- Nothing in this slice changes review, publish, or the processing pipeline.
- Python: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python` (the venv lives only in the main checkout). Tests never call the real `claude` CLI, the network or the DB.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `src/name_evidence.py` (modify) | Two parked slice-1 fixes |
| `src/name_lookup.py` (create) | `Lookup` type, page verification, researcher + cache, roster/politician/local_people matchers, `NameDB` (+ Postgres implementation) |
| `src/name_suggest.py` (create) | `read_captions_text`, `suggest_for_candidate`, `suggest_names`, `write_suggestions` |
| `run_local.py` (modify) | `--suggest-names MEETING_ID` |
| `scripts/eval_name_suggestions.py` (modify) | use `read_captions_text` from `src/name_suggest.py` |
| `src/name_suggestion_eval.py` (modify) | `score_lookup_rows` |
| `scripts/eval_name_lookup.py` (create) | 50-witness spelling eval |
| `tests/test_name_lookup.py`, `tests/test_name_suggest.py`, `tests/test_name_lookup_eval.py` (create); `tests/test_name_evidence.py` (modify) | tests |

---

### Task 1: Parked slice-1 fixes (greedy trim, title-word surnames)

**Files:**
- Modify: `src/name_evidence.py` (`split_name_title` ~121-160; greedy trim in `find_self_intros` ~248-252)
- Test: `tests/test_name_evidence.py` (append)

**Interfaces:**
- Consumes: existing `split_name_title`, `find_self_intros`, `build_turns`, `_UP`, `_TITLE_WORDS`, `_QUAL_WORDS`.
- Produces: same signatures; new private helper `_trim_greedy(raw: str, after: str) -> str`; constants `_CLAUSE_STARTS`, `_NAME_SUFFIXES`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_name_evidence.py
_TAIL = " and I want to thank the committee for the time today."


def _intro(text):
    return [e.name for e in find_self_intros(build_turns([seg(0, "W", text + _TAIL)]))]


def test_trim_does_not_fire_before_a_new_clause():
    assert _intro("My name is Ann Lee Smith I live on Main Street") == ["Ann Lee Smith"]
    assert _intro("My name is Ann Lee Smith I'm a teacher here") == ["Ann Lee Smith"]


def test_trim_does_not_fire_before_a_name_suffix():
    assert _intro("My name is Ann Lee Smith Jr and I live here") == ["Ann Lee Smith"]
    assert _intro("My name is Art Reyes Lopez III from Gary") == ["Art Reyes Lopez"]


def test_trim_still_fires_on_an_unbounded_capital_run():
    assert _intro("My name is Chris Swanson American Federation of Teachers") == ["Chris Swanson"]


def test_title_word_surname_is_kept():
    ev = find_self_intros(build_turns([seg(0, "W", "Hello. I'm Jim Justice, from Beckley." + _TAIL)]))
    assert [(e.name, e.title) for e in ev] == [("Jim Justice", None)]
    assert _intro("Hi, my name is Mary Pastor, from Fishers.") == ["Mary Pastor"]


def test_only_titles_is_still_rejected():
    from src.name_evidence import split_name_title
    assert split_name_title("Senator") == (None, None)
    assert split_name_title("State Senator") == (None, None)
    assert split_name_title("Pastor Smith") == ("Smith", "Pastor")
    assert split_name_title("State Representative Francesca Hong") == ("Francesca Hong", "Representative")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/chrisandrews/Documents/GitHub/on-the-record-names2 && /Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_evidence.py -q`
Expected: FAIL on the new trim / title-word tests (e.g. `['Ann Lee'] != ['Ann Lee Smith']`, `[] != [('Jim Justice', None)]`).

- [ ] **Step 3: Implement**

In `src/name_evidence.py`, near the other constants, add:

```python
# A capitalized word after a 3-token capture that starts a new clause or is a
# name suffix does not make the name boundary unknown.
_CLAUSE_STARTS = {"i", "i'm", "i'll", "i've", "i'd"}
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _trim_greedy(raw: str, after: str) -> str:
    """A 3-token capture followed by another capitalized word has an unknown
    boundary ("Chris Swanson American Federation"): keep the first two tokens.
    Not when that word starts a new clause ("I", "I'm") or is a suffix (Jr, III)."""
    toks = raw.split()
    if len(toks) != 3:
        return raw
    m = re.match(rf"\s+({_UP}[^\s,.;:!?]*)", after)
    if not m:
        return raw
    nxt = m.group(1).lower().rstrip(".").replace("’", "'")
    if nxt in _CLAUSE_STARTS or nxt in _NAME_SUFFIXES:
        return raw
    return " ".join(toks[:2])
```

In `find_self_intros`, replace the existing greedy-trim `if` block (the comment "Greedy capture of 3 capitalized words…" and the two lines under it) with:

```python
                if rx is not _R_IM:
                    raw = _trim_greedy(raw, window[m.end("name"):])
```

In `split_name_title`, replace the block that starts at `title = None` and ends with `toks = toks[last_title + 1:]` with:

```python
    title = None
    title_idx = [i for i, t in enumerate(toks) if t.lower().rstrip(".") in _TITLE_WORDS]
    if title_idx:
        name_like = [t for i, t in enumerate(toks)
                     if i not in title_idx and t.lower().rstrip(".") not in _QUAL_WORDS]
        if not name_like:
            return None, None  # only titles / qualifiers ("Senator", "State Senator")
        last = title_idx[-1]
        if last < len(toks) - 1:  # a name token follows the title: drop through it
            title = toks[last]
            toks = toks[last + 1:]
        # else the title word is the surname ("Jim Justice", "Mary Pastor"): keep it
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_evidence.py tests/test_name_candidates.py tests/test_name_suggestions_real.py -q`
Expected: all pass (no existing test changes).

- [ ] **Step 5: Commit**

```bash
git add src/name_evidence.py tests/test_name_evidence.py
git commit -m "fix(names): greedy trim respects clauses/suffixes; keep title-word surnames

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `Lookup` type and page verification

**Files:**
- Create: `src/name_lookup.py`
- Test: `tests/test_name_lookup.py`

**Interfaces:**
- Consumes: `BROWSER_USER_AGENT` from `src.download`.
- Produces:
  - `Lookup(name: str, source: str, verified: bool, politician_id: Optional[str] = None, local_slug: Optional[str] = None, url: Optional[str] = None, affiliation: Optional[str] = None, reason: Optional[str] = None)` with `to_dict() -> dict`. `source` ∈ `"roster" | "politician" | "local_people" | "web" | "transcript"`.
  - `norm_name(s: str) -> str`, `page_text(html: str) -> str`, `name_on_page(name: str, text: str) -> bool`
  - `default_fetch(url: str) -> str`
  - `verify_on_page(name: str, url: str, fetch: Callable[[str], str] = default_fetch) -> tuple[bool, Optional[str]]` → `(True, None)` or `(False, reason)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_name_lookup.py
from __future__ import annotations

from src.name_lookup import Lookup, name_on_page, norm_name, page_text, verify_on_page

HTML = """<html><head><title>Staff</title><script>var x = "Aaron Spiegel";</script></head>
<body><h1>Our Team</h1><p>Rabbi <b>Aaron&nbsp;Spiegel</b>, Executive Director</p></body></html>"""


def test_page_text_drops_scripts_and_joins_inline_tags():
    t = page_text(HTML)
    assert "var x" not in t and "Aaron Spiegel" in t.replace("\xa0", " ")


def test_name_on_page_is_case_space_and_punctuation_insensitive():
    assert name_on_page("aaron spiegel", page_text(HTML))
    assert name_on_page("Aaron  Spiegel", "Rabbi AARON SPIEGEL, director")
    assert not name_on_page("Aaron Spiegelman", "Rabbi Aaron Spiegel")
    assert not name_on_page("Ann Lee", "Joann Leeds")


def test_norm_name():
    assert norm_name("  O'Brien-Smith, Jr. ") == "o'brien smith jr"


def test_verify_on_page_ok_and_reasons():
    assert verify_on_page("Aaron Spiegel", "https://x.org/team", fetch=lambda u: HTML) == (True, None)
    ok, why = verify_on_page("Rachel Sample", "https://x.org/team", fetch=lambda u: HTML)
    assert not ok and why == "name not on page"

    def boom(u):
        raise RuntimeError("403")

    ok, why = verify_on_page("Aaron Spiegel", "https://x.org/team", fetch=boom)
    assert not ok and why.startswith("fetch failed")
    assert verify_on_page("Aaron Spiegel", "notaurl", fetch=lambda u: HTML) == (False, "bad url")


def test_lookup_to_dict_hides_url_when_unverified():
    d = Lookup(name="Ann Lee", source="web", verified=False, url="https://x", reason="name not on page").to_dict()
    assert d["url"] is None and d["reason"] == "name not on page"
    d2 = Lookup(name="Ann Lee", source="web", verified=True, url="https://x").to_dict()
    assert d2["url"] == "https://x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/chrisandrews/Documents/GitHub/on-the-record-names2 && /Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_lookup.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.name_lookup'`

- [ ] **Step 3: Implement**

```python
# src/name_lookup.py
"""Spelling lookup for speaker name suggestions (slice 2).

Spec: docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md
Order: roster -> politicians (titled, by state) -> local_people -> Claude web
researcher -> our own page verification. Network, DB and the claude CLI are
injected so tests never touch them.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

MAX_PAGE_BYTES = 2_000_000


@dataclass
class Lookup:
    name: str
    source: str                       # roster | politician | local_people | web | transcript
    verified: bool
    politician_id: Optional[str] = None
    local_slug: Optional[str] = None
    url: Optional[str] = None
    affiliation: Optional[str] = None
    reason: Optional[str] = None      # why not verified / not searched

    def to_dict(self) -> dict:
        d = asdict(self)
        if not self.verified:
            d["url"] = None  # never show an unverified source
        return d


def norm_name(s: str) -> str:
    s = s.replace("’", "'").replace("\xa0", " ").lower()
    s = re.sub(r"[^\w' ]+", " ", s)
    return " ".join(s.split())


def page_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup.get_text(" ")


def name_on_page(name: str, text: str) -> bool:
    n = norm_name(name)
    return bool(n) and f" {n} " in f" {norm_name(text)} "


def default_fetch(url: str) -> str:
    import requests

    from .download import BROWSER_USER_AGENT

    resp = requests.get(url, timeout=(10, 20), headers={"User-Agent": BROWSER_USER_AGENT}, stream=True)
    resp.raise_for_status()
    body = resp.raw.read(MAX_PAGE_BYTES, decode_content=True)
    return body.decode(resp.encoding or "utf-8", errors="replace")


def verify_on_page(name: str, url: str, fetch: Callable[[str], str] = default_fetch) -> tuple[bool, Optional[str]]:
    if urlparse(url or "").scheme not in ("http", "https"):
        return False, "bad url"
    try:
        html = fetch(url)
    except Exception as exc:  # noqa: BLE001 — any fetch failure just means "not verified"
        return False, f"fetch failed: {exc}"[:200]
    return (True, None) if name_on_page(name, page_text(html)) else (False, "name not on page")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_lookup.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/name_lookup.py tests/test_name_lookup.py
git commit -m "feat(names): Lookup type and page verification

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Claude Code researcher and cache

**Files:**
- Modify: `src/name_lookup.py` (append)
- Test: `tests/test_name_lookup.py` (append)

**Interfaces:**
- Consumes: `norm_name` (Task 2); `src.atomic_io.atomic_write_json(path, data)`.
- Produces:
  - `class ResearcherUnavailable(RuntimeError)`
  - `RESEARCH_SCHEMA: dict`, `RESEARCH_MODEL = "sonnet"`, `RESEARCH_TIMEOUT_S = 120`
  - `build_prompt(name: str, title: Optional[str], affiliation: Optional[str], place: Optional[str]) -> str`
  - `research_command(prompt: str, model: str = RESEARCH_MODEL) -> list[str]`
  - `research(name, title, affiliation, place, *, runner: Callable[[list[str], int], tuple[int, str, str]] = run_cli) -> Optional[dict]` → `{"name", "affiliation", "url"}` or None; raises `ResearcherUnavailable`.
  - `run_cli(cmd: list[str], timeout: int) -> tuple[int, str, str]`
  - `should_research(titled: bool, partial: bool, affiliation: Optional[str]) -> bool`
  - `class ResearchCache(path: Path)` with `key(name, affiliation, place) -> str`, `get(key) -> Optional[dict]` (a stored miss is `{"found": False}`), `put(key, value: Optional[dict]) -> None`, `save() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_name_lookup.py
import json

import pytest

from src.name_lookup import (
    RESEARCH_SCHEMA, ResearchCache, ResearcherUnavailable, build_prompt, research,
    research_command, should_research,
)


def runner_returning(payload, rc=0):
    calls = []

    def run(cmd, timeout):
        calls.append((cmd, timeout))
        return rc, json.dumps(payload), ""

    run.calls = calls
    return run


def test_prompt_contains_only_name_title_affiliation_place():
    p = build_prompt("Aaron Spiegel", "Rabbi", "Indy Multi-Faith", "Indianapolis, IN")
    assert "Aaron Spiegel" in p and "Rabbi" in p and "Indy Multi-Faith" in p and "Indianapolis, IN" in p
    assert "found=false" in p.lower() or "found false" in p.lower()


def test_command_flags():
    cmd = research_command("PROMPT")
    assert cmd[:3] == ["claude", "-p", "PROMPT"]
    for flag in ("--output-format", "--json-schema", "--allowedTools", "--max-turns",
                 "--no-session-persistence", "--strict-mcp-config", "--model"):
        assert flag in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == RESEARCH_SCHEMA


def test_research_returns_structured_output():
    run = runner_returning({"type": "result", "is_error": False, "structured_output":
                            {"found": True, "name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance",
                             "url": "https://example.org/team"}})
    out = research("Aaron Spiegel", "Rabbi", "Indy multi-faith", "Indianapolis, IN", runner=run)
    assert out == {"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance", "url": "https://example.org/team"}
    assert run.calls[0][1] == 120


def test_research_not_found_and_bad_output_return_none():
    assert research("A B", None, "X", None, runner=runner_returning(
        {"is_error": False, "structured_output": {"found": False}})) is None
    assert research("A B", None, "X", None, runner=lambda c, t: (0, "not json", "")) is None
    assert research("A B", None, "X", None, runner=runner_returning(
        {"is_error": False, "structured_output": {"found": True, "name": "", "url": "x"}})) is None


def test_research_auth_and_limit_errors_raise_unavailable():
    for msg in ("Failed to authenticate. API Error: 401 OAuth access token has expired.",
                "Claude usage limit reached. Your limit will reset at 5pm."):
        with pytest.raises(ResearcherUnavailable):
            research("A B", None, "X", None, runner=runner_returning({"is_error": True, "result": msg}, rc=1))


def test_research_missing_cli_raises_unavailable_and_timeout_returns_none():
    def missing(cmd, timeout):
        raise FileNotFoundError("claude")

    with pytest.raises(ResearcherUnavailable):
        research("A B", None, "X", None, runner=missing)

    import subprocess

    def slow(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    assert research("A B", None, "X", None, runner=slow) is None


def test_should_research():
    assert should_research(titled=False, partial=False, affiliation=None)
    assert should_research(titled=False, partial=True, affiliation="Hoosier Families")
    assert not should_research(titled=False, partial=True, affiliation=None)
    assert not should_research(titled=True, partial=False, affiliation="Senate")


def test_cache_roundtrip_and_stored_miss(tmp_path):
    c = ResearchCache(tmp_path / "cache.json")
    k = c.key("Aaron  Spiegel", "Indy Multi-Faith", "Indianapolis, IN")
    assert c.get(k) is None
    c.put(k, {"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith", "url": "https://x", "extra": "dropped"})
    k2 = c.key("Nobody Here", None, None)
    c.put(k2, None)
    c.save()
    c2 = ResearchCache(tmp_path / "cache.json")
    assert c2.get(k) == {"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith", "url": "https://x"}
    assert c2.get(k2) == {"found": False}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_lookup.py -q`
Expected: FAIL — `ImportError: cannot import name 'RESEARCH_SCHEMA'`

- [ ] **Step 3: Implement (append to `src/name_lookup.py`)**

```python
import json
import subprocess
from pathlib import Path

RESEARCH_MODEL = "sonnet"
RESEARCH_TIMEOUT_S = 120
RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "name": {"type": "string"},
        "affiliation": {"type": "string"},
        "url": {"type": "string"},
    },
    "required": ["found"],
}
_UNAVAILABLE_MARKERS = ("authenticate", "oauth", "401", "log in", "login", "usage limit", "rate limit")


class ResearcherUnavailable(RuntimeError):
    """The claude CLI cannot run lookups now (missing, logged out, usage limit)."""


def build_prompt(name: str, title: Optional[str], affiliation: Optional[str], place: Optional[str]) -> str:
    return (
        "Find the exact spelling of the name of a person who spoke at a public meeting.\n"
        f'Spoken name (from an automatic transcript, may be misspelled): "{name}".\n'
        f'Title said: "{title or "none"}".\n'
        f'Stated affiliation: "{affiliation or "none"}".\n'
        f'Meeting place: "{place or "unknown"}".\n'
        "Search the web and open the most authoritative page that names this person "
        "(the organization's own site preferred). Return the exact spelling printed on "
        "that page, the affiliation as printed, and that page's URL. If no page clearly "
        "names this person with this affiliation or place, return found=false. Do not guess."
    )


def research_command(prompt: str, model: str = RESEARCH_MODEL) -> list[str]:
    return [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--json-schema", json.dumps(RESEARCH_SCHEMA),
        "--allowedTools", "WebSearch,WebFetch",
        "--max-turns", "8",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--model", model,
    ]


def run_cli(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def research(name: str, title: Optional[str], affiliation: Optional[str], place: Optional[str], *,
             runner: Callable[[list[str], int], tuple[int, str, str]] = run_cli) -> Optional[dict]:
    """{"name", "affiliation", "url"} for a found person, else None.

    Raises ResearcherUnavailable when the CLI is missing, logged out or out of usage.
    """
    try:
        _rc, out, err = runner(research_command(build_prompt(name, title, affiliation, place)),
                               RESEARCH_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise ResearcherUnavailable("claude CLI not installed") from exc
    except subprocess.TimeoutExpired:
        return None
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if data.get("is_error"):
        msg = f"{data.get('result') or ''} {err or ''}".lower()
        if any(m in msg for m in _UNAVAILABLE_MARKERS):
            raise ResearcherUnavailable(
                "Claude CLI cannot run lookups (not logged in or usage limit) — "
                "run `claude`, then /login, and try again")
        return None
    so = data.get("structured_output")
    if not isinstance(so, dict) or not so.get("found"):
        return None
    found_name, url = (so.get("name") or "").strip(), (so.get("url") or "").strip()
    if not found_name or not url:
        return None
    return {"name": found_name, "affiliation": (so.get("affiliation") or "").strip() or None, "url": url}


def should_research(titled: bool, partial: bool, affiliation: Optional[str]) -> bool:
    """Spec: titled names never go to the web; a partial name needs an affiliation."""
    if titled:
        return False
    return (not partial) or bool(affiliation)


class ResearchCache:
    """JSON cache keyed by (name, affiliation, place). Stores only name/affiliation/url."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    @staticmethod
    def key(name: str, affiliation: Optional[str], place: Optional[str]) -> str:
        return "|".join(norm_name(x or "") for x in (name, affiliation, place))

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def put(self, key: str, value: Optional[dict]) -> None:
        self._data[key] = ({k: value.get(k) for k in ("name", "affiliation", "url")}
                           if value else {"found": False})

    def save(self) -> None:
        from .atomic_io import atomic_write_json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, self._data)
```

Move the new `import json`, `import subprocess`, `from pathlib import Path` lines up into the module's import block (keep imports at the top of the file).

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_lookup.py -q`
Expected: 13 passed. If `atomic_write_json` has a different signature, read `src/atomic_io.py` and adapt the call (not the test).

- [ ] **Step 5: Commit**

```bash
git add src/name_lookup.py tests/test_name_lookup.py
git commit -m "feat(names): Claude Code researcher with structured output, unavailability, cache

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Roster, politician and local_people matchers; state inference; `NameDB`

**Files:**
- Modify: `src/name_lookup.py` (append)
- Test: `tests/test_name_lookup.py` (append)

**Interfaces:**
- Consumes: `src.roster.RosterMember(name, aliases, politician_slug, politician_id, district_label)`; `src.name_matching.significant_tokens`; `norm_name`.
- Produces:
  - `class NameDB(Protocol)`: `politicians_by_surname(surname: str, state: str) -> list[dict]` (`politician_id`, `full_name`), `states_for_politicians(ids: list[str]) -> list[str]`, `state_for_race(race_id: str) -> Optional[str]`, `local_people_by_name(name: str) -> list[dict]` (`slug`, `name`).
  - `class PgNameDB(database_url: str)` implementing it (read-only).
  - `match_roster(name: str, members: list) -> Optional[tuple[str, Optional[str]]]` → `(display_name, politician_id)`
  - `infer_state(member_politician_ids: list[str], race_id: Optional[str], db) -> Optional[str]`
  - `match_politician(name: str, state: Optional[str], db) -> Optional[dict]`
  - `match_local_people(name: str, db) -> Optional[dict]`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_name_lookup.py
from src.name_lookup import infer_state, match_local_people, match_politician, match_roster
from src.roster import RosterMember


class FakeDB:
    def __init__(self, pols=None, states=None, race_state=None, local=None):
        self.pols, self.states, self.race_state, self.local = pols or {}, states or [], race_state, local or []
        self.calls = []

    def politicians_by_surname(self, surname, state):
        self.calls.append(("pol", surname, state))
        return self.pols.get((surname.lower(), state), [])

    def states_for_politicians(self, ids):
        return self.states

    def state_for_race(self, race_id):
        return self.race_state

    def local_people_by_name(self, name):
        return [r for r in self.local if r["name"].lower() == name.lower()]


MEMBERS = [
    RosterMember(name="Cyndi Carrasco", aliases=["Cyndi Carrasco", "Carrasco", "Senator Carrasco"],
                 politician_id="p-carrasco"),
    RosterMember(name="Liz Brown", aliases=["Liz Brown", "Brown", "Senator Brown"], politician_id="p-brown"),
    RosterMember(name="Tim Brown", aliases=["Tim Brown", "Brown"], politician_id="p-tbrown"),
]


def test_match_roster_full_name_and_unique_surname():
    assert match_roster("Cyndi Carrasco", MEMBERS) == ("Cyndi Carrasco", "p-carrasco")
    assert match_roster("Carrasco", MEMBERS) == ("Cyndi Carrasco", "p-carrasco")
    assert match_roster("Liz Brown", MEMBERS) == ("Liz Brown", "p-brown")
    assert match_roster("Brown", MEMBERS) is None          # two Browns: ambiguous
    assert match_roster("Rachel Sample", MEMBERS) is None


def test_infer_state_prefers_roster_then_race():
    assert infer_state(["p1", "p2"], None, FakeDB(states=["IN"])) == "IN"
    assert infer_state(["p1"], "r1", FakeDB(states=["IN", "OH"], race_state="IN")) == "IN"
    assert infer_state([], "r1", FakeDB(race_state="TX")) == "TX"
    assert infer_state([], None, FakeDB()) is None


def test_match_politician_needs_state_and_a_unique_row():
    db = FakeDB(pols={("garten", "IN"): [{"politician_id": "p-g", "full_name": "Chris Garten"}],
                      ("smith", "IN"): [{"politician_id": "a", "full_name": "A Smith"},
                                        {"politician_id": "b", "full_name": "B Smith"}]})
    assert match_politician("Garten", "IN", db) == {"politician_id": "p-g", "full_name": "Chris Garten"}
    assert match_politician("Smith", "IN", db) is None
    assert match_politician("Garten", None, db) is None and db.calls == [("pol", "garten", "IN"), ("pol", "smith", "IN")]


def test_match_local_people_full_names_only():
    db = FakeDB(local=[{"slug": "rachael-sample", "name": "Rachael Sample"}])
    assert match_local_people("rachael sample", db) == {"slug": "rachael-sample", "name": "Rachael Sample"}
    assert match_local_people("Sample", db) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_lookup.py -q`
Expected: FAIL — `ImportError: cannot import name 'infer_state'`

- [ ] **Step 3: Implement (append to `src/name_lookup.py`; move imports to the top)**

```python
from collections import Counter
from typing import Protocol

from .name_matching import significant_tokens


class NameDB(Protocol):
    def politicians_by_surname(self, surname: str, state: str) -> list[dict]: ...
    def states_for_politicians(self, ids: list[str]) -> list[str]: ...
    def state_for_race(self, race_id: str) -> Optional[str]: ...
    def local_people_by_name(self, name: str) -> list[dict]: ...


def _surname(name: str) -> str:
    toks = significant_tokens(name)
    return toks[-1] if toks else ""


def match_roster(name: str, members: list) -> Optional[tuple[str, Optional[str]]]:
    """(display_name, politician_id) for an exact alias match, or a unique surname."""
    n = norm_name(name)
    exact = [m for m in members if n and any(norm_name(a) == n for a in [m.name, *m.aliases])]
    pool = exact or [m for m in members if _surname(name) and _surname(m.name) == _surname(name)]
    ids = {m.politician_id or m.name for m in pool}
    if len(ids) != 1:
        return None
    m = pool[0]
    full = next((a for a in m.aliases if len(significant_tokens(a)) >= 2), m.name)
    return full, m.politician_id


def infer_state(member_politician_ids: list[str], race_id: Optional[str], db) -> Optional[str]:
    if member_politician_ids:
        states = [s for s in db.states_for_politicians(member_politician_ids) if s]
        if states:
            top, count = Counter(states).most_common(1)[0]
            if count > len(states) / 2:
                return top
    return db.state_for_race(race_id) if race_id else None


def match_politician(name: str, state: Optional[str], db) -> Optional[dict]:
    if not state or not _surname(name):
        return None
    rows = db.politicians_by_surname(_surname(name), state)
    return rows[0] if len({r["politician_id"] for r in rows}) == 1 else None


def match_local_people(name: str, db) -> Optional[dict]:
    if len(significant_tokens(name)) < 2:
        return None
    rows = db.local_people_by_name(name)
    return rows[0] if len({r["slug"] for r in rows}) == 1 else None


class PgNameDB:
    """Read-only Postgres implementation of NameDB."""

    def __init__(self, database_url: str):
        import psycopg2

        self._conn = psycopg2.connect(database_url)
        self._conn.set_session(readonly=True, autocommit=True)

    def _rows(self, sql: str, params: tuple) -> list[dict]:
        import psycopg2.extras

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def politicians_by_surname(self, surname: str, state: str) -> list[dict]:
        return self._rows(
            """select distinct p.id::text as politician_id, p.full_name
               from essentials.politicians p
               join essentials.current_office_holders h on h.politician_id = p.id
               join essentials.offices o on o.id = h.office_id
               where lower(p.last_name) = lower(%s) and o.representing_state = %s""",
            (surname, state))

    def states_for_politicians(self, ids: list[str]) -> list[str]:
        rows = self._rows(
            """select o.representing_state as state
               from essentials.current_office_holders h
               join essentials.offices o on o.id = h.office_id
               where h.politician_id = any(%s::uuid[])""",
            (list(ids),))
        return [r["state"] for r in rows]

    def state_for_race(self, race_id: str) -> Optional[str]:
        rows = self._rows(
            """select e.state from essentials.races r
               join essentials.elections e on e.id = r.election_id where r.id = %s""",
            (race_id,))
        return rows[0]["state"] if rows else None

    def local_people_by_name(self, name: str) -> list[dict]:
        return self._rows(
            "select slug, name from meetings.local_people where lower(name) = lower(%s)",
            (name,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_lookup.py -q`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add src/name_lookup.py tests/test_name_lookup.py
git commit -m "feat(names): roster/politician/local_people matchers, state inference, read-only NameDB

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Orchestrator, output file, and `run_local.py --suggest-names`

**Files:**
- Create: `src/name_suggest.py`
- Modify: `run_local.py` (argparse + dispatch), `scripts/eval_name_suggestions.py` (reuse `read_captions_text`)
- Test: `tests/test_name_suggest.py`

**Interfaces:**
- Consumes: `extract_evidence`, `build_candidates`, `Candidate` (slice 1); everything from `src.name_lookup`; `src.roster.load_roster(body_slug=...)`; `src.models.Segment`.
- Produces:
  - `read_captions_text(meeting_dir: Path) -> Optional[str]` (moved from `scripts/eval_name_suggestions.py::_captions`, same behaviour)
  - `@dataclass Deps(db: Optional[NameDB], researcher: Callable = research, fetch: Callable = default_fetch, cache: Optional[ResearchCache] = None)`
  - `suggest_for_candidate(cand, *, members: list, state: Optional[str], place: Optional[str], deps: Deps, run: dict) -> Lookup` — `run` is a mutable dict holding `{"web_disabled": Optional[str]}`.
  - `suggest_names(meeting: dict, meeting_dir: Path, *, members: list, deps: Deps) -> dict` → `{"generated_at", "model", "state", "warnings": [...], "suggestions": [...]}`; each suggestion: `{label, tier, role, titled, conflict, spoken_name, evidence: [{kind, quote}], lookup: Lookup.to_dict()}`.
  - `write_suggestions(meeting_dir: Path, result: dict) -> Path` (writes `name_suggestions.json`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_name_suggest.py
from __future__ import annotations

import json

from src.name_candidates import Candidate
from src.name_lookup import ResearchCache, ResearcherUnavailable
from src.name_suggest import Deps, read_captions_text, suggest_for_candidate, suggest_names, write_suggestions
from src.roster import RosterMember

PAGE = "<html><body><p>Rabbi Aaron Spiegel, Executive Director, Indy Multi-Faith Alliance</p></body></html>"


def cand(name, *, tier="medium", titled=False, partial=False, affiliation=None, conflict=None, label="W"):
    return Candidate(label=label, name=name, tier=tier, role=None, titled=titled, partial=partial,
                     affiliation=affiliation, evidence=[], conflict=conflict)


class DB:
    def __init__(self, pols=None, local=None):
        self.pols, self.local = pols or {}, local or []

    def politicians_by_surname(self, s, st):
        return self.pols.get((s.lower(), st), [])

    def states_for_politicians(self, ids):
        return []

    def state_for_race(self, r):
        return None

    def local_people_by_name(self, n):
        return [r for r in self.local if r["name"].lower() == n.lower()]


def researcher(result):
    calls = []

    def r(name, title, affiliation, place):
        calls.append((name, title, affiliation, place))
        return result

    r.calls = calls
    return r


def run():
    return {"web_disabled": None}


def test_conflict_is_never_looked_up():
    rs = researcher({"name": "X Y", "affiliation": None, "url": "https://x"})
    lk = suggest_for_candidate(cand("Ann Lee", conflict="name_on_two_labels"), members=[], state=None,
                               place=None, deps=Deps(db=DB(), researcher=rs), run=run())
    assert (lk.source, lk.verified, lk.reason) == ("transcript", False, "conflict: name_on_two_labels") and rs.calls == []


def test_roster_first():
    m = [RosterMember(name="Liz Brown", aliases=["Liz Brown", "Brown"], politician_id="p-b")]
    lk = suggest_for_candidate(cand("Brown", titled=True, partial=True), members=m, state="IN", place=None,
                               deps=Deps(db=DB()), run=run())
    assert (lk.name, lk.source, lk.verified, lk.politician_id) == ("Liz Brown", "roster", True, "p-b")


def test_titled_uses_politicians_and_never_the_web():
    db = DB(pols={("garten", "IN"): [{"politician_id": "p-g", "full_name": "Chris Garten"}]})
    rs = researcher({"name": "Q", "affiliation": None, "url": "https://x"})
    lk = suggest_for_candidate(cand("Garten", titled=True, partial=True), members=[], state="IN", place=None,
                               deps=Deps(db=db, researcher=rs), run=run())
    assert (lk.name, lk.source, lk.politician_id) == ("Chris Garten", "politician", "p-g")
    lk2 = suggest_for_candidate(cand("Smith", titled=True, partial=True), members=[], state="IN", place=None,
                                deps=Deps(db=db, researcher=rs), run=run())
    assert (lk2.source, lk2.verified) == ("transcript", False) and rs.calls == []


def test_local_people_before_web():
    db = DB(local=[{"slug": "rachael-sample", "name": "Rachael Sample"}])
    lk = suggest_for_candidate(cand("Rachael Sample"), members=[], state=None, place=None,
                               deps=Deps(db=db, researcher=researcher(None)), run=run())
    assert (lk.source, lk.local_slug, lk.verified) == ("local_people", "rachael-sample", True)


def test_web_verified_and_unverified(tmp_path):
    rs = researcher({"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance", "url": "https://imfa.org"})
    deps = Deps(db=DB(), researcher=rs, fetch=lambda u: PAGE, cache=ResearchCache(tmp_path / "c.json"))
    lk = suggest_for_candidate(cand("Aaron Spiegal", affiliation="Indy multi-faith"), members=[], state="IN",
                               place="Indianapolis, IN", deps=deps, run=run())
    assert (lk.name, lk.source, lk.verified, lk.url) == ("Aaron Spiegel", "web", True, "https://imfa.org")
    assert rs.calls == [("Aaron Spiegal", None, "Indy multi-faith", "Indianapolis, IN")]
    # second time: cache hit, no new call
    suggest_for_candidate(cand("Aaron Spiegal", affiliation="Indy multi-faith"), members=[], state="IN",
                          place="Indianapolis, IN", deps=deps, run=run())
    assert len(rs.calls) == 1
    bad = Deps(db=DB(), researcher=researcher({"name": "Ann Lee", "affiliation": None, "url": "https://x"}),
               fetch=lambda u: "<p>nobody</p>")
    lk3 = suggest_for_candidate(cand("Ann Lee"), members=[], state=None, place=None, deps=bad, run=run())
    assert (lk3.name, lk3.source, lk3.verified, lk3.reason) == ("Ann Lee", "transcript", False, "name not on page")


def test_not_searched_when_partial_without_affiliation():
    rs = researcher(None)
    lk = suggest_for_candidate(cand("Michael", partial=True), members=[], state=None, place=None,
                               deps=Deps(db=DB(), researcher=rs), run=run())
    assert lk.reason == "not searched: partial name without affiliation" and rs.calls == []


def test_unavailable_disables_web_for_the_run():
    calls = []

    def down(*a):
        calls.append(a)
        raise ResearcherUnavailable("Claude CLI cannot run lookups — run `claude`, then /login")

    state = run()
    for n in ("Ann Lee", "Bob Ray"):
        lk = suggest_for_candidate(cand(n), members=[], state=None, place=None,
                                   deps=Deps(db=DB(), researcher=down), run=state)
        assert lk.reason == "web lookup unavailable"
    assert len(calls) == 1 and "login" in state["web_disabled"]


def test_suggest_names_end_to_end_and_write(tmp_path):
    meeting = {"meeting_id": "m1", "city": "Indianapolis", "event_kind": "council", "race_id": None, "segments": [
        {"segment_id": 0, "start_time": 0, "end_time": 5, "speaker_label": "CHAIR",
         "text": "Okay, next we will hear from Rabbi Aaron Spiegel."},
        {"segment_id": 1, "start_time": 5, "end_time": 30, "speaker_label": "W",
         "text": "Thank you. My name is Rabbi Aaron Spiegel, I'm with the Indy Multi-Faith Alliance. "
                 "I am here to support this bill and thank the committee for its time."},
    ]}
    rs = researcher({"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance", "url": "https://imfa.org"})
    out = suggest_names(meeting, tmp_path, members=[], deps=Deps(db=None, researcher=rs, fetch=lambda u: PAGE))
    [s] = out["suggestions"]
    assert s["label"] == "W" and s["tier"] == "strong" and s["lookup"]["verified"] is True
    assert {e["kind"] for e in s["evidence"]} == {"E1", "E2"}
    assert out["warnings"] == []
    p = write_suggestions(tmp_path, out)
    assert json.loads(p.read_text())["suggestions"][0]["lookup"]["url"] == "https://imfa.org"


def test_read_captions_text_strips_vtt(tmp_path):
    (tmp_path / "captions.vtt").write_text(
        "WEBVTT\nKind: captions\n\n00:00:01.000 --> 00:00:03.000\nhi<00:00:02.000><c> Angelica</c> Salas\n")
    t = read_captions_text(tmp_path)
    assert "Angelica Salas" in " ".join(t.split()) and "-->" not in t and "WEBVTT" not in t
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.name_suggest'`

- [ ] **Step 3: Implement `src/name_suggest.py`**

Copy the body of `_captions` from `scripts/eval_name_suggestions.py` into `read_captions_text(meeting_dir)` here (same regexes and file order), then in that script replace `_captions` with `from src.name_suggest import read_captions_text` and call it — no behaviour change.

```python
# src/name_suggest.py
"""Name suggestions for one meeting: evidence -> candidates -> lookup -> JSON.

Spec: docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md (slice 2).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .models import Segment
from .name_candidates import Candidate, build_candidates
from .name_evidence import extract_evidence
from .name_lookup import (
    RESEARCH_MODEL, Lookup, ResearchCache, ResearcherUnavailable, default_fetch, infer_state,
    match_local_people, match_politician, match_roster, research, should_research, verify_on_page,
)

OUTPUT_NAME = "name_suggestions.json"


def read_captions_text(meeting_dir: Path) -> Optional[str]:
    ...  # moved verbatim from scripts/eval_name_suggestions.py::_captions


@dataclass
class Deps:
    db: Optional[object]
    researcher: Callable = research
    fetch: Callable[[str], str] = default_fetch
    cache: Optional[ResearchCache] = None


def _transcript(cand: Candidate, reason: str) -> Lookup:
    return Lookup(name=cand.name, source="transcript", verified=False,
                  affiliation=cand.affiliation, reason=reason)


def suggest_for_candidate(cand: Candidate, *, members: list, state: Optional[str], place: Optional[str],
                          deps: Deps, run: dict) -> Lookup:
    if cand.conflict:
        return _transcript(cand, f"conflict: {cand.conflict}")
    hit = match_roster(cand.name, members) if members else None
    if hit:
        return Lookup(name=hit[0], source="roster", verified=True, politician_id=hit[1])
    if cand.titled:
        pol = match_politician(cand.name, state, deps.db) if deps.db else None
        if pol:
            return Lookup(name=pol["full_name"], source="politician", verified=True,
                          politician_id=pol["politician_id"])
        return _transcript(cand, "titled: no unique politician match" if state else "titled: state unknown")
    if deps.db:
        local = match_local_people(cand.name, deps.db)
        if local:
            return Lookup(name=local["name"], source="local_people", verified=True, local_slug=local["slug"])
    if not should_research(cand.titled, cand.partial, cand.affiliation):
        return _transcript(cand, "not searched: partial name without affiliation")
    if run.get("web_disabled"):
        return _transcript(cand, "web lookup unavailable")

    key = ResearchCache.key(cand.name, cand.affiliation, place)
    cached = deps.cache.get(key) if deps.cache else None
    if cached is not None:
        found = None if cached.get("found") is False else cached
    else:
        try:
            found = deps.researcher(cand.name, None, cand.affiliation, place)
        except ResearcherUnavailable as exc:
            run["web_disabled"] = str(exc)
            return _transcript(cand, "web lookup unavailable")
        if deps.cache:
            deps.cache.put(key, found)
    if not found:
        return _transcript(cand, "not found on the web")
    ok, why = verify_on_page(found["name"], found["url"], deps.fetch)
    if not ok:
        return _transcript(cand, why)
    return Lookup(name=found["name"], source="web", verified=True, url=found["url"],
                  affiliation=found.get("affiliation") or cand.affiliation)


def suggest_names(meeting: dict, meeting_dir: Path, *, members: list, deps: Deps) -> dict:
    segments = [Segment.from_dict(s) for s in meeting.get("segments", [])]
    cands = build_candidates(extract_evidence(segments, read_captions_text(meeting_dir)),
                             meeting.get("event_kind"))
    member_ids = [m.politician_id for m in members if getattr(m, "politician_id", None)]
    state = infer_state(member_ids, meeting.get("race_id"), deps.db) if deps.db else None
    place = ", ".join(x for x in (meeting.get("city"), state) if x) or None
    run: dict = {"web_disabled": None}
    out = []
    for label in sorted(cands):
        c = cands[label]
        lk = suggest_for_candidate(c, members=members, state=state, place=place, deps=deps, run=run)
        out.append({
            "label": label, "tier": c.tier, "role": c.role, "titled": c.titled, "conflict": c.conflict,
            "spoken_name": c.name,
            "evidence": [{"kind": e.kind, "quote": e.quote} for e in c.evidence],
            "lookup": lk.to_dict(),
        })
    if deps.cache:
        deps.cache.save()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": RESEARCH_MODEL, "state": state,
        "warnings": [run["web_disabled"]] if run["web_disabled"] else [],
        "suggestions": out,
    }


def write_suggestions(meeting_dir: Path, result: dict) -> Path:
    path = Path(meeting_dir) / OUTPUT_NAME
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggest.py tests/test_eval_name_suggestions_cli.py -q`
Expected: all pass (9 + 4).

- [ ] **Step 5: Add the CLI flag**

In `run_local.py`:
1. Next to the other meeting-scoped flags (search for `parser.add_argument("--review", metavar="MEETING_ID"`), add:
```python
    parser.add_argument("--suggest-names", metavar="MEETING_ID",
                        help="Suggest names for unnamed speakers (self-intros, chair calls, "
                             "roster/politician/past-meeting/web lookup); writes name_suggestions.json")
```
2. In `main()`, where single-meeting commands are dispatched (search for `_review_meeting(args.review)`), add before it:
```python
    if args.suggest_names:
        _suggest_names(args.suggest_names)
        return
```
3. Add the function near `_review_meeting`:
```python
def _suggest_names(meeting_id: str) -> None:
    """Write <meeting_dir>/name_suggestions.json and print a summary."""
    from src.name_lookup import PgNameDB, ResearchCache
    from src.name_suggest import Deps, suggest_names, write_suggestions
    from src.roster import load_roster

    meeting_dir = config.MEETINGS_DIR / meeting_id
    meeting = json.loads((meeting_dir / "transcript_named.json").read_text(encoding="utf-8"))
    state_path = meeting_dir / "pipeline_state.json"
    body_slug = json.loads(state_path.read_text()).get("body_slug") if state_path.exists() else None
    roster = load_roster(body_slug=body_slug) if body_slug else None
    db_url = os.environ.get("DATABASE_URL", "").strip()
    deps = Deps(db=PgNameDB(db_url) if db_url else None,
                cache=ResearchCache(config.CONFIG_DIR / "name_lookup_cache.json"))
    result = suggest_names(meeting, meeting_dir, members=roster.members if roster else [], deps=deps)
    path = write_suggestions(meeting_dir, result)
    for w in result["warnings"]:
        print(f"  WARNING: {w}")
    for s in result["suggestions"]:
        lk = s["lookup"]
        mark = "✓" if lk["verified"] else "·"
        print(f"  {mark} {s['label']:<11} {s['tier'] or '-':<7} {lk['name']!s:<28} {lk['source']:<12} "
              f"{lk['url'] or lk['reason'] or ''}")
    print(f"Wrote {path}")
```
Use the module's existing names for the meetings directory and JSON/OS imports (check `config` for the meetings-dir constant — e.g. `config.MEETINGS_DIR` or the helper other commands use to build `meeting_dir`; follow whatever `_review_meeting` uses).

- [ ] **Step 6: Run the full suite and commit**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all pass.

```bash
git add src/name_suggest.py tests/test_name_suggest.py run_local.py scripts/eval_name_suggestions.py
git commit -m "feat(names): per-meeting name suggestions + run_local.py --suggest-names

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Spelling eval on gold witnesses, real run, and PR

**Files:**
- Modify: `src/name_suggestion_eval.py` (add `score_lookup_rows`)
- Create: `scripts/eval_name_lookup.py`
- Test: `tests/test_name_lookup_eval.py`

**Interfaces:**
- Consumes: slice-1 `gold_labels`, `strip_names`, `extract_evidence`, `build_candidates`; `should_research`, `research`, `verify_on_page`, `ResearchCache`, `norm_name`; `read_captions_text`.
- Produces:
  - `score_lookup_rows(rows: list[dict]) -> dict` — rows have `gold`, `spoken`, `looked_up` (name or None), `verified` (bool), `status` (`"verified" | "not_verified" | "not_found" | "unavailable"`). Returns `{"n", "spoken_exact", "final_exact", "verified", "verified_exact", "verified_precision", "not_found", "unavailable"}` where `final` = `looked_up` if verified else `spoken`, and exact = `norm_name` equality.
  - CLI `scripts/eval_name_lookup.py [--meetings-dir] [--sample 50] [--seed 7] [--cache PATH] [--json PATH]` with `select_witnesses(meetings_dir, sample, seed) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_name_lookup_eval.py
from __future__ import annotations

from src.name_suggestion_eval import score_lookup_rows


def test_score_lookup_rows():
    rows = [
        {"gold": "Bob Costello", "spoken": "Bob Gasillo", "looked_up": "Bob Costello", "verified": True, "status": "verified"},
        {"gold": "Rachael Sample", "spoken": "Rachel Sample", "looked_up": "Rachel Sample", "verified": True, "status": "verified"},
        {"gold": "Ann Lee", "spoken": "Ann Lee", "looked_up": None, "verified": False, "status": "not_found"},
        {"gold": "Jo Fox", "spoken": "Joe Fox", "looked_up": "Joe Fox", "verified": False, "status": "not_verified"},
        {"gold": "Kim Wu", "spoken": "Kim Woo", "looked_up": None, "verified": False, "status": "unavailable"},
    ]
    s = score_lookup_rows(rows)
    assert s["n"] == 5 and s["spoken_exact"] == 1 and s["final_exact"] == 2
    assert s["verified"] == 2 and s["verified_exact"] == 1 and s["verified_precision"] == 0.5
    assert s["not_found"] == 1 and s["unavailable"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_lookup_eval.py -q`
Expected: FAIL — `ImportError: cannot import name 'score_lookup_rows'`

- [ ] **Step 3: Implement `score_lookup_rows` (append to `src/name_suggestion_eval.py`)**

```python
def score_lookup_rows(rows: list[dict]) -> dict:
    """Spelling outcomes of the web lookup on gold witnesses (slice 2)."""
    from .name_lookup import norm_name

    def exact(a, b):
        return bool(a and b) and norm_name(a) == norm_name(b)

    verified = [r for r in rows if r.get("verified")]
    v_exact = sum(1 for r in verified if exact(r["gold"], r["looked_up"]))
    return {
        "n": len(rows),
        "spoken_exact": sum(1 for r in rows if exact(r["gold"], r["spoken"])),
        "final_exact": sum(1 for r in rows if exact(r["gold"], r["looked_up"] if r.get("verified") else r["spoken"])),
        "verified": len(verified),
        "verified_exact": v_exact,
        "verified_precision": round(v_exact / len(verified), 3) if verified else 0.0,
        "not_found": sum(1 for r in rows if r.get("status") == "not_found"),
        "unavailable": sum(1 for r in rows if r.get("status") == "unavailable"),
    }
```

Run the test: expect 1 passed.

- [ ] **Step 4: Write `scripts/eval_name_lookup.py`**

```python
#!/usr/bin/env python
"""Spelling accuracy of the web name lookup on gold witnesses (slice 2).

Web step only: the roster/politician/local_people steps are skipped because
published gold names are already in meetings.local_people (they would leak
the answer). Calls the real `claude` CLI — run on Chris's Mac, logged in.

Usage:
  .venv/bin/python scripts/eval_name_lookup.py --sample 50 --json /tmp/lookup_eval.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.name_candidates import build_candidates  # noqa: E402
from src.name_evidence import extract_evidence  # noqa: E402
from src.name_lookup import ResearchCache, ResearcherUnavailable, research, should_research, verify_on_page  # noqa: E402
from src.name_suggest import read_captions_text  # noqa: E402
from src.name_suggestion_eval import gold_labels, score_lookup_rows, strip_names  # noqa: E402


def select_witnesses(meetings_dir: Path, sample: int, seed: int) -> list[dict]:
    """Gold, non-politician speakers whose candidate would be web-researched,
    sampled round-robin across event kinds with a fixed seed."""
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(glob.glob(str(meetings_dir / "*" / "transcript_named.json"))):
        try:
            meeting = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(meeting, dict):
            continue
        gold = {k: v for k, v in gold_labels(meeting).items() if v and not v.startswith("__")}
        if not gold:
            continue
        speakers = meeting.get("speakers") or {}
        mdir = Path(path).parent
        cands = build_candidates(extract_evidence(strip_names(meeting), read_captions_text(mdir)),
                                 meeting.get("event_kind"))
        for label, gname in gold.items():
            sp = speakers.get(label, {}) if isinstance(speakers, dict) else {}
            c = cands.get(label)
            if sp.get("politician_id") or not c or not c.name or c.conflict:
                continue
            if not should_research(c.titled, c.partial, c.affiliation):
                continue
            by_kind[meeting.get("event_kind") or "unknown"].append({
                "meeting": mdir.name, "label": label, "event_kind": meeting.get("event_kind"),
                "gold": gname, "spoken": c.name, "affiliation": c.affiliation,
                "place": meeting.get("city") or None,
            })
    rng = random.Random(seed)
    for items in by_kind.values():
        rng.shuffle(items)
    picked, kinds = [], sorted(by_kind)
    while len(picked) < sample and any(by_kind[k] for k in kinds):
        for k in kinds:
            if by_kind[k] and len(picked) < sample:
                picked.append(by_kind[k].pop())
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings-dir", default=os.path.expanduser("~/CouncilScribe/meetings"))
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cache", default=os.path.expanduser("~/CouncilScribe/config/name_lookup_eval_cache.json"))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    rows = select_witnesses(Path(args.meetings_dir), args.sample, args.seed)
    print(f"Selected {len(rows)} gold witnesses")
    cache = ResearchCache(Path(args.cache))
    unavailable = None
    for i, r in enumerate(rows, 1):
        r.update(looked_up=None, verified=False, status="not_found", url=None, reason=None)
        if unavailable:
            r["status"] = "unavailable"
            continue
        key = ResearchCache.key(r["spoken"], r["affiliation"], r["place"])
        cached = cache.get(key)
        try:
            found = (None if cached and cached.get("found") is False else cached) if cached is not None else \
                research(r["spoken"], None, r["affiliation"], r["place"])
        except ResearcherUnavailable as exc:
            unavailable = str(exc)
            r["status"] = "unavailable"
            print(f"  STOP: {unavailable}")
            continue
        if cached is None:
            cache.put(key, found)
            cache.save()
        if found:
            ok, why = verify_on_page(found["name"], found["url"])
            r.update(looked_up=found["name"], verified=ok, status="verified" if ok else "not_verified",
                     url=found["url"] if ok else None, reason=why)
        print(f"  [{i}/{len(rows)}] {r['status']:<12} gold={r['gold']!r} spoken={r['spoken']!r} -> {r['looked_up']!r}")
    print("\nSummary:", json.dumps(score_lookup_rows(rows), indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Check that the claude CLI is logged in, then run the eval**

Run: `claude -p "say ok" --no-session-persistence --strict-mcp-config`
Expected: prints a short answer. If it prints an authentication / expired-token error, STOP and report NEEDS_CONTEXT ("Chris must run `claude`, then /login") — do not proceed.

Run (from the worktree; outputs go to the SDD workspace the controller names):
```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python scripts/eval_name_lookup.py --sample 50 --json <WORKSPACE>/lookup_eval.json | tee <WORKSPACE>/lookup_eval.txt
```
Expected: "Selected N gold witnesses" (N close to 50), one line per witness, and a Summary block. Do not tune rules toward this output.

- [ ] **Step 6: Run the full suite and commit (no push)**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q -p no:cacheprovider`

```bash
git add src/name_suggestion_eval.py scripts/eval_name_lookup.py tests/test_name_lookup_eval.py
git commit -m "feat(names): spelling eval of the web lookup on gold witnesses

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

The controller opens the PR after the final review; its body includes the Summary block and the verified-but-wrong rows.

---

## Self-review notes (plan author)

- Spec coverage: lookup order a–e → Tasks 4–5; page verification → Task 2; researcher, privacy, cache, unavailability → Task 3 + Task 5; error-handling table → Tasks 2, 3, 5 tests; output + CLI → Task 5; 50-witness spelling test → Task 6; parked fixes → Task 1. Review UI and pipeline auto-run are slice 3.
- `run_local.py` locations are given by search anchors because the file is ~4,600 lines; the implementer follows `_review_meeting`'s way of building `meeting_dir`.
- The eval sends only spoken name + affiliation + city to the web (no transcript text), same as production.
