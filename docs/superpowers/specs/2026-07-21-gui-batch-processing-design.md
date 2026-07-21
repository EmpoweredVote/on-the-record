# GUI Batch Processing — Design

**Date:** 2026-07-21
**Status:** Approved (pending spec review)
**Area:** `gui/` — the local FastAPI processing GUI (`python -m gui`)

## Problem

Meetings are processed one at a time. To run several, the operator opens multiple browser tabs and starts each `/new` run by hand, then juggles those tabs to watch progress. There's no cap, so it's easy to exceed Modal's ~10 concurrent limit, and no single place to see everything in flight. The library sorts by the meeting's clip/air date, so a clip you just processed can sort far down the list.

## Goals

1. Configure meetings via the existing kind-aware `/new` form and **start them immediately into a capped parallel pool** — keep adding while earlier ones run, no tab-juggling.
2. A **background scheduler** drains an overflow queue so you can add more than the cap and walk away; it keeps advancing even with the browser closed.
3. Fold the "what's in flight" view **into the library** (no separate page): sort by most-recent processing activity, add a Processed column, live-update running rows, and show pending items + counts + the concurrency cap.

## Non-Goals

- No change to the per-meeting pipeline (`run_local.py` stages) or to any meeting's processing.
- **No auto-publish.** Batch runs are local-only (no `--publish`), exactly as single GUI runs are today — publishing stays the manual Publish tab.
- No parallelism *within* a meeting; concurrency is across meetings.
- No DB schema change; no `web/` change.
- Manual re-runs (Continue / Re-run a stage from a meeting's Progress tab) remain immediate and are **not** pooled (see Known limitations).

## Approach

A new `gui/batch.py` owns a small persisted batch state (pending queue + cap) and a scheduler. **All new-meeting launches route through it** (`/new` → `batch.launch_or_enqueue`), so the cap governs both a single add and a burst of adds — one code path. The library becomes the live view by sorting on processing recency, polling a batch-status endpoint for in-flight rows, and rendering a pending strip + counts header. The per-meeting launch mechanics (`runner.launch_run`, the `gui_run.json` pid sidecar, `run_status`) are reused verbatim.

---

## Section 1 — Concurrency model & scheduler (`gui/batch.py`)

**Batch state** — a lock-guarded JSON at `config.MEETINGS_DIR / "_batch.json"`:
```json
{ "max_concurrent": 8,
  "seq": 42,
  "pending": [ { "pending_id": 41, "params": { RunParams fields... } }, ... ],
  "active": [ "2026-07-16-...-interview", ... ] }
```
- `max_concurrent` — default 8, adjustable (1–10) from the library.
- `pending` — not-yet-launched items; each has a stable `pending_id` (from the monotonic `seq` counter) plus the full serialized `RunParams` (the meeting id/dir is minted at real launch time via `runner._unique_meeting_id`, so two queued items can't collide). Removal targets `pending_id`, not a list index, so it's race-free when the scheduler promotes items concurrently.
- `active` — meeting_ids the pool has launched and believes are still running. Kept small (only in-flight), so counting is cheap and doesn't scan the whole archive.

**`launch_or_enqueue(params) -> ("started", meeting_id) | ("pending", None)`** (under lock): prune `active` (drop ids whose `runner.run_status` says not-running); if `len(active) < max_concurrent`, launch now via `runner.launch_run`, append the meeting_id to `active`, persist, return `("started", id)`; otherwise append `params` to `pending`, persist, return `("pending", None)`.

**Scheduler** — `_tick()` (pure, testable): prune `active`; while `len(active) < max_concurrent` and `pending`, pop the oldest pending, launch it, append to `active`; persist. A launch that raises is logged and the item is dropped (**skip-and-continue**) so one bad source never blocks the pool. `start_scheduler()` runs a daemon thread: `while True: _tick(); sleep(4)`, wrapped so a tick exception is logged and never kills the thread. `create_app` starts exactly one scheduler (guarded against double-start under uvicorn reload).

**Restart-safe.** Running subprocesses survive a GUI reload (recovered via their pid sidecar — `run_status` already does this). `active` + `pending` are persisted, so the fresh scheduler thread prunes finished runs and resumes draining. No auto-publish anywhere.

**`status() -> dict`** for the library poll:
```json
{ "counts": {"running": 6, "pending": 2, "max": 8},
  "running": [ {"meeting_id","stage","stage_label","running","exit_code"} , ...],
  "pending": [ {"label","event_kind","derived_id"}, ... ] }
```
`running` entries come from `runner.run_status(id)` for each active id; `pending` entries are derived from the queued `RunParams` (a display label + kind + the id the derivation would produce), each carrying its `pending_id`.

Also: `set_max_concurrent(n)` (clamped 1–10), `remove_pending(pending_id)`.

## Section 2 — Add flow

`gui/app.py::new_meeting_launch` (the `/new` POST) changes its final step from `runner.launch_run(p)` to `batch.launch_or_enqueue(p)`, then **redirects back to a fresh `/new`** with a flash query param, e.g. `/new?flash=started&label=<title>` (or `flash=pending`). The dedup interstitial (source already processed) is unchanged and runs first.

`new_meeting_form` (the `/new` GET) reads the flash param + live counts (`batch.status()["counts"]`) and renders a one-line banner above the form:
> ✓ Started: *Becerra — CA Governor* · 6 running · 2 pending · [View library →](/)

"Add & start" replaces the "Start processing" button label. The banner's link points at `/` (the library is the batch view). Filling → Add → fresh form → fill → Add stays fast; no trip through another page.

## Section 3 — Library as the batch view

`gui/library.py` / `gui/models.py`:
- `MeetingSummary` gains `processed_at: Optional[float]` (epoch seconds) + a `processed_label` property (`"2m ago"` / `"3h ago"` / `"Jul 21"` / `"—"`).
- The scanner sets `processed_at` from the `pipeline_state.json` **mtime** (last processing/review activity). No schema change.
- `scan_meetings` **sorts by `processed_at` desc** (most-recent activity first), falling back to the current `(date, meeting_id)` when a state file has no mtime. So running + just-finished meetings float to the top.

`gui/templates/library.html`:
- A **new "Processed" column** (rendered from `processed_label`); the existing **Date** column (clip/air date) stays.
- Above the table, a **batch header strip**: `▶ N running · M pending`, a **max-concurrent** `<select>` (posts to set it), and the existing search/filters + "+ New meeting".
- A **pending strip**: the queued (not-yet-launched) items as small chips with a Remove control — they have no meeting dir yet, so they live here, not as table rows. When the scheduler starts one it appears at the top of the table on the next poll.
- Each in-flight row carries `data-meeting-id` and a `data-running` marker so the poller can target it.

`gui/static/library.js` (extends the existing filter script):
- Polls `GET /batch/status` (~every 4s) **only while** there are running/pending items; updates the running rows' Status/Stage cells, the counts, and the pending strip in place. Stops polling when nothing is in flight (no needless requests on a static archive).

New routes in `gui/app.py`:
- `GET /batch/status` → `JSONResponse(batch.status())`.
- `POST /batch/max` (form `n`) → `batch.set_max_concurrent(n)` → redirect `/`.
- `POST /batch/pending/{pending_id}/remove` → `batch.remove_pending(pending_id)` → redirect `/`.

## Architecture — files

**New**
- `gui/batch.py` — batch state (load/save/lock), `launch_or_enqueue`, `_tick`, `start_scheduler`, `status`, `set_max_concurrent`, `remove_pending`.

**Changed**
- `gui/app.py` — `new_meeting_launch` uses `batch.launch_or_enqueue` + flash redirect; `new_meeting_form` renders the banner + counts; new `/batch/status`, `/batch/max`, `/batch/pending/{pending_id}/remove` routes; `create_app` starts the scheduler once; library route passes batch state to the template.
- `gui/models.py` — `MeetingSummary.processed_at` + `processed_label`.
- `gui/library.py` — scanner reads state mtime → `processed_at`; sort by it.
- `gui/templates/library.html` — Processed column, batch header (counts + max control), pending strip, per-row poll hooks.
- `gui/static/library.js` — batch-status polling + in-place updates.
- `gui/templates/new_meeting.html` — "Add & start" label + flash banner.

## Data flow

1. `/new` (Add & start) → `new_meeting_launch` → dedup check → `batch.launch_or_enqueue(params)`.
   - slot free → `runner.launch_run` spawns `run_local.py` (local-only); id added to `active`.
   - pool full → params appended to `pending`.
   - redirect → fresh `/new` with the banner.
2. Scheduler thread ticks every ~4s: prunes finished from `active`, promotes pending into free slots.
3. Library (`/`) scans meetings (sorted by mtime), reads batch state (pending + counts), renders the table + strips. `library.js` polls `/batch/status` and live-updates in-flight rows/counts.
4. A run finishing frees a slot; the next tick launches the next pending item; its meeting dir now exists and appears at the top of the library on the next scan/poll.

## Error handling

- Scheduler `_tick` is wrapped: any exception is logged, the thread survives. A launch that raises drops that pending item (skip-and-continue) and continues.
- Batch state read is best-effort: a corrupt `_batch.json` resets to empty pending / default cap rather than crashing the library.
- `/batch/status` degrades to empty counts if state can't be read; `run_status` already returns `None` for vanished meetings (pruned from `active`).
- No new DB calls; nothing publishes.

## Testing

- `launch_or_enqueue`: under cap → `("started", id)` (monkeypatch `runner.launch_run`); at cap → `("pending", None)`; prunes finished `active` (monkeypatch `runner.run_status`).
- `_tick`: promotes pending when a slot frees; respects the cap; skip-and-continue on a launch error (leaves the pool advancing).
- State persistence round-trips; corrupt state → safe reset.
- `set_max_concurrent` clamps 1–10; `remove_pending` drops the right item.
- Routes (`TestClient`): `/batch/status` JSON shape; `/batch/max`; `/batch/pending/{pending_id}/remove`; `new_meeting_launch` routes through batch (started vs pending) and redirects with the flash.
- Scanner: `processed_at` from mtime; `scan_meetings` sorts by it (update the existing `test_scan_meetings_reads_state_and_sorts_by_date_desc`, which asserts clip-date order).
- Library render: Processed column, batch header counts, pending strip.
- `library.js` string-contract: references `/batch/status` + updates counts/rows.
- Scheduler thread is not started in tests — tests call `_tick()`/`launch_or_enqueue` directly for determinism; a single test asserts `start_scheduler` launches a daemon thread and is idempotent.

## Rollout

- Local tool: merge → restart `python -m gui`. The scheduler starts on boot; `_batch.json` is created on first use.
- The library sort changes from clip-date to processing-recency — an intentional, visible behavior change (update the one sort test).
- No DB migration; no deploy.

## Known limitations (accepted for v1)

- The cap governs **new-meeting launches** (via `/new`). Manual **Continue / Re-run a stage** from a meeting's Progress tab still run immediately and aren't counted against the cap, so a manual re-run during a full pool could briefly exceed it. Rare and operator-initiated; pooling re-runs is deferred.
- Intra-stage progress isn't shown; the row reports the current stage (N of 7), not a fine-grained percent.
- "Recently finished" isn't a separate section — finished meetings simply sort to the top of the library by their processing time and show their normal Status (needs review / ready / live).
