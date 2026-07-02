# Processing GUI — Slice 2a: Read-Only Review Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Slice 2 decomposition (why this is 2a, not all of Slice 2)

The review page is the biggest surface. Building clip playback + politician search + merge + enrollment + write-back persistence in one pass is high-risk and hard to review, so Slice 2 ships as vertical sub-slices, each usable on its own:

- **2a (this plan):** *Read-only* review page. Click a meeting → see its speakers grouped into "Needs attention" (amber) and "Confirmed" (green), with confidence, sample text, voice-match hints, and **inline clip playback** (HTML5 media seeking to each candidate). **No writes.** Proves: loading review state via `src/review.py`, serving meeting media with range/seek, the grouping UX, clip playback.
- **2b (next):** Write-back — Accept guess, Change (rename + politician search dropdown), persisting via `review.rename_speaker`/`review.link_speaker` + re-persist to disk (mirroring `run_local._persist_after_review` + the `--review` save path) + gate recompute.
- **2c:** Structural actions — Merge (`review.merge_speakers`), Unidentified (`review.mark_unidentified`), Not-a-speaker (`review.mark_non_speaker`).
- **2d:** Enrollment — per-speaker "save this voice" checkbox defaulted by speech length, reusing `_enroll_mapping`/`resolve_mapping_enrollment`/`save_profiles`.

**Goal (2a):** A read-only browser page that shows a processed meeting's speaker review state with playable clips.

**Architecture:** New `gui/review_api.py` loads an existing meeting exactly as `run_local`'s `--review` does — `Meeting.from_dict(transcript_named.json)`, embeddings from `embeddings.json`, `load_profiles()`, then `review.build_review_state(meeting.segments, meeting.speakers, embeddings, profile_db, show_text=True)`. It maps each `SpeakerView` into a `SpeakerCard` (display fields + per-clip **seek positions** already offset-corrected for whichever media we serve), splits into confirmed (`current_name` and `current_confidence >= 0.85`) vs needs-attention, and returns a `ReviewPageData`. `gui/app.py` gets two routes: the review page (renders the template) and a range-capable media route (`FileResponse` of `source.*` video, else `audio.wav`). A small `gui/static/review.js` wires clip buttons to a shared `<video>/<audio>` element. Library rows link to the review page. Builds on Slices 1/1b; reuses `gui/paths.is_safe_meeting_id`.

**Tech Stack:** Python 3, FastAPI (`FileResponse` — Starlette handles `Range` for seeking), Jinja2, vanilla JS. `numpy` (already a dep) for embeddings. Tests: `pytest` + `TestClient`, building fixtures via `Meeting(...).to_dict()` so the on-disk schema is always correct.

---

### Task 1: `SpeakerCard` + `ReviewPageData` models + confirmed/needs split

**Files:**
- Modify: `gui/models.py`
- Test: `tests/test_gui_review.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_review.py`:

```python
from __future__ import annotations

from gui.models import SpeakerCard, ReviewPageData, CONFIDENT_THRESHOLD


def _card(label, name, conf):
    return SpeakerCard(
        label=label, name=name, confidence=conf, method="llm",
        minutes=3.0, seg_count=4, sample_text="hello", hints=[], clip_seeks=[12.0],
    )


def test_confident_threshold_value():
    assert CONFIDENT_THRESHOLD == 0.85


def test_speaker_card_is_confirmed_requires_name_and_high_confidence():
    assert _card("S0", "Mayor Johnson", 0.91).is_confirmed is True
    assert _card("S1", "Mayor Johnson", 0.5).is_confirmed is False   # low conf
    assert _card("S2", None, 0.99).is_confirmed is False              # no name
    assert _card("S3", "(unidentified)", 0.99).is_confirmed is False  # placeholder name


def test_speaker_card_display_name_placeholder():
    assert _card("S0", None, 0.0).display_name == "(unidentified)"
    assert _card("S0", "Mayor Johnson", 0.9).display_name == "Mayor Johnson"


def test_review_page_data_holds_groups():
    page = ReviewPageData(
        meeting_id="m", display_name="Council", media_kind="video",
        needs_attention=[_card("S1", None, 0.0)],
        confirmed=[_card("S0", "Mayor Johnson", 0.9)],
    )
    assert page.speaker_count == 2
    assert page.needs_attention[0].label == "S1"
    assert page.confirmed[0].label == "S0"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -v`
