# Meeting-list first-sentence summary — design

**Date:** 2026-06-26
**Status:** Approved for planning
**Scope:** `web/` (CSS) + ev-accounts API (separate repo)

## Goal

On the homepage meeting list, replace the character-truncated summary preview
(which cuts mid-word with an ellipsis, e.g. "…Monroe County Commi…") with the
**full first sentence** of the meeting's executive summary — shown in its
entirety, never truncated.

This is follow-up #3 of three (the others — speaker count and non-YouTube
thumbnail extraction — are tracked separately and not part of this spec).

## Background

The homepage list ([web/app/MeetingListClient.tsx](../../../web/app/MeetingListClient.tsx)
→ `MeetingCard`) renders `meeting.summary_preview` for each row. That value comes
from the ev-accounts API's `GET /api/meetings` response (`summaryPreview`), mapped
in [web/lib/queries.ts:37](../../../web/lib/queries.ts). Today it is a character
truncation of the executive summary, so long sentences are cut mid-word.

Key facts established during design:

- **`summaryPreview` is computed at read time by ev-accounts.** `src/publish.py`
  writes only the full `summary` JSON (containing `executive_summary`) to
  `meetings.meetings.summary` — there is no `summaryPreview` column. So changing
  the preview is a pure serialization change: **no DB migration, no backfill.**
- **The detail page is unaffected.** The meeting detail page reads the full
  summary from a different endpoint — `GET /api/meetings/{id}/summary` →
  `executive_summary` (mapped from `executiveSummary`,
  [web/lib/queries.ts:57](../../../web/lib/queries.ts) / [:200](../../../web/lib/queries.ts)).
  This spec does not touch that endpoint, the stored `summary` JSON, or the
  full-summary view.
- **The list payload shape does not change.** The field stays `summaryPreview`;
  only its derivation changes. The web continues to consume it as
  `summary_preview`.

## Decision: where the extraction lives

**Approach A — extract in ev-accounts** (chosen over having the web extract from a
full summary shipped in the list payload). The web only receives the preview, so
it cannot recover a long first sentence that was already cut; computing it in the
API keeps a single source of truth and the smallest payload. The web change is
then a one-rule CSS tweak.

## The change

### ev-accounts API (separate repo — implemented there)

Where the list serializer currently builds `summaryPreview` by truncating the
executive summary to a character budget, replace that with **first-sentence
extraction**:

1. Take `executive_summary` (the same source string used today).
2. Trim leading/trailing whitespace.
3. Find the first sentence terminator — a `.`, `!`, or `?` immediately followed
   by whitespace or end-of-string — and return everything up to and including it.
4. **No length cap.** Return the entire first sentence however long.

Edge cases:
- Empty or missing `executive_summary` → return empty/null (the web already hides
  the preview line when the value is falsy — see web change below).
- Placeholder summaries (e.g. "No transcript segments available.") are themselves
  a single sentence and pass through unchanged. Acceptable.
- No terminator found (a fragment with no `.`/`!`/`?`) → return the whole trimmed
  string.

Accepted imperfection: a naive terminator search can mis-split on abbreviations
or decimals within the first sentence (e.g. "Dr.", "U.S.", "No. 5", "$1.5M"). For
LLM-generated prose this is rare; handling it is out of scope. If it proves
noticeable, the implementer may add a small abbreviation guard later, but the MVP
does not require it.

### web/ (this repo)

In [web/app/globals.css](../../../web/app/globals.css), the `.meetingPreview` rule
currently clamps to two lines:

```css
.meetingPreview {
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
```

Remove the clamp so the full first sentence renders without a mid-word cut — drop
`display: -webkit-box`, `-webkit-line-clamp`, `-webkit-box-orient`, and
`overflow: hidden`, leaving:

```css
.meetingPreview {
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 1.5;
}
```

Card height becomes variable with summary length, which is expected and matches
the YouTube-style list (rows already vary by title length). No component or
data-shape changes are needed in `MeetingCard`/`MeetingThumbnail`.

## Deploy ordering (non-blocking)

The two changes are independent and tolerant of either order:
- Web ships first: the (still char-truncated) preview renders un-clamped — a few
  longer rows until ev-accounts ships. Harmless interim.
- ev-accounts ships first: short first sentences render fine; a long one is
  clamped to two lines until the web change lands. Harmless interim.

No coordination required.

## Out of scope

- Speaker count on the meta line (follow-up #2 — pure ev-accounts API change).
- Non-YouTube thumbnail extraction (follow-up #1 — pipeline + storage + API).
- Any change to the stored summary, the summarize pipeline, or the detail page.

## Testing

- **web/:** the CSS change is verified in the browser preview — load the homepage
  and confirm the preview line shows a full sentence without a mid-word "…", in
  both light and dark mode and at the 375px mobile width (no overflow).
- **ev-accounts:** unit-test the first-sentence extractor in that repo against:
  a normal multi-sentence summary (returns sentence 1 only), a single long
  run-on first sentence (returns it whole, no cut), an empty summary (returns
  empty/null), and a no-terminator fragment (returns the whole string).
