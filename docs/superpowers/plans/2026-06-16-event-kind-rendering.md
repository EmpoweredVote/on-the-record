# Event Kind Rendering & Data Standardization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every event kind render correctly — with accurate titles, source attribution, and event-appropriate summaries — so users can trust what they're reading regardless of whether it's a city council meeting, a candidate debate, or a journalist interview.

**Problem this solves:** A `news_clip` interview published via `--resume` without re-passing `--event-kind` got published as "Bloomington Regular Session" with a council-framed summary ("No formal votes were passed...", "The council provided a forum..."). The root causes are (1) the summary pipeline is hardcoded for council meetings, (2) resume loses metadata like `event_kind`, `city`, and `date`, and (3) there is no structured field for source attribution.

**Design decisions (from grill session 2026-06-16):**
- **Interview/Media** is a new event category alongside Deliberative and Electoral. Kinds: `news_clip` (journalist interviews a subject), `press_conference` (subject makes statement + takes questions).
- **`highlights`** replaces `key_decisions` across the data layer. Neutral enough to cover council votes, candidate commitments, and notable interview claims.
- **`meetings.event_orgs`** is a new table: `(meeting_id, org_name)`. Covers single and co-hosted events (e.g. "CBS, Telemundo" or "League of Women Voters, Citizens for Responsible Government"). No role field.
- **Mutual exclusivity** of `chamber_id` / `race_id` is relaxed for `news_clip` and `press_conference`. All other kinds keep their existing rules.
- **`source_title`** captured from yt-dlp at ingest and stored in `processing_metadata`. Used as title fallback.
- **Title fallback chain** for interview/media: explicit `title` → `source_title` (from yt-dlp) → `event_org(s) + kind label`.
- **Summarize pipeline** becomes event-kind-aware: Interview/Media events use topic-based section classification, per-topic claim extraction, and a source-attributed executive summary.
- **Resume** must preserve `event_kind`, `city`, `date`, and `meeting_type` from `pipeline_state.json` instead of defaulting silently.

**Architecture:** Ship in five task groups. Each group is independently deployable but groups 1-3 should land before 4-5.

**Tech Stack:** Python 3, psycopg2, pytest, Anthropic API (Claude Haiku/Sonnet), Next.js App Router, TypeScript, PostgreSQL.

---

## File Map

**on-the-record repository**

| File | Action | Purpose |
|---|---|---|
| `src/event_kinds.py` | modify | Add `press_conference` |
| `src/event_entities.py` | modify | Relax `chamber_id`/`race_id` mutual exclusivity for interview/media kinds |
| `src/models.py` | modify | Rename `key_decisions` → `highlights` in `MeetingSummary` |
| `src/summarize.py` | modify | Event-kind-aware prompts and section types |
| `src/ingest.py` (or equivalent) | modify | Capture `source_title` from yt-dlp metadata |
| `src/publish.py` | modify | Publish `event_orgs` to new table |
| `run_local.py` | modify | Resume metadata preservation; `title` prompt for interview kinds |
| `supabase/migrations/` | create | `event_orgs` table + `key_decisions→highlights` JSONB rename |
| `tests/test_event_kinds.py` | modify | Add `press_conference` assertions |
| `tests/test_event_entities.py` | modify | Add relaxed-constraint test cases |
| `tests/test_summarize.py` | create | Event-kind-aware summarize behavior |
| `tests/test_publish.py` | modify | `event_orgs` upsert |
| `web/lib/types.ts` | modify | `highlights` field; `event_orgs` on `Meeting` |
| `web/lib/format.ts` | modify | Updated `meetingTitle()` fallback chain |
| `web/app/meetings/[meetingId]/page.tsx` | modify | Render source orgs; event-kind-aware summary labels |

---

## Task 1: `press_conference` Event Kind + `highlights` Rename

**Scope:** Pure data model — no prompts, no DB, no web.

