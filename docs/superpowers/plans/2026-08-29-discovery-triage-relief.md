# Discovery Triage Relief — Need-Based Defer + Bulk Actions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the discovery triage queue by auto-deferring low-value tier-3 items whose candidates already have a stronger source, and give a person bulk reject/restore controls — so human review focuses on the high-value head and builds the per-outlet approval history the later auto-ingest phase (mode C) needs.

**Architecture:** A new `deferred` status hides an item from the default triage queue without deleting it (the zero-source alarm still counts it, so a starved race is unaffected). A set-based SQL sweep moves qualifying items to `deferred`; it runs at the end of each poll and as a standalone script to clear the current backlog while the poll is paused. The triage GUI gets a deferred-view toggle and a bulk reject/restore bar built with the HTML5 `form=` attribute (no nested forms).

**Tech Stack:** Python 3, psycopg2, FastAPI + Jinja2 (the `gui/` app), pytest with fakes/`TestClient`. Database is PostgreSQL (`essentials` schema on Supabase). Migration is SQL applied ad hoc.

## Global Constraints

- **This plan spans two repos.** Task 1 is a migration in **ev-accounts** (`essentials` schema is owned there). Tasks 2–7 are in **on-the-record**. Every path below is prefixed with its repo.
- **Base the on-the-record branch on `origin/main` *after* PR #186 (the connection-drop fix) merges.** Both that PR and this plan modify `scripts/poll_discovery.py`; branching after the merge avoids a conflict. Branch name: `feat/discovery-triage-relief`.
- **on-the-record tests run with** `.venv/bin/python -m pytest <path> -q` from the repo root.
- **ev-accounts migration rules (from its CLAUDE.md):** author is **Chris Andrews → `CA_` namespace**. `git fetch origin` first, then take the next free `CA_` slot (as of 2026-08-29 the max is `CA_0028`, so expect **`CA_0029`** — verify at execution time). Zero-pad to four digits. Migration must be **idempotent** and end with a `DO $$ ... $$` **post-verify gate** that `RAISE EXCEPTION`s on failure. **Dry-run against prod** by wrapping the body `BEGIN; ... ROLLBACK;` and confirm it reverted. Verify the number with `npm run check:migrations --prefix backend`.
- **Do NOT add bulk *approve-ingest*.** Ingestion spends real compute and produces voter-facing quotes; auto/bulk ingest belongs to the later mode-C phase, not here. Phase-1 bulk actions are **reject** and **restore** only (both cheap status changes).
- The defer rule was validated against prod on 2026-08-29: it moves **970 of 2,353** pending rows. Preserve that as the expected magnitude when dry-running.

---

## File Structure

**ev-accounts:**
- Create: `backend/migrations/CA_0029_discovered_sources_deferred_status.sql` — extends the `status` CHECK to admit `'deferred'`.

**on-the-record:**
- Modify: `src/discovery/db.py` — add `apply_tier3_defer(cur) -> int`.
- Create: `scripts/defer_low_value.py` — standalone runner for the defer sweep (clears the backlog while the poll is paused; re-runnable).
- Modify: `scripts/poll_discovery.py` — call `apply_tier3_defer` in `main()`'s finalize block so the sweep self-maintains on each run.
- Modify: `gui/discovery.py` — parametrize `pending_rows(status=...)`; add `set_status_bulk(...)`.
- Modify: `gui/app.py` — deferred-view query param on `GET /discovery`; new `POST /discovery/bulk` route.
- Modify: `gui/templates/discovery.html` — row checkboxes, a bulk action bar, and a pending/deferred toggle.
- Modify tests: `tests/test_discovery_db.py`, `tests/test_poll_discovery.py`, `tests/test_gui_discovery.py`.
- Create test: `tests/test_defer_low_value.py`.

---

## Task 1: Migration — add the `deferred` status (ev-accounts)

**Files:**
- Create: `ev-accounts/backend/migrations/CA_0029_discovered_sources_deferred_status.sql`

