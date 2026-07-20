# House-CDN Ingestion → First Real Federal Prod Publish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the 2026-07-16 US House floor session from the House Clerk CDN and publish it to prod as the first real federal meeting — HLS video playback + roll-call votes (outcomes + absolute timestamps) that click-to-seek.

**Architecture:** A new resolver (`src/house_cdn.py`) maps a date → the Clerk's HLS manifest URL + metadata via `GET /broadcastevents/<YYYYMMDD>`. A `--house-floor DATE` entrypoint in `run_local.py` resolves the source, downloads audio from the HLS manifest via ffmpeg (new routing in `download.py`), runs the existing pipeline (ASR → diarize → CREC speaker-ID oracle → floor votes), and publishes. A new `floor` event_kind categorizes it. Everything else reuses shipped code: HLS → `resolve_playback` → `FilePlayer` (no web player change), floor votes → `meetings.votes` (persist + outcome + click-to-seek already shipped), `clip_start_seconds=0`.

**Tech Stack:** Python 3 (`.venv/bin/python`, pytest), ffmpeg (already a pipeline dep), Next.js/TypeScript (`web/`, vitest). See the design: `docs/superpowers/specs/2026-07-19-house-cdn-ingestion-design.md` and spike: `docs/superpowers/specs/2026-07-19-house-cdn-spike-findings.md`.

**Grounding (verified 2026-07-19):**
- Real fixture already committed: `tests/fixtures/house_cdn/broadcastevents_20260716.json` (the actual `/broadcastevents/20260716` JSON-LD). Its `asset.files[]` has `type` in {DASH, HLS, WebVTT}; the two HLS entries' URLs contain `/east/` and `/central/` and end `manifest.m3u8#t=459.387`. `name`="LEGISLATIVE DAY OF JULY 16, 2026"; `superEvent.congressNum`="119", `sessionNum`="2"; `startDate`/`endDate` present; `rights` contains "public domain".
- `src/event_kinds.py`: `EVENT_KINDS` tuple (line 3), `LOCAL_ROLE_SETS` (line 33, `_CIVIC_ROLES` values), framing dispatch (line ~106 `if event_kind in ("council","school_board","community_meeting")`).
- `src/config.py`: `GATE_THRESHOLDS` dict (line 130), council = `{"high":0.90,"low":0.50}`.
- `web/lib/types.ts`: `EventKind` union (lines 1-9). `web/lib/format.ts`: label map (line ~47, `news_clip: "News clip"`), `eventKindLabel` (line 68). Meetings-list chips auto-derive from present kinds (`web/app/MeetingListClient.tsx`).
- `src/publish.py`: `resolve_playback` returns `("hls", url)` for `.m3u8`; `playback_for_meeting` = `resolve_playback(enclosure or meeting.audio_source)`; DB `source_url` column = `meeting.audio_source`. So **citation = `audio_source`, playback = `processing_metadata.source_audio_url` (enclosure)**.
- `run_local.py`: source args are a mutually-exclusive group (`--input/-i`, `--browse-catstv`, `--resume`) at line ~3589; CREC is triggered by `--congressional-record` parsed via `parse_crec_arg` into `crec_request=(date,chamber)` (line 833); the `Meeting(...)` is constructed at line ~843 with `audio_source=str(audio_path)`, `audio_path=args.input` (line 677); Stage-4 floor-structure step runs `extract_floor_structure`/`build_floor_votes` when `crec_request` is set (line ~1497); `meeting.processing_metadata.source_audio_url` is settable (line ~902).
- `src/download.py`: `download_from_url(url, output_path, …)` (line 104) routes yt-dlp URLs to `download_via_ytdlp` (line 130) else streams via `requests.get(stream=True)` (line 140). No HLS handling today.

---

## File Structure

- Modify `src/event_kinds.py`, `src/config.py` — add the `floor` kind + gate thresholds.
- Modify `web/lib/types.ts`, `web/lib/format.ts` — `floor` in the union + label.
- Create `src/house_cdn.py` — the resolver. Test `tests/test_house_cdn.py`. Fixture already present.
- Modify `src/download.py` — HLS→wav via ffmpeg. Test `tests/test_download_hls.py`.
- Modify `run_local.py` — `--house-floor` entrypoint + citation/enclosure wiring.
- (Task 5) No code — the operational live E2E + pre-prod preview + prod publish.

