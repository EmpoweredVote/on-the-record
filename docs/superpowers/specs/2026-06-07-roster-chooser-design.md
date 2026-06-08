# Roster chooser for `run_local.py`

**Date:** 2026-06-07
**Status:** Approved (pending implementation)

## Problem

Running `python run_local.py` without `--body` silently falls back to the
legacy `~/CouncilScribe/config/council_roster.json` (a Bloomington City
Council roster) to guide Stage 4 speaker identification. There is no way to
pick a different roster, or to run with no roster at all, short of passing
`--body <slug>`. Operators who process meetings for other bodies (or who want
no roster bias) get the Bloomington roster applied without being asked.

## Goal

When an interactive run doesn't specify a roster, prompt the operator to
choose one — from the cached per-body rosters, the legacy file, or "no
roster" — instead of silently defaulting to Bloomington.

## Decisions (from brainstorming)

- **D1 — When to prompt:** only when `--body` is *not* given (passing `--body`
  keeps working silently).
- **D2 — Menu contents:** cached rosters from `config/rosters/*.json`, plus the
  legacy `council_roster.json` labeled "legacy", plus "No roster". No
  fetch-a-new-roster flow — `refresh_roster.py` stays the way to add a body.
- **D3 — Non-interactive default:** when there's no TTY and no `--body`, use
  **no roster** (not the legacy file).
- **D4 — Tagging:** picking a cached roster tags the meeting (`body_slug`) the
  same way `--body` does, so resume + voice enrollment reuse it.

## Trigger conditions

The chooser runs inside `run_pipeline`, at the existing Phase 109 body
resolution point (before Stage 1), only when **all** of:

1. `sys.stdin.isatty()` — interactive terminal.
2. No `--body` passed (`cli_body` is falsy).
3. Meeting not already tagged (`persisted_body` is falsy).
4. No prior choice recorded (`state.roster_choice is None`).
5. Stage 4 not already complete (`not state.is_complete(PipelineStage.IDENTIFIED)`)
   — avoids re-prompting on resumes where identification is done.

So: fresh interactive runs without `--body`. Resumes reuse the earlier choice.

## Menu

Numbered list, printed under a `ROSTER SELECTION` banner:

1..N. each `config/rosters/*.json`, shown as `{body_key} ({N} members) [{slug}]`
      (sorted by filename).
N+1.  legacy `council_roster.json` if it exists, shown as
      `{city} {body} (legacy, {N} members)`.
N+2.  `No roster (skip name correction)`.

Bare Enter selects **No roster**. Out-of-range / non-numeric input re-prompts.

## Persistence

Add a `roster_choice: Optional[str]` field to `PipelineState`
(`src/checkpoint.py`), serialized in `pipeline_state.json` alongside
`body_slug`. Values:

| Choice        | `body_slug`        | `roster_choice` |
|---------------|--------------------|-----------------|
| cached roster | `<slug>` (set)     | `<slug>`        |
| legacy file   | unchanged (None)   | `"__legacy__"`  |
| no roster     | unchanged (None)   | `"__none__"`    |

The `__legacy__` / `__none__` sentinels start with `_`, which the Phase 109
slug regex `^[a-z0-9][a-z0-9_-]{0,63}$` rejects, so they can never collide
with a real body slug.

`_load()` reads `roster_choice` with a `.get(..., None)` default for backward
compatibility with state files written before this change.

## Stage 4 roster load

Replace the current two-branch load (run_local.py ~line 706):

```python
if effective_body_slug:
    roster = load_roster(body_slug=effective_body_slug)
elif state.roster_choice == "__legacy__":
    roster = load_roster()        # explicit legacy choice
else:
    roster = None                 # "__none__", or non-interactive / no --body
```

When `roster is None`, print:
`No roster loaded — speaker names won't be corrected against a council roster.`

## Behavior change (intended)

This flips the Phase 109 **D-05** contract: previously, no `--body` + no
persisted slug → bare `load_roster()` (legacy Bloomington). Now that path
yields **no roster** unless the operator interactively picks one. This is the
core of D3 and the reason for the request.

## New components / changes

- `src/checkpoint.py` — add `roster_choice` field (init, `_load`, `save`).
- `run_local.py`:
  - `_list_cached_rosters() -> list[tuple[str, str]]` — scan
    `config/rosters/*.json`, return `(slug, label)`.
  - `_prompt_roster_choice() -> tuple[Optional[str], str]` — print menu, read
    selection, return `(body_slug_or_None, marker)`.
  - `run_pipeline` — invoke the chooser at the body-resolution block; persist
    the result to `state`.
  - Stage 4 — new three-branch load above + the "no roster" message.

## Testing

- `tests/test_body_tagging.py` — update the D-05 test (lines ~307–331) to
  reflect the new contract: no `--body` + no persisted slug + no
  `roster_choice` → `roster is None` (non-interactive default), not bare
  `load_roster()`.
- New tests:
  - `roster_choice == "__legacy__"` → bare `load_roster()` called.
  - `roster_choice == "__none__"` → `roster is None`, `load_roster` not called.
  - cached pick → `body_slug` set and `load_roster(body_slug=...)` called.
  - `PipelineState` round-trips `roster_choice` through save/load; legacy
    state files (no `roster_choice` key) load as `None`.
- The chooser prompt itself is gated on `sys.stdin.isatty()`, so unit tests
  drive the resolution/load logic directly rather than the interactive prompt.

## Out of scope (unchanged)

- Offline utilities `--show-roster`, `--fix-profiles`, `--fix-transcripts`
  keep calling bare `load_roster()`.
- Batch mode stays non-interactive (no chooser; obeys D3 → no roster unless
  `--body`).
- No fetch-a-new-roster flow.

## Documentation

Update `README.md` "Speaker identification strategy" section with a short note
on roster selection for the local CLI: the chooser, the `--body` flag, and the
non-interactive "no roster" default.