**Files:**
- Modify: `src/event_kinds.py`
- Modify: `src/models.py`
- Modify: `src/summarize.py` (field name only, not prompts)
- Modify: `tests/test_event_kinds.py`

### What it does

Adds `press_conference` to the `EVENT_KINDS` tuple. Renames the `key_decisions` field to `highlights` on `MeetingSummary` with full backwards-compat on `from_dict` (accept either key; write `highlights`).

---

- [ ] **Step 1: Failing tests**

In `tests/test_event_kinds.py`, add:
```python
def test_press_conference_in_event_kinds():
    assert "press_conference" in EVENT_KINDS

def test_validate_press_conference():
    assert validate_event_kind("press_conference") == "press_conference"
```

In `tests/test_models.py` (create or modify):
```python
def test_meeting_summary_highlights_field():
    ms = MeetingSummary(highlights=["item 1"], ...)
    assert ms.highlights == ["item 1"]

def test_meeting_summary_from_dict_key_decisions_compat():
    # Old data with key_decisions should still load
    d = {"key_decisions": ["vote passed"], "executive_summary": "...", ...}
    ms = MeetingSummary.from_dict(d)
    assert ms.highlights == ["vote passed"]

def test_meeting_summary_to_dict_uses_highlights():
    ms = MeetingSummary(highlights=["vote passed"], ...)
    assert "highlights" in ms.to_dict()
    assert "key_decisions" not in ms.to_dict()
```

- [ ] **Step 2: Add `press_conference` to `src/event_kinds.py`**

- [ ] **Step 3: Rename `key_decisions` → `highlights` in `src/models.py`**

`MeetingSummary.from_dict` should accept both keys (old DB rows have `key_decisions`):
```python
highlights=data.get("highlights") or data.get("key_decisions", [])
```

- [ ] **Step 4: Update all references to `key_decisions` in `src/summarize.py`** (field name only — prompts stay unchanged until Task 4)

- [ ] **Step 5: Run tests green**

---

## Task 2: Relax Entity Validation + `event_orgs` Table

**Scope:** Validation logic and DB schema. No pipeline behavior changes yet.

**Files:**
- Modify: `src/event_entities.py`
- Modify: `src/publish.py`
- Create: `supabase/migrations/NNNN_event_orgs.sql`
- Modify: `tests/test_event_entities.py`
- Modify: `tests/test_publish.py`

### What it does

Removes the "cannot both be set" error for `news_clip` and `press_conference`. Creates the `meetings.event_orgs` table. Adds upsert logic in `publish.py` to write orgs from `meeting.event_orgs` (new list field on `Meeting`).

---

- [ ] **Step 1: Failing tests**

In `tests/test_event_entities.py`:
```python
def test_news_clip_allows_both_ids():
    # Should not error
    assert validate_event_entities("news_clip", chamber_id=SOME_UUID, race_id=SOME_UUID) is None

def test_press_conference_allows_both_ids():
    assert validate_event_entities("press_conference", chamber_id=SOME_UUID, race_id=SOME_UUID) is None

def test_council_still_blocks_both_ids():
    assert validate_event_entities("council", chamber_id=SOME_UUID, race_id=SOME_UUID) is not None
```

In `tests/test_publish.py`:
```python
def test_event_orgs_upserted(mock_cursor):
    meeting = make_meeting(event_orgs=["California Courier"])
    publish_meeting(meeting, body_slug=None)
    # assert INSERT INTO meetings.event_orgs called with correct args
```

- [ ] **Step 2: Update `src/event_entities.py`**

Change the mutual-exclusivity guard to only apply to deliberative and electoral kinds:
```python
EXCLUSIVE_KINDS = {"council", "school_board", "debate", "forum"}
if chamber_id is not None and race_id is not None and event_kind in EXCLUSIVE_KINDS:
    return "chamber_id and race_id cannot both be set"
```