---

## Task 1: `floor` event_kind (backend + config + web)

**Files:** Modify `src/event_kinds.py`, `src/config.py`, `web/lib/types.ts`, `web/lib/format.ts`. Test `tests/test_event_kinds.py` (create if absent).

- [ ] **Step 1: Write the failing test**

Create/append `tests/test_event_kinds.py`:

```python
from src.event_kinds import EVENT_KINDS, validate_event_kind, LOCAL_ROLE_SETS
from src import config


def test_floor_is_valid_event_kind():
    assert "floor" in EVENT_KINDS
    assert validate_event_kind("floor") == "floor"


def test_floor_has_gate_thresholds():
    t = config.GATE_THRESHOLDS["floor"]
    assert t["high"] == 0.70 and t["low"] == 0.40


def test_floor_has_local_roles():
    # floor uses the civic role vocabulary (legislative body)
    assert "floor" in LOCAL_ROLE_SETS
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_event_kinds.py -q`
Expected: FAIL (`"floor"` not in EVENT_KINDS / GATE_THRESHOLDS / LOCAL_ROLE_SETS).

- [ ] **Step 3: Add `floor` in the backend**

In `src/event_kinds.py`:
1. Add `"floor",` to the `EVENT_KINDS` tuple (e.g. after `"community_meeting",`).
2. Add `"floor": _CIVIC_ROLES,` to `LOCAL_ROLE_SETS`.
3. In the framing dispatch, include floor with the civic branch: change `if event_kind in ("council", "school_board", "community_meeting"):` to `if event_kind in ("council", "school_board", "community_meeting", "floor"):`.

