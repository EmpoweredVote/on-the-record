# Scaled Source Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v1 source-discovery system from `docs/superpowers/specs/2026-08-02-source-discovery-design.md`: a daily poller that finds candidate media on YouTube (watchlist RSS + per-race searches), triages it in two stages (free prefilter → LLM verdict), writes a queue to Postgres, and a GUI Discovery tab where Chris approves items into batch ingestion.

**Architecture:** New `src/discovery/` package (pure logic + thin I/O), `scripts/poll_discovery.py` CLI in the `poll_agendas.py` mold run by launchd, three new `essentials.*` tables (migration lives in ev-accounts), and a Discovery page in the existing FastAPI GUI that enqueues via `gui.batch.launch_or_enqueue`.

**Tech Stack:** Python 3.11 (`.venv/bin/python`), psycopg2 (connect-per-call, raw SQL), yt-dlp (python module, always with `"js_runtimes": {"node": {}}`), Anthropic via `src/llm_providers.get_provider`, FastAPI + Jinja2 (no JS framework), pytest (pure-function bias, fake cursors, autouse `_no_real_db_env`).

**Conventions that bind every task:**
- Run tests with `.venv/bin/python -m pytest …` (system python lacks deps).
- Engine scripts: `load_env_local()` BEFORE `from src import config`.
- DB code returns neutral values in GUI modules (never raise) but raises in engine modules (`_require_db_url` pattern).
- `matched_politician_ids` binds as `%s::uuid[]` (psycopg2 sends lists as text[] — regression-tested gotcha).
- Commit after each task with the message given in its final step.

**Spec addendum (approved shape + three columns):** `discovered_sources` additionally carries `channel_name`, `channel_id`, `channel_url` — search-discovered items must record their channel identity or the flywheel ("watch this channel") has nothing to insert into `source_outlets`.

---

## Phase 1 — Substrate (migration + shared-seam fixes)

### Task 1: DB migration — three `essentials` tables

**Files:**
- Create: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/migrations/<N>_source_discovery.sql` (N = next free number, see Step 1)
- No on-the-record files.

- [ ] **Step 1: Pick the migration number**

```bash
ls /Users/chrisandrews/Documents/GitHub/ev-accounts/backend/migrations/ | sort -n | tail -3
```

Use the next free integer for `<N>` below.

- [ ] **Step 2: Write the migration file**

Create `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/migrations/<N>_source_discovery.sql`:

```sql
-- Source discovery: outlet registry + discovered-items triage queue + sweep state.
-- Spec: on-the-record/docs/superpowers/specs/2026-08-02-source-discovery-design.md

