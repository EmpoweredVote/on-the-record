# Processing GUI — Design Overview & Roadmap

> This is the **design/shared-understanding** document produced from the grilling session on 2026-07-02. It is *not* a task list. Each of the four slices gets its own executable plan file (`2026-07-02-processing-gui-slice-N-*.md`); Slice 1 is written, Slices 2–4 are detailed as each is picked up so their steps reflect patterns Slice 1 establishes.

## Problem

`run_local.py` is the CLI for the CouncilScribe pipeline. The terminal workflow is confusing in two ways:

- **Pain A (launch/monitor):** parameters are hard to keep straight (`--compute`, `--diarizer`, `--clip`), and the concepts `title` / `event_kind` / `meeting_type` / `meeting_id` blur together. It's easy to hand-type a slightly different `--meeting-id` for a video already processed and get a **duplicate**. When a run errors, you get a raw traceback with no headline.
- **Pain B (speaker review):** the interactive review loop is terminal-only — `cbreak` keypresses, `ffplay` windows that steal keyboard focus, and an overloaded **Enter** that *skips* (keeps whatever name is attached) rather than *accepts* the guess (which is `Y`). Picking the linked politician is a numbered-list prompt.

## Architecture (locked)

A **local, single-user web app** that runs on the operator's Mac. Two surfaces that relate to the pipeline differently:

- **Batch stages (ingest/diarize/transcribe/summarize/publish)** — the GUI **shells out to `run_local.py`** as a subprocess and streams its stdout. The heavy pipeline stays as the trusted CLI; if the GUI crashes, checkpoints on disk are untouched.
- **Speaker review (Stage 4)** — the GUI **imports `src/review.py` directly** (in-process). That module is a *pure, terminal-free core* (`build_review_state`, `rename_speaker`, `merge_speakers`, `link_speaker`, `mark_unidentified`, `mark_non_speaker`, `link_to_unidentified_handle`) — the terminal loop `_interactive_speaker_review` is just one shell over it. The web review page is a **second shell over the same functions**, so review logic is reused, not rewritten.

**Tech stack:** FastAPI backend serving **server-rendered HTML (Jinja2)** with light vanilla JS / **Alpine.js** for the review page's interactivity. One Python process, no build step, no auth (localhost). Runs via `python -m gui`. Kept entirely separate from the public static-export `web/` Next.js app. Heavy compute still defaults to **Modal** (free tier) so the Mac isn't pegged.

**Future ev-accounts admin panel:** deliberately *not* coupled to today. The heavy pipeline needs local/Modal compute and can't move there anyway. If the screens migrate later, the FastAPI backend and all of `review.py` come along unchanged; only the screens get reimplemented.

## Resolved decisions

### Meeting identity & duplicates
- Identity is the **source key** — the normalized identity of a source recording (yt-dlp `extractor:id`, CATS TV archive id, or local absolute path). **One source key → at most one meeting.** Grabbing the same video again *opens the existing meeting*, never creates a duplicate. (Glossary: `CONTEXT.md` → "Source key".)
- **Same URL, different clip window is NOT a new meeting** — if the whole video is processed you navigate within it. The clip window only matters for "process a slice from the start" (cost), a first-grab-only choice.
- `audio_source` (the URL) is only saved at Stage 4 today and is **not** in `pipeline_state.json`. To dedup *before* launching, a small additive change writes a normalized `source_key` into `pipeline_state.json` at Stage 1 (Slice 3). Both CLI and GUI then populate it → no separate index that drifts.
- On a match: **soft warning, never a hard block** — "You've grabbed this as `<id>` (stage N/7). [Open it] · [Make a new copy anyway]".

### URLs & metadata edits (ADR 0002)
- Public meeting URLs key on the **immutable UUID** (`meetings.meetings.id = gen_random_uuid()`), surfaced to the frontend as `meeting_id`. The human slug lives in the separate `slug` column and isn't used in public URLs today. So **editing a title cannot break a URL.**
- **Freeze rule (adopted):** the GUI's metadata editor writes `title`/`meeting_type`/`city`/`date`/`event_kind` to local files + Supabase, but **never renames the local directory or rewrites the `slug` column.** Keeps a future pretty-URL migration safe without building redirect infra. See `docs/adr/0002-meeting-url-identity-and-frozen-slug.md`.
- The site is `output: "export"` (fully static) for **listing/topic/search** pages, but the **meeting detail page fetches at runtime** by id. So a metadata edit shows on the detail page immediately, but listings need a rebuild. Therefore: **edits save to local + Supabase immediately; a separate "Publish to site" button triggers one Render redeploy** (`_trigger_render_deploy`, `RENDER_DEPLOY_HOOK_URL`). Edits batch — fix several meetings, rebuild once.

### Progress
- **6-step stepper** driven by the on-disk `pipeline_state.json` (`completed_stage`) — correct even if the browser is closed/reopened mid-run or the subprocess dies.
- **Live scrolling log pane** mirroring subprocess stdout (zero script change).
- **Elapsed timer + rough ETA** per stage. **No fabricated percentage bars** — diarization/Modal are opaque black boxes and a lying bar is worse than an honest log.
- Small additive script change: emit structured `{"event":"stage_start","stage":"diarize"}` lines at boundaries so the stepper animates live (reuses the same event channel as errors).