**Interfaces:**
- Produces: the value `'deferred'` becomes writable to `essentials.discovered_sources.status`. Every later task depends on this.

- [ ] **Step 1: Fetch and confirm the migration slot**

```bash
cd ~/Documents/GitHub/ev-accounts
git fetch origin
git checkout -b migration/discovered-sources-deferred origin/master
ls backend/migrations | grep -E '^CA_' | sort | tail -3   # confirm CA_0028 is the max; use CA_0029
```

- [ ] **Step 2: Write the migration file**

Create `backend/migrations/CA_0029_discovered_sources_deferred_status.sql`:

```sql
-- CA_0029_discovered_sources_deferred_status.sql
-- Adds the 'deferred' status to essentials.discovered_sources. 'deferred' hides a
-- low-value discovered item from the human triage queue WITHOUT deleting it: the
-- zero-source alarm (essentials.discovered_sources status IN ('approved','ingested'))
-- is unaffected, so a starved race still alarms and a person can restore a deferred
-- item. Idempotent; safe to re-run.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'essentials.discovered_sources'::regclass
      AND conname  = 'discovered_sources_status_check'
      AND pg_get_constraintdef(oid) LIKE '%deferred%'
  ) THEN
    ALTER TABLE essentials.discovered_sources
      DROP CONSTRAINT IF EXISTS discovered_sources_status_check;
    ALTER TABLE essentials.discovered_sources
      ADD CONSTRAINT discovered_sources_status_check
      CHECK (status = ANY (ARRAY[
        'pending'::text, 'auto_filtered'::text, 'approved'::text,
        'rejected'::text, 'ingested'::text, 'superseded'::text,
        'deferred'::text]));
  END IF;
END $$;

-- post-verify gate: the constraint must now admit 'deferred'
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'essentials.discovered_sources'::regclass
      AND conname  = 'discovered_sources_status_check'
      AND pg_get_constraintdef(oid) LIKE '%deferred%'
  ) THEN
    RAISE EXCEPTION 'CA_0029 post-verify failed: deferred not admitted by discovered_sources_status_check';
  END IF;
END $$;
```

- [ ] **Step 3: Verify the migration number**

Run: `npm run check:migrations --prefix backend`
Expected: PASS (no duplicate `CA_29`).

- [ ] **Step 4: Dry-run against prod, then confirm rollback reverted**

