# GUI Meeting Workspace Redesign — Design

**Date:** 2026-07-20
**Status:** Approved (pending spec review)
**Area:** `gui/` — the local FastAPI processing GUI (`python -m gui`)

## Problem

The processing GUI splits a single meeting's lifecycle across four separate,
full-page-reloading routes — `/meetings/{id}/run`, `/review`, `/edit`,
`/publish` — each with its own ad-hoc header nav. Moving through the natural
workflow (start → watch processing → review speakers → continue processing →
publish) means clicking "back" and re-navigating repeatedly; e.g. Publish is
only reachable from the Review page, and Continue-processing only from the Run
page. Everything the operator does to one meeting should live in one pane.

Separately, the auto-derived meeting ID — `{date}-{slug(meeting_type)}`, e.g.
`2026-02-10-regular-session` — carries too little context. It doubles as the
permanent public URL slug, yet doesn't say *which* council, *which* outlet, or
*which* race. Much of that context is already available (or auto-derivable) at
creation time and should be folded into the ID.

## Goals

1. Consolidate a meeting's Processing / Review / Details / Publish into a single
   **tabbed workspace** page with no full-page reloads between actions.
2. Make the auto-derived meeting ID **rich and kind-aware**, using context
   available at creation time (body/city/chamber; and for electoral/interview
   kinds, race and guest).
3. **Kind-aware new-meeting form:** show only the fields that apply to the
   chosen event kind.
4. **Richer, searchable library:** per-row context (city/body/org/race/guest) +
   a client-side filter bar; clicking a row opens the workspace on the
   stage-appropriate tab.
5. **Inline publish** (confirm + result inside the Publish tab) and
   **deep-linkable tabs** with live status updates.

## Non-Goals

- No SPA / framework rewrite. This stays FastAPI + Jinja + vanilla JS, matching
  the existing `run.js` / `review.js` idioms.
- **No change to existing meeting slugs.** The rich-ID scheme applies to *new*
  meetings only. ADR-0002 (frozen slug identity) is preserved.
- No change to `run_local.py` CLI/batch defaults (e.g. compute default stays
  `local` on the CLI; only the GUI form defaults to `modal`).
- No change to the published site (`web/`) or the `meetings.*` / `essentials.*`
  schemas. No DB migration.

## Approach

**Approach A — Fragment-swap workspace** (chosen over a visibility-toggle page
and over an SPA rewrite). One workspace shell page per meeting plus one
fragment endpoint per tab. Tabs and actions use `fetch` to load/refresh only the
affected panel; deep-linkable via `?tab=`. The current per-page templates are
refactored into reusable Jinja partials, which also removes the duplicated
header nav that exists in every template today.

The governing simplification: **an action re-renders the panel it lives in.**
Saving a speaker refreshes the Review panel in place; publishing turns the
Publish panel into the result; saving metadata confirms in the Details panel.
No 303 redirects, no reloads, no per-action refresh bookkeeping.

---

## Section 1 — The workspace shell

**One URL per meeting: `GET /meetings/{id}`.** A persistent header (never
reloads) sits above four tabs.

**Header** (updates live during a run, independent of the active tab):
- Title · date pill · kind pill · **review-gate pill** · **live-site pill**.
- A `⋯` kebab menu holds the secondary/destructive actions — **Clean up media**
  and **Delete** — so they're reachable from any tab but out of the way.

**Tabs:** Progress · Review · Details · Publish.
- **Review** shows a `●` attention dot when speakers still need attention.
- **Progress** — the stepper + live log tail + "Continue processing" +
  "Continue anyway (override gate)" + "Re-run a stage".
- **Review** — the media player + speaker cards (identical cards/actions to
  today: accept, rename, link, unlink, merge, mark unidentified/not-a-speaker,
  clip seek, enroll).
- **Details** — the metadata edit form (title / city / date / label / kind).
  Saves locally and pushes live if published; the ID/URL never changes.
