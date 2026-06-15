# Phase 6 — Topics & Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag each substantive meeting discussion with Compass issue topics (AI-predicted), render meetings as a topic-labeled outline above the transcript with an executive summary, add cross-meeting topic pages, and show a CalMatters-style predicted/verified provenance badge — per `docs/superpowers/specs/2026-06-13-topics-and-provenance-design.md`.

**Architecture:** Cross-repo, same split as Phases 2–3. **Pipeline** (`src/`) gains a topic-classification stage that fetches the live Compass vocabulary, classifies the summarizer's substantive sections via Haiku, and checkpoints results. **ev-accounts** fixes the broken summary read path (read `meetings.meetings.summary` JSONB instead of non-existent tables), adds a `meetings.meeting_topics` table written by `publish.py`, attaches topics to sections at read, and serves `/api/topics` + `/api/topics/[key]`. **web** renders the exec-summary block + section outline with topic labels and provenance badges, plus `/topics` index and `/topics/[key]` pages.

**Tech Stack:** Python + psycopg2 + Anthropic SDK (pipeline); Express + pg + vitest/supertest (ev-accounts); Next.js 16 static export (web).

**Conventions (identical to Phases 2–3):** `pool.query` only for `meetings.*`; explicit row types + camelCase mappers, never spread rows; `Number()` on pg numerics; `optionalAuth`; validation before DB; 422/`VALIDATION_ERROR`, 500/`INTERNAL_ERROR`. Web types snake_case via explicit mappers. Work on `master` (ev-accounts) / `main` (web) / current branch (pipeline); do NOT push. End every commit message with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**Scope:** council-only (all current meetings are council sessions). Event-kinds and event→entity modeling are deferred and additive.

---

## File structure

| File | Repo | Responsibility |
|---|---|---|
| `src/config.py` | pipeline | add `TOPIC_CLASSIFY_MODEL`, `SUBSTANTIVE_SECTION_TYPES` |
| `src/models.py` | pipeline | add `SectionTopic` dataclass + `Meeting.section_topics` |
| `src/topics.py` | pipeline | NEW — vocab fetch, classification, validation |
| `tests/test_topics.py` | pipeline | NEW — unit tests for the deterministic parts |
| `run_local.py` | pipeline | wire the classify stage after summary |
| `src/publish.py` | pipeline | write `meetings.meeting_topics` from `section_topics` |
| `backend/migrations/365_meeting_topics.sql` | ev-accounts | NEW — `meeting_topics` table |
| `backend/src/lib/meetingsService.ts` | ev-accounts | fix summary read → JSONB; attach topics to sections |
| `backend/src/lib/topicsService.ts` | ev-accounts | NEW — topic list + detail queries |
| `backend/src/routes/topics.ts` + `.test.ts` | ev-accounts | NEW — `/api/topics`, `/api/topics/:key` |
| `backend/src/index.ts` | ev-accounts | mount topics router |
| `web/lib/types.ts`, `web/lib/queries.ts` | web | summary/section/speaker/topic types + fetchers |
| `web/components/ProvenanceBadge.tsx` | web | NEW — predicted/verified badge |
| `web/app/meetings/[meetingId]/*` | web | exec summary + outline + speaker badges |
| `web/app/page.tsx` | web | summary preview on index cards |
| `web/app/topics/page.tsx`, `web/app/topics/[key]/page.tsx` | web | NEW — topic index + detail |
| `web/app/globals.css` | web | styles |

---

## Part A — Pipeline (work in `~/Documents/GitHub/on-the-record`)

### Task 1: config + model carriers

**Files:**
- Modify: `src/config.py`
- Modify: `src/models.py`

- [ ] **Step 1: Add config constants**

Append to `src/config.py` (near the `SUMMARY_*` block, ~line 50):

```python
# Topic classification (Phase 6)
TOPIC_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"
# Section types worth tagging with a topic (procedural/roll_call/opening/closing skipped)
SUBSTANTIVE_SECTION_TYPES = ("discussion", "public_comment", "consent_agenda", "vote")
```

- [ ] **Step 2: Add the `SectionTopic` dataclass and `Meeting.section_topics`**

In `src/models.py`, add after the `SummarySection` class:

```python
@dataclass
class SectionTopic:
    """AI-predicted topic tags for one summary section (by array index)."""
    section_index: int
    topic_keys: list[str] = field(default_factory=list)
    confidence: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "section_index": self.section_index,
            "topic_keys": self.topic_keys,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SectionTopic":
        return cls(
            section_index=d["section_index"],
            topic_keys=d.get("topic_keys", []),
            confidence=d.get("confidence"),
        )
```

In the `Meeting` dataclass, add the field (after `summary`):

```python
    section_topics: list[SectionTopic] = field(default_factory=list)
```

`section_topics` is deliberately **not** added to `Meeting.to_dict()` — it is checkpointed separately as `topics.json` (Task 3/4) and kept off the summary JSONB so `meetings.meeting_topics` stays the single source of truth.

- [ ] **Step 3: Verify import + commit**

Run: `cd ~/Documents/GitHub/on-the-record && python -c "from src.models import SectionTopic, Meeting; m=Meeting(meeting_id='x',city='c',date='2026-01-01'); print(m.section_topics)"`
Expected: `[]`

```bash
git add src/config.py src/models.py && git commit -m "feat(pipeline): SectionTopic model + topic-classify config"
```

### Task 2: topic classification module (TDD on the deterministic parts)

**Files:**
- Create: `src/topics.py`
- Create: `tests/test_topics.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_topics.py
from src.models import SummarySection
from src.topics import substantive_sections, validate_topic_keys, build_classification_prompt


def _section(stype, title="T", idx_text="hello world"):
    return SummarySection(section_type=stype, title=title, content=idx_text,
                          start_time=0.0, end_time=1.0, start_segment=0, end_segment=1)


def test_substantive_sections_filters_by_type_and_keeps_index():
    sections = [
        _section("opening"),
        _section("discussion"),
        _section("roll_call"),
        _section("vote"),
    ]
    result = substantive_sections(sections)
    # returns (original_index, section) pairs for substantive types only
    assert [i for i, _ in result] == [1, 3]


def test_validate_drops_out_of_vocab_keys():
    vocab = {"housing", "data-centers"}
    assert validate_topic_keys(["housing", "made-up", "data-centers"], vocab) == ["housing", "data-centers"]


def test_validate_dedupes_and_preserves_order():
    vocab = {"housing", "transit"}
    assert validate_topic_keys(["transit", "housing", "transit"], vocab) == ["transit", "housing"]


def test_validate_empty_when_none_match():
    assert validate_topic_keys(["nope"], {"housing"}) == []


def test_build_prompt_includes_keys_and_section_titles():
    vocab = [
        {"topic_key": "housing", "short_title": "Housing", "question_text": "Rent control?"},
    ]
    sections = [(1, _section("discussion", title="Affordable Housing Ordinance"))]
    prompt = build_classification_prompt(sections, vocab)
    assert "housing" in prompt
    assert "Affordable Housing Ordinance" in prompt
    assert "section 1" in prompt or "1" in prompt
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/Documents/GitHub/on-the-record && python -m pytest tests/test_topics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.topics'`

