# Reject a Source Family with One Checkbox — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the Discovery review page, a checkbox on the reject control rejects the clicked row plus every pending row from the same source, opt-in, without changing the default one-row reject.

**Architecture:** Extract the source-key WHERE logic shared with the merged approve feature into a pure `_family_where(row)` helper; refactor `approve_source_family` to use it with no behavior change; add `reject_source_family(row, reason)`. The reject route gains a `whole_source` form field; the template shows the checkbox only when the row has siblings.

**Tech Stack:** Python 3, FastAPI, Jinja2 templates, psycopg2, pytest (`tests/test_gui_discovery.py`). Run everything via `.venv/bin/python`.

## Global Constraints

- The whole-source reject is OPT-IN. Unchecked, reject behaves byte-for-byte as today (single-row `set_status(row_id, "rejected", reason=reason)`).
- Source match reuses `family_key(row)` precedence: `outlet_id`, else `channel_id`, else `(channel_name or "").strip().lower()`. Whole-queue scope, all races.
- Only rows with `status = 'pending'` are ever changed.
- The refactor must NOT change `approve_source_family`'s SQL (`set status = 'approved', status_reason = null`) or its parameter tuple (`(val,)`) — its existing unit tests must still pass unchanged.
- The reject value must be bound as a parameter; the WHERE clause fragment comes only from a hardcoded map or the literal `id = %s::uuid` — no injection surface.
- The checkbox count in the label is `family_count + 1` (the whole source, including the clicked row). It renders only when `family_count > 0`, which the page populates only on the pending view.
- Use `.venv/bin/python`, never system `python3`.
- Each commit message ends with the trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## File Structure

- `gui/discovery.py` — extract `_family_where`; refactor `approve_source_family`; add `reject_source_family`.
- `gui/app.py` — `discovery_reject` gains the `whole_source` branch.
- `gui/templates/discovery.html` — reject form gains the opt-in checkbox.
- `tests/test_gui_discovery.py` — new tests for the helper, the reject DB action, the route branch, and the checkbox render. Existing approve and reject tests are regression guards (unchanged).

This work happens on branch `feat/reject-source-family` (already created, spec already committed there).

---

### Task 1: Extract `_family_where` and refactor `approve_source_family`

**Files:**
- Modify: `gui/discovery.py` (add `_family_where`; refactor `approve_source_family` to use it)
- Test: `tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: existing `family_key(row) -> tuple[str,str] | None`.
- Produces: `_family_where(row: DiscoveredRow) -> tuple[str, str]` returning `(where_clause, value)` where `where_clause` is one of `outlet_id = %s::uuid` / `channel_id = %s` / `lower(btrim(channel_name)) = %s` / `id = %s::uuid`, and `value` is the matched id/name (or `row.id` for the keyless case).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_discovery.py`:

```python
# --- Reject source family: shared WHERE helper ---

def test_family_where_outlet():
    r = _row(outlet_id="00000000-0000-0000-0000-000000000001",
             channel_id="UCk", channel_name="Wisconsin PBS")
    assert discovery._family_where(r) == (
        "outlet_id = %s::uuid", "00000000-0000-0000-0000-000000000001")


def test_family_where_channel():
    r = _row(outlet_id=None, channel_id="UCk", channel_name="Wisconsin PBS")
    assert discovery._family_where(r) == ("channel_id = %s", "UCk")


def test_family_where_name():
    r = _row(outlet_id=None, channel_id=None, channel_name="  Wisconsin PBS ")
    assert discovery._family_where(r) == (
        "lower(btrim(channel_name)) = %s", "wisconsin pbs")


def test_family_where_keyless_uses_id():
    r = _row(id="d9", outlet_id=None, channel_id=None, channel_name=None)
    assert discovery._family_where(r) == ("id = %s::uuid", "d9")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k family_where -v`
Expected: FAIL — `AttributeError: module 'gui.discovery' has no attribute '_family_where'`.

- [ ] **Step 3: Add the helper**

In `gui/discovery.py`, immediately BEFORE the existing `approve_source_family` function, add:

```python
def _family_where(row: "DiscoveredRow") -> "tuple[str, str]":
    """The (where_clause, value) selecting a row's source family, by the same
    precedence as family_key. The clause is drawn only from the hardcoded match
    map or the literal id fallback — never from row data — so it carries no
    injection surface; the value is always bound as a parameter by callers."""
    key = family_key(row)
    match = {
        "outlet": "outlet_id = %s::uuid",
        "channel": "channel_id = %s",
        "name": "lower(btrim(channel_name)) = %s",
    }
    if key is None:
        return "id = %s::uuid", row.id
    return match[key[0]], key[1]
```

- [ ] **Step 4: Refactor `approve_source_family` to use it**

In `gui/discovery.py`, replace the key-derivation block at the top of `approve_source_family` — these lines:

```python
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
```

with:

```python
    where, val = _family_where(row)
    url = _db_url()
```

Leave the rest of `approve_source_family` (the docstring, the `try/connect`, the `update … set status = 'approved', status_reason = null …` SQL, `(val,)`, and the return/except) exactly as it is.