Expected: FAIL — `cannot import name 'SpeakerCard'`.

- [ ] **Step 3: Implement in `gui/models.py`**

Append:

```python
# Confidence at/above which an identified speaker is auto-accepted (green) and
# not surfaced for attention. Mirrors the pipeline's gate threshold.
CONFIDENT_THRESHOLD = 0.85

_UNIDENTIFIED = "(unidentified)"


@dataclass
class SpeakerCard:
    """One speaker in the review page."""

    label: str
    name: Optional[str]
    confidence: float
    method: Optional[str]
    minutes: float
    seg_count: int
    sample_text: Optional[str] = None
    hints: list[tuple[str, float]] = field(default_factory=list)
    clip_seeks: list[float] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name if self.name and self.name.strip() else _UNIDENTIFIED

    @property
    def is_confirmed(self) -> bool:
        return (
            bool(self.name)
            and self.name.strip() not in ("", _UNIDENTIFIED)
            and self.confidence >= CONFIDENT_THRESHOLD
        )


@dataclass
class ReviewPageData:
    meeting_id: str
    display_name: str
    media_kind: Optional[str]  # "video" | "audio" | None
    needs_attention: list[SpeakerCard] = field(default_factory=list)
    confirmed: list[SpeakerCard] = field(default_factory=list)

    @property
    def speaker_count(self) -> int:
        return len(self.needs_attention) + len(self.confirmed)
```

`field` is already imported at the top of `models.py` (used by existing dataclasses); confirm the import line reads `from dataclasses import dataclass, field` and add `field` if missing.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/models.py tests/test_gui_review.py
git commit -m "feat(gui): SpeakerCard + ReviewPageData models for the review page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `gui/review_api.py` — load a meeting into `ReviewPageData`

**Files:**
- Create: `gui/review_api.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
import json

import pytest

from gui.review_api import find_meeting_media, load_review_page


def _write_meeting(mdir, *, clip_start=None):
    """Write a transcript_named.json with 2 speakers (one confident, one not)."""
    from src.models import Meeting, Segment, SpeakerMapping

    segs = [
        Segment(segment_id=0, start_time=10.0, end_time=70.0, speaker_label="SPEAKER_00",
                text="Good evening and welcome to the council meeting.", speaker_name="Mayor Johnson"),
        Segment(segment_id=1, start_time=80.0, end_time=95.0, speaker_label="SPEAKER_01",
                text="Point of order.", speaker_name=None),
    ]
    speakers = {
        "SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Mayor Johnson",
                                     confidence=0.95, id_method="voice"),
        "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01", speaker_name=None, confidence=0.0),
    }
    meeting = Meeting(meeting_id=mdir.name, city="Bloomington", date="2026-02-04",
                      meeting_type="Regular Session", event_kind="council",
                      segments=segs, speakers=speakers, clip_start_seconds=clip_start)
    (mdir / "transcript_named.json").write_text(json.dumps(meeting.to_dict()))


def test_find_meeting_media_prefers_video_then_audio(tmp_path):
    assert find_meeting_media(tmp_path) is None
    (tmp_path / "audio.wav").write_bytes(b"RIFF")
    assert find_meeting_media(tmp_path) == ("audio", "audio.wav")
    (tmp_path / "source.mp4").write_bytes(b"\x00\x00")
    assert find_meeting_media(tmp_path) == ("video", "source.mp4")


def test_load_review_page_missing_meeting_returns_none(tmp_meetings_dir):
    assert load_review_page("nope") is None
    assert load_review_page("../escape") is None  # unsafe id


def test_load_review_page_groups_and_orders(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    page = load_review_page("2026-02-04-council")
    assert page is not None
    assert page.display_name == "Council" or "Bloomington" in page.display_name
    # SPEAKER_00 is named @0.95 -> confirmed; SPEAKER_01 unnamed -> needs attention.
    assert [c.label for c in page.confirmed] == ["SPEAKER_00"]
    assert [c.label for c in page.needs_attention] == ["SPEAKER_01"]
    assert page.media_kind is None  # no media files written


def test_load_review_page_computes_audio_seeks_cliplocal(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    (mdir / "audio.wav").write_bytes(b"RIFF")
    page = load_review_page("2026-02-04-council")
    assert page.media_kind == "audio"
    # SPEAKER_00's longest turn starts at 10.0; audio is clip-local, 3s lead-in -> 7.0
    conf = page.confirmed[0]
    assert conf.clip_seeks and conf.clip_seeks[0] == pytest.approx(7.0)


def test_load_review_page_video_seeks_add_clip_offset(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir, clip_start=600.0)
    (mdir / "source.mp4").write_bytes(b"\x00")
    page = load_review_page("2026-02-04-council")
    assert page.media_kind == "video"
    # video is full source: seek = max(0, 10-3) + 600 = 607.0
    assert page.confirmed[0].clip_seeks[0] == pytest.approx(607.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "media or load_review" -v`