- [ ] **Step 3: Add `event_orgs: list[str]` to `Meeting` dataclass** in `src/models.py` (default `[]`)

- [ ] **Step 4: Create migration**

```sql
-- supabase/migrations/NNNN_event_orgs.sql
CREATE TABLE IF NOT EXISTS meetings.event_orgs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id  text NOT NULL REFERENCES meetings.meetings(slug) ON DELETE CASCADE,
    org_name    text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_event_orgs_meeting_id ON meetings.event_orgs(meeting_id);
```

- [ ] **Step 5: Add `event_orgs` upsert to `src/publish.py`**

After the meeting row upsert, delete existing orgs for this meeting and re-insert from `meeting.event_orgs`. Idempotent — same as the speaker upsert pattern.

- [ ] **Step 6: Run tests green**

---

## Task 3: Resume Metadata Preservation + Interview Title Handling

**Scope:** `run_local.py` only. Fixes the bug that caused "Bloomington Regular Session" to appear.

**Files:**
- Modify: `run_local.py`
- Modify: `src/checkpoint.py` (add `event_kind`, `city`, `meeting_type` to persisted state)

### What it does

Persists `event_kind`, `city`, `date`, and `meeting_type` into `pipeline_state.json` on first run. On resume, loads them back as defaults before `_resolve_metadata` runs so they are never overwritten by CLI defaults.

Also: prompts for `title` when `event_kind` is `news_clip` or `press_conference` and no `--title` was supplied. Does not default — requires explicit input.

---

- [ ] **Step 1: Add fields to `PipelineState`**

In `src/checkpoint.py`, add `event_kind`, `city`, `meeting_type` to the state dict (alongside `race_id`, `body_slug`, etc.). Persist on `state.save()`.

- [ ] **Step 2: Load persisted metadata in `run_local.py` on resume**

After `state = PipelineState(meeting_dir)`, before `_resolve_metadata`:
```python
if args.resume:
    if not args.event_kind and state.event_kind:
        args.event_kind = state.event_kind
    if not args.city and state.city:
        args.city = state.city
    if not args.date and state.date:
        args.date = state.date
    if not args.meeting_type and state.meeting_type:
        args.meeting_type = state.meeting_type
```

- [ ] **Step 3: Persist metadata to state after first resolve**

After `_resolve_metadata(args)`, save back:
```python
state.event_kind = args.event_kind
state.city = args.city
state.date = args.date
state.meeting_type = args.meeting_type
state.save()
```

- [ ] **Step 4: Prompt for `title` when interview kind and no `--title`**

In `_resolve_metadata`, after resolving `event_kind`:
```python
INTERVIEW_KINDS = {"news_clip", "press_conference"}
if args.event_kind in INTERVIEW_KINDS and not args.title:
    if interactive:
        args.title = input("  Title (required for interview/media events): ").strip() or None
    # Non-interactive: title stays None; fallback chain handles it at publish/display time
```

- [ ] **Step 5: Capture `source_title` from yt-dlp**

In the Stage 1 ingest path, when downloading via yt-dlp, extract the video title from the info dict and store as `processing_metadata["source_title"]`. Pass it through to `Meeting.processing_metadata` so it's available at publish time and in the web fallback chain.

- [ ] **Step 6: Manual verification**

Run `--resume` on an existing `news_clip` meeting without re-passing `--event-kind` and confirm it reads the persisted value.

---

## Task 4: Event-Kind-Aware Summary Pipeline

**Scope:** `src/summarize.py` only. This is the biggest prompt engineering change.

**Files:**
- Modify: `src/summarize.py`
- Create: `tests/test_summarize.py`

### What it does

`generate_summary(meeting)` already receives the `Meeting` object. It now branches on `meeting.event_kind` to use category-specific prompts and section types.

**Interview/Media category** (`news_clip`, `press_conference`):