create table if not exists essentials.source_outlets (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  kind text not null check (kind in ('youtube_channel','podcast_rss','web_page')),
  feed_url text not null unique,
  external_channel_id text,
  state char(2),
  chamber_id uuid references essentials.chambers(id) on delete set null,
  added_via text not null default 'manual' check (added_via in ('seed','flywheel','manual')),
  active boolean not null default true,
  last_polled_at timestamptz,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists essentials.discovered_sources (
  id uuid primary key default gen_random_uuid(),
  source_key text not null unique,
  url text not null,
  title text,
  description_snippet text,
  channel_name text,
  channel_id text,
  channel_url text,
  outlet_id uuid references essentials.source_outlets(id) on delete set null,
  duration_seconds integer,
  published_at timestamptz,
  matched_politician_ids uuid[] not null default '{}',
  race_id uuid references essentials.races(id) on delete set null,
  chamber_id uuid references essentials.chambers(id) on delete set null,
  event_kind_guess text,
  source_tier_guess smallint,
  route text not null default 'ingest' check (route in ('ingest','quote_source')),
  confidence real,
  why text,
  discovered_via text not null check (discovered_via in ('watchlist','search','agent')),
  status text not null default 'pending'
    check (status in ('pending','auto_filtered','approved','rejected','ingested','superseded')),
  status_reason text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  constraint rejected_needs_reason check (status <> 'rejected' or status_reason is not null)
);

create index if not exists discovered_sources_triage_idx
  on essentials.discovered_sources (status, race_id);
create index if not exists discovered_sources_created_idx
  on essentials.discovered_sources (created_at desc);

create table if not exists essentials.discovery_race_state (
  race_id uuid primary key references essentials.races(id) on delete cascade,
  last_swept_at timestamptz,
  last_alarm_at timestamptz
);
```

- [ ] **Step 3: Apply to prod**

Preferred: Supabase MCP `apply_migration` with name `source_discovery` (same route used for `readrank_race_pipeline`). Fallback from shell (DATABASE_URL from `on-the-record/.env.local` — the IPv4 pooler URL):

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record && set -a; . ./.env.local; set +a; psql "$DATABASE_URL" -f /Users/chrisandrews/Documents/GitHub/ev-accounts/backend/migrations/<N>_source_discovery.sql
```

- [ ] **Step 4: Verify**

```bash
psql "$DATABASE_URL" -c "select table_name from information_schema.tables where table_schema='essentials' and table_name in ('source_outlets','discovered_sources','discovery_race_state') order by 1"
```

Expected: all three names.

- [ ] **Step 5: Commit (ev-accounts repo)**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts && git add backend/migrations/<N>_source_discovery.sql && git commit -m "feat: source discovery tables (source_outlets, discovered_sources, discovery_race_state)"
```

### Task 2: `llm_providers.complete()` gains a `system` kwarg

The provider seam is hardcoded to the speaker-ID system prompt (`src/llm_providers.py:15`). Discovery needs its own system prompt through the same seam.

**Files:**
- Modify: `src/llm_providers.py`
- Test: `tests/test_llm_providers.py` (may exist — extend if so)

- [ ] **Step 1: Write the failing test**

Append to (or create) `tests/test_llm_providers.py`:

```python
from src import llm_providers


class _FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs

        class _Msg:
            content = [type("B", (), {"text": "ok"})()]

        return _Msg()


class _FakeAnthropic:
    def __init__(self):
        self.messages = _FakeMessages()


def test_complete_accepts_custom_system_prompt():
    client = _FakeAnthropic()
    p = llm_providers.AnthropicProvider(model="m", client=client)
    p.complete("hi", max_tokens=10, temperature=0.0, system="You screen videos.")
    assert client.messages.kwargs["system"] == "You screen videos."


def test_complete_defaults_to_speaker_id_system_prompt():
    client = _FakeAnthropic()
    p = llm_providers.AnthropicProvider(model="m", client=client)
    p.complete("hi", max_tokens=10, temperature=0.0)
    assert client.messages.kwargs["system"] == llm_providers._SYSTEM_PROMPT
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_llm_providers.py -v`
Expected: FAIL — `complete() got an unexpected keyword argument 'system'`.

- [ ] **Step 3: Implement**

In `src/llm_providers.py`, change the Protocol and both providers (keep default behavior identical):

```python
class SpeakerIDProvider(Protocol):
    name: str
    model: str

    def complete(self, prompt: str, *, max_tokens: int, temperature: float,
                 system: "str | None" = None) -> str: ...
```

`AnthropicProvider.complete`:

```python
    def complete(self, prompt, *, max_tokens, temperature, system=None) -> str:
        msg = self._client.messages.create(
            model=self.model, max_tokens=max_tokens, temperature=temperature,
            system=system or _SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text
```

`OpenAICompatProvider.complete`: same signature; its system message becomes `{"role": "system", "content": system or _SYSTEM_PROMPT}`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_llm_providers.py tests/ -k "llm" -v`
Expected: PASS (including any pre-existing provider tests — the default path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/llm_providers.py tests/test_llm_providers.py && git commit -m "feat: optional system kwarg on LLM provider seam (discovery classifier reuse)"
```

### Task 3: `fetch_source_metadata` returns description/channel identity + gets `js_runtimes`

Stage-1 needs `description`; the flywheel needs `channel_id`/`channel_url`; and every yt-dlp call must set `"js_runtimes": {"node": {}}` (silent-degradation landmine fixed in f856431 — `src/ingest.py:116` still lacks it).

**Files:**
- Modify: `src/ingest.py:102-130` (`fetch_source_metadata`)
- Test: `tests/test_ingest_metadata.py` (create; check for an existing test file covering this function first and extend it instead if present)

- [ ] **Step 1: Write the failing test**

```python
import sys
import types

from src import ingest


class _FakeYDL:
    captured_opts = None
    info = {}

    def __init__(self, opts):
        _FakeYDL.captured_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        return dict(_FakeYDL.info)


def _install_fake_ytdlp(monkeypatch, info):
    _FakeYDL.info = info
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = _FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)


def test_metadata_includes_description_and_channel_identity(monkeypatch):
    _install_fake_ytdlp(monkeypatch, {
        "title": "T", "uploader": "KXAN", "upload_date": "20260801",
        "duration": 3480, "description": "Full debate.",
        "channel_id": "UCabc", "channel_url": "https://www.youtube.com/channel/UCabc",
    })
    meta = ingest.fetch_source_metadata("https://www.youtube.com/watch?v=x")
    assert meta["description"] == "Full debate."
    assert meta["channel_id"] == "UCabc"
    assert meta["channel_url"] == "https://www.youtube.com/channel/UCabc"


def test_metadata_opts_enable_node_js_runtime(monkeypatch):
    _install_fake_ytdlp(monkeypatch, {})
    ingest.fetch_source_metadata("https://www.youtube.com/watch?v=x")
    assert _FakeYDL.captured_opts.get("js_runtimes") == {"node": {}}


def test_metadata_empty_dict_still_has_new_keys(monkeypatch):
    _install_fake_ytdlp(monkeypatch, {})
    meta = ingest.fetch_source_metadata("https://www.youtube.com/watch?v=x")
    for key in ("description", "channel_id", "channel_url"):
        assert key in meta and meta[key] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_ingest_metadata.py -v`
Expected: FAIL — KeyError `description`.

- [ ] **Step 3: Implement**

In `src/ingest.py` `fetch_source_metadata`: add the three keys to the `empty` dict; add `"js_runtimes": {"node": {}}` to the `YoutubeDL` opts; extend the return dict:

```python
        "description": info.get("description") or None,
        "channel_id": info.get("channel_id") or None,
        "channel_url": info.get("channel_url") or None,
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_ingest_metadata.py tests/ -k "ingest or source_meta" -v`
Expected: PASS, and no regression in GUI `/api/source-meta` tests (extra dict keys are additive).

- [ ] **Step 5: Commit**

```bash
git add src/ingest.py tests/test_ingest_metadata.py && git commit -m "feat: source metadata carries description + channel identity; enable node js_runtime"
```

### Task 4: Discovery config constants

**Files:**
- Modify: `src/config.py` (after the `SPEAKER_ID_*` block)
- Test: none (constants; import is covered by every later test)

- [ ] **Step 1: Add the block**

```python
# --- Source discovery (docs/superpowers/specs/2026-08-02-source-discovery-design.md) ---
DISCOVERY_DIR = DRIVE_ROOT / "discovery"        # poll.log + caption cache
DISCOVERY_MODEL_ACTIVE = "haiku"                # key into SPEAKER_ID_MODELS registry
DISCOVERY_CLASSIFY_MAX_TOKENS = 500
DISCOVERY_CLASSIFY_CAP_PER_RUN = 200            # spend cap; truncation is logged loudly
DISCOVERY_CONFIDENCE_FLOOR = 0.30               # below -> stored as auto_filtered
DISCOVERY_CAPTIONS_BAND = (0.35, 0.75)          # mid-confidence band triggers captions peek
DISCOVERY_SEARCH_RESULTS_PER_QUERY = 10         # ytsearchN
DISCOVERY_SEARCH_SLEEP_SECONDS = 2.0            # politeness between searches
DISCOVERY_SHORT_CLIP_MAX_SECONDS = 8 * 60       # < this from a news channel = likely package
DISCOVERY_FULL_EVENT_MIN_SECONDS = 25 * 60      # >= this = likely full event
```

- [ ] **Step 2: Sanity-run existing suite**

Run: `.venv/bin/python -m pytest tests/test_config*.py -v` (or `tests/ -k config`)
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/config.py && git commit -m "feat: discovery config constants"
```

---

## Phase 2 — Pure discovery logic (models, prefilter, feeds, search, classify)

### Task 5: `src/discovery/models.py` + `src/discovery/prefilter.py`

Pure, network-free, DB-free. This is where the house testing bias pays off.

**Files:**
- Create: `src/discovery/__init__.py` (empty)
- Create: `src/discovery/models.py`
- Create: `src/discovery/prefilter.py`
- Test: `tests/test_discovery_prefilter.py`

- [ ] **Step 1: Write `models.py`** (dataclasses only, no logic — no test needed beyond import)

```python
"""Shared dataclasses for the source-discovery engine.

RawItem is the unified shape for anything found by a watchlist feed or a
search sweep; the engine hydrates missing fields via yt-dlp before stage 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawItem:
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    channel_name: Optional[str] = None
    channel_id: Optional[str] = None
    channel_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    published_at: Optional[str] = None  # ISO date or datetime string
    outlet_id: Optional[str] = None     # set for watchlist finds
    via: str = "watchlist"              # 'watchlist' | 'search' | 'agent'


@dataclass
class Outlet:
    id: str
    name: str
    kind: str          # 'youtube_channel' | 'podcast_rss' | 'web_page'
    feed_url: str
    external_channel_id: Optional[str] = None


@dataclass
class TrackedCandidate:
    politician_id: str
    race_id: str
    full_name: str
    race_label: str
    election_date: Optional[str] = None  # ISO date


@dataclass
class PrefilterVerdict:
    passed: bool
    matched_names: list = field(default_factory=list)
    duration_signal: str = "unknown"  # 'short' | 'long' | 'neutral' | 'unknown'
    reason: str = ""


@dataclass
class Verdict:
    relevant: bool
    confidence: float
    candidates_present: list = field(default_factory=list)
    event_kind_guess: Optional[str] = None
    source_tier_guess: Optional[int] = None
    original_vs_clip: Optional[str] = None  # 'original' | 'clip'
    route: str = "ingest"
    why: str = ""
    rejected_reason: Optional[str] = None   # set when the reply wasn't parseable
```

- [ ] **Step 2: Write the failing prefilter tests**

`tests/test_discovery_prefilter.py`:

```python
from src.discovery.prefilter import (
    duration_signal, match_names, normalize, prefilter_item,
)


def test_normalize_strips_accents_case_punctuation():
    assert normalize("Verónica O'Brien-Smith!") == "veronica o brien smith"


def test_match_requires_full_name_not_last_name():
    names = ["Maria Delgado", "Cher"]
    hits = match_names("Delgado wins straw poll", "", names)
    assert hits == []  # last name alone is collision bait
    hits = match_names("Maria Delgado town hall on housing", "", names)
    assert hits == ["Maria Delgado"]


def test_single_token_names_never_match_at_stage_one():
    assert match_names("An evening with Cher", "", ["Cher"]) == []


def test_match_found_in_description_too():
    hits = match_names("Candidate forum", "Featuring Maria Delgado and others", ["Maria Delgado"])
    assert hits == ["Maria Delgado"]


def test_duration_signal_bands():
    assert duration_signal(None) == "unknown"
    assert duration_signal(3 * 60) == "short"
    assert duration_signal(12 * 60) == "neutral"
    assert duration_signal(40 * 60) == "long"


def test_prefilter_rejects_no_name_match():
    v = prefilter_item("City weather update", "", 3600, ["Maria Delgado"])
    assert not v.passed and v.reason == "no tracked candidate name"


def test_prefilter_rejects_short_clip_without_event_term():
    v = prefilter_item("Maria Delgado responds to poll", "", 90, ["Maria Delgado"])
    assert not v.passed and v.duration_signal == "short"


def test_prefilter_passes_short_clip_with_event_term():
    v = prefilter_item("Maria Delgado town hall highlights", "", 90, ["Maria Delgado"])
    assert v.passed


def test_prefilter_passes_long_video_with_name():
    v = prefilter_item("Delgado vs. Ruiz: full debate", "Maria Delgado faces Ana Ruiz",
                       55 * 60, ["Maria Delgado", "Ana Ruiz"])
    assert v.passed and set(v.matched_names) == {"Maria Delgado", "Ana Ruiz"}
    assert v.duration_signal == "long"
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_discovery_prefilter.py -v`
Expected: FAIL — `ModuleNotFoundError: src.discovery`.

- [ ] **Step 4: Implement `prefilter.py`**

```python
"""Stage-1 prefilter: free, pure triage of discovered items.

No DB, no network, no yt-dlp. Decides which raw items are worth a
stage-2 LLM verdict. Tuned for recall — the human gate owns precision.
"""
from __future__ import annotations

import re
import unicodedata

from src import config
from src.discovery.models import PrefilterVerdict

EVENT_TERMS = (
    "debate", "forum", "town hall", "townhall", "town-hall",
    "interview", "q&a", "one-on-one", "sits down", "candidates",
)


def normalize(text: "str | None") -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def duration_signal(duration_seconds: "int | None") -> str:
    if duration_seconds is None:
        return "unknown"
    if duration_seconds < config.DISCOVERY_SHORT_CLIP_MAX_SECONDS:
        return "short"
    if duration_seconds >= config.DISCOVERY_FULL_EVENT_MIN_SECONDS:
        return "long"
    return "neutral"


def match_names(title: "str | None", description: "str | None",
                full_names: list) -> list:
    """Full-name contiguous matches only. Single-token names are skipped —
    they are collision bait ('Cher for School Board' matches concert uploads)."""
    hay = f" {normalize(title)} {normalize(description)} "
    out = []
    for name in full_names:
        norm = normalize(name)
        if len(norm.split()) < 2:
            continue
        if f" {norm} " in hay:
            out.append(name)
    return out


def _has_event_term(title: "str | None") -> bool:
    t = normalize(title)
    return any(term.replace("-", " ") in t for term in EVENT_TERMS)


def prefilter_item(title, description, duration_seconds, full_names) -> PrefilterVerdict:
    matched = match_names(title, description, full_names)
    sig = duration_signal(duration_seconds)
    if not matched:
        return PrefilterVerdict(False, [], sig, "no tracked candidate name")
    if sig == "short" and not _has_event_term(title):
        return PrefilterVerdict(False, matched, sig, "short clip without event term")
    return PrefilterVerdict(True, matched, sig, "name match")
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_prefilter.py -v`
Expected: PASS (9 tests).

- [ ] **Step 6: Commit**

```bash
git add src/discovery/ tests/test_discovery_prefilter.py && git commit -m "feat: discovery models + stage-1 prefilter (pure)"
```

### Task 6: `src/discovery/feeds.py` — YouTube channel RSS + podcast RSS

YouTube exposes Atom at `https://www.youtube.com/feeds/videos.xml?channel_id=UC…` (title, link, published, `media:description`, `yt:videoId`, `yt:channelId` — **no duration**; the engine hydrates survivors). Parse with `xml.etree.ElementTree` — no new dependency.

**Files:**
- Create: `src/discovery/feeds.py`
- Create: `tests/fixtures/discovery_youtube_feed.xml`, `tests/fixtures/discovery_podcast_feed.xml`
- Test: `tests/test_discovery_feeds.py`

- [ ] **Step 1: Create the YouTube fixture** `tests/fixtures/discovery_youtube_feed.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>KXAN</title>
  <entry>
    <yt:videoId>abc12345678</yt:videoId>
    <yt:channelId>UCkxan000000000000000000</yt:channelId>
    <title>Texas Senate debate: full video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc12345678"/>
    <author><name>KXAN</name><uri>https://www.youtube.com/channel/UCkxan000000000000000000</uri></author>
    <published>2026-08-01T21:04:00+00:00</published>
    <media:group>
      <media:title>Texas Senate debate: full video</media:title>
      <media:description>All four candidates meet in Austin.</media:description>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>def12345678</yt:videoId>
    <yt:channelId>UCkxan000000000000000000</yt:channelId>
    <title>Morning weather update</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=def12345678"/>
    <author><name>KXAN</name><uri>https://www.youtube.com/channel/UCkxan000000000000000000</uri></author>
    <published>2026-08-02T12:00:00+00:00</published>
    <media:group>
      <media:title>Morning weather update</media:title>
      <media:description>Hot again.</media:description>
    </media:group>
  </entry>
</feed>
```

- [ ] **Step 2: Create the podcast fixture** `tests/fixtures/discovery_podcast_feed.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>What's Next Austin</title>
    <item>
      <title>Maria Delgado on housing</title>
      <description>A one-on-one with the Senate candidate.</description>
      <link>https://example.buzzsprout.com/ep/101</link>
      <enclosure url="https://example.buzzsprout.com/ep/101.mp3" type="audio/mpeg" length="1"/>
      <pubDate>Sat, 01 Aug 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
```

- [ ] **Step 3: Write the failing tests** `tests/test_discovery_feeds.py`

```python
from pathlib import Path

from src.discovery import feeds
from src.discovery.models import Outlet

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_youtube_feed_maps_entries():
    xml = (FIXTURES / "discovery_youtube_feed.xml").read_text()
    items = feeds.parse_youtube_feed(xml, outlet_id="o1")
    assert len(items) == 2
    first = items[0]
    assert first.url == "https://www.youtube.com/watch?v=abc12345678"
    assert first.title == "Texas Senate debate: full video"
    assert first.description == "All four candidates meet in Austin."
    assert first.channel_name == "KXAN"
    assert first.channel_id == "UCkxan000000000000000000"
    assert first.published_at == "2026-08-01T21:04:00+00:00"
    assert first.outlet_id == "o1" and first.via == "watchlist"
    assert first.duration_seconds is None  # RSS has no duration; hydration fills it


def test_parse_podcast_feed_prefers_page_link_over_enclosure():
    xml = (FIXTURES / "discovery_podcast_feed.xml").read_text()
    items = feeds.parse_podcast_feed(xml, outlet_id="o2")
    assert len(items) == 1
    assert items[0].url == "https://example.buzzsprout.com/ep/101"  # page URL = citation
    assert items[0].title == "Maria Delgado on housing"
    assert items[0].published_at.startswith("2026-08-01")


def test_fetch_outlet_items_dispatches_by_kind(monkeypatch):
    calls = {}

    def fake_fetch(url):
        calls["url"] = url
        return '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    monkeypatch.setattr(feeds, "_fetch_text", fake_fetch)
    outlet = Outlet(id="o1", name="KXAN", kind="youtube_channel",
                    feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCk")
    assert feeds.fetch_outlet_items(outlet) == []
    assert calls["url"] == outlet.feed_url


def test_fetch_outlet_items_web_page_kind_is_noop():
    outlet = Outlet(id="o3", name="Site", kind="web_page", feed_url="https://x.example")
    assert feeds.fetch_outlet_items(outlet) == []
```

- [ ] **Step 4: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_discovery_feeds.py -v`
Expected: FAIL — no module `src.discovery.feeds`.

- [ ] **Step 5: Implement `feeds.py`**

```python
"""Watchlist feed fetching/parsing: YouTube channel Atom + podcast RSS.

Parsing is pure (string in, RawItems out); only _fetch_text touches the
network. web_page outlets are a registered-but-unpolled kind in v1.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

from src.discovery.models import Outlet, RawItem

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def _fetch_text(url: str) -> str:
    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def parse_youtube_feed(xml_text: str, *, outlet_id: "str | None" = None) -> list:
    root = ET.fromstring(xml_text)
    items = []
    for entry in root.findall("atom:entry", _NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=_NS)
        link = entry.find("atom:link[@rel='alternate']", _NS)
        url = link.get("href") if link is not None else (
            f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
        if not url:
            continue
        items.append(RawItem(
            url=url,
            title=entry.findtext("atom:title", default=None, namespaces=_NS),
            description=entry.findtext("media:group/media:description",
                                       default=None, namespaces=_NS),
            channel_name=entry.findtext("atom:author/atom:name",
                                        default=None, namespaces=_NS),
            channel_id=entry.findtext("yt:channelId", default=None, namespaces=_NS),
            channel_url=entry.findtext("atom:author/atom:uri",
                                       default=None, namespaces=_NS),
            published_at=entry.findtext("atom:published", default=None, namespaces=_NS),
            outlet_id=outlet_id,
            via="watchlist",
        ))
    return items


def parse_podcast_feed(xml_text: str, *, outlet_id: "str | None" = None) -> list:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall("./channel/item"):
        page = item.findtext("link")
        enclosure = item.find("enclosure")
        url = page or (enclosure.get("url") if enclosure is not None else None)
        if not url:
            continue
        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).isoformat()
            except (TypeError, ValueError):
                published = None
        items.append(RawItem(
            url=url,
            title=item.findtext("title"),
            description=item.findtext("description"),
            channel_name=root.findtext("./channel/title"),
            published_at=published,
            outlet_id=outlet_id,
            via="watchlist",
        ))
    return items


def fetch_outlet_items(outlet: Outlet) -> list:
    """Network + parse for one outlet. Raises on HTTP/parse errors — the
    engine catches per-outlet and keeps going."""
    if outlet.kind == "youtube_channel":
        return parse_youtube_feed(_fetch_text(outlet.feed_url), outlet_id=outlet.id)
    if outlet.kind == "podcast_rss":
        return parse_podcast_feed(_fetch_text(outlet.feed_url), outlet_id=outlet.id)
    return []  # web_page: registered but not polled in v1
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_feeds.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add src/discovery/feeds.py tests/test_discovery_feeds.py tests/fixtures/discovery_*.xml && git commit -m "feat: discovery watchlist feed parsing (YouTube Atom + podcast RSS)"
```

### Task 7: `src/discovery/search.py` — ytsearch sweeps + hydration

Flat search (one request per query) yields id/title/channel/duration but usually no description or upload date; `hydrate_item` fills gaps for prefilter survivors via the Task-3-extended `fetch_source_metadata`.

**Files:**
- Create: `src/discovery/search.py`
- Test: `tests/test_discovery_search.py`

- [ ] **Step 1: Write the failing tests**

```python
import sys
import types

from src.discovery import search
from src.discovery.models import RawItem


class _FakeYDL:
    captured_opts = None
    captured_query = None
    result = {"entries": []}

    def __init__(self, opts):
        _FakeYDL.captured_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, query, download=False):
        _FakeYDL.captured_query = query
        return dict(_FakeYDL.result)


def _install(monkeypatch, result):
    _FakeYDL.result = result
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = _FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)


def test_ytsearch_builds_flat_query_with_node_runtime(monkeypatch):
    _install(monkeypatch, {"entries": []})
    search.ytsearch('"Maria Delgado" debate', limit=10)
    assert _FakeYDL.captured_query == 'ytsearch10:"Maria Delgado" debate'
    assert _FakeYDL.captured_opts.get("extract_flat") == "in_playlist"
    assert _FakeYDL.captured_opts.get("js_runtimes") == {"node": {}}


def test_ytsearch_maps_entries_and_skips_blank(monkeypatch):
    _install(monkeypatch, {"entries": [
        {"id": "abc12345678", "url": "https://www.youtube.com/watch?v=abc12345678",
         "title": "Full debate", "channel": "KXAN", "duration": 3300},
        {},  # malformed entry
    ]})
    items = search.ytsearch("q", limit=5)
    assert len(items) == 1
    assert items[0].url == "https://www.youtube.com/watch?v=abc12345678"
    assert items[0].channel_name == "KXAN"
    assert items[0].duration_seconds == 3300
    assert items[0].via == "search"


def test_ytsearch_swallows_extractor_errors(monkeypatch):
    class _Boom(_FakeYDL):
        def extract_info(self, query, download=False):
            raise RuntimeError("bot check")

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = _Boom
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    assert search.ytsearch("q", limit=5) == []


def test_hydrate_fills_only_missing_fields(monkeypatch):
    monkeypatch.setattr(search, "fetch_source_metadata", lambda url: {
        "title": "Hydrated title", "channel": "KXAN", "upload_date": "2026-08-01",
        "duration": 3300, "chapters": [], "description": "All four candidates.",
        "channel_id": "UCk", "channel_url": "https://www.youtube.com/channel/UCk",
    })
    item = RawItem(url="https://www.youtube.com/watch?v=abc12345678",
                   title="Full debate", via="search")
    out = search.hydrate_item(item)
    assert out.title == "Full debate"            # existing value kept
    assert out.description == "All four candidates."
    assert out.duration_seconds == 3300
    assert out.published_at == "2026-08-01"
    assert out.channel_id == "UCk"


def test_queries_for_candidate():
    qs = search.queries_for_candidate("Maria Delgado")
    assert '"Maria Delgado" debate' in qs
    assert '"Maria Delgado" town hall' in qs
    assert len(qs) == 4
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_discovery_search.py -v`
Expected: FAIL — no module `src.discovery.search`.

- [ ] **Step 3: Implement `search.py`**

```python
"""Per-race YouTube search sweeps (yt-dlp ytsearch, flat) + item hydration."""
from __future__ import annotations

from src import config
from src.discovery.models import RawItem
from src.ingest import fetch_source_metadata

SEARCH_TERMS = ("debate", "forum", "town hall", "interview")


def queries_for_candidate(full_name: str) -> list:
    return [f'"{full_name}" {term}' for term in SEARCH_TERMS]


def ytsearch(query: str, *, limit: "int | None" = None) -> list:
    """Flat search — one network request, no per-video page fetches.
    Best-effort: any extractor error returns []."""
    n = limit or config.DISCOVERY_SEARCH_RESULTS_PER_QUERY
    try:
        import yt_dlp
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist",
            "js_runtimes": {"node": {}},
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    except Exception:
        return []
    items = []
    for entry in (info or {}).get("entries") or []:
        vid = entry.get("id")
        url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
        if not url:
            continue
        items.append(RawItem(
            url=url,
            title=entry.get("title") or None,
            channel_name=entry.get("channel") or entry.get("uploader") or None,
            channel_id=entry.get("channel_id") or None,
            duration_seconds=int(entry["duration"]) if entry.get("duration") else None,
            via="search",
        ))
    return items


def hydrate_item(item: RawItem) -> RawItem:
    """Fill missing metadata via one yt-dlp metadata fetch (no download).
    Existing values win; hydration only fills gaps."""
    meta = fetch_source_metadata(item.url)
    item.title = item.title or meta.get("title")
    item.description = item.description or meta.get("description")
    item.channel_name = item.channel_name or meta.get("channel")
    item.channel_id = item.channel_id or meta.get("channel_id")
    item.channel_url = item.channel_url or meta.get("channel_url")
    item.duration_seconds = item.duration_seconds or meta.get("duration")
    item.published_at = item.published_at or meta.get("upload_date")
    return item
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_search.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/discovery/search.py tests/test_discovery_search.py && git commit -m "feat: discovery ytsearch sweeps + metadata hydration"
```

### Task 8: `src/discovery/classify.py` — stage-2 LLM verdict + captions peek

Modeled on `src/agenda_interpret.py` (typed result carrying `rejected_reason`, regex-extract-then-`json.loads`), but through the `get_provider` seam with the Task-2 `system` kwarg. Captions judge **discourse shape**, never speaker identity.

**Files:**
- Create: `src/discovery/classify.py`
- Test: `tests/test_discovery_classify.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.discovery import classify
from src.discovery.models import RawItem, Verdict


def test_parse_verdict_happy_path():
    v = classify.parse_verdict("""Here you go:
    {"relevant": true, "confidence": 0.85,
     "candidates_present": ["Maria Delgado"],
     "event_kind": "debate", "source_tier": 1,
     "original_vs_clip": "original", "route": "ingest",
     "why": "58-min video, all candidates in description"}""")
    assert v.relevant and v.confidence == 0.85
    assert v.event_kind_guess == "debate" and v.source_tier_guess == 1
    assert v.rejected_reason is None


def test_parse_verdict_no_json():
    v = classify.parse_verdict("I cannot help with that.")
    assert not v.relevant and v.rejected_reason == "no JSON in reply"


def test_parse_verdict_malformed_json():
    v = classify.parse_verdict('{"relevant": true, "confidence": }')
    assert v.rejected_reason == "malformed JSON"


def test_parse_verdict_clamps_and_validates():
    v = classify.parse_verdict('{"relevant": true, "confidence": 7, "event_kind": "circus", "route": "banana"}')
    assert v.confidence == 1.0
    assert v.event_kind_guess is None   # unknown kind dropped
    assert v.route == "ingest"          # unknown route falls back


def test_vtt_to_text_strips_cues_and_dedupes():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
I will cut <b>property taxes</b>

00:00:02.000 --> 00:00:04.000
I will cut property taxes

00:00:04.000 --> 00:00:06.000
starting with my first budget."""
    text = classify.vtt_to_text(vtt)
    assert text == "I will cut property taxes starting with my first budget."


class _FakeProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.systems = []

    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        return self.replies.pop(0)


def _item():
    return RawItem(url="https://www.youtube.com/watch?v=abc12345678",
                   title="Full debate", description="All four candidates",
                   channel_name="KXAN", duration_seconds=3300, via="search")


def test_classify_item_single_pass_when_confident():
    provider = _FakeProvider(['{"relevant": true, "confidence": 0.9, "why": "clear"}'])
    v = classify.classify_item(provider, _item(), race_label="TX Senate",
                               roster_names=["Maria Delgado"], captions_fetcher=None)
    assert v.confidence == 0.9 and len(provider.prompts) == 1
    assert "Maria Delgado" in provider.prompts[0]
    assert provider.systems[0] == classify._SYSTEM


def test_classify_item_mid_confidence_triggers_captions_second_pass():
    provider = _FakeProvider([
        '{"relevant": true, "confidence": 0.5, "why": "unsure"}',
        '{"relevant": true, "confidence": 0.92, "why": "sustained first-person speech"}',
    ])
    fetched = {}

    def fake_captions(url):
        fetched["url"] = url
        return "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nI will cut taxes"

    v = classify.classify_item(provider, _item(), race_label="TX Senate",
                               roster_names=["Maria Delgado"], captions_fetcher=fake_captions)
    assert v.confidence == 0.92 and len(provider.prompts) == 2
    assert "I will cut taxes" in provider.prompts[1]
    assert fetched["url"] == _item().url
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py -v`
Expected: FAIL — no module `src.discovery.classify`.

- [ ] **Step 3: Implement `classify.py`**

```python
"""Stage-2 LLM verdict for discovered items.

The verdict ranks items for the human skim; it never claims speaker
identity — that stays in the real pipeline post-approval. Structured
output = regex-extract-then-json.loads (house pattern, agenda_interpret.py).
"""
from __future__ import annotations

import json
import re

from src import config
from src.discovery.models import RawItem, Verdict

_SYSTEM = (
    "You screen newly discovered political media for an ingestion pipeline. "
    "You judge from metadata and (sometimes) unlabeled captions. "
    "Respond ONLY with a single JSON object."
)

ALLOWED_KINDS = {"debate", "forum", "news_clip", "press_conference",
                 "podcast", "community_meeting", "other"}
ALLOWED_ROUTES = {"ingest", "quote_source"}

_PROMPT_TEMPLATE = """A tracked election race and a newly found video/audio item are below.
Decide whether this item is an ORIGINAL source of the candidates' own spoken words
(a debate, forum, town hall, long-form interview, press conference, or podcast
appearance) — as opposed to a news package ABOUT them, an ad, or a clip compilation.

Race: {race_label}
Tracked candidates:
{roster}

Item metadata:
- title: {title}
- channel: {channel}
- duration_seconds: {duration}
- published: {published}
- description (truncated): {description}
{captions_block}
Source tiers: 1 = debate/candidate forum; 2 = news interview; 3 = prepared public
remarks (stump speech, town hall, testimony); 4 = candidate-bylined written.
"original_vs_clip": "original" = the full event / substantial segment where the
candidate speaks at length; "clip" = a short excerpt or a package about them.
If captions are provided, judge DISCOURSE SHAPE: sustained first-person policy
speech and moderator/Q&A signatures suggest an original event; third-person
anchor narration with soundbites suggests a news package. Do not guess who is
speaking — only whether candidate speech is present at length.

Respond with JSON only:
{{"relevant": true/false, "confidence": 0.0-1.0,
  "candidates_present": ["names from the tracked list that appear"],
  "event_kind": "debate|forum|news_clip|press_conference|podcast|community_meeting|other",
  "source_tier": 1-4, "original_vs_clip": "original|clip",
  "route": "ingest|quote_source",
  "why": "one sentence citing your strongest evidence"}}"""


def build_prompt(item: RawItem, *, race_label: str, roster_names: list,
                 captions_excerpt: "str | None" = None) -> str:
    roster = "\n".join(f"- {n}" for n in roster_names) or "- (none)"
    captions_block = ""
    if captions_excerpt:
        captions_block = f"\nUnlabeled auto-captions excerpt:\n\"\"\"\n{captions_excerpt}\n\"\"\"\n"
    desc = (item.description or "")[:1500]
    return _PROMPT_TEMPLATE.format(
        race_label=race_label, roster=roster, title=item.title or "(none)",
        channel=item.channel_name or "(unknown)",
        duration=item.duration_seconds if item.duration_seconds is not None else "(unknown)",
        published=item.published_at or "(unknown)", description=desc or "(none)",
        captions_block=captions_block,
    )


def parse_verdict(text: str) -> Verdict:
    match = re.search(r"\{[\s\S]*\}", text or "")
    if not match:
        return Verdict(False, 0.0, rejected_reason="no JSON in reply")
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return Verdict(False, 0.0, rejected_reason="malformed JSON")
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    kind = data.get("event_kind")
    tier = data.get("source_tier")
    try:
        tier = int(tier) if tier is not None else None
    except (TypeError, ValueError):
        tier = None
    ovc = data.get("original_vs_clip")
    route = data.get("route")
    return Verdict(
        relevant=bool(data.get("relevant")),
        confidence=confidence,
        candidates_present=[str(n) for n in data.get("candidates_present") or []],
        event_kind_guess=kind if kind in ALLOWED_KINDS else None,
        source_tier_guess=tier if tier in (1, 2, 3, 4) else None,
        original_vs_clip=ovc if ovc in ("original", "clip") else None,
        route=route if route in ALLOWED_ROUTES else "ingest",
        why=str(data.get("why") or ""),
    )


def vtt_to_text(vtt: str, max_chars: int = 6000) -> str:
    lines = []
    for line in (vtt or "").splitlines():
        line = line.strip()
        if (not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
                or "-->" in line or line.isdigit()):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if lines and lines[-1] == line:
            continue  # auto-captions repeat lines across cues
        lines.append(line)
    return " ".join(lines)[:max_chars]


def classify_item(provider, item: RawItem, *, race_label: str, roster_names: list,
                  captions_fetcher=None) -> Verdict:
    """One LLM pass; a second pass with captions when confidence lands in the
    mid band and a captions_fetcher is supplied. captions_fetcher(url) returns
    raw VTT text or None."""
    text = provider.complete(
        build_prompt(item, race_label=race_label, roster_names=roster_names),
        max_tokens=config.DISCOVERY_CLASSIFY_MAX_TOKENS, temperature=0.0, system=_SYSTEM)
    verdict = parse_verdict(text)
    low, high = config.DISCOVERY_CAPTIONS_BAND
    if (captions_fetcher is not None and verdict.rejected_reason is None
            and low <= verdict.confidence < high):
        vtt = captions_fetcher(item.url)
        if vtt:
            text2 = provider.complete(
                build_prompt(item, race_label=race_label, roster_names=roster_names,
                             captions_excerpt=vtt_to_text(vtt)),
                max_tokens=config.DISCOVERY_CLASSIFY_MAX_TOKENS, temperature=0.0,
                system=_SYSTEM)
            second = parse_verdict(text2)
            if second.rejected_reason is None:
                return second
    return verdict
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/discovery/classify.py tests/test_discovery_classify.py && git commit -m "feat: discovery stage-2 LLM verdict with captions peek"
```

---

## Phase 3 — Engine (DB helpers, orchestration, CLI, seeding)

### Task 9: `src/discovery/db.py` — cursor-bound DB helpers

House pattern: composable functions that take a `cur` (like `publish.resolve_races_for_politicians`), a raising `_require_db_url`, and the `%s::uuid[]` cast for uuid-array binds.

**Files:**
- Create: `src/discovery/db.py`
- Test: `tests/test_discovery_db.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.discovery import db


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def test_insert_discovered_binds_uuid_array_and_conflicts_silently():
    cur = _FakeCursor(rows=[("new-id",)])
    inserted = db.insert_discovered(cur, {
        "source_key": "youtube:abc12345678",
        "url": "https://www.youtube.com/watch?v=abc12345678",
        "title": "Full debate", "description_snippet": "d",
        "channel_name": "KXAN", "channel_id": "UCk", "channel_url": "https://x",
        "outlet_id": None, "duration_seconds": 3300, "published_at": "2026-08-01",
        "matched_politician_ids": ["11111111-1111-1111-1111-111111111111"],
        "race_id": "22222222-2222-2222-2222-222222222222",
        "event_kind_guess": "debate", "source_tier_guess": 1, "route": "ingest",
        "confidence": 0.9, "why": "w", "discovered_via": "search", "status": "pending",
    })
    assert inserted is True
    sql, params = cur.executed[0]
    assert "on conflict (source_key) do nothing" in sql.lower()
    assert "%s::uuid[]" in sql
    assert params[0] == "youtube:abc12345678"


def test_insert_discovered_returns_false_on_conflict():
    cur = _FakeCursor(rows=[])
    assert db.insert_discovered(cur, _minimal_row()) is False


def _minimal_row():
    return {"source_key": "k", "url": "u", "title": None, "description_snippet": None,
            "channel_name": None, "channel_id": None, "channel_url": None,
            "outlet_id": None, "duration_seconds": None, "published_at": None,
            "matched_politician_ids": [], "race_id": None, "event_kind_guess": None,
            "source_tier_guess": None, "route": "ingest", "confidence": None,
            "why": None, "discovered_via": "watchlist", "status": "pending"}


def test_fetch_tracked_candidates_filters_active_pipeline_races():
    cur = _FakeCursor(rows=[("p1", "r1", "Maria Delgado", "TX Senate (general)", "2026-11-03")])
    tracked = db.fetch_tracked_candidates(cur)
    sql, _ = cur.executed[0]
    assert "readrank_race_pipeline" in sql
    assert "'needs_quotes','quotes_staged','published'" in sql.replace(" ", "")
    assert tracked[0].full_name == "Maria Delgado"
    assert tracked[0].race_label == "TX Senate (general)"


def test_alarm_races_excludes_races_with_approved_sources():
    cur = _FakeCursor(rows=[])
    db.alarm_races(cur, days=30)
    sql, params = cur.executed[0]
    assert "not exists" in sql.lower()
    assert "'approved','ingested'" in sql.replace(" ", "")
    assert params == (30,)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_discovery_db.py -v`
Expected: FAIL — no module `src.discovery.db`.

- [ ] **Step 3: Implement `db.py`**

```python
"""Cursor-bound DB helpers for the discovery engine (essentials schema).

Engine-side policy: DATABASE_URL is required (raise), unlike the GUI's
best-effort variants. All functions take a cur so they compose in one
transaction; the engine owns connect/commit.
"""
from __future__ import annotations

import os

import psycopg2

from src.discovery.models import Outlet, TrackedCandidate


def _require_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "Discovery requires DATABASE_URL (add it to .env.local; use the "
            "IPv4 pooler host).")
    return url


def connect():
    return psycopg2.connect(_require_db_url(), sslmode="require")


def fetch_active_outlets(cur) -> list:
    cur.execute(
        "select id::text, name, kind, feed_url, external_channel_id "
        "from essentials.source_outlets where active order by name")
    return [Outlet(id=r[0], name=r[1], kind=r[2], feed_url=r[3],
                   external_channel_id=r[4]) for r in cur.fetchall()]


def fetch_tracked_candidates(cur) -> list:
    cur.execute("""
        select rc.politician_id::text, rc.race_id::text, rc.full_name,
               p.race_label, p.election_date::text
        from essentials.race_candidates rc
        join essentials.readrank_race_pipeline p on p.race_id = rc.race_id
        where p.status in ('needs_quotes','quotes_staged','published')
          and p.election_date >= current_date
          and coalesce(rc.candidate_status, 'active') not in ('withdrawn','removed')
          and rc.full_name is not null
    """)
    return [TrackedCandidate(politician_id=r[0], race_id=r[1], full_name=r[2],
                             race_label=r[3], election_date=r[4])
            for r in cur.fetchall()]


def fetch_sweep_state(cur) -> dict:
    cur.execute("select race_id::text, last_swept_at "
                "from essentials.discovery_race_state")
    return {r[0]: r[1] for r in cur.fetchall()}


def existing_source_keys(cur) -> set:
    cur.execute("select source_key from essentials.discovered_sources")
    return {r[0] for r in cur.fetchall()}


def insert_discovered(cur, row: dict) -> bool:
    """Idempotent on source_key. Returns True when a row was inserted."""
    cur.execute("""
        insert into essentials.discovered_sources
          (source_key, url, title, description_snippet, channel_name, channel_id,
           channel_url, outlet_id, duration_seconds, published_at,
           matched_politician_ids, race_id, event_kind_guess, source_tier_guess,
           route, confidence, why, discovered_via, status)
        values (%s, %s, %s, %s, %s, %s, %s, %s::uuid, %s, %s,
                %s::uuid[], %s::uuid, %s, %s, %s, %s, %s, %s, %s)
        on conflict (source_key) do nothing
        returning id
    """, (
        row["source_key"], row["url"], row["title"], row["description_snippet"],
        row["channel_name"], row["channel_id"], row["channel_url"], row["outlet_id"],
        row["duration_seconds"], row["published_at"],
        row["matched_politician_ids"], row["race_id"], row["event_kind_guess"],
        row["source_tier_guess"], row["route"], row["confidence"], row["why"],
        row["discovered_via"], row["status"],
    ))
    return cur.fetchone() is not None


def mark_outlet_polled(cur, outlet_id: str) -> None:
    cur.execute("update essentials.source_outlets "
                "set last_polled_at = now(), updated_at = now() "
                "where id = %s::uuid", (outlet_id,))


def record_sweep(cur, race_id: str) -> None:
    cur.execute("""
        insert into essentials.discovery_race_state (race_id, last_swept_at)
        values (%s::uuid, now())
        on conflict (race_id) do update set last_swept_at = now()
    """, (race_id,))


def alarm_races(cur, days: int = 30) -> list:
    """Races inside the deadline window, still sourcing, with zero approved
    items on either route. Returns [(race_id, race_label, election_date)]."""
    cur.execute("""
        select p.race_id::text, p.race_label, p.election_date::text
        from essentials.readrank_race_pipeline p
        where p.race_id is not null and p.status = 'needs_quotes'
          and p.election_date between current_date
              and current_date + make_interval(days => %s)
          and not exists (
              select 1 from essentials.discovered_sources d
              where d.race_id = p.race_id and d.status in ('approved','ingested'))
        order by p.election_date
    """, (days,))
    return cur.fetchall()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_db.py -v`
Expected: PASS (4 tests). (The `params == (30,)` assertion matches `make_interval(days => %s)` taking one bind.)

- [ ] **Step 5: Commit**

```bash
git add src/discovery/db.py tests/test_discovery_db.py && git commit -m "feat: discovery DB helpers (cursor-bound, idempotent inserts, alarm query)"
```

### Task 10: `src/discovery/engine.py` — orchestration

Every network/LLM/DB dependency is injected so tests use fakes. The engine owns commit points (per outlet, per race) so a crash mid-run loses at most one unit of work.

**Files:**
- Create: `src/discovery/engine.py`
- Test: `tests/test_discovery_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
import datetime as dt

from src.discovery import db, engine
from src.discovery.models import Outlet, RawItem, TrackedCandidate, Verdict


class _FakeConn:
    def __init__(self):
        self.commits = 0

    def cursor(self):
        return object()

    def commit(self):
        self.commits += 1


TRACKED = [
    TrackedCandidate("p1", "r1", "Maria Delgado", "TX Senate", "2026-11-03"),
    TrackedCandidate("p2", "r1", "Ana Ruiz", "TX Senate", "2026-11-03"),
]
OUTLET = Outlet(id="o1", name="KXAN", kind="youtube_channel", feed_url="https://f")
GOOD_ITEM = RawItem(url="https://www.youtube.com/watch?v=abc12345678",
                    title="Maria Delgado and Ana Ruiz: full debate",
                    description="d", channel_name="KXAN", channel_id="UCk",
                    duration_seconds=3300, published_at="2026-08-01",
                    outlet_id="o1", via="watchlist")
NOISE_ITEM = RawItem(url="https://www.youtube.com/watch?v=zzz12345678",
                     title="Morning weather", description="", channel_name="KXAN",
                     duration_seconds=120, outlet_id="o1", via="watchlist")


def _patch_db(monkeypatch, inserted):
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: list(TRACKED))
    monkeypatch.setattr(db, "fetch_active_outlets", lambda cur: [OUTLET])
    monkeypatch.setattr(db, "fetch_sweep_state", lambda cur: {})
    monkeypatch.setattr(db, "existing_source_keys", lambda cur: set())
    monkeypatch.setattr(db, "insert_discovered",
                        lambda cur, row: inserted.append(row) or True)
    monkeypatch.setattr(db, "mark_outlet_polled", lambda cur, oid: None)
    monkeypatch.setattr(db, "record_sweep", lambda cur, rid: None)


