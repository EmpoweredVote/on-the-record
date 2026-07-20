# House-CDN Ingestion → First Real Federal Prod Publish — Design

**Date:** 2026-07-19
**Status:** Approved (brainstorm) → ready for implementation plan
**Spike:** see [2026-07-19-house-cdn-spike-findings.md](2026-07-19-house-cdn-spike-findings.md)

## Goal

Ingest a recent (~3.4 h) US House floor session from the House Clerk CDN and publish it to **prod** as the **first real federal meeting** — with HLS video playback and roll-call votes (outcomes + absolute timestamps) that click-to-seek. Target date: **2026-07-16** (CREC published, ~3.4 h session).

This is the milestone that turns the three shipped vote slices (persist → click-to-seek → outcome) into something visible end-to-end on the live site.

## Why this is small

The spike established that the heavy risks don't exist:
- The CDN exposes an **HLS `manifest.m3u8`** → `resolve_playback` returns `hls` → the existing `FilePlayer` (hls.js) plays it. **No web player changes.** CORS is open (`access-control-allow-origin: *`), VOD, public-domain.
- Vote timestamps from ASR land in the manifest's own timeline; we publish that same manifest ⇒ `clip_start_seconds = 0`, no offset math for seek.
- Vote persist + click-to-seek + outcome already shipped.

New work: a resolver, ffmpeg HLS audio extraction, an ingestion entrypoint, a `floor` event_kind, and the publish run.

## Non-Goals (fast-follows)

- Silence-cutting for long (8–12 h) sessions (deferred; a ~3.4 h session ingests directly).
- WebVTT-caption seeding/validation (Clerk captions exist but our Whisper+diarization pipeline is the timestamp source of truth).
- Cross-linking `meetings.votes` ↔ `essentials.legislative_votes`.
- Automatic discovery/backfill of many sessions (this is one meeting, by explicit date).

## Architecture

```
date "2026-07-16"
  → house_cdn.resolve_session()               [NEW]  GET /broadcastevents/20260716
      → HouseFloorSource(manifest_url=HLS-east, title, congress, session, start, end, citation_url, rights)
  → run_local --house-floor 2026-07-16        [NEW entrypoint]
      → download: ffmpeg HLS → wav             [NEW routing in download.py]
      → existing pipeline: transcribe → diarize → CREC speaker-ID oracle
      → existing Stage-4: extract_floor_structure + build_floor_votes  (floor_votes w/ outcomes + clip-local ts)
      → gate (event_kind="floor" thresholds)   [NEW kind]
  → publish_meeting()                          [EXISTING]
      → audio_source = HLS manifest URL → resolve_playback → "hls" → FilePlayer
      → floor_votes → meetings.votes (outcome + absolutized ts; clip_start=0 ⇒ ts unchanged)
  → live meeting page: HLS video + Votes panel, click-to-seek
```

## Components

### 1. `src/house_cdn.py` (new) — source resolver

```python
@dataclass
class HouseFloorSource:
    date: str            # "2026-07-16"
    manifest_url: str    # HLS east manifest, hash stripped
    title: str           # "LEGISLATIVE DAY OF JULY 16, 2026"
    congress: str        # "119"
    session: str         # "2"
    start: str           # gavel ISO, from startDate
    end: str             # endDate ISO
    citation_url: str    # https://live.house.gov/?date=2026-07-16
    rights: str          # public-domain notice

def resolve_session(date: str, *, fetch=...) -> Optional[HouseFloorSource]: ...
```