| Pass | Section types | System prompt change |
|---|---|---|
| 1 — Classify | `topic` (one per major question/theme) | "You are analyzing a {kind} transcript. Identify the main topics discussed..." |
| 2 — Summarize | Per topic: subject's stated position + notable quotes | "Summarize what {subject} said about this topic, including any specific commitments or claims..." |
| 3 — Executive | Source-attributed opening + `highlights` as key claims | "Write 3-5 sentences starting with 'In an interview with [outlet], [subject]...'" |

**Deliberative category** (`council`, `school_board`): unchanged.

**Electoral category** (`debate`, `forum`): unchanged for now (extend in a future plan).

---

- [ ] **Step 1: Failing tests**

In `tests/test_summarize.py`:
```python
def test_interview_classify_uses_topic_sections(mock_claude):
    # mock Claude to return topic sections
    # verify _classify_sections called with interview prompt when event_kind=news_clip
    ...

def test_interview_executive_uses_source_attribution(mock_claude):
    # verify executive summary prompt includes outlet name
    ...

def test_deliberative_classify_unchanged(mock_claude):
    # verify council meetings still get roll_call/discussion/vote sections
    ...
```

- [ ] **Step 2: Add interview-specific classify prompt**

```python
_INTERVIEW_CLASSIFY_SYSTEM = """You are analyzing a media interview transcript.
Identify the main topics or question clusters discussed. For each topic, provide:
- type: always "topic"
- title: a short descriptive label for what was discussed (e.g. "Tax Policy", "Immigration")
- start_segment / end_segment: the segment range

Return JSON:
{"sections": [{"type": "topic", "start_segment": N, "end_segment": M, "title": "..."}]}"""
```

- [ ] **Step 3: Add interview-specific summarize prompt**

```python
_INTERVIEW_SUMMARIZE_SYSTEM = """You are summarizing one topic from a media interview for citizens.
Write 2-4 paragraphs in plain language covering:
- What the interviewer asked
- What the subject said (their position, any specific claims or commitments)
- Any notable quotes (use direct quotes sparingly and accurately)
Do not editorialize. Attribute all claims to the speaker by name."""
```

- [ ] **Step 4: Add interview-specific executive prompt**

```python
_INTERVIEW_EXECUTIVE_SYSTEM = """You are writing an executive summary of a media interview.
Open with: "In an interview with [outlet], [subject name] discussed..."
Then 2-3 sentences covering the main topics.
Then extract 3-5 key claims or commitments the subject made as bullet points.
Return JSON: {"executive_summary": "...", "highlights": ["...", "..."]}"""
```

- [ ] **Step 5: Branch in `generate_summary()`**

```python
INTERVIEW_KINDS = {"news_clip", "press_conference"}
if meeting.event_kind in INTERVIEW_KINDS:
    classify_system = _INTERVIEW_CLASSIFY_SYSTEM
    summarize_system = _INTERVIEW_SUMMARIZE_SYSTEM
    executive_system = _INTERVIEW_EXECUTIVE_SYSTEM
    substantive_types = {"topic"}
else:
    classify_system = _CLASSIFY_SYSTEM
    # ... existing deliberative branch
```

- [ ] **Step 6: Wire outlet name into executive prompt**

Extract the first `event_org` from `meeting.event_orgs` (if any) to substitute into the executive prompt. Fall back to `meeting.source_title` or "the interviewer."

- [ ] **Step 7: Run tests green**

---

## Task 5: Web Rendering

**Scope:** `web/` only. Makes the public site display all the above correctly.

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/format.ts`
- Modify: `web/app/meetings/[meetingId]/page.tsx`
- Modify: `web/lib/queries.ts`

### What it does

1. **`highlights` field**: Replace `key_decisions` with `highlights` in `MeetingSummary`. Accept both from API (old rows have `key_decisions`).
2. **Title fallback**: Update `meetingTitle()` to use `source_title` from `processing_metadata` before falling back to `event_org + kind label`.
3. **Source orgs**: Fetch `event_orgs` from API and render "Produced by X, Y" on the meeting page header.
4. **Event-kind-aware summary labels**: Don't render "Key decisions" heading for interview/media events — render "Highlights" for all kinds.

---

- [ ] **Step 1: Update `web/lib/types.ts`**

```typescript
interface MeetingSummary {
  executive_summary: string;
  highlights: string[];      // renamed from key_decisions; accept either from API
  key_decisions?: string[];  // keep for backwards compat parsing only
  // ...
}