class _FakeProvider:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.calls += 1
        return self.reply


def _run(monkeypatch, inserted, **kwargs):
    provider = kwargs.pop("provider", _FakeProvider(
        '{"relevant": true, "confidence": 0.9, "candidates_present": ["Maria Delgado"],'
        ' "event_kind": "debate", "source_tier": 1, "original_vs_clip": "original",'
        ' "route": "ingest", "why": "long full debate"}'))
    _patch_db(monkeypatch, inserted)
    stats = engine.run_discovery(
        _FakeConn(), provider=provider,
        fetch_feed_items=kwargs.pop("fetch_feed_items", lambda o: [GOOD_ITEM, NOISE_ITEM]),
        ytsearch_fn=kwargs.pop("ytsearch_fn", lambda q: []),
        hydrate_fn=lambda item: item,
        captions_fetcher=None, sleep_fn=lambda s: None,
        meeting_keys=kwargs.pop("meeting_keys", set()),
        today=dt.date(2026, 8, 2), **kwargs)
    return stats, provider


def test_watchlist_flow_inserts_pending_row(monkeypatch):
    inserted = []
    stats, provider = _run(monkeypatch, inserted, skip_sweeps=True)
    assert provider.calls == 1              # noise item died in prefilter, free
    assert len(inserted) == 1
    row = inserted[0]
    assert row["status"] == "pending" and row["race_id"] == "r1"
    assert set(row["matched_politician_ids"]) == {"p1", "p2"}
    assert row["source_key"] == "youtube:abc12345678"
    assert stats.inserted_pending == 1 and stats.prefiltered_out == 1


