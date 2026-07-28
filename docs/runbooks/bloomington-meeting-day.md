# Runbook: Bloomington Common Council meeting day

The operational sequence for a regular-session Wednesday, written for
**2026-07-29** (first live Pass B run). Adjust dates/refs for later meetings.

All commands run from the repo root (`/Users/chrisandrews/Documents/GitHub/on-the-record`)
with `.venv/bin/python` (never system python). Secrets come from `.env.local`
(DATABASE_URL, ANTHROPIC_API_KEY), loaded by the scripts themselves.

---

## 0. Pre-meeting (automatic)

The launchd poller (`vote.empowered.poll-agendas`, daily 9:00 AM, logs to
`~/CouncilScribe/agendas/poll.log`) keeps the scheduled meeting and its agenda
items fresh — addenda can land through meeting day and the poller re-publishes
changed agendas idempotently. Nothing to do.

Manual re-poll (e.g. to pick up a same-day addendum immediately):

```bash
.venv/bin/python scripts/poll_agendas.py            # default window: today .. +8 days
```

(`--dry-run` previews without DB writes; `--days N` widens the window.)

The scheduled row this creates/maintains is slug
**`bloomington-city-council-2026-07-29`** — every later step keys on that slug.

## 1. Wait for CATS to post the video

URL pattern (same Azure blob store as July 22's `B_CC_260722.m4v`):

```
https://catstv.blob.core.windows.net/videoarchive/B_CC_260729.m4v
```

Check availability (200 = posted):

```bash
curl -sI https://catstv.blob.core.windows.net/videoarchive/B_CC_260729.m4v | head -1
```

CATS usually posts within a day or two of the meeting.

## 2. Process the recording — CLI, NOT the GUI launch form

> **Why not the GUI?** The GUI's New Meeting form cannot take a custom meeting
> id — `gui/runner.py:derive_meeting_id` honors `RunParams.meeting_id`, but the
> `/run` form (`gui/app.py`) never populates it, so a GUI launch derives
> `2026-07-29-bloomington-common-council-regular-session`. That slug does NOT
> match the scheduled row, so publish would create a **duplicate meeting**
> instead of flipping the scheduled one. Use the CLI for processing; the GUI is
> still the right place for speaker review (step 3).

```bash
.venv/bin/python run_local.py \
  --input "https://catstv.blob.core.windows.net/videoarchive/B_CC_260729.m4v" \
  --meeting-id bloomington-city-council-2026-07-29 \
  --body bloomington-common-council \
  --date 2026-07-29 \
  --event-kind council \
  --meeting-type "Regular Session" \
  --city Bloomington \
  --compute modal \
  --diarizer oss \
  --no-review
```

- `--meeting-id bloomington-city-council-2026-07-29` is **MANDATORY** — it must
  be the scheduled slug so Pass A (agenda) and Pass B (video) share one meeting
  row and item permalinks survive. An auto-derived id will not match.
- `--body bloomington-common-council` loads the council roster for speaker ID
  (July 22 was processed without it; this run should be faster to review).
- `--no-review` skips terminal review so the pipeline runs to completion
  unattended; review happens in the GUI next (same flow as July 22).
- The other flags are the July 22 council standard.

## 3. Review speakers in the GUI

```bash
.venv/bin/python -m gui        # http://127.0.0.1:8000
```

Open the meeting → Review tab. With `--body` supplied, roster-matched council
members should already be named; fix/confirm the rest as usual.

## 4. Publish (flips the scheduled meeting in place)

```bash
.venv/bin/python run_local.py --publish-meeting bloomington-city-council-2026-07-29
```

## 5. Align agenda items to the video (Pass B flip)

```bash
.venv/bin/python run_local.py --align-agenda bloomington-city-council-2026-07-29
```

Runs anchors → LLM span bounding → mechanical gates → legislation-page oracle,
then flips every item to `happened` via per-row
`UPDATE meetings.agenda_items ... WHERE meeting_id AND position` — never
deletes (item ids are public permalinks). Prints a loud per-item table (span
times or ABSTAINED + reason, outcome or none).

Guard: if it reports **zero agenda items** for the slug, the meeting was
published under the wrong `--meeting-id` — do not proceed; the scheduled slug
is required (see step 2).

## 6. When the clerk's Memorandum posts

OnBoard file type "Memorandum" — observed next-day for July 22, allow up to
~a week:

```bash
.venv/bin/python run_local.py --reconcile-memo bloomington-city-council-YYYY-MM-DD
```

Overwrites item outcomes from the memo (authoritative — fixes the
pass-abstention gap: the chair never says "motion carries"), writes one
meetings.votes row per substantive motion ("Passed 8–0" style), and
per-member vote_records on named split votes. Unparseable motions
abstain loudly — read the NOTE lines.

- **Re-run this after any `--publish-meeting` re-publish** — re-publishing
  wipes memo votes (`_replace_votes` delete-then-inserts).
- Or let the daily poller do it: `poll_agendas.py --reconcile-memos`
  (opt-in flag; not yet on the launchd job — enable after July 22 ages
  out of the lookback window, since its legacy slug fails loudly there).

## 7. Verify

- `/upcoming` no longer lists the meeting; its item pages (`/items/<id>`)
  show the happened-state badge and, for bound items, a **Watch the
  discussion** link that seeks the meeting video.
- Item-page footer: the meeting name is now a live link to
  `/meetings/bloomington-city-council-2026-07-29` (it links only once the
  meeting row is `published`).

### Expected caveats (from the July 22 calibration)

- **Passes likely abstain.** The outcome gate requires a citable outcome
  phrase in a segment's own text; garbled roll-calls don't qualify
  (abstain-don't-guess — July 22's Resolution 2026-13 passed in fact but
  published with a blank outcome). Expect blank outcomes on the items voted
  this night: **Ordinance 2026-15 / 2026-16 / 2026-17** (Resolution 2026-14
  too, if its disposition isn't spoken cleanly). `continued`/`failed`
  outcomes verified fine on July 22.
- **Short-title items get no Watch link.** Items without a legislation ref or
  ≥2 distinctive title tokens (agenda boilerplate like "ROLL CALL") can't pass
  the containment gate and publish as happened-without-span. July 22 bound
  10/15; the 5 unbound were exactly these.
- **Re-run `--align-agenda` in a few days** once the city's legislation pages
  publish final actions for 2026-15/16/17. **Known limitation:** the oracle
  (`apply_oracle`) only VETOES an outcome the LLM claimed — it cannot FILL an
  abstained outcome, so a re-run helps only if the LLM claims the outcome and
  the oracle now confirms it. Oracle-driven outcome backfill is an open
  follow-up (Phase 4 territory).

## Rollback / re-run

`--align-agenda` is re-runnable: it updates the same rows in place (keyed by
meeting + position) and never deletes, so a bad alignment is fixed by simply
re-running it (after prompt/gate fixes if needed). Item and meeting permalinks
are unaffected.