- [ ] **Step 3: Implement `src/topics.py`**

```python
"""Stage 6: Topic classification — tag substantive summary sections with
Compass issue topic_keys (AI-predicted).

Vocabulary is the live set of inform.compass_topics, fetched at publish time
so it tracks Compass (including rewrites). One Haiku call per meeting maps each
substantive section to 0..N topic_keys drawn ONLY from that vocabulary; the
model's choices are validated against the vocab before use.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from . import config
from .models import SectionTopic, SummarySection


def fetch_live_topics(conn) -> list[dict]:
    """Read live Compass topics (the classification vocabulary) via psycopg2.

    Returns dicts with topic_key, short_title, question_text.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT topic_key, short_title, question_text
            FROM inform.compass_topics
            WHERE is_live = true
            ORDER BY topic_key
            """
        )
        rows = cur.fetchall()
    return [
        {"topic_key": r[0], "short_title": r[1], "question_text": r[2]}
        for r in rows
    ]


def substantive_sections(
    sections: list[SummarySection],
) -> list[tuple[int, SummarySection]]:
    """Return (original_index, section) for substantive section types only."""
    return [
        (i, s)
        for i, s in enumerate(sections)
        if s.section_type in config.SUBSTANTIVE_SECTION_TYPES
    ]


def validate_topic_keys(keys: list[str], vocab: set[str]) -> list[str]:
    """Keep only in-vocabulary keys, deduped, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k in vocab and k not in seen:
            seen.add(k)
            out.append(k)
    return out


_CLASSIFY_SYSTEM = """You tag city council meeting discussion sections with the political issues they're about.

You are given a fixed list of allowed topics (each: a key, a short title, and the question it represents) and a set of meeting sections. For each section, choose the topic keys that the discussion is genuinely about — zero, one, or several. Only use keys from the allowed list. Many routine items (contract renewals, procedural votes) match no topic; return an empty list for those rather than forcing a match.

Respond with ONLY valid JSON:
{
  "sections": [
    {"section_index": 1, "topic_keys": ["housing"]},
    {"section_index": 3, "topic_keys": []}
  ]
}"""


def build_classification_prompt(
    sections: list[tuple[int, SummarySection]],
    vocab: list[dict],
) -> str:
    """Build the user prompt: allowed topics + the sections to classify."""
    topic_lines = [
        f"- {t['topic_key']}: {t.get('short_title') or ''} — {t.get('question_text') or ''}".rstrip(" —")
        for t in vocab
    ]
    sec_lines = []
    for idx, sec in sections:
        body = (sec.content or "")[:600]
        sec_lines.append(f"section {idx} — \"{sec.title}\":\n{body}")
    return (
        "ALLOWED TOPICS:\n"
        + "\n".join(topic_lines)
        + "\n\nSECTIONS TO TAG:\n"
        + "\n\n".join(sec_lines)
    )


def classify_sections(
    client,
    sections: list[SummarySection],
    vocab: list[dict],
) -> list[SectionTopic]:
    """Classify substantive sections into topic_keys. Returns one SectionTopic
    per substantive section (topic_keys possibly empty)."""
    subs = substantive_sections(sections)
    if not subs:
        return []

    vocab_keys = {t["topic_key"] for t in vocab}
    prompt = build_classification_prompt(subs, vocab)

    message = client.messages.create(
        model=config.TOPIC_CLASSIFY_MODEL,
        max_tokens=2048,
        system=_CLASSIFY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text
    match = re.search(r"\{[\s\S]*\}", text)
    parsed = {}
    if match:
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            parsed = {}

    by_index = {
        item.get("section_index"): item.get("topic_keys", [])
        for item in parsed.get("sections", [])
        if isinstance(item, dict)
    }

    result = []
    for idx, _sec in subs:
        raw = by_index.get(idx, [])
        keys = validate_topic_keys(raw if isinstance(raw, list) else [], vocab_keys)
        result.append(SectionTopic(section_index=idx, topic_keys=keys))
    return result
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd ~/Documents/GitHub/on-the-record && python -m pytest tests/test_topics.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/topics.py tests/test_topics.py && git commit -m "feat(pipeline): topic classification module"
```

### Task 3: wire the classify stage into run_local

**Files:**
- Modify: `run_local.py`

- [ ] **Step 1: Add the stage after summary generation**

In `run_local.py`, immediately after the Stage 5 (summary) block that sets `meeting.summary` and saves `summary_path`, add a Stage 5b that classifies topics. It connects to the DB for the vocabulary (read-only) and is checkpointed to `topics.json` so re-runs don't re-bill:

```python
    # --- Stage 5b: Topic classification (Phase 6) ---
    topics_path = meeting_dir / "topics.json"
    if topics_path.exists():
        from src.models import SectionTopic
        with open(topics_path, "r") as f:
            meeting.section_topics = [SectionTopic.from_dict(d) for d in json.load(f)]
        print(f"  Loaded topics ({len(meeting.section_topics)} sections)")
    elif args.skip_summary or meeting.summary is None:
        print("  Skipped topic classification (no summary).")
    else:
        db_url = os.environ.get("DATABASE_URL", "").strip()
        if not db_url:
            print("  No DATABASE_URL — skipping topic classification (vocabulary lives in the DB).")
        else:
            try:
                import anthropic
                import psycopg2
                from src.topics import fetch_live_topics, classify_sections

                conn = psycopg2.connect(db_url)
                try:
                    vocab = fetch_live_topics(conn)
                finally:
                    conn.close()
                print(f"  Classifying topics against {len(vocab)} live Compass topics...")
                client = anthropic.Anthropic()
                meeting.section_topics = classify_sections(client, meeting.summary.sections, vocab)
                with open(topics_path, "w") as f:
                    json.dump([st.to_dict() for st in meeting.section_topics], f, indent=2)
                tagged = sum(1 for st in meeting.section_topics if st.topic_keys)
                print(f"  Tagged {tagged}/{len(meeting.section_topics)} substantive sections")
            except Exception as e:
                print(f"  ⚠ Skipping topic classification — {e}")
                meeting.section_topics = []
```