Wrap the body in a transaction and roll back (use the repo's prod psql connection — the session pooler `DATABASE_URL`):

```bash
# BEGIN; <paste the two DO $$ blocks>; 
#   then check: SELECT pg_get_constraintdef(oid) FROM pg_constraint
#     WHERE conname='discovered_sources_status_check';   -- should show 'deferred'
# ROLLBACK;
# re-run the SELECT after ROLLBACK — 'deferred' must be GONE again.
```
Expected: inside the transaction the constraint lists `deferred`; after `ROLLBACK` it does not.

- [ ] **Step 5: Apply for real, then verify**

Apply the file's body (no wrapping transaction) against prod. Then:

```sql
SELECT pg_get_constraintdef(oid) FROM pg_constraint
WHERE conname = 'discovered_sources_status_check';
```
Expected: the definition includes `'deferred'`.

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/CA_0029_discovered_sources_deferred_status.sql
git commit -m "feat(discovery): add 'deferred' status to discovered_sources (CA_0029)"
```

---

## Task 2: `apply_tier3_defer` — the defer sweep (on-the-record)

**Files:**
- Modify: `on-the-record/src/discovery/db.py`
- Test: `on-the-record/tests/test_discovery_db.py`

**Interfaces:**
- Consumes: an open psycopg2 cursor `cur`; the `'deferred'` status from Task 1.
- Produces: `apply_tier3_defer(cur) -> int` — runs one set-based UPDATE and returns `cur.rowcount` (rows moved to `deferred`). The caller owns commit.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_discovery_db.py`:

```python
class _RowcountCursor:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def test_apply_tier3_defer_targets_low_value_search_found_and_returns_count():
    cur = _RowcountCursor(rowcount=970)
    n = db.apply_tier3_defer(cur)
    assert n == 970
    sql = cur.executed[0][0]
    assert "update essentials.discovered_sources" in sql.lower()
    assert "'deferred'" in sql
    assert "status = 'pending'" in sql.lower()          # only touches the queue
    assert "source_tier_guess >= 3" in sql.lower()      # tier-3 tail
    assert "outlet_id is null" in sql.lower()            # search-found only; watchlisted kept
    assert "not exists" in sql.lower()                   # every-candidate-has-a-better-source guard
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_db.py::test_apply_tier3_defer_targets_low_value_search_found_and_returns_count -q`
Expected: FAIL with `AttributeError: module 'src.discovery.db' has no attribute 'apply_tier3_defer'`.

- [ ] **Step 3: Write the implementation**

Add to `src/discovery/db.py`:

```python
def apply_tier3_defer(cur) -> int:
    """Move low-value tier-3+ items OUT of the human queue into 'deferred'.

    An item defers only when ALL hold: it is still pending; its tier is 3 or
    worse; it was search-found (outlet_id IS NULL — watchlisted/trusted shows
    are always kept); and EVERY candidate it names already has a stronger,
    non-deferred tier-1/2 source. A candidate whose only speech is this item is
    never deferred. 'deferred' is not counted by the zero-source alarm, so a
    starved race still surfaces and a person can restore the item. Returns the
    number of rows moved. Caller commits."""
    cur.execute("""
        update essentials.discovered_sources d
        set status = 'deferred',
            status_reason = 'auto-deferred: every matched candidate has a stronger (tier 1-2) source'
        where d.status = 'pending'
          and d.source_tier_guess >= 3
          and d.outlet_id is null
          and cardinality(d.matched_politician_ids) > 0
          and not exists (
            select 1 from unnest(d.matched_politician_ids) as pid
            where not exists (
              select 1 from essentials.discovered_sources b
              where b.source_tier_guess in (1, 2)
                and b.status in ('pending', 'approved', 'ingested')
                and pid = any(b.matched_politician_ids)))
    """)
    return cur.rowcount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_discovery_db.py::test_apply_tier3_defer_targets_low_value_search_found_and_returns_count -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/discovery/db.py tests/test_discovery_db.py
git commit -m "feat(discovery): add apply_tier3_defer sweep (need-based tier-3 defer)"
```

---

## Task 3: Standalone defer runner + poll wiring (on-the-record)

**Files:**
- Create: `on-the-record/scripts/defer_low_value.py`
- Modify: `on-the-record/scripts/poll_discovery.py` (finalize block in `main()`)
- Test: `on-the-record/tests/test_defer_low_value.py`, `on-the-record/tests/test_poll_discovery.py`

**Interfaces:**
- Consumes: `db.connect()`, `db.apply_tier3_defer(cur)` (Task 2).
- Produces: `python scripts/defer_low_value.py` prints `DEFERRED <n> low-value items` and commits. `poll_discovery.main()` calls the sweep in its finalize block.

- [ ] **Step 1: Write the failing test for the standalone runner**

Create `tests/test_defer_low_value.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import defer_low_value  # noqa: E402


class _FakeConn:
    def __init__(self, log):
        self._log = log
    def cursor(self):
        return object()
    def commit(self):
        self._log.append("commit")
    def close(self):
        self._log.append("close")


def test_defer_low_value_runs_sweep_commits_and_reports(monkeypatch, capsys):
    log = []
    monkeypatch.setattr(defer_low_value.db, "connect", lambda: _FakeConn(log))
    monkeypatch.setattr(defer_low_value.db, "apply_tier3_defer", lambda cur: 970)
    rc = defer_low_value.main()
    assert rc == 0
    assert "DEFERRED 970" in capsys.readouterr().out
    assert log == ["commit", "close"]   # commit before close
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_defer_low_value.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'defer_low_value'`.

- [ ] **Step 3: Write the standalone runner**

Create `scripts/defer_low_value.py`:

```python
"""Run the tier-3 defer sweep once and report how many items moved.

Use while the discovery poll is paused to clear the existing backlog, or any
time to re-file low-value items. Idempotent (only touches status='pending').

  .venv/bin/python scripts/defer_low_value.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src.discovery import db  # noqa: E402


def main() -> int:
    conn = db.connect()
    try:
        cur = conn.cursor()
        n = db.apply_tier3_defer(cur)
        conn.commit()
        print(f"DEFERRED {n} low-value items")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_defer_low_value.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing test for poll wiring**

Add to `tests/test_poll_discovery.py`:

```python
def test_defer_sweep_runs_after_finish_run(monkeypatch):
    log = []
    _patch_common(monkeypatch, log)   # existing helper in this file
    monkeypatch.setattr(poll_discovery.engine, "run_discovery",
                        lambda *a, **kw: _stats())   # existing stats helper
    monkeypatch.setattr(poll_discovery.db, "apply_tier3_defer",
                        lambda cur: log.append("defer") or 0)
    rc = poll_discovery.main()
    assert "finish_run" in log and "defer" in log
    assert log.index("defer") > log.index("finish_run")   # defer runs in finalize, after the record
```

Note: reuse whatever stats factory the neighbouring tests use (read the top of `tests/test_poll_discovery.py`; several tests build a `RunStats`-like object). If they build stats inline, inline the same here instead of `_stats()`.

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_poll_discovery.py::test_defer_sweep_runs_after_finish_run -q`
Expected: FAIL (no `defer` in log — the sweep is not wired yet).

- [ ] **Step 7: Wire the sweep into `poll_discovery.main()`**

In `scripts/poll_discovery.py`, in the finalize block of `main()`, immediately AFTER the `record_alarms` commit and BEFORE `if stats.failures:`, add:

```python
        # Defer low-value tier-3 items whose candidates already have a stronger
        # source, so they leave the human queue (reversible; alarm-safe).
        if not args.dry_run:
            cur = conn.cursor()
            deferred = db.apply_tier3_defer(cur)
            conn.commit()
            print(f"DEFERRED {deferred} low-value items")
```

- [ ] **Step 8: Run both tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_defer_low_value.py tests/test_poll_discovery.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add scripts/defer_low_value.py scripts/poll_discovery.py tests/test_defer_low_value.py tests/test_poll_discovery.py
git commit -m "feat(discovery): run defer sweep standalone and at end of each poll"
```

---

## Task 4: Deferred-view toggle in the triage GUI (on-the-record)

**Files:**
- Modify: `on-the-record/gui/discovery.py` (`pending_rows`)
- Modify: `on-the-record/gui/app.py` (`GET /discovery`)
- Modify: `on-the-record/gui/templates/discovery.html` (toggle link)
- Test: `on-the-record/tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `'deferred'` status.
- Produces: `discovery.pending_rows(status: str = "pending")`; `GET /discovery?show=deferred` lists deferred rows; template renders a pending⇄deferred toggle.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui_discovery.py`:

```python
def test_discovery_page_deferred_view_lists_deferred(monkeypatch):
    seen = {}
    def fake_rows(status="pending"):
        seen["status"] = status
        return [_row(status=status)]
    monkeypatch.setattr(discovery, "pending_rows", fake_rows)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 1})
    client = TestClient(create_app())
    resp = client.get("/discovery?show=deferred")
    assert resp.status_code == 200
    assert seen["status"] == "deferred"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py::test_discovery_page_deferred_view_lists_deferred -q`
Expected: FAIL — `pending_rows()` takes no argument and the route ignores `show`.

- [ ] **Step 3: Parametrize `pending_rows`**

In `gui/discovery.py`, replace the `_PENDING_ORDER` constant and `pending_rows` with a status-parametrized version:

```python
_LIST_WHERE_ORDER = """
    where d.status = %s
    order by e.election_date asc nulls last,
             d.source_tier_guess asc nulls last,
             d.confidence desc nulls last, d.created_at desc
"""


