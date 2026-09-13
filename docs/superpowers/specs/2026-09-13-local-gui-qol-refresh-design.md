# Local Processing GUI — Quality-of-Life Refresh + Floor-Session Import — Design

Date: 2026-09-13
Status: approved (brainstorming), ready for planning
Repo: `on-the-record` (`gui/` package + `.github/workflows/`)
Related:
- Memory notes: `processing-gui-plan`, `gui-workspace-redesign`, `gui-batch-processing`,
  `review-ui-future-direction`, `house-floor-weekly-automation`,
  `house-floor-admin-review-panel`, `meeting-confidence-gate-status`,
  `web-publishing-architecture`
- Superseded direction: a full web admin panel in `web/` was considered and set aside.
  The operator keeps the pipeline on the Mac/Windows for now, so the local GUI is the
  primary workstation and gets the investment. The Project-2 web panel stays as shipped
  (House-floor draft review only).

## Problem

The local processing GUI (`gui/`, FastAPI + Jinja + vanilla JS, run via `python -m gui`)
is feature-complete but has accumulated day-to-day friction. Three problems dominate:

1. **Too much clicking in review.** Every speaker mutation re-fetches and replaces the
   whole review panel and resets scroll, so working down a long meeting (e.g. a 48-speaker
   floor session) means being bounced to the top after every action. Identity actions cost
   an extra "reveal the panel" click before you can act.
2. **Weak library filtering.** Filters are client-side, AND-only, with no sorting UI, no
   date range, and only four coarse status buckets.
3. **Dated, inconsistent visuals.** No design tokens (raw hex repeated ~200 lines), no base
   layout template (every page duplicates its `<head>`), dense review cards.

A fourth, separate need: **House CDN floor meetings cannot be reviewed or voice-enrolled on
the Mac.** The weekly cloud cron writes them straight to the DB as `status='draft'` and
discards its working files, so no local meeting folder is ever created. Voice enrollment
requires local artifacts (the embeddings, and the profile DB `speaker_profiles.pkl`, both of
which live on the Mac). Today floor sessions can only be reviewed read-only in the web panel;
they can never get voice profiles.

## Goals

- Less clicking and smoother flow in the review page.
- Better library filtering and sorting.
- A clean, modern, consistent visual refresh — same page structures, no rewrite.
- Bring a House CDN floor session's artifacts to the Mac so it can be reviewed and
  voice-enrolled through the existing GUI flow.

## Non-goals (v1)

- Bulk-accept of confident guesses (considered, deferred).
- Keyboard shortcuts (global) and desktop/browser notifications (explicitly out).
- A deeper re-layout of the review page's interaction model (retheme + tidy hierarchy only).
- Any change to the `web/` admin panel or the ev-accounts API.
- Moving the pipeline itself to the cloud (compute stays on the Mac / Modal / the existing
  weekly cron).
- Shipping raw audio in the floor artifact (see the floor-import optimization below).

## Current state (verified 2026-09-13)

Stack: **FastAPI + Jinja2 server-render + vanilla JS, no framework, no build step.**
Run via `python -m gui` → uvicorn autoreload at `http://127.0.0.1:8000`.

- **Routes** all live in `gui/app.py` (`create_app()`), no APIRouter split.
- **Panel/tab model is a hybrid:** legacy routes (`/review`, `/run`, `/edit`, `/publish`)
  301-redirect into the tabbed shell `/meetings/{id}?tab=...`; tab switches are fragment
  swaps via `fetch` of `/meetings/{id}/panel/{name}` (`gui/static/workspace.js`); every
  in-panel POST action returns a 303 that the JS intercepts, re-POSTs via fetch, then
  **re-fetches and replaces the whole active panel** (`workspace.js` `loadPanel`), losing
  scroll.
- **Library** (`gui/templates/library.html` + `gui/static/library.js`): client-side filter
  over pre-rendered rows. Text search on a baked `data-search` attribute (name/city/org/id/
  race/guest — not date or status); `data-kind` exact match; `data-status` exact match over
  four buckets computed in `gui/models.py` (`live | ready | needs-review | processing`).
  Combine with AND. **No sorting UI**; order is fixed to state-file mtime desc
  (`gui/library.py`). Only the name anchor is clickable, not the row.
- **Review** (`gui/templates/panels/review.html` + `gui/templates/panels/_macros.html`
  `card()` macro, ~261 lines): a sticky player (YouTube iframe / HLS video / local video /
  audio; `hls.min.js` bundled), then "Needs attention" and "Confirmed" sections. A card is
  "Confirmed" only with a real name AND confidence ≥ 0.85 AND a trusted-tier id method
  (`gui/models.py`). The identity chooser has four radio-revealed panels (roster / local /
  unidentified / non-speaker) plus an "Also" block (accept / rename / merge / enroll).
  - Accept a guess: 1 click. Rename: ~2–3 steps (prefilled box + Save). Link a politician:
    ~4–5 interactions (reveal radio → focus → type ≥2 chars → 250ms debounce fetch
    `/api/politicians/search?q=`, server limit 10 → mouse-click a result form; no keyboard
    pick). Merge: 3–4 clicks (select target + Merge, + native `confirm()` on voice mismatch).
    Mark unidentified / non-speaker: 2 clicks each (reveal radio + button). Enroll: 1 click.
  - **No bulk actions.** Every mutation re-fetches the whole panel and resets scroll.
