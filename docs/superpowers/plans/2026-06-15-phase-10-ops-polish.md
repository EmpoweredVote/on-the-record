# Phase 10 — Ops & Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up a Render deploy hook so publishing a meeting automatically rebuilds the site, add per-meeting OpenGraph metadata and a sitemap for SEO, and add a standalone consistency-check script that flags drift between disk transcripts and the DB.

**Architecture:** The deploy hook is a best-effort HTTP POST in `run_local.py` after both publish paths (`--publish` inline + `--publish-meeting` standalone). The sitemap and OG metadata live in the Next.js static-export app (`app/sitemap.ts` + `generateMetadata` on the meeting page). The consistency check is a standalone script at the repo root that cross-references `transcript_named.json` files on disk against `meetings.meetings` rows in the DB.

**Tech Stack:** Python 3 / stdlib `urllib.request` (deploy hook), Next.js 16 App Router (`MetadataRoute`, `generateMetadata`), psycopg2 (consistency check), pytest.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `run_local.py` | modify | add `_trigger_render_rebuild()` + call in 2 publish sites |
| `.env.local.example` | modify | document `RENDER_DEPLOY_HOOK` |
| `tests/test_deploy_hook.py` | create | unit tests for the deploy hook helper |
| `web/app/meetings/[meetingId]/page.tsx` | modify | add `generateMetadata` export |
| `web/app/sitemap.ts` | create | build-time sitemap covering all routes |
| `web/.env.local.example` | modify | document `SITE_URL` |
| `check_consistency.py` | create | standalone consistency check script |
| `tests/test_check_consistency.py` | create | unit tests for disk-vs-DB comparison logic |

---

## Task 1: Deploy Hook Helper

**Files:**
- Modify: `run_local.py` (add helper ~line 1657, call at lines ~1428 and ~1692)
- Modify: `.env.local.example`
- Create: `tests/test_deploy_hook.py`

### What it does

After a successful `publish_meeting()` call, `_trigger_render_rebuild()` reads `RENDER_DEPLOY_HOOK` from env and POSTs to it. No-op when the var is unset. Failure is logged as a WARNING but never raises — a broken hook must not undo a successful DB publish.

---

- [ ] **Step 1: Write the failing tests**

Create `tests/test_deploy_hook.py`:

```python
from unittest.mock import MagicMock, patch

import run_local


def test_noop_when_hook_unset(monkeypatch):
    monkeypatch.delenv("RENDER_DEPLOY_HOOK", raising=False)
    with patch("urllib.request.urlopen") as mock_open:
        run_local._trigger_render_rebuild()
    mock_open.assert_not_called()


def test_posts_to_hook_url(monkeypatch):
    monkeypatch.setenv("RENDER_DEPLOY_HOOK", "https://api.render.com/deploy/test?key=abc")
    mock_resp = MagicMock()
    mock_resp.status = 201
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        run_local._trigger_render_rebuild()
    mock_open.assert_called_once()
    req = mock_open.call_args[0][0]
    assert req.full_url == "https://api.render.com/deploy/test?key=abc"
    assert req.method == "POST"


def test_tolerates_network_failure(monkeypatch, capsys):
    monkeypatch.setenv("RENDER_DEPLOY_HOOK", "https://api.render.com/deploy/test?key=abc")
    with patch("urllib.request.urlopen", side_effect=Exception("network error")):
        run_local._trigger_render_rebuild()  # must not raise
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
```

- [ ] **Step 2: Run tests — expect FAIL (AttributeError: module 'run_local' has no attribute '_trigger_render_rebuild')**

```bash
cd /path/to/on-the-record
pytest tests/test_deploy_hook.py -v
```

Expected: 3 failures, `AttributeError: module 'run_local' has no attribute '_trigger_render_rebuild'`

- [ ] **Step 3: Add `_trigger_render_rebuild` to `run_local.py`**

In `run_local.py`, find the block of standalone functions just before `build_parser()` (around line 1807, near `_meeting_body_slug`). Add this new function immediately after `_meeting_body_slug`:

```python
def _trigger_render_rebuild() -> None:
    """POST to the Render deploy hook to trigger a site rebuild.

    No-op when RENDER_DEPLOY_HOOK is unset. Failure is logged but never raised.
    """
    hook_url = os.environ.get("RENDER_DEPLOY_HOOK", "").strip()
    if not hook_url:
        return
    import urllib.request

    req = urllib.request.Request(hook_url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  Render deploy triggered (HTTP {resp.status})")
    except Exception as e:
        print(f"  WARNING: Render deploy hook failed: {e}")
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_deploy_hook.py -v
```

