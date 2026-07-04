# YouTube Metadata → Agenda Sections + Correct Attribution

**Date:** 2026-07-04
**Status:** Approved design, pending implementation plan

## Problem

Two issues, one shared root cause: the pipeline discards most of yt-dlp's metadata at ingest.

1. **Agenda/section boundaries are always LLM-guessed.** Many source videos already ship
   creator-authored chapters (or timestamped agenda items in the description). Today
   `src/ingest.py` pulls only the video `title` from yt-dlp's `extract_info`; the section
   structure is then re-derived by an LLM pass (`_classify_sections_interview` for interviews,
   `classify_sections` for council/meeting kinds). This costs a classification call and can
   disagree with boundaries the creator already provided.

2. **Wrong name in the interview executive summary.** The interview exec summary fills the
   `[outlet]` slot (`src/summarize.py:442-446`) with `event_orgs[0]` **or falls back to
   `source_title`** — the raw video title. So a clickbait title like
   *"LA Mayor's race EXPLODES with major UPDATE"* gets dropped into
   *"In an interview with ___, Nithya Raman discussed…"*. The actual interviewer
   (e.g. the YouTube channel/uploader "Brian Tyler Cohen") is available from yt-dlp but
   currently discarded.

Both are fixed by capturing `chapters`, `description`, and `uploader`/`channel` at ingest and
using them downstream.

## Decisions