def test_already_seen_sources_are_skipped_before_classify(monkeypatch):
    inserted = []
    stats, provider = _run(monkeypatch, inserted, skip_sweeps=True,
                           meeting_keys={"youtube:abc12345678"})
    assert provider.calls == 0 and inserted == [] and stats.skipped_seen == 1


def test_low_confidence_stored_as_auto_filtered(monkeypatch):
    inserted = []
    stats, _ = _run(monkeypatch, inserted, skip_sweeps=True, provider=_FakeProvider(
        '{"relevant": false, "confidence": 0.1, "why": "news package"}'))
    assert inserted[0]["status"] == "auto_filtered"
    assert stats.inserted_auto_filtered == 1


def test_spend_cap_stops_classification_loudly(monkeypatch, capsys):
    inserted = []
    stats, provider = _run(monkeypatch, inserted, skip_sweeps=True, classify_cap=0)
    assert provider.calls == 0 and inserted == [] and stats.spend_capped == 1
    assert "SPEND CAP" in capsys.readouterr().out


def test_dry_run_skips_llm_and_writes(monkeypatch, capsys):
    inserted = []
    stats, provider = _run(monkeypatch, inserted, skip_sweeps=True, dry_run=True)
    assert provider.calls == 0 and inserted == []
    assert "DRY-RUN" in capsys.readouterr().out


def test_outlet_failure_is_nonfatal(monkeypatch):
    inserted = []

    def boom(outlet):
        raise RuntimeError("feed 500")

    stats, _ = _run(monkeypatch, inserted, skip_sweeps=True, fetch_feed_items=boom)
    assert stats.failures and inserted == []


def test_sweep_queries_each_candidate_and_records(monkeypatch):
    inserted = []
    queries = []

    def fake_search(q):
        queries.append(q)
        return [GOOD_ITEM]

    stats, provider = _run(monkeypatch, inserted, skip_watchlist=True,
                           ytsearch_fn=fake_search)
    assert len(queries) == 8               # 2 candidates x 4 terms
    assert provider.calls == 1             # dedup: same item after first insert
    assert stats.inserted_pending == 1


def test_sweep_interval_days_bands():
    assert engine.sweep_interval_days(90) == 7
    assert engine.sweep_interval_days(45) == 3
    assert engine.sweep_interval_days(10) == 2


def test_sweep_due_respects_last_swept(monkeypatch):
    today = dt.date(2026, 8, 2)
    assert engine.sweep_due("2026-11-03", None, today) is True
    recent = dt.datetime(2026, 8, 1, 9, 0)
    assert engine.sweep_due("2026-11-03", recent, today) is False  # weekly band
    assert engine.sweep_due("2026-08-20", recent, today) is False  # 2-3 day band, 1 day ago
    old = dt.datetime(2026, 7, 20, 9, 0)
    assert engine.sweep_due("2026-11-03", old, today) is True
    assert engine.sweep_due("2026-07-01", old, today) is False     # election passed
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_discovery_engine.py -v`
Expected: FAIL — no module `src.discovery.engine`.

- [ ] **Step 3: Implement `engine.py`**

```python
"""Discovery run orchestration.

All I/O is injected (provider, feed fetcher, search, hydration, captions,
sleep) so the whole run is testable with fakes. The engine owns commits:
one per outlet and one per swept race, so a crash loses at most one unit.
Log lines follow the poll_agendas convention: UPPERCASE verb prefixes.
"""
from __future__ import annotations

