# Evidence Pipeline Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the evidence extractor read whole/large sources without silently losing quotes, and add an opt-in headless-browser fetch fallback for pages that block plain HTTP — so the pipeline yields real evidence for candidates like Karen Bass.

**Architecture:** Two independent changes in on-the-record. (1) `src/evidence/extract.py`: window the full source, extract per window, salvage truncated JSON, dedup. (2) `src/discovery/feeds.py`: a pluggable fetch chain — plain HTTP → Playwright render — behind an opt-in flag, reusing the existing HTML→text extraction. The runner turns the fallback on. No schema, no ev-accounts, no trust-gate changes.

**Tech Stack:** Python 3, `requests` (existing), `playwright` (new, sync API, lazy-imported), pytest. LLMs via `src/llm_providers.get_provider` over OpenRouter.

## Global Constraints

- **LLM models are OpenRouter only:** extractor `haiku-or`, crosschecker `gemini-flash`, judge `deepseek`. Direct Anthropic (`sonnet`) has no credits and must not be a live default.
- **The verbatim gate must keep verifying against the FULL fetched page** (`verbatim_ok(cand.text, text)` in `pipeline.py:59`). Windowing is an extractor-input concern only; do not pass a window to `verbatim_ok`.
- **`extract_quotes` keeps its call contract** used at `pipeline.py:53`: `extract_quotes(text, *, candidate_name=..., provider=...)`. New parameters are keyword-only with defaults.
- **`fetch_page_text`'s default behavior is unchanged** when `render_fallback` is not passed (same return values, same raises). Every existing caller (discovery classify, etc.) must be unaffected.
- **Playwright is lazy-imported** inside the renderer and **`render_fallback` defaults to `False`**, so no caller needs a browser unless it opts in.
- **Offline tests never launch a real browser or hit the network:** inject a fake `renderer` and a fake `provider`.
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Run tests and the runner with the MAIN checkout venv: `~/Documents/GitHub/on-the-record/.venv/bin/python`.

## File Structure

