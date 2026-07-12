# GUI: Autopopulate source metadata from a pasted URL

**Date:** 2026-07-04
**Status:** Approved — ready for planning

## Problem

In the processing GUI's new-meeting form (`/new`), the operator pastes a
YouTube (or other yt-dlp-supported) URL and then re-types information the video
already carries: the publish date, the title, and the producing org. The
pipeline *already* fetches title, channel, and chapters from yt-dlp during
processing ([src/ingest.py:176-185](../../../src/ingest.py)), but only after the
run starts — the form never sees it. The operator does redundant lookup work
before every launch.

## Goal

When the operator pastes a yt-dlp-supported URL into the form's source field,
automatically fill the empty **Date**, **Title**, and **Event org(s)** fields
from the video's own metadata, leaving the operator free to edit or override.

## Non-goals

- Guessing `event_kind` from channel/title keywords.
- Displaying source duration.
- Mapping channel names to canonical org names (channel/uploader is used
  verbatim).
- Fetching metadata for local file paths or non-video URLs (nothing to fetch).

## Behavior contract

- **Trigger:** the fetch fires on `blur`/`change` of the source-URL field,
  debounced, and skips re-fetching when the value is unchanged from the last
  fetch.
- **Only yt-dlp URLs:** if the input is a local path or a non-video URL, no
  fetch happens and no note is shown.
- **Never clobber:** autofill writes only to fields the operator has left empty.
  Any value already typed (including values from a prior fetch the operator then
  edited) is preserved.
- **Fail quiet:** if the fetch errors, times out, or the video is private/
  unavailable, the form stays as-is and a small inline note appears near the URL
  field ("Couldn't fetch details — fill in manually"). No blocking dialog, no
  console-only failure.
- **Preview stays in sync:** after autofill, the existing `refresh()` runs so
  the preview card and derived meeting id reflect the new values.

## Design

Single source of truth for "what we pull from a video," reused by both the
processing pipeline and the form.

### 1. Shared helper — `fetch_source_metadata(url) -> dict`

Extract the yt-dlp info block currently inline in
`ingest.normalize_audio` into a standalone function (in `src/ingest.py`).
It runs `yt_dlp.extract_info(url, download=False)` with
`{"quiet": True, "no_warnings": True, "skip_download": True}` **once** and
returns a normalized dict:

```python
{
    "title": str | None,        # info["title"]
    "channel": str | None,      # info["uploader"] or info["channel"]
    "upload_date": str | None,  # "YYYY-MM-DD", normalized from yt-dlp's "YYYYMMDD"
    "chapters": list[dict],     # via existing normalize_chapters(info)
    "duration": float | None,   # info["duration"], seconds (captured for reuse; unused by form)
}
```

- Missing/blank fields normalize to `None` (or `[]` for chapters).
- `upload_date` normalization: yt-dlp returns `"20260210"`; convert to
  `"2026-02-10"`. Malformed/short values → `None`.
- Any exception inside the helper is caught and yields an all-empty dict, matching
  the pipeline's current best-effort behavior (the `try/except: pass` today).

`normalize_audio` is refactored to call `fetch_source_metadata` for its
`source_title` / `source_channel` / `source_chapters` values, so pipeline and
form behavior can never drift.

### 2. GUI endpoint — `GET /api/source-meta?url=...`

Thin route in `gui/app.py`:

- Guard with `download.is_ytdlp_url(url)`. If false (local path or non-video
  URL), return `{"date": null, "title": null, "event_org": null}` with `200`
  (empty payload — the client treats it as "nothing to fill").
- Otherwise call `fetch_source_metadata(url)` and return:

  ```json
  { "date": "<upload_date>", "title": "<title>", "event_org": "<channel>" }
  ```

- The route is a normal `def` (not `async def`) so FastAPI runs the ~1-3s
  blocking yt-dlp call in its threadpool and doesn't stall other requests.

### 3. Client wiring — `gui/static/new_meeting.js`

- Add a debounced handler on the source field (`#f-input`) for `blur`/`change`.
  Track the last-fetched URL string; skip if unchanged or empty.
- Skip the fetch client-side too when the value doesn't look like an http(s)
  URL (cheap guard; the server is still authoritative).
- While fetching, show a small note element near the URL field: "Fetching video
  details…".
- On success, for each of `#f-date`, `#f-title`, `#f-orgs`: set the value
  **only if the field is currently empty** (`.value.trim() === ""`). Then call
  the existing `refresh()`.
- On any error or empty payload with a real URL, show "Couldn't fetch details —
  fill in manually". Clear the note on the next successful fetch.

The note lives in `new_meeting.html` as an initially-empty `<small>` under the
source-URL label.

## Testing

No live network in any test.

- **`fetch_source_metadata` (unit):** feed a stubbed yt-dlp info dict (patch
  `yt_dlp.YoutubeDL`); assert title/channel/duration pass through, `upload_date`
  `"20260210" → "2026-02-10"`, missing fields → `None`, malformed date → `None`,
  and an extractor exception → all-empty dict.
- **Endpoint (unit, via `TestClient`):** monkeypatch `fetch_source_metadata`;
  a non-ytdlp input (`/path/to.mp4`) returns the empty payload; a stubbed ytdlp
  URL returns `{date, title, event_org}` with `event_org` == channel.
- **Regression:** existing `ingest`/`normalize_audio` tests still pass after the
  refactor (the helper feeds the same three values the pipeline used before).

## Files touched

- `src/ingest.py` — add `fetch_source_metadata`; refactor `normalize_audio` to
  use it.
- `gui/app.py` — add `GET /api/source-meta`.
- `gui/static/new_meeting.js` — debounced fetch + empty-only autofill.
- `gui/templates/new_meeting.html` — inline note element under the URL field.
- `tests/` — new tests for the helper and the endpoint.
