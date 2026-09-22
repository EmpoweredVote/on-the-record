# House-floor weekly automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A weekly, off-Mac GitHub Actions job discovers new US House floor sessions, processes each through the CREC speaker oracle, and writes each meeting to the database as a `draft` (hidden from the public site) for later human approval.

**Architecture:** A new `src/floor_dispatch.py` lists House Clerk floor videos (yt-dlp), keeps only dates with House Congressional Record data (govinfo), drops dates already in the database, and runs `run_local.py` once per new session as a subprocess with a new `--publish-as-draft` flag. The publish path gains a `status` parameter so drafts land as `status='draft'`. The public web app filters those out. Interim `--list-drafts` / `--promote` commands let the operator review and go live from any machine until the Project 2 admin panel exists.

**Tech Stack:** Python 3.12, psycopg2, yt-dlp, GitHub Actions, Modal (GPU offload), Next.js (`web/`), pytest, vitest.

## Global Constraints

- Run Python with `.venv/bin/python`, never system `python3` (system 3.14 lacks project deps).
- House floor only. Senate floor is a documented no-go.
- CREC runs skip the speaker-ID LLM (already enforced by `should_run_llm`). No per-meeting summaries on floor auto-runs (mirror batch mode's `skip_summary=True`).
- `DATABASE_URL` must use the IPv4 pooler host (direct host is IPv6-only).
- A `draft` meeting must never appear on the public site. The `web/` filter (Task 2) must be merged before any real draft is written to the production database.
- Tests are offline: inject `fetch` / extractor / subprocess-runner / DB cursor. Follow the existing `tests/fixtures/govinfo/` + `RecordingCursor` patterns.
- Frequent commits: one per task, after its tests pass.
- The per-session command the dispatcher issues:
  ```
  run_local.py --input <url> --date <YYYY-MM-DD> --event-kind other \
    --meeting-type "House Floor" --congressional-record <YYYY-MM-DD> house \
    --compute modal --no-review --publish-as-draft [--cookies <file>]
  ```

---

### Task 1: `--publish-as-draft` — write meetings as `status='draft'` and capture the gate verdict

**Files:**
- Modify: `src/models.py` (`ProcessingMetadata` dataclass + its `to_dict`)
- Modify: `src/publish.py` (`_upsert_meeting`, `publish_meeting`)
- Modify: `run_local.py` (new `--publish-as-draft` arg; draft branch in the `run_pipeline` publish block; `_option_supplied` map entry)
- Test: `tests/test_publish.py`

**Interfaces:**
- Produces: `publish_meeting(meeting, body_slug=None, trigger_deploy=True, status="published") -> PublishResult`; `_upsert_meeting(cur, meeting, body_slug, status="published") -> str`; `ProcessingMetadata.gate_verdict: Optional[str]`, `ProcessingMetadata.gate_coverage: Optional[float]`; a `run_local.py` CLI flag `--publish-as-draft`.
- Consumes (existing): `quality.evaluate_meeting(meeting) -> dict` with keys `verdict`, `effective_coverage`, `trusted_coverage`, `reason`.

- [ ] **Step 1: Write the failing test for the draft status parameter**

Add to `tests/test_publish.py` (reuses the existing `RecordingCursor` and `Meeting` imports in that file):

```python
def test_upsert_meeting_defaults_to_published():
    cur = RecordingCursor(None)  # None → INSERT path
    meeting = Meeting(
        meeting_id="2026-09-04-house-floor",
        city=None, date="2026-09-04", meeting_type="House Floor",
        title="House Floor Proceedings", event_kind="other",
    )
    _upsert_meeting(cur, meeting, None)
    _write_sql, write_params = cur.calls[1]
    assert "published" in write_params
    assert "draft" not in write_params


def test_upsert_meeting_writes_draft_status_when_requested():
    cur = RecordingCursor(None)
    meeting = Meeting(
        meeting_id="2026-09-04-house-floor",
        city=None, date="2026-09-04", meeting_type="House Floor",
        title="House Floor Proceedings", event_kind="other",
    )
    _upsert_meeting(cur, meeting, None, status="draft")
    _write_sql, write_params = cur.calls[1]
    assert "draft" in write_params
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_publish.py::test_upsert_meeting_writes_draft_status_when_requested -v`
Expected: FAIL — `_upsert_meeting()` takes no `status` keyword.

- [ ] **Step 3: Thread `status` through `_upsert_meeting`**

In `src/publish.py`, change the signature:

```python
def _upsert_meeting(cur, meeting: Meeting, body_slug: Optional[str], status: str = "published") -> str:
```

Then replace the two hard-coded status literals inside it. The UPDATE branch currently passes `"published",` in its params tuple (the value bound to `status = %s`); change that single literal to `status`. The INSERT branch currently passes `"published",` (the value for the `status` column); change that single literal to `status`. Leave every other line untouched.

- [ ] **Step 4: Thread `status` through `publish_meeting`**

In `src/publish.py`, change the signature:

```python
def publish_meeting(
    meeting: Meeting, body_slug: Optional[str] = None, trigger_deploy: bool = True,
    status: str = "published",
) -> PublishResult:
```

Inside, change the call site from `meeting_uuid = _upsert_meeting(cur, meeting, body_slug)` to:

```python
                meeting_uuid = _upsert_meeting(cur, meeting, body_slug, status=status)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_publish.py -v`
Expected: PASS (new tests pass; all pre-existing publish tests still pass).

- [ ] **Step 6: Add gate-verdict fields to `ProcessingMetadata`**

In `src/models.py`, add two fields to the `ProcessingMetadata` dataclass (after `source_audio_url`):

```python
    gate_verdict: Optional[str] = None
    gate_coverage: Optional[float] = None
```

Confirm `to_dict` serializes all dataclass fields (e.g. via `dataclasses.asdict(self)` or `asdict`). If `to_dict` lists fields explicitly, add `"gate_verdict": self.gate_verdict, "gate_coverage": self.gate_coverage` to the returned dict.

- [ ] **Step 7: Add the `--publish-as-draft` flag**

In `run_local.py`, next to the `--no-publish` argument (search for `parser.add_argument("--no-publish"`), add:

```python
    parser.add_argument("--publish-as-draft", action="store_true",
                        help="Publish the meeting as a not-live draft "
                             "(status='draft'); bypasses the confidence gate and "
                             "captures the gate verdict for later review. Used by "
                             "the weekly floor automation.")
```

In the `_option_supplied(...)` dictionary (search for `"--no-publish":`), add:

```python
            "--publish-as-draft": _option_supplied(cli_argv, "--publish-as-draft"),
```

- [ ] **Step 8: Add the draft branch in the publish block**

In `run_local.py` `run_pipeline`, find the publish block that begins `if getattr(args, "publish", False):`. Add a draft branch **before** the existing `_may_publish` check, so a draft always publishes and is never gated:

```python
    if getattr(args, "publish", False):
        if getattr(args, "publish_as_draft", False):
            from src import quality
            from src.publish import publish_meeting
            report = quality.evaluate_meeting(meeting)
            meeting.processing_metadata.gate_verdict = report["verdict"]
            meeting.processing_metadata.gate_coverage = report["effective_coverage"]
            _attach_thumbnail(meeting, meeting_dir)
            try:
                result = publish_meeting(meeting, state.body_slug, status="draft")
                print(f"  Published as DRAFT: {result.segments} segments, "
                      f"{result.speakers} speakers "
                      f"(gate={report['verdict']}, "
                      f"coverage={report['effective_coverage']:.0%})")
            except Exception as e:
                print(f"  WARNING: draft publish failed: {e}")
        elif not _may_publish(state.review_status, getattr(args, "publish_anyway", False)):
            print(f"  Not publishing — gate verdict is "
                  f"'{state.review_status}'. Review and re-run, or pass "
                  f"--publish-anyway to override.")
        else:
            # ... existing non-draft publish body unchanged ...
```

Keep the existing non-draft `else` body exactly as it was (the `from src.publish import publish_meeting` / `_attach_thumbnail` / `publish_meeting(meeting, state.body_slug)` block).

- [ ] **Step 9: Verify the flag parses and drives a draft (smoke, no DB)**

Run: `.venv/bin/python run_local.py --help | grep -A2 publish-as-draft`
Expected: the flag help text prints.
Run: `.venv/bin/python -m pytest tests/test_publish.py tests/test_models.py -v` (run `tests/test_models.py` only if it exists)
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/models.py src/publish.py run_local.py tests/test_publish.py
git commit -m "feat(publish): --publish-as-draft writes status=draft and captures gate verdict"
```

---

### Task 2: Hide `draft` meetings from the public web app

**Files:**
- Modify: `web/lib/queries.ts` (`fetchMeetings`, `fetchMeeting`)
- Test: `web/lib/queries.test.ts`

**Interfaces:**
- Consumes: `mapMeeting(m)` returns a `Meeting` whose `.status` defaults to `"published"` (existing).
- Produces: `fetchMeetings()` returns only `status === "published"` meetings; `fetchMeeting(id)` returns `null` for a non-published meeting.

- [ ] **Step 1: Verify the API surfaces `status` on the list + detail payloads**

Run (uses the app's configured API base):
```bash
cd web && node -e "const u=process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL; fetch(u+'/api/meetings').then(r=>r.json()).then(d=>console.log('has status field:', 'status' in (d[0]||{}), Object.keys(d[0]||{}).slice(0,12)))"
```
Expected: `has status field: true`.
If it prints `false`, the ev-accounts `/api/meetings` (and `/api/meetings/:id`) response must first be changed to include the `status` column — do that small change in the nested `~/Documents/GitHub/ev-accounts/` clone (add `status` to the meeting SELECT/serializer), deploy it, and re-run this step before continuing. The `web/lib/queries.test.ts` note "mapMeeting carries status" indicates the field is already surfaced, so this is expected to pass.

- [ ] **Step 2: Write the failing tests**

In `web/lib/queries.test.ts`, mirror the existing fetch-mock style used by the other tests in that file (same `vi`/`jest` harness the file already imports). Add:

```ts
it("fetchMeetings drops non-published (draft) meetings", async () => {
  mockFetchJson([
    { id: "m1", slug: "2026-09-04-house-floor", status: "draft" },
    { id: "m2", slug: "2026-08-01-city-council", status: "published" },
  ]);
  const meetings = await fetchMeetings();
  expect(meetings.map((m) => m.id)).toEqual(["m2"]);
});

it("fetchMeeting returns null for a draft meeting", async () => {
  mockFetchJson({ id: "m1", slug: "2026-09-04-house-floor", status: "draft" });
  const meeting = await fetchMeeting("2026-09-04-house-floor");
  expect(meeting).toBeNull();
});
```

Use the file's existing mock helper (if it wraps `global.fetch`, reuse it; otherwise define `mockFetchJson(body)` as a thin `vi.stubGlobal("fetch", ...)` returning `{ ok: true, status: 200, json: async () => body }`, matching the other tests).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd web && npx vitest run lib/queries.test.ts`
Expected: FAIL — drafts are currently returned.

- [ ] **Step 4: Filter in `fetchMeetings`**

In `web/lib/queries.ts`, change the `fetchMeetings` return line from:
```ts
  return (data as unknown[]).map(mapMeeting);
```
to:
```ts
  return (data as unknown[]).map(mapMeeting).filter((m) => m.status === "published");
```

- [ ] **Step 5: Filter in `fetchMeeting`**

In `web/lib/queries.ts`, change the `fetchMeeting` return from:
```ts
  return mapMeeting(await res.json());
```
to:
```ts
  const meeting = mapMeeting(await res.json());
  return meeting.status === "published" ? meeting : null;
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd web && npx vitest run lib/queries.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/lib/queries.ts web/lib/queries.test.ts
git commit -m "feat(web): hide draft meetings from the public list and detail pages"
```

---

### Task 3: `src/floor_dispatch.py` — discover, filter, de-duplicate, dispatch

**Files:**
- Modify: `src/govinfo.py` (add `has_chamber_data`)
- Modify: `src/publish.py` (add `existing_meeting_slugs`)
- Create: `src/floor_dispatch.py`
- Test: `tests/test_floor_dispatch.py`

**Interfaces:**
- Produces:
  - `govinfo.has_chamber_data(date, chamber, *, fetch=_default_fetch, api_key=None) -> bool`
  - `publish.existing_meeting_slugs(db_url=None) -> set[str]`
  - `floor_dispatch.FloorVideo` (dataclass: `date: str`, `url: str`, `title: str`)
  - `floor_dispatch._parse_floor_date(title: str) -> Optional[str]`
  - `floor_dispatch.list_floor_videos(channel_url, *, cookies_file=None, extractor=...) -> list[FloorVideo]`
  - `floor_dispatch._already_processed(date: str, slugs: set[str]) -> bool`
  - `floor_dispatch.discover_sessions(*, channel_url, since, existing_slugs, has_house, videos) -> list[FloorVideo]`
  - `floor_dispatch.dispatch(video, *, cookies_file=None, runner=subprocess.run) -> int`
  - `floor_dispatch.main(argv=None) -> int`
- Consumes: `govinfo._list_matching_granule_ids`, `publish._require_db_url`.

- [ ] **Step 1: Write the failing test for `has_chamber_data`**

Add `tests/test_floor_dispatch.py` (reuses the existing `tests/fixtures/govinfo/granules_page1.json` fixture, which contains HOUSE granules):

```python
import json
from pathlib import Path

from src import govinfo

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def test_has_chamber_data_true_for_house_when_granules_present():
    page = (FIX / "granules_page1.json").read_text()
    calls = []
    def fake_fetch(url):
        calls.append(url)
        return page  # single page: no nextPage → one call
    assert govinfo.has_chamber_data("2026-07-16", "house",
                                    fetch=fake_fetch, api_key="k") is True


def test_has_chamber_data_false_when_no_record():
    def fake_fetch(url):
        raise RuntimeError("404 no package")  # recess day
    assert govinfo.has_chamber_data("2026-12-25", "house",
                                    fetch=fake_fetch, api_key="k") is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_floor_dispatch.py::test_has_chamber_data_true_for_house_when_granules_present -v`
Expected: FAIL — `has_chamber_data` does not exist.

- [ ] **Step 3: Implement `has_chamber_data`**

In `src/govinfo.py`, after `fetch_congressional_record_turns`, add:

```python
def has_chamber_data(
    date: str,
    chamber: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
    api_key: Optional[str] = None,
) -> bool:
    """True when the Congressional Record for `date` has granules for `chamber`.

    A recess day / missing package yields False (never an exception). Lighter
    than fetch_congressional_record_turns: it lists granule ids only, never
    fetching granule text.
    """
    key = _resolve_api_key(api_key)
    ids = _list_matching_granule_ids(_package_id(date), chamber, key, fetch)
    return bool(ids)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_floor_dispatch.py -v`
Expected: PASS for the two `has_chamber_data` tests.

- [ ] **Step 5: Write the failing test for date parsing + discovery**

Append to `tests/test_floor_dispatch.py`:

```python
from src import floor_dispatch as fd


def test_parse_floor_date_extracts_iso_date():
    title = "US House Floor Proceedings (Thursday, September 4, 2026)"
    assert fd._parse_floor_date(title) == "2026-09-04"


def test_parse_floor_date_returns_none_for_unrelated_title():
    assert fd._parse_floor_date("Weekly leadership press conference") is None


def test_list_floor_videos_maps_titles_to_dated_videos():
    fake_info = {"entries": [
        {"title": "US House Floor Proceedings (Thursday, September 4, 2026)",
         "url": "https://youtu.be/aaa"},
        {"title": "Some hearing clip", "url": "https://youtu.be/bbb"},
    ]}
    def fake_extractor(url, *, cookies_file=None):
        return fake_info
    vids = fd.list_floor_videos("https://youtube.com/@USHouseClerk/streams",
                                extractor=fake_extractor)
    assert [(v.date, v.url) for v in vids] == [("2026-09-04", "https://youtu.be/aaa")]


def test_discover_sessions_filters_since_dedupe_and_house():
    videos = [
        fd.FloorVideo(date="2026-09-04", url="u4", title="t4"),
        fd.FloorVideo(date="2026-09-03", url="u3", title="t3"),  # already processed
        fd.FloorVideo(date="2026-08-01", url="u1", title="t1"),  # before since
        fd.FloorVideo(date="2026-09-02", url="u2", title="t2"),  # recess: no house data
    ]
    existing = {"2026-09-03-house-floor"}
    def has_house(date):
        return date != "2026-09-02"
    got = fd.discover_sessions(
        since="2026-09-01", existing_slugs=existing, has_house=has_house, videos=videos)
    assert [v.date for v in got] == ["2026-09-04"]


def test_already_processed_matches_any_floor_slug_on_that_date():
    assert fd._already_processed("2026-09-03", {"2026-09-03-house-floor"}) is True
    assert fd._already_processed("2026-09-03", {"2026-09-03-city-council"}) is False
    assert fd._already_processed("2026-09-04", {"2026-09-03-house-floor"}) is False


def test_dispatch_builds_expected_command_and_returns_code():
    captured = {}
    class Result:  # mimic subprocess.CompletedProcess
        returncode = 0
    def fake_runner(argv, **kwargs):
        captured["argv"] = argv
        return Result()
    code = fd.dispatch(fd.FloorVideo(date="2026-09-04", url="https://youtu.be/aaa", title="t"),
                       runner=fake_runner)
    assert code == 0
    argv = captured["argv"]
    assert "run_local.py" in argv[1]
    assert "--publish-as-draft" in argv
    assert "--congressional-record" in argv
    i = argv.index("--congressional-record")
    assert argv[i + 1] == "2026-09-04" and argv[i + 2] == "house"
    assert "--compute" in argv and argv[argv.index("--compute") + 1] == "modal"
    assert "--no-review" in argv
```

- [ ] **Step 6: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_floor_dispatch.py -v`
Expected: FAIL — `src/floor_dispatch` module does not exist.

- [ ] **Step 7: Implement `src/floor_dispatch.py`**

```python
"""Discover new US House floor sessions and dispatch the CREC oracle pipeline.

Run weekly, off-Mac (GitHub Actions). For each House Clerk floor video that
(a) is newer than the lookback window, (b) has House Congressional Record data,
and (c) is not already in the meetings database, run run_local.py once as a
subprocess to process it and publish it as a not-live draft.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from src import govinfo
from src.publish import existing_meeting_slugs

DEFAULT_CHANNEL_URL = "https://www.youtube.com/@USHouseClerk/streams"

# "US House Floor Proceedings (Thursday, September 4, 2026)" → the inner date.
_FLOOR_DATE_RE = re.compile(r"\(([A-Za-z]+,\s*[A-Za-z]+\s+\d{1,2},\s*\d{4})\)")


@dataclass
class FloorVideo:
    date: str   # YYYY-MM-DD
    url: str
    title: str


def _parse_floor_date(title: str) -> Optional[str]:
    """Extract the ISO session date from a House Clerk floor-video title, or None."""
    if "floor proceedings" not in title.lower():
        return None
    m = _FLOOR_DATE_RE.search(title)
    if not m:
        return None
    try:
        # "Thursday, September 4, 2026" → drop the weekday, parse the rest.
        _weekday, rest = m.group(1).split(",", 1)
        return _dt.datetime.strptime(rest.strip(), "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _default_channel_extractor(url: str, *, cookies_file: Optional[str] = None) -> dict:
    """Flat-list a channel's videos via yt-dlp (metadata only, no download)."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
            "js_runtimes": {"node": {}}}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def list_floor_videos(
    channel_url: str,
    *,
    cookies_file: Optional[str] = None,
    extractor: Callable[..., dict] = _default_channel_extractor,
) -> list[FloorVideo]:
    """All dated House floor videos on the channel, newest-first order preserved."""
    info = extractor(channel_url, cookies_file=cookies_file)
    videos: list[FloorVideo] = []
    for entry in info.get("entries") or []:
        title = entry.get("title") or ""
        date = _parse_floor_date(title)
        url = entry.get("url") or entry.get("webpage_url")
        if date and url:
            videos.append(FloorVideo(date=date, url=url, title=title))
    return videos


