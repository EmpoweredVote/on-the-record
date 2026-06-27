# Multi-Race Events — E1 (Pipeline) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `publish` derive a meeting's races from its linked candidates and reconcile them into the `meetings.event_races` join table on every publish, replacing the single-race `meetings.meetings.race_id` write — so multi-race forums publish correctly.

**Architecture:** A new `resolve_races_for_politicians` returns *all* distinct races for a meeting's linked politicians; `publish_meeting` reconciles `meetings.event_races` (delete + insert) after speakers are upserted, within the existing transaction, and blocks debate/forum meetings that resolve to zero races. The old single-race resolver, the `race_id` column write, and the bulk-relink debate-only special-case are removed.

**Tech Stack:** Python 3, pytest, psycopg2. Run with the repo venv: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`. Work on branch `claude/multi-race-events` (already created off `main`; the E spec is committed there).

**Spec:** `docs/superpowers/specs/2026-06-27-multi-race-events-design.md`

**Deploy dependency (do NOT skip):** This code writes to `meetings.event_races`, which the **ev-accounts E2 migration creates first** (sequencing step 1). E1 code/tests are safe to write and run locally (tests mock the DB), but a real prod `--publish-meeting` will fail until that table exists. Don't run a live publish against prod until E2's create+backfill migration is applied.

---

## File Structure

- **Modify `src/publish.py`** — replace `resolve_race_id_for_politicians` with `resolve_races_for_politicians` (plural); add `_reconcile_event_races`; call it in `publish_meeting` after `_upsert_speakers`; stop writing `meetings.meetings.race_id` in `_upsert_meeting` and pass `None` for race to the validator.
- **Modify `src/event_entities.py`** — drop the "`debate`/`forum` require `race_id`" rule (races are now derived + validated in publish).
- **Modify `run_local.py`** — remove `_resolve_debate_race_id` and the `event_kind == "debate"` block in `_bulk_relink_apply` (publish now owns race derivation).
- **Modify `tests/test_bulk_relink.py`** — replace the `resolve_race_id_for_politicians` tests with `resolve_races_for_politicians` tests.
- **Modify `tests/test_event_entities.py`** — update the rule assertions for the dropped race requirement.
- **Modify `tests/test_bulk_relink_apply.py`** — update the two debate tests that referenced `_resolve_debate_race_id`.
- **Create `tests/test_publish_event_races.py`** — unit tests for `_reconcile_event_races` (delete+insert, multi-race union, zero-race debate raises).

---

## Task 1: Replace the single-race resolver with `resolve_races_for_politicians`

**Files:**
- Modify: `src/publish.py:140-165` (the `resolve_race_id_for_politicians` function)
- Test: `tests/test_bulk_relink.py:267-313` (the `resolve_race*` block)

Context: the current function (added in C, fixed for the uuid cast in PR #30) returns a single race or `None` with a `LIMIT 2` "exactly one or give up" gate. The new model needs **all** distinct races. The existing `_FakeCursor` test helper (`tests/test_bulk_relink.py:270`) stores `executed`/`params` and returns canned `fetchall()` rows.

- [ ] **Step 1: Replace the tests** — In `tests/test_bulk_relink.py`, replace the entire block from `from src.publish import resolve_race_id_for_politicians` (line ~267) through the last `test_resolve_race_*` function (the `test_resolve_race_none_when_empty_politician_list`, line ~313) with:

```python
from src.publish import resolve_races_for_politicians


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


def test_resolve_races_returns_all_distinct():
    cur = _FakeCursor([("race-1",), ("race-2",)])
    assert resolve_races_for_politicians(cur, ["pol-a", "pol-b"]) == ["race-1", "race-2"]


def test_resolve_races_single():
    cur = _FakeCursor([("race-1",)])
    assert resolve_races_for_politicians(cur, ["pol-a"]) == ["race-1"]


def test_resolve_races_empty_when_no_rows():
    cur = _FakeCursor([])
    assert resolve_races_for_politicians(cur, ["pol-a"]) == []