- `src/evidence/extract.py` — MODIFY: add `chunk_text`, `_split_point`, `_iter_json_objects` (salvage), `_to_candidate`, `_dedup`, `_norm`; rewrite `extract_quotes`; drop the `[:60000]` cap in `build_extract_prompt`.
- `tests/test_evidence_extract.py` — CREATE (or extend if present): unit tests for the above, mock provider.
- `src/discovery/feeds.py` — MODIFY: refactor the plain body into `_extract_body_text` + `_page_text_from_bytes` (no behavior change); add `render_fallback`/`renderer` params + `_render_page_text` + `_RENDER_MIN_CHARS` + `_fetch_rendered`.
- `tests/test_feeds_render.py` — CREATE: unit tests for the chain with an injected renderer.
- `scripts/evidence_slice.py` — MODIFY: `--render/--no-render` flag; wire `render_fallback` into the fetcher partial; docstring note about `playwright install chromium`.
- `requirements.txt` (or the project's requirements file) — MODIFY: add `playwright`.

Locate the existing extractor tests before Task 1: `ls tests/ | grep -i extract`. If a test file exists, extend it; otherwise create `tests/test_evidence_extract.py`.

---

### Task 1: `chunk_text` — window the full source with overlap

**Files:**
- Modify: `src/evidence/extract.py`
- Test: `tests/test_evidence_extract.py`

**Interfaces:**
- Produces: `chunk_text(text: str, size: int = 12000, overlap: int = 2000) -> list[str]` and `_split_point(text: str, target: int, floor: int) -> int`. Later tasks call `chunk_text` from `extract_quotes`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_extract.py
from src.evidence.extract import chunk_text

def test_chunk_text_short_returns_single_window():
    assert chunk_text("hello", size=100) == ["hello"]
    assert chunk_text("", size=100) == []

def test_chunk_text_windows_cover_all_text_with_overlap():
    text = "".join(f"word{i} " for i in range(4000))  # ~ >12000 chars
    windows = chunk_text(text, size=3000, overlap=500)
    assert len(windows) > 1
    assert all(len(w) <= 3000 for w in windows)
    # every character position appears in at least one window (no gaps)
    covered = 0
    for w in windows:
        start = text.index(w, max(0, covered - len(w)))
        assert start <= covered  # windows are contiguous/overlapping, no gap
        covered = max(covered, start + len(w))
    assert covered == len(text)

def test_chunk_text_prefers_paragraph_boundary():
    left = "a" * 2900
    right = "b" * 2900
    text = left + "\n\n" + right
    windows = chunk_text(text, size=3000, overlap=200)
    # the first window ends at the blank-line boundary, not mid-run
    assert windows[0].endswith("\n\n") or windows[0] == left + "\n\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -k chunk_text -v`
Expected: FAIL (ImportError: cannot import name 'chunk_text').

- [ ] **Step 3: Implement `chunk_text` + `_split_point`**

```python
# src/evidence/extract.py  (add near the top, after imports)
def _split_point(text: str, target: int, floor: int) -> int:
    """Index just after a natural boundary at or before `target` but not before
    `floor`; falls back to `target` when none is found (so a run with no
    boundary still splits)."""
    for sep in ("\n\n", "\n", ". ", " "):
        i = text.rfind(sep, floor, target)
        if i != -1:
            return i + len(sep)
    return target


def chunk_text(text: str, size: int = 12000, overlap: int = 2000) -> list:
    """Split `text` into overlapping windows of about `size` chars, preferring
    to cut on a paragraph/sentence/space boundary. Overlap keeps a quote that
    straddles a cut whole in an adjacent window. Empty text -> no windows."""
    text = text or ""
    if len(text) <= size:
        return [text] if text else []
    windows, start, n = [], 0, len(text)
    while start < n:
        target = min(start + size, n)
        end = target if target >= n else _split_point(text, target, start + size // 2)
        windows.append(text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return windows
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -k chunk_text -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/extract.py tests/test_evidence_extract.py
git commit -m "feat(evidence): chunk_text windows the full source with overlap

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Salvage complete quote objects from truncated JSON

**Files:**
- Modify: `src/evidence/extract.py`
- Test: `tests/test_evidence_extract.py`

**Interfaces:**
- Consumes: existing `_FENCE`, `QuoteCandidate`, current `parse_extract` field logic.
- Produces: `_iter_json_objects(payload: str) -> Iterator[dict]`, `_to_candidate(q) -> QuoteCandidate | None`, and a `parse_extract` that recovers complete objects on a `JSONDecodeError` instead of returning `[]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_extract.py
from src.evidence.extract import parse_extract

def test_parse_extract_normal_json():
    raw = '{"quotes":[{"text":"I will build 10000 homes.","issue":"housing"}]}'
    out = parse_extract(raw)
    assert len(out) == 1 and out[0].text == "I will build 10000 homes."

def test_parse_extract_salvages_truncated_reply():
    # Two complete objects, then a third cut off mid-string (the Bass failure).
    raw = ('{"quotes":['
           '{"text":"A: declare a state of emergency.","issue":"homelessness"},'
           '{"text":"B: end all street encampments.","issue":"homelessness"},'
           '{"text":"C: appoint and empower one indiv')
    out = parse_extract(raw)
    assert [c.text for c in out] == [
        "A: declare a state of emergency.",
        "B: end all street encampments.",
    ]

def test_parse_extract_junk_returns_empty():
    assert parse_extract("not json at all") == []
    assert parse_extract("") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -k parse_extract -v`
Expected: FAIL (salvage test returns `[]` today).

- [ ] **Step 3: Implement salvage + refactor `parse_extract`**

```python
# src/evidence/extract.py  (replace the existing parse_extract)
def _iter_json_objects(payload: str):
    """Yield each complete top-level object inside the `quotes` array, stopping
    at the first incomplete one. Lets a truncated reply still surrender the
    quotes it did finish."""
    key = payload.find('"quotes"')
    lb = payload.find("[", key) if key != -1 else payload.find("[")
    if lb == -1:
        return
    dec = json.JSONDecoder()
    i, n = lb + 1, len(payload)
    while i < n:
        j = payload.find("{", i)
        if j == -1:
            break
        try:
            obj, end = dec.raw_decode(payload, j)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            yield obj
        i = end


def _to_candidate(q):
    if not isinstance(q, dict):
        return None
    text = q.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    ctx = q.get("context"); iss = q.get("issue")
    return QuoteCandidate(
        text=text.strip(),
        context=ctx.strip() if isinstance(ctx, str) else "",
        issue=iss.strip().lower() if isinstance(iss, str) else "",
        date=q.get("date"), setting=q.get("setting"),
        is_own_words=bool(q.get("is_own_words", True)),
        is_primary_venue=bool(q.get("is_primary_venue", True)),
        reported_event=q.get("reported_event"),
        primary_handle=q.get("primary_handle"))


def parse_extract(raw: str) -> list:
    m = _FENCE.search(raw or "")
    payload = m.group(1) if m else (raw or "")
    try:
        data = json.loads(payload)
        items = data.get("quotes", []) if isinstance(data, dict) else []
    except json.JSONDecodeError:
        items = list(_iter_json_objects(payload))  # salvage a truncated reply
    out = []
    for q in items:
        c = _to_candidate(q)
        if c is not None:
            out.append(c)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -k parse_extract -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/extract.py tests/test_evidence_extract.py
git commit -m "feat(evidence): salvage complete quotes from a truncated extractor reply

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Per-window extraction + dedup in `extract_quotes`

**Files:**
- Modify: `src/evidence/extract.py`
- Test: `tests/test_evidence_extract.py`

**Interfaces:**
- Consumes: `chunk_text` (Task 1), `parse_extract` (Task 2).
- Produces: `extract_quotes(text, *, candidate_name, provider, max_tokens=3000, chunk_size=12000, overlap=2000) -> list` — extracts per window and dedups; `_dedup(cands) -> list`, `_norm(s) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_extract.py
from src.evidence.extract import extract_quotes

class _FakeProvider:
    """Returns a canned reply per call; records the prompts it saw."""
    def __init__(self, replies):
        self._replies = list(replies)
        self.prompts = []
    def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        return self._replies.pop(0) if self._replies else '{"quotes":[]}'

def test_extract_quotes_calls_provider_per_window_and_merges():
    text = "P" * 3000 + "\n\n" + "Q" * 3000  # forces >1 window at size=3000
    prov = _FakeProvider([
        '{"quotes":[{"text":"from window one","issue":"a"}]}',
        '{"quotes":[{"text":"from window two","issue":"b"}]}',
    ])
    out = extract_quotes(text, candidate_name="X", provider=prov,
                         chunk_size=3000, overlap=200)
    assert len(prov.prompts) >= 2
    assert {c.text for c in out} == {"from window one", "from window two"}

def test_extract_quotes_dedups_overlap_duplicates():
    text = "P" * 3000 + "\n\n" + "Q" * 3000
    dup = '{"quotes":[{"text":"Same quote, verbatim.","issue":"a"}]}'
    prov = _FakeProvider([dup, dup])
    out = extract_quotes(text, candidate_name="X", provider=prov,
                         chunk_size=3000, overlap=200)
    assert len(out) == 1

def test_extract_quotes_reads_past_60k():
    # content only in the tail (past the old 60000-char cap) must be reached
    text = ("filler. " * 9000) + "TAILMARKER"   # ~72000 chars
    seen = {}
    class P:
        def complete(self, prompt, **kw):
            seen["tail_in_some_prompt"] = seen.get("tail_in_some_prompt") or ("TAILMARKER" in prompt)
            return '{"quotes":[]}'
    extract_quotes(text, candidate_name="X", provider=P(),
                   chunk_size=20000, overlap=1000)
    assert seen["tail_in_some_prompt"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -k extract_quotes -v`
Expected: FAIL (current `extract_quotes` calls the provider once and has no chunk params).

- [ ] **Step 3: Implement `_norm`, `_dedup`, rewrite `extract_quotes`, drop the 60K cap**

```python
# src/evidence/extract.py

# build_extract_prompt: the window is already bounded by chunk_text, so drop
# the [:60000] cap (it truncated the input and dropped tail content).
def build_extract_prompt(text: str, candidate_name: str) -> str:
    return _INSTRUCTIONS.format(name=candidate_name, text=text)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _dedup(cands: list) -> list:
    seen, out = set(), []
    for c in cands:
        k = _norm(c.text)
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def extract_quotes(text, *, candidate_name, provider, max_tokens=3000,
                   chunk_size=12000, overlap=2000) -> list:
    cands = []
    for window in chunk_text(text, chunk_size, overlap):
        raw = provider.complete(build_extract_prompt(window, candidate_name),
                                max_tokens=max_tokens, temperature=0.0,
                                system=_SYSTEM)
        cands.extend(parse_extract(raw))
    return _dedup(cands)
```

- [ ] **Step 4: Run the full evidence-extract test file**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -v`
Expected: PASS (all tasks 1-3 green).

- [ ] **Step 5: Commit**

```bash
git add src/evidence/extract.py tests/test_evidence_extract.py
git commit -m "feat(evidence): extract per window and dedup; read the whole source

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Refactor the plain fetch body into reusable helpers (no behavior change)

**Files:**
- Modify: `src/discovery/feeds.py`
- Test: `tests/test_feeds_render.py`

**Interfaces:**
- Produces: `_extract_body_text(html_str: str, max_chars: int) -> str` (the block-clean → longest article/main slice → `_html_to_text` → Candidate-Connection-window → truncate logic, lifted verbatim from `fetch_page_text`), and `_page_text_from_bytes(url: str, max_chars: int) -> str` (fetch bytes + content-type gate + `_extract_body_text`). `fetch_page_text` calls `_page_text_from_bytes` after the robots/pause guard; its observable behavior is identical.

- [ ] **Step 1: Write the characterization test (guards against behavior drift)**

```python
# tests/test_feeds_render.py
from src.discovery import feeds

def test_extract_body_text_prefers_longest_article(monkeypatch):
    html = "<html><body><nav>menu menu menu</nav><article>" + ("Real body. " * 40) + "</article></body></html>"
    out = feeds._extract_body_text(html, max_chars=6000)
    assert "Real body." in out and "menu" not in out

def test_fetch_page_text_default_path_unchanged(monkeypatch):
    html = "<html><body><article>" + ("Hello world. " * 40) + "</article></body></html>"
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_polite_pause", lambda *a, **k: None)
    monkeypatch.setattr(feeds, "_fetch_page_bytes",
                        lambda url, **k: ("text/html", html.encode()))
    out = feeds.fetch_page_text("https://example.com/x", max_chars=6000)
    assert "Hello world." in out
```

- [ ] **Step 2: Run to verify the first test fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_feeds_render.py -v`
Expected: `test_extract_body_text_prefers_longest_article` FAILs (AttributeError: `_extract_body_text`); the default-path test may already pass.

- [ ] **Step 3: Extract the helpers, leaving `fetch_page_text` behavior identical**

```python
# src/discovery/feeds.py

def _extract_body_text(html_str: str, max_chars: int) -> str:
    """Block-clean the HTML, take the longest <article>/<main> slice (whole
    page if none is long enough), scrub to text, shift to a Ballotpedia
    Candidate Connection answer window when present, then truncate. Lifted from
    fetch_page_text so the plain and rendered tiers share one extraction."""
    cleaned = _BLOCK_RE.sub(" ", _COMMENT_RE.sub(" ", html_str))
    match = max(_ARTICLE_OR_MAIN_RE.finditer(cleaned),
                key=lambda m: len(m.group(0)), default=None)
    slice_ = match.group(0) if match and len(match.group(0)) >= 200 else cleaned
    full = _html_to_text(slice_)
    window = _candidate_connection_window(full)
    return (window if window is not None else full)[:max_chars]


def _page_text_from_bytes(url: str, max_chars: int) -> str:
    content_type, raw = _fetch_page_bytes(url)
    ctype = content_type.split(";")[0].strip().lower()
    if not ctype.startswith(_PAGE_TEXT_CONTENT_TYPES):
        return ""
    return _extract_body_text(raw.decode("utf-8", errors="replace"), max_chars)
```

Then replace the body of `fetch_page_text` (lines 482-498) so that after the robots + pause guard it simply does:

```python
    return _page_text_from_bytes(url, max_chars)
```

(Keep the `_robots_allowed`/`_polite_pause` lines and the docstring as they are.)

- [ ] **Step 4: Run the feeds test file AND the existing feeds suite**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_feeds_render.py tests/ -k feeds -v`
Expected: PASS — the refactor is behavior-preserving, so pre-existing feeds/page-text tests stay green.

- [ ] **Step 5: Commit**

```bash
git add src/discovery/feeds.py tests/test_feeds_render.py
git commit -m "refactor(feeds): extract _extract_body_text + _page_text_from_bytes (no behavior change)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Rendered fetch chain (plain → renderer), opt-in and injectable

**Files:**
- Modify: `src/discovery/feeds.py`
- Test: `tests/test_feeds_render.py`

**Interfaces:**
- Consumes: `_extract_body_text`, `_page_text_from_bytes` (Task 4).
- Produces: `fetch_page_text(url, max_chars=6000, *, sleep_fn=time.sleep, render_fallback=False, renderer=None) -> str`; `_render_page_text(url, max_chars, *, renderer=None) -> str`; module constant `_RENDER_MIN_CHARS = 200`. When `render_fallback` is True and the plain path raises or returns fewer than `_RENDER_MIN_CHARS` chars, the renderer is tried; its text is used only if longer than the plain result.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_feeds_render.py
from src.discovery import feeds

def _stub_common(monkeypatch):
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_polite_pause", lambda *a, **k: None)

_RENDERED = "<html><body><article>" + ("Rendered body sentence. " * 40) + "</article></body></html>"

def test_render_used_when_plain_raises(monkeypatch):
    _stub_common(monkeypatch)
    def boom(url, **k): raise ConnectionError("dropped")
    monkeypatch.setattr(feeds, "_fetch_page_bytes", boom)
    out = feeds.fetch_page_text("https://x/y", max_chars=6000,
                                render_fallback=True, renderer=lambda u: _RENDERED)
    assert "Rendered body sentence." in out

def test_render_used_when_plain_short(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(feeds, "_fetch_page_bytes",
                        lambda url, **k: ("text/html", b"<html><body>hi</body></html>"))
    out = feeds.fetch_page_text("https://x/y", max_chars=6000,
                                render_fallback=True, renderer=lambda u: _RENDERED)
    assert "Rendered body sentence." in out

def test_render_skipped_when_plain_good(monkeypatch):
    _stub_common(monkeypatch)
    good = "<html><body><article>" + ("Plain good body. " * 40) + "</article></body></html>"
    monkeypatch.setattr(feeds, "_fetch_page_bytes",
                        lambda url, **k: ("text/html", good.encode()))
    called = {"n": 0}
    def r(u): called["n"] += 1; return _RENDERED
    out = feeds.fetch_page_text("https://x/y", max_chars=6000,
                                render_fallback=True, renderer=r)
    assert "Plain good body." in out and called["n"] == 0

def test_render_off_preserves_raise(monkeypatch):
    _stub_common(monkeypatch)
    def boom(url, **k): raise ConnectionError("dropped")
    monkeypatch.setattr(feeds, "_fetch_page_bytes", boom)
    import pytest
    with pytest.raises(ConnectionError):
        feeds.fetch_page_text("https://x/y", max_chars=6000)  # render_fallback defaults False

def test_render_robots_denial_still_blocks(monkeypatch):
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: False)
    called = {"n": 0}
    def r(u): called["n"] += 1; return _RENDERED
    out = feeds.fetch_page_text("https://x/y", render_fallback=True, renderer=r)
    assert out == "" and called["n"] == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_feeds_render.py -v`
Expected: FAIL (fetch_page_text has no `render_fallback`/`renderer` params yet).

- [ ] **Step 3: Implement the chain**

```python
# src/discovery/feeds.py
_RENDER_MIN_CHARS = 200  # a page shorter than this is treated as empty/blocked


def _render_page_text(url: str, max_chars: int, *, renderer=None) -> str:
    """Render `url` via `renderer` (default: real Playwright) and run the shared
    body extraction. Returns '' on any renderer failure -- the caller then keeps
    the plain result."""
    render = renderer or _fetch_rendered
    try:
        html_str = render(url)
    except Exception:
        return ""
    return _extract_body_text(html_str, max_chars) if html_str else ""
```

Then rewrite `fetch_page_text` after the robots + pause guard:

```python
    if not render_fallback:
        return _page_text_from_bytes(url, max_chars)   # unchanged default (may raise)
    try:
        plain = _page_text_from_bytes(url, max_chars)
    except Exception:
        plain = ""
    if len(plain) >= _RENDER_MIN_CHARS:
        return plain
    rendered = _render_page_text(url, max_chars, renderer=renderer)
    return rendered if len(rendered) > len(plain) else plain
```

Update the signature to:
`def fetch_page_text(url: str, max_chars: int = 6000, *, sleep_fn=time.sleep, render_fallback: bool = False, renderer=None) -> str:`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_feeds_render.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/discovery/feeds.py tests/test_feeds_render.py
git commit -m "feat(feeds): opt-in rendered fetch fallback (plain -> renderer chain)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Real Playwright renderer + requirements + runner flag

**Files:**
- Modify: `src/discovery/feeds.py` (add `_fetch_rendered`)
- Modify: `requirements.txt` (add `playwright`)
- Modify: `scripts/evidence_slice.py` (`--render/--no-render` + wire into the fetcher + docstring)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_fetch_rendered(url: str, *, timeout_ms: int = 30000) -> str` (real headless Chromium; lazy-imports Playwright). The runner passes `render_fallback=args.render` into the fetcher partial.

Note: `_fetch_rendered` is a thin I/O adapter over a real browser; it is exercised by the human-gated live run (Task 7), not by a unit test (unit tests inject a fake `renderer`, per the Global Constraints).

- [ ] **Step 1: Add `_fetch_rendered` (lazy import)**

```python
# src/discovery/feeds.py
def _fetch_rendered(url: str, *, timeout_ms: int = 30000) -> str:
    """Headless-Chromium render for pages that block the plain fetch. Lazy-imports
    Playwright so it is only required when the rendered tier actually runs.
    Returns the fully rendered HTML (the shared _extract_body_text scrubs it)."""
    from playwright.sync_api import sync_playwright  # lazy: optional dependency
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return page.content()
        finally:
            browser.close()
```

- [ ] **Step 2: Add the dependency**

Add `playwright` to `requirements.txt` (find it: `ls requirements*.txt pyproject.toml 2>/dev/null`). Then in the main checkout:

```bash
~/Documents/GitHub/on-the-record/.venv/bin/pip install playwright
~/Documents/GitHub/on-the-record/.venv/bin/python -m playwright install chromium
```

- [ ] **Step 3: Wire the runner flag**

In `scripts/evidence_slice.py`, add to `build_parser`:

```python
    ap.add_argument("--render", dest="render", action="store_true", default=True,
                    help="Use the Playwright rendered-fetch fallback (default: on)")
    ap.add_argument("--no-render", dest="render", action="store_false",
                    help="Disable the rendered-fetch fallback")
```

And change the fetcher partial in `main`:

```python
    fetcher = functools.partial(fetch_page_text, max_chars=args.max_chars,
                                render_fallback=args.render)
```

Add to the module docstring: "Rendered fallback needs Playwright + a browser: `pip install playwright && python -m playwright install chromium` (pass `--no-render` to skip it)."

- [ ] **Step 4: Verify imports + the runner parses**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -c "import scripts.evidence_slice as s; s.build_parser().parse_args(['--no-render'])" && echo OK`
Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/ -q`
Expected: `OK`, and the full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/discovery/feeds.py scripts/evidence_slice.py requirements.txt
git commit -m "feat(feeds): real Playwright renderer + evidence-slice --render flag

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Human-gated validation — re-run Bass and confirm real evidence

**This task is not TDD; it spends LLM + network and needs Chris. Do NOT run it automatically — present the commands and results to Chris.**

**Files:** none changed (artifacts land in `docs/superpowers/spikes/2026-09-19-evidence-trust-core/` or a Bass subdir).

- [ ] **Step 1: Sanity-check the extractor on the Ballotpedia fixture**

Confirm the fix works before a full run:

```bash
export OPENROUTER_API_KEY=$(grep -E '^OPENROUTER_API_KEY=' ~/Documents/GitHub/on-the-record/.env.local | head -1 | cut -d= -f2- | tr -d '"'"'"'\r')
~/Documents/GitHub/on-the-record/.venv/bin/python - <<'PY'
from src.discovery.feeds import fetch_page_text
from src.evidence import extract
from src.llm_providers import get_provider
txt = fetch_page_text("https://ballotpedia.org/Karen_Bass", max_chars=200000)
items = extract.extract_quotes(txt, candidate_name="Karen Bass", provider=get_provider("haiku-or"))
print("ballotpedia items:", len(items))
for it in items[:6]: print("  -", it.text[:120])
PY
```

Expected: several items with Bass's own concrete-lever statements (was 0 before the fix).

- [ ] **Step 2: Full Bass run with rendering on**

```bash
~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py \
  --candidate 21c9e711-fb18-4afb-884f-08acd2b598ba \
  --env-file ~/Documents/GitHub/ev-accounts/backend/.env \
  --out docs/superpowers/spikes/2026-09-19-evidence-trust-core/bass
```

Read `bass/eval_report.md` and `bass/evidence_items.json`: report green/flagged/dropped, and how many sources the rendered tier recovered (e.g. `mayor.lacity.gov`).

- [ ] **Step 3: Commit the recovered evidence (dry-run first)**

```bash
~/Documents/GitHub/on-the-record/.venv/bin/python scripts/commit_evidence.py \
  docs/superpowers/spikes/2026-09-19-evidence-trust-core/bass/evidence_items.json \
  --env-file ~/Documents/GitHub/ev-accounts/backend/.env
# review the dry-run, then re-run with --commit
```

- [ ] **Step 4: Report to Chris**

Report: Bass before (0 usable) → after (N green / M flagged), the sites the renderer recovered, any sites still blocked, and confirm the review surface now shows two LA Mayor candidates. Chris reviews Bass's batch.

---

## Self-Review

**Spec coverage:**
- Whole-source extraction → Tasks 1 (windowing) + 2 (salvage) + 3 (per-window + dedup + drop 60K cap). ✓
- Rendered fetch chain → Tasks 4 (refactor) + 5 (chain) + 6 (real renderer + flag). ✓
- Opt-in / pluggable / default-off / lazy import → Task 5 (`render_fallback=False`) + Task 6 (lazy import). ✓
- Verbatim uses full text → unchanged; `pipeline.py:59` not touched; called out in Global Constraints. ✓
- Re-run Bass validation → Task 7 (human-gated). ✓
- Managed-API third tier deferred → not built; the single `_render_page_text` seam leaves room. ✓

**Placeholder scan:** No TBD/TODO; every code and test step is concrete. Window sizes are real defaults; Task 7 Step 1 validates them against the Ballotpedia fixture and the plan says to adjust if a window still overflows.

**Type consistency:** `chunk_text`, `parse_extract`, `_dedup`, `extract_quotes` signatures match across tasks; `extract_quotes` keeps the `pipeline.py:53` call contract; `fetch_page_text` new params are keyword-only with defaults, matching the `pipeline.py:43` / runner-partial call sites.

**Ambiguity:** the render trigger (raise OR `< _RENDER_MIN_CHARS`), the "use rendered only if longer" rule, and the default-off behavior are each pinned in code and tests.

## Execution Handoff

Two execution options:
1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute in this session with checkpoints.
