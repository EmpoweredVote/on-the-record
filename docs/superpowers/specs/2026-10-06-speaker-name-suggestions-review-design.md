# Speaker name suggestions — slice 3: review integration (design)

Date: 2026-10-06 · Status: approved design, spec under review · Owner: Chris
Parent spec: `2026-10-02-speaker-name-suggestions-design.md` (slices 1–2, merged:
PR #270 and #274).

## Goal

Make the suggestions from slice 2 save review time: generate them automatically
when a meeting is processed, show them on each speaker card in the GUI review
(and minimally in the terminal review), accept them with one click (or all
verified ones at once), and log every accept/edit so the auto-apply decision can
later be made on data. Nothing is ever applied without Chris.

## Part 1 — generation

- **New pipeline step** in `run_local.py`, immediately after Stage 4 (speaker
  identification): call the slice-2 orchestration (`src.name_suggest.suggest_names`
  + `write_suggestions`) and write `name_suggestions.json` in the meeting dir.
  - Never fails the run: any exception becomes a printed warning and an entry in
    the file's `warnings`; the pipeline continues.
  - **Skipped** when `event_kind == "floor"` (Congressional Record names
    everyone), when `--no-suggest-names` is passed, and on a resumed run when
    `name_suggestions.json` exists and is newer than `transcript_named.json`.
  - Uses the same dependencies as `run_local.py --suggest-names`
    (`PgNameDB` when `DATABASE_URL` is set, the research cache, the roster for the
    meeting's `body_slug`).
- **One lookup at a time across processes.** The web researcher call
  (`src.name_lookup.research`) is wrapped in an exclusive file lock
  (`fcntl.flock` on `CONFIG_DIR/name_lookup.lock`), so parallel GUI batch jobs
  queue their lookups instead of running them together. Waiting for the lock has
  no timeout beyond the per-lookup timeout already in place.
- **Off the Mac / logged out:** unchanged slice-2 behaviour — the web step is
  skipped with one warning; roster, politician and past-meeting steps still run.
- **Which speakers:** suggestions are computed for every speaker with evidence.
  The review shows a suggestion only when it would change something: the speaker
  is unnamed (no name, unidentified status, or no identity), or the suggested name
  differs from the current name (normalized comparison) — this surfaces
  misspellings like the five fixed on 2026-10-06.
- **Label drift:** suggestions are keyed by speaker label; a suggestion whose
  label no longer exists (e.g. after a merge) is ignored.
- **Re-run:** `run_local.py --suggest-names MEETING_ID` (exists) and a GUI route
  (`POST /meetings/{id}/suggest-names`) that runs it as a background job and
  reloads the review when done.

## Part 2 — review

### Card states (GUI, in the card's evidence area beside the voice hints)

| State | Shown | Actions |
|---|---|---|
| **Verified, no conflict** | "Suggested: NAME ✓", source (`web · <domain>` link, `roster`, `politician`, `past meeting`), the evidence quote(s), the guessed role | **Accept**; **Edit** (opens the existing name/role form pre-filled) |
| **Not verified** | "Suggested (not verified): NAME" + reason (`different name returned`, `page blocked`, `web lookup unavailable`, …) | **Edit** only, pre-filled |
| **Conflict or partial** | "Possible: NAME" + why (`also suggested for SPEAKER_X`, `first name only`) | none (information only) |

The suggestion model is derived from the record's `prefill_name`, `lookup`,
`conflict` and `partial` fields (slice 2). A record is **acceptable** only if
`prefill_name` is set (verified, no conflict, not partial).

### Accept → existing actions (no new identity logic)

| Suggestion source | Action called |
|---|---|
| `roster` / `politician` (has `politician_id`) | existing link action (`review_api.apply_link`) with the suggested name |
| `local_people` (has `local_slug`) | existing local-person action with that slug and the suggested role |
| `web` / `transcript` | existing local-person action with a new slug from the name (the card's `default_slug` logic) and the suggested role (`public_comment` if none) |

### "Accept all verified (N)"

A button at the top of the review page. It applies every acceptable suggestion
whose speaker is **still unnamed**, and never overwrites a name already set by
Chris, a voice profile or a roster match. It reports what it changed
("Accepted 7: SPEAKER_10 Aaron Spiegel, …"). N counts only those speakers.

### Banner

When the file's `warnings` say the lookup was skipped (logged out, usage limit,
database unavailable), the review page shows one banner with the warning text and
a **Re-run name lookup** button.

### Logging

Append-only JSONL at `<meeting_dir>/name_suggestion_log.jsonl`, one line per event:
`{ts, meeting_id, label, suggested_name, tier, source, verified, action, final_name}`
with `action` ∈ `accepted` | `edited` | `bulk_accepted` | `overridden`.

- `accepted` / `bulk_accepted`: written by the Accept paths.
- `edited`: the Edit button opens the existing form pre-filled and adds a hidden
  field `from_suggestion=<label>`; when that form is submitted and the final name
  or identity differs from the suggestion, an `edited` line is written (an
  unchanged submit logs `accepted`).
- `overridden`: at publish, for every speaker with an acceptable suggestion whose
  published name differs (normalized) from it — one line per label per publish,
  de-duplicated against the last line for that label.

These lines feed the graduation rule in the parent spec (a tier may auto-apply
only after ≥ 98% unchanged acceptance over ~10 reviewed meetings). No
auto-apply is built in this slice.

### Terminal review (`run_local.py --review`, minimal)

Each speaker prompt shows "Suggested: NAME (verified, SOURCE)" when an acceptable
suggestion exists (or "Suggested (not verified): NAME — REASON"). **[Y]** accepts
the acceptable suggestion when present (same action mapping and log line);
otherwise [Y] keeps today's meaning (accept the voice match). No bulk accept and
no editing UI in the terminal.

## Error handling

- Missing or corrupt `name_suggestions.json` → the review page renders exactly as
  today (no suggestions, no banner).
- Accept on a label that changed since the page loaded → the existing action's
  404/400 path; the card re-renders.
- Logging failure (disk) never blocks an accept; it prints a warning.

## Testing

- Card model: each state (verified / not verified / conflict / partial / no
  change needed) from fixture suggestion records.
- Accept mapping: each source calls the right existing action with the right args
  (monkeypatched `review_api` functions).
- Bulk accept: skips named, conflict, unverified and partial speakers; reports
  the changed list; idempotent on a second click.
- Logging: lines for accepted, edited, bulk_accepted; `overridden` at publish with
  de-duplication.
- Pipeline step: never raises; skip rules (floor, flag, fresh file); writes the
  file on a normal run (with fakes).
- Lock: two concurrent `research` calls serialize (a fake runner that records
  overlapping intervals).
- Terminal: [Y] takes the acceptable suggestion first, else the voice match.
- GUI: route tests for accept, bulk accept, re-run; a preview check in the
  browser pane of one real meeting's review page before merge.

## Out of scope

- Auto-apply of any tier.
- The lookup tuning round (suffixes, spelling variants, blocked official sites,
  CLI errors, IP pinning) — tracked separately.
- Auditing already-published names in bulk.