(Place it so `meeting.section_topics` is set before the publish step reads it. The exact insertion point is right after the summary checkpoint save, before the final export/publish.)

- [ ] **Step 2: Smoke the wiring (no API call needed)**

Run: `cd ~/Documents/GitHub/on-the-record && python -c "import ast; ast.parse(open('run_local.py').read()); print('parse-ok')"`
Expected: `parse-ok`

- [ ] **Step 3: Commit**

```bash
git add run_local.py && git commit -m "feat(pipeline): topic classification stage (checkpointed)"
```

### Task 4: publish meeting_topics rows

**Files:**
- Modify: `src/publish.py`

- [ ] **Step 1: Add `_replace_topics`**

Mirror the existing `_replace_segments` (delete-then-insert per meeting). Add to `src/publish.py`:

```python
def _replace_topics(cur, meeting_uuid: str, meeting: "Meeting") -> None:
    """Delete-then-insert meeting_topics rows from meeting.section_topics.

    Denormalizes section metadata (title/type/times) so topic pages are a
    single query. status is always 'predicted' in this build.
    """
    cur.execute(
        "DELETE FROM meetings.meeting_topics WHERE meeting_id = %s",
        (meeting_uuid,),
    )
    if not meeting.section_topics or not meeting.summary:
        return

    sections = meeting.summary.sections
    model = meeting.summary.model or None
    rows = []
    for st in meeting.section_topics:
        if st.section_index < 0 or st.section_index >= len(sections):
            continue
        sec = sections[st.section_index]
        for key in st.topic_keys:
            rows.append((
                meeting_uuid, st.section_index, key, "predicted",
                st.confidence, model,
                sec.title, sec.section_type, sec.start_time, sec.end_time,
            ))

    if rows:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO meetings.meeting_topics
              (meeting_id, section_index, topic_key, status, confidence, model,
               section_title, section_type, start_time, end_time)
            VALUES %s
            """,
            rows,
        )
```

- [ ] **Step 2: Call it from `publish_meeting`**

In `publish_meeting`, after the segments are replaced (and within the same transaction/cursor), add:

```python
    _replace_topics(cur, meeting_uuid, meeting)
```

- [ ] **Step 3: Verify import + commit**

Run: `cd ~/Documents/GitHub/on-the-record && python -c "import ast; ast.parse(open('src/publish.py').read()); print('parse-ok')"`
Expected: `parse-ok`

```bash
git add src/publish.py && git commit -m "feat(pipeline): write meetings.meeting_topics on publish"
```

---

## Part B — ev-accounts (work in `~/Documents/GitHub/ev-accounts/backend`)

### Task 5: meeting_topics migration

**Files:**
- Create: `backend/migrations/365_meeting_topics.sql`

- [ ] **Step 1: Write the migration**

```sql
-- Migration 365: meeting_topics — AI-predicted Compass-issue tags on meeting
-- discussion sections (Phase 6). topic_key is a soft reference to
-- inform.compass_topics.topic_key (resolved by join to the live version).

CREATE TABLE IF NOT EXISTS meetings.meeting_topics (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  meeting_id     UUID NOT NULL REFERENCES meetings.meetings(id) ON DELETE CASCADE,
  section_index  INT  NOT NULL,
  topic_key      TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'predicted',
  confidence     REAL,
  model          TEXT,
  section_title  TEXT,
  section_type   TEXT,
  start_time     REAL,
  end_time       REAL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS meeting_topics_topic_key_idx ON meetings.meeting_topics(topic_key);
CREATE INDEX IF NOT EXISTS meeting_topics_meeting_id_idx ON meetings.meeting_topics(meeting_id);
```

