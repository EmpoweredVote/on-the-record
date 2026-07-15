# Podcast & Web Audio-Interview Ingestion — Design

**Date:** 2026-07-15
**Status:** Approved design, pending implementation plan
**Scope:** `on-the-record` repo only (front-end audio playback deferred)

## Problem

Candidates increasingly appear on podcasts and local public-radio interview
shows. These are audio-only (not YouTube videos), and each host publishes them
differently:

- **Buzzsprout** and similar podcast hosts expose episodes via RSS feeds
  (e.g. `https://feeds.buzzsprout.com/1414123.rss`,
  `https://whatsnextlosangeles.buzzsprout.com`). Episode pages advertise their
  feed via standard RSS autodiscovery.
- **Public-radio station CMSes** (e.g. Indiana Public Media, `ipm.org`; KUER,
  `kuer.org` — both on NPR's Brightspot platform shared by many stations)
  publish an episode as an article page with structured JSON-LD metadata, a
  direct MP3 on NPR's distribution CDN, **and (often) an already-cleaned
  transcript** in the article body. Validated against two independent stations
  (IPM `cpa.ds.npr.org/s385/...`, KUER `.../s213/...`); the shape is
  consistent.

Today the pipeline ingests content through `fetch_source_metadata()`
(`src/ingest.py`), which only knows how to ask **yt-dlp**. yt-dlp does not
reliably resolve these audio sources or their metadata (published date, show
notes, artwork) — which is exactly the metadata we care about.

The transcription core is already audio-capable: `normalize_audio()`
(`src/ingest.py`) accepts a URL or local file, audio or video, and ffmpeg
strips it to 16 kHz mono WAV. The gap is **resolution and metadata**, not
transcription.

## Goals

- Process **one episode at a time** from a **human-pasted episode page URL**
  (the URL you'd copy from a browser), matching today's one-meeting-at-a-time
  GUI/CLI flow.
- Work for **any RSS-autodiscovering podcast host** (general, not
  Buzzsprout-only) and for **NPR/Brightspot station pages** (starting with
  `ipm.org`).
- Capture real structured metadata: published **date**, **show notes /
  description**, **outlet** (show/station name), and **artwork**.
- When a source provides a **clean transcript**, use it to improve accuracy
  **without** discarding the timestamp-based data model (segments, quotes,
  clips).
- Degrade gracefully: audio-only sources get an artwork thumbnail instead of a
  video frame; an unrecognized URL falls back to the existing generic download
  path rather than erroring.

## Non-Goals (explicit)

- **Front-end audio playback.** We store the MP3 as `playback_url` with a new
  `playback_kind = 'audio'` so the data is ready, but the `<audio>` player lives
  in ev-accounts/web and is a separate follow-up.
- **Feed subscriptions / auto-pull.** No monitoring feeds for new episodes and
  no relevance filtering. One episode at a time, initiated by a human.
- **A "pick from feed list" UI.** Not in this spec.
- **Text-only civic communications** (e.g. a mayor's newsletter / municipal
  topic page like `cityoflinton.in.gov`). These have no audio and no interview,
  so nothing in the audio-resolver pipeline applies. They are a separate future
  source class — a "text source" that could feed the quotes pipeline directly —
  with their own open questions (is a newsletter a first-person on-the-record
  statement? how is authorship/attribution established?). Deliberately **not**
  in scope here; captured so the idea isn't lost.

## Architecture: pluggable source resolvers

Introduce a small set of **source resolvers**. Each takes a URL and returns one
normalized result; everything downstream is unchanged and consumes that result.

```
ResolvedSource {
  audio_url      # direct MP3/M4A to feed the existing pipeline
  title          # episode/article title
  date           # published date (YYYY-MM-DD)
  outlet         # show / station name  -> source_channel
  description    # show notes / article body -> summarizer hint + chapter parse
  image_url      # episode / show / og artwork -> thumbnail (no video frame)
  transcript?    # OPTIONAL clean transcript text, when the source provides one
}
```

A dispatcher selects a resolver by URL / page shape — mirroring how the **CATS
TV** source is already a first-class branch in `download_from_url`
(`src/download.py`). If no resolver confidently resolves the URL, fall back to
the existing generic download path.

### First two resolvers

| Resolver | Sources | Metadata from | Transcript? |
|---|---|---|---|
| **Podcast RSS** (`src/podcast.py`) | Buzzsprout, Libsyn, Simplecast, Megaphone, and any host with RSS autodiscovery | episode page → autodiscovered RSS feed → matched `<item>` | No — audio only |
| **Brightspot/NPR CMS** (`src/brightspot.py`) | `ipm.org`, `kuer.org`, and other public-radio stations on NPR's Brightspot platform | JSON-LD (episode `@type` varies: `RadioEpisode` / `PodcastEpisode` / `AudioObject`; date from `NewsArticle.datePublished`) + `og:` tags + direct `cpa.ds.npr.org` MP3 | **Yes, when present** — article-body `<p>` paragraphs |

New sources later = one new resolver returning `ResolvedSource`. No pipeline
changes.

## Component detail

### 1. Podcast RSS resolver — `src/podcast.py`

`resolve_podcast_episode(url) -> ResolvedSource`:

1. **Fetch the episode page** (`requests`/`httpx`, already deps).
2. **Discover the feed** — parse HTML with `bs4` for
   `<link rel="alternate" type="application/rss+xml" href="…">`.
3. **Parse the feed** with `feedparser` (new dependency — handles the iTunes
   namespace and real-world malformed feeds far better than hand-rolled
   `xml.etree`).
4. **Match the episode** — find the `<item>` whose `link` or `guid` matches the
   pasted page URL (normalized comparison; fall back to enclosure host + slug).
5. **Map** feed fields → `ResolvedSource`: enclosure → `audio_url`, item title →
   `title`, `pubDate` → `date`, channel/show `<title>` (or `<itunes:author>`) →
   `outlet`, item `<description>`/`content:encoded` → `description`, item
   `<itunes:image>` (else channel image) → `image_url`. `transcript` = None.

### 2. Brightspot/NPR CMS resolver — `src/brightspot.py`

`resolve_brightspot_episode(url) -> ResolvedSource`:

1. **Fetch the page.**
2. **Parse JSON-LD** blocks: `NewsArticle.datePublished` → `date` (the episode
   block's date is often absent), `headline` / episode `name` → `title`,
   `author[].name` → contributor, `og:site_name` → `outlet` (e.g. "Indiana
   Public Media", "KUER"). Accept any episode `@type` (`RadioEpisode`,
   `PodcastEpisode`, `AudioObject`) — the type varies by station.
3. **Find the MP3** — the episode's direct enclosure on the NPR distribution
   CDN (`cpa.ds.npr.org/...mp3`), disambiguating the current episode's file from
   sidebar/related episodes (match against the page's canonical `og:url` /
   date / headline slug).
4. **Extract the transcript** — the article-body `<p>` paragraphs (present in
   served HTML; no JS rendering needed). → `transcript`.
5. **Artwork** — `og:image` → `image_url`.
6. **Description** — the article summary / `og:description` → `description`.

### 3. Transcript-as-corrector — `src/reconcile.py`

The one genuinely new pipeline component. When `ResolvedSource.transcript` is
present:

- Save it as a `reference_transcript.txt` artifact and record it in
  `processing_metadata` (needed so the later summarize stage can read it).
- **Run the normal Whisper + diarization transcribe stage unchanged** — this is
  what produces timestamped, speaker-attributed segments that quotes and clips
  depend on.
- **After** transcribe, run a reconciliation pass: keep Whisper's timestamps and
  diarized speaker turns, but use the clean reference transcript as an LLM
  reference to correct proper nouns, mishearings, and punctuation,
  segment-by-segment.

This is *additive* to transcription (unlike `src/vtt_align.py`, which *replaces*
Whisper when timestamped captions exist). Timestamps and speaker turns come from
audio; only the words are improved. **Highest-risk piece — prototype first.**

Where it hooks: a reconciliation step that runs as part of / immediately after
the `TRANSCRIBED` stage when a reference transcript exists, checkpointed like
other stage work so `--resume`/`--redo` behave. (Exact stage placement to be
finalized in the implementation plan against `src/checkpoint.py`'s stage model.)

### 4. Metadata mapping into the existing model

Each resolver fills `ProcessingMetadata` the same way `fetch_source_metadata()`
does today, so the rest of the pipeline is source-agnostic.

| `ResolvedSource` | Lands in | Consumed by |
|---|---|---|
| `title` | `ProcessingMetadata.source_title` | meeting title default |
| `outlet` | `ProcessingMetadata.source_channel` | `_resolve_outlet()` → "In an interview with [outlet]" (`src/summarize.py`) |
| `date` | `meeting.date` | `meeting_id`, DB `meeting_date` |
| `description` | `ProcessingMetadata.source_chapters` (via existing `parse_description_chapters` / `normalize_chapters`) **and** summarizer hint | Stage 5 summarize |
| `image_url` | `thumbnail.jpg` → `meeting-thumbnails` bucket | site meeting card |
| `audio_url` | `meeting.audio_source` → DB `playback_url` (new `playback_kind = 'audio'`) | playback (deferred) |
| `transcript` | `reference_transcript.txt` + `processing_metadata` | `src/reconcile.py` |

### 5. Thumbnail without video — `src/thumbnail.py`

`find_video_file` only recognizes video extensions. Add a fallback: when the
resolver supplies `image_url`, download that artwork to `thumbnail.jpg` and
upload it to the existing `meeting-thumbnails` bucket via `src/storage.py` — no
ffmpeg frame extraction. Existing best-effort behavior (review clip falls back
to `audio.opus`) already covers audio-only sources.

### 6. Content type & dispatch

- Add a `podcast` value to `event_kind` in `src/event_kinds.py`, and include it
  in `_INTERVIEW_KINDS` (`src/summarize.py`) so it uses the interview
  summarization path (outlet-attributed exec summary keyed on `source_channel`).
- Add its label/help in `gui/formmeta.py` and set its gate/section config in
  `src/config.py` alongside the other interview kinds.
- Add the resolver-detection branch to the download/metadata dispatch
  (`download_from_url` in `src/download.py`, and the metadata path parallel to
  `fetch_source_metadata` in `src/ingest.py`), mirroring the CATS TV branch.
  Detection is best-effort: recognize known host/feed shapes, otherwise attempt
  resolution and only claim the URL if a resolver actually succeeds; on failure
  fall back to the existing generic path.

### 7. Source dedup & playback

- Extend `src/source_key.py` to normalize podcast enclosure URLs / GUIDs and
  Brightspot canonical URLs to stable identities, so the GUI's "already
  processed this source" check keeps working.
- Add `'audio'` to the DB `playback_kind` values and to `resolve_playback()`
  (`src/publish.py`); store the enclosure MP3 as `playback_url`. (No front-end
  rendering in this spec.)

## Data flow (end to end)

```
episode page URL (pasted)
   │
   ▼
dispatcher → resolver (podcast RSS | brightspot CMS | … | generic fallback)
   │
   ▼
ResolvedSource { audio_url, title, date, outlet, description, image_url, transcript? }
   │
   ├─ metadata → ProcessingMetadata (source_title/source_channel/source_chapters)
   ├─ image_url → thumbnail.jpg → meeting-thumbnails bucket
   ├─ transcript? → reference_transcript.txt + processing_metadata
   │
   ▼
existing pipeline: normalize_audio → diarize → transcribe (+ reconcile if reference)
                    → identify → summarize (interview path) → export → publish
```

## Error handling

- **No feed discovered** (podcast resolver): fall through to the next resolver /
  generic download path; surface a clear message rather than silently producing
  wrong metadata.
- **Episode not matched in feed**: fall back to enclosure host + slug match; if
  still unmatched, use page-level metadata (og tags) and continue.
- **No MP3 found** (either resolver): hard error with the page URL — there's
  nothing to process.
- **Reconciliation failure / low overlap** between reference transcript and
  Whisper output: skip reconciliation and keep the raw Whisper transcript;
  log a warning. Never let a bad reference corrupt the timestamped segments.
- **Artwork download fails**: proceed with no thumbnail (best-effort, as today).

## Testing

- **Resolver unit tests** with saved HTML/RSS fixtures (Buzzsprout feed +
  episode page; `ipm.org` article page) → assert exact `ResolvedSource` fields.
  No network in tests.
- **Dispatch/detection tests**: known host shapes route to the right resolver;
  unknown URL falls back to generic path.
- **Source-key tests**: podcast enclosure / Brightspot URL → stable dedup key.
- **Reconciliation tests**: given fixed Whisper segments + a reference
  transcript, corrected segments preserve timestamps and speaker turns and fix
  seeded proper-noun errors; low-overlap reference is skipped, not applied.
- **Chapter-parse reuse**: description with timestamped lines → chapters via
  existing `parse_description_chapters`.

## Key files touched

| File | Change |
|---|---|
| `src/podcast.py` | **new** — RSS resolver |
| `src/brightspot.py` | **new** — NPR/Brightspot CMS resolver |
| `src/reconcile.py` | **new** — transcript-as-corrector |
| `src/download.py` | resolver-detection branch in `download_from_url` |
| `src/ingest.py` | metadata path that consumes resolvers alongside `fetch_source_metadata` |
| `src/source_key.py` | normalize podcast/Brightspot identities |
| `src/event_kinds.py` | add `podcast` kind |
| `src/summarize.py` | add `podcast` to `_INTERVIEW_KINDS` |
| `src/thumbnail.py` | artwork-URL fallback |
| `src/publish.py` | `audio` playback kind in `resolve_playback` |
| `src/config.py` | gates/section config for `podcast` |
| `gui/formmeta.py` | `podcast` labels/help |
| `requirements.txt` | add `feedparser` |
| DB migration | add `'audio'` to `playback_kind` comment/allowed values |

## Open items for the implementation plan

- Exact stage placement of reconciliation within `src/checkpoint.py`'s
  `PipelineStage` model (sub-step of `TRANSCRIBED` vs. its own checkpoint).
- Reconciliation prompt/algorithm details and the overlap threshold below which
  it is skipped — prototype first.
- Brightspot MP3 disambiguation heuristic: validated against two stations
  (IPM `s385`, KUER `s213`) — current episode is the first `cpa.ds.npr.org` MP3,
  followed by related-episode links; match the current episode against the
  canonical `og:url` / date / headline slug. Confirm the heuristic holds on a
  third station before treating it as settled.