- **Review data layer:** `gui/review_api.py` (`load_review_page()`, `apply_*` mutations,
  `search_politicians_safe()`); politician search also in `gui/politicians.py`.
- **Visual:** one CSS file `gui/static/style.css` (~227 lines), **no `:root` design tokens**,
  raw hex repeated, ad-hoc rems. **No base layout template** — `library.html`,
  `workspace.html`, `new_meeting.html`, `dedup_confirm.html`, `discovery.html` each duplicate
  the doctype/head/stylesheet link. Components are CSS-class conventions; the only reusable
  partial is the `card()` macro. Minimal responsiveness (one 720px media query).
- **Floor pipeline (existing):** `run_local.py --house-floor <date>` sets `event_kind='floor'`,
  ingests from the House CDN (`src/house_cdn.py resolve_session`), and (in the cloud) publishes
  `status='draft'` via `--publish-as-draft`. The weekly workflow is
  `.github/workflows/house-floor-weekly.yml`. Draft rows carry the gate verdict/coverage in
  `processing_metadata`. `run_local.py --list-drafts` / `--promote <slug>` exist as CLI
  stopgaps. The site reads live from the API, so a promote is a pure DB status flip (no
  rebuild). The GUI already shells out to subprocesses for pipeline work.

## Design

Four workstreams. All stay on the current stack (Jinja + vanilla JS, no build step).

### 1. Keep-your-place review

Replace the "re-fetch the whole panel" behaviour with a per-card update.

- Add a fragment route that renders a single speaker's `card()` macro for a given meeting +
  speaker label, reusing the existing `_macros.html card()` and `review_api.load_review_page()`
  data (or a single-card variant).
- In `workspace.js`, after a successful mutation POST, swap only the affected card's DOM node
  with the freshly rendered fragment, leaving scroll untouched. Do not call the whole-panel
  `loadPanel` for card mutations.
- **Section-move simplification:** when an action makes a "Needs attention" card qualify as
  "Confirmed" (or vice versa), the card is restyled in place and the two sections re-sort only
  on the next full panel load / tab revisit. This keeps the JS simple and the operator's place
  fixed. Documented as a deliberate simplification.
- Whole-panel actions that are not per-card (if any remain) keep `loadPanel`.
- Optional (not required): after a card action, advance focus/scroll to the next
  "Needs attention" card.

### 2. Fewer clicks per speaker

Flatten the identity chooser so the common actions are directly actionable, removing the
preliminary "reveal the panel" click.

- On an unconfirmed card, the roster politician **search box is always visible** (no reveal
  radio needed to start typing).
- "Mark unidentified" and "Mark not a speaker" become single direct buttons on the card
  (no reveal step), keeping their existing undo affordances.
- Politician search: raise the result limit above 10 and allow picking the top match with
  **Enter** (standard typeahead behaviour, not a global keyboard shortcut). Mouse-click on any
  result still works. Improve match ordering if cheap.
- Accept stays 1 click. Rename, merge, local-person keep their inline forms; replace the merge
  native `confirm()` with an inline confirm consistent with the refreshed styling (see #4).
- These changes are card-macro + review-JS + a small `review_api`/`politicians` search tweak;
  no change to the underlying mutation semantics.

### 3. Richer library filtering + sorting

Stay client-side (all rows are in the DOM; scale is small).

- **Sortable columns:** clicking a header sorts by that column (date, processed, length,
  speakers, status, name), ascending/descending toggle. Default remains processing-recency
  (mtime) desc.
- **Date-range filter** on meeting date (from / to).
- **Finer status buckets:** extend the four current buckets with **failed**, and keep
  live / ready / needs-review / processing distinct and clearly labelled. Compute in
  `gui/models.py`; expose via `data-*`.
- **Quick-filter chips:** one-click presets ("Needs review", "Live", "Failed").
- Enrich the per-row `data-*` attributes (date, numeric length/speakers, status) to drive
  sorting and range filtering; extend `library.js` accordingly.
- Make the whole row clickable (not just the name anchor).

### 4. Retheme + tidy hierarchy

Same page structures; introduce the missing scaffolding, then refresh.

- **Design tokens:** add a `:root` token set to `style.css` — color palette (the existing
  green/amber/red/blue semantics as named variables), spacing scale, type scale, radii.
  Replace repeated literals with tokens.
- **Base layout template:** add `base.html` that all pages extend, eliminating the per-page
  `<head>` duplication across `library.html`, `workspace.html`, `new_meeting.html`,
  `dedup_confirm.html`, `discovery.html`.
- **Componentize** badges (`.gate`, `.live-badge`, `.stage`, `.pill`, `.identpill`) and
  buttons via consistent classes / small Jinja macros.
- **Refresh** palette, typography, and spacing for a cleaner, more legible look.
- **De-densify the review card** (`_macros.html`) hierarchy: clearer block separation, tighter
  grouping, and (optionally) the "Confirmed" section collapsed by default.
- Light responsiveness improvements only.
- Use the `frontend-design` skill at build time for the actual visual quality.

### 5. Floor-session import (House CDN → local)

Bring a floor session's artifacts to the Mac so the existing GUI review + enroll + publish
flow works on it. Chosen mechanism: **download the cloud run's files** (GitHub Actions
artifacts), reusing the compute rather than reprocessing.