- [ ] **Step 2: Apply it** (via the project's migration runner, or psql against the dev DB)

Run the repo's standard migration command (check `package.json`/`backend/migrations` README for the runner). Expected: table `meetings.meeting_topics` exists.

- [ ] **Step 3: Commit**

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/migrations/365_meeting_topics.sql && git commit -m "feat(db): meeting_topics table for AI-predicted topic tags"
```

### Task 6: fix the summary read path (JSONB) + attach topics

**Files:**
- Modify: `backend/src/lib/meetingsService.ts`

- [ ] **Step 1: Rewrite `getSummaryByMeetingId` to read the JSONB column**

Replace the body that queries `meetings.meeting_summaries` / `meetings.summary_sections` (tables that do not exist) with a read of `meetings.meetings.summary` (JSONB written by the pipeline), shaped into the existing `MeetingSummary`/`SummarySection` response, with topics attached per section from `meeting_topics`:

```typescript
export async function getSummaryByMeetingId(
  meetingId: string
): Promise<MeetingSummary | null> {
  const { rows } = await pool.query<{ summary: unknown | null }>(
    `SELECT summary FROM meetings.meetings WHERE id = $1`,
    [meetingId]
  );
  if (rows.length === 0 || !rows[0].summary) return null;

  // summary JSONB shape (written by publish.py): { executive_summary,
  // key_decisions[], sections[], model, generated_at }
  const s = rows[0].summary as {
    executive_summary?: string;
    key_decisions?: string[];
    sections?: Array<{
      section_type: string; title: string; content: string;
      start_time?: number; end_time?: number;
      start_segment?: number; end_segment?: number;
    }>;
    model?: string;
    generated_at?: string;
  };

  // Attach topic tags by section_index (meeting_topics is the source of truth).
  const { rows: topicRows } = await pool.query<{
    section_index: string; topic_key: string; status: string; title: string | null;
  }>(
    `SELECT mt.section_index, mt.topic_key, mt.status, ct.short_title AS title
     FROM meetings.meeting_topics mt
     LEFT JOIN inform.compass_topics ct
       ON ct.topic_key = mt.topic_key AND ct.is_live = true
     WHERE mt.meeting_id = $1`,
    [meetingId]
  );
  const topicsByIndex = new Map<number, { key: string; title: string | null; status: string }[]>();
  for (const r of topicRows) {
    const idx = Number(r.section_index);
    const list = topicsByIndex.get(idx) ?? [];
    list.push({ key: r.topic_key, title: r.title, status: r.status });
    topicsByIndex.set(idx, list);
  }

  const sections: SummarySection[] = (s.sections ?? []).map((sec, i) => ({
    id: `${meetingId}:${i}`,
    summaryId: meetingId,
    sectionType: sec.section_type,
    title: sec.title,
    content: sec.content,
    startTime: sec.start_time != null ? Number(sec.start_time) : null,
    endTime: sec.end_time != null ? Number(sec.end_time) : null,
    sortOrder: i,
    topics: topicsByIndex.get(i) ?? [],
  }));

  return {
    id: meetingId,
    meetingId,
    summaryType: 'meeting',
    model: s.model ?? null,
    createdAt: s.generated_at ?? null,
    executiveSummary: s.executive_summary ?? '',
    keyDecisions: s.key_decisions ?? [],
    sections,
  };
}
```

Update the `MeetingSummary` / `SummarySection` TypeScript interfaces in this file to match: add `executiveSummary: string`, `keyDecisions: string[]` to `MeetingSummary`; add `topics: { key: string; title: string | null; status: string }[]` to `SummarySection`. Remove the now-dead `SummaryRow`/`SummarySectionRow` row interfaces and `mapSummarySection` if they're unused after this change.

- [ ] **Step 2: Typecheck**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck`
Expected: clean (fix any references to removed fields).

- [ ] **Step 3: Commit**

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/src/lib/meetingsService.ts && git commit -m "fix(api): read meeting summary from JSONB + attach topic tags"
```

### Task 7: topicsService

**Files:**
- Create: `backend/src/lib/topicsService.ts`

- [ ] **Step 1: Write the service**

```typescript
/**
 * topicsService — cross-meeting topic aggregation.
 *
 * meetings.meeting_topics holds AI-predicted Compass-issue tags on meeting
 * sections. Topic titles resolve from the live inform.compass_topics row by
 * topic_key. pool.query only; explicit mappers; Number() on counts/times.
 */

import { pool } from './db.js';

export interface TopicListEntry {
  topicKey: string;
  title: string | null;
  itemCount: number;
  meetingCount: number;
}

export interface TopicItem {
  meetingId: string;
  city: string;
  meetingType: string;
  date: string;
  playbackKind: string | null;
  sectionIndex: number;
  sectionTitle: string | null;
  sectionType: string | null;
  startTime: number | null;
  status: string;
}

export interface TopicDetail {
  topicKey: string;
  title: string | null;
  items: TopicItem[];
}

export async function getTopics(): Promise<{ topics: TopicListEntry[]; uncategorizedCount: number }> {
  const { rows } = await pool.query<{
    topic_key: string; title: string | null; item_count: string; meeting_count: string;
  }>(
    `SELECT mt.topic_key,
            ct.short_title AS title,
            COUNT(*) AS item_count,
            COUNT(DISTINCT mt.meeting_id) AS meeting_count
     FROM meetings.meeting_topics mt
     LEFT JOIN inform.compass_topics ct
       ON ct.topic_key = mt.topic_key AND ct.is_live = true
     GROUP BY mt.topic_key, ct.short_title
     ORDER BY item_count DESC, mt.topic_key`
  );

  // Uncategorized = substantive sections (any meeting) with zero tags.
  // Counted as meetings that have a summary but fewer tagged sections than
  // substantive sections is non-trivial from SQL alone; expose a simple proxy:
  // sections present in summaries with no row in meeting_topics is computed
  // client-rarely, so we return 0 here and let the web omit the row when 0.
  // (A precise count is deferred to the curation phase.)
  const uncategorizedCount = 0;

  return {
    topics: rows.map((r) => ({
      topicKey: r.topic_key,
      title: r.title,
      itemCount: Number(r.item_count),
      meetingCount: Number(r.meeting_count),
    })),
    uncategorizedCount,
  };
}

export async function getTopicByKey(topicKey: string): Promise<TopicDetail | null> {
  const { rows: titleRows } = await pool.query<{ title: string | null }>(
    `SELECT short_title AS title FROM inform.compass_topics
     WHERE topic_key = $1 AND is_live = true LIMIT 1`,
    [topicKey]
  );

  const { rows } = await pool.query<{
    meeting_id: string; city: string; meeting_type: string; date: string;
    playback_kind: string | null; section_index: string; section_title: string | null;
    section_type: string | null; start_time: string | null; status: string;
  }>(
    `SELECT mt.meeting_id, m.city, m.meeting_type, m.date::text AS date,
            m.playback_kind, mt.section_index, mt.section_title, mt.section_type,
            mt.start_time, mt.status
     FROM meetings.meeting_topics mt
     JOIN meetings.meetings m ON m.id = mt.meeting_id
     WHERE mt.topic_key = $1
     ORDER BY m.date DESC, mt.meeting_id, mt.section_index`,
    [topicKey]
  );

  if (titleRows.length === 0 && rows.length === 0) return null;

  return {
    topicKey,
    title: titleRows[0]?.title ?? null,
    items: rows.map((r) => ({
      meetingId: r.meeting_id,
      city: r.city,
      meetingType: r.meeting_type,
      date: r.date,
      playbackKind: r.playback_kind,
      sectionIndex: Number(r.section_index),
      sectionTitle: r.section_title,
      sectionType: r.section_type,
      startTime: r.start_time != null ? Number(r.start_time) : null,
      status: r.status,
    })),
  };
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck`
Expected: clean.

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/src/lib/topicsService.ts && git commit -m "feat(api): topicsService — cross-meeting topic aggregation"
```

### Task 8: topics routes (TDD)

**Files:**
- Create: `backend/src/routes/topics.test.ts`
- Create: `backend/src/routes/topics.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
import { vi, describe, it, expect, beforeEach } from 'vitest';
import express from 'express';
import request from 'supertest';

const { mockGetTopics, mockGetTopicByKey } = vi.hoisted(() => ({
  mockGetTopics: vi.fn(),
  mockGetTopicByKey: vi.fn(),
}));
vi.mock('../lib/topicsService.js', () => ({
  getTopics: mockGetTopics,
  getTopicByKey: mockGetTopicByKey,
}));
vi.mock('../middleware/auth.js', () => ({
  optionalAuth: (_req: unknown, _res: unknown, next: () => void) => next(),
}));

import topicsRouter from './topics.js';

const app = express();
app.use('/api/topics', topicsRouter);

beforeEach(() => {
  mockGetTopics.mockReset();
  mockGetTopicByKey.mockReset();
});

describe('GET /api/topics', () => {
  it('200 with topic list', async () => {
    mockGetTopics.mockResolvedValueOnce({
      topics: [{ topicKey: 'housing', title: 'Housing', itemCount: 14, meetingCount: 9 }],
      uncategorizedCount: 0,
    });
    const res = await request(app).get('/api/topics');
    expect(res.status).toBe(200);
    expect(res.body.topics[0].topicKey).toBe('housing');
  });
});

describe('GET /api/topics/:key', () => {
  it('422 on a malformed key', async () => {
    const res = await request(app).get('/api/topics/Bad!Key');
    expect(res.status).toBe(422);
    expect(mockGetTopicByKey).not.toHaveBeenCalled();
  });

  it('404 when unknown', async () => {
    mockGetTopicByKey.mockResolvedValueOnce(null);
    const res = await request(app).get('/api/topics/nope');
    expect(res.status).toBe(404);
  });

  it('200 with topic detail', async () => {
    mockGetTopicByKey.mockResolvedValueOnce({
      topicKey: 'housing', title: 'Housing',
      items: [{ meetingId: 'm1', city: 'Bloomington', meetingType: 'City Council',
                date: '2026-02-18', playbackKind: 'youtube', sectionIndex: 2,
                sectionTitle: 'Ordinance 26-04', sectionType: 'discussion',
                startTime: 1843, status: 'predicted' }],
    });
    const res = await request(app).get('/api/topics/housing');
    expect(res.status).toBe(200);
    expect(res.body.items).toHaveLength(1);
    expect(mockGetTopicByKey).toHaveBeenCalledWith('housing');
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/topics.test.ts`
Expected: FAIL — cannot find module './topics.js'

- [ ] **Step 3: Write the route**

```typescript
/**
 * Topic routes — cross-meeting topic pages for the on-the-record site.
 * Public reads (optionalAuth). topic_key validated before any DB work.
 */

import { Router } from 'express';
import type { Request, Response } from 'express';
import { optionalAuth } from '../middleware/auth.js';
import { getTopics, getTopicByKey } from '../lib/topicsService.js';

const router = Router();
const KEY_REGEX = /^[a-z0-9][a-z0-9_-]{0,99}$/;

router.get('/', optionalAuth, async (_req: Request, res: Response): Promise<void> => {
  try {
    const result = await getTopics();
    res.status(200).json(result);
  } catch (err) {
    console.error('[GET /topics] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

router.get('/:key', optionalAuth, async (req: Request, res: Response): Promise<void> => {
  const key = req.params.key as string;
  if (!KEY_REGEX.test(key)) {
    res.status(422).json({ code: 'VALIDATION_ERROR', message: 'Invalid topic key' });
    return;
  }
  try {
    const topic = await getTopicByKey(key);
    if (!topic) {
      res.status(404).json({ code: 'NOT_FOUND', message: 'Topic not found' });
      return;
    }
    res.status(200).json(topic);
  } catch (err) {
    console.error('[GET /topics/:key] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

export default router;
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/topics.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 5: Register + full suite + commit**

In `backend/src/index.ts`: add `import topicsRouter from './routes/topics.js';` (next to searchRouter) and `app.use('/api/topics', topicsRouter);` (next to `/api/search`).

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck && npm test`
Expected: typecheck clean; no NEW failures (known pre-existing failures unrelated).

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/src/routes/topics.ts backend/src/routes/topics.test.ts backend/src/index.ts && git commit -m "feat(api): /api/topics and /api/topics/:key endpoints"
```

---

## Part C — web (work in `~/Documents/GitHub/on-the-record`)

### Task 9: types + fetchers

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/queries.ts`

- [ ] **Step 1: Append types to `web/lib/types.ts`**

```typescript
export type ProvenanceStatus = "predicted" | "verified";

export interface SectionTopicRef {
  key: string;
  title: string | null;
  status: ProvenanceStatus;
}

export interface SummarySection {
  section_type: string;
  title: string;
  content: string;
  start_time: number | null;
  end_time: number | null;
  sort_order: number;
  topics: SectionTopicRef[];
}

export interface MeetingSummary {
  executive_summary: string;
  key_decisions: string[];
  model: string | null;
  sections: SummarySection[];
}

export interface MeetingSpeaker {
  label: string;
  display_name: string | null;
  politician_slug: string | null;
  id_method: string | null;   // "human_review" ⇒ verified; else predicted
  confidence: number | null;
}

export interface TopicListEntry {
  topic_key: string;
  title: string | null;
  item_count: number;
  meeting_count: number;
}

export interface TopicItem {
  meeting_id: string;
  city: string;
  meeting_type: string;
  meeting_date: string;
  playback_kind: string | null;
  section_index: number;       // for keying; deep links use start_time
  section_title: string | null;
  section_type: string | null;
  start_time: number | null;
  status: ProvenanceStatus;
}

export interface TopicDetail {
  topic_key: string;
  title: string | null;
  items: TopicItem[];
}
```

- [ ] **Step 2: Add fetchers + extend the meeting fetch in `web/lib/queries.ts`**

Add mappers + fetchers (extend the type import accordingly):

```typescript
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapSummary(s: any): MeetingSummary {
  return {
    executive_summary: s.executiveSummary ?? "",
    key_decisions: s.keyDecisions ?? [],
    model: s.model ?? null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    sections: ((s.sections ?? []) as any[]).map((sec) => ({
      section_type: sec.sectionType,
      title: sec.title,
      content: sec.content,
      start_time: sec.startTime ?? null,
      end_time: sec.endTime ?? null,
      sort_order: sec.sortOrder ?? 0,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      topics: ((sec.topics ?? []) as any[]).map((t) => ({
        key: t.key, title: t.title ?? null, status: (t.status ?? "predicted"),
      })),
    })),
  };
}

export async function fetchSummary(meetingId: string): Promise<MeetingSummary | null> {
  const res = await fetch(`${BASE}/api/meetings/${encodeURIComponent(meetingId)}/summary`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`summary fetch failed: ${res.status}`);
  return mapSummary(await res.json());
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapTopicEntry(t: any): TopicListEntry {
  return {
    topic_key: t.topicKey, title: t.title ?? null,
    item_count: t.itemCount ?? 0, meeting_count: t.meetingCount ?? 0,
  };
}

export async function fetchTopics(): Promise<TopicListEntry[]> {
  const res = await fetch(`${BASE}/api/topics`);
  if (!res.ok) throw new Error(`topics fetch failed: ${res.status}`);
  const data = await res.json();
  return ((data.topics ?? []) as unknown[]).map(mapTopicEntry);
}

export async function fetchTopic(key: string): Promise<TopicDetail | null> {
  const res = await fetch(`${BASE}/api/topics/${encodeURIComponent(key)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`topic fetch failed: ${res.status}`);
  const t = await res.json();
  return {
    topic_key: t.topicKey,
    title: t.title ?? null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    items: ((t.items ?? []) as any[]).map((it) => ({
      meeting_id: it.meetingId, city: it.city, meeting_type: it.meetingType,
      meeting_date: it.date, playback_kind: it.playbackKind ?? null,
      section_index: it.sectionIndex, section_title: it.sectionTitle ?? null,
      section_type: it.sectionType ?? null, start_time: it.startTime ?? null,
      status: (it.status ?? "predicted"),
    })),
  };
}
```

The meeting page also needs the meeting's `speakers[]` with `id_method`. `GET /api/meetings/:id` already returns `speakers` (with `idMethod`/`confidence`) via `getMeetingById`. Extend the existing `Meeting` type + `mapMeeting` (or add a `mapMeetingSpeaker`) so `fetchMeeting` surfaces `speakers: MeetingSpeaker[]`. Add to `mapMeeting`:

```typescript
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    speakers: ((m.speakers ?? []) as any[]).map((sp) => ({
      label: sp.label,
      display_name: sp.displayName ?? null,
      politician_slug: sp.politicianSlug ?? null,
      id_method: sp.idMethod ?? null,
      confidence: sp.confidence ?? null,
    })),
```

and add `speakers: MeetingSpeaker[]` to the `Meeting` interface in `types.ts`. (Index/list fetches won't include speakers — default to `[]` in the mapper when absent.)

- [ ] **Step 3: Typecheck + commit**

Run: `cd ~/Documents/GitHub/on-the-record/web && npx tsc --noEmit`
Expected: clean.

```bash
cd ~/Documents/GitHub/on-the-record && git add web/lib/types.ts web/lib/queries.ts && git commit -m "feat(web): summary/topic/speaker types and fetchers"
```

### Task 10: ProvenanceBadge component

**Files:**
- Create: `web/components/ProvenanceBadge.tsx`

- [ ] **Step 1: Write the component**

```tsx
import type { ProvenanceStatus } from "@/lib/types";

const COPY: Record<ProvenanceStatus, { label: string; title: string }> = {
  predicted: { label: "✦ AI predicted", title: "Automated — pending human review." },
  verified: { label: "✓ Verified", title: "Confirmed by a human reviewer." },
};

export default function ProvenanceBadge({ status }: { status: ProvenanceStatus }) {
  const c = COPY[status];
  return (
    <span className={`provBadge prov-${status}`} title={c.title}>
      {c.label}
    </span>
  );
}

// Map a speaker's id_method to a provenance status.
export function speakerStatus(idMethod: string | null): ProvenanceStatus {
  return idMethod === "human_review" ? "verified" : "predicted";
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd ~/Documents/GitHub/on-the-record/web && npx tsc --noEmit`
Expected: clean.

```bash
git add web/components/ProvenanceBadge.tsx && git commit -m "feat(web): provenance badge component"
```

### Task 11: meeting page — exec summary (server) + outline & speaker badges (client)

**Files:**
- Modify: `web/app/meetings/[meetingId]/page.tsx`
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx`

**Why the outline lives in MeetingView:** the outline must seek the player on click. `MeetingView`'s `?t=` deep-link handler only runs on mount, so a same-page `?t=` link wouldn't re-fire it. The interactive outline therefore renders **inside** the client `MeetingView` and calls its in-component seek directly. The static executive-summary block stays server-rendered in `page.tsx`.

- [ ] **Step 1: `page.tsx` — fetch summary, render exec summary, pass the outline into MeetingView**

```tsx
import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchMeeting, fetchMeetings, fetchSegments, fetchSummary } from "@/lib/queries";
import MeetingView from "./MeetingView";

export const dynamicParams = false;

export async function generateStaticParams() {
  let meetings: Awaited<ReturnType<typeof fetchMeetings>> = [];
  try { meetings = await fetchMeetings(); } catch { /* empty-DB build */ }
  if (meetings.length === 0)
    return [{ meetingId: "00000000-0000-0000-0000-000000000000" }];
  return meetings.map((m) => ({ meetingId: m.meeting_id }));
}

const SUBSTANTIVE = new Set(["discussion", "public_comment", "consent_agenda", "vote"]);

export default async function MeetingPage({ params }: { params: Promise<{ meetingId: string }> }) {
  const { meetingId } = await params;
  const meeting = await fetchMeeting(meetingId);
  if (!meeting) notFound();
  const [segments, summary] = await Promise.all([
    fetchSegments(meetingId),
    fetchSummary(meetingId).catch(() => null),
  ]);

  const outline = (summary?.sections ?? []).filter((s) => SUBSTANTIVE.has(s.section_type));

  return (
    <main className="meetingPage">
      <header className="meetingHeader">
        <Link href="/" className="backLink">← All meetings</Link>
        <h1>{meeting.city} {meeting.meeting_type} — {meeting.meeting_date}</h1>
        {meeting.source_url && (
          <a className="sourceLink" href={meeting.source_url} target="_blank" rel="noreferrer">Original source ↗</a>
        )}
      </header>

      {summary?.executive_summary && (
        <section className="execSummary">
          <h2>Summary</h2>
          <p>{summary.executive_summary}</p>
          {summary.key_decisions.length > 0 && (
            <ul className="keyDecisions">
              {summary.key_decisions.map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          )}
        </section>
      )}

      <MeetingView meeting={meeting} segments={segments} outline={outline} />
    </main>
  );
}
```

- [ ] **Step 2: `MeetingView.tsx` — accept `outline`, render it with seek, add speaker badges**

Add the import and prop:

```tsx
import Link from "next/link";
import ProvenanceBadge, { speakerStatus } from "@/components/ProvenanceBadge";
import type { Meeting, Segment, SummarySection } from "@/lib/types";
```

Extend the component signature to accept `outline: SummarySection[]` (default `[]`).

Find the existing seek logic. `MeetingView` already seeks the player for `?t=` and click-to-seek; reuse it via a `seekToTime(seconds: number)` helper. If one isn't already factored out, add it next to the existing `seekToSegment`:

```tsx
const seekToTime = useCallback((seconds: number) => {
  playerRef.current?.seekTo?.(seconds);   // same call the ?t= handler uses
  const idx = segments.findIndex((s) => s.start_time >= seconds);
  const target = idx === -1 ? segments.length - 1 : Math.max(0, idx);
  document.getElementById(`seg-${segments[target]?.segment_id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
}, [segments]);
```

(Match the actual player-seek call already used in this file for `?t=`; the above mirrors it.)

Render the outline above the transcript pane:

```tsx
{outline.length > 0 && (
  <section className="outline">
    <h2>Discussed</h2>
    <ul>
      {outline.map((sec) => (
        <li key={sec.sort_order} className="outlineItem">
          <button
            type="button"
            className="outlineLink"
            onClick={() => seekToTime(Math.floor(sec.start_time ?? 0))}
          >
            <span className="outlineTitle">{sec.title}</span>
            <span className="outlineTime">{formatTime(sec.start_time ?? 0)}</span>
          </button>
          {sec.topics.length > 0 && (
            <span className="outlineTopics">
              {sec.topics.map((t) => (
                <span key={t.key} className="topicLabel">
                  <Link href={`/topics/${t.key}`}>{t.title ?? t.key}</Link>
                  <ProvenanceBadge status={t.status} />
                </span>
              ))}
            </span>
          )}
        </li>
      ))}
    </ul>
  </section>
)}
```

Speaker badge: build a label→status map and show the badge once per consecutive same-speaker run, just after the existing speaker-name span (the Phase-2 speaker→/people link stays):

```tsx
const statusByLabel = new Map(
  (meeting.speakers ?? []).map((sp) => [sp.label, speakerStatus(sp.id_method)] as const)
);
```

```tsx
{(i === 0 || segments[i - 1].speaker_label !== seg.speaker_label) && (
  <ProvenanceBadge status={statusByLabel.get(seg.speaker_label) ?? "predicted"} />
)}
```

- [ ] **Step 3: Typecheck + commit**

Run: `cd ~/Documents/GitHub/on-the-record/web && npx tsc --noEmit && npm run lint`
Expected: clean.

```bash
git add "web/app/meetings/[meetingId]/page.tsx" "web/app/meetings/[meetingId]/MeetingView.tsx" && git commit -m "feat(web): meeting summary, topic outline, speaker provenance badges"
```

### Task 12: index summary preview

**Files:**
- Modify: `backend/src/lib/meetingsService.ts` (ev-accounts)
- Modify: `web/lib/types.ts`, `web/lib/queries.ts`, `web/app/page.tsx` (web)

The list endpoint already selects the `summary` JSONB (it's in `MEETING_COLS`), so no SQL change is needed — derive a short preview in the mapper rather than shipping the full summary downstream.

- [ ] **Step 1: ev-accounts — expose `summaryPreview` on the meeting payload**

In `meetingsService.ts`, add `summaryPreview: string | null` to the `Meeting` interface, and in `mapMeeting` derive it from the already-selected `summary` JSONB (first ~160 chars of `executive_summary`):

```typescript
// inside mapMeeting, after the existing fields:
summaryPreview: (() => {
  const ex = (row.summary as { executive_summary?: string } | null)?.executive_summary;
  if (!ex) return null;
  return ex.length > 160 ? ex.slice(0, 157).trimEnd() + "…" : ex;
})(),
```

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck` → clean. Commit:

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/src/lib/meetingsService.ts && git commit -m "feat(api): summaryPreview on meeting payload for index cards"
```

- [ ] **Step 2: web — map and render the preview**

In `web/lib/types.ts`, add `summary_preview: string | null` to `Meeting`. In `web/lib/queries.ts` `mapMeeting`, add `summary_preview: m.summaryPreview ?? null,`. In `web/app/page.tsx`, render it inside each card when present:

```tsx
{m.summary_preview && <span className="meetingPreview">{m.summary_preview}</span>}
```

Add a style to `globals.css`:

```css
.meetingPreview { display: block; color: var(--muted); font-size: 0.85rem; margin-top: 0.25rem; }
```

- [ ] **Step 3: Typecheck + commit (web)**

Run: `cd ~/Documents/GitHub/on-the-record/web && npx tsc --noEmit && npm run lint`
Expected: clean.

```bash
cd ~/Documents/GitHub/on-the-record && git add web/lib/types.ts web/lib/queries.ts web/app/page.tsx web/app/globals.css && git commit -m "feat(web): executive-summary preview on index cards"
```

### Task 13: /topics index + /topics/[key] pages + nav + styles

**Files:**
- Create: `web/app/topics/page.tsx`
- Create: `web/app/topics/[key]/page.tsx`
- Modify: `web/app/page.tsx` (nav), `web/app/globals.css`

- [ ] **Step 1: `web/app/topics/page.tsx`**

```tsx
import Link from "next/link";
import { fetchTopics } from "@/lib/queries";
import type { TopicListEntry } from "@/lib/types";

export const metadata = { title: "Topics — On the Record" };

export default async function TopicsPage() {
  let topics: TopicListEntry[] = [];
  let loadError = false;
  try { topics = await fetchTopics(); } catch { loadError = true; }

  return (
    <main className="indexPage">
      <Link href="/" className="backLink">← All meetings</Link>
      <h1>Topics</h1>
      <p className="tagline">Issues discussed across meetings, from the Compass topic set.</p>
      {loadError ? (
        <p>Topics are temporarily unavailable.</p>
      ) : topics.length === 0 ? (
        <p>No topics tagged yet.</p>
      ) : (
        <ul className="topicList">
          {topics.map((t) => (
            <li key={t.topic_key}>
              <Link href={`/topics/${t.topic_key}`} className="topicRow">
                <span className="topicName">{t.title ?? t.topic_key}</span>
                <span className="topicCount">
                  {t.item_count} item{t.item_count === 1 ? "" : "s"} · {t.meeting_count} meeting{t.meeting_count === 1 ? "" : "s"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
```

- [ ] **Step 2: `web/app/topics/[key]/page.tsx`**

```tsx
import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchTopic, fetchTopics } from "@/lib/queries";
import ProvenanceBadge from "@/components/ProvenanceBadge";

export const dynamicParams = false;

export async function generateStaticParams() {
  let topics: Awaited<ReturnType<typeof fetchTopics>> = [];
  try { topics = await fetchTopics(); } catch { /* empty-DB build */ }
  if (topics.length === 0) return [{ key: "none" }];
  return topics.map((t) => ({ key: t.topic_key }));
}

function fmt(seconds: number | null): string {
  if (seconds == null) return "";
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60);
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export default async function TopicPage({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const topic = await fetchTopic(key);
  if (!topic) notFound();

  return (
    <main className="indexPage">
      <Link href="/topics" className="backLink">← All topics</Link>
      <h1>{topic.title ?? topic.topic_key}</h1>
      <p className="tagline">{topic.items.length} item{topic.items.length === 1 ? "" : "s"} across meetings</p>
      <ul className="topicItems">
        {topic.items.map((it) => (
          <li key={`${it.meeting_id}:${it.segment_id}`} className="topicItem">
            <Link href={`/meetings/${it.meeting_id}?t=${it.start_time != null ? Math.floor(it.start_time) : 0}`} className="topicItemLink">
              <span className="topicItemTitle">{it.section_title ?? "Discussion"}</span>
              <span className="topicItemTime">{fmt(it.start_time)}</span>
            </Link>
            <span className="topicItemMeta">
              {it.city} {it.meeting_type} · {it.meeting_date} <ProvenanceBadge status={it.status} />
            </span>
          </li>
        ))}
      </ul>
    </main>
  );
}
```

- [ ] **Step 3: Nav link + styles**

In `web/app/page.tsx`, add to the `siteNav`:

```tsx
        <Link href="/topics">Topics →</Link>
```

Append to `web/app/globals.css`:

```css
/* ---------- Summary & outline ---------- */
.execSummary { margin: 1rem 0 1.5rem; }
.execSummary p { color: var(--foreground); line-height: 1.55; }
.keyDecisions { margin: 0.75rem 0 0 1.25rem; color: var(--muted); }
.outline { margin-bottom: 1.5rem; }
.outline h2, .execSummary h2 { font-size: 1.1rem; margin-bottom: 0.5rem; }
.outlineItem { padding: 0.5rem 0; border-bottom: 1px solid var(--border); }
.outlineLink { display: flex; justify-content: space-between; gap: 1rem; color: var(--accent); }
.outlineTime { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 0.85rem; }
.outlineTopics { display: inline-flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.25rem; }
.topicLabel { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.8rem; }
.topicLabel a { background: var(--accent-soft); color: var(--accent); border-radius: 4px; padding: 1px 7px; }

/* ---------- Provenance badge ---------- */
.provBadge { font-size: 0.7rem; padding: 0 5px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); white-space: nowrap; }
.prov-verified { color: #16a34a; border-color: #16a34a; }

/* ---------- Topics ---------- */
.topicList { list-style: none; display: flex; flex-direction: column; gap: 2px; }
.topicRow { display: flex; justify-content: space-between; padding: 0.5rem 0.6rem; border-radius: 6px; }
.topicRow:hover { background: var(--accent-soft); }
.topicName { color: var(--accent); font-weight: 600; }
.topicCount { color: var(--muted); font-size: 0.85rem; }
.topicItems { list-style: none; display: flex; flex-direction: column; gap: 0.85rem; }
.topicItem { border-left: 3px solid var(--border); padding-left: 0.75rem; }
.topicItemLink { display: flex; justify-content: space-between; gap: 1rem; color: var(--accent); }
.topicItemTime { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 0.85rem; }
.topicItemMeta { color: var(--muted); font-size: 0.8rem; display: inline-flex; gap: 0.4rem; align-items: center; }
```

- [ ] **Step 4: Typecheck, lint, commit**

Run: `cd ~/Documents/GitHub/on-the-record/web && npx tsc --noEmit && npm run lint`
Expected: clean.

```bash
git add web/app/topics web/app/page.tsx web/app/globals.css && git commit -m "feat(web): topic index + detail pages, nav, styles"
```

---

## Part D — verification + roadmap

### Task 14: end-to-end build + roadmap

- [ ] **Step 1: Build the static site against the local backend**

Start the ev-accounts dev server (`cd ~/Documents/GitHub/ev-accounts/backend && npm run dev`, port 3000). Then:

```bash
cd ~/Documents/GitHub/on-the-record/web && EV_ACCOUNTS_URL=http://localhost:3000 NEXT_PUBLIC_EV_ACCOUNTS_URL=http://localhost:3000 npm run build
```

Expected: build succeeds; route list includes `○ /topics` and `● /topics/[key]`. (DB is empty until data is re-published, so pages render empty states; the sentinel params keep the build green — same as Phases 2–3.)

- [ ] **Step 2: Browser verification**

Serve `web/out` and confirm: `/topics` renders (empty state OK with no data); a meeting page shows the Summary block + Discussed outline + topic labels + provenance badges when data exists; speaker badges render once per speaker run; outline links seek the player. With an empty DB, confirm no console errors and the empty states render; note data-dependent checks pending re-publish.

- [ ] **Step 3: Roadmap update**

In `docs/web-roadmap.md`: mark Phase 6 built (date 2026-06-13), note Phase 4's web display landed with it, and that event-kinds / event→entity modeling remain open (being grilled). Move the `← next up` marker appropriately.

- [ ] **Step 4: Commit**

```bash
git add docs/web-roadmap.md && git commit -m "docs: mark Phase 6 (topics & provenance) built"
```

---

## Deployment notes

1. Run migration 365 on the ev-accounts DB; deploy ev-accounts.
2. Re-publish meetings through the pipeline with `DATABASE_URL` + `ANTHROPIC_API_KEY` set so summaries and topic tags are generated and written.
3. Trigger the static-site rebuild (topics are baked at build time).

## Consciously deferred (per spec)

Post-publish curation web app (verify/retag, clear Uncategorized, promote predicted→verified); pre-publish topic review in the CLI; precise Uncategorized counting in `/api/topics`; official agenda ingestion; LA council-file linking; event-kinds + event→entity modeling; unified topic page merging quotes/stances; scope-aware vocabulary.