def test_resolve_races_empty_politician_list_skips_query():
    cur = _FakeCursor([("race-1",)])
    assert resolve_races_for_politicians(cur, []) == []
    assert cur.executed is None


def test_resolve_races_casts_param_to_uuid_array():
    # Regression: essentials.race_candidates.politician_id is uuid; psycopg2 sends
    # a list as text[], so the query MUST cast (ANY(%s::uuid[])).
    cur = _FakeCursor([("race-1",)])
    resolve_races_for_politicians(cur, ["11111111-1111-1111-1111-111111111111"])
    assert "::uuid[]" in cur.executed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -k resolve_races -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_races_for_politicians'`

- [ ] **Step 3: Replace the implementation** — In `src/publish.py`, replace the whole `resolve_race_id_for_politicians` function (lines ~140-165) with:

```python
def resolve_races_for_politicians(cur, politician_ids) -> list[str]:
    """All distinct essentials races the given linked politicians belong to.

    A meeting's races are the union of its linked candidates' races. Returns
    every distinct race_id (no "exactly one" gate) so multi-race forums are
    represented; [] when there are no ids or no race_candidates rows. Casts to
    uuid[] because essentials.race_candidates.politician_id is a uuid column and
    psycopg2 sends a Python list as text[].
    """
    ids = [pid for pid in (politician_ids or []) if pid]
    if not ids:
        return []
    cur.execute(
        """
        SELECT DISTINCT race_id
        FROM essentials.race_candidates
        WHERE politician_id = ANY(%s::uuid[])
        """,
        (ids,),
    )
    return [str(r[0]) for r in cur.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink.py -k resolve_races -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/publish.py tests/test_bulk_relink.py
git commit -m "feat(multi-race): resolve_races_for_politicians returns all distinct races"
```

---

## Task 2: Drop the `race_id`-required rule from `validate_event_entities`

**Files:**
- Modify: `src/event_entities.py:31-32`
- Test: `tests/test_event_entities.py:43-54`

Context: races are now derived and validated in publish (Task 3), so the validator must no longer block debate/forum for a missing `race_id`. Keep UUID validation, the chamber requirement, and the mutual-exclusion (it still guards `other`/misc kinds).

- [ ] **Step 1: Update the test** — In `tests/test_event_entities.py`, replace `test_entity_validation_rules` (lines ~43-54) with:

```python
def test_entity_validation_rules():
    assert validate_event_entities("council", CHAMBER_ID, None) is None
    assert validate_event_entities("debate", None, RACE_ID) is None
    # debate/forum no longer require a race_id here — races are derived and
    # validated at publish time (>=1 derived race).
    assert validate_event_entities("debate", None, None) is None
    assert validate_event_entities("forum", None, None) is None
    assert "chamber_id is required" in validate_event_entities("council", None, None)
    assert "cannot both be set" in validate_event_entities("other", CHAMBER_ID, RACE_ID)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_event_entities.py::test_entity_validation_rules -v`
Expected: FAIL — `validate_event_entities("debate", None, None)` currently returns the "race_id is required" string, not `None`.

- [ ] **Step 3: Remove the rule** — In `src/event_entities.py`, delete these two lines (the debate/forum race requirement, lines ~31-32):

```python
    if event_kind in ("debate", "forum") and race_id is None:
        return f"race_id is required for event_kind {event_kind}"
```

Leave the rest of `validate_event_entities` unchanged (UUID validation, the mutual-exclusion `if`, and the council/school_board chamber requirement all stay).

