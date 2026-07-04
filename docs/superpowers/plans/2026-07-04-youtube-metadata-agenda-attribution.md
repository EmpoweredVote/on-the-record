# YouTube Metadata → Agenda Sections + Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture yt-dlp `chapters`, `description`, and `uploader`/`channel` at ingest, use creator chapters as a hint to the section classifier (keeping neutral titles verbatim), and fix the interview executive summary so the video title can never be used as the interviewer/outlet.

**Architecture:** Add two pure, unit-testable helpers — `parse_description_chapters` / `normalize_chapters` in `src/ingest.py` and `chapters_to_segment_hints` in `src/summarize.py` — so the network- and LLM-dependent code stays thin. Persist `source_channel` and `source_chapters` on `ProcessingMetadata` so they survive the checkpoint into the summarize stage. Thread an optional `chapter_hint` string into both classifiers (`classify_sections` for council, `_classify_sections_interview` for interviews) and swap the outlet fallback chain from `source_title` to `source_channel`.

**Tech Stack:** Python 3, pytest, yt-dlp, Anthropic SDK. No new dependencies.

Reference spec: `docs/superpowers/specs/2026-07-04-youtube-metadata-agenda-attribution-design.md`

---

## File Structure

- `src/ingest.py` — **modify.** Add `parse_description_chapters` (pure), `_drop_intro_chapters` (pure), and `normalize_chapters` (pure); extend the existing `skip_download` `extract_info` block to also read `uploader`/`channel`, `chapters`, `description`; return `source_channel` and `source_chapters` in the metadata dict.
- `src/models.py` — **modify.** Add `source_channel` and `source_chapters` fields to `ProcessingMetadata`, with `to_dict`/`from_dict` support.
- `run_local.py` — **modify.** Set the two new metadata fields alongside the existing `source_title` assignment (~line 849).
- `src/summarize.py` — **modify.** Add `chapters_to_segment_hints` (pure) and `_format_chapter_hint` (pure); add an optional `chapter_hint` param to `classify_sections`, `_classify_sections_chunk`, and `_classify_sections_interview`, injecting prompt guidance; build the hint in `generate_summary`; change the outlet fallback in `_generate_interview_executive_summary`.
- `tests/test_ingest_chapters.py` — **create.** Unit tests for description parsing + chapter normalization.
- `tests/test_summarize_chapters.py` — **create.** Unit tests for `chapters_to_segment_hints`, hint injection into classifiers, and the outlet fallback chain.
- `tests/test_models.py` — **modify.** Add round-trip assertions for the two new metadata fields.

---

## Task 1: `parse_description_chapters` — extract timestamped lines from a description

**Files:**
- Modify: `src/ingest.py`
- Test: `tests/test_ingest_chapters.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_ingest_chapters.py`:

```python
"""Chapter extraction from yt-dlp metadata (description fallback + normalization)."""

from src.ingest import parse_description_chapters, normalize_chapters


CBS_DESCRIPTION = """California has spent more than $14 billion on high-speed rail, but the project remains unfinished and controversial.

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
** Rep. Eric Swalwell appeared in an earlier version of this composite.

COMING SOON | CBS News California Interactive Governor Candidate Guide
"""


def test_parses_only_timestamp_led_lines():
    chapters = parse_description_chapters(CBS_DESCRIPTION)
    titles = [c["title"] for c in chapters]
    assert len(chapters) == 10
    assert titles[0] == "CBS News California Investigates"
    assert titles[1] == "Steve Hilton on high-speed rail."
    # Double-space after timestamp still parses, title is stripped:
    assert "Matt Mahan on high-speed rail." in titles
    # Non-timestamp lines excluded:
    assert all("Swalwell" not in t for t in titles)
    assert all("COMING SOON" not in t for t in titles)


def test_start_times_and_inferred_end_times():
    chapters = parse_description_chapters(CBS_DESCRIPTION)
    assert chapters[0]["start_time"] == 0.0
    assert chapters[1]["start_time"] == 14.0
    assert chapters[2]["start_time"] == 124.0  # 02:04
    # end_time is the next entry's start; last is None
    assert chapters[0]["end_time"] == 14.0
    assert chapters[-1]["end_time"] is None


def test_hms_timestamps_parse():
    desc = "Intro at start\n1:02:03 Deep segment\n1:05:10 Next segment"
    chapters = parse_description_chapters(desc)
    assert len(chapters) == 2
    assert chapters[0]["start_time"] == 3723.0  # 1:02:03
    assert chapters[0]["title"] == "Deep segment"


def test_fewer_than_two_matches_returns_empty():
    desc = "Some prose.\n00:30 The only timestamp line here.\nMore prose."
    assert parse_description_chapters(desc) == []


def test_none_or_empty_description():
    assert parse_description_chapters(None) == []
    assert parse_description_chapters("") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ingest_chapters.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_description_chapters'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/ingest.py` (after the imports, before `_is_url`):