def _already_processed(date: str, slugs: set[str]) -> bool:
    """True if any existing meeting slug is a floor meeting on this date."""
    return any(s.startswith(date) and "floor" in s for s in slugs)


def discover_sessions(
    *,
    since: str,
    existing_slugs: set[str],
    has_house: Callable[[str], bool],
    videos: list[FloorVideo],
) -> list[FloorVideo]:
    """Videos on/after `since`, not already processed, with House CREC data."""
    out: list[FloorVideo] = []
    for v in videos:
        if v.date < since:
            continue
        if _already_processed(v.date, existing_slugs):
            continue
        if not has_house(v.date):
            continue
        out.append(v)
    return out


def dispatch(
    video: FloorVideo,
    *,
    cookies_file: Optional[str] = None,
    runner: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
) -> int:
    """Run run_local.py for one session, publishing it as a draft. Returns exit code."""
    run_local = os.path.join(os.path.dirname(os.path.dirname(__file__)), "run_local.py")
    argv = [
        sys.executable, run_local,
        "--input", video.url,
        "--date", video.date,
        "--event-kind", "other",
        "--meeting-type", "House Floor",
        "--congressional-record", video.date, "house",
        "--compute", "modal",
        "--no-review",
        "--publish-as-draft",
    ]
    if cookies_file:
        argv += ["--cookies", cookies_file]
    result = runner(argv, check=False)
    return int(getattr(result, "returncode", 1) or 0)