def pending_rows(status: str = "pending") -> list:
    url = _db_url()
    if not url:
        return []
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(_SELECT + _LIST_WHERE_ORDER, (status,))
                return [_to_row(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []
```

(Delete the now-unused `_PENDING_ORDER`.)

- [ ] **Step 4: Read the `show` param in the route**

In `gui/app.py`, change the `discovery_page` signature and the rows fetch:

```python
    @app.get("/discovery", response_class=HTMLResponse)
    def discovery_page(request: Request, flash: str = "", show: str = "pending") -> HTMLResponse:
        from gui import discovery, races
        status = "deferred" if show == "deferred" else "pending"
        rows = discovery.pending_rows(status)
```

Then add `"show": status,` to the template context dict passed to `discovery.html`.

- [ ] **Step 5: Add the toggle to the template**

In `gui/templates/discovery.html`, near the page header, add:

```html
<p class="view-toggle">
  {% if show == 'deferred' %}
    <a href="/discovery">← Back to pending</a> · showing <strong>deferred</strong> (low-value, auto-filed)
  {% else %}
    <a href="/discovery?show=deferred">View deferred items →</a>
  {% endif %}
</p>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -q`
Expected: PASS (the new test and all existing ones — `pending_rows()` still defaults to pending).

- [ ] **Step 7: Commit**

```bash
git add gui/discovery.py gui/app.py gui/templates/discovery.html tests/test_gui_discovery.py
git commit -m "feat(discovery): add deferred-items view toggle to triage page"
```

---

## Task 5: `set_status_bulk` — bulk status change (on-the-record)

**Files:**
- Modify: `on-the-record/gui/discovery.py`
- Test: `on-the-record/tests/test_gui_discovery.py`

**Interfaces:**
- Produces: `discovery.set_status_bulk(row_ids: list[str], status: str, reason: "str | None" = None) -> int` — updates only rows currently in `('pending','deferred')`, returns affected count. Never clobbers approved/ingested/rejected.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui_discovery.py`:

```python
def test_set_status_bulk_updates_only_pending_or_deferred(monkeypatch):
    captured = {}
    class _Cur:
        rowcount = 2
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): captured["committed"] = True
        def close(self): pass
    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect", lambda url: _Conn())

    n = discovery.set_status_bulk(["a", "b"], "rejected", reason="tier-3")
    assert n == 2
    assert captured["committed"] is True
    sql = captured["sql"].lower()
    assert "update essentials.discovered_sources" in sql
    assert "id = any(%s::uuid[])" in sql
    assert "status = any(array['pending','deferred'])" in sql
    assert discovery.set_status_bulk([], "rejected", reason="x") == 0   # empty is a no-op
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py::test_set_status_bulk_updates_only_pending_or_deferred -q`
Expected: FAIL — `set_status_bulk` does not exist.

- [ ] **Step 3: Write the implementation**

Add to `gui/discovery.py` (next to `set_status`):

```python
def set_status_bulk(row_ids: "list[str]", status: str, reason: "str | None" = None) -> int:
    """Set status on many rows at once. Only rows currently pending or deferred
    are touched, so a bulk action can never un-ingest or un-approve. Returns the
    number of rows changed. Empty id list is a no-op."""
    if not row_ids:
        return 0
    url = _db_url()
    if not url:
        return 0
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    update essentials.discovered_sources
                    set status = %s, status_reason = %s, reviewed_at = now()
                    where id = any(%s::uuid[])
                      and status = any(array['pending','deferred'])
                """, (status, reason, row_ids))
                n = cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py::test_set_status_bulk_updates_only_pending_or_deferred -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/discovery.py tests/test_gui_discovery.py
git commit -m "feat(discovery): add set_status_bulk (pending/deferred only)"
```

---

## Task 6: `POST /discovery/bulk` route (on-the-record)

**Files:**
- Modify: `on-the-record/gui/app.py`
- Test: `on-the-record/tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `discovery.set_status_bulk` (Task 5); `_discovery_redirect` (existing).
- Produces: `POST /discovery/bulk` with form fields `action` (`reject`|`restore`), repeated `row_ids`, and `reason` (default `other`). Redirects with a `?flash=` summary.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui_discovery.py`:

```python
def test_bulk_reject_calls_set_status_bulk_with_reason(monkeypatch):
    calls = []
    monkeypatch.setattr(discovery, "set_status_bulk",
                        lambda ids, status, reason=None: calls.append((ids, status, reason)) or len(ids))
    client = TestClient(create_app())
    resp = client.post("/discovery/bulk",
                       data={"action": "reject", "row_ids": ["a", "b"], "reason": "tier-5"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert calls == [(["a", "b"], "rejected", "tier-5")]
    assert "rejected 2" in _flash(resp)


def test_bulk_restore_sets_pending(monkeypatch):
    calls = []
    monkeypatch.setattr(discovery, "set_status_bulk",
                        lambda ids, status, reason=None: calls.append((ids, status, reason)) or len(ids))
    client = TestClient(create_app())
    resp = client.post("/discovery/bulk",
                       data={"action": "restore", "row_ids": ["a"]},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert calls == [(["a"], "pending", None)]
    assert "restored 1" in _flash(resp)


def test_bulk_no_rows_is_a_noop(monkeypatch):
    monkeypatch.setattr(discovery, "set_status_bulk",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    client = TestClient(create_app())
    resp = client.post("/discovery/bulk", data={"action": "reject"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "no rows selected" in _flash(resp)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k bulk -q`
Expected: FAIL — `/discovery/bulk` returns 404/405.

- [ ] **Step 3: Add the route**

In `gui/app.py`, alongside the other `/discovery/...` POST routes, add (note `Form` and `List` — import `from typing import List` at the top if not present, or use `list[str]`):

```python
    @app.post("/discovery/bulk")
    def discovery_bulk(action: str = Form(...),
                       row_ids: list[str] = Form(default=[]),
                       reason: str = Form("other")):
        from gui import discovery
        if not row_ids:
            return _discovery_redirect("no rows selected")
        if action == "reject":
            n = discovery.set_status_bulk(row_ids, "rejected", reason=reason)
            return _discovery_redirect(f"rejected {n}")
        if action == "restore":
            n = discovery.set_status_bulk(row_ids, "pending", reason=None)
            return _discovery_redirect(f"restored {n} to pending")
        return _discovery_redirect(f"unknown action: {action}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k bulk -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_discovery.py
git commit -m "feat(discovery): add POST /discovery/bulk (reject/restore selected)"
```

---

## Task 7: Checkboxes + bulk action bar in the template (on-the-record)

**Files:**
- Modify: `on-the-record/gui/templates/discovery.html`
- Test: `on-the-record/tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `POST /discovery/bulk` (Task 6); the `show` context var (Task 4).
- Produces: the triage page renders a per-row checkbox (`name="row_ids"`, associated to the bulk form via `form="bulkform"`) and a bulk bar posting to `/discovery/bulk`. In the deferred view the bar offers **Restore**; in the pending view it offers **Reject**.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui_discovery.py`:

```python
def test_triage_page_has_checkboxes_and_bulk_bar(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [_row(id="d1")])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 1})
    client = TestClient(create_app())
    html = client.get("/discovery").text
    assert 'action="/discovery/bulk"' in html
    assert 'name="row_ids"' in html
    assert 'value="d1"' in html
    assert 'form="bulkform"' in html          # checkbox associated to the bulk form, not nested
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py::test_triage_page_has_checkboxes_and_bulk_bar -q`
Expected: FAIL — the markup is not there yet.

- [ ] **Step 3: Add the bulk form and checkboxes**

In `gui/templates/discovery.html`:

(a) Add the bulk bar once, near the top of the item list (HTML5 `form` attribute lets the row checkboxes below belong to it without nesting inside the per-row action forms):

```html
<form id="bulkform" method="post" action="/discovery/bulk" class="bulk-bar">
  {% if show == 'deferred' %}
    <button type="submit" name="action" value="restore">Restore selected to pending</button>
  {% else %}
    <label>Reason
      <select name="reason">
        <option value="tier-5">tier-5 (low value)</option>
        <option value="wrong-person">wrong-person</option>
        <option value="clip-not-original">clip-not-original</option>
        <option value="other" selected>other</option>
      </select>
    </label>
    <button type="submit" name="action" value="reject">Reject selected</button>
  {% endif %}
</form>
```

(b) Inside each rendered item row (wherever a row's `id` is available as e.g. `item.id`), add a checkbox associated to the bulk form:

```html
<input type="checkbox" name="row_ids" value="{{ item.id }}" form="bulkform">
```

Match the existing loop variable name in this template (the row object rendered per item). If the template iterates `for item in items` within `for label, items in groups`, use `item.id`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py::test_triage_page_has_checkboxes_and_bulk_bar -q`
Expected: PASS.

- [ ] **Step 5: Run the full discovery test set**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py tests/test_discovery_db.py tests/test_discovery_engine.py tests/test_poll_discovery.py tests/test_defer_low_value.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add gui/templates/discovery.html tests/test_gui_discovery.py
git commit -m "feat(discovery): row checkboxes + bulk reject/restore bar in triage UI"
```

---

## Deployment / backfill (after all tasks land)

1. **Apply CA_0029** to prod (Task 1) — this must land before any `deferred` write.
2. **Clear the current backlog while the poll is paused:** run `.venv/bin/python scripts/defer_low_value.py` from the primary on-the-record checkout. Expected: `DEFERRED ~970 low-value items`. Re-runnable and idempotent.
3. When the discovery poll is later re-enabled (see the OTR discovery memory for the `launchctl enable`/`bootstrap` commands), the sweep self-maintains at the end of each run — no separate cron.

---

## Later phases (NOT planned here — separate plans)

- **Phase 2 — mode-C auto-ingest (the actual "automate ingestion" goal).** Wire the already-tracked per-outlet bar (≥10 reviewed, ≥90% approved, 0 identity rejects — `gui/discovery.py:270`) to auto-run `approve-ingest` for a qualified outlet's new items. Requires the review history that Phase 1's bulk actions let a person build. Guard with the eval harness before trusting a threshold.
- **Phase 3 — narrow cold-start auto-ingest.** Optionally auto-ingest the safest slice (watchlisted + tier-1 debate/forum + `original` + high confidence) with early human spot-checks, to get automation before outlets accrue 10-review history.

---

## Self-Review

- **Spec coverage:** need-based defer (Tasks 1–3) ✓; watchlisted/trusted kept — `outlet_id IS NULL` guard in Task 2 ✓; only-source podcasts kept — the every-candidate `NOT EXISTS` guard ✓; deferred is alarm-safe and viewable (Task 4) and reversible (Tasks 5–6 restore) ✓; bulk reject (Tasks 5–7) ✓; no LLM partisanship gate — none introduced ✓; auto-ingest deliberately excluded — Global Constraints + Later phases ✓.
- **Placeholder scan:** no TBD/TODO; every code step carries real code. The one soft spot is the loop-variable name in Tasks 3 (stats factory) and 7 (`item` vs the template's actual name) — each is flagged inline with how to resolve by reading the neighbouring code.
- **Type consistency:** `apply_tier3_defer(cur) -> int` used identically in Tasks 2/3; `set_status_bulk(row_ids, status, reason) -> int` used identically in Tasks 5/6; `pending_rows(status="pending")` used in Tasks 4/7; `show` context var defined in Task 4, consumed in Task 7; `'deferred'` status defined in Task 1, used in Tasks 2/4/5.
