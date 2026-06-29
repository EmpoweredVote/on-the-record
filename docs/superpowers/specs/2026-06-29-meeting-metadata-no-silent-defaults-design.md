# Stop silently stamping meetings as Bloomington / Regular Session / council

**Date:** 2026-06-29
**Status:** Approved (pending implementation plan)

## Problem

Meetings ingested without explicit metadata silently default to
`city="Bloomington"`, `meeting_type="Regular Session"`, `event_kind="council"`.
This mis-classified at least three real candidate forums (Josephine County,
Lawrence County, Monroe County) as "Bloomington Regular Session" council
meetings in production (already corrected in the DB by a separate session).

The silent fallback survives in several places even after the codebase moved
the CLI flags to `default=None`:

- `run_local.py:2609-2610` — `event_kind` silently becomes `"council"` when
  unset, even non-interactively / in batch.
- `run_local.py:2615-2622` — non-interactive runs silently stamp
  `Bloomington` / `Regular Session` and default `date` to today.
- `run_local.py:1756-1757, 1775` — batch input parsing hardcodes
  `"Bloomington"` / `"Regular Session"`.
- `run_local.py:1845-1848` — batch silently defaults a missing `date` to today.
- `src/models.py:240,242` and `289-291` — dataclass + `from_dict` defaults
  silently fabricate `"Regular Session"` / `"council"`.

`src/publish.py` `_upsert_meeting` has no guard: `validate_event_entities`
never checks the `event_kind` value itself, so a guessed/missing kind reaches
the DB unchallenged.

## Goal

An unknown classification must never be silently invented. A run either:

1. gets explicit metadata from the operator (CLI flags or interactive prompt),
2. explicitly opts into the civic defaults via `--default`, or
3. fails loudly with guidance.

`city`, `meeting_type`, `event_kind`, **and `date`** are all required-explicit.
`date` is included because the meeting rarely occurred on the processing day —
guessing today is wrong almost always.

## Approach (chosen)

Option (a): require explicit metadata. Rejected option (b) (derive from the AI
summary) because the summarizer (`src/summarize.py`) is hard-wired to "city
council meeting" prompts and runs *after* `meeting_id`/directory are built from
`meeting_type` — AI-derivation would require reordering the pipeline and
rewriting prompts, and cannot fail loudly before metadata is first needed.
Option (b) may be revisited later as an enhancement; it is out of scope here.

## Design: three layers of defense + batch fixes

### Layer 1 — Ingest enforcement: `run_local.py` `_resolve_metadata` (~2600-2635)

The operator-intent capture point. For each of `event_kind`, `city`,
`meeting_type`, `date`, resolve by mode:

- **Explicit flag given** → use it. Validate `event_kind` against the enum
  (`validate_event_kind`).
- **Interactive (`stdin.isatty()` and not `--default`)** → prompt:
  - `event_kind` — *new prompt*. Show `council` as the Enter-default and list
    valid kinds (`council/school_board/forum/debate/community_meeting/
    news_clip/press_conference/other`). Validate the response.
  - `city` (only when `event_kind in (council, school_board)`) — prompt,
    Enter accepts `CITY_DEFAULT`.
  - `meeting_type` — prompt, Enter accepts `MEETING_TYPE_DEFAULT`.
  - `date` — prompt, **no Enter-default**; re-prompt until a non-empty value
    is given (no today fallback).
- **`--default` passed** → silently apply `Bloomington` / `Regular Session` /
  `council` for the unset fields. `--default` does **not** fill `date` — date
  is always required.
- **Non-interactive, no `--default`, field unset** → collect into a missing
  list. After processing all fields, if the list is non-empty, raise
  `ValueError` with guidance, e.g.:

  > Refusing to guess meeting metadata: missing --date, --event-kind, --city.
  > Pass them explicitly, or pass --default to use Bloomington / Regular
  > Session / council (date is always required).