- **Publish** — gate status + publish button, with confirm **and** result
  **inline** (no separate pages). The "Publish anyway" override appears **only**
  in the failed-gate state.

**Default tab** is computed from the meeting's stage: still processing →
Progress; ready (stage ≥ 4, "Identified") → Review. `?tab=publish` overrides.

**Non-ready panels** render a friendly placeholder rather than empty/error —
e.g. Review before the Identify stage shows "Speakers appear after the Identify
stage — currently Transcribing…".

**Old URLs 301-redirect** to the workspace: `/run`→`?tab=progress`,
`/review`→`?tab=review`, `/edit`→`?tab=details`, `/publish`→`?tab=publish`.

## Section 2 — Interaction model + refactor

**Server routes**
- `GET /meetings/{id}` — the shell (header + tab strip + `#panel` container). It
  **server-renders the default/`?tab` panel inline** on first load (no empty
  flash; the initial view works without JS).
- `GET /meetings/{id}/panel/{progress|review|details|publish}` — returns that
  panel's HTML fragment.
- `GET /meetings/{id}/status` — JSON for the header: `completed_stage`,
  `stage_label`, `running`, `review_status`, `is_live`, `attention_count`.
  Supersedes today's `/run/status`, which remains as an alias.
- Existing action POST endpoints keep their URLs but **return the re-rendered
  panel fragment** instead of a 303 redirect. Exception: **Delete** returns
  `{"redirect": "/"}` (it genuinely leaves the workspace).

**Client — one new `gui/static/workspace.js`** (absorbs `run.js` + `review.js`):
- Tab clicks → `pushState(?tab=…)` + fetch panel + swap; `popstate` restores.
- Poll `/status` while `running` → update header pills + Review `●` dot; stop
  when idle.
- Intercept in-panel form submits → `fetch` POST → swap the returned fragment
  into `#panel` → refresh header. Forms marked `data-navigate` (Delete) do a
  normal submit.

**Refactor (code-quality win)**
- `gui/workspace.py` (new): pure context assembly — `panel_context(name, id)`,
  default-tab-for-stage, and the header `status()` dict. Single source of truth
  called by both the `GET /panel/{name}` route and the POST action responses,
  reusing `load_review_page`, `_load_meeting_ctx`, `run_status`, `PipelineState`.
- Templates split into `templates/workspace.html` (shell) +
  `templates/panels/{progress,review,details,publish}.html` (content only). The
  speaker-card macro moves into `templates/panels/_macros.html`.
- **Retire:** `run.html`, `review.html`, `edit_meeting.html`,
  `publish_confirm.html`, `publish_result.html`, `run.js`, `review.js`.
  `dedup_confirm.html` stays (it belongs to the `/new` flow).

## Section 3 — Rich, kind-aware meeting ID

New rule: `{date}-{locus}-{label}`, where `label` is the slug of the event
label (unchanged) and `locus` is chosen by kind. **New meetings only; existing
slugs untouched.**

| Kind | `locus` source (first non-empty) |
|---|---|
| council / school_board | roster body-slug → else city |
| community_meeting | city → else event org |
| debate / forum | **race slug** → else event org → else city |
| news_clip / press_conference / podcast | **guest** and/or **race slug** → else event org |
| floor | *(none — the label already carries the chamber, e.g. "House Floor")* |
| other | city → else event org |

For interview kinds, the order is **guest before race**, label last; the event
org drops out of the *slug* when guest/race are present (still recorded as
"Produced by").

Example derived IDs:

| Scenario | Derived ID |
|---|---|
| council + roster, "Regular Session" | `2026-02-10-bloomington-city-council-regular-session` |
| council, no roster, Monroe, "Special Session" | `2026-02-10-monroe-special-session` |
| school board, Bloomington | `2026-02-10-bloomington-board-meeting` |
| House floor | `2026-02-10-house-floor` |
| Senate floor | `2026-02-10-senate-floor` |
| forum, race CA Governor | `2026-05-01-ca-governor-candidate-forum` |
| debate, race TX Senate | `2026-05-01-tx-senate-debate` |
| news_clip, guest Becerra + race CA Governor | `2026-05-01-becerra-ca-governor-interview` |
| news_clip, guest Becerra, no race, org CBS | `2026-05-01-becerra-interview` |
| podcast, guest Allred + race TX Senate | `2026-06-01-allred-tx-senate-podcast` |