In `src/config.py`, add to `GATE_THRESHOLDS` (after `"community_meeting"`):
```python
    "floor":            {"high": 0.70, "low": 0.40},
```
(Lenient vs. council's 0.90/0.50: a floor session carries heavy procedural Chair/Clerk speech that is legitimately unnamed. SEED — recalibrate via `bench/calibrate_gate.py` after the first reviewed floor meeting.)

- [ ] **Step 4: Add `floor` in the web types**

In `web/lib/types.ts`, add `| "floor"` to the `EventKind` union.
In `web/lib/format.ts`, add `floor: "Floor",` to the label map object (alongside `news_clip: "News clip"`).

- [ ] **Step 5: Run tests + web typecheck**

Run: `.venv/bin/python -m pytest tests/test_event_kinds.py -q` → PASS.
Run: `cd web && npm run build` → compiles (the new union member is exhaustive across `eventKindLabel` etc.).

- [ ] **Step 6: Commit**
```bash
git add src/event_kinds.py src/config.py web/lib/types.ts web/lib/format.ts tests/test_event_kinds.py
git commit -m "feat(federal): add 'floor' event_kind (backend, gate thresholds, web label)"
```

---

## Task 2: `src/house_cdn.py` — date → HLS source resolver

**Files:** Create `src/house_cdn.py`. Test `tests/test_house_cdn.py`. Fixture `tests/fixtures/house_cdn/broadcastevents_20260716.json` (already present).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_house_cdn.py`:

```python
import json
from pathlib import Path
import pytest
from src.house_cdn import resolve_session, HouseFloorSource

FIX = Path(__file__).parent / "fixtures" / "house_cdn" / "broadcastevents_20260716.json"


def _fake_fetch(_url: str) -> str:
    return FIX.read_text()


def test_resolve_session_picks_hls_east_and_strips_hash():
    s = resolve_session("2026-07-16", fetch=_fake_fetch)
    assert isinstance(s, HouseFloorSource)
    assert s.manifest_url == (
        "https://houseliveprod-f9h4cpb9dyb8gegg.a01.azurefd.net"
        "/east/2026-07-16T08-51-14/manifest.m3u8"
    )  # HLS, east mirror, no #t= hash
    assert s.manifest_url.endswith("manifest.m3u8")


def test_resolve_session_metadata_and_citation():
    s = resolve_session("2026-07-16", fetch=_fake_fetch)
    assert s.date == "2026-07-16"
    assert s.title == "LEGISLATIVE DAY OF JULY 16, 2026"
    assert s.congress == "119" and s.session == "2"
    assert s.start.startswith("2026-07-16") and s.end.startswith("2026-07-16")
    assert s.citation_url == "https://live.house.gov/?date=2026-07-16"
    assert "public domain" in s.rights.lower()


def test_resolve_session_builds_broadcastevents_url():
    captured = {}
    def fetch(url):
        captured["url"] = url
        return FIX.read_text()
    resolve_session("2026-07-16", fetch=fetch)
    assert captured["url"].endswith("/broadcastevents/20260716")  # dashes stripped for id


def test_resolve_session_falls_back_to_central_when_no_east():
    doc = json.loads(FIX.read_text())
    ev = doc[0]
    ev["asset"]["files"] = [f for f in ev["asset"]["files"]
                            if not (f["type"] == "HLS" and "/east/" in f["url"])]
    s = resolve_session("2026-07-16", fetch=lambda _u: json.dumps([ev]))
    assert "/central/" in s.manifest_url and s.manifest_url.endswith("manifest.m3u8")


def test_resolve_session_returns_none_when_no_hls():
    doc = json.loads(FIX.read_text())
    ev = doc[0]
    ev["asset"]["files"] = [f for f in ev["asset"]["files"] if f["type"] != "HLS"]
    assert resolve_session("2026-07-16", fetch=lambda _u: json.dumps([ev])) is None


def test_resolve_session_returns_none_on_empty_or_error():
    assert resolve_session("2026-07-16", fetch=lambda _u: "[]") is None
    def boom(_u): raise RuntimeError("404")
    assert resolve_session("2026-07-16", fetch=boom) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_house_cdn.py -q`
Expected: FAIL — `src.house_cdn` does not exist.

- [ ] **Step 3: Implement `src/house_cdn.py`**

```python
"""Resolve a US House floor session date to its public Clerk CDN stream.

The Clerk's live site (live.house.gov) resolves a session via
`GET {LIVEPROXY}/broadcastevents/<YYYYMMDD>` → a schema.org BroadcastEvent
JSON-LD whose `asset.files[]` lists HLS/DASH/WebVTT URLs in east+central CDN
mirrors. We take the HLS (`manifest.m3u8`) east mirror — `publish.resolve_playback`
maps `.m3u8` to the `hls` playback kind, which the web FilePlayer plays. The video
is public-domain (Title 17 §105). Pure except the injected `fetch`.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

_LIVEPROXY = "https://liveproxy-azapp-prod-eastus2-003.azurewebsites.net"
_CITATION = "https://live.house.gov/?date={date}"


@dataclass
class HouseFloorSource:
    date: str            # "2026-07-16"
    manifest_url: str    # HLS east manifest, #t= hash stripped
    title: str
    congress: str
    session: str
    start: str
    end: str
    citation_url: str
    rights: str


def _default_fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (fixed gov host)
        return r.read().decode("utf-8")


def _pick_hls(files: list) -> Optional[str]:
    hls = [f for f in files if (f.get("type") or "").upper() == "HLS" and f.get("url")]
    if not hls:
        return None
    east = next((f for f in hls if "/east/" in f["url"]), None)
    chosen = east or hls[0]
    return chosen["url"].split("#", 1)[0]  # strip the #t=<offset> hash


def resolve_session(
    date: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
) -> Optional[HouseFloorSource]:
    """Resolve "YYYY-MM-DD" -> HouseFloorSource, or None if unavailable."""
    event_id = date.replace("-", "")
    try:
        raw = fetch(f"{_LIVEPROXY}/broadcastevents/{event_id}")
        doc = json.loads(raw)
    except Exception:
        return None
    events = doc if isinstance(doc, list) else [doc]
    if not events:
        return None
    ev = events[0]
    manifest = _pick_hls((ev.get("asset") or {}).get("files") or [])
    if not manifest:
        return None
    se = ev.get("superEvent") or {}
    return HouseFloorSource(
        date=date,
        manifest_url=manifest,
        title=ev.get("name", ""),
        congress=str(se.get("congressNum", "")),
        session=str(se.get("sessionNum", "")),
        start=ev.get("startDate", ""),
        end=ev.get("endDate", ""),
        citation_url=_CITATION.format(date=date),
        rights=ev.get("rights", ""),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_house_cdn.py -q` → PASS (all 6).

- [ ] **Step 5: Commit**
```bash
git add src/house_cdn.py tests/test_house_cdn.py
git commit -m "feat(federal): House-CDN session resolver (date -> HLS manifest + metadata)"
```

---

## Task 3: HLS audio extraction in `download.py`

**Files:** Modify `src/download.py`. Test `tests/test_download_hls.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_download_hls.py`:

```python
from unittest import mock
from pathlib import Path
from src import download


def test_is_hls_url():
    assert download.is_hls_url("https://x.azurefd.net/east/T/manifest.m3u8")
    assert not download.is_hls_url("https://x/video.mp4")
    assert not download.is_hls_url("https://youtube.com/watch?v=abc")


def test_download_from_url_routes_m3u8_to_ffmpeg(tmp_path, monkeypatch):
    calls = {}
    def fake_run(cmd, *a, **k):
        calls["cmd"] = cmd
        Path(cmd[cmd.index("-i") + 3]).write_bytes(b"RIFF")  # touch the -y <out> target
        class R: returncode = 0
        return R()
    # ffmpeg extraction goes through subprocess.run
    monkeypatch.setattr(download.subprocess, "run", fake_run)
    out = tmp_path / "audio.wav"
    url = "https://houseliveprod.azurefd.net/east/T/manifest.m3u8"
    res = download.download_from_url(url, str(out))
    cmd = calls["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd and url in cmd
    assert "-vn" in cmd  # audio only
    assert str(res).endswith(".wav")
```

> NOTE: adjust the `fake_run` output-touch to match the exact ffmpeg arg order you implement; the assertion that matters is `ffmpeg -i <url> -vn … <out.wav>`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_download_hls.py -q`
Expected: FAIL — `is_hls_url` missing / `.m3u8` not routed to ffmpeg.

- [ ] **Step 3: Implement HLS routing in `src/download.py`**

Add near the other URL predicates (after `_is_ytdlp_url`):

```python
def is_hls_url(url: str) -> bool:
    """A raw HLS manifest (…/manifest.m3u8). Extract audio with ffmpeg, not requests."""
    return url.split("?", 1)[0].lower().endswith(".m3u8")
```

Add an ffmpeg extractor (needs `import subprocess` — add if not present):

```python
def download_audio_via_ffmpeg(url: str, output_path: str) -> str:
    """Extract mono 16 kHz WAV audio from an HLS/DASH manifest URL via ffmpeg.

    ffmpeg reads the CDN's range-seekable, CORS-open HLS natively. Returns the
    wav path (extension forced to .wav)."""
    out = str(Path(output_path).with_suffix(".wav"))
    cmd = ["ffmpeg", "-y", "-i", url, "-vn", "-ac", "1", "-ar", "16000", out]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not Path(out).exists():
        raise RuntimeError(f"ffmpeg HLS extraction failed ({result.returncode}): {result.stderr[-500:]}")
    return out
```

In `download_from_url`, route HLS BEFORE the yt-dlp/requests branches:

```python
    if is_hls_url(url):
        return download_audio_via_ffmpeg(url, output_path)
    if _is_ytdlp_url(url):
        return download_via_ytdlp(url, output_path, cookies_file=cookies_file, progress=progress)
    ...
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_download_hls.py -q` → PASS.

- [ ] **Step 5: Commit**
```bash
git add src/download.py tests/test_download_hls.py
git commit -m "feat(download): extract audio from HLS manifest URLs via ffmpeg"
```

---

## Task 4: `--house-floor` entrypoint in `run_local.py`

**Files:** Modify `run_local.py`. Test `tests/test_house_floor_entrypoint.py`.

The `--house-floor DATE` flag is a convenience wrapper that resolves the session and populates the existing pipeline args, so ingestion reuses the standard flow. It sets:
- `args.input = source.manifest_url` (download target → ffmpeg HLS, Task 3),
- `args.event_kind = "floor"`, `args.meeting_type = "House Floor"`, `args.date = DATE`, `args.title = source.title`,
- `args.congressional_record = f"house:{DATE}"` (drives the CREC oracle + Stage-4 floor votes),
- and stashes `args._house_source` so meeting construction can set **citation vs. playback**: `audio_source = citation` (→ DB `source_url`) and `processing_metadata.source_audio_url = manifest_url` (→ HLS playback).

- [ ] **Step 1: Write the failing test**

Create `tests/test_house_floor_entrypoint.py`:

```python
import argparse
from unittest import mock
import run_local
from src.house_cdn import HouseFloorSource

SRC = HouseFloorSource(
    date="2026-07-16",
    manifest_url="https://cdn/east/T/manifest.m3u8",
    title="LEGISLATIVE DAY OF JULY 16, 2026",
    congress="119", session="2",
    start="2026-07-16T09:00:00", end="2026-07-16T12:15:32",
    citation_url="https://live.house.gov/?date=2026-07-16",
    rights="… public domain …",
)


def _args(**kw):
    base = dict(house_floor="2026-07-16", input=None, event_kind=None,
                meeting_type=None, date="", title=None, congressional_record=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_expand_house_floor_populates_args(monkeypatch):
    monkeypatch.setattr(run_local, "resolve_session", lambda d: SRC)
    args = _args()
    run_local._expand_house_floor(args)
    assert args.input == SRC.manifest_url
    assert args.event_kind == "floor"
    assert args.meeting_type == "House Floor"
    assert args.date == "2026-07-16"
    assert args.title == SRC.title
    assert args.congressional_record == "house:2026-07-16"
    assert args._house_source is SRC


def test_expand_house_floor_aborts_when_unresolved(monkeypatch):
    monkeypatch.setattr(run_local, "resolve_session", lambda d: None)
    with pytest.raises(SystemExit):
        run_local._expand_house_floor(_args())


import pytest  # noqa: E402
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_house_floor_entrypoint.py -q`
Expected: FAIL — `--house-floor` / `_expand_house_floor` / `resolve_session` import not present.

- [ ] **Step 3: Add the flag + expansion**

In `run_local.py`:

1. Import the resolver near the top-level imports (module scope, so the test can monkeypatch `run_local.resolve_session`):
```python
from src.house_cdn import resolve_session
```

2. Add the flag to the source mutually-exclusive group (near `--input`, line ~3589):
```python
    source.add_argument(
        "--house-floor", metavar="YYYY-MM-DD",
        help="Ingest a US House floor session by date from the House Clerk CDN",
    )
```

3. Add the expansion helper (module scope):
```python
def _expand_house_floor(args) -> None:
    """Resolve --house-floor DATE into the standard pipeline args + stash the source."""
    date = getattr(args, "house_floor", None)
    if not date:
        return
    source = resolve_session(date)
    if source is None:
        raise SystemExit(
            f"No House floor session resolvable for {date} "
            f"(not in session, or CDN has no HLS stream)."
        )
    args.input = source.manifest_url
    args.event_kind = "floor"
    args.meeting_type = "House Floor"
    args.date = date
    if not getattr(args, "title", None):
        args.title = source.title
    args.congressional_record = f"house:{date}"
    args._house_source = source
```

4. Call `_expand_house_floor(args)` at the START of `run_pipeline(args)` (line ~665), before metadata resolution and Stage 1:
```python
def run_pipeline(args: argparse.Namespace) -> None:
    _expand_house_floor(args)
    ...
```

5. At meeting construction (after the `Meeting(...)` at line ~843-852), set citation vs. playback when the source is a house-floor source:
```python
    _house_source = getattr(args, "_house_source", None)
    if _house_source is not None:
        meeting.audio_source = _house_source.citation_url            # DB source_url = live.house.gov page
        meeting.processing_metadata.source_audio_url = _house_source.manifest_url  # playback = HLS
```
(`resolve_playback(enclosure or audio_source)` then returns `("hls", manifest_url)` while `source_url` is the citation. Download already happened from `args.input = manifest_url`.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_house_floor_entrypoint.py -q` → PASS.

- [ ] **Step 5: Regression — full suite**

Run: `.venv/bin/python -m pytest -q` → all green (no existing test relies on the changed lines).

- [ ] **Step 6: Commit**
```bash
git add run_local.py tests/test_house_floor_entrypoint.py
git commit -m "feat(run_local): --house-floor DATE ingests a House session from the Clerk CDN"
```

---

## Task 5: Live E2E + pre-prod preview + first prod publish (operational)

**Files:** none (operational; run by the controller with the user in the loop). Do NOT perform the prod write without an explicit go-ahead.

- [ ] **Step 1: Live ingest E2E (no publish)**

Ensure `GOVINFO_API_KEY` is configured. Run the pipeline end-to-end on Modal GPU without publishing:
```bash
.venv/bin/python run_local.py --house-floor 2026-07-16 --diarizer oss --compute modal --no-publish
```
Verify: audio extracted from HLS; a transcript exists; the CREC oracle named real members; `floor_votes` produced with outcomes + timestamps. Inspect `~/CouncilScribe/meetings/2026-07-16-house-floor/` (transcript_named.json, quality.json, pipeline_state.json). Report the gate verdict, speaker coverage, and vote count/sample.

- [ ] **Step 2: Pre-prod preview (no prod write)**

Preview the published shape WITHOUT writing prod — a Supabase dev branch or `--dry-run`:
```bash
.venv/bin/python run_local.py --publish-meeting 2026-07-16-house-floor --dry-run
```
Confirm the intended `meetings.votes` rows (resolution/description/result "Agreed to · Y–N"/timestamp), `playback_kind="hls"` with the manifest URL, `source_url` = the live.house.gov citation, and `event_kind="floor"`. If speaker coverage is below the floor gate `low` (0.40), note that `--publish-anyway` will be required. Report the preview to the user.

- [ ] **Step 3: First real prod publish (requires explicit user go-ahead)**

Only after the user approves the preview:
```bash
.venv/bin/python run_local.py --publish-meeting 2026-07-16-house-floor   # add --publish-anyway iff gate < low
```
Then verify live: `GET /api/meetings/:id/votes` returns the rows; the meeting page plays the HLS video and vote click-to-seek lands correctly. Screenshot for confirmation.

- [ ] **Step 4: Record the outcome** in memory (first federal prod publish shipped; the meeting id/slug; any gate/coverage notes).

---

## Self-Review

**Spec coverage:** resolver (Task 2) ✓; HLS audio (Task 3) ✓; `--house-floor` entrypoint incl. citation/enclosure split (Task 4) ✓; `floor` event_kind across backend/config/web (Task 1) ✓; live E2E + pre-prod preview + gated prod publish (Task 5) ✓; web player unchanged ✓; `clip_start_seconds=0` (default; not overridden) ✓. Silence-cutting explicitly out of scope (design non-goal).

**Placeholder scan:** none — real committed fixture, exact anchors/line numbers, complete code, runnable commands. The one adjust-to-fit note (ffmpeg arg order in the Task 3 test) is called out explicitly.

**Type consistency:** `HouseFloorSource` fields (Task 2) are consumed unchanged in Task 4 (`manifest_url`, `citation_url`, `title`); `resolve_session` is imported at module scope in `run_local` so the Task 4 test monkeypatches `run_local.resolve_session`; `"floor"` is added to `EVENT_KINDS`, `GATE_THRESHOLDS`, `LOCAL_ROLE_SETS`, the web `EventKind` union, and the label map consistently (Task 1); `audio_source`=citation / `source_audio_url`=manifest matches `publish.playback_for_meeting`'s `resolve_playback(enclosure or audio_source)` and `source_url = audio_source`.

**Ordering:** Task 1 (floor kind) precedes Task 4 (which sets `event_kind="floor"`), and Task 2 (resolver) precedes Task 4 (which imports it). Task 3 (HLS download) precedes Task 5 (live ingest).