- [ ] **Step 5: Run the tests to verify they pass — including the approve regression guard**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "family_where or approve_family" -v`
Expected: PASS — the 4 new `_family_where` tests AND all 5 existing `approve_family` tests (the approve SQL/params are unchanged, so its tests still pass).

- [ ] **Step 6: Commit**

```bash
git add gui/discovery.py tests/test_gui_discovery.py
git commit -m "refactor(discovery): extract _family_where shared by family status actions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `reject_source_family` DB action

**Files:**
- Modify: `gui/discovery.py` (add `reject_source_family`)
- Test: `tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `_family_where(row)` from Task 1.
- Produces: `reject_source_family(row: DiscoveredRow, reason: str | None) -> int` — sets `status='rejected', status_reason=reason` on every `pending` row matching the row's key; returns rows changed; 0 on DB failure.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_discovery.py` (this reuses the `_capture_conn` helper already defined in the approve-family test section of this file):

```python
# --- Reject source family: DB action ---

def test_reject_family_by_outlet_sets_reason(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=4)
    r = _row(outlet_id="00000000-0000-0000-0000-000000000001")
    n = discovery.reject_source_family(r, "tier-5")
    assert n == 4
    assert captured["committed"] is True
    sql = captured["sql"].lower()
    assert "update essentials.discovered_sources" in sql
    assert "status = 'rejected'" in sql
    assert "status_reason = %s" in sql
    assert "status = 'pending'" in sql
    assert "outlet_id = %s::uuid" in sql
    assert captured["params"] == ("tier-5", "00000000-0000-0000-0000-000000000001")


def test_reject_family_by_name(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=2)
    r = _row(outlet_id=None, channel_id=None, channel_name="Wisconsin PBS")
    assert discovery.reject_source_family(r, "stale") == 2
    sql = captured["sql"].lower()
    assert "lower(btrim(channel_name)) = %s" in sql
    assert captured["params"] == ("stale", "wisconsin pbs")


def test_reject_family_keyless_updates_only_self(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=1)
    r = _row(id="d9", outlet_id=None, channel_id=None, channel_name=None)
    assert discovery.reject_source_family(r, "other") == 1
    sql = captured["sql"].lower()
    assert "id = %s::uuid" in sql
    assert captured["params"] == ("other", "d9")


def test_reject_family_returns_zero_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery.reject_source_family(_row(), "tier-5") == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k reject_family -v`
Expected: FAIL — `AttributeError: module 'gui.discovery' has no attribute 'reject_source_family'`.

- [ ] **Step 3: Write the implementation**

In `gui/discovery.py`, immediately AFTER `approve_source_family`, add:

```python
def reject_source_family(row: "DiscoveredRow", reason: "str | None") -> int:
    """Reject every pending row that shares this row's source key (family_key),
    all with one reason. Whole-queue scope, all races. Only 'pending' rows are
    touched. A keyless row rejects only itself. Returns rows changed, 0 on
    failure."""
    where, val = _family_where(row)
    url = _db_url()
    if not url:
        return 0
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    update essentials.discovered_sources
                    set status = 'rejected', status_reason = %s, reviewed_at = now()
                    where status = 'pending' and {where}
                """, (reason, val))
                n = cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:
        return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k reject_family -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add gui/discovery.py tests/test_gui_discovery.py
git commit -m "feat(discovery): reject_source_family bulk-rejects matching pending rows

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Reject route branch + checkbox

**Files:**
- Modify: `gui/app.py` (`discovery_reject`)
- Modify: `gui/templates/discovery.html` (reject form)
- Test: `tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `reject_source_family(row, reason)` from Task 2; `DiscoveredRow.family_count` (already populated on the pending view).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_discovery.py`:

```python
# --- Reject source family: route + checkbox ---

def test_reject_whole_source_fans_out_and_reports_count(monkeypatch):
    calls = {}
    monkeypatch.setattr(discovery, "get_row",
                        lambda rid: _row(channel_name="Wisconsin PBS"))
    monkeypatch.setattr(discovery, "reject_source_family",
                        lambda row, reason: calls.update(row=row, reason=reason) or 4)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject",
                       data={"reason": "tier-5", "whole_source": "1"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert calls["row"].id == "d1" and calls["reason"] == "tier-5"
    assert "rejected 4 (Wisconsin PBS)" in _flash(resp)


def test_reject_single_row_when_checkbox_absent(monkeypatch):
    calls = {"family": False}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: calls.update(
                            status=status, reason=reason) or True)
    monkeypatch.setattr(discovery, "reject_source_family",
                        lambda row, reason: calls.update(family=True) or 9)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject",
                       data={"reason": "clip-not-original"}, follow_redirects=False)
    assert resp.status_code == 303
    assert calls["family"] is False
    assert calls["status"] == "rejected" and calls["reason"] == "clip-not-original"
    assert "rejected" in _flash(resp)