- `GET https://liveproxy-azapp-prod-eastus2-003.azurewebsites.net/broadcastevents/<YYYYMMDD>` (date with dashes → id without dashes).
- Parse JSON-LD `BroadcastEvent`: from `asset.files[]` pick `type=="HLS"` preferring the `east` mirror (fall back to `central`), strip the `#t=…` hash for a clean manifest URL. Pull `name`, `superEvent.congressNum`/`sessionNum`, `startDate`, `endDate`, `rights`.
- `citation_url = f"https://live.house.gov/?date={date}"`.
- Returns `None` (graceful) if: no event, not in session, or no HLS file present.
- `fetch` injectable (`Callable[[str], str]`) for tests; **fixture** = the real BroadcastEvent JSON captured in the spike.
- Base URL is a module constant (documented, matches the Clerk's `liveproxy` host).

### 2. HLS audio ingestion (extend `src/download.py`)

- Detect an HLS manifest source (`.m3u8`) and extract audio via **ffmpeg**: `ffmpeg -y -i <manifest_url> -vn -ac 1 -ar 16000 <out.wav>` (mono 16 kHz, matching the pipeline's expected input). ffmpeg reads HLS natively; the CDN is range-seekable + CORS-open.
- Route it from the download entrypoint so an `.m3u8` `audio_source` uses ffmpeg rather than the plain `requests.get` path (which would fetch the playlist text, not media).
- No new dependency (ffmpeg already used by the pipeline for audio).

### 3. Ingestion entrypoint (`run_local.py`)

- New flag: `--house-floor YYYY-MM-DD`.
- Behavior: `resolve_session(date)` → set meeting metadata:
  - `meeting_type = "House Floor"`, `event_kind = "floor"`, `title = source.title`,
  - `audio_source = source.manifest_url` (the HLS URL — becomes playback),
  - `source_url = source.citation_url` (human citation),
  - CREC date/chamber = (`date`, `"house"`) so the existing Stage-4 floor-structure step runs,
  - `clip_start_seconds = 0` (full source).
- Download audio via the ffmpeg HLS path, then run the **full pipeline** (transcribe → diarize → CREC speaker-ID oracle → floor votes). The CREC oracle (already built + validated for House floor) names real members from one-minute speeches/debate.
- Abort gracefully with a clear message if `resolve_session` returns `None`.

### 4. `floor` event_kind (backend + web + config)

- `src/event_kinds.py`: add `"floor"` to `EVENT_KINDS`; add to `LOCAL_ROLE_SETS` (civic roles) and the `_CIVIC_FRAMING` branch so summarization/roles treat it like a legislative body.
- `src/config.py GATE_THRESHOLDS`: add `"floor": {"high": 0.70, "low": 0.40}` — deliberately lenient vs. council's 0.90/0.50 because a floor session carries heavy procedural (presiding Chair/Clerk) speech that is legitimately unnamed, deflating named-member coverage. SEED value; recalibrate via `bench/calibrate_gate.py` after the first reviewed floor meeting.
- `web/lib/types.ts`: add `"floor"` to the `EventKind` union.
- `web/lib/format.ts`: add `floor: "Floor"` to the label map.
- Meetings-list filter chips **auto-derive** from event_kinds present (`MeetingListClient.tsx`) — a "Floor" chip appears once a floor meeting exists; no chip hardcoding needed.

### 5. Publish

- Reuses `publish_meeting` unchanged. `audio_source` (HLS `.m3u8`) → `resolve_playback` → `("hls", url)` → `FilePlayer`. `floor_votes` → `meetings.votes` (outcome + tally + absolute timestamps; with `clip_start_seconds=0`, `absolutize_meeting_times` leaves timestamps unchanged).
- **Body/chamber:** look up the US-House slug in `essentials.chambers`. If exactly one row matches, `chamber_id` is set; otherwise publish **unchambered** (`_resolve_chamber_id` already returns `None` gracefully — still a valid publish). The correct body_slug is confirmed at implementation time against `essentials.chambers`.
- **Gate:** if effective coverage lands below the `floor` `low` threshold, publish with `--publish-anyway` (member coverage on a floor day is inherently partial). The gate verdict is reported either way.

### 6. Web player

No changes. HLS (`file`/`hls` kind) already supported by `FilePlayer` + `MeetingView`.

## Data / correctness notes

- **Seek correctness:** ASR runs on the manifest audio, so segment/word/vote timestamps are in the manifest's timeline. The published playback IS that manifest. Click-to-seek (`seekTo(t)` → `<video>.currentTime = t`) is directly correct. No `#t=` offset applied.
- **Idempotency:** `meetings.votes` write is delete-then-insert (on-the-record is the sole writer); re-publishing the same meeting is safe.
- **Public domain:** the BroadcastEvent `rights` field is asserted; recorded on the meeting as the source citation.

## Testing

- **Unit (resolver):** against the real BroadcastEvent fixture — HLS-east selection, hash-strip, metadata extraction, `east`→`central` fallback, missing-session/no-HLS → `None`.
- **Unit (download routing):** an `.m3u8` source routes to the ffmpeg audio path (ffmpeg invocation asserted/mocked; no network).
- **Unit (event_kind):** `"floor"` validates; gate thresholds present; web typecheck compiles with the new kind.
- **Live E2E (controller):** run `--house-floor 2026-07-16` end-to-end on Modal GPU; verify a transcript, named members via the CREC oracle, and `floor_votes` with outcomes + timestamps.
- **Pre-prod preview:** before the real prod write, validate the published shape via a **dev-branch publish or `--dry-run`** — inspect `meetings.votes` rows + `/api/meetings/:id/votes` + the meeting page (HLS plays, votes click-to-seek) — THEN do the real prod publish.

## Rollout / risks

- **CREC key:** `GOVINFO_API_KEY` must be configured in the run env (text fetch failed in the spike shell without it).
- **First-parse risk:** CREC parsing is validated on 2019-07-11 but this is a fresh 2026-07-16 granule — the live E2E is where we confirm rolls/tallies/outcomes parse correctly.
- **Session size:** ~3.4 h ASR on Modal GPU is tractable; if the chosen date is unexpectedly long, fall back to a shorter recent session day (from `/sessiondays/`).
- **Prod write:** the actual publish is the one irreversible step — gated behind the pre-prod preview and an explicit go-ahead.