import datetime as dt
import sys
from collections import Counter
from dataclasses import dataclass, field

from src import config
from src.discovery import db
from src.discovery.classify import classify_item
from src.discovery.prefilter import normalize, prefilter_item
from src.discovery.search import queries_for_candidate
from src.source_key import source_key


@dataclass
class RunStats:
    examined: int = 0
    skipped_seen: int = 0
    prefiltered_out: int = 0
    classified: int = 0
    inserted_pending: int = 0
    inserted_auto_filtered: int = 0
    spend_capped: int = 0
    failures: list = field(default_factory=list)


def sweep_interval_days(days_to_election: int) -> int:
    if days_to_election > 60:
        return 7
    if days_to_election > 30:
        return 3
    return 2


def sweep_due(election_date: "str | None", last_swept_at, today: dt.date) -> bool:
    if not election_date:
        return False
    election = dt.date.fromisoformat(election_date[:10])
    days_to = (election - today).days
    if days_to < 0:
        return False
    if last_swept_at is None:
        return True
    last = last_swept_at.date() if hasattr(last_swept_at, "date") else last_swept_at
    return (today - last).days >= sweep_interval_days(days_to)


def _snippet(text: "str | None", limit: int = 500) -> "str | None":
    if not text:
        return None
    return text[:limit]


def run_discovery(conn, *, provider, fetch_feed_items, ytsearch_fn, hydrate_fn,
                  captions_fetcher, sleep_fn, meeting_keys: set, today: dt.date,
                  dry_run: bool = False, race_filter: "str | None" = None,
                  classify_cap: "int | None" = None,
                  skip_watchlist: bool = False, skip_sweeps: bool = False) -> RunStats:
    stats = RunStats()
    cur = conn.cursor()
    tracked = db.fetch_tracked_candidates(cur)
    by_race: dict = {}
    by_norm_name: dict = {}
    for t in tracked:
        by_race.setdefault(t.race_id, []).append(t)
        by_norm_name.setdefault(normalize(t.full_name), []).append(t)
    all_names = sorted({t.full_name for t in tracked})
    seen = db.existing_source_keys(cur) | set(meeting_keys)
    cap = classify_cap if classify_cap is not None else config.DISCOVERY_CLASSIFY_CAP_PER_RUN

    def process(item, roster_names, race_hint):
        key = source_key(item.url)
        if not key or key in seen:
            stats.skipped_seen += 1
            return
        stats.examined += 1
        pf = prefilter_item(item.title, item.description, item.duration_seconds,
                            roster_names)
        if not pf.passed:
            stats.prefiltered_out += 1
            return
        if item.duration_seconds is None or item.description is None:
            item = hydrate_fn(item)
            pf = prefilter_item(item.title, item.description, item.duration_seconds,
                                roster_names)
            if not pf.passed:
                stats.prefiltered_out += 1
                return
        matched = [t for name in pf.matched_names
                   for t in by_norm_name.get(normalize(name), [])]
        if race_hint:
            in_race = [t for t in matched if t.race_id == race_hint]
            matched = in_race or matched
        if not matched:
            stats.prefiltered_out += 1
            return
        race_id = race_hint or Counter(t.race_id for t in matched).most_common(1)[0][0]
        race_cands = by_race.get(race_id, [])
        race_label = race_cands[0].race_label if race_cands else "(unknown race)"
        roster = [t.full_name for t in race_cands] or pf.matched_names
        if dry_run:
            print(f"DRY-RUN candidate [{item.via}] {item.title!r} "
                  f"({item.channel_name}, {item.duration_seconds}s) -> {race_label}")
            seen.add(key)
            return
        if stats.classified >= cap:
            if stats.spend_capped == 0:
                print(f"SPEND CAP reached ({cap} classifications); "
                      "remaining items left for the next run")
            stats.spend_capped += 1
            return
        verdict = classify_item(provider, item, race_label=race_label,
                                roster_names=roster, captions_fetcher=captions_fetcher)
        stats.classified += 1
        pending = (verdict.rejected_reason is None and verdict.relevant
                   and verdict.confidence >= config.DISCOVERY_CONFIDENCE_FLOOR)
        status = "pending" if pending else "auto_filtered"
        matched_ids = sorted({t.politician_id for t in matched
                              if t.race_id == race_id})
        db.insert_discovered(cur, {
            "source_key": key, "url": item.url, "title": item.title,
            "description_snippet": _snippet(item.description),
            "channel_name": item.channel_name, "channel_id": item.channel_id,
            "channel_url": item.channel_url, "outlet_id": item.outlet_id,
            "duration_seconds": item.duration_seconds,
            "published_at": item.published_at,
            "matched_politician_ids": matched_ids, "race_id": race_id,
            "event_kind_guess": verdict.event_kind_guess,
            "source_tier_guess": verdict.source_tier_guess,
            "route": verdict.route, "confidence": verdict.confidence,
            "why": verdict.why or verdict.rejected_reason,
            "discovered_via": item.via, "status": status,
        })
        seen.add(key)
        if status == "pending":
            stats.inserted_pending += 1
            print(f"QUEUED [{item.via}] {item.title!r} -> {race_label} "
                  f"({verdict.confidence:.2f})")
        else:
            stats.inserted_auto_filtered += 1

    if not skip_watchlist:
        for outlet in db.fetch_active_outlets(cur):
            try:
                items = fetch_feed_items(outlet)
            except Exception as exc:  # noqa: BLE001 — per-outlet, loud, non-fatal
                stats.failures.append(f"outlet {outlet.name}: {exc}")
                print(f"FAILED outlet {outlet.name}: {exc}", file=sys.stderr)
                continue
            for item in items:
                process(item, all_names, None)
            if not dry_run:
                db.mark_outlet_polled(cur, outlet.id)
                conn.commit()

    if not skip_sweeps:
        state = db.fetch_sweep_state(cur)
        for race_id, cands in by_race.items():
            if race_filter and race_id != race_filter:
                continue
            if not race_filter and not sweep_due(cands[0].election_date,
                                                 state.get(race_id), today):
                continue
            for cand in cands:
                for query in queries_for_candidate(cand.full_name):
                    try:
                        results = ytsearch_fn(query)
                    except Exception as exc:  # noqa: BLE001
                        stats.failures.append(f"search {query!r}: {exc}")
                        print(f"FAILED search {query!r}: {exc}", file=sys.stderr)
                        continue
                    for item in results:
                        process(item, [c.full_name for c in cands], race_id)
                    sleep_fn(config.DISCOVERY_SEARCH_SLEEP_SECONDS)
            if not dry_run:
                db.record_sweep(cur, race_id)
                conn.commit()

    return stats
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_engine.py -v`
Expected: PASS (9 tests). If `test_sweep_queries_each_candidate_and_records` fails on call count, check that `process()` adds the key to `seen` after the first insert — the same GOOD_ITEM from later queries must dedup.

- [ ] **Step 5: Run the whole discovery suite**

Run: `.venv/bin/python -m pytest tests/ -k discovery -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/discovery/engine.py tests/test_discovery_engine.py && git commit -m "feat: discovery engine orchestration (injected deps, spend cap, cadence)"
```

**Post-review amendments (2026-08-02):** a deep review of the engine code above found seven degraded-path bugs, fixed in `fix: engine degraded-path hardening (per-item guard, cap-aware sweeps, cross-race anchors)` — the plan text above is left as originally written; these are sanctioned deviations layered on top of it.
1. `process()` calls at both loop sites are now wrapped by a `process_safe()` helper that catches per-item exceptions (classifier errors, bad inserts), records a failure, and rolls back the connection instead of killing the whole run.
2. The sweep loop snapshots `spend_capped` per race and skips `record_sweep`/commit when the cap truncated that race's results; it also breaks out of the race loop early (printing `SPEND CAP: deferring remaining sweeps to next run`) once the cap is already exhausted before a race starts, so no further searches are paid for.
3. `matched_politician_ids` is now `sorted({t.politician_id for t in matched})` (no race-id filter) — politicians are the anchor and a cross-race name match should not silently drop a politician_id.
4. `db.fetch_tracked_candidates` gained `order by rc.race_id, rc.full_name` for deterministic race/candidate ordering.
5. `run_discovery` now keeps a per-run `hydrated_cache` keyed by `source_key`, so the same item hit by multiple sweep queries only pays for `hydrate_fn` once.
6. `sweep_due` converts a timezone-aware `last_swept_at` via `.astimezone().date()` instead of a raw `.date()`, so cadence is computed against the local calendar date rather than whatever the DB timestamp's timezone happens to be.
7. An unrecognized `race_filter` (not a key of `by_race`) now appends a loud failure and stderr line instead of silently sweeping nothing.

### Task 11: `scripts/poll_discovery.py` — the CLI the launchd job runs

Thin I/O wrapper in the `poll_agendas.py` mold; all logic already tested in the engine. No new tests — the script only wires tested pieces together.

**Files:**
- Create: `scripts/poll_discovery.py`

- [ ] **Step 1: Implement the script**

```python
"""Poll discovery sources: watchlist feeds + due per-race YouTube sweeps.

Usage:
  .venv/bin/python scripts/poll_discovery.py                # full daily run
  .venv/bin/python scripts/poll_discovery.py --dry-run      # prefilter only, no LLM, no writes
  .venv/bin/python scripts/poll_discovery.py --race RACE_ID # force-sweep one race
  .venv/bin/python scripts/poll_discovery.py --skip-sweeps  # watchlist only
  .venv/bin/python scripts/poll_discovery.py --skip-watchlist
  .venv/bin/python scripts/poll_discovery.py --classify-cap N
  .venv/bin/python scripts/poll_discovery.py --print-alarms # zero-source races, then exit
"""
import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()  # before src.config so CS_DATA_DIR / API keys are visible

from src import config  # noqa: E402
from src.discovery import db, engine, feeds, search  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402
from src.source_key import source_key  # noqa: E402


def _meeting_source_keys() -> set:
    """Every already-processed source, computed once per run (the per-item
    find_meeting_by_source scan would be O(items x meetings))."""
    from gui.runner import _meeting_source_key
    if not config.MEETINGS_DIR.exists():
        return set()
    keys = set()
    for child in sorted(config.MEETINGS_DIR.iterdir()):
        if child.is_dir():
            key = _meeting_source_key(child)
            if key:
                keys.add(key)
    return keys


