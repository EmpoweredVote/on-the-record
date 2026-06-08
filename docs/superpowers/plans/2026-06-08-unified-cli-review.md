# Unified CLI Speaker Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate speaker review/name/merge into one guided flow that auto-runs after interactive pipeline runs, add a real in-review same-speaker merge, and fix the yt-dlp video / roster-alias / audio-fallback gaps — all on top of a new pure, testable `src/review.py` core.

**Architecture:** A new `src/review.py` holds pure operations (`build_review_state`, `rename_speaker`, `merge_speakers`, `speakers_needing_review`) over in-memory data. `run_local.py`'s interactive loop calls them and handles I/O + persistence; the future GUI reuses the same core. Three targeted bug fixes land in `src/download.py`, `src/roster.py`, and `run_local.py`.

**Tech Stack:** Python 3, pytest, numpy, ffplay (ffmpeg), yt-dlp. Use `.venv/bin/python` for all commands.

Spec: `docs/superpowers/specs/2026-06-08-unified-cli-review-design.md`

---

## File Structure

- **Create** `src/review.py` — pure review core: `SpeakerView`, `RenameResult`, `MergeResult` dataclasses + the four functions.
- **Create** `tests/test_review.py` — unit tests for the core.
- **Modify** `src/roster.py` — `add_alias` becomes body-aware (writes per-body cache when `body_slug` given).
- **Modify** `src/download.py` — `download_via_ytdlp` downloads capped-resolution video, not audio-only; extract `_ytdlp_format()` for testability.
- **Modify** `run_local.py`:
  - add `play_speaker_clip()` (video-or-audio playback); route the review loop + borderline-enroll loop through it.
  - refactor `_interactive_speaker_review` to the new signature, consuming `src/review.py` + adding `[M]erge`.
  - update the 3 callers (`_review_meeting`, `_identify_speakers_standalone`, the `--pre-identify` block) + persist merges.
  - Stage 4 auto-drops into the rich review on interactive runs; add `--no-review` and a canonical `--review <ID>`.
- **Modify** `README.md` — document the new flow.
- **Modify** `tests/` — targeted tests for the three fixes.

**Shared signatures (defined once; referenced across tasks):**

```python
# src/review.py
@dataclass
class SpeakerView:
    label: str
    current_name: Optional[str]
    current_confidence: float
    current_method: Optional[str]
    seg_count: int
    total_speech_seconds: float
    clip_start: Optional[float]
    sample_text: Optional[str]
    soft_hints: list[tuple[str, float]]
    needs_review: bool

@dataclass
class RenameResult:
    label: str
    old_name: Optional[str]
    new_name: str
    alias_suggestion: Optional[str]   # old wrong name to offer as alias, else None

@dataclass
class MergeResult:
    source_label: str
    target_label: str
    moved_segments: int
    combined_name: Optional[str]

def build_review_state(segments, mappings, embeddings, profile_db, *, show_text: bool) -> list[SpeakerView]: ...
def rename_speaker(mappings, segments, label: str, new_name: str, *, roster=None) -> RenameResult: ...
def merge_speakers(segments, embeddings, mappings, source_label: str, target_label: str) -> MergeResult: ...
def speakers_needing_review(mappings) -> list[str]: ...
```

`embeddings` is a `dict[str, np.ndarray]` of one centroid per speaker label (matches `embeddings.json` loaded shape). `mappings` is `dict[str, SpeakerMapping]`. `segments` is `list[Segment]`.

---

## Task 1: `src/review.py` — SpeakerView + build_review_state

**Files:**
- Create: `src/review.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review.py`:

```python
"""Tests for the pure speaker-review core (spec 2026-06-08-unified-cli-review)."""
from __future__ import annotations

import numpy as np
import pytest

from src.models import Segment, SpeakerMapping
from src import review


def _seg(label, start, end, text=""):
    return Segment(start_time=start, end_time=end, speaker_label=label, text=text)


class _FakeProfile:
    def __init__(self, display_name, centroid):
        self.display_name = display_name
        self.centroid = centroid
        self.embeddings = [centroid]


class _FakeProfileDB:
    def __init__(self, profiles):
        self.profiles = profiles  # id -> _FakeProfile


def test_build_review_state_orders_by_speech_desc():
    segments = [
        _seg("SPEAKER_00", 0, 5, "hello"),
        _seg("SPEAKER_01", 5, 35, "a much longer turn"),
    ]
    mappings = {
        "SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00"),
        "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01"),
    }
    views = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=True)
    assert [v.label for v in views] == ["SPEAKER_01", "SPEAKER_00"]
    assert views[0].total_speech_seconds == 30.0
    assert views[0].seg_count == 1


def test_build_review_state_show_text_toggle():
    segments = [_seg("SPEAKER_00", 0, 5, "hello there")]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    with_text = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=True)
    no_text = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=False)
    assert with_text[0].sample_text == "hello there"
    assert no_text[0].sample_text is None
    # clip_start present regardless
    assert with_text[0].clip_start == 0.0
    assert no_text[0].clip_start == 0.0


def test_build_review_state_includes_soft_hints():
    vec = np.array([1.0, 0.0, 0.0])
    segments = [_seg("SPEAKER_00", 0, 10)]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    embeddings = {"SPEAKER_00": vec}
    db = _FakeProfileDB({"mayor-jones": _FakeProfile("Mayor Jones", vec)})
    views = review.build_review_state(segments, mappings, embeddings, db, show_text=False)
    assert views[0].soft_hints, "expected a voice hint for an identical embedding"
    assert views[0].soft_hints[0][0] == "Mayor Jones"
    assert views[0].soft_hints[0][1] == pytest.approx(1.0, abs=1e-6)


def test_build_review_state_needs_review_flag():
    segments = [_seg("SPEAKER_00", 0, 10)]
    m = SpeakerMapping(speaker_label="SPEAKER_00")
    m.needs_review = True
    views = review.build_review_state(segments, {"SPEAKER_00": m}, {}, _FakeProfileDB({}), show_text=False)
    assert views[0].needs_review is True
```