Expected: 3 passed

- [ ] **Step 5: Wire the hook into the `--publish` inline flow**

In `run_pipeline()`, find this block (around line 1424):

```python
    if getattr(args, "publish", False):
        try:
            from src.publish import publish_meeting

            result = publish_meeting(meeting, state.body_slug)
            print(f"  Published to Supabase: {result.segments} segments, "
                  f"{result.speakers} speakers")
        except Exception as e:
            print(f"  WARNING: Supabase publish failed: {e}")
            print(f"  Retry later with: python run_local.py --publish-meeting {meeting.meeting_id}")
```

Add `_trigger_render_rebuild()` after the successful publish print:

```python
    if getattr(args, "publish", False):
        try:
            from src.publish import publish_meeting

            result = publish_meeting(meeting, state.body_slug)
            print(f"  Published to Supabase: {result.segments} segments, "
                  f"{result.speakers} speakers")
            _trigger_render_rebuild()
        except Exception as e:
            print(f"  WARNING: Supabase publish failed: {e}")
            print(f"  Retry later with: python run_local.py --publish-meeting {meeting.meeting_id}")
```

- [ ] **Step 6: Wire the hook into the `--publish-meeting` standalone flow**

In `_publish_meeting_standalone()`, find the end of the function (around line 1688):

```python
    print(f"Publishing {meeting_id} to Supabase...")
    result = publish_meeting(meeting, body_slug)
    print(f"  Meeting:  {result.meeting_id}")
    print(f"  Segments: {result.segments}")
    print(f"  Speakers: {result.speakers}")
```

Add `_trigger_render_rebuild()` immediately after:

```python
    print(f"Publishing {meeting_id} to Supabase...")
    result = publish_meeting(meeting, body_slug)
    print(f"  Meeting:  {result.meeting_id}")
    print(f"  Segments: {result.segments}")
    print(f"  Speakers: {result.speakers}")
    _trigger_render_rebuild()
```

- [ ] **Step 7: Document the new env var in `.env.local.example`**

In `.env.local.example` at the repo root, append:

```
# Render deploy hook — triggers a site rebuild after --publish / --publish-meeting.
# Get from: Render dashboard → static site → Settings → Deploy Hook.
RENDER_DEPLOY_HOOK=
```

- [ ] **Step 8: Run the full test suite to verify no regressions**

```bash
pytest tests/ -v
```

Expected: all tests pass (new 3 + existing)

- [ ] **Step 9: Commit**

```bash
git add run_local.py .env.local.example tests/test_deploy_hook.py
git commit -m "feat(pipeline): trigger Render deploy hook after publish"
```

---

## Task 2: Per-Meeting OpenGraph Metadata

**Files:**
- Modify: `web/app/meetings/[meetingId]/page.tsx`
- Modify: `web/.env.local.example`

### What it does

Adds a `generateMetadata` export to the meeting page. At build time Next.js calls it alongside `generateStaticParams`; it fetches the meeting + summary and returns a `Metadata` object with title, description, canonical URL, and OpenGraph tags. If `SITE_URL` is unset, URL fields are omitted gracefully.

---

- [ ] **Step 1: Add `SITE_URL` to `web/.env.local.example`**

In `web/.env.local.example`, append:

```
# Canonical origin for sitemap and OpenGraph URLs (no trailing slash).
# Also set this as a build env var on Render.
SITE_URL=https://on-the-record.onrender.com
```

- [ ] **Step 2: Add `generateMetadata` to the meeting page**

In `web/app/meetings/[meetingId]/page.tsx`, the current imports are:

```ts
import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchMeeting, fetchMeetings, fetchSegments, fetchSummary } from "@/lib/queries";
import { eventKindLabel, formatMeetingDate, meetingTitle } from "@/lib/format";
import MeetingView from "./MeetingView";
```

Replace them with (add `Metadata` import from `"next"`):

```ts
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchMeeting, fetchMeetings, fetchSegments, fetchSummary } from "@/lib/queries";
import { eventKindLabel, formatMeetingDate, meetingTitle } from "@/lib/format";
import MeetingView from "./MeetingView";
```

Then add `generateMetadata` between `generateStaticParams` and `MeetingPage`. Insert it after the closing brace of `generateStaticParams` (after line 24):

