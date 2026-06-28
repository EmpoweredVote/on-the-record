# One-Command Maintenance (`--republish-all`) — Design

**Date:** 2026-06-27
**Status:** Approved for planning
**Repo:** on-the-record (pipeline / `run_local.py`, `src/publish.py`)
**Context:** Sub-project **B** of the speaker-linking automation effort
(decomposition A → B → C → D; A/C/D/E shipped). Replaces the brittle
hand-maintained `republish_all.sh` with a single, dynamic maintenance command.

## Problem

Re-syncing published meeting data after a change (e.g. the multi-race/chamber
work) is a manual, error-prone chore:

- `republish_all.sh` loops `run_local.py --publish-meeting` over a **hardcoded
  list of 14 meeting ids** — the list must be hand-edited whenever meetings are
  added, and drifts out of date.
- It runs `--publish-meeting` **without `--publish-anyway`**, so meetings with
  `review_status != "pass"` (the common case — most are `None`) are skipped, and
  the resync silently misses them.
- `publish_meeting` fires the Render **deploy hook on every publish**, so a bulk
  re-publish triggers ~14 deploys instead of one.
- Rebuilding the voice-profile DB (`reenroll_profiles.py`) and the web redeploy
  are separate manual steps with no single entry point.

## Goal

One command that **re-publishes every already-published meeting** (a resync of
live data), optionally rebuilds the voice-profile DB, and fires **one** web
deploy at the end — dynamic (no hardcoded list), idempotent, and safe.

## Decisions (resolved in brainstorming)

- **Scope = re-publish meetings that are already in the published DB.** Discover
  all meeting dirs in `MEETINGS_DIR`, intersect with the set of slugs already in
  `meetings.meetings`, and re-publish exactly those. This is a *resync of live
  data*, not a force-publish of drafts.
- **Un-published meetings are skipped and reported**, never force-published. A
  bulk maintenance command must not push un-vetted drafts live; new meetings go
  through the normal review / bulk-relink flow.