NOTE: confirm `Segment` and `SpeakerMapping` constructor kwargs against `src/models.py` before finalizing (use the fields the test uses: `Segment(start_time, end_time, speaker_label, text)`, `SpeakerMapping(speaker_label=...)` with attributes `speaker_name`, `confidence`, `id_method`, `needs_review`). If a kwarg differs, adjust the test helper, not the production code.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.review'`

- [ ] **Step 3: Implement `src/review.py` (this task's portion)**

Create `src/review.py`:

```python
"""Pure speaker-review operations shared by the CLI and (later) the GUI.

No prompts, no printing, no file writes — these functions transform in-memory
data (segments, mappings, embeddings) so they are directly unit-testable and
reusable. Persistence and interaction live in the callers (run_local.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class SpeakerView:
    label: str
    current_name: Optional[str]
    current_confidence: float
    current_method: Optional[str]
    seg_count: int
    total_speech_seconds: float
    clip_start: Optional[float]
    sample_text: Optional[str]
    soft_hints: list[tuple[str, float]] = field(default_factory=list)
    needs_review: bool = False


def _representative_segment(segs):
    """Pick a segment near the 1/3 point, preferring ones with text."""
    text_segs = [s for s in segs if getattr(s, "text", None) and s.text.strip()]
    pool = text_segs or segs
    if not pool:
        return None
    idx = max(0, len(pool) // 3 - 1)
    return pool[idx]


def build_review_state(segments, mappings, embeddings, profile_db, *, show_text: bool) -> list[SpeakerView]:
    """Build one SpeakerView per speaker label, sorted by speech time desc.

    soft_hints come from voice-profile soft matching when embeddings + profiles
    are available; otherwise empty.
    """
    # Per-label stats
    by_label: dict[str, list] = {}
    for seg in segments:
        by_label.setdefault(seg.speaker_label, []).append(seg)

    # Soft hints (best-effort; empty if no embeddings/profiles)
    hints: dict[str, list[tuple[str, float]]] = {}
    if embeddings and getattr(profile_db, "profiles", None):
        from src.enroll import get_stored_centroids
        from src.identify import soft_match_voice_profiles

        centroids = get_stored_centroids(profile_db)
        if centroids:
            display_names = {pid: p.display_name for pid, p in profile_db.profiles.items()}
            hints = soft_match_voice_profiles(embeddings, centroids, display_names)

    views: list[SpeakerView] = []
    for label, segs in by_label.items():
        total = sum(s.end_time - s.start_time for s in segs)
        rep = _representative_segment(segs)
        mapping = mappings.get(label)
        sample_text = None
        if show_text and rep is not None and getattr(rep, "text", None) and rep.text.strip():
            sample_text = rep.text
        views.append(SpeakerView(
            label=label,
            current_name=getattr(mapping, "speaker_name", None) if mapping else None,
            current_confidence=getattr(mapping, "confidence", 0.0) if mapping else 0.0,
            current_method=getattr(mapping, "id_method", None) if mapping else None,
            seg_count=len(segs),
            total_speech_seconds=total,
            clip_start=rep.start_time if rep is not None else None,
            sample_text=sample_text,
            soft_hints=hints.get(label, []),
            needs_review=getattr(mapping, "needs_review", False) if mapping else False,
        ))

    views.sort(key=lambda v: v.total_speech_seconds, reverse=True)
    return views
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_review.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/review.py tests/test_review.py
git commit -m "feat(review): add SpeakerView + build_review_state core"
```

---

## Task 2: `src/review.py` — rename_speaker

**Files:**
- Modify: `src/review.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review.py`:

```python
def test_rename_speaker_updates_mapping_and_segments():
    segments = [_seg("SPEAKER_00", 0, 5, "hi"), _seg("SPEAKER_00", 6, 9, "again"), _seg("SPEAKER_01", 9, 12)]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    res = review.rename_speaker(mappings, segments, "SPEAKER_00", "Mayor Jones")
    assert res.new_name == "Mayor Jones"
    assert res.old_name is None
    assert mappings["SPEAKER_00"].speaker_name == "Mayor Jones"
    assert mappings["SPEAKER_00"].confidence == 1.0
    assert mappings["SPEAKER_00"].id_method == "human_review"
    assert mappings["SPEAKER_00"].needs_review is False
    # segments for that label get the name; other label untouched
    assert [s.speaker_name for s in segments if s.speaker_label == "SPEAKER_00"] == ["Mayor Jones", "Mayor Jones"]


def test_rename_speaker_suggests_alias_when_correcting():
    segments = [_seg("SPEAKER_00", 0, 5)]
    m = SpeakerMapping(speaker_label="SPEAKER_00")
    m.speaker_name = "Misheard Name"
    mappings = {"SPEAKER_00": m}
    res = review.rename_speaker(mappings, segments, "SPEAKER_00", "Mayor Jones")
    assert res.old_name == "Misheard Name"
    assert res.alias_suggestion == "Misheard Name"


def test_rename_speaker_no_alias_when_no_prior_name():
    segments = [_seg("SPEAKER_00", 0, 5)]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    res = review.rename_speaker(mappings, segments, "SPEAKER_00", "Mayor Jones")
    assert res.alias_suggestion is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review.py -k rename -v`
Expected: FAIL — `AttributeError: module 'src.review' has no attribute 'rename_speaker'`

- [ ] **Step 3: Implement**

Append to `src/review.py`:

```python
@dataclass
class RenameResult:
    label: str
    old_name: Optional[str]
    new_name: str
    alias_suggestion: Optional[str]


def rename_speaker(mappings, segments, label: str, new_name: str, *, roster=None) -> RenameResult:
    """Assign new_name to a speaker label across its mapping and segments.

    If roster is given, the name is normalized via correct_speaker_name. Returns
    a RenameResult; alias_suggestion is the prior (wrong) name, to offer as an
    alias, or None when there was no prior name or it equals the new name.
    """
    from src.models import SpeakerMapping

    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    old_name = mapping.speaker_name

    final_name = new_name
    if roster is not None:
        from src.roster import correct_speaker_name
        final_name = correct_speaker_name(new_name, roster)

    mapping.speaker_name = final_name
    mapping.confidence = 1.0
    mapping.id_method = "human_review"
    mapping.needs_review = False
    mappings[label] = mapping

    for seg in segments:
        if seg.speaker_label == label:
            seg.speaker_name = final_name

    alias = old_name if (old_name and old_name != final_name) else None
    return RenameResult(label=label, old_name=old_name, new_name=final_name, alias_suggestion=alias)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_review.py -k rename -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/review.py tests/test_review.py
git commit -m "feat(review): add rename_speaker"
```

---

## Task 3: `src/review.py` — merge_speakers + speakers_needing_review

**Files:**
- Modify: `src/review.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review.py`:

```python
def test_merge_speakers_full_merge():
    segments = [
        _seg("SPEAKER_00", 0, 10, "a"),   # target, 10s, 1 seg
        _seg("SPEAKER_01", 10, 40, "b"),  # source, 30s, 1 seg
    ]
    target_vec = np.array([1.0, 0.0])
    source_vec = np.array([0.0, 1.0])
    embeddings = {"SPEAKER_00": target_vec.copy(), "SPEAKER_01": source_vec.copy()}
    m0 = SpeakerMapping(speaker_label="SPEAKER_00"); m0.speaker_name = "Mayor"
    m1 = SpeakerMapping(speaker_label="SPEAKER_01")
    mappings = {"SPEAKER_00": m0, "SPEAKER_01": m1}

    res = review.merge_speakers(segments, embeddings, mappings, "SPEAKER_01", "SPEAKER_00")

    # all segments now belong to target
    assert all(s.speaker_label == "SPEAKER_00" for s in segments)
    assert res.moved_segments == 1
    assert res.combined_name == "Mayor"
    # source removed
    assert "SPEAKER_01" not in embeddings
    assert "SPEAKER_01" not in mappings
    # centroid is seg-count-weighted: (10*target + 30*source)/40
    expected = (10 * target_vec + 30 * source_vec) / 40
    assert np.allclose(embeddings["SPEAKER_00"], expected)


def test_merge_adopts_source_name_when_target_unnamed():
    segments = [_seg("SPEAKER_00", 0, 10), _seg("SPEAKER_01", 10, 20)]
    embeddings = {"SPEAKER_00": np.array([1.0]), "SPEAKER_01": np.array([1.0])}
    m0 = SpeakerMapping(speaker_label="SPEAKER_00")  # unnamed target
    m1 = SpeakerMapping(speaker_label="SPEAKER_01"); m1.speaker_name = "Clerk Smith"; m1.confidence = 1.0
    mappings = {"SPEAKER_00": m0, "SPEAKER_01": m1}
    res = review.merge_speakers(segments, embeddings, mappings, "SPEAKER_01", "SPEAKER_00")
    assert res.combined_name == "Clerk Smith"
    assert mappings["SPEAKER_00"].speaker_name == "Clerk Smith"


def test_merge_rejects_same_label():
    segments = [_seg("SPEAKER_00", 0, 10)]
    with pytest.raises(ValueError):
        review.merge_speakers(segments, {}, {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}, "SPEAKER_00", "SPEAKER_00")


def test_merge_missing_embeddings_still_relabels():
    segments = [_seg("SPEAKER_00", 0, 10), _seg("SPEAKER_01", 10, 20)]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00"),
                "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01")}
    res = review.merge_speakers(segments, {}, mappings, "SPEAKER_01", "SPEAKER_00")
    assert all(s.speaker_label == "SPEAKER_00" for s in segments)
    assert res.moved_segments == 1
    assert "SPEAKER_01" not in mappings


def test_speakers_needing_review():
    a = SpeakerMapping(speaker_label="A"); a.needs_review = True
    b = SpeakerMapping(speaker_label="B"); b.needs_review = False
    assert review.speakers_needing_review({"A": a, "B": b}) == ["A"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review.py -k "merge or needing" -v`
Expected: FAIL — `AttributeError: module 'src.review' has no attribute 'merge_speakers'`

- [ ] **Step 3: Implement**

Append to `src/review.py`:

```python
@dataclass
class MergeResult:
    source_label: str
    target_label: str
    moved_segments: int
    combined_name: Optional[str]


def merge_speakers(segments, embeddings, mappings, source_label: str, target_label: str) -> MergeResult:
    """Full merge: fold source_label into target_label.

    - Relabels every source segment to the target.
    - Combines centroids weighted by each label's pre-merge speech time and
      recomputes the target centroid (if both embeddings present).
    - Drops the source from embeddings and mappings.
    - If the target has no name but the source does, the target adopts it.

    Raises ValueError if labels are equal or the source has no segments/mapping.
    """
    if source_label == target_label:
        raise ValueError("Cannot merge a speaker into itself.")
    if source_label not in mappings and not any(s.speaker_label == source_label for s in segments):
        raise ValueError(f"Unknown source speaker: {source_label}")

    # Pre-merge speech time per label (for centroid weighting)
    speech: dict[str, float] = {}
    for s in segments:
        speech[s.speaker_label] = speech.get(s.speaker_label, 0.0) + (s.end_time - s.start_time)

    # Relabel
    moved = 0
    for s in segments:
        if s.speaker_label == source_label:
            s.speaker_label = target_label
            moved += 1

    # Combine centroids (weighted by speech time)
    if source_label in embeddings and target_label in embeddings:
        w_src = speech.get(source_label, 0.0)
        w_tgt = speech.get(target_label, 0.0)
        total = w_src + w_tgt
        if total > 0:
            embeddings[target_label] = (
                w_tgt * np.asarray(embeddings[target_label]) + w_src * np.asarray(embeddings[source_label])
            ) / total
        else:
            embeddings[target_label] = np.mean(
                [np.asarray(embeddings[target_label]), np.asarray(embeddings[source_label])], axis=0
            )
    embeddings.pop(source_label, None)

    # Mapping: target keeps its name unless empty, then adopt source's
    src_map = mappings.pop(source_label, None)
    tgt_map = mappings.get(target_label)
    if tgt_map is not None and not getattr(tgt_map, "speaker_name", None) and src_map is not None and getattr(src_map, "speaker_name", None):
        tgt_map.speaker_name = src_map.speaker_name
        tgt_map.confidence = max(getattr(tgt_map, "confidence", 0.0), getattr(src_map, "confidence", 0.0))
        tgt_map.id_method = src_map.id_method
        tgt_map.needs_review = False

    combined_name = getattr(tgt_map, "speaker_name", None) if tgt_map is not None else None
    return MergeResult(source_label=source_label, target_label=target_label, moved_segments=moved, combined_name=combined_name)


def speakers_needing_review(mappings) -> list[str]:
    """Labels whose mapping is flagged needs_review."""
    return [label for label, m in mappings.items() if getattr(m, "needs_review", False)]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_review.py -v`
Expected: PASS (all review tests green)

- [ ] **Step 5: Commit**

```bash
git add src/review.py tests/test_review.py
git commit -m "feat(review): add merge_speakers + speakers_needing_review"
```

---

## Task 4: Body-aware `add_alias`

**Files:**
- Modify: `src/roster.py` (`add_alias`, currently at `src/roster.py:245-302`)
- Test: `tests/test_roster_chooser.py` (append; reuses `tmp_config_dir`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_roster_chooser.py`:

```python
def test_add_alias_legacy_targets_council_roster(tmp_config_dir):
    from src import roster
    (tmp_config_dir / "council_roster.json").write_text(json.dumps({
        "city": "Bloomington", "body": "City Council",
        "members": [{"name": "Council President Asare", "aliases": []}],
    }), encoding="utf-8")
    added = roster.add_alias(None, "Council President Asare", "Sasseberg")
    assert added is True
    data = json.loads((tmp_config_dir / "council_roster.json").read_text())
    assert "Sasseberg" in data["members"][0]["aliases"]


def test_add_alias_body_slug_targets_per_body_cache(tmp_config_dir):
    from src import roster
    rosters = tmp_config_dir / "rosters"
    rosters.mkdir(parents=True, exist_ok=True)
    # per-body cache schema: politicians[] with full_name/title/aliases
    (rosters / "bloomington-common-council.json").write_text(json.dumps({
        "body_slug": "bloomington-common-council",
        "body_key": "Bloomington Common Council",
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "politicians": [
            {"full_name": "Isak Asare", "title": "Council President", "aliases": []},
        ],
    }), encoding="utf-8")
    # canonical name for the slug path is "{title} {last_name}" => "Council President Asare"
    added = roster.add_alias(None, "Council President Asare", "Sasseberg",
                             body_slug="bloomington-common-council")
    assert added is True
    data = json.loads((rosters / "bloomington-common-council.json").read_text())
    assert "Sasseberg" in data["politicians"][0]["aliases"]
    # legacy file must NOT be created/changed
    assert not (tmp_config_dir / "council_roster.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_roster_chooser.py -k add_alias -v`
Expected: FAIL — `test_add_alias_body_slug_targets_per_body_cache` fails (body_slug kwarg unsupported / writes nothing).

- [ ] **Step 3: Implement**

In `src/roster.py`, replace the `add_alias` signature and body so it handles both schemas. The new function:

```python
def add_alias(
    roster_path: Optional[Path],
    canonical_name: str,
    new_alias: str,
    *,
    body_slug: Optional[str] = None,
) -> bool:
    """Add a new alias to a roster member's alias list.

    Two targets:
    - body_slug given → the per-body cache at CONFIG_DIR/rosters/{body_slug}.json,
      whose members live under "politicians" with a derived canonical name
      "{title} {last_name}" (matching load_roster's slug path).
    - else → the legacy council_roster.json (or roster_path), "members" schema.

    Returns True if an alias was added, False otherwise.
    """
    # Guard: reject nonsense aliases (shared by both schemas)
    if not new_alias or len(new_alias.strip()) < 3:
        return False
    alias_stripped = new_alias.strip()
    _SKIP = {"speaker", "unknown", "unidentified", "none", "n/a"}
    if alias_stripped.lower() in _SKIP:
        return False
    if alias_stripped.startswith("SPEAKER_"):
        return False

    if body_slug:
        cache_path = config.CONFIG_DIR / "rosters" / f"{body_slug}.json"
        if not cache_path.exists():
            return False
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for pol in data.get("politicians", []):
            full_name = pol.get("full_name", "")
            title = pol.get("title", "")
            last_name = full_name.split()[-1] if full_name else ""
            derived = f"{title} {last_name}".strip() if (title and last_name) else full_name
            if derived.lower() != canonical_name.lower():
                continue
            existing = [a.lower() for a in pol.get("aliases", [])]
            if alias_stripped.lower() in existing or alias_stripped.lower() == derived.lower():
                return False
            pol.setdefault("aliases", []).append(alias_stripped)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        return False

    # Legacy path (unchanged behavior)
    if roster_path is None:
        roster_path = config.CONFIG_DIR / "council_roster.json"
    if not roster_path.exists():
        return False
    with open(roster_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for member in data.get("members", []):
        if member["name"].lower() != canonical_name.lower():
            continue
        existing = [a.lower() for a in member.get("aliases", [])]
        if alias_stripped.lower() in existing:
            return False
        if alias_stripped.lower() == member["name"].lower():
            return False
        if "aliases" not in member:
            member["aliases"] = []
        member["aliases"].append(alias_stripped)
        with open(roster_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    return False
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_roster_chooser.py -k add_alias -v`
Expected: PASS (2 passed). Also run the full roster/body suites:
`.venv/bin/python -m pytest tests/test_roster_chooser.py tests/test_body_tagging.py tests/test_roster_load.py -q` → expect all pass (existing `add_alias` callers pass `body_slug=None` implicitly).

- [ ] **Step 5: Commit**

```bash
git add src/roster.py tests/test_roster_chooser.py
git commit -m "feat(roster): make add_alias body-aware (per-body cache target)"
```

---

## Task 5: yt-dlp downloads capped video (so clips work)

**Files:**
- Modify: `src/download.py` (`download_via_ytdlp`, `src/download.py:33-86`)
- Test: `tests/test_download_format.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_download_format.py`:

```python
"""yt-dlp now requests a capped-resolution VIDEO stream so review clips exist."""
from __future__ import annotations

from src import download


def test_ytdlp_format_requests_video():
    fmt = download._ytdlp_format()
    # must contain a video selector, not be audio-only
    assert "bestvideo" in fmt
    assert "height<=480" in fmt
    assert fmt != "bestaudio/best"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_download_format.py -v`
Expected: FAIL — `AttributeError: module 'src.download' has no attribute '_ytdlp_format'`

- [ ] **Step 3: Implement**

In `src/download.py`, add a module-level helper above `download_via_ytdlp`:

```python
def _ytdlp_format() -> str:
    """yt-dlp format string: a capped (~480p) video+audio stream.

    Capped resolution keeps downloads modest — clips only need to show a face —
    while still producing a playable source video for the review step. Falls back
    to best available if the capped combo is unavailable.
    """
    return "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
```

Then in `download_via_ytdlp`, change the `ydl_opts` format line from:

```python
        "format": "bestaudio/best",
```

to:

```python
        "format": _ytdlp_format(),
```

Also update the function's docstring line "Downloads the best available audio stream." to "Downloads a capped-resolution video+audio stream (so the source video is available for review clips)."

- [ ] **Step 4: Run test**

Run: `.venv/bin/python -m pytest tests/test_download_format.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/download.py tests/test_download_format.py
git commit -m "fix(download): yt-dlp fetches capped video so review clips work"
```

---

## Task 6: `play_speaker_clip` — video-or-audio playback

**Files:**
- Modify: `run_local.py` (add `play_speaker_clip` after `play_video_clip` at `run_local.py:340-369`)
- Test: `tests/test_play_clip.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_play_clip.py`:

```python
"""play_speaker_clip uses video when present, else audio.wav (no afplay lie)."""
from __future__ import annotations

import run_local


def test_play_speaker_clip_uses_video(monkeypatch):
    captured = {}
    monkeypatch.setattr(run_local.subprocess, "run", lambda cmd, **kw: captured.setdefault("cmd", cmd))
    run_local.play_speaker_clip("/m/source.mp4", "/m/audio.wav", 30.0, duration=10.0, title="x")
    assert captured["cmd"][-1] == "/m/source.mp4"
    assert "-nodisp" not in captured["cmd"]  # video → display on


def test_play_speaker_clip_falls_back_to_audio(monkeypatch):
    captured = {}
    monkeypatch.setattr(run_local.subprocess, "run", lambda cmd, **kw: captured.setdefault("cmd", cmd))
    run_local.play_speaker_clip(None, "/m/audio.wav", 30.0, duration=10.0, title="x")
    assert captured["cmd"][-1] == "/m/audio.wav"
    assert "-nodisp" in captured["cmd"]  # audio-only → no display window


def test_play_speaker_clip_no_media(monkeypatch, capsys):
    called = {"ran": False}
    monkeypatch.setattr(run_local.subprocess, "run", lambda *a, **k: called.__setitem__("ran", True))
    run_local.play_speaker_clip(None, None, 30.0)
    assert called["ran"] is False
    assert "no media" in capsys.readouterr().out.lower()
```

NOTE: this test references `run_local.subprocess`. `play_speaker_clip` must `import subprocess` at module top of `run_local.py` (it is currently imported locally inside `play_video_clip`). Move/add a top-level `import subprocess` so the monkeypatch target exists. Confirm `import subprocess` is present at the top of `run_local.py`; add it to the import block (near `import sys`) if missing, and drop the local `import subprocess` inside `play_video_clip`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_play_clip.py -v`
Expected: FAIL — `AttributeError: module 'run_local' has no attribute 'play_speaker_clip'` (and/or `run_local.subprocess` missing).

- [ ] **Step 3: Implement**

In `run_local.py`: ensure `import subprocess` is at module top (add near the other stdlib imports), and remove the `import subprocess` line inside `play_video_clip`.

Add this function immediately after `play_video_clip`:

```python
def play_speaker_clip(
    video_path: str | None,
    audio_path: str | None,
    start_time: float,
    duration: float = 20.0,
    title: str = "",
) -> None:
    """Play a clip of a speaker: video if available, else the audio segment.

    Uses ffplay. Starts a few seconds early for context. When only audio is
    available, plays audio.wav with no display window (-nodisp).
    """
    media = video_path or audio_path
    if not media:
        print("    No media to play (no video or audio found).")
        return

    seek = max(0, start_time - 3.0)
    cmd = ["ffplay", "-ss", str(seek), "-t", str(duration), "-autoexit", "-loglevel", "quiet"]
    if not video_path:
        cmd.append("-nodisp")
    if title:
        cmd += ["-window_title", title]
    cmd.append(media)

    kind = "video" if video_path else "audio"
    print(f"    Playing {kind} clip ({duration:.0f}s from {int(seek // 60):02d}:{int(seek % 60):02d})...")
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print("    ffplay not found — install ffmpeg to enable clip playback")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_play_clip.py -v`
Expected: PASS (3 passed). Also `.venv/bin/python -c "import run_local"` → clean.

- [ ] **Step 5: Commit**

```bash
git add run_local.py tests/test_play_clip.py
git commit -m "feat(run_local): play_speaker_clip with audio fallback"
```

---

## Task 7: Refactor `_interactive_speaker_review` onto the review core + `[M]erge`

**Files:**
- Modify: `run_local.py` (`_interactive_speaker_review`, currently `run_local.py:1498-1623`)

This task changes the function's signature and body. The 3 callers are updated in Task 8 — after this task `import run_local` must still succeed, but the callers will be temporarily passing the OLD arguments. To keep the tree importable and avoid a broken intermediate, **do Task 7 and Task 8 back-to-back and commit them together** (this task's commit includes the Task 8 caller updates). For clarity they are split in the plan; the implementer may combine the final commit.

- [ ] **Step 1: Replace the function**

Replace the entire `_interactive_speaker_review` function (from its `def` line through its final `return changes`) with:

```python
def _interactive_speaker_review(
    segments,
    mappings: dict,
    embeddings: dict,
    profile_db,
    video_path: str | None,
    audio_path: str | None,
    *,
    roster=None,
    body_slug: str | None = None,
    show_text: bool = True,
) -> list[dict]:
    """Interactive review loop built on the pure src/review.py core.

    Lets the operator, per speaker: play a clip ([V]), accept the top voice hint
    ([Y]), merge this speaker into another ([M]), skip ([Enter]), quit ([Q]), or
    type a name. Mutates segments/mappings/embeddings in place via review.py;
    the CALLER persists (diarization.json / embeddings.json / transcript).

    Returns a list of change dicts:
      {"label", "old_name", "new_name"} for renames, and
      {"label", "merged_into"} for merges.
    """
    from src import review

    if not sys.stdin.isatty():
        print("(non-interactive mode — cannot review)")
        return []

    changes: list[dict] = []
    views = review.build_review_state(segments, mappings, embeddings, profile_db, show_text=show_text)

    i = 0
    quit_requested = False
    while i < len(views):
        view = views[i]
        label = view.label
        name = view.current_name or "(unidentified)"
        mins = view.total_speech_seconds / 60

        print(f"\n[{i+1}/{len(views)}] {label}: {name}")
        print(f"  Segments: {view.seg_count}, Speech: {mins:.1f}m", end="")
        if view.current_confidence > 0:
            print(f", Confidence: {view.current_confidence:.2f}, Method: {view.current_method or 'none'}", end="")
        print()

        top_hint = None
        if view.soft_hints and not (view.current_name and view.current_confidence >= 0.85):
            for hint_name, hint_score in view.soft_hints[:3]:
                marker = "*" if hint_score >= 0.85 else "?"
                print(f"  {marker} Voice match: {hint_name} ({hint_score:.2f})")
            top_hint = view.soft_hints[0]

        if view.sample_text:
            preview = view.sample_text[:120] + "..." if len(view.sample_text) > 120 else view.sample_text
            print(f"  Sample [{_format_ts(view.clip_start or 0)}]: \"{preview}\"")
        elif view.clip_start is not None:
            print(f"  Clip at [{_format_ts(view.clip_start)}]")

        advance = True
        while True:
            parts = ["  "]
            if (video_path or audio_path) and view.clip_start is not None:
                parts.append("[V]iew")
            if top_hint:
                parts.append(f"[Y=accept {top_hint[0]}]")
            if len(views) > 1:
                parts.append("[M]erge")
            parts.append("[Enter=skip] [Q=quit] or type name: ")
            choice = input(" ".join(parts)).strip()

            if choice.lower() in ("v", "view") and (video_path or audio_path) and view.clip_start is not None:
                play_speaker_clip(video_path, audio_path, view.clip_start, duration=20.0,
                                  title=f"{label} → {name}")
                continue
            elif choice.lower() == "q":
                print("  Quitting review.")
                quit_requested = True
                break
            elif choice == "":
                break  # skip
            elif choice.lower() == "m" and len(views) > 1:
                others = [v for v in views if v.label != label]
                print("  Merge THIS speaker into which?")
                for k, ov in enumerate(others):
                    print(f"    {k+1}. {ov.label}: {ov.current_name or '(unidentified)'}")
                sel = input("    Number (or Enter to cancel): ").strip()
                if not sel:
                    continue
                try:
                    target = others[int(sel) - 1]
                except (ValueError, IndexError):
                    print("    Invalid selection.")
                    continue
                try:
                    res = review.merge_speakers(segments, embeddings, mappings, label, target.label)
                except ValueError as e:
                    print(f"    {e}")
                    continue
                changes.append({"label": label, "merged_into": target.label})
                print(f"  Merged {label} → {target.label} ({res.combined_name or 'unidentified'})")
                # rebuild views; current slot now holds the next speaker → don't advance
                views = review.build_review_state(segments, mappings, embeddings, profile_db, show_text=show_text)
                advance = False
                break
            elif choice.lower() in ("y", "yes") and top_hint:
                res = review.rename_speaker(mappings, segments, label, top_hint[0], roster=roster)
                mappings[label].id_method = "human_confirmed"
                changes.append({"label": label, "old_name": res.old_name, "new_name": res.new_name})
                print(f"  Confirmed: {label} -> {res.new_name}")
                break
            else:
                res = review.rename_speaker(mappings, segments, label, choice, roster=roster)
                changes.append({"label": label, "old_name": res.old_name, "new_name": res.new_name})
                print(f"  Updated: {label} -> {res.new_name}")
                if res.alias_suggestion:
                    from src.roster import add_alias
                    if add_alias(None, res.new_name, res.alias_suggestion, body_slug=body_slug):
                        target = body_slug or "council_roster.json"
                        print(f"  Auto-added alias: '{res.alias_suggestion}' -> '{res.new_name}' ({target})")
                break

        if quit_requested:
            break
        if advance:
            i += 1

    return changes
```

- [ ] **Step 2: Verify it parses (callers still old — full import check happens after Task 8)**

Run: `.venv/bin/python -c "import ast; ast.parse(open('run_local.py').read()); print('parse OK')"`
Expected: `parse OK`. (Runtime import is verified at the end of Task 8 once callers match.)

- [ ] **Step 3: Proceed directly to Task 8 (do not commit yet).**

---

## Task 8: Update the 3 callers + persist merges

**Files:**
- Modify: `run_local.py` — `_review_meeting`, `_identify_speakers_standalone`, and the `--pre-identify` block in `run_pipeline`.

Each caller currently builds `speaker_stats` + `soft_matches` and calls `_interactive_speaker_review(sorted_labels, speaker_stats, current_mappings, video_path, soft_matches=..., show_text=...)`. They must now load `embeddings` + `profile_db`, pass the new arguments, and persist any merges (write updated `diarization.json` + `embeddings.json`).

Add this small persistence helper near the other helpers in `run_local.py` (e.g. just after `_load_soft_matches`):

```python
def _persist_after_review(meeting_dir: Path, segments, embeddings, changes) -> None:
    """If the review performed any merges, rewrite diarization.json + embeddings.json."""
    if not any("merged_into" in c for c in changes):
        return
    diar_path = meeting_dir / "diarization.json"
    emb_path = meeting_dir / "embeddings.json"
    with open(diar_path, "w") as f:
        json.dump([s.to_dict() for s in segments], f, indent=2)
    emb_out = {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in embeddings.items()}
    with open(emb_path, "w") as f:
        json.dump(emb_out, f)
```

### 8a — `_identify_speakers_standalone` (currently `run_local.py:1804-...`)

Find where it builds stats/soft_matches and calls the loop:

```python
    speaker_stats = _build_speaker_stats(segments)
    soft_matches = _load_soft_matches(embeddings_path)

    sorted_labels = sorted(
        speaker_stats.keys(),
        key=lambda l: speaker_stats[l]["total_speech"],
        reverse=True,
    )
```
...and later...
```python
    changes = _interactive_speaker_review(
        sorted_labels, speaker_stats, current_mappings,
        video_path, soft_matches=soft_matches, show_text=has_text,
    )
```

Replace the stats/soft_matches/sorted_labels block with loading embeddings + profile_db (keep the overview-table code that uses `speaker_stats`/`soft_matches` working — see note):

```python
    import numpy as np
    from src.enroll import load_profiles

    speaker_stats = _build_speaker_stats(segments)
    soft_matches = _load_soft_matches(embeddings_path)
    sorted_labels = sorted(
        speaker_stats.keys(),
        key=lambda l: speaker_stats[l]["total_speech"],
        reverse=True,
    )

    if embeddings_path.exists():
        with open(embeddings_path, "r") as f:
            _emb = json.load(f)
        embeddings = {k: np.array(v) for k, v in _emb.items()}
    else:
        embeddings = {}
    profile_db = load_profiles()
    body_slug = _meeting_body_slug(meeting_dir)
```

And replace the loop call with:

```python
    changes = _interactive_speaker_review(
        segments, current_mappings, embeddings, profile_db,
        video_path, str(meeting_dir / "audio.wav"),
        body_slug=body_slug, show_text=has_text,
    )
    _persist_after_review(meeting_dir, segments, embeddings, changes)
```

NOTE: `_build_speaker_stats`/`_load_soft_matches`/`sorted_labels` are kept ONLY because the existing overview table printed before the loop uses them. Leave that table code as-is.

### 8b — `_review_meeting` (currently `run_local.py:1700-...`)

It has the same pattern. Add embeddings + profile_db + body_slug loading next to its existing `speaker_stats`/`soft_matches`:

```python
    import numpy as np
    from src.enroll import load_profiles

    if embeddings_path.exists():
        with open(embeddings_path, "r") as f:
            _emb = json.load(f)
        embeddings = {k: np.array(v) for k, v in _emb.items()}
    else:
        embeddings = {}
    profile_db = load_profiles()
    body_slug = _meeting_body_slug(meeting_dir)
```

Replace its loop call:

```python
    changes = _interactive_speaker_review(
        meeting.segments, meeting.speakers, embeddings, profile_db,
        video_path, str(meeting_dir / "audio.wav"),
        body_slug=body_slug, show_text=True,
    )
    _persist_after_review(meeting_dir, meeting.segments, embeddings, changes)
```

(The existing "apply corrections to segments and save / export" block below stays; it already rewrites `transcript_named.json` and re-exports.)

### 8c — `--pre-identify` block in `run_pipeline` (currently `run_local.py:694-...`)

It builds `speaker_stats`, `soft_matches`, `sorted_labels`, a `temp_mappings`, and calls the loop. Replace its loop call:

```python
        changes = _interactive_speaker_review(
            sorted_labels, speaker_stats, temp_mappings,
            video_path, soft_matches=soft_matches, show_text=False,
        )
```

with (embeddings already loaded earlier in run_pipeline as `embeddings_path`; load profile_db + body_slug):

```python
        import numpy as np
        from src.enroll import load_profiles as _load_profiles
        if embeddings_path.exists():
            with open(embeddings_path, "r") as f:
                _emb = json.load(f)
            _pre_embeddings = {k: np.array(v) for k, v in _emb.items()}
        else:
            _pre_embeddings = {}
        changes = _interactive_speaker_review(
            segments, temp_mappings, _pre_embeddings, _load_profiles(),
            video_path, str(meeting_dir / "audio.wav"),
            body_slug=effective_body_slug, show_text=False,
        )
        _persist_after_review(meeting_dir, segments, _pre_embeddings, changes)
```

### 8d — add the `_meeting_body_slug` helper

Add near `_persist_after_review`:

```python
def _meeting_body_slug(meeting_dir: Path) -> str | None:
    """Read the persisted body_slug from a meeting's pipeline_state.json, if any."""
    state_file = meeting_dir / "pipeline_state.json"
    if not state_file.exists():
        return None
    try:
        with open(state_file, "r") as f:
            return json.load(f).get("body_slug")
    except Exception:
        return None
```

- [ ] **Step 1: Apply 8a–8d.**

- [ ] **Step 2: Verify import + existing suite**

Run: `.venv/bin/python -c "import run_local" && .venv/bin/python -m pytest -q`
Expected: clean import; full suite passes (the interactive loop isn't unit-tested directly — its logic lives in `src/review.py`, already covered).

- [ ] **Step 3: Manual smoke (optional but recommended)**

If a processed meeting exists locally:
`.venv/bin/python run_local.py --review <SOME_MEETING_ID>` → confirm the table, a `[V]iew` clip, typing a name, and `[M]erge` all work and the transcript re-exports.

- [ ] **Step 4: Commit (Tasks 7 + 8 together)**

```bash
git add run_local.py
git commit -m "feat(run_local): route review loop through src/review.py + add [M]erge"
```

---

## Task 9: Auto-review after interactive runs + `--no-review` + canonical `--review`

**Files:**
- Modify: `run_local.py` — Stage 4 review call (around `run_local.py:943`), argparse, and command dispatch.

- [ ] **Step 1: Replace the Stage 4 `human_review` call**

Find in `run_pipeline` Stage 4:

```python
        # Human review
        mappings = human_review(mappings)

        # Apply to segments
        segments = apply_mappings_to_segments(segments, mappings)
```

Replace with:

```python
        # Human review — rich interactive review on a terminal (clips, hints,
        # merge); fall back to the text-only quick review otherwise or when
        # --no-review is set.
        if sys.stdin.isatty() and not getattr(args, "no_review", False):
            review_video = find_video_file(meeting_dir, meeting.audio_source)
            review_changes = _interactive_speaker_review(
                segments, mappings, speaker_embeddings, profile_db,
                review_video, str(wav_path),
                roster=roster, body_slug=effective_body_slug, show_text=True,
            )
            _persist_after_review(meeting_dir, segments, speaker_embeddings, review_changes)
        else:
            mappings = human_review(mappings)

        # Apply to segments
        segments = apply_mappings_to_segments(segments, mappings)
```

NOTE: confirm these names are in scope at that point in `run_pipeline`: `speaker_embeddings` (loaded earlier in Stage 4), `profile_db` (loaded in Stage 4), `roster`, `effective_body_slug`, `wav_path`, `meeting.audio_source`. They are all defined earlier in the function per the current code; if `profile_db`/`speaker_embeddings` are scoped only inside a branch, hoist the load so they're available here. Do not change their load logic otherwise.

- [ ] **Step 2: Add `--no-review` and canonical `--review`**

In `main()`'s argparse block, add:

```python
    parser.add_argument("--no-review", action="store_true",
                        help="Skip the interactive speaker review at the end of a run")
    parser.add_argument("--review", metavar="MEETING_ID",
                        help="Review/correct/merge speakers in an existing meeting "
                             "(canonical; --review-meeting and --identify-speakers are aliases)")
```

In the command-dispatch section (where `--review-meeting` and `--identify-speakers` are handled), route the canonical flag. Find:

```python
    if args.review_meeting:
        _review_meeting(args.review_meeting)
        return

    if args.identify_speakers:
        _identify_speakers_standalone(args.identify_speakers)
        return
```

Replace with:

```python
    if args.review:
        # Canonical review: prefer the full post-transcription review when a
        # named transcript exists, else fall back to diarization-only ID.
        from src import config as _config
        if (_config.MEETINGS_DIR / args.review / "transcript_named.json").exists():
            _review_meeting(args.review)
        else:
            _identify_speakers_standalone(args.review)
        return

    if args.review_meeting:
        _review_meeting(args.review_meeting)
        return

    if args.identify_speakers:
        _identify_speakers_standalone(args.identify_speakers)
        return
```

Update the `--review-meeting` / `--identify-speakers` help strings to note they are aliases of `--review`.

- [ ] **Step 3: Verify**

Run: `.venv/bin/python -c "import run_local"` → clean.
Run: `.venv/bin/python run_local.py --help` → confirm `--review`, `--no-review` appear.
Run: `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 4: Commit**

```bash
git add run_local.py
git commit -m "feat(run_local): auto-review after interactive runs; --review/--no-review"
```

---

## Task 10: README + final verification

**Files:**
- Modify: `README.md` (the speaker-identification / roster section)

- [ ] **Step 1: Add documentation**

In `README.md`, after the "Choosing a roster (local CLI)" subsection (added previously), insert:

```markdown
### Reviewing & naming speakers

After an interactive run finishes, CouncilScribe drops into a guided speaker
review (skip with `--no-review`). For each detected speaker it shows stats and
any voice-profile match hints, and lets you:

- **`[V]iew`** — play a ~20s clip of that speaker (video if available, otherwise
  audio),
- **`[Y]`** — accept the suggested voice-profile match,
- **`[M]erge`** — merge this speaker into another (when diarization split one
  person into two: their segments and voice data combine),
- type a **name**, or **`[Enter]`** to skip / **`[Q]`** to quit.

Naming a speaker enrolls their voice so future meetings auto-match them. To
re-review a finished meeting later: `python run_local.py --review <MEETING_ID>`
(the old `--review-meeting` / `--identify-speakers` still work as aliases).

YouTube/Facebook meetings now download a capped-resolution video so clips are
available during review (CATS TV, direct URLs, and local files always had them).
```

- [ ] **Step 2: Final full verification**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (new `tests/test_review.py`, `tests/test_download_format.py`, `tests/test_play_clip.py`, appended `add_alias` tests, plus the full existing suite).

Run: `.venv/bin/python -c "import run_local"` → clean.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the unified speaker review flow"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** `src/review.py` core = Tasks 1–3 (D1). Full merge = Task 3 + `[M]erge` in Task 7 (D2). Auto-drop into rich review + `--no-review` = Task 9 (D3). yt-dlp video = Task 5 (D4). Body-aware alias = Task 4. Audio fallback = Task 6. Consolidated `--review` = Task 9. README = Task 10.
- **Type consistency:** `build_review_state(segments, mappings, embeddings, profile_db, *, show_text)`, `rename_speaker(mappings, segments, label, new_name, *, roster)`, `merge_speakers(segments, embeddings, mappings, source_label, target_label)` are referenced identically in Tasks 7–9. `add_alias(..., *, body_slug=None)` consistent across Tasks 4 and 7. `play_speaker_clip(video_path, audio_path, start_time, duration, title)` consistent Tasks 6–7.
- **Pre-flight checks the implementer MUST do** (cheap reads, not assumptions): `src/models.py` `Segment`/`SpeakerMapping` field names; that `import subprocess` can be hoisted in `run_local.py`; that `profile_db`/`speaker_embeddings` are in scope at the Stage 4 review call (hoist their load if not); that `config.MEETINGS_DIR` is importable in `main()`.
- **Intermediate-state caveat:** Task 7 changes the loop signature; the tree is only runtime-importable again after Task 8. They commit together (Task 8 Step 4). Task 7 only verifies with `ast.parse`.
- **Out of scope:** the web GUI (sub-project 2), hosting/auth, diarization changes.
```
