# Discovery review GUI — visual polish + shared components — design

Date: 2026-09-18
Status: Draft (pending spec review)
Area: The local FastAPI review GUI — `gui/templates/discovery.html`, `gui/static/style.css`, and small copy/logic touches in `gui/app.py` + `gui/discovery.py`. Branch `feat/discovery-gui-polish`, off `main`, in an isolated worktree (kept separate from the open Phase-3 flywheel PR #241 and from the Phase-4 session).

## Goal

Make the `/discovery` review page look finished instead of raw. Lift it onto the app's existing design-token system, apply the approved mockup's card hierarchy and iconed count chips (in the **light** palette), and make the Trust-vs-Approve distinction clear. Extract the reusable pieces (count chip, icons, badges, buttons) as shared classes other pages can adopt later.

Non-goals (explicitly out of scope this pass):
- **Dark mode.** The mockup is dark; we polish in light now and treat a full app-wide dark theme as a separate follow-up (a long-deferred want that touches every page).
- **A persistent cross-state sidebar tree** (the mockup's Indiana → counties tree). That is an information-architecture change; we keep the current single-state drill-down and only restyle its nav.
- **Rewriting other pages.** They gain the shared classes as *available*, not applied.
- Any change to discovery data flow, routes, or the DB.

## Findings that shape this

1. **The gap is presentation, not structure.** `discovery.html` already has the mockup's information architecture — a left nav of localities with pending counts, races grouped by level (county / city / school / statewide), each race expanding to outlet groups with per-lane rows and actions. What it lacks is polish: it is built almost entirely with inline `style="..."` attributes and generic `.pill`s instead of the mature token system in `gui/static/style.css` that the rest of the app (workspace, library, review, new-meeting) uses. So this is a CSS/refactor task, not a rebuild.
2. **"Trust outlet" and "Approve → quote source" are different, and the UI hides it.**
   - *Approve → quote source* (`POST /discovery/{id}/quote-source` → `approve_source_family`) is a **one-time** decision about this item (and its source family): mark them approved as quote sources.
   - *Trust outlet* (`POST /discovery/{id}/trust` → `trust_from_row`) is a **standing** decision about the whole outlet: set `source_outlets.trusted=true` so every future item auto-approves, and sweep the outlet's existing pending clips into approved now.
   - `trust_from_row` needs the row to have a registered outlet **or** a YouTube channel id. A web/hub source (a plain URL, no channel) can't be trusted — the call returns `(False, "could not register outlet")` and the page just flashes a small message. Combined with the raw styling, the result of a *successful* trust is also nearly invisible. Hence "it doesn't seem to do anything."

## Design

### 1. Shared primitives (added to `style.css`, light palette, reusable)

Built on the existing tokens (`--c-*`, `--sp-*`, `--r-*`, `--fs-*`). No new color system, no web fonts, no build step — consistent with the file's "native stack only" rule.

- **`.count-chip`** — an inline-SVG icon + a number (+ optional text), used for the four per-race counts (candidates / quote sources / ingested / pending). A `.count-chip.is-zero` variant renders muted (dim icon + number) so non-zero counts stand out, matching the mockup's dim "0"s.
- **Inline-SVG icon set** — a Jinja macro `icon(name)` emitting a small `<svg>` for a fixed set: `people`, `quote`, `mic`, `screen` (the four counts), plus `check` and `chevron`. Defined once (e.g. in `_ui.html` or a new `_icons.html`), currentColor-filled so it inherits chip color. No icon font.
- **`.badge` variants** — `.badge--trusted` (green, on the pass tokens) and `.badge--unknown` (neutral) for the outlet trust state shown on outlet group headers.
- **A small button system** — `.btn` base plus `.btn--primary`, `.btn--ghost` (secondary/neutral), and `.btn--danger`. Discovery adopts these; existing button classes elsewhere (`.enroll`, `.delete-btn`, `.mark`, …) are left untouched (no forced migration — YAGNI). The new set is what other pages *can* move to later.

### 2. Discovery page restyle (`discovery.html`)

Replace inline styles with the classes above and these page-specific classes (a `disc-` prefix where a name would otherwise collide with an existing class such as `.card`):

- **Race rows → cards.** Each `<details class="race-row">` becomes a `.disc-race` card: a header row with the race title on the left and the four `.count-chip`s right-aligned, a `chevron` that rotates on open, and a hover/open affordance. The COUNTY / CITY-LOCAL / SCHOOL / STATEWIDE section headers stay.
- **Outlet groups → sub-cards.** `.disc-outlet` holds the outlet name + a trust badge (`.badge--trusted` / `.badge--unknown`) on the right, with the lane rows nested inside — mirroring the mockup (e.g. "WTIU News · PBS `trusted`" over its lane rows).
- **Lane / source rows.** `.disc-item` gives each source row consistent spacing, a small kind pill, a meta line (channel · duration · date · kind · tier · confidence · via), the "why" line, and the action row using the new `.btn` variants. The prior-cycle badge and the auto-kept "hidden N" summary are preserved.
- **Left nav → styled sidebar.** `.disc-nav` styles the existing per-state locality nav: sticky, with an active-locality highlight and `✓ done` / `N pending` badges. IA unchanged (still `/discovery` → choose state → `/discovery?state=XX`).
- **Header + legend.** A cleaner header summary line and a small one-row legend mapping each count icon to its meaning.

### 3. Trust / Approve clarity fix (small logic + copy)

- **Only render "Trust outlet" where it can work** — for outlet groups backed by a real outlet or a YouTube channel (the group already knows this; expose a boolean on the group so the template can gate the button). For channel-less web/hub groups, omit it instead of offering a button that fails.
- **Label the distinction.** Keep a one-line helper under "Trust outlet" ("auto-approve everything this outlet sends from now on"); "Approve → quote source" reads as the one-time action. After trusting, the outlet shows the `trusted` badge, so the result is visible.
- This changes button *visibility and copy only* — the routes and `trust_from_row` / `approve_source_family` behavior are unchanged.

## Constraints

- **Light palette only.** Use the existing `:root` tokens; add no dark-theme blocks this pass.
- **No web fonts, no build step, no new runtime dependency.** Inline SVG for icons; native font stack.
- **Preserve existing tests.** `tests/test_gui_discovery.py` render tests assert on text and form actions (route paths, "prior cycle", the Trust/Approve forms), not inline styles. The restyle keeps that text and those form `action`s; any test that happens to assert an inline-style detail is updated to assert the equivalent class.
- **Accessibility basics.** Icons that carry meaning get an accessible label (title/aria); the SVG-only count chips include a text number, so they aren't icon-only.
- **`discovery.html` overlaps the open PR #241** (which adds the "Add as hub" flywheel form to the `row_actions` macro). That form is absent on `main`, so it is out of scope here; whichever PR merges second reconciles the `discovery.html` change (most new styling is additive in `style.css`, which PR #241 does not touch). The new `.btn` classes will be available for the flywheel form to adopt at reconciliation.

## Testing / verification

- Run the existing GUI test suite (`tests/test_gui_discovery.py`, `tests/test_gui_coverage.py`) — green throughout.
- Run the GUI dev server in the browser preview and screenshot the real rendered `/discovery` page (pending view, a state view with races expanded, the auto-kept and deferred views) to iterate against the mockup — showing the actual page, not a mockup.
- Verify the Trust/Approve copy and the "Trust outlet" gating render correctly for a channel-backed group vs. a channel-less group.

## Decided (from this session's questions)

- **Theme:** polish in the existing **light** palette now; full dark mode is a separate follow-up.
- **Scope:** restyle the discovery page **and extract reusable primitives** (count chip, icons, badges, buttons) as shared classes; do not rewrite other pages.

## Open questions / follow-ups

- A full app-wide dark theme (make the tokens theme-aware + a toggle) — deferred, would then let discovery match the dark mockup exactly.
- The mockup's persistent cross-state sidebar tree (states → counties in one nav) — an IA change, deferred.
- Migrating the app's other button classes onto the new `.btn` system — optional later cleanup.