`city` is required only for `council`/`school_board` (preserve the existing
`requires_city_default` logic). Other kinds may have a null city.

### Layer 2 — Honest data layer: `src/models.py`

The dataclass/serialization layer must never fabricate a civic classification.

- `meeting_type: Optional[str] = None` (was `str = "Regular Session"`)
- `event_kind: Optional[str] = None` (was `str = "council"`)
- `from_dict`: `d.get("meeting_type")` / `d.get("event_kind")` — drop the
  invented fallbacks. (`city` is already `Optional`.)

Saved `transcript.json` files already carry real values written by `to_dict`,
so reload of existing meetings is unaffected; only genuinely-absent fields
become `None` and fall through to the Layer 3 backstop. Existing falsy-filtered
usages (e.g. `for part in (city, meeting_type) if part`) and `in`-checks are
`None`-safe; the summary header that interpolates these runs only after Layer 1
resolution.

### Layer 3 — Publish backstop: `src/publish.py` `_upsert_meeting` (~196)

The last line before the DB. Before the upsert:

- `validate_event_kind(meeting.event_kind)` — raises on `None`/empty/invalid,
  closing the gap that `validate_event_entities` never checks the kind value.
- Require non-empty `meeting_type`.
- For `event_kind in (council, school_board)`, require non-empty `city`.

Raise a clear `RuntimeError`/`ValueError` on violation so a future code path
that constructs a `Meeting` directly cannot write a guessed Bloomington/council
row.

### Layer 4 — Batch: `_parse_batch_inputs` + `_run_batch`

- `_parse_batch_inputs` (~1736-1782): stop hardcoding `"Bloomington"` /
  `"Regular Session"` (1756-1757, 1775). Leave `city`/`meeting_type` as `None`
  when the input omits them; keep parsing the optional CITY/TYPE columns and the
  filename date extraction (that is explicit, not a guess).
- `_run_batch` `batch_args` Namespace (~1822-1842): add the currently-missing
  `event_kind` (latent `AttributeError`), and propagate `default` and `title`
  from the parent args.
- Remove the "No date provided, using today" fallback (1845-1848); a missing
  date now flows to Layer 1 and hard-fails the entry.
- Guard `mid` construction (lines 1810, 1850) against a `None` `meeting_type`
  (e.g. defer building it, or use a date-only placeholder for the result
  record).
- Batch is non-interactive, so under-specified entries hard-fail in Layer 1.
  The existing per-entry `try/except Exception` records them as `failed` in the
  batch summary while sibling entries continue.

### Error type

`_resolve_metadata` raises a plain `ValueError` (caught by batch's
`except Exception` per entry). The single-run CLI path catches it and prints the
guidance message cleanly with a non-zero exit, not a traceback.

### Help text

Update `--default`'s help (3544-3546) to drop "today" and note that `--date` is
always required.

## Testing

- `_resolve_metadata` unit tests (mock `sys.stdin.isatty`, `input`):
  - non-interactive + no `--default` + any unset field → raises `ValueError`
    naming the missing fields (incl. `date`).
  - `--default` → `council` / `Bloomington` / `Regular Session`, but still
    fails if `date` is unset.
  - explicit flags honored; invalid `event_kind` rejected.
  - interactive prompts, including the new `event_kind` prompt and the
    re-prompt-until-given `date` behavior.
- `models.Meeting`: defaults are `None`; `from_dict` without the fields yields
  `None`; `to_dict`/`from_dict` round-trip preserves real values.
- `publish._upsert_meeting` guard: `None`/invalid `event_kind` → raises;
  council with empty `city` → raises; valid meeting passes (reuse existing
  publish test harness/mocks).
- Batch: an under-specified entry is recorded `failed` while siblings complete;
  `--default` applies to batch entries.
- Run the existing test suite (`.venv/bin/python -m pytest`).

## Out of scope

- Option (b): AI-derived classification.
- Backfilling/auditing historical meetings (already corrected separately).