```python
import re

# A line counts as a chapter only if its first non-whitespace token is a
# timestamp (MM:SS or HH:MM:SS), followed by whitespace and a non-empty title.
_TIMESTAMP_LINE_RE = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(\S.*?)\s*$")


def _timestamp_to_seconds(ts: str) -> float:
    """Parse 'MM:SS' or 'HH:MM:SS' into float seconds."""
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        h, m, s = parts
        return float(h * 3600 + m * 60 + s)
    m, s = parts
    return float(m * 60 + s)


def parse_description_chapters(description: str | None) -> list[dict]:
    """Extract timestamped agenda lines from a video description.

    Only lines whose first non-whitespace token is a timestamp are treated as
    chapters. Requires at least 2 such lines, otherwise returns []. Each result
    is {start_time, end_time, title}; end_time is the next entry's start_time
    (None for the last entry).
    """
    if not description:
        return []

    parsed: list[dict] = []
    for line in description.splitlines():
        match = _TIMESTAMP_LINE_RE.match(line)
        if match:
            parsed.append({
                "start_time": _timestamp_to_seconds(match.group(1)),
                "title": match.group(2).strip(),
            })

    if len(parsed) < 2:
        return []

    for i, chap in enumerate(parsed):
        chap["end_time"] = parsed[i + 1]["start_time"] if i + 1 < len(parsed) else None

    return parsed
```

Also add a stub `normalize_chapters` (implemented in Task 2) so the test module import succeeds:

```python
def normalize_chapters(info: dict) -> list[dict]:
    """Placeholder — implemented in Task 2."""
    raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ingest_chapters.py -k "parse or hms or fewer or none" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest.py tests/test_ingest_chapters.py
git commit -m "feat(ingest): parse timestamped chapters from video descriptions"
```

---

## Task 2: `normalize_chapters` — prefer yt-dlp chapters, fall back to description

**Files:**
- Modify: `src/ingest.py`
- Test: `tests/test_ingest_chapters.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ingest_chapters.py`:

```python
def test_normalize_uses_ytdlp_chapters_when_present():
    # start times are non-zero so the intro-drop doesn't interfere here;
    # this test isolates "prefer yt-dlp chapters over description".
    info = {
        "chapters": [
            {"start_time": 10.0, "end_time": 40.0, "title": "Zoning"},
            {"start_time": 40.0, "end_time": 90.0, "title": "Housing"},
        ],
        "description": "00:00 Ignored\n01:00 Also ignored",
    }
    chapters = normalize_chapters(info)
    assert [c["title"] for c in chapters] == ["Zoning", "Housing"]
    assert chapters[0]["start_time"] == 10.0
    assert chapters[0]["end_time"] == 40.0


def test_normalize_falls_back_to_description():
    info = {
        "chapters": [],
        "description": "00:30 First topic\n01:30 Second topic",
    }
    chapters = normalize_chapters(info)
    assert [c["title"] for c in chapters] == ["First topic", "Second topic"]
    assert chapters[1]["start_time"] == 90.0


def test_normalize_drops_intro_from_ytdlp():
    info = {
        "chapters": [
            {"start_time": 0.0, "end_time": 30.0, "title": "Intro"},
            {"start_time": 30.0, "end_time": 90.0, "title": "Housing"},
        ],
    }
    chapters = normalize_chapters(info)
    assert [c["title"] for c in chapters] == ["Housing"]


def test_normalize_drops_intro_from_description():
    info = {"chapters": [], "description": "00:00 Branding\n01:30 Real topic\n02:30 Another"}
    chapters = normalize_chapters(info)
    assert [c["title"] for c in chapters] == ["Real topic", "Another"]


def test_normalize_no_chapters_no_timestamps_returns_empty():
    assert normalize_chapters({"chapters": [], "description": "just prose"}) == []
    assert normalize_chapters({}) == []


def test_normalize_coerces_partial_ytdlp_entries():
    # yt-dlp entries sometimes omit end_time; title may be missing.
    info = {"chapters": [{"start_time": 5.0, "title": "A"}, {"start_time": 12.0}]}
    chapters = normalize_chapters(info)
    assert chapters[0] == {"start_time": 5.0, "end_time": None, "title": "A"}
    assert chapters[1]["title"] == ""
    assert chapters[1]["start_time"] == 12.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ingest_chapters.py -k normalize -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Write minimal implementation**

Replace the `normalize_chapters` stub in `src/ingest.py`, and add the intro-drop helper above it:

```python
def _drop_intro_chapters(chapters: list[dict]) -> list[dict]:
    """Remove intro-type entries — a chapter starting at 0:00 is almost always a
    cold open / branding card, not an agenda item."""
    return [c for c in chapters if c.get("start_time") != 0.0]