- **Chapters are a hint, not authority** (option #3): pass creator chapters to the classifier as
  a prior, with a **strong bias toward keeping the creator's titles verbatim when neutral or
  descriptive**, rewriting only clickbait/promotional/ALL-CAPS hype titles, and adjusting
  boundaries only when clearly wrong.
- **Parse description timestamps as a fallback** (option #2) when yt-dlp reports no formal
  chapters. Only lines whose first non-whitespace token is a timestamp count.
- **Channel as outlet fallback, never the title** (option #1): the `[outlet]` slot resolves
  `event_orgs[0]` → captured channel → `"the interviewer"`. The video title is never used as the
  outlet. `--event-org` remains the authoritative manual override.
- **Applies to council/agenda videos too**, not just interviews — the chapter-hint mechanism is
  generic across both classifiers.
- **Drop intro-type entries.** A chapter whose start time is `0:00` is almost always a cold
  open / branding card ("CBS News California Investigates", "Intro"), not an agenda item, so it
  is removed during normalization — uniformly for both yt-dlp chapters and description-parsed
  ones.

## Design

### 1. Capture richer metadata at ingest — `src/ingest.py`

`normalize_audio` already makes one `skip_download` `extract_info` call solely to read `title`
(lines 100-102). Extend that **same** call (no additional network request) to also read:

- `uploader`, falling back to `channel` — the interviewer/outlet signal.
- `chapters` — a list of `{start_time, end_time, title}` dicts.
- `description` — used only by the fallback parser below; not persisted.

Derive a single normalized `chapters` list inside `normalize_audio`:

- **Primary:** use `info["chapters"]` when present and non-empty. Normalize each entry to
  `{start_time: float, end_time: float | None, title: str}`.
- **Fallback:** when `chapters` is empty/absent, scan `description` line by line with a
  **leading-anchored** regex:

  (The `≥2` threshold and the intro-drop below are counted/applied *after* extracting every
  matching line — the raw parser returns all timestamped lines; normalization filters.)

  ```
  ^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(\S.*?)\s*$
  ```

  Keep only matching lines. Require **≥2** matches for the description to be treated as a chapter
  source (otherwise return no chapters and let the LLM classify as today). Each match becomes
  `{start_time, title}` with `start_time` parsed from `MM:SS` or `HH:MM:SS`; `end_time` is the
  next entry's `start_time` (last entry's `end_time` is `None`).

  Worked example (real description, CBS News California governor high-speed-rail composite):

  ```
  00:00 CBS News California Investigates
  00:14 Steve Hilton on high-speed rail.
  02:04 Chad Bianco on high-speed rail.
  03:33 Tom Steyer on high-speed rail.
  04:16 Katie Porter on high-speed rail.
  05:59  Matt Mahan on high-speed rail.
  07:08 Xavier Becerra on high-speed rail.
  09:40 Antonio Villaraigosa on high-speed rail.
  12:16 Betty Yee on high-speed rail.
  14:05 Tony Thurmond on high-speed rail.
  ** Rep. Eric Swalwell appeared in an earlier version...
  COMING SOON | CBS News California Interactive Governor Candidate Guide
  ```

  → The parser extracts all **10** timestamped lines (`00:00`–`14:05`; note `05:59  Matt Mahan`
  with the double space is included). The prose paragraphs, the `**` line, and the `COMING SOON`
  line are all excluded because they do not start with a timestamp. Normalization then drops the
  `00:00` intro entry ("CBS News California Investigates"), leaving **9** agenda chapters
  (`00:14`–`14:05`).

`normalize_audio`'s return dict gains `source_channel` and `source_chapters` alongside the
existing `source_title`.

### 2. Persist metadata — `src/models.py` `ProcessingMetadata`

Add two optional fields, serialized in `to_dict`/`from_dict` so they survive the checkpoint and
are available when the summarize stage runs (including on resume):

- `source_channel: Optional[str] = None`
- `source_chapters: Optional[list[dict]] = None`  — each `{start_time, end_time, title}`

### 3. Wire ingest → metadata — `run_local.py`

Alongside the existing `source_title` assignment (~line 849-850), set
`meeting.processing_metadata.source_channel` and `.source_chapters` from the ingest metadata dict
when present.

### 4. Chapters as a classifier hint — `src/summarize.py`

Before classification in `generate_summary`:

- If `meeting.processing_metadata.source_chapters` is present, map each chapter's `start_time` to
  the nearest segment index (segments carry `start_time`; choose the segment whose `start_time` is
  closest to, and not after where avoidable, the chapter start). Build a candidate section list of
  `{start_segment, title}` (end_segment inferred from the next chapter's start_segment).
- Pass this candidate list into **both** `_classify_sections_interview(client, segments, chapters=…)`
  and `classify_sections(client, segments, chapters=…)` as an optional prior. When absent, both
  behave exactly as today.
- Prompt guidance added to both classifiers when a prior is supplied:

  > The creator provided these chapter boundaries and titles: {…}. Strongly prefer them. Keep each
  > title verbatim if it is neutral and descriptive. Rewrite a title only if it is clickbait,
  > promotional, or ALL-CAPS hype. Adjust boundaries only if clearly wrong.

The classifier still returns the existing `{type, title, start_segment, end_segment}` shape, so the
downstream summarize/exec-summary passes are unchanged.

### 5. Fix outlet attribution — `src/summarize.py:442-446`

Change the fallback chain in `_generate_interview_executive_summary` to:

```
outlet = event_orgs[0]  if event_orgs
         else source_channel  if source_channel
         else "the interviewer"
```

`source_title` is **removed** from this chain.

## Testing

- **Description parser** (`src/ingest.py`): the CBS example above → all 10 timestamped titles
  (parser keeps `00:00`); prose, `**`, and `COMING SOON` lines excluded. Also: `<2` matches →
  no chapters; `HH:MM:SS` timestamps parse correctly; leading/inner whitespace tolerated.
- **Intro drop** (`src/ingest.py`): `normalize_chapters` removes the `0:00` entry from both a
  yt-dlp chapter list and a description-parsed list.
- **Chapter → segment mapping** (`src/summarize.py`): chapter start times map to the expected
  segment indices given a synthetic segment list; boundaries inferred correctly for the last entry.
- **Outlet fallback chain** (`src/summarize.py`): asserts channel is used when `event_orgs` is
  empty, `event_orgs[0]` wins when set, `"the interviewer"` when both absent, and the video
  **title is never selected** in any case.
- **Chapters-present classification**: classifier receives the prior and preserves neutral titles;
  behavior with no chapters is unchanged.

## Non-goals (YAGNI)

- Not auto-filling `event_orgs` from the channel — `event_orgs` stays "a value a human set."
- Not changing how the subject *name* is inferred (already correct via speaker ID).
- Not persisting the raw video description.
- Not changing the web `source_title` display in `MeetingView.tsx` (a legitimate use of the title).