Guards:
- **Overlap de-dup:** if the label already contains the locus token, drop the
  locus (no `bloomington-bloomington-…`).
- **Length cap:** the derived ID is capped (~80 chars); the label is truncated
  if needed.
- **Collision suffix unchanged:** the existing `-2/-3` bump still handles a
  genuinely different source landing on the same ID (`_unique_meeting_id`);
  rich IDs just make that far rarer.
- Result must pass `is_safe_meeting_id` (single safe path component); `slug()`
  guarantees this.

**Race** is chosen from a searchable picker backed by `essentials.races` (same
DB the GUI already queries for politician search). Picking a race both:
1. sets `race_id` → passed to `run_local.py` via `--race-id` (real publish
   linkage; today this is CLI-only), and
2. contributes the slugified race name to the ID (`race_slug`).

**Guest** is a free-text field for interview kinds; it is slugified into the ID
and needs no `run_local` flag (it exists only to enrich the slug).

**Form ID preview** (`new_meeting.js`) mirrors the new rule client-side; the
server (`runner.derive_meeting_id`) stays authoritative.

## Section 4 — Kind-aware new-meeting form

- **Always shown:** Source · Date · Event kind · Title · Event org(s).
- **Kind-gated:**
  - City — council/school_board (**required**); community_meeting/other (optional).
  - Body/roster — council/school_board.
  - Race picker — debate, forum, interviews.
  - Guest/subject — interviews.
  - Congressional Record chamber — floor. Selecting **Senate** sets the label
    default to "Senate Floor" (House → "House Floor"), so the ID reads
    `…-senate-floor` / `…-house-floor` without the operator hand-editing the
    label. Only auto-applied while the label is still a default value (never
    clobbers a hand-typed label — mirrors the existing `applyKindDefault` guard).
- **Under a "Processing & advanced" collapsible:** Compute · Diarizer · Clip
  window · event label + live ID preview.
- **New defaults (GUI form only):** Compute → **`modal`** (was `local`);
  Diarizer → `oss`. `run_local.py` CLI defaults unchanged.
- The live "how this looks on ontherecord.com" preview card stays.

Per-kind visibility is data-driven from `gui/formmeta.py` (a new
`FIELDS_BY_KIND`-style map), keeping the template and any server-side guard in
sync. The existing city-required server guard (`CITY_REQUIRED_KINDS`) stays.

## Section 5 — Richer, searchable library

- **Filter bar:** a search box (matches name / city / org / id) + **Kind** and
  **Status** dropdowns, filtering **client-side** (instant, no reload). "+ New
  meeting" and "Clean up all" move into this bar.
  - Status values: `Processing`, `Needs review`, `Ready to publish`,
    `Published/Live`.
- **Context subline** per row under the name — e.g. `Bloomington · Bloomington
  City Council`; `CBS · CA Governor 2026 · guest Xavier Becerra`. Pulled from
  `body_slug` / `event_orgs` / race / guest.
- **Race name is best-effort:** shown when cheaply available; omitted rather
  than blocking the library load on the DB.
- **Click-through** opens the workspace on the stage-appropriate tab
  (processing → Progress; ready → Review).

`MeetingSummary` + the scanner (`gui/library.py`) gain `event_orgs`,
`body_slug`, an optional `race_label`, and a `context_line` property. Race
labels are resolved best-effort via `gui/races.py`.

## Architecture — files

**New**
- `gui/workspace.py` — panel context assembly + default-tab + header status.
- `gui/races.py` — `search_races_safe(q)`; best-effort `race_labels(ids)`.
- `gui/templates/workspace.html`; `gui/templates/panels/{progress,review,details,publish}.html`; `gui/templates/panels/_macros.html`.
- `gui/static/workspace.js`.