def normalize_chapters(info: dict) -> list[dict]:
    """Build a normalized chapter list from a yt-dlp info dict.

    Prefers the creator's formal chapters (info["chapters"]); when absent/empty,
    falls back to timestamped lines in the description. Intro-type entries
    (start_time 0:00) are dropped from either source. Each result is
    {start_time: float, end_time: float | None, title: str}.
    """
    raw = info.get("chapters") or []
    normalized: list[dict] = []
    if raw:
        for c in raw:
            start = c.get("start_time")
            if start is None:
                continue
            normalized.append({
                "start_time": float(start),
                "end_time": float(c["end_time"]) if c.get("end_time") is not None else None,
                "title": (c.get("title") or "").strip(),
            })

    if not normalized:
        normalized = parse_description_chapters(info.get("description"))

    return _drop_intro_chapters(normalized)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ingest_chapters.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest.py tests/test_ingest_chapters.py
git commit -m "feat(ingest): normalize chapters, preferring yt-dlp over description"
```

---

## Task 3: Capture channel + chapters in `normalize_audio`

**Files:**
- Modify: `src/ingest.py:87-104,123-133`

- [ ] **Step 1: Extend the yt-dlp `extract_info` block**

In `normalize_audio`, replace the metadata block (currently lines ~87-104) so it captures channel and chapters from the *same* `extract_info` call:

```python
    # Download from URL if needed
    source_title = None
    source_channel = None
    source_chapters: list[dict] = []
    if _is_url(source_str):
        from .download import download_from_url, is_ytdlp_url

        # Use a placeholder stem; yt-dlp may change the extension
        download_path = output_path.parent / "source.mp4"
        print(f"  Downloading from URL...")
        actual_path = download_from_url(source_str, download_path, cookies_file=cookies_file)
        ffmpeg_input = str(actual_path)

        if is_ytdlp_url(source_str):
            try:
                import yt_dlp
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                    info = ydl.extract_info(source_str, download=False)
                    source_title = info.get("title") or None
                    source_channel = info.get("uploader") or info.get("channel") or None
                    source_chapters = normalize_chapters(info)
            except Exception:
                pass
    else:
        ffmpeg_input = str(Path(input_path))
```

- [ ] **Step 2: Add the new keys to the return dict**

Replace the `return {...}` at the end of `normalize_audio` to include the two new keys:

```python
    return {
        "source": source_str,
        "output": str(output_path),
        "duration_seconds": duration,
        "sample_rate": config.SAMPLE_RATE,
        "channels": config.CHANNELS,
        "noise_reduced": noise_reduce,
        "clip_start_seconds": clip_start,
        "clip_end_seconds": clip_end,
        "source_title": source_title,
        "source_channel": source_channel,
        "source_chapters": source_chapters,
    }