- [ ] **Step 4: Run the full event_entities suite**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_event_entities.py -v`
Expected: PASS (all tests; the `test_entity_validation_rejects_bad_uuid` test still passes because UUID validation is retained).

- [ ] **Step 5: Commit**

```bash
git add src/event_entities.py tests/test_event_entities.py
git commit -m "feat(multi-race): drop debate/forum race_id requirement (now derived at publish)"
```

---

## Task 3: Reconcile `meetings.event_races` in publish; stop writing the `race_id` column

**Files:**
- Modify: `src/publish.py` — add `_reconcile_event_races`; call it in `publish_meeting` (after `_upsert_speakers`, ~line 529); edit `_upsert_meeting` (validate call ~171, UPDATE ~211, INSERT ~244) to stop writing `race_id`.
- Test: `tests/test_publish_event_races.py` (new)

Context: `publish_meeting` (`src/publish.py:516`) runs everything in one transaction: `_upsert_meeting` → `_upsert_event_orgs` → `_upsert_local_people` → `_upsert_speakers` → segments → topics. The meeting's linked politician ids are on the in-memory mappings (`meeting.speakers[label].politician_id`). The `meetings.meetings.race_id` column still exists until E2 drops it, but we stop writing it.

- [ ] **Step 1: Write the failing unit tests** — Create `tests/test_publish_event_races.py`:

```python
from __future__ import annotations

import pytest

from src.models import Meeting, SpeakerMapping
from src.publish import _reconcile_event_races


class _RecordingCursor:
    """Captures execute() calls and serves canned fetchall() rows in order."""

    def __init__(self, fetch_results):
        self._fetch = list(fetch_results)
        self.calls = []  # list of (sql, params)

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self._fetch.pop(0)


def _meeting(kind, *names):
    speakers = {
        f"S{i}": SpeakerMapping(speaker_label=f"S{i}", speaker_name=n,
                                politician_id=f"pol-{i}")
        for i, n in enumerate(names)
    }
    return Meeting(meeting_id="m1", city="X", date="2026-04-01",
                   event_kind=kind, speakers=speakers)


def test_reconcile_writes_union_for_multi_race_forum():
    # resolve query returns two races; reconcile deletes then inserts both.
    cur = _RecordingCursor([[("race-clerk",), ("race-pros",)]])
    _reconcile_event_races(cur, _meeting("forum", "A", "B"), "muid-1")
    sqls = [c[0] for c in cur.calls]
    assert any("DELETE FROM meetings.event_races" in s for s in sqls)
    inserts = [c for c in cur.calls if "INSERT INTO meetings.event_races" in c[0]]
    inserted_races = {c[1][1] for c in inserts}
    assert inserted_races == {"race-clerk", "race-pros"}
    # every insert is for this meeting uuid
    assert all(c[1][0] == "muid-1" for c in inserts)


def test_reconcile_single_race_debate():
    cur = _RecordingCursor([[("race-gov",)]])
    _reconcile_event_races(cur, _meeting("debate", "A"), "muid-1")
    inserts = [c for c in cur.calls if "INSERT INTO meetings.event_races" in c[0]]
    assert {c[1][1] for c in inserts} == {"race-gov"}


def test_reconcile_zero_races_debate_raises():
    cur = _RecordingCursor([[]])  # no races resolved
    with pytest.raises(RuntimeError, match="no race"):
        _reconcile_event_races(cur, _meeting("debate", "A"), "muid-1")