```ts
const SITE_URL = (process.env.SITE_URL ?? "").replace(/\/$/, "");

export async function generateMetadata({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}): Promise<Metadata> {
  const { meetingId } = await params;

  let meeting: Awaited<ReturnType<typeof fetchMeeting>> = null;
  let summary: Awaited<ReturnType<typeof fetchSummary>> = null;
  try {
    [meeting, summary] = await Promise.all([
      fetchMeeting(meetingId),
      fetchSummary(meetingId).catch(() => null),
    ]);
  } catch {
    // fall through to generic metadata
  }

  if (!meeting) return { title: "Meeting | CouncilScribe" };

  const title = `${meetingTitle(meeting)} | CouncilScribe`;
  const rawDesc = summary?.executive_summary ?? "";
  const description = rawDesc
    ? rawDesc.slice(0, 160) + (rawDesc.length > 160 ? "…" : "")
    : `Public meeting transcript: ${meetingTitle(meeting)}, ${formatMeetingDate(meeting.meeting_date)}.`;

  const url = SITE_URL ? `${SITE_URL}/meetings/${meetingId}` : undefined;

  return {
    title,
    description,
    ...(url && { alternates: { canonical: url } }),
    openGraph: {
      type: "article",
      title,
      description,
      ...(url && { url }),
    },
  };
}
```

- [ ] **Step 3: Verify the build compiles without errors**

```bash
cd web
npm run build 2>&1 | tail -30
```

Expected: build succeeds (TypeScript errors would surface here). If you see a type error on `openGraph.type`, change `"article"` to `"website"` — some Next.js versions don't include `"article"` in the Metadata type union.

- [ ] **Step 4: Commit**

```bash
cd ..
git add web/app/meetings/\[meetingId\]/page.tsx web/.env.local.example
git commit -m "feat(web): add per-meeting OpenGraph metadata"
```

---

## Task 3: Sitemap

**Files:**
- Create: `web/app/sitemap.ts`

### What it does

A Next.js special-file route at `app/sitemap.ts`. When the static export runs (`npm run build`), Next.js calls this function and writes the result as `out/sitemap.xml`. It covers the 4 static routes plus all meeting, people, and topic pages. Requires `SITE_URL` (added in Task 2). Returns an empty array when `SITE_URL` is unset so builds in environments without the var succeed.

---

- [ ] **Step 1: Create `web/app/sitemap.ts`**

```ts
import type { MetadataRoute } from "next";
import { fetchMeetings, fetchPeople, fetchTopics } from "@/lib/queries";

const SITE_URL = (process.env.SITE_URL ?? "").replace(/\/$/, "");

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  if (!SITE_URL) return [];

  const staticRoutes: MetadataRoute.Sitemap = [
    { url: SITE_URL, priority: 1.0 },
    { url: `${SITE_URL}/people`, priority: 0.8 },
    { url: `${SITE_URL}/search`, priority: 0.7 },
    { url: `${SITE_URL}/topics`, priority: 0.7 },
  ];

  const [meetings, people, topics] = await Promise.all([
    fetchMeetings().catch(() => []),
    fetchPeople().catch(() => []),
    fetchTopics().catch(() => []),
  ]);

  const meetingRoutes: MetadataRoute.Sitemap = meetings.map((m) => ({
    url: `${SITE_URL}/meetings/${m.meeting_id}`,
    lastModified: m.meeting_date,
    priority: 0.9,
  }));

  const peopleRoutes: MetadataRoute.Sitemap = people.map((p) => ({
    url: `${SITE_URL}/people/${p.slug}`,
    priority: 0.6,
  }));

  const topicRoutes: MetadataRoute.Sitemap = topics.map((t) => ({
    url: `${SITE_URL}/topics/${t.topic_key}`,
    priority: 0.6,
  }));

  return [...staticRoutes, ...meetingRoutes, ...peopleRoutes, ...topicRoutes];
}
```

- [ ] **Step 2: Verify the build produces `out/sitemap.xml`**

```bash
cd web
SITE_URL=https://on-the-record.onrender.com npm run build 2>&1 | tail -20
ls out/sitemap.xml
```

Expected: `out/sitemap.xml` exists. If it doesn't appear, Next.js 16 may require `export const dynamic = "force-static"` at the top of the file — add it if needed:

```ts
export const dynamic = "force-static";
```

- [ ] **Step 3: Spot-check the sitemap output**

```bash
head -40 out/sitemap.xml
```

Expected: valid XML with `<urlset>` root containing `<url><loc>https://on-the-record.onrender.com/</loc>` etc.