```

- [ ] **Step 3: Verify nothing broke**

Run: `.venv/bin/python -m pytest tests/test_ingest_chapters.py tests/test_ingest_clip.py -v`
Expected: PASS (no network — `normalize_audio` isn't invoked by these; this confirms the module still imports and helpers still pass)

- [ ] **Step 4: Commit**

```bash
git add src/ingest.py
git commit -m "feat(ingest): capture channel + chapters from yt-dlp metadata"
```

---

## Task 4: Persist `source_channel` + `source_chapters` on `ProcessingMetadata`

**Files:**
- Modify: `src/models.py:201-232`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_processing_metadata_roundtrips_channel_and_chapters():
    from src.models import ProcessingMetadata

    meta = ProcessingMetadata(
        source_title="Some Video Title",
        source_channel="Brian Tyler Cohen",
        source_chapters=[{"start_time": 0.0, "end_time": 30.0, "title": "Intro"}],
    )
    restored = ProcessingMetadata.from_dict(meta.to_dict())
    assert restored.source_channel == "Brian Tyler Cohen"
    assert restored.source_chapters == [{"start_time": 0.0, "end_time": 30.0, "title": "Intro"}]


def test_processing_metadata_omits_unset_new_fields():
    from src.models import ProcessingMetadata

    d = ProcessingMetadata().to_dict()
    assert "source_channel" not in d
    assert "source_chapters" not in d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models.py -k "channel or chapters" -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'source_channel'`

- [ ] **Step 3: Write minimal implementation**

In `src/models.py`, add the two fields to `ProcessingMetadata` (after `source_title`):

```python
    source_title: Optional[str] = None
    source_channel: Optional[str] = None
    source_chapters: Optional[list] = None
```

In `to_dict`, after the `source_title` block:

```python
        if self.source_channel is not None:
            d["source_channel"] = self.source_channel
        if self.source_chapters is not None:
            d["source_chapters"] = self.source_chapters
```

In `from_dict`, add the two kwargs:

```python
            source_title=d.get("source_title"),
            source_channel=d.get("source_channel"),
            source_chapters=d.get("source_chapters"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_models.py -k "channel or chapters" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat(models): persist source_channel + source_chapters"
```

---

## Task 5: Wire ingest metadata into the meeting in `run_local.py`

**Files:**
- Modify: `run_local.py:849-850`

- [ ] **Step 1: Set the new fields alongside `source_title`**

In `run_local.py`, replace the existing block (lines ~849-850):

```python
        if metadata.get("source_title"):
            meeting.processing_metadata.source_title = metadata["source_title"]
```

with:

```python
        if metadata.get("source_title"):
            meeting.processing_metadata.source_title = metadata["source_title"]
        if metadata.get("source_channel"):
            meeting.processing_metadata.source_channel = metadata["source_channel"]
        if metadata.get("source_chapters"):
            meeting.processing_metadata.source_chapters = metadata["source_chapters"]
```

- [ ] **Step 2: Verify the module imports**

Run: `.venv/bin/python -c "import run_local"`
Expected: no output, exit 0

- [ ] **Step 3: Commit**

```bash
git add run_local.py
git commit -m "feat(pipeline): store source_channel + source_chapters on meeting"
```

---

## Task 6: `chapters_to_segment_hints` — map chapter times to segment indices

**Files:**
- Modify: `src/summarize.py`
- Test: `tests/test_summarize_chapters.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_summarize_chapters.py`:

```python
"""Chapter-hint mapping and injection for the section classifier."""

from src.models import Segment
from src.summarize import chapters_to_segment_hints, _format_chapter_hint


def _segs(starts):
    return [
        Segment(segment_id=i, start_time=s, end_time=s + 10.0, text=f"seg {i}")
        for i, s in enumerate(starts)
    ]


def test_maps_chapter_start_to_containing_segment():
    segments = _segs([0.0, 30.0, 60.0, 90.0])
    chapters = [
        {"start_time": 0.0, "end_time": 60.0, "title": "Intro"},
        {"start_time": 60.0, "end_time": None, "title": "Housing"},
    ]
    hints = chapters_to_segment_hints(chapters, segments)
    assert hints == [
        {"start_segment": 0, "end_segment": 1, "title": "Intro"},
        {"start_segment": 2, "end_segment": 3, "title": "Housing"},
    ]


def test_snaps_to_nearest_when_between_segments():
    # Chapter at 35s: segment 1 starts 30s (contains it) — pick 1, not 2.
    segments = _segs([0.0, 30.0, 60.0])
    chapters = [
        {"start_time": 0.0, "end_time": 35.0, "title": "A"},
        {"start_time": 35.0, "end_time": None, "title": "B"},
    ]
    hints = chapters_to_segment_hints(chapters, segments)
    assert hints[1]["start_segment"] == 1


def test_empty_chapters_or_segments():
    assert chapters_to_segment_hints([], _segs([0.0])) == []
    assert chapters_to_segment_hints([{"start_time": 0.0, "title": "X"}], []) == []


def test_format_chapter_hint_includes_titles_and_guidance():
    hints = [{"start_segment": 0, "end_segment": 2, "title": "Housing"}]
    text = _format_chapter_hint(hints)
    assert "Housing" in text
    assert "verbatim" in text.lower()


def test_format_chapter_hint_empty_is_empty_string():
    assert _format_chapter_hint([]) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_summarize_chapters.py -v`