Expected: FAIL — `No module named 'gui.review_api'`.

- [ ] **Step 3: Implement `gui/review_api.py`**

```python
"""Load an already-processed meeting into a read-only ReviewPageData.

Mirrors run_local's --review loading: Meeting.from_dict(transcript_named.json),
embeddings.json, load_profiles(), then review.build_review_state(). Read-only —
no mutation, no persistence (that arrives in Slice 2b)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src import config
from src.models import Meeting

from gui.models import CONFIDENT_THRESHOLD, ReviewPageData, SpeakerCard
from gui.paths import is_safe_meeting_id

# Video container preference order (same set run_local.find_video_file checks).
_VIDEO_EXTS = (".m4v", ".mp4", ".mkv", ".webm", ".avi", ".mov")
_LEAD_IN = 3.0  # seconds of context before a clip, mirroring run_local._review_seek


def find_meeting_media(meeting_dir: Path) -> Optional[tuple[str, str]]:
    """(kind, filename) for the best playable media: video if present, else
    audio.wav, else None. kind is 'video' or 'audio'."""
    for ext in _VIDEO_EXTS:
        candidate = meeting_dir / f"source{ext}"
        if candidate.exists():
            return "video", candidate.name
    if (meeting_dir / "audio.wav").exists():
        return "audio", "audio.wav"
    return None


def _seek(candidate: float, *, is_video: bool, clip_offset: float) -> float:
    """Seek position in the SERVED media. audio.wav is clip-local; the source
    video is the full recording, so clip-local candidates need clip_offset added."""
    base = max(0.0, candidate - _LEAD_IN)
    return base + (clip_offset if is_video else 0.0)


def load_review_page(meeting_id: str) -> Optional[ReviewPageData]:
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    try:
        meeting = Meeting.from_dict(json.loads(named.read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError):
        return None

    import numpy as np
    from src.enroll import load_profiles
    from src import review

    emb_path = meeting_dir / "embeddings.json"
    embeddings = {}
    if emb_path.exists():
        try:
            embeddings = {k: np.array(v) for k, v in json.loads(emb_path.read_text()).items()}
        except (ValueError, OSError):
            embeddings = {}
    profile_db = load_profiles()

    views = review.build_review_state(
        meeting.segments, meeting.speakers, embeddings, profile_db, show_text=True
    )

    media = find_meeting_media(meeting_dir)
    media_kind = media[0] if media else None
    is_video = media_kind == "video"
    clip_offset = meeting.clip_start_seconds or 0.0

    confirmed: list[SpeakerCard] = []
    needs: list[SpeakerCard] = []
    for v in views:
        card = SpeakerCard(
            label=v.label,
            name=v.current_name,
            confidence=v.current_confidence,
            method=v.current_method,
            minutes=v.total_speech_seconds / 60.0,
            seg_count=v.seg_count,
            sample_text=v.sample_text,
            hints=[(h[0], h[1]) for h in v.soft_hints[:3]],
            clip_seeks=[_seek(c, is_video=is_video, clip_offset=clip_offset)
                        for c in v.clip_candidates],
        )
        (confirmed if card.is_confirmed else needs).append(card)

    display_name = meeting.title or " ".join(
        p for p in (meeting.city, meeting.meeting_type) if p
    ) or meeting_id

    return ReviewPageData(
        meeting_id=meeting_id,
        display_name=display_name,
        media_kind=media_kind,
        needs_attention=needs,
        confirmed=confirmed,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add gui/review_api.py tests/test_gui_review.py
git commit -m "feat(gui): load_review_page builds read-only ReviewPageData via review.py

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Review route + range-capable media route

**Files:**
- Modify: `gui/app.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
from fastapi.testclient import TestClient