- **`publish_anyway=True` for the resync.** These meetings were already vetted
  when first published; re-publishing existing data must not be re-blocked by the
  confidence gate (and `review_status` is `None` for most, so respecting the gate
  would skip nearly everything — the gate is the wrong signal here; "already
  published" is the right one).
- **Reenroll is opt-in (`--reenroll`).** It rebuilds the whole voice-profile DB,
  is the slowest step, and is orthogonal to publishing (publish reads
  transcripts, not profiles — reenroll changes future voice-ID, not the published
  data). The frequent case (resync after a publish-logic change) doesn't need it;
  `--reenroll` runs it (before the deploy) when profiles genuinely need rebuilding.
- **One deploy at the end.** Suppress the per-publish deploy hook during the
  batch and fire a single `_trigger_render_deploy()` at the end (`--no-deploy`
  to skip).
- **Continue-on-error + summary.** One failing meeting must not abort the batch;
  collect failures and report them, exit non-zero if any failed (mirrors
  `republish_all.sh`'s behavior).
- **Home: a new `run_local.py` subcommand** (`--republish-all`), alongside
  `--publish-meeting`, `--bulk-relink-apply`. Supersedes `republish_all.sh`
  (which is untracked — left in place, no longer the path of record).

## CLI surface

```
python run_local.py --republish-all
    [--reenroll]     # also rebuild the voice-profile DB (before the deploy)
    [--no-deploy]    # skip the single Render rebuild at the end
    [--dry-run]      # print the plan (which meetings would publish, reenroll y/n,
                     #   deploy y/n); write nothing
```

## Components

### 1. Suppress per-publish deploy — `publish.publish_meeting(..., trigger_deploy=True)`
Add a `trigger_deploy: bool = True` keyword to `publish_meeting`
(`src/publish.py:538`). The final `_trigger_deploy_hook()` call
(`src/publish.py:570`) runs only when `trigger_deploy` is `True`. Default `True`
preserves every existing caller (a lone `--publish-meeting` still deploys).
Thread the same flag through `run_local._publish_meeting_standalone(meeting_id,
publish_anyway=False, trigger_deploy=True)` (`run_local.py:1820`) so the batch
can suppress it.

### 2. Published-slug lookup — `_published_meeting_slugs() -> set[str]`
A small helper (in `run_local.py`, or `src/publish.py` beside the other DB
helpers) that connects via `DATABASE_URL` and runs
`SELECT slug FROM meetings.meetings` → a set of published slugs. Used to decide
which discovered meetings to re-publish. Unit-testable with a fake cursor.

### 3. Orchestrator — `_republish_all(args)` (subcommand handler)
1. Discover meeting dirs: `sorted(d for d in config.MEETINGS_DIR.iterdir() if
   d.is_dir() and not d.name.startswith(".") and (d/"transcript_named.json").exists())`.
2. Fetch `published = _published_meeting_slugs()`.
3. Partition: `to_publish = [d for d in dirs if d.name in published]`;
   `skipped = [d for d in dirs if d.name not in published]`.
4. Print the plan. `--dry-run` → print and stop (no writes).
5. For each `to_publish` meeting: `_publish_meeting_standalone(name,
   publish_anyway=True, trigger_deploy=False)` inside try/except — on error,
   record the failure and continue.
6. If `--reenroll`: run reenroll as a **subprocess** —
   `subprocess.run([sys.executable, "reenroll_profiles.py"], ...)` — over all
   meetings (its default). Subprocess (not an in-process call) avoids coupling
   to `reenroll_profiles.main()`'s `sys.argv` parsing and keeps the script
   standalone. Report its exit status; a reenroll failure doesn't undo publishes.
7. If not `--no-deploy`: one `_trigger_render_deploy()`.
8. Summary: `N published, K failed (list), M skipped-unpublished (list),
   reenroll y/n, deploy y/n`. Exit non-zero if any publish failed.

## Data flow

```
--republish-all [--reenroll]
   │ discover MEETINGS_DIR transcripts  ∩  SELECT slug FROM meetings.meetings
   ▼ for each already-published meeting:
   │    _publish_meeting_standalone(slug, publish_anyway=True, trigger_deploy=False)
   │    [continue on error; collect failures]
   ▼ (--reenroll) reenroll_profiles — rebuild the voice-profile DB
   ▼ one _trigger_render_deploy()        [unless --no-deploy]
   ▼ summary: published / failed / skipped-unpublished / reenroll / deploy
```

## Error handling / edge cases

- **A meeting fails to publish** → recorded, batch continues; non-zero exit at
  the end with the failed list.
- **Discovered meeting not yet published** → skipped + reported (not force-published).
- **Published slug with no transcript dir** (deleted locally) → can't re-publish;
  reported as skipped (it's in `published` but not in discovered dirs).
- **`--dry-run`** → no publish, profile, or deploy writes.
- **Reenroll fails** → reported; publishes already done are not rolled back; the
  deploy still fires (data was published) unless `--no-deploy`.
- **DB unreachable** (`_published_meeting_slugs`) → clear error, exit non-zero,
  nothing published.

## Testing

- `publish_meeting(trigger_deploy=False)` does **not** call `_trigger_deploy_hook`;
  default (`True`) still does. (Mock the hook + DB.)
- `_published_meeting_slugs`: returns the slug set from a fake cursor.
- `_republish_all` orchestrator (temp `MEETINGS_DIR`, mocked publish + deploy +
  slug-set, mirroring the bulk-relink apply tests):
  - publishes only already-published meetings; skips + reports unpublished ones;
  - calls publish with `publish_anyway=True, trigger_deploy=False`;
  - fires **exactly one** deploy at the end; `--no-deploy` fires none;
  - continues past a failing meeting and reports it (non-zero exit);
  - `--reenroll` invokes the reenroll subprocess (mock `subprocess.run`, assert
    called); absence skips it;
  - `--dry-run` writes/publishes nothing.

## Out of scope / deferred

- **Change-only detection** (re-publish only meetings whose transcript changed) —
  publish all already-published, dynamically; change-tracking is premature at
  ~14 meetings.
- **Force-publishing un-vetted drafts** — deliberately excluded; that's the
  normal review / bulk-relink path.
- **Removing `republish_all.sh`** — untracked; superseded, left in place.
- **Sub-project D** (auto-link high-confidence matches on first pass) — separate.