def _captions_fetcher(url: str):
    from src.download import download_captions_via_ytdlp
    cache = config.DISCOVERY_DIR / "captions"
    cache.mkdir(parents=True, exist_ok=True)
    safe = source_key(url).replace(":", "_").replace("/", "_")
    dest = cache / f"{safe}.vtt"
    if dest.exists():
        return dest.read_text(encoding="utf-8", errors="replace")
    path = download_captions_via_ytdlp(url, dest)
    if path is None:
        return None
    return Path(path).read_text(encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--race", help="race_id: sweep this race now regardless of cadence")
    ap.add_argument("--skip-watchlist", action="store_true")
    ap.add_argument("--skip-sweeps", action="store_true")
    ap.add_argument("--classify-cap", type=int, default=None)
    ap.add_argument("--print-alarms", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    try:
        if args.print_alarms:
            rows = db.alarm_races(conn.cursor())
            if not rows:
                print("No zero-source alarms.")
            for race_id, label, date in rows:
                print(f"ALARM {date} {label} ({race_id}) — no approved sources; "
                      "run an agent gap-filler (see docs/runbooks/source-discovery.md)")
            return 0

        provider = get_provider(config.DISCOVERY_MODEL_ACTIVE)
        stats = engine.run_discovery(
            conn,
            provider=provider,
            fetch_feed_items=feeds.fetch_outlet_items,
            ytsearch_fn=search.ytsearch,
            hydrate_fn=search.hydrate_item,
            captions_fetcher=_captions_fetcher,
            sleep_fn=time.sleep,
            meeting_keys=_meeting_source_keys(),
            today=dt.date.today(),
            dry_run=args.dry_run,
            race_filter=args.race,
            classify_cap=args.classify_cap,
            skip_watchlist=args.skip_watchlist,
            skip_sweeps=args.skip_sweeps,
        )
        print(f"DONE examined={stats.examined} queued={stats.inserted_pending} "
              f"auto_filtered={stats.inserted_auto_filtered} "
              f"prefiltered_out={stats.prefiltered_out} seen={stats.skipped_seen} "
              f"classified={stats.classified} capped={stats.spend_capped}")
        for alarm in db.alarm_races(conn.cursor()):
            print(f"ALARM {alarm[2]} {alarm[1]} — no approved sources")
        if stats.failures:
            print(f"{len(stats.failures)} failure(s)", file=sys.stderr)
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke-test dry-run against prod (reads only, no LLM, no writes)**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record && .venv/bin/python scripts/poll_discovery.py --dry-run --skip-sweeps
```

Expected: exits 0. With an empty `source_outlets` table this prints just the `DONE …` summary line (all zeros) and any `ALARM` lines. Also run `--print-alarms` once.

- [ ] **Step 3: Commit**

```bash
git add scripts/poll_discovery.py && git commit -m "feat: poll_discovery CLI (launchd entrypoint)"
```

### Task 12: `scripts/harvest_outlets.py` — seed the registry from proven channels

One-time (rerunnable, idempotent) harvest: every YouTube source among the ~136 local meetings, resolved to its channel RSS feed via one yt-dlp metadata call per distinct video, inserted as `source_outlets` rows with `added_via='seed'`. Default is a dry-run print; `--apply` writes.

**Files:**
- Create: `scripts/harvest_outlets.py`
- Test: `tests/test_harvest_outlets.py` (pure helper only)

- [ ] **Step 1: Write the failing test for the pure helper**

```python
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts/harvest_outlets.py"
_spec = importlib.util.spec_from_file_location("harvest_outlets", _PATH)
harvest_outlets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_spec and harvest_outlets)


def test_collect_channel_candidates_dedupes_by_channel(tmp_path):
    for mid, url, channel in [
        ("m1", "https://www.youtube.com/watch?v=aaa11111111", "KXAN"),
        ("m2", "https://www.youtube.com/watch?v=bbb11111111", "KXAN"),
        ("m3", "https://www.youtube.com/watch?v=ccc11111111", "PBS Wisconsin"),
        ("m4", "/local/audio.wav", None),
    ]:
        d = tmp_path / mid
        d.mkdir()
        meta = {"audio_source": url}
        if channel:
            meta["processing_metadata"] = {"source_channel": channel}
        (d / "transcript_named.json").write_text(__import__("json").dumps(meta))
    cands = harvest_outlets.collect_channel_candidates(tmp_path)
    assert sorted(cands) == [("KXAN", "https://www.youtube.com/watch?v=aaa11111111"),
                             ("PBS Wisconsin", "https://www.youtube.com/watch?v=ccc11111111")]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_harvest_outlets.py -v`
Expected: FAIL — file not found.

- [ ] **Step 3: Implement the script**

```python
"""Seed source_outlets from channels proven by already-ingested meetings.

Usage:
  .venv/bin/python scripts/harvest_outlets.py            # dry-run: print what would be inserted
  .venv/bin/python scripts/harvest_outlets.py --apply    # insert (idempotent on feed_url)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src import config  # noqa: E402


def collect_channel_candidates(meetings_dir: Path) -> list:
    """(channel_name, sample_video_url) per distinct channel, from
    transcript_named.json of every local meeting. Pure; unit-tested."""
    seen = {}
    for child in sorted(p for p in meetings_dir.iterdir() if p.is_dir()):
        tn = child / "transcript_named.json"
        if not tn.exists():
            continue
        try:
            data = json.loads(tn.read_text())
        except (ValueError, OSError):
            continue
        url = data.get("audio_source") or ""
        channel = (data.get("processing_metadata") or {}).get("source_channel")
        if not channel or "youtube.com" not in url and "youtu.be" not in url:
            continue
        seen.setdefault(channel, url)
    return sorted(seen.items())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="insert rows (default: dry-run)")
    args = ap.parse_args()

    from src.discovery import db  # noqa: PLC0415 — after load_env_local
    from src.ingest import fetch_source_metadata

    candidates = collect_channel_candidates(config.MEETINGS_DIR)
    print(f"{len(candidates)} distinct channels found locally")
    rows = []
    for channel_name, sample_url in candidates:
        meta = fetch_source_metadata(sample_url)
        channel_id = meta.get("channel_id")
        if not channel_id:
            print(f"SKIP {channel_name}: could not resolve channel_id", file=sys.stderr)
            continue
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        rows.append((meta.get("channel") or channel_name, channel_id, feed_url))
        print(f"OUTLET {channel_name} -> {feed_url}")
    if not args.apply:
        print(f"DRY-RUN: {len(rows)} outlets would be inserted (pass --apply)")
        return 0
    conn = db.connect()
    try:
        inserted = 0
        with conn:
            with conn.cursor() as cur:
                for name, channel_id, feed_url in rows:
                    cur.execute("""
                        insert into essentials.source_outlets
                          (name, kind, feed_url, external_channel_id, added_via, notes)
                        values (%s, 'youtube_channel', %s, %s, 'seed',
                                'harvested from ingested meetings')
                        on conflict (feed_url) do nothing
                        returning id
                    """, (name, feed_url, channel_id))
                    if cur.fetchone() is not None:
                        inserted += 1
        print(f"INSERTED {inserted} outlets ({len(rows) - inserted} already present)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test, then the real dry-run**

Run: `.venv/bin/python -m pytest tests/test_harvest_outlets.py -v` — expected PASS.

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record && .venv/bin/python scripts/harvest_outlets.py
```

Expected: `~55 distinct channels found locally`, one `OUTLET …` line each (this makes ~55 yt-dlp metadata calls — a few minutes), ending `DRY-RUN: N outlets would be inserted`.

- [ ] **Step 5: Apply for real, verify, commit**

```bash
.venv/bin/python scripts/harvest_outlets.py --apply
```

Then `psql "$DATABASE_URL" -c "select count(*), kind from essentials.source_outlets group by kind"` — expect ~50+ `youtube_channel` rows.

```bash
git add scripts/harvest_outlets.py tests/test_harvest_outlets.py && git commit -m "feat: harvest proven channels into source_outlets (seed pass)"
```

---

## Phase 4 — GUI Discovery tab

### Task 13: `gui/discovery.py` data layer + read-only `/discovery` page

Copy the library split: data module returns dataclasses with display `@property`s; the template stays dumb. GUI DB code is best-effort (no `DATABASE_URL` → empty page, never a crash).

**Files:**
- Create: `gui/discovery.py`
- Create: `gui/templates/discovery.html`
- Modify: `gui/app.py` (add route in `create_app()`)
- Modify: `gui/templates/library.html:50` (nav link next to "+ New meeting")
- Test: `tests/test_gui_discovery.py`

- [ ] **Step 1: Write the failing tests**

```python
from fastapi.testclient import TestClient

from gui.app import create_app
import gui.discovery as discovery
from gui.discovery import DiscoveredRow


def _row(**over):
    base = dict(
        id="d1", url="https://www.youtube.com/watch?v=abc12345678",
        title="Full debate", description_snippet="All four candidates",
        channel_name="KXAN", channel_id="UCk", channel_url=None, outlet_id=None,
        duration_seconds=3480, published_at="2026-08-01", race_id="r1",
        event_kind_guess="debate", source_tier_guess=1, route="ingest",
        confidence=0.9, why="58-min video, all candidates in description",
        discovered_via="search", status="pending", election_date="2026-11-03",
        race_label="TX · U.S. Senate · General · 2026",
    )
    base.update(over)
    return DiscoveredRow(**base)


def test_thumb_and_duration_properties():
    r = _row()
    assert r.thumb_url == "https://i.ytimg.com/vi/abc12345678/mqdefault.jpg"
    assert r.duration_label == "58m"
    assert _row(duration_seconds=5460).duration_label == "1h31m"
    assert _row(duration_seconds=None).duration_label == "?"
    assert _row(url="https://x.example/ep/1").thumb_url is None


def test_discovery_page_renders_rows_and_health(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda: [_row()])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [("r9", "MI Governor (D primary)", "2026-08-04")],
        "stale_outlets": ["PBS Kansas"], "pending_total": 1})
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    body = resp.text
    assert "Full debate" in body
    assert "TX · U.S. Senate" in body
    assert "MI Governor (D primary)" in body       # alarm strip
    assert "58-min video" in body                   # the classifier's why
    assert "watch this channel" in body.lower()     # flywheel offer (no outlet_id)


def test_discovery_page_empty_state(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda: [])
    monkeypatch.setattr(discovery, "health",
                        lambda: {"alarms": [], "stale_outlets": [], "pending_total": 0})
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert "No pending discoveries" in resp.text


def test_library_links_to_discovery(monkeypatch, tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.get("/")
    assert 'href="/discovery"' in resp.text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: FAIL — no module `gui.discovery`.

- [ ] **Step 3: Implement `gui/discovery.py`**

```python
"""Data layer for the Discovery triage tab.

Best-effort like gui/races.py: no DATABASE_URL or DB error -> empty values,
never a crash. Writes commit explicitly.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

import psycopg2


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")

_SELECT = """
    select d.id::text, d.url, d.title, d.description_snippet, d.channel_name,
           d.channel_id, d.channel_url, d.outlet_id::text, d.duration_seconds,
           d.published_at::text, d.race_id::text, d.event_kind_guess,
           d.source_tier_guess, d.route, d.confidence, d.why, d.discovered_via,
           d.status, e.election_date::text
    from essentials.discovered_sources d
    left join essentials.races r on r.id = d.race_id
    left join essentials.elections e on e.id = r.election_id
"""


@dataclass
class DiscoveredRow:
    id: str
    url: str
    title: Optional[str]
    description_snippet: Optional[str]
    channel_name: Optional[str]
    channel_id: Optional[str]
    channel_url: Optional[str]
    outlet_id: Optional[str]
    duration_seconds: Optional[int]
    published_at: Optional[str]
    race_id: Optional[str]
    event_kind_guess: Optional[str]
    source_tier_guess: Optional[int]
    route: str
    confidence: Optional[float]
    why: Optional[str]
    discovered_via: str
    status: str
    election_date: Optional[str] = None
    race_label: Optional[str] = None  # filled by the route via races.race_labels

    @property
    def thumb_url(self) -> Optional[str]:
        m = _YT_ID.search(self.url or "")
        return f"https://i.ytimg.com/vi/{m.group(1)}/mqdefault.jpg" if m else None

    @property
    def duration_label(self) -> str:
        if not self.duration_seconds:
            return "?"
        minutes = round(self.duration_seconds / 60)
        if minutes >= 60:
            return f"{minutes // 60}h{minutes % 60:02d}m".replace("h00m", "h")
        return f"{minutes}m"

    @property
    def confidence_label(self) -> str:
        return f"{self.confidence:.2f}" if self.confidence is not None else "—"


def _to_row(r) -> DiscoveredRow:
    return DiscoveredRow(*r)


def pending_rows() -> list:
    url = _db_url()
    if not url:
        return []
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(_SELECT + """
                    where d.status = 'pending'
                    order by e.election_date asc nulls last,
                             d.confidence desc nulls last, d.created_at desc
                """)
                return [_to_row(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def get_row(row_id: str) -> Optional[DiscoveredRow]:
    url = _db_url()
    if not url:
        return None
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(_SELECT + " where d.id = %s::uuid", (row_id,))
                r = cur.fetchone()
                return _to_row(r) if r else None
        finally:
            conn.close()
    except Exception:
        return None


def set_status(row_id: str, status: str, reason: "str | None" = None) -> bool:
    url = _db_url()
    if not url:
        return False
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    update essentials.discovered_sources
                    set status = %s, status_reason = %s, reviewed_at = now()
                    where id = %s::uuid
                """, (status, reason, row_id))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def health() -> dict:
    empty = {"alarms": [], "stale_outlets": [], "pending_total": 0}
    url = _db_url()
    if not url:
        return empty
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    select p.race_id::text, p.race_label, p.election_date::text
                    from essentials.readrank_race_pipeline p
                    where p.race_id is not null and p.status = 'needs_quotes'
                      and p.election_date between current_date
                          and current_date + interval '30 days'
                      and not exists (
                          select 1 from essentials.discovered_sources d
                          where d.race_id = p.race_id
                            and d.status in ('approved','ingested'))
                    order by p.election_date
                """)
                alarms = cur.fetchall()
                cur.execute("""
                    select name from essentials.source_outlets
                    where active and (last_polled_at is null
                                      or last_polled_at < now() - interval '48 hours')
                    order by name
                """)
                stale = [r[0] for r in cur.fetchall()]
                cur.execute("select count(*) from essentials.discovered_sources "
                            "where status = 'pending'")
                total = cur.fetchone()[0]
            return {"alarms": alarms, "stale_outlets": stale, "pending_total": total}
        finally:
            conn.close()
    except Exception:
        return empty


def race_slug_for(race_id: "str | None") -> str:
    """Slug for RunParams.race_slug (feeds the meeting id derivation)."""
    if not race_id:
        return ""
    url = _db_url()
    if not url:
        return ""
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    select r.position_name, e.state, r.primary_party, e.election_type
                    from essentials.races r
                    left join essentials.elections e on e.id = r.election_id
                    where r.id = %s::uuid
                """, (race_id,))
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return ""
    if not row:
        return ""
    from gui.races import race_slug
    return race_slug(row[0], row[1], row[2], row[3])


def watch_channel(row: DiscoveredRow) -> "tuple[bool, str]":
    """Flywheel: insert the row's channel as an active outlet."""
    if not row.channel_id:
        return False, "no channel id on this item"
    url = _db_url()
    if not url:
        return False, "no DATABASE_URL"
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={row.channel_id}"
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    insert into essentials.source_outlets
                      (name, kind, feed_url, external_channel_id, added_via)
                    values (%s, 'youtube_channel', %s, %s, 'flywheel')
                    on conflict (feed_url) do nothing
                    returning id
                """, (row.channel_name or row.channel_id, feed_url, row.channel_id))
                inserted = cur.fetchone() is not None
            conn.commit()
            return True, ("watching " + (row.channel_name or row.channel_id)
                          if inserted else "already watching")
        finally:
            conn.close()
    except Exception as exc:
        return False, f"failed: {exc}"
```

- [ ] **Step 4: Add the GET route in `gui/app.py`** (inside `create_app()`, near the library route)

```python
    @app.get("/discovery", response_class=HTMLResponse)
    def discovery_page(request: Request, flash: str = "") -> HTMLResponse:
        from gui import discovery, races
        rows = discovery.pending_rows()
        labels = races.race_labels({r.race_id for r in rows if r.race_id})
        groups: dict = {}
        for r in rows:
            if r.race_id and labels.get(r.race_id):
                r.race_label = labels[r.race_id]
            groups.setdefault(r.race_label or "Unmatched", []).append(r)
        return _templates.TemplateResponse(
            request, "discovery.html",
            {"groups": list(groups.items()), "health": discovery.health(),
             "flash": flash})
```

- [ ] **Step 5: Create `gui/templates/discovery.html`** (standalone document, house style)

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Discovery — Council Scribe</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header class="batch-header">
  <a href="/">&larr; Library</a>
  <strong>Discovery</strong>
  <span class="pill">{{ health.pending_total }} pending</span>
  {% if health.stale_outlets %}
  <span class="pill" title="{{ health.stale_outlets|join(', ') }}">
    {{ health.stale_outlets|length }} stale feed(s)</span>
  {% endif %}
</header>

{% if flash %}<p class="flash">{{ flash }}</p>{% endif %}

{% if health.alarms %}
<section class="flash-pending">
  <strong>Zero-source alarms</strong> — races inside 30 days with no approved sources:
  <ul>
    {% for race_id, label, date in health.alarms %}
    <li>{{ date }} — {{ label }} <span class="pill">gap-filler needed</span></li>
    {% endfor %}
  </ul>
</section>
{% endif %}

{% if not groups %}<p>No pending discoveries.</p>{% endif %}

{% for label, rows in groups %}
<h2>{{ label }}</h2>
<table class="library">
  <tbody>
  {% for r in rows %}
  <tr>
    <td class="name">
      {% if r.thumb_url %}<img class="thumb" src="{{ r.thumb_url }}" alt="" loading="lazy">{% endif %}
      <div>
        <a href="{{ r.url }}" target="_blank" rel="noopener">{{ r.title or r.url }}</a><br>
        <small>{{ r.channel_name or "?" }} · {{ r.duration_label }} ·
          {{ r.published_at or "?" }} · {{ r.event_kind_guess or "?" }}
          {% if r.source_tier_guess %} · tier {{ r.source_tier_guess }}{% endif %}
          · conf {{ r.confidence_label }} · via {{ r.discovered_via }}</small><br>
        <small><em>{{ r.why }}</em></small>
      </div>
    </td>
    <td>
      <form method="post" action="/discovery/{{ r.id }}/approve-ingest">
        <button type="submit" class="enroll">Approve &rarr; ingest</button>
      </form>
      <form method="post" action="/discovery/{{ r.id }}/quote-source">
        <button type="submit">Approve &rarr; quote source</button>
      </form>
      <form method="post" action="/discovery/{{ r.id }}/reject">
        <select name="reason">
          <option value="clip-not-original">clip, not original</option>
          <option value="wrong-person">wrong person</option>
          <option value="tier-5">tier 5</option>
          <option value="duplicate">duplicate</option>
          <option value="other" selected>other</option>
        </select>
        <button type="submit" class="delete-btn">Reject</button>
      </form>
      {% if not r.outlet_id and r.channel_id %}
      <form method="post" action="/discovery/{{ r.id }}/watch-channel">
        <button type="submit" title="Add this channel to the watchlist">
          + Watch this channel</button>
      </form>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endfor %}
</body>
</html>
```

- [ ] **Step 6: Add the nav link in `gui/templates/library.html`** next to the existing `+ New meeting` anchor (line ~50):

```html
    <a class="newlink" href="/discovery">Discovery</a>
```

- [ ] **Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py tests/test_gui_library.py -v`
Expected: new tests PASS; library tests still PASS.

- [ ] **Step 8: Commit**

```bash
git add gui/discovery.py gui/templates/discovery.html gui/templates/library.html gui/app.py tests/test_gui_discovery.py && git commit -m "feat: Discovery triage page (read-only) with health strip"
```

### Task 14: Discovery actions — approve/reject/flywheel endpoints

**Files:**
- Modify: `gui/app.py` (four POST routes in `create_app()`)
- Test: extend `tests/test_gui_discovery.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_gui_discovery.py`)

```python
def test_approve_ingest_enqueues_with_gated_fields(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    launched = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: launched.setdefault("status", status) or True)
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)

    def fake_enqueue(p):
        launched["params"] = p
        return ("started", "mid")

    monkeypatch.setattr(batch, "launch_or_enqueue", fake_enqueue)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert resp.status_code == 303
    p = launched["params"]
    assert p.input == "https://www.youtube.com/watch?v=abc12345678"
    assert p.event_kind == "debate" and p.meeting_type == "Debate"
    assert p.date == "2026-08-01"
    assert p.race_id == "r1" and p.race_slug == "us-senate-tx-general"
    assert p.event_orgs == ["KXAN"]
    assert launched["status"] == "ingested"


def test_approve_ingest_blocks_known_duplicate(monkeypatch):
    import gui.runner as runner
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: "2026-08-01-debate")
    statuses = {}
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: statuses.update(s=status, r=reason) or True)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert resp.status_code == 303 and "duplicate" in resp.headers["location"]
    assert statuses["s"] == "superseded"


def test_reject_requires_and_records_reason(monkeypatch):
    calls = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: calls.update(status=status, reason=reason) or True)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject", data={"reason": "clip-not-original"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert calls == {"status": "rejected", "reason": "clip-not-original"}


def test_quote_source_route_marks_approved(monkeypatch):
    calls = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: calls.update(status=status) or True)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/quote-source", follow_redirects=False)
    assert resp.status_code == 303 and calls["status"] == "approved"


def test_watch_channel_calls_flywheel(monkeypatch):
    called = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())

    def fake_watch(row):
        called["row"] = row
        return (True, "watching KXAN")

    monkeypatch.setattr(discovery, "watch_channel", fake_watch)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/watch-channel", follow_redirects=False)
    assert resp.status_code == 303 and "watching" in resp.headers["location"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: new tests FAIL with 404/405 (routes missing).

- [ ] **Step 3: Implement the routes in `gui/app.py`**

```python
    def _discovery_redirect(flash: str) -> RedirectResponse:
        from urllib.parse import quote
        return RedirectResponse(url=f"/discovery?flash={quote(flash)}", status_code=303)

    @app.post("/discovery/{row_id}/approve-ingest")
    def discovery_approve_ingest(row_id: str):
        import datetime as _dt
        from gui import batch, discovery, runner
        from gui.formmeta import (DEFAULT_COMPUTE, DEFAULT_DIARIZER,
                                  FIELDS_BY_KIND, MEETING_TYPE_DEFAULTS)
        from gui.runner import RunParams
        from src.event_kinds import EVENT_KINDS

        row = discovery.get_row(row_id)
        if row is None:
            raise HTTPException(status_code=404)
        existing = runner.find_meeting_by_source(row.url)
        if existing:
            discovery.set_status(row_id, "superseded",
                                 reason=f"already ingested as {existing}")
            return _discovery_redirect(f"duplicate of {existing}")
        kind = row.event_kind_guess if row.event_kind_guess in EVENT_KINDS else "news_clip"
        fields = FIELDS_BY_KIND.get(kind, ())
        race_id = row.race_id if "race" in fields else None
        params = RunParams(
            input=row.url,
            date=(row.published_at or "")[:10] or _dt.date.today().isoformat(),
            meeting_type=MEETING_TYPE_DEFAULTS.get(kind, "Recording"),
            event_kind=kind,
            title=row.title,
            compute=DEFAULT_COMPUTE,
            diarizer=DEFAULT_DIARIZER,
            event_orgs=[row.channel_name] if row.channel_name else [],
            race_id=race_id,
            race_slug=discovery.race_slug_for(race_id) if race_id else None,
        )
        try:
            outcome, meeting_id = batch.launch_or_enqueue(params)
        except ValueError as exc:
            return _discovery_redirect(f"error: {exc}")
        discovery.set_status(row_id, "ingested")
        return _discovery_redirect(f"{outcome}: {meeting_id or params.title}")

    @app.post("/discovery/{row_id}/quote-source")
    def discovery_quote_source(row_id: str):
        from gui import discovery
        if discovery.get_row(row_id) is None:
            raise HTTPException(status_code=404)
        discovery.set_status(row_id, "approved")
        return _discovery_redirect("approved as quote source")

    @app.post("/discovery/{row_id}/reject")
    def discovery_reject(row_id: str, reason: str = Form("other")):
        from gui import discovery
        if discovery.get_row(row_id) is None:
            raise HTTPException(status_code=404)
        discovery.set_status(row_id, "rejected", reason=reason)
        return _discovery_redirect("rejected")

    @app.post("/discovery/{row_id}/watch-channel")
    def discovery_watch_channel(row_id: str):
        from gui import discovery
        row = discovery.get_row(row_id)
        if row is None:
            raise HTTPException(status_code=404)
        ok, message = discovery.watch_channel(row)
        return _discovery_redirect(message)
```

Note: `runner.find_meeting_by_source` must be monkeypatchable via `gui.runner`, so import the **module** (`from gui import runner`) and call `runner.find_meeting_by_source(...)` — same lazy-import idiom as every other handler.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_discovery.py && git commit -m "feat: discovery approve/reject/quote-source/watch-channel actions"
```

### Task 15: `/new` prefill + "Edit first" link

`GET /new` accepts only `flash`/`label` today; nothing prefills. Add query-param prefill so complex approvals (clip windows, guests) can open the full form pre-populated.

**Files:**
- Modify: `gui/app.py` (`new_meeting_form` signature + context)
- Modify: `gui/templates/new_meeting.html` (value attributes)
- Modify: `gui/templates/discovery.html` (Edit-first link)
- Test: extend `tests/test_gui_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
def test_new_form_prefills_from_query(monkeypatch):
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    client = TestClient(create_app())
    resp = client.get("/new", params={
        "input": "https://www.youtube.com/watch?v=abc12345678",
        "date": "2026-08-01", "title": "Full debate", "event_kind": "debate",
        "meeting_type": "Debate", "race_id": "r1",
        "race_label": "TX · U.S. Senate · General · 2026", "event_orgs": "KXAN",
    })
    body = resp.text
    assert 'value="https://www.youtube.com/watch?v=abc12345678"' in body
    assert 'value="2026-08-01"' in body
    assert 'value="Full debate"' in body
    assert 'value="KXAN"' in body
    assert 'value="r1"' in body
    assert 'value="us-senate-tx-general"' in body
    assert "TX · U.S. Senate" in body
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py::test_new_form_prefills_from_query -v`
Expected: FAIL (no value attributes rendered).

- [ ] **Step 3: Implement**

`gui/app.py` — extend the handler (keep `flash`/`label` behavior unchanged):

```python
    @app.get("/new", response_class=HTMLResponse)
    def new_meeting_form(request: Request, flash: str = "", label: str = "",
                         input: str = "", date: str = "", title: str = "",
                         event_kind: str = "", meeting_type: str = "",
                         race_id: str = "", race_slug: str = "",
                         race_label: str = "", event_orgs: str = "",
                         guest: str = "") -> HTMLResponse:
        if race_id and not race_slug:
            from gui import discovery
            race_slug = discovery.race_slug_for(race_id)
        prefill = {"input": input, "date": date, "title": title,
                   "event_kind": event_kind, "meeting_type": meeting_type,
                   "race_id": race_id, "race_slug": race_slug,
                   "race_label": race_label, "event_orgs": event_orgs,
                   "guest": guest}
        # ... existing context, plus: "prefill": prefill
```

`gui/templates/new_meeting.html` — add value attributes to the existing inputs (ids from the current template; every one defaults to empty string so non-prefilled loads render exactly as today):

- `#f-input` → `value="{{ prefill.input or '' }}"`
- `#f-date` → `value="{{ prefill.date or '' }}"`
- `#f-title` → `value="{{ prefill.title or '' }}"`
- `#f-type` (meeting_type) → `value="{{ prefill.meeting_type or '' }}"`
- `#f-orgs` → `value="{{ prefill.event_orgs or '' }}"`
- `#f-guest` → `value="{{ prefill.guest or '' }}"`
- event-kind `<select>` options → `{% if prefill.event_kind == kind %}selected{% endif %}`
- `#f-race-id` → `value="{{ prefill.race_id or '' }}"`, `#f-race-slug` → `value="{{ prefill.race_slug or '' }}"`
- `#f-race-chosen` span → `{{ prefill.race_label or '' }}`

(`new_meeting.js`'s `applyKindDefault()` only overwrites `meeting_type` when it's blank or a known default, so an explicit prefill survives.)

`gui/templates/discovery.html` — add to the actions cell, after the reject form:

```html
      <a class="pill" href="/new?{{ {'input': r.url,
          'date': (r.published_at or '')[:10], 'title': r.title or '',
          'event_kind': r.event_kind_guess or 'news_clip',
          'race_id': r.race_id or '', 'race_label': r.race_label or '',
          'event_orgs': r.channel_name or ''}|urlencode }}">Edit first</a>
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py tests/test_gui_launch.py -v`
Expected: PASS — including the pre-existing `/new` tests (prefill defaults are all empty).

- [ ] **Step 5: Commit**

```bash
git add gui/app.py gui/templates/new_meeting.html gui/templates/discovery.html tests/test_gui_discovery.py && git commit -m "feat: /new form prefill via query params + discovery edit-first link"
```

---

## Phase 5 — Ops, docs, eval

### Task 16: launchd plist (versioned) + runbook + race-pipeline integration

**Files:**
- Create: `scripts/launchd/vote.empowered.poll-discovery.plist`
- Create: `docs/runbooks/source-discovery.md`
- Modify: `.claude/skills/race-pipeline/SKILL.md` (append one section)

- [ ] **Step 1: Create the plist** (first launchd config under version control — the agendas one lives only in `~/Library/LaunchAgents`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>vote.empowered.poll-discovery</string>
  <!-- Secrets come from the script's own .env.local load; nothing here. -->
  <key>ProgramArguments</key>
  <array>
    <string>/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python</string>
    <string>/Users/chrisandrews/Documents/GitHub/on-the-record/scripts/poll_discovery.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/chrisandrews/Documents/GitHub/on-the-record</string>
  <!-- 08:00 daily — before the 09:00 agenda poll; launchd runs missed jobs on wake. -->
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>8</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/chrisandrews/CouncilScribe/discovery/poll.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/chrisandrews/CouncilScribe/discovery/poll.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Install and verify**

```bash
mkdir -p ~/CouncilScribe/discovery && cp scripts/launchd/vote.empowered.poll-discovery.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/vote.empowered.poll-discovery.plist && launchctl list | grep poll-discovery
```

Expected: one line containing `vote.empowered.poll-discovery`.

- [ ] **Step 3: Write `docs/runbooks/source-discovery.md`**

```markdown
# Source discovery — runbook

Spec: `docs/superpowers/specs/2026-08-02-source-discovery-design.md`
Job: `vote.empowered.poll-discovery` (launchd, daily 08:00) →
`scripts/poll_discovery.py` → log at `~/CouncilScribe/discovery/poll.log`

## Daily workflow

1. Open the GUI (`.venv/bin/python -m gui`) → **Discovery** (link in the library toolbar).
2. Red strip = zero-source alarms (races ≤30 days out, still sourcing, no approved
   sources). Each needs an agent gap-filler (below).
3. Skim pending items per race: title, outlet, duration, the classifier's *why*.
   Click through and scrub ~10s when unsure.
4. Actions: **Approve → ingest** (enqueues into the batch pool), **Edit first**
   (prefilled /new form — use for clip windows/guests), **Approve → quote source**
   (marks for race-pipeline pickup), **Reject** (pick the reason — it trains
   nothing automatically yet, but it suppresses re-surfacing and is the flywheel's
   record), **+ Watch this channel** (adds the outlet to the watchlist).

## Manual runs

    .venv/bin/python scripts/poll_discovery.py --dry-run      # no LLM, no writes
    .venv/bin/python scripts/poll_discovery.py --race RACE_ID # force one race now
    .venv/bin/python scripts/poll_discovery.py --print-alarms

## Agent gap-filler (zero-source alarm)

For each alarmed race, run a one-shot deep hunt in a Claude session:

> Find original sources of the candidates' own spoken words for RACE_LABEL
> (election ELECTION_DATE). Tier order: debates/forums, news interviews,
> prepared remarks, candidate-bylined written. Search the open web, local TV
> and newspaper sites, LWV/civic orgs, candidate sites/channels. For each find,
> insert a row into essentials.discovered_sources (discovered_via='agent',
> status='pending', route='ingest' for video/audio or 'quote_source' for text,
> source_key per src/source_key.py, race_id and matched_politician_ids set,
> why = one evidence sentence). Never C-SPAN. Do not ingest anything.

## Outlet packs (rolling seed)

When a state comes inside ~90 days of an election, run an agent to research and
insert 8–15 outlets (`added_via='seed'`): local TV news channels, PBS + NPR
affiliates, LWV state chapter, Clean-Elections/civic-debate orgs, top newspaper
channels. Insert `essentials.source_outlets` rows with the YouTube channel-RSS
feed_url (`https://www.youtube.com/feeds/videos.xml?channel_id=UC…`) and
`state` set. Outlets are active on insert — the per-item triage gate is the guard.

## First-time setup

1. Task-12 harvest: `.venv/bin/python scripts/harvest_outlets.py --apply`
2. Install the plist (see `scripts/launchd/`).
3. First run by hand: `.venv/bin/python scripts/poll_discovery.py`
```

- [ ] **Step 4: Append to `.claude/skills/race-pipeline/SKILL.md`**

```markdown
## Discovery queue integration

Before hunting sources for a race, check the discovery triage queue — a human
has already vetted these:

    select url, title, channel_name, why, source_tier_guess
    from essentials.discovered_sources
    where race_id = :race_id and status = 'approved' and route = 'quote_source'
    order by source_tier_guess nulls last;

Use them first (they still get the normal verify-then-cite treatment). After
sourcing quotes from a row, mark it consumed:

    update essentials.discovered_sources set status = 'ingested',
      status_reason = 'quotes sourced' where id = :id;

Video shortlists: rows with `route='ingest'` are handled by the GUI Discovery
tab, not by pipeline sessions — do not ingest them from here. When your own
research finds a NEW source worth ingesting, insert a `discovered_sources` row
(`discovered_via='agent'`, `status='pending'`) instead of writing
`ingest-candidates.json`.
```

- [ ] **Step 5: Commit**

```bash
git add scripts/launchd/vote.empowered.poll-discovery.plist docs/runbooks/source-discovery.md .claude/skills/race-pipeline/SKILL.md && git commit -m "feat: discovery launchd job (versioned plist), runbook, race-pipeline integration"
```

### Task 17: Classifier eval harness

Same split as speaker-ID eval: pure scoring in `src/discovery/eval.py`, CLI in `scripts/eval_discovery_classifier.py`, labeled examples as a fixture. Start with 8 hand-labeled cases; grow it from real approve/reject history later.

**Files:**
- Create: `src/discovery/eval.py`
- Create: `tests/fixtures/discovery_eval.jsonl`
- Create: `scripts/eval_discovery_classifier.py`
- Test: `tests/test_discovery_eval.py`

- [ ] **Step 1: Write the failing test**

```python
from src.discovery.eval import classify_outcome, summarize
from src.discovery.models import Verdict


def test_classify_outcome():
    hit = Verdict(relevant=True, confidence=0.9, original_vs_clip="original")
    assert classify_outcome(True, hit) == "true_positive"
    assert classify_outcome(False, hit) == "false_positive"
    miss = Verdict(relevant=False, confidence=0.2)
    assert classify_outcome(True, miss) == "false_negative"
    assert classify_outcome(False, miss) == "true_negative"
    broken = Verdict(relevant=False, confidence=0.0, rejected_reason="no JSON in reply")
    assert classify_outcome(True, broken) == "parse_failure"


def test_summarize_counts():
    s = summarize("haiku", ["true_positive", "true_positive", "false_negative"])
    assert s["model"] == "haiku" and s["true_positive"] == 2
    assert s["recall"] == 2 / 3
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_discovery_eval.py -v`
Expected: FAIL — no module `src.discovery.eval`.

- [ ] **Step 3: Implement `src/discovery/eval.py`**

```python
"""Pure scoring for the discovery classifier eval (no filesystem, no network)."""
from __future__ import annotations

from collections import Counter

from src.discovery.models import Verdict

OUTCOMES = ("true_positive", "true_negative", "false_positive",
            "false_negative", "parse_failure")


def classify_outcome(gold_relevant: bool, verdict: Verdict) -> str:
    if verdict.rejected_reason is not None:
        return "parse_failure"
    if verdict.relevant and gold_relevant:
        return "true_positive"
    if verdict.relevant and not gold_relevant:
        return "false_positive"
    if not verdict.relevant and gold_relevant:
        return "false_negative"
    return "true_negative"


def summarize(model: str, outcomes: list) -> dict:
    counts = Counter(outcomes)
    tp = counts["true_positive"]
    fn = counts["false_negative"]
    fp = counts["false_positive"]
    out = {"model": model, "n": len(outcomes)}
    out.update({name: counts[name] for name in OUTCOMES})
    out["recall"] = tp / (tp + fn) if (tp + fn) else None
    out["precision"] = tp / (tp + fp) if (tp + fp) else None
    return out
```

- [ ] **Step 4: Create `tests/fixtures/discovery_eval.jsonl`** (one JSON object per line; these mirror real ingested-source shapes — a debate, a forum, a long interview, a podcast = positives; a news package, a 30-second ad, a rally clip compilation, an unrelated upload = negatives)

```jsonl
{"title": "Wisconsin governor debate: full video", "description": "The candidates meet for their first debate.", "channel": "WISN 12 News", "duration_seconds": 3540, "race_label": "WI Governor (general)", "roster": ["Alice Example", "Bob Sample"], "gold_relevant": true}
{"title": "Candidate forum: LWV of Kansas", "description": "League of Women Voters candidate forum for governor.", "channel": "League of Women Voters", "duration_seconds": 5210, "race_label": "KS Governor (general)", "roster": ["Alice Example", "Bob Sample"], "gold_relevant": true}
{"title": "One-on-one with Alice Example", "description": "Alice Example sits down for an extended conversation about housing and taxes.", "channel": "KXAN", "duration_seconds": 1420, "race_label": "TX U.S. Senate (general)", "roster": ["Alice Example", "Bob Sample"], "gold_relevant": true}
{"title": "Ep 214: Alice Example", "description": "The Senate candidate joins the pod to talk policy for an hour.", "channel": "What's Next", "duration_seconds": 3890, "race_label": "TX U.S. Senate (general)", "roster": ["Alice Example", "Bob Sample"], "gold_relevant": true}
{"title": "Example leads new poll as race tightens", "description": "Our political team breaks down the latest numbers on Alice Example and Bob Sample.", "channel": "KXAN", "duration_seconds": 165, "race_label": "TX U.S. Senate (general)", "roster": ["Alice Example", "Bob Sample"], "gold_relevant": false}
{"title": "Alice Example for Senate", "description": "Paid for by Example for Senate.", "channel": "Alice Example", "duration_seconds": 31, "race_label": "TX U.S. Senate (general)", "roster": ["Alice Example", "Bob Sample"], "gold_relevant": false}
{"title": "Best moments from the Example rally", "description": "Highlights compilation from Saturday's rally.", "channel": "PoliticsClips247", "duration_seconds": 480, "race_label": "TX U.S. Senate (general)", "roster": ["Alice Example", "Bob Sample"], "gold_relevant": false}
{"title": "City council preview: budget week", "description": "What to expect this week at city hall.", "channel": "KXAN", "duration_seconds": 300, "race_label": "TX U.S. Senate (general)", "roster": ["Alice Example", "Bob Sample"], "gold_relevant": false}
```

- [ ] **Step 5: Create `scripts/eval_discovery_classifier.py`**

```python
"""Eval the discovery classifier against labeled fixtures.

Usage:
  .venv/bin/python scripts/eval_discovery_classifier.py --models haiku sonnet
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src import config  # noqa: E402
from src.discovery.classify import classify_item  # noqa: E402
from src.discovery.eval import classify_outcome, summarize  # noqa: E402
from src.discovery.models import RawItem  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests/fixtures/discovery_eval.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=[config.DISCOVERY_MODEL_ACTIVE])
    args = ap.parse_args()
    examples = [json.loads(line) for line in FIXTURES.read_text().splitlines() if line]
    rows = []
    for model in args.models:
        provider = get_provider(model)
        outcomes = []
        for ex in examples:
            item = RawItem(url="https://example.test/eval", title=ex["title"],
                           description=ex["description"], channel_name=ex["channel"],
                           duration_seconds=ex["duration_seconds"], via="search")
            verdict = classify_item(provider, item, race_label=ex["race_label"],
                                    roster_names=ex["roster"], captions_fetcher=None)
            outcome = classify_outcome(ex["gold_relevant"], verdict)
            outcomes.append(outcome)
            print(f"{model} {outcome:15s} conf={verdict.confidence:.2f} {ex['title']!r}")
        rows.append(summarize(model, outcomes))
    print("\n| model | n | recall | precision | parse_failure |")
    print("|---|---|---|---|---|")
    for r in rows:
        rec = f"{r['recall']:.2f}" if r["recall"] is not None else "—"
        prec = f"{r['precision']:.2f}" if r["precision"] is not None else "—"
        print(f"| {r['model']} | {r['n']} | {rec} | {prec} | {r['parse_failure']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run the unit test, then the live eval once**

Run: `.venv/bin/python -m pytest tests/test_discovery_eval.py -v` — expected PASS.

```bash
.venv/bin/python scripts/eval_discovery_classifier.py
```

Expected: 8 per-example lines + a summary table; recall and precision ≥ 0.75 on this easy set (if not, iterate on `_PROMPT_TEMPLATE` before shipping).

- [ ] **Step 7: Commit**

```bash
git add src/discovery/eval.py scripts/eval_discovery_classifier.py tests/fixtures/discovery_eval.jsonl tests/test_discovery_eval.py && git commit -m "feat: discovery classifier eval harness + starter labeled set"
```

---

## Final verification (after all tasks)

- [ ] Full suite: `.venv/bin/python -m pytest tests/ -q` — all green.
- [ ] End-to-end on prod data, watchlist only (registry now seeded from Task 12):

```bash
.venv/bin/python scripts/poll_discovery.py --skip-sweeps
```

Expected: `DONE …` summary; some `QUEUED` lines if any watched channel posted matching content today (zero is normal on day one).

- [ ] One forced sweep of a race with an imminent election (pick a race_id from `readrank_race_pipeline` with the nearest `election_date`):

```bash
.venv/bin/python scripts/poll_discovery.py --skip-watchlist --race <race_id>
```

- [ ] GUI walk-through with the browser preview (`.claude/launch.json` name `gui`): open `/discovery`, verify grouping/health strip, approve one real high-confidence item end-to-end and confirm it appears in the batch queue on `/`, reject one item, watch one channel.
- [ ] `launchctl list | grep poll-discovery` shows the job; next morning check `~/CouncilScribe/discovery/poll.log`.

## Deliberately not in this plan (per spec v1 gaps)

Beyond-YouTube sweeps, the calendar layer, text-feed watchlists, auto-ingest (mode C), deliberative-platform pollers, server hosting, push alerting, cross-race dedup UI — see spec §"v1 deficiencies → v2+ roadmap".