Expected: FAIL with `ImportError: cannot import name 'chapters_to_segment_hints'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/summarize.py` (near the top, after `_condensed_transcript`):

```python
def chapters_to_segment_hints(
    chapters: list[dict],
    segments: list[Segment],
) -> list[dict]:
    """Map chapter start times to segment indices for use as a classifier prior.

    Each chapter start snaps to the segment that contains it (the last segment
    whose start_time <= the chapter start), falling back to the nearest segment.
    Returns [{start_segment, end_segment, title}] with end_segment inferred from
    the next chapter's start_segment.
    """
    if not chapters or not segments:
        return []

    def _seg_index_for(t: float) -> int:
        # Last segment starting at or before t; else the closest by start_time.
        idx = 0
        for i, seg in enumerate(segments):
            if seg.start_time <= t:
                idx = i
            else:
                break
        return idx

    starts = [_seg_index_for(c["start_time"]) for c in chapters]
    hints = []
    for i, chap in enumerate(chapters):
        start_seg = starts[i]
        if i + 1 < len(chapters):
            end_seg = max(start_seg, starts[i + 1] - 1)
        else:
            end_seg = len(segments) - 1
        hints.append({
            "start_segment": start_seg,
            "end_segment": end_seg,
            "title": chap.get("title", ""),
        })
    return hints


def _format_chapter_hint(hints: list[dict]) -> str:
    """Render segment hints as prompt guidance, or '' when there are none."""
    if not hints:
        return ""
    lines = [
        f'- segments {h["start_segment"]}-{h["end_segment"]}: "{h["title"]}"'
        for h in hints
    ]
    listing = "\n".join(lines)
    return (
        "\n\nThe video creator provided these chapter boundaries and titles:\n"
        f"{listing}\n"
        "Strongly prefer these boundaries and titles. Keep each title VERBATIM if "
        "it is neutral and descriptive. Rewrite a title only if it is clickbait, "
        "promotional, or ALL-CAPS hype. Adjust boundaries only if clearly wrong."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_summarize_chapters.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/summarize.py tests/test_summarize_chapters.py
git commit -m "feat(summarize): map chapters to segment hints for classifier prior"
```

---

## Task 7: Inject the chapter hint into both classifiers

**Files:**
- Modify: `src/summarize.py` (`_classify_sections_chunk`, `classify_sections`, `_classify_sections_interview`)
- Test: `tests/test_summarize_chapters.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_summarize_chapters.py`:

```python
from unittest.mock import MagicMock
from src.summarize import classify_sections, _classify_sections_interview


def _capture_client(response_json: str):
    client = MagicMock()
    captured = {}

    def create(**kwargs):
        captured["content"] = kwargs["messages"][0]["content"]
        msg = MagicMock()
        msg.content = [MagicMock(text=response_json)]
        return msg

    client.messages.create.side_effect = create
    return client, captured


def test_council_classifier_includes_hint():
    client, captured = _capture_client('{"sections": []}')
    classify_sections(client, _segs([0.0, 10.0]), chapter_hint="HINTMARKER housing")
    assert "HINTMARKER housing" in captured["content"]


def test_council_classifier_omits_hint_when_absent():
    client, captured = _capture_client('{"sections": []}')
    classify_sections(client, _segs([0.0, 10.0]))
    assert "HINTMARKER" not in captured["content"]


def test_interview_classifier_includes_hint():
    client, captured = _capture_client('{"sections": []}')
    _classify_sections_interview(client, _segs([0.0, 10.0]), chapter_hint="HINTMARKER tax")
    assert "HINTMARKER tax" in captured["content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_summarize_chapters.py -k classifier -v`
Expected: FAIL with `TypeError: classify_sections() got an unexpected keyword argument 'chapter_hint'`

- [ ] **Step 3: Write minimal implementation**

In `src/summarize.py`:

(a) `_classify_sections_chunk` — add the param and append the hint to the user content:

```python
def _classify_sections_chunk(
    client,
    condensed: str,
    seg_offset: int = 0,
    chapter_hint: str = "",
) -> list[dict]:
    """Classify one chunk of transcript into sections using Haiku."""
    message = client.messages.create(
        model=config.SUMMARY_CLASSIFY_MODEL,
        max_tokens=config.SUMMARY_MAX_TOKENS_CLASSIFY,
        system=_CLASSIFY_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Classify this council meeting transcript into sections:\n\n{condensed}{chapter_hint}",
        }],
    )
    # ... (rest unchanged)
```

(b) `classify_sections` — add the param and pass it through. Only apply the hint on the single-chunk path (chunked transcripts renumber segments per chunk, so the hint's segment indices would not line up):

```python
def classify_sections(
    client,
    segments: list[Segment],
    chapter_hint: str = "",
) -> list[dict]:
    """Classify the full transcript into sections, chunking if needed."""
    chunk_size = config.SUMMARY_CHUNK_SIZE

    if len(segments) <= chunk_size:
        condensed = _condensed_transcript(segments)
        return _classify_sections_chunk(client, condensed, chapter_hint=chapter_hint)

    # Chunked path: hint segment indices span the whole transcript, not a chunk,
    # so we do not inject it here (falls back to today's behavior).
    all_sections = []
    for i in range(0, len(segments), chunk_size):
        chunk = segments[i : i + chunk_size]
        condensed = _condensed_transcript(chunk)
        chunk_sections = _classify_sections_chunk(client, condensed, seg_offset=i)
        all_sections.extend(chunk_sections)
    # ... (merge logic unchanged)
```

(c) `_classify_sections_interview` — add the param and append the hint:

```python
def _classify_sections_interview(
    client,
    segments: list[Segment],
    chapter_hint: str = "",
) -> list[dict]:
    """Classify interview transcript into topic sections using Haiku."""
    condensed = _condensed_transcript(segments)
    message = client.messages.create(
        model=config.SUMMARY_CLASSIFY_MODEL,
        max_tokens=config.SUMMARY_MAX_TOKENS_CLASSIFY,
        system=_INTERVIEW_CLASSIFY_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Classify this interview transcript into topic sections:\n\n{condensed}{chapter_hint}",
        }],
    )
    # ... (rest unchanged)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_summarize_chapters.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/summarize.py tests/test_summarize_chapters.py
git commit -m "feat(summarize): thread chapter hint into both classifiers"
```

---

## Task 8: Build the hint in `generate_summary` and pass it through

**Files:**
- Modify: `src/summarize.py:518-525` (inside `generate_summary`)
- Test: `tests/test_summarize_chapters.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_summarize_chapters.py`:

```python
from unittest.mock import patch
from src.models import Meeting
from src.summarize import generate_summary


def _meeting_with_chapters(chapters):
    segs = _segs([0.0, 30.0, 60.0])
    m = Meeting(
        meeting_id="t", city=None, date="2026-07-04",
        meeting_type="News Clip", event_kind="news_clip", segments=segs,
    )
    m.processing_metadata.source_chapters = chapters
    return m


def test_generate_summary_passes_chapter_hint_to_classifier():
    meeting = _meeting_with_chapters(
        [{"start_time": 0.0, "end_time": 60.0, "title": "UNIQUEHINT topic"},
         {"start_time": 60.0, "end_time": None, "title": "Second"}]
    )
    client, captured = _capture_client('{"sections": []}')
    with patch("src.summarize.anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        generate_summary(meeting)
    # First call is the classifier; its content should carry the hint.
    assert "UNIQUEHINT topic" in captured["content"]
```

Note: `_capture_client` records only the most recent call's content; the classifier is the first `create` call and no sections are returned, so `generate_summary` returns early after classification — leaving the classifier call as the captured one.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_summarize_chapters.py -k passes_chapter_hint -v`
Expected: FAIL (hint not present — `generate_summary` does not yet build/pass it)

- [ ] **Step 3: Write minimal implementation**

In `generate_summary`, replace the classification block (currently lines ~520-525):

```python
    # --- Pass 1: Classify sections ---
    _progress("classifying sections")
    chapter_hint = _format_chapter_hint(
        chapters_to_segment_hints(
            meeting.processing_metadata.source_chapters or [],
            segments,
        )
    )
    if is_interview:
        raw_sections = _classify_sections_interview(client, segments, chapter_hint=chapter_hint)
    else:
        raw_sections = classify_sections(client, segments, chapter_hint=chapter_hint)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_summarize_chapters.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/summarize.py tests/test_summarize_chapters.py
git commit -m "feat(summarize): build chapter hint from meeting metadata"
```

---

## Task 9: Fix the outlet fallback — channel, never the title

**Files:**
- Modify: `src/summarize.py:442-446` (`_generate_interview_executive_summary`)
- Test: `tests/test_summarize_chapters.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_summarize_chapters.py`:

```python
from src.summarize import _resolve_outlet


def _meeting_for_outlet(event_orgs=None, channel=None, title=None):
    m = Meeting(
        meeting_id="t", city=None, date="2026-07-04",
        meeting_type="News Clip", event_kind="news_clip",
        event_orgs=event_orgs or [], segments=[],
    )
    m.processing_metadata.source_channel = channel
    m.processing_metadata.source_title = title
    return m


def test_outlet_prefers_event_org():
    m = _meeting_for_outlet(event_orgs=["KQED"], channel="Some Channel", title="CLICKBAIT")
    assert _resolve_outlet(m) == "KQED"


def test_outlet_uses_channel_when_no_event_org():
    m = _meeting_for_outlet(channel="Brian Tyler Cohen", title="LA Mayor's race EXPLODES")
    assert _resolve_outlet(m) == "Brian Tyler Cohen"


def test_outlet_never_uses_title():
    m = _meeting_for_outlet(title="LA Mayor's race EXPLODES with major UPDATE")
    assert _resolve_outlet(m) == "the interviewer"


def test_outlet_default_when_all_absent():
    m = _meeting_for_outlet()
    assert _resolve_outlet(m) == "the interviewer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_summarize_chapters.py -k outlet -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_outlet'`

- [ ] **Step 3: Write minimal implementation**

In `src/summarize.py`, add a helper above `_generate_interview_executive_summary`:

```python
def _resolve_outlet(meeting: Meeting) -> str:
    """Interviewer/outlet for the exec summary.

    Resolution order: a human-set event_org, then the captured source channel,
    then a generic fallback. The raw video TITLE is never used — a clickbait
    title must never land in the 'In an interview with ___' slot.
    """
    if meeting.event_orgs:
        return meeting.event_orgs[0]
    if meeting.processing_metadata.source_channel:
        return meeting.processing_metadata.source_channel
    return "the interviewer"
```

Then replace the `outlet = (...)` block (lines ~442-446) in `_generate_interview_executive_summary`:

```python
    outlet = _resolve_outlet(meeting)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_summarize_chapters.py -k outlet -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/summarize.py tests/test_summarize_chapters.py
git commit -m "fix(summarize): use channel (never video title) as interview outlet"
```

---

## Task 10: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest tests/test_ingest_chapters.py tests/test_summarize_chapters.py tests/test_summarize.py tests/test_models.py tests/test_ingest_clip.py -v`
Expected: all PASS

- [ ] **Step 2: Run the entire test suite to catch unrelated breakage**

Run: `.venv/bin/python -m pytest -q`
Expected: no new failures relative to the pre-change baseline

- [ ] **Step 3: Commit any incidental fixes**

If Step 2 surfaced a break caused by these changes, fix it, then:

```bash
git add -A
git commit -m "test: fix regressions from chapter/attribution changes"
```

---

## Self-Review Notes

- **Spec §1 (capture metadata + intro drop):** Tasks 1-3. Intro-type (`0:00`) entries dropped in `_drop_intro_chapters` for both sources, tested in Task 2. ✓
- **Spec §2 (persist):** Task 4. ✓
- **Spec §3 (wire ingest→meeting):** Task 5. ✓
- **Spec §4 (chapter hint, both classifiers, keep-if-clean):** Tasks 6-8. Hint text carries the "keep VERBATIM unless clickbait" guidance (`_format_chapter_hint`). ✓
- **Spec §5 (outlet fix):** Task 9 — title removed from chain, asserted by `test_outlet_never_uses_title`. ✓
- **Spec testing bullets:** description parser (Task 1), chapter→segment mapping (Task 6), outlet chain (Task 9), chapters-present classification (Tasks 7-8). ✓
- **Known limitation (documented in Task 7b):** the chapter hint is applied only on the single-chunk classify path; long, chunked council transcripts fall back to today's behavior because per-chunk segment renumbering breaks the hint's global indices. Acceptable for now — interview/agenda videos are typically under one chunk; revisit if long council videos need it.