from gui.app import create_app


def test_review_route_renders_groups(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())
    resp = client.get("/meetings/2026-02-04-council/review")
    assert resp.status_code == 200
    body = resp.text
    assert "Mayor Johnson" in body            # confirmed speaker
    assert "SPEAKER_01" in body               # needs-attention label
    assert "Needs attention" in body and "Confirmed" in body


def test_review_route_404_for_unknown_meeting(tmp_meetings_dir):
    client = TestClient(create_app())
    assert client.get("/meetings/ghost/review").status_code == 404


def test_media_route_serves_audio_with_range(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    (mdir / "audio.wav").write_bytes(b"0123456789")
    client = TestClient(create_app())

    full = client.get("/meetings/2026-02-04-council/media")
    assert full.status_code == 200
    assert full.content == b"0123456789"

    part = client.get("/meetings/2026-02-04-council/media", headers={"Range": "bytes=0-3"})
    assert part.status_code == 206
    assert part.content == b"0123"


def test_media_route_404_when_no_media(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    client = TestClient(create_app())
    assert client.get("/meetings/2026-02-04-council/media").status_code == 404


def test_media_route_404_unsafe_id(tmp_meetings_dir):
    client = TestClient(create_app())
    assert client.get("/meetings/..%2Fx/media").status_code in (404, 400)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "review_route or media_route" -v`
Expected: FAIL — routes don't exist (404 for the render test's assertions / no media route).

- [ ] **Step 3: Add routes to `gui/app.py`**

Add imports:

```python
from gui.review_api import find_meeting_media, load_review_page
```

Inside `create_app()`, after the thumbnail route:

```python
    @app.get("/meetings/{meeting_id}/review", response_class=HTMLResponse)
    def review_page(request: Request, meeting_id: str) -> HTMLResponse:
        page = load_review_page(meeting_id)
        if page is None:
            raise HTTPException(status_code=404)
        return _templates.TemplateResponse(request, "review.html", {"page": page})

    @app.get("/meetings/{meeting_id}/media")
    def media(meeting_id: str):
        if not is_safe_meeting_id(meeting_id):
            raise HTTPException(status_code=404)
        meeting_dir = config.MEETINGS_DIR / meeting_id
        found = find_meeting_media(meeting_dir)
        if found is None:
            raise HTTPException(status_code=404)
        kind, filename = found
        media_type = "video/mp4" if kind == "video" else "audio/wav"
        return FileResponse(str(meeting_dir / filename), media_type=media_type)
```

(`FileResponse` — Starlette — honors the `Range` header and returns `206` for partial content, which is what lets `<video>`/`<audio>` seek. `is_safe_meeting_id`, `HTTPException`, `FileResponse`, `config`, `Request`, `HTMLResponse` are already imported from Slices 1/1b — confirm and add any missing.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -v`
Expected: PASS (all). If the render test fails only because `review.html` doesn't exist yet, proceed to Task 4 and re-run — but add a minimal `review.html` now if `TemplateResponse` raises `TemplateNotFound` (Task 4 fills it in).

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_review.py
git commit -m "feat(gui): review page route + range-capable media route

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Template, clip-playback JS, styles, and library link

**Files:**
- Create: `gui/templates/review.html`
- Create: `gui/static/review.js`
- Modify: `gui/static/style.css`
- Modify: `gui/templates/library.html`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_review.py`:

```python
def test_review_page_has_media_player_and_clip_buttons(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    (mdir / "audio.wav").write_bytes(b"RIFF0000")
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    assert '/meetings/2026-02-04-council/media' in body   # media element src
    assert 'data-seek=' in body                            # at least one clip button
    assert 'review.js' in body                             # playback script wired


def test_library_links_to_review(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/").text
    assert 'href="/meetings/2026-02-04-council/review"' in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "media_player or links_to_review" -v`
Expected: FAIL — no player/buttons/link yet.

- [ ] **Step 3: Create `gui/templates/review.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Review — {{ page.display_name }}</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header>
    <a class="back" href="/">← Library</a>
    <h1>{{ page.display_name }}</h1>
    <p class="sub">{{ page.speaker_count }} speakers · {{ page.needs_attention|length }} need attention</p>
  </header>
  <main class="review">
    {% if page.media_kind == "video" %}
      <video id="player" class="player" src="/meetings/{{ page.meeting_id }}/media" controls preload="metadata"></video>
    {% elif page.media_kind == "audio" %}
      <audio id="player" class="player" src="/meetings/{{ page.meeting_id }}/media" controls preload="metadata"></audio>
    {% else %}
      <p class="empty">No media found for clip playback.</p>
    {% endif %}

    {% macro card(c) %}
    <div class="card {{ 'confirmed' if c.is_confirmed else 'attention' }}">
      <div class="card-head">
        <span class="label">{{ c.label }}</span>
        <span class="cname">{{ c.display_name }}</span>
        {% if c.confidence > 0 %}<span class="conf">conf {{ '%.2f'|format(c.confidence) }}</span>{% endif %}
        <span class="mins">{{ '%.1f'|format(c.minutes) }}m · {{ c.seg_count }} segs</span>
      </div>
      {% for hname, hscore in c.hints %}
        <div class="hint">▸ voice match: {{ hname }} ({{ '%.2f'|format(hscore) }})</div>
      {% endfor %}
      {% if c.sample_text %}<p class="sample">“{{ c.sample_text[:200] }}”</p>{% endif %}
      {% if c.clip_seeks %}
      <div class="clips">
        {% for s in c.clip_seeks %}
        <button type="button" class="clip" data-seek="{{ '%.2f'|format(s) }}">▶ clip {{ loop.index }}</button>
        {% endfor %}
      </div>
      {% endif %}
    </div>
    {% endmacro %}

    <section>
      <h2>Needs attention</h2>
      {% if page.needs_attention %}
        {% for c in page.needs_attention %}{{ card(c) }}{% endfor %}
      {% else %}<p class="empty">Nothing needs attention — every speaker is confirmed.</p>{% endif %}
    </section>

    <section>
      <h2>Confirmed</h2>
      {% for c in page.confirmed %}{{ card(c) }}{% endfor %}
    </section>
  </main>
  <script src="/static/review.js"></script>
</body>
</html>
```

- [ ] **Step 4: Create `gui/static/review.js`**

```javascript
// Clip playback: clicking a clip button seeks the shared media element and plays.
document.addEventListener("click", function (e) {
  const btn = e.target.closest(".clip");
  if (!btn) return;
  const player = document.getElementById("player");
  if (!player) return;
  const seek = parseFloat(btn.getAttribute("data-seek"));
  if (!Number.isNaN(seek)) {
    player.currentTime = seek;
    player.play();
  }
});
```

- [ ] **Step 5: Append review styles to `gui/static/style.css`**

```css
.back { display: inline-block; margin-bottom: 0.5rem; color: #2a5db0; text-decoration: none; font-size: 0.9rem; }
header .sub { margin: 0.25rem 0 0; color: #666; font-size: 0.9rem; }
main.review { max-width: 900px; }
.player { position: sticky; top: 0.5rem; width: 100%; max-height: 40vh; background: #000; border-radius: 0.5rem; margin-bottom: 1rem; z-index: 5; }
audio.player { max-height: none; background: transparent; }
.review section h2 { font-size: 1rem; margin: 1.25rem 0 0.5rem; }
.card { border: 1px solid #e2e2e2; border-radius: 0.5rem; padding: 0.75rem; margin-bottom: 0.6rem; }
.card.attention { border-left: 4px solid #e0a52a; }
.card.confirmed { border-left: 4px solid #2ea56a; }
.card-head { display: flex; gap: 0.6rem; align-items: baseline; flex-wrap: wrap; }
.card .label { font-family: ui-monospace, monospace; font-size: 0.8rem; color: #888; }
.card .cname { font-weight: 600; }
.card .conf, .card .mins { font-size: 0.8rem; color: #777; }
.card .hint { font-size: 0.85rem; color: #9a6a00; }
.card .sample { font-size: 0.9rem; color: #333; margin: 0.4rem 0; }
.clips { display: flex; gap: 0.4rem; flex-wrap: wrap; }
button.clip { font-size: 0.85rem; padding: 0.2rem 0.6rem; border: 1px solid #ccc; border-radius: 0.4rem; background: #f6f6f8; cursor: pointer; }
button.clip:hover { background: #e9e9ef; }
```

- [ ] **Step 6: Link library rows to the review page — `gui/templates/library.html`**

Change the display-name line inside `td.name` from a bare `<div>{{ m.display_name }}</div>` to a link:

```html
              <div><a class="mlink" href="/meetings/{{ m.meeting_id }}/review">{{ m.display_name }}</a></div>
```

And append to `gui/static/style.css`:

```css
a.mlink { color: #2a5db0; text-decoration: none; }
a.mlink:hover { text-decoration: underline; }
```

- [ ] **Step 7: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -v`
Expected: PASS (all).

- [ ] **Step 8: Commit**

```bash
git add gui/templates/review.html gui/static/review.js gui/static/style.css gui/templates/library.html tests/test_gui_review.py
git commit -m "feat(gui): read-only review page with inline clip playback + library link

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite regression + manual smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Slices 1, 1b, 2a), no regressions.

- [ ] **Step 2: Manual smoke**

Run: `.venv/bin/python -m gui`; open http://127.0.0.1:8000, click a meeting's name → the review page shows Needs-attention and Confirmed groups; the media element loads; clicking "▶ clip N" seeks and plays. Ctrl-C to stop (leave no listener on 8000).

---

## Self-Review

**Spec coverage:** Read-only review page (Task 3 route + Task 4 template) ✅ · load via `review.build_review_state` mirroring `--review` (Task 2) ✅ · green/amber grouping at 0.85 (Task 1 `is_confirmed` + Task 2 split) ✅ · inline clip playback with offset-correct seeks — audio clip-local, video +clip_offset (Task 2 `_seek` + Task 4 JS/media element) ✅ · range-capable media route for seeking (Task 3) ✅ · path safety reused (`is_safe_meeting_id`, Task 2/3) ✅ · library links in (Task 4) ✅ · NO writes/persistence/enrollment (correctly deferred to 2b–2d) ✅.

**Placeholder scan:** none — complete code + exact commands throughout.

**Type consistency:** `SpeakerCard`/`ReviewPageData`/`CONFIDENT_THRESHOLD` used identically across `models.py`, `review_api.py`, template, tests. `find_meeting_media` returns `(kind, filename)` consumed the same way in `review_api.load_review_page` and the `app.py` media route. `load_review_page` returns `Optional[ReviewPageData]`; both the route and tests handle `None` → 404. Fixtures build via `Meeting(...).to_dict()` so the persisted schema matches `Meeting.from_dict`.