**Cloud side** (`.github/workflows/house-floor-weekly.yml`):
- After each session is processed, upload its meeting folder as a GitHub Actions artifact,
  named by slug, with a retention window (default 30 days).
- **Optimization — JSON artifacts only, no raw audio/video.** Upload `transcript_named.json`,
  `diarization.json`, `embeddings.json`, `pipeline_state.json`, and summary/quality/topics.
  Rationale: voice enrollment reads the stored **embeddings**, not raw audio, so no audio is
  needed locally; review clip playback streams from the **House CDN URL** the meeting already
  carries (the same source the web panel plays). This keeps each artifact a few MB instead of
  ~1 GB, so GitHub storage quota and retention are not a concern.
- **Must verify at planning before locking the optimization** (fallbacks noted):
  1. Enrollment truly reads only `embeddings.json` / stored embeddings and never re-extracts
     from raw audio. If it needs audio → include audio in the artifact.
  2. The GUI review clip buttons can play time ranges from the CDN HLS URL (not only a local
     media route). If not → add CDN-range playback for imported meetings, or include audio.

**GUI side** (`gui/`):
- A "House floor sessions" view lists the available session artifacts. Discovery via the
  Mac's existing `gh` auth — the GUI shells out to `gh` (consistent with its subprocess
  pattern) to list workflow artifacts by slug/date. Optional enrichment with DB draft status /
  gate verdict is a nice-to-have, not required for v1.
- **"Pull to local":** download and unpack the chosen artifact into a local meeting folder
  under the meetings dir; it then appears in the library and opens in the workspace for review,
  **voice enrollment**, and publish.
- **Republish / overwrite:** the local floor slug matches the cloud floor slug (date-based),
  so publishing upserts the same DB row (publish is idempotent by slug) and flips
  `draft → published` on approval — no duplicate row. **Pin down at planning:** guard against
  a duplicate local directory or a duplicate DB row, and respect the known
  `meeting-slug-rename-fk` and `duplicate-meeting-dirs-same-video` hazards.

## Build slices (each shippable, TDD, subagent-driven)

1. **Visual foundation** — base template + design tokens + componentized badges/buttons +
   palette/type/spacing refresh. First, so later slices inherit the styling.
2. **Review flow** — per-card fragment swap (keep-your-place) + flattened identity chooser
   (fewer clicks, always-visible search, Enter-to-pick, inline merge confirm) + card
   de-densification.
3. **Library filtering & sorting** — sortable columns, date range, finer status (incl.
   failed), quick chips, enriched `data-*`, clickable rows.
4. **Floor-session import** — workflow artifact upload (JSON-only) + GUI list + pull-to-local,
   enabling floor review and voice enrollment; republish overwrite guard.

Slices 2 and 3 are independent of each other; both build on slice 1. Slice 4 is independent of
1–3 but benefits from the refreshed library (slice 3) for surfacing pulled sessions.

## Testing & process

- **Python / logic:** pytest + FastAPI `TestClient` — the new single-card fragment route,
  enriched `MeetingSummary` fields and status bucketing, the `gh`-shell-out import path (mock
  the subprocess), and the artifact-unpack-into-a-local-folder logic.
- **JS behaviour** (scroll preservation, in-place card swap, filters, sorting, typeahead
  Enter-pick): verified **live in the browser**, carefully. **Subagents must NOT start a
  server or touch port 8000** (a real run may be active) — they verify via pytest/TestClient
  only. Live JS verification is done by the operator or against an operator-run instance.
- **Workflow change** (`house-floor-weekly.yml`): validate YAML + the upload step; a full
  live run is a manual `workflow_dispatch` de-risk (per the existing floor-automation practice).
- Each slice: impl → spec-review → code-quality-review by subagents, per repo convention.
- Tests run under `.venv/bin/python`.

## Open items to verify at planning

1. Enrollment reads only stored embeddings (no raw audio) — gates the JSON-only artifact.
2. GUI clip playback can use the CDN HLS URL for imported meetings — gates the no-audio path.
3. `gh` artifact listing/download naming and auth on the Mac (public repo; `gh run download`).
4. Floor local slug == cloud floor slug, so republish upserts the same row (no duplicate);
   duplicate-dir / slug-FK guards.
5. Whether the GUI new-meeting/floor path or a dedicated import view is the cleaner home for
   "pull to local" (lean: a dedicated "House floor sessions" view).

## Out of scope (v1)

Bulk-accept; global keyboard shortcuts; notifications; deeper review-layout redesign; any
`web/` admin or ev-accounts change; moving the pipeline to the cloud; shipping raw audio in
the floor artifact.