interface Meeting {
  // ...
  event_orgs: string[];      // new; may be empty
  source_title?: string | null;  // from processing_metadata
}
```

- [ ] **Step 2: Update `meetingTitle()` in `web/lib/format.ts`**

```typescript
export function meetingTitle(
  meeting: Pick<Meeting, "title" | "city" | "meeting_type" | "event_kind" | "event_orgs" | "source_title">
): string {
  if (meeting.title?.trim()) return meeting.title.trim();
  if (meeting.source_title?.trim()) return meeting.source_title.trim();
  // Interview/media fallback: "California Courier · News Clip"
  const INTERVIEW_KINDS: EventKind[] = ["news_clip", "press_conference"];
  if (INTERVIEW_KINDS.includes(meeting.event_kind)) {
    const orgs = meeting.event_orgs.join(", ");
    const kindLabel = eventKindLabel(meeting.event_kind);
    return orgs ? `${orgs} · ${kindLabel}` : kindLabel;
  }
  return [meeting.city, meeting.meeting_type]
    .filter((p): p is string => Boolean(p?.trim()))
    .join(" ");
}
```

- [ ] **Step 3: Update `web/lib/queries.ts`**

Map `highlights` from API (accepting `key_decisions` as fallback for old rows):
```typescript
highlights: data.summary?.highlights ?? data.summary?.key_decisions ?? []
```

Fetch and map `event_orgs` from the API meeting row.

- [ ] **Step 4: Update `web/app/meetings/[meetingId]/page.tsx`**

Render source orgs below the event kind badge:
```tsx
{meeting.event_orgs.length > 0 && (
  <p className="text-sm text-muted-foreground">
    {meeting.event_orgs.join(" · ")}
  </p>
)}
```

Replace any hardcoded "Key decisions" label with "Highlights" (works for all event kinds).

- [ ] **Step 5: DB migration for existing summaries (backfill)**

```sql
-- Rename key_decisions → highlights in all existing summary JSONB rows
UPDATE meetings.meetings
SET summary = jsonb_set(
  summary - 'key_decisions',
  '{highlights}',
  summary->'key_decisions'
)
WHERE summary ? 'key_decisions';
```

Run this before deploying the web changes.

- [ ] **Step 6: Visual verification**

Load the Steve Hilton interview page and confirm:
- Title shows "California Courier - Steve Hilton Interview" (or source_title fallback)
- Source org shows "California Courier"
- Summary does not contain "council", "session", "votes", or "Bloomington"
- `highlights` section renders correctly

---

## Dependency Order

```
Task 1 (event kinds + highlights rename)
    → Task 2 (entity validation + event_orgs table)
    → Task 3 (resume fix + source_title capture)
    → Task 4 (summarize pipeline)
    → Task 5 (web rendering) ← run DB backfill BEFORE deploying this
```

Tasks 3 and 4 can be worked in parallel after Task 2 lands.

---

## Out of Scope (deferred)

- **Civic Spaces surfacing architecture** — how events surface to users based on jurisdiction/race follows from politician → race/chamber links; design session TBD.
- **Electoral event summary variants** — debate and forum summary prompts are unchanged. A future plan should add candidate-comparison summary structure.
- **Source org as a structured entity** — `org_name` is plain text for now. A future `source_organizations` table with slugs and URLs can be layered on top.
- **`press_conference` summary variant** — shares the Interview/Media prompts for now; may diverge if press conferences need "statement + Q&A" section structure.