### Errors
- **Always tier (every error):** red banner with *which stage failed* (from the state file) + the exception's **last line** as headline + a **folded** full traceback (never hidden) + a **Retry** button (re-launch same meeting-id resumes from the intact checkpoint).
- Exit codes distinguish success (`0`) / known-guard failure (`2`, already has a clean message) / crash (other).
- **Growing catalog, not speculative:** the GUI logs every nonzero-exit failure to a local file; a friendly explanation is added once a failure recurs. No guessing at errors never seen.

### Reprocessing
- **Whole-stage redo only** (`--redo diarize|transcribe|identify|summary`), exposed as buttons. **No surgical/section-level** reprocessing (transcription is a single whole-audio pass; splicing has correctness risk for marginal gain).
- "Fix a speaker's name" = re-open the review page (Stage 4 only, no GPU).

### Review page (Pain B)
- **Auto-accept high-confidence guesses** (confidence ≥ 0.85, matching the existing gate): shown **green and pre-confirmed** under a collapsed "Confirmed" section. **Uncertain ones (amber) sort to the top** under "Needs attention". Everything stays visible, editable, and reversible — auto-accept changes the *default state*, not your *access*.
- **No overloaded Enter** — every action is an explicit labeled button: Accept / Change (politician search dropdown) / Merge / Unidentified / Not a speaker.
- **Clip playback inline** via HTML5 `<video>`/`<audio>` seeking to candidate timestamps (source media already in the meeting dir). Kills the `ffplay` focus-stealing / `osascript` hack entirely.
- **Politician linking** = a live search box over `essentials` (reuses `search_politicians`), link on click; plus "create local person" for non-essentials speakers. Underneath: `review.link_speaker`.
- **Enrollment (the flywheel):** per-speaker **"✓ Save this voice for future meetings"** checkbox, **defaulted by speech length** (on for enough speech, off + "thin sample" note for very short) — a guard against the profile pollution calibration already found. Shows NEW vs UPDATE. Reuses `_enroll_mapping` / `resolve_mapping_enrollment` / `save_profiles`. Enrollment stays keyed on the meeting **directory name** (calibration leave-one-out depends on it).

### New-meeting form (Pain A)
- Keep **both** `event_kind` (controlled enum, drives Chamber-vs-Race anchor + roles) and `meeting_type` (free-text sub-label shown on the site). **Auto-derive and hide `meeting_id`** (removes the hand-typed-duplicate vector), with a read-only "Advanced" reveal + rare override.
- `event_kind` = dropdown with plain-English one-line descriptions. **Conditional fields:** council/school_board → Chamber picker; debate/forum → Race picker; others optional.
- **Live "how this will look on ontherecord.com" preview card** updating as you type — the fastest way to learn which field feeds what.

## Cross-cutting file structure

All GUI code lives under a new top-level `gui/` package, separate from `src/` (pipeline) and `web/` (public site):

```
gui/
├── __init__.py
├── __main__.py        # `python -m gui` → uvicorn
├── app.py             # FastAPI app factory; mounts routers, templates, static
├── models.py          # GUI-facing dataclasses (MeetingSummary, …) — no HTTP
├── library.py         # Slice 1: scan meetings dir → summaries (pure)
├── review_api.py      # Slice 2: wraps src/review.py for the web page
├── launch.py          # Slice 3: build command, spawn run_local.py, stream stdout
├── publish_api.py     # Slice 4: metadata edit + Supabase + Render redeploy
├── templates/         # Jinja2: library.html, review.html, new_meeting.html, …
└── static/            # style.css, review.js (Alpine), …
tests/
├── test_gui_library.py    # Slice 1
├── test_gui_review.py     # Slice 2
├── test_gui_launch.py     # Slice 3
└── test_gui_publish.py    # Slice 4
```

Rationale: split by responsibility, not layer. Each `gui/*.py` module has one job and is testable without a browser (pure functions + FastAPI `TestClient`). Reuses existing pytest fixtures in `tests/conftest.py` (`tmp_meetings_dir`, `tagged_meeting_dir`).

## Roadmap (build order — locked)

1. **Meeting Library (tracer).** Read `MEETINGS_DIR` + each `pipeline_state.json`; list processed meetings with stage/status. Small, low-risk, proves the plumbing, immediately useful. → `slice-1-library.md`
2. **Review page (Pain B).** Self-contained (works on already-processed meetings, no subprocess): clips, greens/ambers, politician dropdown, merge/unidentified, enrollment checkboxes, write-back via `review.py`. Highest value, lowest wiring risk. → `slice-2-review.md`
3. **New-meeting + Launch + Progress (Pain A).** De-confusing form, `source_key` dedup check, subprocess launch, stepper + live log, error always-tier. CLI still works meanwhile. → `slice-3-launch.md`
4. **Edit + Publish polish.** Metadata editing (local + Supabase), batch "Publish to site" redeploy, redo-stage buttons. → `slice-4-publish.md`

## Conventions
- Run everything with the project venv: `.venv/bin/python`, `.venv/bin/pytest` (system `python3` lacks deps).
- Data root honors `CS_DATA_DIR`; never hardcode `~/CouncilScribe`.
- New runtime deps go in `requirements.txt`. Tests use `fastapi.testclient.TestClient` (needs `httpx`).
```
