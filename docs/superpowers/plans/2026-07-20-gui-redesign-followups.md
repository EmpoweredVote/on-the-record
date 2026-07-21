# GUI Redesign — Deferred Follow-ups

Small items surfaced by the code reviews across the three GUI-redesign plans
(workspace shell, rich IDs + kind-aware form, searchable library). None block
using the redesign today; each was consciously deferred. Ordered by value.

Branch: `feat/gui-meeting-workspace`. Spec: `2026-07-20-gui-meeting-workspace-redesign-design.md`.

---

## 1. Surface the inline publish-success line (Plan 1)

**What:** After a no-reload publish, the operator should see "✓ Published · N
segments · M speakers" in the Publish panel. Today `gui/app.py::publish_apply`
returns that success fragment, but `gui/static/workspace.js`'s form interception
re-fetches the whole Publish panel afterward (whose `#publish-result` slot in
`gui/templates/panels/publish.html` is left empty), so the operator only sees the
re-rendered panel with the "· already published (this will update it)" note.

**Fix:** In `workspace.js`, for the publish form specifically, swap the POST
response into `#publish-result` instead of (or in addition to) re-fetching the
panel; or have the re-fetched panel reflect the just-published result.

**Effort:** Small — one branch in the `workspace.js` submit handler.

---

## 2. Make the header attention-dot appear live (Plan 1)

**What:** The Review tab's `●` attention dot is only rendered into the header
(`gui/templates/workspace.html`) when `attention_count > 0` at initial page load.
The header is never re-rendered on tab swaps or status polls, so if attention
goes 0 → non-zero purely via `/status` polling, there's no `#attn-dot` element
for `workspace.js::refreshStatus` to reveal.

**Fix:** Always render the `#attn-dot` span in the header (hidden by default) so
the poll can toggle its visibility. Purely additive template + CSS change.

**Effort:** Small.

---

## 3. Surface the guest in the library context line (Plan 3)

**What:** For interview meetings, `MeetingSummary.context_line` shows org + race
but not the guest, because `guest` is captured only at creation time
(`gui/runner.py`), slugified into the meeting ID, and never persisted to
`pipeline_state.json`. The spec's Section 5 example included the guest.

**Fix (larger):** Persist `guest` — add a field to `src.checkpoint.PipelineState`
(+ `to_dict`/`_load`), have `run_local.py` write it, read it in
`gui/library.py::_summarize`, add a `guest` field to `MeetingSummary`, and
include it in `context_line`. Touches the pipeline, so out of the GUI-only scope
of Plan 3.

**Effort:** Medium — spans the pipeline state, not just the GUI.

---

## 4. Humanize the library Kind filter labels (Plan 3)

**What:** The Kind dropdown in `gui/templates/library.html` renders raw
snake_case values (`school_board`, `news_clip`, `community_meeting`). This is
consistent with how the app shows event kinds everywhere (the table column, the
new-meeting form), so it's a cosmetic inconsistency only — the Status dropdown
uses friendly labels.

**Fix:** A shared humanize (e.g. `.replace('_',' ').title()`) applied to the kind
option labels — ideally app-wide for consistency, not just the one dropdown.

**Effort:** Small (cosmetic).

---

## 5. Avoid the double `load_review_page` on the Publish-tab shell load (Plan 1)

**What:** Loading the workspace on `?tab=publish` (or any tab at stage ≥ 4) can
call `load_review_page` twice in one request — once in
`workspace.header_context` (for `attention_count`) and once in the panel context.
Accepted as fine for a local single-user tool.

**Fix (only if it becomes noticeable):** memoize the `ReviewPageData` per request,
or compute `attention_count` without a full page load.

**Effort:** Small; low priority.

---

## Accepted-as-designed (not follow-ups, recorded for context)

- **Guest slug uses the full name** (`xavier-becerra`, not `becerra`) in the
  meeting ID — deliberate; depends on operator input and is length-capped.
- **Action POSTs keep their 303 redirects** (workspace.js re-fetches the panel)
  rather than returning fragments — identical no-reload UX, simpler endpoints.
- **No-JS publish returns a bare unstyled fragment** — degraded-but-functional
  fallback for the (unsupported) no-JavaScript case; not worth styling.
