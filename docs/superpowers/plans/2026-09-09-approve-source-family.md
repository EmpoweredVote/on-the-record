# Approve a Source Family in One Click — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the Discovery review page, "Approve → quote source" approves the clicked row plus every other pending row from the same source across the whole queue, in one click.

**Architecture:** A pure `family_key(row)` picks a row's source identity by precedence (outlet id → channel id → channel name). The page render uses it to label each button with a sibling count; the quote-source route uses a DB action `approve_source_family(row)` that re-derives the key and bulk-updates matching pending rows to `approved`. `Approve → ingest` is untouched.

**Tech Stack:** Python 3, FastAPI, Jinja2 templates, psycopg2, pytest (`tests/test_gui_discovery.py`).

## Global Constraints

- Fan-out fires only from **Approve → quote source**. `Approve → ingest` stays a single-row action.
- Match precedence, one field only: `outlet_id`, else `channel_id`, else normalized `channel_name` (`(channel_name or "").strip().lower()`). A row with an id never matches by name. A row with no id and no name approves only itself.
- Fan-out scope is the whole pending queue, across every race. Only rows with `status = 'pending'` are changed.
- Route (`ingest` vs `quote_source`) is ignored when matching — identity only.
- Use `.venv/bin/python` for all Python/pytest invocations, never system `python3`.
- Run the full DB layer against a mocked `psycopg2` in unit tests (see the `_Conn`/`_Cur` pattern already in `tests/test_gui_discovery.py`). No test touches a real database.
- Each commit message ends with the trailer line:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## File Structure

- `gui/discovery.py` — add `family_key(row)` (pure) and `approve_source_family(row)` (DB action); add a `family_count` field to `DiscoveredRow`.
- `gui/app.py` — `discovery_page` populates `family_count` on pending rows; `discovery_quote_source` calls `approve_source_family` and reports the count.
- `gui/templates/discovery.html` — the quote-source button label gains a ` (+N more)` suffix.
- `tests/test_gui_discovery.py` — new tests for the key, the DB action, the button count, and the route; two existing quote-source route tests are updated to the new call.

**Before Task 1:** create the working branch off `main`:

```bash
git checkout -b feat/approve-source-family
```

---

### Task 1: `family_key` matching helper + `family_count` field

**Files:**
- Modify: `gui/discovery.py` (add `family_count` to the `DiscoveredRow` dataclass; add `family_key`)
- Test: `tests/test_gui_discovery.py`

**Interfaces:**
- Produces: `family_key(row: DiscoveredRow) -> tuple[str, str] | None` returning `("outlet", outlet_id)`, `("channel", channel_id)`, `("name", normalized_name)`, or `None`.
- Produces: `DiscoveredRow.family_count: int = 0` (new trailing dataclass field; default keeps `_to_row(*r)` positional construction valid because SQL does not select it).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_discovery.py`:

```python
# --- Approve source family: matching key ---

def test_family_key_prefers_outlet_id():
    r = _row(outlet_id="00000000-0000-0000-0000-000000000001",
             channel_id="UCk", channel_name="Wisconsin PBS")
    assert discovery.family_key(r) == ("outlet", "00000000-0000-0000-0000-000000000001")


def test_family_key_falls_back_to_channel_id():
    r = _row(outlet_id=None, channel_id="UCk", channel_name="Wisconsin PBS")
    assert discovery.family_key(r) == ("channel", "UCk")


def test_family_key_falls_back_to_normalized_name():
    r = _row(outlet_id=None, channel_id=None, channel_name="  Wisconsin PBS ")
    assert discovery.family_key(r) == ("name", "wisconsin pbs")


def test_family_key_none_when_no_identity():
    r = _row(outlet_id=None, channel_id=None, channel_name=None)
    assert discovery.family_key(r) is None


def test_family_key_none_when_name_blank():
    r = _row(outlet_id=None, channel_id=None, channel_name="   ")
    assert discovery.family_key(r) is None