def test_reject_whole_source_blocks_non_pending(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(status="approved"))
    called = {"family": False}
    monkeypatch.setattr(discovery, "reject_source_family",
                        lambda row, reason: called.update(family=True) or 1)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject",
                       data={"reason": "tier-5", "whole_source": "1"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "already approved" in _flash(resp)
    assert called["family"] is False


def test_reject_checkbox_shows_only_with_siblings(monkeypatch):
    rows = [_row(id="a", channel_id="UCw"), _row(id="b", channel_id="UCw"),
            _row(id="c", channel_id="UConly")]
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": rows)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 3})
    client = TestClient(create_app())
    html = client.get("/discovery").text
    assert 'name="whole_source"' in html          # the two UCw rows have a sibling
    assert "apply to all 2 from this source" in html


def test_reject_checkbox_absent_on_deferred_view(monkeypatch):
    rows = [_row(id="a", channel_id="UCw", status="deferred"),
            _row(id="b", channel_id="UCw", status="deferred")]
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": rows)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0})
    client = TestClient(create_app())
    html = client.get("/discovery?show=deferred").text
    assert 'name="whole_source"' not in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "reject_whole_source or reject_single_row or reject_checkbox" -v`
Expected: FAIL — the route ignores `whole_source` (no fan-out, wrong flash) and the template has no `whole_source` checkbox.

- [ ] **Step 3: Add the `whole_source` branch to the route**

In `gui/app.py`, change the `discovery_reject` signature and body. Replace:

```python
    def discovery_reject(row_id: str, reason: str = Form("other")):
        from gui import discovery
        row = discovery.get_row(row_id)
        if row is None:
            raise HTTPException(status_code=404)
        if row.status != "pending":
            return _discovery_redirect(f"already {row.status}")
        ok = discovery.set_status(row_id, "rejected", reason=reason)
        flash = "rejected"
        if not ok:
            flash += " — SAVE FAILED, retry"
        return _discovery_redirect(flash)
```

with:

```python
    def discovery_reject(row_id: str, reason: str = Form("other"),
                         whole_source: str = Form("")):
        from gui import discovery
        row = discovery.get_row(row_id)
        if row is None:
            raise HTTPException(status_code=404)
        if row.status != "pending":
            return _discovery_redirect(f"already {row.status}")
        if whole_source:
            n = discovery.reject_source_family(row, reason)
            if n:
                flash = f"rejected {n} ({row.channel_name or 'source'})"
            else:
                flash = "rejected — SAVE FAILED, retry"
            return _discovery_redirect(flash)
        ok = discovery.set_status(row_id, "rejected", reason=reason)
        flash = "rejected"
        if not ok:
            flash += " — SAVE FAILED, retry"
        return _discovery_redirect(flash)
```

- [ ] **Step 4: Add the checkbox to the template**

In `gui/templates/discovery.html`, inside the reject `<form method="post" action="/discovery/{{ r.id }}/reject">`, between the closing `</select>` of the reason dropdown and the `<button type="submit" class="delete-btn">Reject</button>`, insert:

```html
        {% if r.family_count %}<label><input type="checkbox" name="whole_source" value="1"> apply to all {{ r.family_count + 1 }} from this source</label>{% endif %}
```

Leave the reason `<select>` and the Reject button unchanged.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "reject_whole_source or reject_single_row or reject_checkbox" -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the whole discovery module (regression: existing reject tests)**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: PASS — including the pre-existing `test_reject_requires_and_records_reason` and `test_reject_blocks_non_pending_status`, which exercise the unchanged single-row path.

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/discovery.html tests/test_gui_discovery.py
git commit -m "feat(discovery): opt-in checkbox to reject a whole source family

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS with no new failures (pre-existing DB-gated skips in `test_gui_politicians.py` are fine). If an unrelated pre-existing failure appears, note it but do not fix it here.

- [ ] **Step 2: Manual smoke (optional, if a GUI is running)**

Open `/discovery`, find a repeated outlet, tick "apply to all N from this source" on its reject control, pick a reason, and confirm the flash reads `rejected N (<outlet name>)` and all its pending rows leave the list. Confirm a loner row shows no checkbox.

---

## Self-Review

**Spec coverage:**
- Checkbox opt-in, count `family_count + 1`, pending-view/siblings-only render → Task 3 (template + render tests).
- Unchecked = unchanged single-row reject → Task 3 (route else-branch; regression tests).
- `family_key` precedence + whole-queue + pending-only → Task 1 (`_family_where`) + Task 2 (`reject_source_family` WHERE).
- Approve SQL/params/tests unchanged → Task 1 Step 4 (only key-derivation extracted) + Step 5 (approve regression run).
- Injection-safe (bound value, hardcoded clause) → Task 1 helper + Task 2 params `(reason, val)`.
- One reason across the family → Task 2 (single `reason` param) + Task 3 route.

**Placeholder scan:** none — every step carries real code or an exact command.

**Type consistency:** `_family_where(row) -> tuple[str,str]` produced in Task 1, consumed in Task 2. `reject_source_family(row, reason) -> int` produced in Task 2, consumed in Task 3. `family_count` read in the template matches the existing dataclass field. `whole_source` form field name matches between route (`Form("")`) and template (`name="whole_source"`). Flash strings match between route and route tests (`rejected {n} ({name})`).