**Changed**
- `gui/app.py` — shell route, `/panel/{name}`, `/status`, `/api/races/search`;
  action POSTs return fragments; Delete returns redirect JSON; 301s for old URLs.
- `gui/runner.py` — rich kind-aware `derive_meeting_id`; `RunParams` gains
  `guest`, `race_id`, `race_slug`; `build_run_command` adds `--race-id`.
- `gui/models.py` — `MeetingSummary` context fields + `context_line`.
- `gui/library.py` — scanner reads context fields; best-effort race label.
- `gui/formmeta.py` — per-kind field-visibility map; compute default.
- `gui/templates/new_meeting.html`, `gui/static/new_meeting.js` — kind-aware
  fields, guest/race, `modal` default, advanced collapsible, ID-preview rule.
- `gui/templates/library.html` (+ small filter script), `gui/static/style.css`.

**Retired:** `run.html`, `review.html`, `edit_meeting.html`,
`publish_confirm.html`, `publish_result.html`, `run.js`, `review.js`.

## Data flow

1. **Create:** `/new` form (kind-aware) → `RunParams` (incl. `guest`,
   `race_id`, `race_slug`) → `derive_meeting_id` (rich) → `_unique_meeting_id`
   → spawn `run_local.py` (with `--race-id` when set) → redirect to
   `/meetings/{id}` (opens Progress).
2. **Process:** `workspace.js` polls `/status`; header pills + Progress panel
   update live.
3. **Review:** speaker action POST → mutate → return re-rendered Review panel →
   header refresh.
4. **Details:** metadata POST → local write (+ Supabase if published) → return
   Details panel (saved).
5. **Publish:** POST → `apply_publish` → return Publish panel (inline result);
   header live pill refreshes.
6. **Library:** scanner reads local files (+ best-effort race labels) → rows
   with context; client-side filter; click → workspace at stage-appropriate tab.

## Error handling

- Unknown/unsafe meeting id → 404 (as today, via `is_safe_meeting_id` /
  `_load_meeting_ctx` returning `None`).
- Panel fetch/POST failures → `workspace.js` shows an inline error in the panel
  and leaves prior content intact; status polling backs off on transient errors
  (mirrors `run.js`).
- DB unavailable → race search + race labels degrade to empty/best-effort;
  library still renders; publish returns the existing structured `no_db`/`error`
  reasons.
- Not-ready panels → placeholder, never an error.

## Testing (extend `tests/test_gui_*.py`)

- `derive_meeting_id`: all kinds × guest/race/overlap/length/collision (pure).
- `panel_context`: correct data; `None` for unknown; placeholder when not-ready.
- Routes (FastAPI `TestClient`): shell renders default panel; `/panel/{name}`
  returns a fragment; old URLs 301; action POST returns fragment (not 303);
  Delete returns redirect JSON; `/status` shape.
- Library scanner: context fields populated; race label `None` when DB absent.
- Race search: monkeypatched DB (like the politician-search tests).
- Kind-aware form: server still enforces city-required kinds; guest/race are
  optional and affect only the ID.
- **Update** existing GUI tests asserting 303 redirects → assert fragments.

## Rollout

- **No DB migration.** Rich-ID scheme is new-meetings-only; existing slugs
  untouched — ADR-0002 preserved.
- Add **ADR-0003** documenting the rich kind-aware derivation rule alongside
  ADR-0002.
- Local tool: merge → restart `python -m gui` (auto-reload picks up changes).
  Old bookmarks handled by the 301s.

## Assumptions / to confirm in planning

- `essentials.races` exposes an id + a human-readable name/slug column to
  slugify and display. The politician search already proves the DB is reachable;
  the exact column is confirmed at planning time.
- Guest is GUI-only metadata (no `run_local` flag needed) — it exists solely to
  enrich the derived slug.