- [ ] **Step 4: Commit**

```bash
cd ..
git add web/app/sitemap.ts
git commit -m "feat(web): add sitemap.xml via app/sitemap.ts"
```

---

## Task 4: Nightly Consistency Check Script

**Files:**
- Create: `check_consistency.py`
- Create: `tests/test_check_consistency.py`

### What it does

Standalone script. Walks `config.MEETINGS_DIR` for `transcript_named.json` files, queries `meetings.meetings` in the DB, cross-references by slug, and prints issues in three categories: UNPUBLISHED (on disk, not in DB), MISSING_DISK (in DB, not on disk), and COUNT_MISMATCH (segment count differs). Exits 0 when clean, 1 when issues found — makes it trivially cronnable.

---

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_consistency.py`:

```python
import json
from pathlib import Path

import pytest


# Import the pure functions directly — no DB calls in these tests.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from check_consistency import compare, find_disk_meetings


# ── find_disk_meetings ────────────────────────────────────────────────────────

def test_empty_dir_returns_empty(tmp_path):
    assert find_disk_meetings(tmp_path) == {}


def test_dir_without_transcript_ignored(tmp_path):
    (tmp_path / "2026-01-01-regular-session").mkdir()
    assert find_disk_meetings(tmp_path) == {}


def test_counts_non_empty_segments(tmp_path):
    m = tmp_path / "2026-01-01-regular-session"
    m.mkdir()
    (m / "transcript_named.json").write_text(json.dumps({
        "segments": [
            {"text": "Hello", "speaker_label": "SPEAKER_00"},
            {"text": "",      "speaker_label": "SPEAKER_01"},   # empty — excluded
            {"text": "World", "speaker_label": "SPEAKER_00"},
        ]
    }))
    result = find_disk_meetings(tmp_path)
    assert result["2026-01-01-regular-session"]["segment_count"] == 2


def test_hidden_dirs_ignored(tmp_path):
    (tmp_path / ".DS_Store").mkdir()
    assert find_disk_meetings(tmp_path) == {}


# ── compare ───────────────────────────────────────────────────────────────────

def test_compare_clean():
    disk = {"mtg-a": {"segment_count": 100}}
    db   = {"mtg-a": {"segment_count": 100}}
    assert compare(disk, db) == []


def test_compare_unpublished():
    disk = {"mtg-a": {"segment_count": 50}}
    db   = {}
    issues = compare(disk, db)
    assert len(issues) == 1
    assert "UNPUBLISHED" in issues[0]
    assert "mtg-a" in issues[0]


def test_compare_missing_disk():
    disk = {}
    db   = {"mtg-a": {"segment_count": 50}}
    issues = compare(disk, db)
    assert len(issues) == 1
    assert "MISSING_DISK" in issues[0]
    assert "mtg-a" in issues[0]


def test_compare_count_mismatch():
    disk = {"mtg-a": {"segment_count": 50}}
    db   = {"mtg-a": {"segment_count": 63}}
    issues = compare(disk, db)
    assert len(issues) == 1
    assert "COUNT_MISMATCH" in issues[0]
    assert "disk=50" in issues[0]
    assert "db=63" in issues[0]


def test_compare_multiple_issues():
    disk = {"mtg-a": {"segment_count": 50}, "mtg-b": {"segment_count": 30}}
    db   = {"mtg-a": {"segment_count": 50}, "mtg-c": {"segment_count": 10}}
    issues = compare(disk, db)
    # mtg-b on disk not in DB → UNPUBLISHED
    # mtg-c in DB not on disk → MISSING_DISK
    labels = {i.split()[0] for i in issues}
    assert "UNPUBLISHED" in labels
    assert "MISSING_DISK" in labels
    assert len(issues) == 2