def test_reconcile_zero_races_council_ok():
    # council has no linked candidates / no races and that's fine (no raise);
    # it still clears any stale rows.
    cur = _RecordingCursor([[]])
    m = Meeting(meeting_id="m1", city="X", date="2026-04-01", event_kind="council",
                speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name="Mayor",
                                               politician_id="pol-0")})
    _reconcile_event_races(cur, m, "muid-1")  # must not raise
    assert any("DELETE FROM meetings.event_races" in c[0] for c in cur.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_publish_event_races.py -v`
Expected: FAIL with `ImportError: cannot import name '_reconcile_event_races'`

- [ ] **Step 3: Add `_reconcile_event_races`** — In `src/publish.py`, add this function directly after `resolve_races_for_politicians` (from Task 1):

```python
def _reconcile_event_races(cur, meeting: Meeting, meeting_uuid: str) -> list[str]:
    """Derive the meeting's races from its linked candidates and reconcile the
    meetings.event_races join table (delete this meeting's rows, insert the
    current set). Returns the race ids written.

    debate/forum require >=1 derived race: an empty set raises (aborting the
    publish transaction) — recoverable by linking candidates, then re-publishing.
    council/school_board legitimately have no races; an empty set just clears
    stale rows.
    """
    pol_ids = [m.politician_id for m in meeting.speakers.values() if m.politician_id]
    races = resolve_races_for_politicians(cur, pol_ids)

    if not races and meeting.event_kind in ("debate", "forum"):
        raise RuntimeError(
            f"{meeting.meeting_id}: {meeting.event_kind} resolved to no race — "
            "no linked candidate maps to an essentials race yet. Link candidates, "
            "then re-publish."
        )

    cur.execute("DELETE FROM meetings.event_races WHERE meeting_id = %s", (meeting_uuid,))
    for race_id in races:
        cur.execute(
            "INSERT INTO meetings.event_races (meeting_id, race_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (meeting_uuid, race_id),
        )
    return races
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_publish_event_races.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Call it in `publish_meeting`** — In `src/publish.py`, in `publish_meeting` (the transaction body, ~line 529), add the reconcile call immediately after the `_upsert_speakers` line:

```python
                label_to_uuid = _upsert_speakers(cur, meeting, meeting_uuid)
                _reconcile_event_races(cur, meeting, meeting_uuid)
```

- [ ] **Step 6: Stop writing the `race_id` column** — In `src/publish.py` `_upsert_meeting`:

(a) The validator call (~line 171) — pass `None` for race so the (now race-agnostic) validator only enforces chamber rules:

```python
    entity_error = validate_event_entities(
        meeting.event_kind,
        chamber_id,
        None,
    )
```

(b) The UPDATE statement (~lines 200-237) — remove the `race_id = %s,` line from the SET list, and remove the `meeting.race_id,` value from the params tuple (the value currently between `chamber_id,` and `source if is_url else None,`).

(c) The INSERT statement (~lines 239-260) — remove `race_id,` from the column list, remove its `%s` placeholder from the VALUES list, and remove the `meeting.race_id,` value from the params tuple. Keep `chamber_id` in all three.

(Leave `meetings.meetings.race_id` in the DB; it is dropped later by E2. We simply stop writing it.)

- [ ] **Step 7: Verify the publish module imports and the unit tests still pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -c "import src.publish" && /Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_publish_event_races.py tests/test_publish.py -v`
Expected: import OK; tests pass. If `tests/test_publish.py` has an assertion about `race_id` being written, update it to reflect that publish no longer writes the column (read the failure, adjust the expectation to match the new behavior — do not re-add the write).

- [ ] **Step 8: Commit**

```bash
git add src/publish.py tests/test_publish_event_races.py tests/test_publish.py
git commit -m "feat(multi-race): reconcile meetings.event_races on publish; stop writing race_id column"
```

---

## Task 4: Remove the bulk-relink debate-only race special-case

**Files:**
- Modify: `run_local.py` — delete `_resolve_debate_race_id` (~line 1936) and the `event_kind == "debate"` block inside `_bulk_relink_apply` (~lines 2052-2065).
- Test: `tests/test_bulk_relink_apply.py:153-210` (the two debate tests).

Context: publish now derives races for all race-bearing events, so the apply orchestrator no longer needs to resolve/set `race_id` before publishing. The publish boundary is mocked in these tests (`_publish_meeting_standalone`), so a debate meeting in apply now just publishes like any other.

- [ ] **Step 1: Update the debate tests** — In `tests/test_bulk_relink_apply.py`, replace the two tests `test_apply_resolves_debate_race_id_before_publish` and `test_apply_debate_blocked_when_race_id_unresolvable` (lines ~153-210) with a single test that confirms a debate meeting publishes through apply without any race special-casing:

```python
def test_apply_publishes_debate_without_race_special_casing(tmp_path, monkeypatch):
    # Race derivation now lives in publish (mocked here), so apply treats a
    # debate like any other meeting: relink -> fold -> publish.
    meetings_root = tmp_path / "meetings"
    debate = Meeting(meeting_id="m1", city="X", date="2026-04-01", event_kind="debate",
                     speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name="Steve Hilton")})
    _write_meeting(meetings_root / "m1", debate)
    monkeypatch.setattr(run_local.config, "MEETINGS_DIR", meetings_root)
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [{"politician_id": _UUID, "politician_slug": None,
                                          "full_name": "Steve Hilton"}])
    monkeypatch.setattr("src.enroll.load_profiles", lambda: ProfileDB(profiles={}))
    monkeypatch.setattr("src.enroll.save_profiles", lambda db: None)
    published = []
    monkeypatch.setattr(run_local, "_publish_meeting_standalone",
                        lambda mid, anyway=False: published.append(mid))

    review_file = tmp_path / "review.yaml"
    review_file.write_text(yaml.safe_dump(
        {"speakers": [{"name": "Steve Hilton", "decision": "link", "politician_id": _UUID}]}))

    run_local._bulk_relink_apply(_args(review_file, publish_anyway=True))

    assert published == ["m1"]