def test_discovered_row_has_family_count_default_zero():
    assert _row().family_count == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k family_key -v`
Expected: FAIL — `AttributeError: module 'gui.discovery' has no attribute 'family_key'` (and the `family_count` test fails on an unexpected keyword / attribute).

- [ ] **Step 3: Add the dataclass field**

In `gui/discovery.py`, in the `DiscoveredRow` dataclass, immediately after the `race_label` field:

```python
    race_label: Optional[str] = None  # filled by the route via races.race_labels
    family_count: int = 0  # other pending rows sharing this row's source key (page render)
```

- [ ] **Step 4: Add the helper**

In `gui/discovery.py`, after `_to_row` (near the top-level helpers):

```python
def family_key(row: "DiscoveredRow") -> "tuple[str, str] | None":
    """A row's source identity, by precedence: registered outlet, else
    YouTube channel, else the channel name (trimmed + lowercased). Two rows
    are the same source when this returns the same pair. A row with none of
    the three has no family."""
    if row.outlet_id:
        return ("outlet", row.outlet_id)
    if row.channel_id:
        return ("channel", row.channel_id)
    name = (row.channel_name or "").strip().lower()
    if name:
        return ("name", name)
    return None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "family_key or family_count" -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add gui/discovery.py tests/test_gui_discovery.py
git commit -m "feat(discovery): add family_key matching + family_count field

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `approve_source_family` DB action

**Files:**
- Modify: `gui/discovery.py` (add `approve_source_family`)
- Test: `tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `family_key(row)` from Task 1.
- Produces: `approve_source_family(row: DiscoveredRow) -> int` — sets `status='approved'` on every `pending` row matching the clicked row's key; returns the number changed. Returns 0 on DB failure. A keyless row updates only itself (matched by `id`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_discovery.py`:

```python
# --- Approve source family: DB action ---

def _capture_conn(monkeypatch, rowcount=1):
    captured = {}
    class _Cur:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
        @property
        def rowcount(self):
            return rowcount
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): captured["committed"] = True
        def close(self): pass
    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect", lambda url: _Conn())
    return captured


def test_approve_family_by_outlet_id(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=6)
    r = _row(outlet_id="00000000-0000-0000-0000-000000000001")
    n = discovery.approve_source_family(r)
    assert n == 6
    assert captured["committed"] is True
    sql = captured["sql"].lower()
    assert "update essentials.discovered_sources" in sql
    assert "status = 'approved'" in sql
    assert "status = 'pending'" in sql
    assert "outlet_id = %s::uuid" in sql
    assert captured["params"] == ("00000000-0000-0000-0000-000000000001",)


def test_approve_family_by_channel_id(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=3)
    r = _row(outlet_id=None, channel_id="UCk")
    assert discovery.approve_source_family(r) == 3
    sql = captured["sql"].lower()
    assert "channel_id = %s" in sql
    assert captured["params"] == ("UCk",)


def test_approve_family_by_name(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=2)
    r = _row(outlet_id=None, channel_id=None, channel_name="Wisconsin PBS")
    assert discovery.approve_source_family(r) == 2
    sql = captured["sql"].lower()
    assert "lower(btrim(channel_name)) = %s" in sql
    assert captured["params"] == ("wisconsin pbs",)


def test_approve_family_keyless_updates_only_self(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=1)
    r = _row(id="d9", outlet_id=None, channel_id=None, channel_name=None)
    assert discovery.approve_source_family(r) == 1
    sql = captured["sql"].lower()
    assert "id = %s::uuid" in sql
    assert captured["params"] == ("d9",)


def test_approve_family_returns_zero_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery.approve_source_family(_row()) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k approve_family -v`
Expected: FAIL — `AttributeError: module 'gui.discovery' has no attribute 'approve_source_family'`.

- [ ] **Step 3: Write the implementation**

In `gui/discovery.py`, after `set_status_bulk`:

```python
def approve_source_family(row: "DiscoveredRow") -> int:
    """Approve, as a quote source, every pending row that shares this row's
    source key (see family_key). Whole-queue scope, all races. Only 'pending'
    rows are touched, so this can never un-ingest or re-approve. A keyless row
    approves only itself. Returns the number of rows changed, 0 on failure."""
    key = family_key(row)
    match = {
        "outlet": "outlet_id = %s::uuid",
        "channel": "channel_id = %s",
        "name": "lower(btrim(channel_name)) = %s",
    }
    if key is None:
        where, val = "id = %s::uuid", row.id
    else:
        where, val = match[key[0]], key[1]
    url = _db_url()
    if not url:
        return 0
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    update essentials.discovered_sources
                    set status = 'approved', status_reason = null, reviewed_at = now()
                    where status = 'pending' and {where}
                """, (val,))
                n = cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:
        return 0
```

Note: `where` is drawn only from the hardcoded `match` dict or the literal `id` clause — never from row data — so the f-string carries no injection surface; the value is bound as a parameter.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k approve_family -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add gui/discovery.py tests/test_gui_discovery.py
git commit -m "feat(discovery): approve_source_family bulk-approves matching pending rows

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Button shows the sibling count

**Files:**
- Modify: `gui/app.py` (`discovery_page`)
- Modify: `gui/templates/discovery.html` (quote-source button label)
- Test: `tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `family_key(row)` from Task 1; `DiscoveredRow.family_count` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_discovery.py`:

```python
# --- Approve source family: button count on the page ---

def test_quote_source_button_shows_sibling_count(monkeypatch):
    rows = [_row(id="a", channel_id="UCw", channel_name="Wisconsin PBS", race_id="r1"),
            _row(id="b", channel_id="UCw", channel_name="Wisconsin PBS", race_id="r2"),
            _row(id="c", channel_id="UCw", channel_name="Wisconsin PBS", race_id="r3")]
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": rows)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 3})
    client = TestClient(create_app())
    html = client.get("/discovery").text
    # three rows share a channel → each button offers "+2 more" (siblings across races)
    assert "(+2 more)" in html


def test_quote_source_button_plain_for_loner(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows",
                        lambda status="pending": [_row(id="a", channel_id="UCsolo")])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 1})
    client = TestClient(create_app())
    html = client.get("/discovery").text
    assert "Approve &rarr; quote source</button>" in html or \
           "Approve → quote source</button>" in html
    assert "more)" not in html


def test_quote_source_button_no_count_on_deferred_view(monkeypatch):
    rows = [_row(id="a", channel_id="UCw", status="deferred"),
            _row(id="b", channel_id="UCw", status="deferred")]
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": rows)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0})
    client = TestClient(create_app())
    html = client.get("/discovery?show=deferred").text
    assert "more)" not in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "sibling_count or plain_for_loner or no_count_on_deferred" -v`
Expected: FAIL — `(+2 more)` absent; the loner test may pass incidentally, the count tests fail.

- [ ] **Step 3: Populate `family_count` in `discovery_page`**

In `gui/app.py`, inside `discovery_page`, after the `groups` loop and before `h = discovery.health()`, add:

```python
        if status == "pending":
            from collections import defaultdict
            from gui.discovery import family_key
            fam: dict = defaultdict(list)
            for r in rows:
                k = family_key(r)
                if k is not None:
                    fam[k].append(r)
            for members in fam.values():
                for r in members:
                    r.family_count = len(members) - 1
```

(Deferred view leaves every `family_count` at its default 0.)

- [ ] **Step 4: Add the suffix in the template**

In `gui/templates/discovery.html`, change the quote-source button (currently:
`<button type="submit">Approve &rarr; quote source</button>`) to:

```html
        <button type="submit">Approve &rarr; quote source{% if r.family_count %} (+{{ r.family_count }} more){% endif %}</button>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "sibling_count or plain_for_loner or no_count_on_deferred" -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add gui/app.py gui/templates/discovery.html tests/test_gui_discovery.py
git commit -m "feat(discovery): label quote-source button with sibling count

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Route fans out and reports the count

**Files:**
- Modify: `gui/app.py` (`discovery_quote_source`)
- Test: `tests/test_gui_discovery.py` (add one test; update two existing tests)

**Interfaces:**
- Consumes: `approve_source_family(row)` from Task 2.

- [ ] **Step 1: Update the two existing quote-source route tests and add the fan-out test**

In `tests/test_gui_discovery.py`, replace `test_quote_source_route_marks_approved` (it currently monkeypatches `discovery.set_status`) with:

```python
def test_quote_source_route_approves_family_and_reports_count(monkeypatch):
    calls = {}
    monkeypatch.setattr(discovery, "get_row",
                        lambda rid: _row(channel_name="Wisconsin PBS"))
    monkeypatch.setattr(discovery, "approve_source_family",
                        lambda row: calls.setdefault("row", row) or 6)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/quote-source", follow_redirects=False)
    assert resp.status_code == 303
    assert calls["row"].id == "d1"
    assert "approved 6 (Wisconsin PBS)" in _flash(resp)
```

And replace `test_quote_source_blocks_non_pending_status` (it currently monkeypatches `set_status`) with:

```python
def test_quote_source_blocks_non_pending_status(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(status="rejected"))
    called = {"fanned": False}
    monkeypatch.setattr(discovery, "approve_source_family",
                        lambda row: called.update(fanned=True) or 1)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/quote-source", follow_redirects=False)
    assert resp.status_code == 303
    assert "already rejected" in _flash(resp)
    assert called["fanned"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "quote_source_route_approves_family or quote_source_blocks_non_pending" -v`
Expected: FAIL — the route still calls `set_status`, so `approve_source_family` is never invoked and the flash reads `approved as quote source`, not `approved 6 (Wisconsin PBS)`.

- [ ] **Step 3: Rewrite the route**

In `gui/app.py`, replace the body of `discovery_quote_source` (the lines from `ok = discovery.set_status(row_id, "approved")` through `return _discovery_redirect(flash)`) with:

```python
        n = discovery.approve_source_family(row)
        if n:
            flash = f"approved {n} ({row.channel_name or 'source'})"
        else:
            flash = "approved as quote source — SAVE FAILED, retry"
        return _discovery_redirect(flash)
```

Keep the existing guard above it unchanged (`if row is None: raise HTTPException(...)` and `if row.status != "pending": return _discovery_redirect(f"already {row.status}")`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "quote_source" -v`
Expected: PASS (route approve-family, non-pending block, and any other quote_source tests).

- [ ] **Step 5: Run the whole discovery test module**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: PASS (all tests, including the pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add gui/app.py tests/test_gui_discovery.py
git commit -m "feat(discovery): approve-quote-source fans out to the source family

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS with no new failures. If a pre-existing unrelated failure appears, note it but do not fix in this plan.

- [ ] **Step 2: Manual smoke (optional, if a GUI is running)**

Open `/discovery`, confirm a repeated outlet shows `Approve → quote source (+N more)`, click it, and confirm the flash reads `approved N (<outlet name>)` and the siblings leave the pending list.

---

## Self-Review

**Spec coverage:**
- Match precedence (outlet→channel→name), keyless single-row, name normalization → Task 1 (`family_key`) + Task 2 (`approve_source_family` keyless branch).
- Whole-queue, pending-only, route-ignored fan-out → Task 2 (`where status='pending' and <key>`, no race/route filter).
- Button pre-labels count, pending view only → Task 3.
- One-click approve + flash `approved N (name)`, `Approve → ingest` untouched, non-pending short-circuit kept → Task 4.
- No new undo → nothing added; bulk bar unchanged.

**Placeholder scan:** none — every step carries real code or an exact command.

**Type consistency:** `family_key` returns `tuple[str,str] | None` and is consumed identically in Task 2 (`key[0]`/`key[1]`) and Task 3 (grouping). `approve_source_family(row) -> int` is produced in Task 2 and consumed in Task 4. `family_count: int` set in Task 3, read in the template. Names match across tasks.
