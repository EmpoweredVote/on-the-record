# Publish Wiring — Slice 2 (publish floor votes to meetings.votes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a meeting's CREC `floor_votes` into the existing `meetings.votes` table (with absolute video-time timestamps) so the already-live `GET /api/meetings/:id/votes` endpoint serves them.

**Architecture:** Extend `clip.absolutize_meeting_times` to shift `floor_votes` timestamps at the publish boundary (it already shifts segments/sections, and `publish_meeting` calls it first). Add `_replace_votes` to `publish.py` mirroring `_replace_segments` (delete-then-insert, no unique constraint needed). Map each `FloorVote` → a `meetings.votes` row. Per-member positions are deliberately NOT written to `meetings.vote_records` (federal floor voters aren't meeting speakers; the per-politician record already lives in `essentials.legislative_votes`).

**Tech Stack:** Python 3, `pytest`, `psycopg2`. `.venv/bin/pytest` / `.venv/bin/python`. Builds on merged #95–#98.

**Grounding (verified read-only against the DB, 2026-07-19):** `meetings.votes(id uuid PK, meeting_id uuid NOT NULL FK→meetings.meetings, resolution text NULL, description text NULL, result text **NOT NULL**, vote_type text NULL, timestamp **numeric** NULL, created_at timestamptz NULL)` — no unique constraint. `getVotesByMeetingId` SELECTs those columns `ORDER BY timestamp` and left-joins `vote_records`. The table is currently **empty**; `essentials.legislative_votes` already holds the per-politician federal record (don't duplicate it).

**Scope:** publish floor votes to `meetings.votes` only. OUT of scope: `vote_records` (per-member positions — not applicable to federal floor); capturing the official pass/fail outcome (we store the tally string in `result` — a follow-on); web click-to-seek UI; recording `clip_start_seconds` on captures; cross-linking to `essentials.legislative_votes`. **No production write happens in this slice** — the code adds the capability; Task 3 validates via a no-write dry run.

---

## File Structure

- Modify `src/clip.py` — extend `absolutize_meeting_times` to shift `floor_votes` timestamps.
- Modify `src/publish.py` — add `_replace_votes`, call it in `publish_meeting`.
- Modify `tests/test_clip.py` — floor-votes absolutize test.
- Modify `tests/test_publish.py` — `_replace_votes` row-building test.

---

## Task 1: Absolutize floor-vote timestamps at the publish boundary

**Files:**
- Modify: `src/clip.py`
- Test: `tests/test_clip.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_clip.py`:

```python
def test_absolutize_shifts_floor_votes():
    from src.clip import absolutize_meeting_times
    from src.models import Meeting, FloorVote
    m = Meeting(meeting_id="m", city=None, date="2019-07-11", clip_start_seconds=14600.0,
                floor_votes=[
                    FloorVote(438, "Q", 236, 193, 0, 9, 102.6, 0, True),
                    FloorVote(500, "Q2", 1, 1, 0, 0, None, None, False),  # unmatched
                ])
    out = absolutize_meeting_times(m)
    assert out.floor_votes[0].timestamp == 14702.6
    assert out.floor_votes[1].timestamp is None          # None stays None
    assert m.floor_votes[0].timestamp == 102.6           # input not mutated (deep copy)


def test_absolutize_no_offset_leaves_floor_votes():
    from src.clip import absolutize_meeting_times
    from src.models import Meeting, FloorVote
    m = Meeting(meeting_id="m", city=None, date="d",
                floor_votes=[FloorVote(438, "Q", 236, 193, 0, 9, 102.6, 0, True)])
    assert absolutize_meeting_times(m).floor_votes[0].timestamp == 102.6   # clip_start None -> unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_clip.py::test_absolutize_shifts_floor_votes -v`
Expected: FAIL — `floor_votes[0].timestamp` is still `102.6` (absolutize doesn't touch floor_votes yet).

- [ ] **Step 3: Extend `absolutize_meeting_times`**

In `src/clip.py`, inside `absolutize_meeting_times`, add a loop after the existing `if out.summary:` section-shifting block and before `return out`:

```python
    for fv in out.floor_votes:
        if fv.timestamp is not None:
            fv.timestamp += offset
```

(The function already deep-copies `meeting` into `out` and early-returns unchanged when `clip_start_seconds` is `None`/`0`, so the None-offset and non-mutation cases are already handled.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_clip.py -v`
Expected: PASS (existing clip tests + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/clip.py tests/test_clip.py
git commit -m "feat(crec): absolutize floor-vote timestamps at the publish boundary"
```

---

## Task 2: `_replace_votes` — write floor votes to meetings.votes

**Files:**
- Modify: `src/publish.py`
- Test: `tests/test_publish.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_publish.py`:

```python
def test_replace_votes_builds_rows(monkeypatch):
    from src import publish
    from src.models import Meeting, FloorVote

    captured = {}
    def fake_execute_values(cur, sql, rows):
        captured["sql"] = sql
        captured["rows"] = rows
    monkeypatch.setattr(publish.psycopg2.extras, "execute_values", fake_execute_values)

    class _Cur:
        def __init__(self):
            self.executes = []
        def execute(self, sql, params=None):
            self.executes.append((sql, params))

    m = Meeting(meeting_id="m", city=None, date="2019-07-11", floor_votes=[
        FloorVote(438, "On the Smith amendment", 236, 193, 0, 9, 102.6, 0, True),
        FloorVote(500, "On the Jones amendment", 300, 100, 0, 5, None, None, False),
    ])
    cur = _Cur()
    n = publish._replace_votes(cur, m, "uuid-1")

    assert n == 2
    assert any("DELETE FROM meetings.votes" in s for s, _ in cur.executes)
    assert captured["rows"][0] == (
        "uuid-1", "Roll No. 438", "On the Smith amendment", "Yea 236, Nay 193", "recorded", 102.6)
    assert captured["rows"][1][5] is None   # unmatched roll -> NULL timestamp


def test_replace_votes_empty_deletes_and_inserts_nothing(monkeypatch):
    from src import publish
    from src.models import Meeting

    called = {"execute_values": False}
    monkeypatch.setattr(publish.psycopg2.extras, "execute_values",
                        lambda *a, **k: called.__setitem__("execute_values", True))

    class _Cur:
        def __init__(self): self.executes = []
        def execute(self, sql, params=None): self.executes.append((sql, params))

    m = Meeting(meeting_id="m", city=None, date="d")   # no floor_votes
    cur = _Cur()
    assert publish._replace_votes(cur, m, "uuid-1") == 0
    assert any("DELETE FROM meetings.votes" in s for s, _ in cur.executes)  # still idempotent
    assert called["execute_values"] is False           # nothing to insert
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_publish.py::test_replace_votes_builds_rows -v`
Expected: FAIL — `AttributeError: module 'src.publish' has no attribute '_replace_votes'`.

- [ ] **Step 3: Add `_replace_votes` and call it**

In `src/publish.py`, add this function next to `_replace_segments`:

```python
def _replace_votes(cur, meeting: Meeting, meeting_uuid: str) -> int:
    """Delete then insert this meeting's recorded floor votes into meetings.votes.

    Federal floor votes carry only the vote event (roll, tally, timestamp) — the
    400+ voters are not meeting speakers and their per-member positions already
    live in essentials.legislative_votes, so meetings.vote_records is deliberately
    NOT populated here. On-the-record owns meetings.votes for meetings it publishes
    (delete-then-insert, mirroring _replace_segments). `result` is NOT NULL; we
    store the tally string (the official pass/fail outcome is a later follow-on).
    Timestamps are expected to already be source-absolute (absolutize_meeting_times).
    """
    cur.execute("DELETE FROM meetings.votes WHERE meeting_id = %s", (meeting_uuid,))
    rows = []
    for fv in meeting.floor_votes:
        rows.append((
            meeting_uuid,
            f"Roll No. {fv.roll_number}",        # resolution
            fv.question,                          # description
            f"Yea {fv.yea}, Nay {fv.nay}",        # result (NOT NULL)
            "recorded",                           # vote_type
            fv.timestamp,                         # numeric seconds (absolutized), NULL if unmatched
        ))
    if rows:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO meetings.votes
              (meeting_id, resolution, description, result, vote_type, timestamp)
            VALUES %s
            """,
            rows,
        )
    return len(rows)
```

In `publish_meeting`, call it right after the `_replace_topics(cur, meeting_uuid, meeting)` line:

```python
                vote_count = _replace_votes(cur, meeting, meeting_uuid)
                if vote_count:
                    print(f"  Published {vote_count} floor vote(s) to meetings.votes")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_publish.py -v`
Expected: PASS (existing publish tests + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/publish.py tests/test_publish.py
git commit -m "feat(crec): publish floor votes to meetings.votes (_replace_votes)"
```

---

## Task 3: No-write dry-run validation (controller runs; NO production write)

**Files:** none.

- [ ] **Step 1: Prove the full mapping end-to-end without touching the DB**

The captured meeting `2019-07-11-house-floor-ndaa` already has `floor_votes` (from publish-wiring Slice 1). Load it, set a demo `clip_start_seconds` (14600 — the ffmpeg `-ss` used to pull the clip), run `absolutize_meeting_times`, and build the exact `meetings.votes` rows via a recording cursor — **no connection, no write**:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from src.models import Meeting
from src.clip import absolutize_meeting_times
from src import publish

d = json.loads((Path.home()/"CouncilScribe/meetings/2019-07-11-house-floor-ndaa/transcript_named.json").read_text())
m = Meeting.from_dict(d)
m.clip_start_seconds = 14600.0                 # demo offset (real capture didn't record one — separate follow-on)
m = absolutize_meeting_times(m)

captured = {}
publish.psycopg2.extras.execute_values = lambda cur, sql, rows: captured.update(rows=rows)  # type: ignore
class _Cur:
    def execute(self, sql, params=None): pass
n = publish._replace_votes(_Cur(), m, "DEMO-UUID")
print(f"rows that WOULD be written to meetings.votes: {n}")
for r in [x for x in captured["rows"] if x[5] is not None][:5]:
    print(" ", r)   # (meeting_uuid, resolution, description, result, vote_type, absolute_ts_seconds)
PY
```
Expected: rows for all 21 votes; the 5 matched ones show `timestamp` = 14600 + clip-local (roll 438 ≈ 14702.6 s), `result` = "Yea 236, Nay 193" etc., `resolution` = "Roll No. 438". Confirms the mapping + absolute-time shift with zero DB writes. Record the output in the PR description.

- [ ] **Step 2: Note the remaining gates + follow-ons in the PR description (do not implement here)**
  - **First real integration test / prod write happens on the next actual federal meeting publish** — or, for a pre-prod check, publish this meeting to a **Supabase dev branch** and hit `GET /api/meetings/:id/votes` against the branch, then discard. Not done in this slice (no prod write).
  - Capture the official pass/fail outcome for `result` (parse the CREC "agreed to"/"rejected" / House timeline) instead of the tally string.
  - Record `clip_start_seconds` on captures via full-source `--clip` ingestion (House-CDN adapter) so published timestamps are automatically absolute.
  - web/ click-to-seek UI consuming `/api/meetings/:id/votes`.
  - Cross-link `meetings.votes` ↔ `essentials.legislative_votes` by roll-call # + session.

---

## Self-Review

**Spec coverage:** absolutize floor votes at publish (Task 1) ✓; write floor votes to `meetings.votes` with the confirmed schema/idempotency (Task 2) ✓; no-write validation (Task 3) ✓; `vote_records`, outcome string, web UI, clip-offset recording, cross-link all explicitly deferred ✓; **no production write in this slice** ✓.

**Placeholder scan:** none — real schema, exact column mapping, runnable code/commands.

**Type consistency:** `FloorVote` fields (`roll_number`, `question`, `yea`, `nay`, `timestamp`) used in `_replace_votes` (Task 2) and the absolutize loop (Task 1) match the model shipped in #98; `meetings.votes` columns (`meeting_id, resolution, description, result, vote_type, timestamp`) match the verified schema and the `getVotesByMeetingId` SELECT; `result` is always non-null (tally string); unmatched votes carry `NULL` timestamp, which the API's `ORDER BY timestamp` places last.

**Idempotency & safety:** `_replace_votes` deletes-then-inserts per `meeting_id` (mirrors `_replace_segments`), so re-publishing is safe; the table is currently empty and on-the-record is its only writer. The DELETE runs on every publish (incl. non-federal meetings with no `floor_votes`), which is a harmless no-op clear — consistent with `_replace_segments`.