def test_resolve_debate_race_id_helper_is_gone():
    # The debate-only race resolver was removed; publish now owns derivation.
    assert not hasattr(run_local, "_resolve_debate_race_id")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink_apply.py -k "debate or helper_is_gone" -v`
Expected: FAIL — `test_resolve_debate_race_id_helper_is_gone` fails because `_resolve_debate_race_id` still exists.

- [ ] **Step 3: Delete `_resolve_debate_race_id`** — In `run_local.py`, delete the entire `_resolve_debate_race_id` function (~lines 1936-1954, from `def _resolve_debate_race_id(meeting) -> str | None:` through its closing `conn.close()`).

- [ ] **Step 4: Remove the debate block in `_bulk_relink_apply`** — In `run_local.py`, in the publish loop of `_bulk_relink_apply` (~lines 2049-2071), delete the debate race-resolution block so the loop body becomes just the gate check + publish:

```python
    for mdir in sorted(to_publish, key=lambda p: p.name):
        meeting = to_publish[mdir]
        state = PipelineState(mdir)
        if not _may_publish(state.review_status, args.publish_anyway):
            print(f"  skip publish {mdir.name}: gate verdict '{state.review_status}' "
                  f"(re-run with --publish-anyway)")
            blocked.append(mdir.name)
            continue
        _publish_meeting_standalone(mdir.name, args.publish_anyway)
```

(Remove the `if meeting.event_kind == "debate" and not meeting.race_id:` … `continue` block entirely. The `meeting = to_publish[mdir]` line may now be unused; keep it only if the surrounding code references `meeting`, otherwise delete it to avoid an unused local.)

- [ ] **Step 5: Run the apply tests**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_bulk_relink_apply.py -v`
Expected: PASS (all tests; the two old debate tests are replaced).

- [ ] **Step 6: Commit**

```bash
git add run_local.py tests/test_bulk_relink_apply.py
git commit -m "feat(multi-race): drop bulk-relink debate-only race special-case (publish owns it)"
```

---

## Task 5: Full regression

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q`
Expected: all tests pass. If any pre-existing test referenced `resolve_race_id_for_politicians`, `_resolve_debate_race_id`, or the `race_id` column write and now fails, update it to the new behavior (these were enumerated in Tasks 1, 3, 4) — do not reintroduce removed code.

- [ ] **Step 2: Commit any test fixups**

```bash
git add -u
git commit -m "test(multi-race): align remaining tests with derived-race publish"
```

(Skip if Step 1 was already green.)

---

## Self-Review Notes (reconciled against the spec)

- **Spec coverage:** `resolve_races_for_politicians` returning all races (Task 1) · validation no longer requires race_id (Task 2) · derive + reconcile `event_races` on publish, stop writing the column, block zero-race debate/forum (Task 3) · bulk-relink debate special-case removed (Task 4) · regression (Task 5). The schema migration + column DROP and all read-side work are **E2 (ev-accounts)**, out of this plan. `Meeting.race_id` is intentionally left in the model (vestigial).
- **Type/name consistency:** `resolve_races_for_politicians(cur, politician_ids) -> list[str]` and `_reconcile_event_races(cur, meeting, meeting_uuid) -> list[str]` are used consistently across tasks; the table/columns `meetings.event_races(meeting_id, race_id)` match the E2 migration contract in the spec.
- **Deploy ordering:** E1 must not run a live prod publish until the E2 create+backfill migration exists (noted in the header).