```

- [ ] **Step 2: Run — expect FAIL (ModuleNotFoundError: No module named 'check_consistency')**

```bash
pytest tests/test_check_consistency.py -v
```

Expected: all fail with `ModuleNotFoundError`

- [ ] **Step 3: Implement `check_consistency.py`**

Create at repo root:

```python
#!/usr/bin/env python3
"""Consistency check: compare disk transcript_named.json files against the DB.

Prints issues in three categories:
  UNPUBLISHED   — meeting on disk with transcript_named.json, not in DB
  MISSING_DISK  — meeting in DB, no local directory
  COUNT_MISMATCH — segment_count in DB differs from non-empty segments on disk

Exit code: 0 (clean), 1 (issues found), 2 (error / bad config).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_DIR))

_env_file = _REPO_DIR / ".env.local"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

from src import config


def find_disk_meetings(meetings_dir: Path) -> dict[str, dict]:
    """Return {slug: {segment_count: int}} for all transcript_named.json on disk."""
    result: dict[str, dict] = {}
    if not meetings_dir.exists():
        return result
    for meeting_dir in sorted(meetings_dir.iterdir()):
        if not meeting_dir.is_dir() or meeting_dir.name.startswith("."):
            continue
        named_path = meeting_dir / "transcript_named.json"
        if not named_path.exists():
            continue
        try:
            with open(named_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = sum(1 for s in data.get("segments", []) if s.get("text"))
            result[meeting_dir.name] = {"segment_count": count}
        except Exception as e:
            result[meeting_dir.name] = {"segment_count": None, "error": str(e)}
    return result


def fetch_db_meetings(db_url: str) -> dict[str, dict]:
    """Return {slug: {segment_count: int}} from published meetings in the DB."""
    import psycopg2

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT slug, segment_count FROM meetings.meetings WHERE status = 'published'"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return {slug: {"segment_count": seg_count} for slug, seg_count in rows}


def compare(disk: dict[str, dict], db: dict[str, dict]) -> list[str]:
    """Cross-reference disk and DB, returning a sorted list of issue strings."""
    issues: list[str] = []
    disk_slugs = set(disk)
    db_slugs = set(db)

    for slug in sorted(disk_slugs - db_slugs):
        issues.append(f"UNPUBLISHED    {slug}  (on disk, not in DB)")

    for slug in sorted(db_slugs - disk_slugs):
        issues.append(f"MISSING_DISK   {slug}  (in DB, not on disk)")

    for slug in sorted(disk_slugs & db_slugs):
        disk_count = disk[slug].get("segment_count")
        db_count = db[slug].get("segment_count")
        if disk_count is None:
            err = disk[slug].get("error", "unknown")
            issues.append(f"READ_ERROR     {slug}  ({err})")
        elif db_count is not None and disk_count != db_count:
            issues.append(f"COUNT_MISMATCH {slug}  disk={disk_count} db={db_count}")

    return issues


def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        print("ERROR: DATABASE_URL not set. Add it to .env.local.", file=sys.stderr)
        sys.exit(2)

    print(f"Scanning disk: {config.MEETINGS_DIR}")
    disk = find_disk_meetings(config.MEETINGS_DIR)
    print(f"  {len(disk)} meeting(s) on disk with transcript_named.json")

    print("Querying DB...")
    db = fetch_db_meetings(db_url)
    print(f"  {len(db)} published meeting(s) in DB")

    issues = compare(disk, db)

    if not issues:
        print("\nAll good — disk and DB are consistent.")
        sys.exit(0)
    else:
        print(f"\n{len(issues)} issue(s) found:")
        for issue in issues:
            print(f"  {issue}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_check_consistency.py -v
```

Expected: all 9 tests pass

- [ ] **Step 5: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass

- [ ] **Step 6: Smoke test the script (requires DATABASE_URL in .env.local)**

```bash
python check_consistency.py
```

Expected: either "All good" or a list of UNPUBLISHED / MISSING_DISK / COUNT_MISMATCH issues with exit code 1.

- [ ] **Step 7: Commit**

```bash
git add check_consistency.py tests/test_check_consistency.py
git commit -m "feat(pipeline): add nightly consistency check script"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Render deploy hook called after `--publish` inline flow (Task 1 Step 5)
- [x] Render deploy hook called after `--publish-meeting` standalone flow (Task 1 Step 6)
- [x] `RENDER_DEPLOY_HOOK` documented in `.env.local.example` (Task 1 Step 7)
- [x] `sitemap.xml` covering `/`, `/people`, `/search`, `/topics`, and all meeting/people/topic pages (Task 3)
- [x] Per-meeting OpenGraph metadata (title, description, canonical URL) (Task 2)
- [x] Nightly consistency check comparing disk vs DB (Task 4)
- [x] Segment count mismatch detection (Task 4)
- [x] Missing publish detection (Task 4)

**Notable omissions (by design):**
- OG image tags: not included — no image generation pipeline exists yet (roadmap notes clip OG cards arrive in Phase 5)
- `robots.txt`: already handled by Next.js default or can be added independently
- Cron wiring for consistency check: the script is standalone and intentionally left for the operator to schedule (cron, Render cron job, etc.)