def _default_since(lookback_days: int) -> str:
    return (_dt.date.today() - _dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly House-floor discovery + dispatch")
    parser.add_argument("--channel-url", default=DEFAULT_CHANNEL_URL)
    parser.add_argument("--since", default=None,
                        help="Only sessions on/after this YYYY-MM-DD "
                             "(default: today minus --lookback-days).")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--max-sessions", type=int, default=6,
                        help="Cap the number of sessions dispatched per run.")
    parser.add_argument("--cookies", default=os.environ.get("YT_DLP_COOKIES") or None,
                        help="Netscape cookies file for yt-dlp (datacenter-IP fallback).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan; do not dispatch.")
    args = parser.parse_args(argv)

    since = args.since or _default_since(args.lookback_days)

    videos = list_floor_videos(args.channel_url, cookies_file=args.cookies)
    existing = existing_meeting_slugs()
    sessions = discover_sessions(
        since=since,
        existing_slugs=existing,
        has_house=lambda d: govinfo.has_chamber_data(d, "house"),
        videos=videos,
    )
    sessions = sessions[: args.max_sessions]

    print(f"Discovered {len(sessions)} new House-floor session(s) since {since}.")
    for v in sessions:
        print(f"  {v.date}  {v.url}")
    if args.dry_run or not sessions:
        return 0

    failures = 0
    for v in sessions:
        print(f"\n=== Dispatching {v.date} ===")
        code = dispatch(v, cookies_file=args.cookies)
        if code != 0:
            failures += 1
            print(f"  FAILED ({v.date}) exit={code}")
    print(f"\nDone: {len(sessions) - failures} ok, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Add `existing_meeting_slugs` to `src/publish.py`**

```python
def existing_meeting_slugs(db_url: Optional[str] = None) -> set[str]:
    """Every slug present in meetings.meetings, any status (dedupe source of truth)."""
    conn = psycopg2.connect(db_url or _require_db_url())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT slug FROM meetings.meetings WHERE slug IS NOT NULL")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
```

- [ ] **Step 9: Run the full floor_dispatch test module**

Run: `.venv/bin/python -m pytest tests/test_floor_dispatch.py -v`
Expected: PASS (all tests).

- [ ] **Step 10: Dry-run smoke against the live channel + DB (network)**

Run: `.venv/bin/python -m src.floor_dispatch --dry-run --lookback-days 20`
Expected: prints a small list of recent House-floor sessions (or zero if all processed). No dispatch. Requires `GOVINFO_API_KEY` and `DATABASE_URL` in the environment (loaded from `.env.local`).

- [ ] **Step 11: Commit**

```bash
git add src/floor_dispatch.py src/govinfo.py src/publish.py tests/test_floor_dispatch.py
git commit -m "feat(floor): weekly House-floor discovery + dispatch (src/floor_dispatch.py)"
```

---

### Task 4: Interim review commands — `--list-drafts` and `--promote`

**Files:**
- Modify: `run_local.py` (two helpers, two argparse flags, `_option_supplied` entries, dispatch in `main`)
- Test: `tests/test_review_drafts.py`

**Interfaces:**
- Produces:
  - `_list_draft_meetings(*, connect=psycopg2.connect) -> list[dict]`
  - `_promote_meeting(slug, *, connect=psycopg2.connect) -> bool` (True if a row was flipped)
  - CLI flags `--list-drafts`, `--promote <SLUG>`.
- Consumes: `src.publish._require_db_url`.

- [ ] **Step 1: Write the failing tests (fake connection)**

Create `tests/test_review_drafts.py`:

```python
import run_local


class _Cur:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount
        self.executed = []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchall(self):
        return self._rows
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, cur):
        self._cur = cur
    def cursor(self):
        return self._cur
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def commit(self): pass
    def close(self): pass


def test_list_draft_meetings_queries_draft_status():
    cur = _Cur(rows=[("2026-09-04-house-floor", "2026-09-04", "House Floor",
                      120, 8, {"gate_verdict": "review", "gate_coverage": 0.62})])
    drafts = run_local._list_draft_meetings(connect=lambda *_a, **_k: _Conn(cur))
    sql = cur.executed[0][0]
    assert "status = 'draft'" in sql.replace('"', "'")
    assert drafts[0]["slug"] == "2026-09-04-house-floor"
    assert drafts[0]["gate_verdict"] == "review"


def test_promote_meeting_flips_status_to_published():
    cur = _Cur(rowcount=1)
    ok = run_local._promote_meeting("2026-09-04-house-floor",
                                    connect=lambda *_a, **_k: _Conn(cur))
    sql, params = cur.executed[0]
    assert "set status = 'published'" in sql.lower().replace('"', "'")
    assert "where slug = %s" in sql.lower()
    assert params == ("2026-09-04-house-floor",)
    assert ok is True


def test_promote_meeting_returns_false_when_no_row():
    cur = _Cur(rowcount=0)
    ok = run_local._promote_meeting("missing",
                                    connect=lambda *_a, **_k: _Conn(cur))
    assert ok is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review_drafts.py -v`
Expected: FAIL — helpers do not exist.

- [ ] **Step 3: Implement the two helpers**

In `run_local.py`, near `_published_meeting_slugs`, add:

```python
def _list_draft_meetings(*, connect=None) -> list[dict]:
    """Draft meetings with their stored gate verdict, newest first."""
    import psycopg2
    from src.publish import _require_db_url
    connect = connect or psycopg2.connect
    conn = connect(_require_db_url())
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT slug, date, meeting_type, segment_count, speaker_count,
                       processing_metadata
                  FROM meetings.meetings
                 WHERE status = 'draft'
                 ORDER BY date DESC
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    out = []
    for slug, date, mtype, segs, spks, pmeta in rows:
        pmeta = pmeta or {}
        out.append({
            "slug": slug, "date": str(date), "meeting_type": mtype,
            "segment_count": segs, "speaker_count": spks,
            "gate_verdict": pmeta.get("gate_verdict"),
            "gate_coverage": pmeta.get("gate_coverage"),
        })
    return out


def _promote_meeting(slug: str, *, connect=None) -> bool:
    """Flip one draft meeting to published (goes live via the API). Returns True if flipped."""
    import psycopg2
    from src.publish import _require_db_url
    connect = connect or psycopg2.connect
    conn = connect(_require_db_url())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE meetings.meetings SET status = 'published', updated_at = NOW() "
                    "WHERE slug = %s AND status = 'draft'",
                    (slug,),
                )
                flipped = (getattr(cur, "rowcount", 0) or 0) > 0
    finally:
        conn.close()
    return flipped
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_review_drafts.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the CLI flags**

In `run_local.py`, near `--review-queue`, add:

```python
    parser.add_argument("--list-drafts", action="store_true",
                        help="List meetings queued as drafts (from the floor automation).")
    parser.add_argument("--promote", metavar="SLUG", default=None,
                        help="Flip one draft meeting to published (go live).")
```

In the `_option_supplied(...)` map, add:

```python
            "--list-drafts": _option_supplied(cli_argv, "--list-drafts"),
            "--promote": _option_supplied(cli_argv, "--promote"),
```

In `main()`, alongside the other early-return standalone actions (e.g. where `--review-queue` is handled), add:

```python
    if getattr(args, "list_drafts", False):
        drafts = _list_draft_meetings()
        if not drafts:
            print("No draft meetings.")
            return
        for d in drafts:
            cov = f"{d['gate_coverage']:.0%}" if d["gate_coverage"] is not None else "—"
            print(f"  {d['slug']:<34} {d['meeting_type']:<14} "
                  f"gate={d['gate_verdict'] or '—':<7} coverage={cov:<5} "
                  f"speakers={d['speaker_count']} segments={d['segment_count']}")
        return

    if getattr(args, "promote", None):
        if _promote_meeting(args.promote):
            print(f"Promoted {args.promote} → published (live via the API).")
        else:
            print(f"No draft meeting with slug {args.promote!r} (already live or missing).")
        return
```

Place these next to the existing `--review-queue` handler so they short-circuit before the normal pipeline path (match how `--review-queue` returns early).

- [ ] **Step 6: Smoke the CLI parses**

Run: `.venv/bin/python run_local.py --help | grep -E "list-drafts|promote"`
Expected: both flags print.
Run: `.venv/bin/python -m pytest tests/test_review_drafts.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add run_local.py tests/test_review_drafts.py
git commit -m "feat(review): --list-drafts and --promote for cloud-processed floor drafts"
```

---

### Task 5: GitHub Actions weekly workflow + de-risk run

**Files:**
- Create: `.github/workflows/house-floor-weekly.yml`

**Interfaces:**
- Consumes: `src/floor_dispatch.py` `main`; GitHub encrypted secrets.

- [ ] **Step 1: Create the workflow**

```yaml
name: House floor — weekly

on:
  schedule:
    - cron: "0 13 * * 6"   # Saturday 13:00 UTC (08:00 EST / 09:00 EDT)
  workflow_dispatch:
    inputs:
      lookback_days:
        description: "How many days back to scan"
        default: "10"
      max_sessions:
        description: "Max sessions to process this run"
        default: "6"

concurrency:
  group: house-floor-weekly
  cancel-in-progress: false

jobs:
  process:
    runs-on: ubuntu-latest
    timeout-minutes: 330
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      GOVINFO_API_KEY: ${{ secrets.GOVINFO_API_KEY }}
      HF_TOKEN: ${{ secrets.HF_TOKEN }}
      PYANNOTE_AI_KEY: ${{ secrets.PYANNOTE_AI_KEY }}
      MODAL_TOKEN_ID: ${{ secrets.MODAL_TOKEN_ID }}
      MODAL_TOKEN_SECRET: ${{ secrets.MODAL_TOKEN_SECRET }}
      YT_DLP_COOKIES: ${{ secrets.YT_DLP_COOKIES && 'cookies.txt' || '' }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Install ffmpeg
        run: sudo apt-get update && sudo apt-get install -y ffmpeg

      - name: Install Python deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Write YouTube cookies (optional fallback)
        if: ${{ secrets.YT_DLP_COOKIES != '' }}
        run: printf '%s' "${{ secrets.YT_DLP_COOKIES }}" > cookies.txt

      - name: Discover + dispatch House-floor sessions
        run: |
          python -m src.floor_dispatch \
            --lookback-days "${{ github.event.inputs.lookback_days || '10' }}" \
            --max-sessions "${{ github.event.inputs.max_sessions || '6' }}"
```

Notes for the implementer:
- The job installs `node` because `src/download.py` sets `js_runtimes: {"node": {}}` for yt-dlp.
- `timeout-minutes: 330` stays under the 6-hour runner cap while allowing several ~2-hour video downloads. Lower it if runs are quick.
- If `requirements.txt` does not pin `yt-dlp`, add an explicit `pip install yt-dlp` line.
- Confirm `3.12` matches the project's supported Python (the `.venv` the team uses). Adjust if the repo targets 3.11/3.13.

- [ ] **Step 2: Add the GitHub secrets**

In the `chrisandrewsedu/on-the-record` repo settings → Secrets and variables → Actions, add: `DATABASE_URL` (IPv4 pooler host), `GOVINFO_API_KEY`, `HF_TOKEN`, `PYANNOTE_AI_KEY`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`. Leave `YT_DLP_COOKIES` unset for now (add it only if Step 4 shows YouTube blocks the runner). Do not add `RENDER_DEPLOY_HOOK_URL` — drafts are not deployed.

- [ ] **Step 3: Confirm Task 2 is merged first**

The public web filter (Task 2) must already be on `main` / deployed before any draft is written to production. Verify it is, or a draft could appear on the live site.

- [ ] **Step 4: De-risk run (manual dispatch)**

Trigger the workflow manually (Actions tab → "House floor — weekly" → Run workflow, `lookback_days=20`, `max_sessions=1`). Watch the log:
- If yt-dlp downloads the video and the run publishes a draft: success.
- If yt-dlp prints "Sign in to confirm you're not a bot" or a 403/429: export a `cookies.txt` from a signed-in YouTube browser session ("Get cookies.txt" extension), paste its contents into the `YT_DLP_COOKIES` secret, and re-run. The workflow writes it to `cookies.txt` and `src/floor_dispatch` passes it through to `run_local --cookies`.

- [ ] **Step 5: Verify the draft is present and hidden**

Run locally: `.venv/bin/python run_local.py --list-drafts`
Expected: the de-risk session appears with a gate verdict and coverage.
Then confirm it is NOT on the public site: load the production meetings list and the meeting URL — the draft must be absent (list) and 404/absent (detail).
Finally promote it if it looks right: `.venv/bin/python run_local.py --promote <slug>`, then confirm it now appears publicly.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/house-floor-weekly.yml
git commit -m "ci: weekly GitHub Actions job to process House-floor sessions as drafts"
```

---

## Self-Review

**1. Spec coverage:**
- Spec §2 (`draft` status) → Task 1 (publish `status` param) + Task 2 (web filter). ✓
- Spec §3 (`--publish-as-draft`, no deploy hook) → Task 1. Deploy hook is already never called on publish (publish.py comment), so "skip deploy" is automatic. ✓
- Spec §4 (discovery + dispatch, subprocess, dedupe, guards) → Task 3. ✓
- Spec §5 (per-session command) → Task 3 `dispatch`. ✓
- Spec §6 (GitHub Actions, Saturday, secrets, no `RENDER_DEPLOY_HOOK_URL`) → Task 5. ✓
- Spec §7 (yt-dlp datacenter-IP de-risk + cookies fallback) → Task 5 Steps 4–5; cookies threaded in Task 3. ✓
- Spec §8 (interim `--list-drafts` / `--promote`, with coverage) → Task 4; coverage persisted via `ProcessingMetadata.gate_coverage` in Task 1. ✓
- Spec §9 non-goals (Senate, auto-live, panel, cloud relabeling, summaries) → none built. ✓

**2. Placeholder scan:** No "TBD"/"handle errors"/"similar to". Each code step is concrete. The one conditional (Task 2 Step 1 API-status check; Task 5 cookies fallback) is a real verification with a defined branch, not a placeholder.

**3. Type consistency:** `status` param name matches across `_upsert_meeting` and `publish_meeting`. `has_chamber_data`, `existing_meeting_slugs`, `FloorVideo(date,url,title)`, `_parse_floor_date`, `discover_sessions(has_house=...)`, `dispatch(runner=...)`, `_list_draft_meetings`, `_promote_meeting` names are used identically in their tests and call sites. `gate_verdict`/`gate_coverage` match between Task 1 (write) and Task 4 (read).

**Refinement noted for the user:** the spec said the deploy hook is skipped for drafts; in fact the publish path no longer triggers any deploy hook at all (the site reads live from the API), so a promote goes live without a deploy. This simplifies Task 4 — `--promote` is a pure DB status flip.
