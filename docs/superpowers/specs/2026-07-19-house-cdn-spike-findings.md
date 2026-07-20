# House-CDN ingestion — spike findings (2026-07-19)

Read-only spike to resolve how the House Clerk delivers floor video and whether our stack can ingest + play it, before designing the first real federal prod publish.

## TL;DR

Much smaller than feared. **The web player needs zero changes** — the CDN exposes an HLS manifest, which `resolve_playback` → `file`/`hls` kind → `FilePlayer` (hls.js) already plays. Date→stream resolution is a single clean JSON-LD endpoint. Video is public-domain, durable, CORS-open, VOD-seekable.

## 1. Date → stream resolution

`GET https://liveproxy-azapp-prod-eastus2-003.azurewebsites.net/broadcastevents/<YYYYMMDD>`
returns a schema.org **BroadcastEvent** JSON-LD:

- `name` — e.g. "LEGISLATIVE DAY OF JULY 16, 2026"
- `superEvent` — `{congressNum:"119", sessionNum:"2"}`
- `startDate` (gavel, e.g. `2026-07-16T09:00:00`), `endDate` (e.g. `12:15:32`)
- `rights` — **"Pursuant to Title 17 Section 105 … in the public domain."**
- `asset.name` — encoder start timestamp (e.g. `2026-07-16T08-51-14`)
- `asset.files[]` — the media, in **east + central** mirrors:
  - `DASH  manifest.mpd`
  - **`HLS   manifest.m3u8`**  ← what we use
  - `WebVTT captions.vtt`      ← Clerk's live captions (potential seed/validation)
  - each URL carries a `#t=<offset>` hash (encoder-start → gavel offset, e.g. `459.387`)

Supporting endpoints: `GET /sessiondays/` → every session day as `{_id:"YYYYMMDD", startDate}` (discovery/validation). `live.house.gov/?date=YYYY-MM-DD` is the human citation page.

## 2. Playback format — the key finding

- CDN host: `houseliveprod-f9h4cpb9dyb8gegg.a01.azurefd.net` (Azure Front Door), path `/east/<encoder-ts>/manifest.m3u8`. Durable, public.
- HLS master playlist: multi-bitrate (240p–720p) + separate `audio_0.m3u8` audio group; `#EXT-X-VERSION:6`.
- Response headers: **`access-control-allow-origin: *`** (cross-origin playback from our web app works), `accept-ranges: bytes` (seekable). Session ended → VOD.
- **Web player: no changes.** `src/publish.py resolve_playback` returns `hls` for `.m3u8`; `web/.../players/FilePlayer.tsx` handles `kind:"hls"` natively (Safari) or via hls.js; `MeetingView` already renders it for `playback_kind === "hls"`.

## 3. Audio for ASR

The existing `download.py` handles yt-dlp sites + direct requests, but **not** a raw HLS manifest (a `.m3u8` would download as a text file). New small piece: extract audio from the manifest via **ffmpeg** (`ffmpeg -i manifest.m3u8 -vn …`, or the `audio_0.m3u8` sub-playlist). WebVTT captions are also available but our Whisper + diarization pipeline remains the timestamp source of truth.

## 4. Timeline / offset

Vote timestamps come from ASR-anchoring the audio, so they land in the **CDN manifest's own timeline** (encoder t=0). The published playback is that same manifest, so click-to-seek is directly correct with **`clip_start_seconds = 0`** for a full-source ingest — no offset math needed for seek. The `#t=<offset>` is only the encoder-start→gavel gap (useful for trimming a pretty start, not for seek correctness).

## 5. Session length

The sampled recent session (2026-07-16) was **~3.4 h** (09:00–12:15), not 12 h. Recent days are often this size ⇒ a first full-session ingest is directly tractable; silence-cutting is a scale optimization for long days, not a hard prerequisite for the first publish.

## 6. CREC availability

`CREC-2026-07-16` is published (House granules `…PgH4547`+). CREC lags a session by a few days, so pick a target date old enough that CREC exists. (Text fetch needs `GOVINFO_API_KEY` configured in the run env — operational.)

## Implications for the design

New work is modest:
1. **House-CDN resolver** — `broadcastevents/<YYYYMMDD>` → HLS east manifest URL + metadata (title, congress/session, start/end, public-domain rights, citation URL). Small, fixture-testable.
2. **HLS audio ingestion** — ffmpeg audio extraction from the manifest into the existing pipeline; `clip_start_seconds = 0`.
3. **Publish wiring** — `audio_source` = HLS manifest URL (→ `hls` playback), `source_url` = `live.house.gov/?date=…` citation, US-House body/event_kind. Vote persist + click-to-seek + outcome already shipped.
4. **Silence-cutting w/ original-timeline preservation** — deferrable to a fast-follow; a ~3.4 h first session ingests directly.
5. **Web player** — no changes.
