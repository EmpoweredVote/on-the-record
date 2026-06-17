# Meeting Confidence Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a meeting-level confidence gate that scores each meeting by speech-time-weighted trusted-method coverage, then routes it to auto-publish / needs-review / failed — so confident meetings publish unattended and uncertain ones land in a review queue instead of silently going live.

**Architecture:** A pure scoring module (`src/quality.py`) classifies each speaker's `id_method` into trust tiers and computes per-event-kind coverage. `run_local.py` computes the verdict right after Stage 4 (identification), writes a `quality.json` artifact, mirrors the headline into `pipeline_state.json`, skips the paid summary + enrollment for non-passing non-interactive runs, and guards every publish path behind the verdict (with a `--publish-anyway` human override). A non-destructive calibration harness (`bench/calibrate_gate.py`) re-derives automated attributions for already-reviewed meetings and reports `trusted_coverage` and `trusted_precision` per kind so thresholds can be set against ground truth.

**Tech Stack:** Python 3 / stdlib + numpy (already a dependency), pytest. No new third-party packages. Reuses `src/identify.py`, `src/roster.py`, `src/enroll.py`, `src/models.py`.

---

## Scope

**In scope (item "A"):** scoring module, `quality.json` + state fields, post-Stage-4 verdict computation, summary/enrollment skip for non-passing non-interactive runs, publish guard + `--publish-anyway`, `--review-queue` lister, per-kind config thresholds, calibration harness.

**Explicitly out of scope:** making `_run_batch` run summaries or auto-publish (that is item "C"); replacing the local Qwen Layer-3 LLM with an Anthropic call (item "B"); publishing needs-review meetings as a hidden DB status (deferred until the ev-accounts `/api/meetings` query enforces a `status='published'` default — otherwise hidden meetings leak onto the public site).

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `src/config.py` | modify | Add `GATE_THRESHOLDS`, `GATE_PROBABLE_DISCOUNT`, `GATE_SPEECH_FLOOR_SECONDS` |
| `src/quality.py` | create | Pure scoring: tier classification, coverage, verdict, identity-key, report dict |
| `tests/test_quality.py` | create | Unit tests for tier classification + scoring + verdict |
| `src/checkpoint.py` | modify | Add `review_status` + `trusted_coverage` to `PipelineState` |
| `tests/test_quality_state.py` | create | Round-trip tests for the two new state fields |
| `run_local.py` | modify | Compute verdict after Stage 4, write `quality.json`, early-return guard, publish guards, `--publish-anyway`, `--review-queue` |
| `tests/test_gate_pipeline.py` | create | Tests for verdict computation + early-return + publish guard helper |
| `tests/test_review_queue.py` | create | Tests for the queue lister |
| `bench/calibrate_gate.py` | create | Non-destructive calibration harness (library-direct) |
| `tests/test_calibrate_gate.py` | create | Tests for precision computation + non-destructiveness |

---

## Trust-tier reference (used throughout)

Derived from the actual `id_method` strings emitted by the codebase:

- **trusted:** `human_review`, `human_confirmed`, `voice_profile` (exact), and the strong patterns `roll_call`, `self_identification`, `chair_recognition`.
- **probable:** `voice_profile (returning_2)` / `voice_profile (returning_3+)` — voice matches accepted at the *lowered* returning-speaker threshold (these carry `returning_` in the method string; see `src/identify.py:62-66`).
- **unverified:** `llm`, `name_addressing`, `title_context`.
- **unknown:** no mapping, no `speaker_name`, or any unrecognized method.

---

## Task 1: Config thresholds

**Files:**
- Modify: `src/config.py` (append after line 68, the roster surname block)

- [ ] **Step 1: Add the gate constants**

Append to `src/config.py`:

```python
# --- Meeting confidence gate (Phase A) ---
# Probable-tier coverage (returning-speaker voice matches at the lowered
# threshold) counts toward the verdict at this discount vs. trusted coverage.
GATE_PROBABLE_DISCOUNT = 0.5

# Speakers whose total speech-time is below this are treated as incidental
# (e.g. public commenters) and excluded from the coverage denominator, UNLESS
# excluding them would leave no eligible speakers (then all are kept).
GATE_SPEECH_FLOOR_SECONDS = 60.0

# Per-event-kind verdict thresholds on the (discounted) effective coverage.
# verdict: effective >= high -> pass; high > effective >= low -> review;
#          effective < low -> failed.
# SEED VALUES — provisional and conservative; recalibrate with
# bench/calibrate_gate.py once one meeting of each kind has been reviewed.
GATE_THRESHOLDS = {
    "default":          {"high": 0.90, "low": 0.50},
    "council":          {"high": 0.90, "low": 0.50},
    "school_board":     {"high": 0.90, "low": 0.50},
    "debate":           {"high": 0.95, "low": 0.60},
    "forum":            {"high": 0.90, "low": 0.55},
    "community_meeting":{"high": 0.70, "low": 0.40},
    "news_clip":        {"high": 0.90, "low": 0.50},
    "press_conference": {"high": 0.90, "low": 0.50},
    "other":            {"high": 0.90, "low": 0.50},
}
```

- [ ] **Step 2: Verify it imports**

Run: `.venv/bin/python -c "from src import config; print(config.GATE_THRESHOLDS['council'], config.GATE_SPEECH_FLOOR_SECONDS, config.GATE_PROBABLE_DISCOUNT)"`
Expected: `{'high': 0.9, 'low': 0.5} 60.0 0.5`

- [ ] **Step 3: Commit**

```bash
git add src/config.py
git commit -m "feat(gate): add per-event-kind confidence thresholds to config"
```

---

## Task 2: Scoring module `src/quality.py`

**Files:**
- Create: `src/quality.py`
- Test: `tests/test_quality.py`

### What it does

Pure functions over a `Meeting` (no IO): classify each speaker's method into a tier, weight by per-label speech-time (excluding incidental sub-floor speakers from the denominator), compute coverage numbers, derive a per-kind verdict, and build the `quality.json` report dict. Also exposes `identity_key()` for link-first identity comparison (used by calibration).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quality.py`:

```python
"""Unit tests for the meeting confidence gate scoring (src/quality.py)."""
from __future__ import annotations

from src import quality
from src.models import Meeting, Segment, SpeakerMapping


def _seg(seg_id, label, start, end, name=None):
    return Segment(segment_id=seg_id, start_time=start, end_time=end,
                   speaker_label=label, text="x", speaker_name=name)


def _meeting(segments, speakers, event_kind="council"):
    return Meeting(meeting_id="m", city="C", date="2026-01-01",
                   event_kind=event_kind, segments=segments, speakers=speakers)


# --- tier classification ---

def test_classify_trusted_methods():
    for m in ("human_review", "human_confirmed", "voice_profile",
              "roll_call", "self_identification", "chair_recognition"):
        assert quality.classify_method(m) == quality.TIER_TRUSTED


def test_classify_returning_voice_is_probable():
    assert quality.classify_method("voice_profile (returning_2)") == quality.TIER_PROBABLE
    assert quality.classify_method("voice_profile (returning_3+)") == quality.TIER_PROBABLE


def test_classify_unverified_methods():
    for m in ("llm", "name_addressing", "title_context"):
        assert quality.classify_method(m) == quality.TIER_UNVERIFIED


def test_classify_unknown():
    assert quality.classify_method(None) == quality.TIER_UNKNOWN
    assert quality.classify_method("") == quality.TIER_UNKNOWN
    assert quality.classify_method("mystery") == quality.TIER_UNKNOWN


# --- coverage + verdict ---

def test_all_trusted_long_speakers_passes():
    segs = [_seg(0, "S0", 0, 600, "Mayor A"), _seg(1, "S1", 600, 1200, "Member B")]
    speakers = {
        "S0": SpeakerMapping("S0", "Mayor A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", "Member B", 0.95, "roll_call"),
    }
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    assert report["verdict"] == quality.VERDICT_PASS
    assert report["trusted_coverage"] == 1.0


def test_incidental_short_speaker_excluded_from_denominator():
    # Two long trusted council members + one 30s unknown public commenter.
    segs = [
        _seg(0, "S0", 0, 600, "Mayor A"),
        _seg(1, "S1", 600, 1200, "Member B"),
        _seg(2, "S2", 1200, 1230, None),   # 30s < 60s floor -> incidental
    ]
    speakers = {
        "S0": SpeakerMapping("S0", "Mayor A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", "Member B", 0.95, "roll_call"),
        "S2": SpeakerMapping("S2", None, 0.0, None),
    }
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    # The unknown 30s speaker is excluded, so coverage stays 1.0 and it passes.
    assert report["trusted_coverage"] == 1.0
    assert report["verdict"] == quality.VERDICT_PASS


def test_long_unknown_speaker_routes_to_review():
    # A long (10m) unidentified principal speaker tanks coverage to ~0.5.
    segs = [_seg(0, "S0", 0, 600, "Mayor A"), _seg(1, "S1", 600, 1200, None)]
    speakers = {
        "S0": SpeakerMapping("S0", "Mayor A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", None, 0.0, None),
    }
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    assert report["verdict"] == quality.VERDICT_REVIEW
    assert abs(report["trusted_coverage"] - 0.5) < 1e-6


def test_probable_counts_at_discount():
    # 50% trusted + 50% probable -> effective = 0.5 + 0.5*0.5 = 0.75 -> review (council high=0.90).
    segs = [_seg(0, "S0", 0, 600, "A"), _seg(1, "S1", 600, 1200, "B")]
    speakers = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", "B", 0.80, "voice_profile (returning_3+)"),
    }
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    assert abs(report["effective_coverage"] - 0.75) < 1e-6
    assert report["verdict"] == quality.VERDICT_REVIEW


def test_below_low_is_failed():
    segs = [_seg(0, "S0", 0, 1200, None)]
    speakers = {"S0": SpeakerMapping("S0", None, 0.0, None)}
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    assert report["verdict"] == quality.VERDICT_FAILED


def test_no_speech_is_failed():
    report = quality.evaluate_meeting(_meeting([], {}))
    assert report["verdict"] == quality.VERDICT_FAILED
    assert report["total_speech_seconds"] == 0.0


def test_event_kind_threshold_applied():
    # Debate requires high=0.95; 0.90 trusted coverage -> review for debate.
    segs = [_seg(0, "S0", 0, 900, "A"), _seg(1, "S1", 900, 1000, None)]
    speakers = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", None, 0.0, None),
    }
    # S1 is 100s (>= 60s floor) so it counts; coverage = 900/1000 = 0.9.
    report = quality.evaluate_meeting(_meeting(segs, speakers, event_kind="debate"))
    assert abs(report["trusted_coverage"] - 0.9) < 1e-6
    assert report["verdict"] == quality.VERDICT_REVIEW


# --- identity key (link-first) ---

def test_identity_key_prefers_politician_slug():
    m = SpeakerMapping("S0", "Mayor John Hamilton", 0.9, "voice_profile",
                       politician_slug="hamilton-john")
    assert quality.identity_key(m) == "essentials:hamilton-john"


def test_identity_key_local_slug_second():
    m = SpeakerMapping("S0", "Jane Doe", 0.9, "human_review", local_slug="jane-doe")
    assert quality.identity_key(m) == "local:jane-doe"


def test_identity_key_normalized_name_fallback():
    a = SpeakerMapping("S0", "Mayor Hamilton", 0.9, "roll_call")
    b = SpeakerMapping("S1", "hamilton", 0.9, "llm")
    assert quality.identity_key(a) == quality.identity_key(b)


def test_identity_key_none_when_unidentified():
    assert quality.identity_key(SpeakerMapping("S0", None, 0.0, None)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_quality.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.quality'`

- [ ] **Step 3: Implement `src/quality.py`**

Create `src/quality.py`:

```python
"""Meeting confidence gate: speech-time-weighted trust scoring (Phase A).

Pure functions over a Meeting — no IO. The pipeline computes a verdict after
Stage 4 and routes the meeting to publish / review / failed.
"""
from __future__ import annotations

from typing import Optional

from . import config
from .models import Meeting, SpeakerMapping

# Trust tiers
TIER_TRUSTED = "trusted"
TIER_PROBABLE = "probable"
TIER_UNVERIFIED = "unverified"
TIER_UNKNOWN = "unknown"

# Verdicts
VERDICT_PASS = "pass"
VERDICT_REVIEW = "review"
VERDICT_FAILED = "failed"

_TRUSTED_METHODS = {
    "human_review", "human_confirmed", "voice_profile",
    "roll_call", "self_identification", "chair_recognition",
}
_UNVERIFIED_METHODS = {"llm", "name_addressing", "title_context"}

# Titles stripped when normalizing names for the identity-key fallback.
_TITLES = {
    "councilmember", "councilwoman", "councilman", "alderman", "alderwoman",
    "commissioner", "mayor", "vice-mayor", "president", "vice-president",
    "clerk", "secretary", "treasurer", "supervisor", "representative",
    "chair", "chairman", "chairwoman", "dr", "mr", "mrs", "ms",
}


def classify_method(id_method: Optional[str]) -> str:
    """Map an id_method string to a trust tier."""
    if not id_method:
        return TIER_UNKNOWN
    # Returning-speaker voice matches (lowered threshold) -> probable.
    if id_method.startswith("voice_profile") and "returning_" in id_method:
        return TIER_PROBABLE
    if id_method in _TRUSTED_METHODS:
        return TIER_TRUSTED
    # voice_profile with any other parenthetical tag is still a full match.
    if id_method.startswith("voice_profile"):
        return TIER_TRUSTED
    if id_method in _UNVERIFIED_METHODS:
        return TIER_UNVERIFIED
    return TIER_UNKNOWN


def _normalize_name(name: str) -> str:
    """Lowercase, strip leading titles, collapse whitespace — for name fallback."""
    tokens = [t for t in name.strip().lower().replace(".", "").split() if t]
    filtered = [t for t in tokens if t not in _TITLES]
    return " ".join(filtered or tokens)


def identity_key(mapping: Optional[SpeakerMapping]) -> Optional[str]:
    """Stable identity key for comparison: politician_slug > local_slug > name.

    Returns None for an unidentified speaker (no name and no link).
    """
    if mapping is None:
        return None
    if mapping.politician_slug:
        return f"essentials:{mapping.politician_slug}"
    if mapping.local_slug:
        return f"local:{mapping.local_slug}"
    if mapping.speaker_name:
        return f"name:{_normalize_name(mapping.speaker_name)}"
    return None


def _speech_by_label(meeting: Meeting) -> dict[str, float]:
    secs: dict[str, float] = {}
    for seg in meeting.segments:
        dur = max(0.0, (seg.end_time or 0.0) - (seg.start_time or 0.0))
        secs[seg.speaker_label] = secs.get(seg.speaker_label, 0.0) + dur
    return secs


def _tier_for_label(meeting: Meeting, label: str) -> str:
    m = meeting.speakers.get(label)
    if not m or not m.speaker_name:
        return TIER_UNKNOWN
    return classify_method(m.id_method)


def evaluate_meeting(
    meeting: Meeting,
    *,
    thresholds: Optional[dict] = None,
    discount: Optional[float] = None,
    floor: Optional[float] = None,
) -> dict:
    """Score a meeting and return the quality report dict (written to quality.json)."""
    thresholds = thresholds if thresholds is not None else config.GATE_THRESHOLDS
    discount = config.GATE_PROBABLE_DISCOUNT if discount is None else discount
    floor = config.GATE_SPEECH_FLOOR_SECONDS if floor is None else floor

    secs_by_label = _speech_by_label(meeting)
    total_speech = sum(secs_by_label.values())

    # Eligible (principal) speakers: above the incidental floor. If excluding
    # short speakers would leave none, keep all (avoids div-by-zero on short clips).
    eligible = {l: s for l, s in secs_by_label.items() if s >= floor}
    if not eligible:
        eligible = dict(secs_by_label)
    eligible_total = sum(eligible.values())

    secs_by_tier = {TIER_TRUSTED: 0.0, TIER_PROBABLE: 0.0,
                    TIER_UNVERIFIED: 0.0, TIER_UNKNOWN: 0.0}
    per_speaker = []
    for label in sorted(secs_by_label):
        tier = _tier_for_label(meeting, label)
        secs = secs_by_label[label]
        if label in eligible:
            secs_by_tier[tier] += secs
        m = meeting.speakers.get(label)
        per_speaker.append({
            "label": label,
            "name": (m.speaker_name if m else None),
            "id_method": (m.id_method if m else None),
            "tier": tier,
            "speech_seconds": round(secs, 1),
            "eligible": label in eligible,
        })

    def _cov(tier: str) -> float:
        return (secs_by_tier[tier] / eligible_total) if eligible_total else 0.0

    trusted_coverage = _cov(TIER_TRUSTED)
    probable_coverage = _cov(TIER_PROBABLE)
    unverified_coverage = _cov(TIER_UNVERIFIED)
    unknown_coverage = _cov(TIER_UNKNOWN)
    effective_coverage = (
        (secs_by_tier[TIER_TRUSTED] + discount * secs_by_tier[TIER_PROBABLE])
        / eligible_total
    ) if eligible_total else 0.0

    cfg = thresholds.get(meeting.event_kind) or thresholds["default"]
    if total_speech <= 0:
        verdict = VERDICT_FAILED
        reason = "no speech-time in transcript"
    elif effective_coverage >= cfg["high"]:
        verdict = VERDICT_PASS
        reason = f"effective_coverage {effective_coverage:.2f} >= high {cfg['high']:.2f}"
    elif effective_coverage >= cfg["low"]:
        verdict = VERDICT_REVIEW
        reason = (f"effective_coverage {effective_coverage:.2f} in "
                  f"[{cfg['low']:.2f}, {cfg['high']:.2f})")
    else:
        verdict = VERDICT_FAILED
        reason = f"effective_coverage {effective_coverage:.2f} < low {cfg['low']:.2f}"

    return {
        "verdict": verdict,
        "reason": reason,
        "event_kind": meeting.event_kind,
        "thresholds_used": dict(cfg),
        "trusted_coverage": round(trusted_coverage, 4),
        "probable_coverage": round(probable_coverage, 4),
        "unverified_coverage": round(unverified_coverage, 4),
        "unknown_coverage": round(unknown_coverage, 4),
        "effective_coverage": round(effective_coverage, 4),
        "total_speech_seconds": round(total_speech, 1),
        "eligible_speech_seconds": round(eligible_total, 1),
        "seconds_by_tier": {k: round(v, 1) for k, v in secs_by_tier.items()},
        "per_speaker": per_speaker,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_quality.py -q`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/quality.py tests/test_quality.py
git commit -m "feat(gate): add speech-time trust scoring module"
```

---

## Task 3: Persist the verdict headline in `PipelineState`

**Files:**
- Modify: `src/checkpoint.py` (`__init__` ~lines 32-42, `_load` ~lines 48-57, `save` ~lines 61-71)
- Test: `tests/test_quality_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_quality_state.py`:

```python
"""Round-trip tests for the gate fields on PipelineState."""
from __future__ import annotations

from src.checkpoint import PipelineState


def test_gate_fields_default_none(tmp_path):
    state = PipelineState(tmp_path)
    assert state.review_status is None
    assert state.trusted_coverage is None


def test_gate_fields_persist(tmp_path):
    state = PipelineState(tmp_path)
    state.review_status = "review"
    state.trusted_coverage = 0.73
    state.save()

    reloaded = PipelineState(tmp_path)
    assert reloaded.review_status == "review"
    assert reloaded.trusted_coverage == 0.73
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_quality_state.py -q`
Expected: FAIL — `AttributeError: 'PipelineState' object has no attribute 'review_status'`

- [ ] **Step 3: Add the fields**

In `src/checkpoint.py` `__init__`, after the `self.meeting_type` line (~line 41), add:

```python
        self.review_status: Optional[str] = None       # pass | review | failed
        self.trusted_coverage: Optional[float] = None  # gate headline metric
```

In `_load`, after the `self.meeting_type = data.get("meeting_type")` line (~line 57), add:

```python
            self.review_status = data.get("review_status")
            self.trusted_coverage = data.get("trusted_coverage")
```

In `save`, inside the `data = {...}` dict, after `"meeting_type": self.meeting_type,` (~line 71), add:

```python
            "review_status": self.review_status,
            "trusted_coverage": self.trusted_coverage,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_quality_state.py -q`
Expected: PASS

- [ ] **Step 5: Run the existing state tests to confirm no regression**

Run: `.venv/bin/python -m pytest tests/test_rewind_to.py tests/test_body_tagging.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/checkpoint.py tests/test_quality_state.py
git commit -m "feat(gate): persist review_status + trusted_coverage in pipeline state"
```

---

## Task 4: Compute the verdict after Stage 4 and gate downstream work

**Files:**
- Modify: `run_local.py` — add a helper near the other module-level helpers (after `_persist_after_review`, ~line 1824), and call it in `run_pipeline` after the Stage 4 block (the `print()` at ~line 1190, immediately before the Stage 5 header at ~line 1195)
- Test: `tests/test_gate_pipeline.py`

### What it does

`_apply_gate(meeting, meeting_dir, state)` evaluates the meeting, writes `quality.json`, mirrors `verdict` + `trusted_coverage` into `state`, prints a one-line verdict, and returns the report dict. In `run_pipeline`, after Stage 4: compute the gate, then — only when the run is **non-interactive** and the verdict is **not `pass`** and `--publish-anyway` was not passed — print a "queued for review" message and `return` before Stage 5 (summary), Stage 5b (topics), Stage 6 (enrollment), Stage 7 (export), and publish. This protects the Anthropic budget AND prevents auto-enrolling voice profiles from unreviewed automated guesses.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gate_pipeline.py`:

```python
"""Tests for the Stage-4 gate helper in run_local.py."""
from __future__ import annotations

import json

import run_local
from src.checkpoint import PipelineState
from src.models import Meeting, Segment, SpeakerMapping


def _meeting(verdict_kind):
    if verdict_kind == "pass":
        segs = [Segment(0, 0, 600, "S0", "x", speaker_name="A"),
                Segment(1, 600, 1200, "S1", "x", speaker_name="B")]
        speakers = {
            "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile"),
            "S1": SpeakerMapping("S1", "B", 0.95, "roll_call"),
        }
    else:  # review/failed: one long unknown
        segs = [Segment(0, 0, 600, "S0", "x", speaker_name="A"),
                Segment(1, 600, 1200, "S1", "x", speaker_name=None)]
        speakers = {
            "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile"),
            "S1": SpeakerMapping("S1", None, 0.0, None),
        }
    return Meeting(meeting_id="m", city="C", date="2026-01-01",
                   event_kind="council", segments=segs, speakers=speakers)


def test_apply_gate_writes_quality_json_and_state(tmp_path):
    meeting = _meeting("pass")
    state = PipelineState(tmp_path)
    report = run_local._apply_gate(meeting, tmp_path, state)

    assert report["verdict"] == "pass"
    quality_file = tmp_path / "quality.json"
    assert quality_file.exists()
    on_disk = json.loads(quality_file.read_text())
    assert on_disk["verdict"] == "pass"

    reloaded = PipelineState(tmp_path)
    assert reloaded.review_status == "pass"
    assert reloaded.trusted_coverage == report["trusted_coverage"]


def test_apply_gate_records_review_verdict(tmp_path):
    meeting = _meeting("review")
    state = PipelineState(tmp_path)
    report = run_local._apply_gate(meeting, tmp_path, state)
    assert report["verdict"] == "review"
    assert PipelineState(tmp_path).review_status == "review"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gate_pipeline.py -q`
Expected: FAIL — `AttributeError: module 'run_local' has no attribute '_apply_gate'`

- [ ] **Step 3: Add the `_apply_gate` helper**

In `run_local.py`, after the `_persist_after_review` function (~line 1824), add:

```python
def _apply_gate(meeting, meeting_dir: Path, state) -> dict:
    """Evaluate the confidence gate, write quality.json, mirror headline to state.

    Returns the full quality report dict. Pure-ish: only writes quality.json
    and the two state fields. Recomputed on every run that reaches Stage 4 so
    the verdict always reflects current attributions (incl. human review).
    """
    from src import quality

    report = quality.evaluate_meeting(meeting)
    with open(meeting_dir / "quality.json", "w") as f:
        json.dump(report, f, indent=2)

    state.review_status = report["verdict"]
    state.trusted_coverage = report["trusted_coverage"]
    state.save()

    print(
        f"  Gate: {report['verdict'].upper()} "
        f"(trusted={report['trusted_coverage']:.0%}, "
        f"effective={report['effective_coverage']:.0%}) — {report['reason']}"
    )
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gate_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: Wire the gate into `run_pipeline`**

In `run_local.py`, find the end of the Stage 4 block — the lone `print()` at ~line 1190, right before:

```python
    # ======================================================================
    # Stage 5: Summary Generation
    # ======================================================================
```

Insert immediately after that `print()` and before the Stage 5 header comment:

```python
    # ======================================================================
    # Confidence gate (Phase A): score the meeting, route non-passing
    # non-interactive runs to the review queue BEFORE any paid summary or
    # voice enrollment (the latter would poison profiles with unreviewed guesses).
    # ======================================================================
    gate_report = _apply_gate(meeting, meeting_dir, state)
    _interactive = sys.stdin.isatty()
    _publish_anyway = getattr(args, "publish_anyway", False)
    if gate_report["verdict"] != "pass" and not _interactive and not _publish_anyway:
        print()
        print("=" * 60)
        print(f"QUEUED FOR REVIEW — verdict: {gate_report['verdict']}")
        print("=" * 60)
        print(f"  {gate_report['reason']}")
        print(f"  Review with: python run_local.py --review {meeting_id}")
        print(f"  Then publish with: python run_local.py --resume {meeting_id}")
        print("  (Summary, enrollment, and publish were skipped to save cost "
              "and protect voice profiles.)")
        return
```

- [ ] **Step 6: Verify the module still imports and the full suite is green**

Run: `.venv/bin/python -c "import run_local"`
Expected: no output, exit 0

Run: `.venv/bin/python -m pytest tests/test_gate_pipeline.py tests/test_quality.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add run_local.py tests/test_gate_pipeline.py
git commit -m "feat(gate): compute verdict after Stage 4 and queue non-passing runs"
```

---

## Task 5: Guard every publish path + `--publish-anyway`

**Files:**
- Modify: `run_local.py` — add `--publish-anyway` to the parser (~after line 2657, near `--no-publish`); add a `_may_publish` helper; guard the inline publish (~line 1443) and `_publish_meeting_standalone` (~line 1707)
- Test: `tests/test_gate_pipeline.py` (extend)

### What it does

`_may_publish(review_status, publish_anyway)` returns `True` only when `review_status == "pass"` or `publish_anyway` is set. Both publish sites consult it; when blocked, they print why instead of publishing.

- [ ] **Step 1: Write the failing tests (append to `tests/test_gate_pipeline.py`)**

Append:

```python
def test_may_publish_only_on_pass():
    assert run_local._may_publish("pass", False) is True
    assert run_local._may_publish("review", False) is False
    assert run_local._may_publish("failed", False) is False
    assert run_local._may_publish(None, False) is False


def test_may_publish_override():
    assert run_local._may_publish("review", True) is True
    assert run_local._may_publish("failed", True) is True
    assert run_local._may_publish(None, True) is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gate_pipeline.py -q`
Expected: FAIL — `AttributeError: module 'run_local' has no attribute '_may_publish'`

- [ ] **Step 3: Add the helper**

In `run_local.py`, directly after the `_apply_gate` function added in Task 4, add:

```python
def _may_publish(review_status: str | None, publish_anyway: bool) -> bool:
    """Publishing is allowed only on a 'pass' verdict, unless forced by a human."""
    return publish_anyway or review_status == "pass"
```

- [ ] **Step 4: Add the `--publish-anyway` flag**

In `build_parser`, after the `--no-publish` argument (~line 2657), add:

```python
    parser.add_argument("--publish-anyway", action="store_true",
                        help="Force publishing even when the confidence gate "
                             "verdict is 'review' or 'failed' (human override)")
```

- [ ] **Step 5: Guard the inline publish**

In `run_pipeline`, replace the inline publish block (~line 1443):

```python
    if getattr(args, "publish", False):
        try:
            from src.publish import publish_meeting
```

with:

```python
    if getattr(args, "publish", False):
        if not _may_publish(state.review_status, getattr(args, "publish_anyway", False)):
            print(f"  Not publishing — gate verdict is "
                  f"'{state.review_status}'. Review and re-run, or pass "
                  f"--publish-anyway to override.")
        else:
          try:
            from src.publish import publish_meeting
```

Note: the existing body of the `try` (lines 1445-1452) must be re-indented one level deeper to sit under the new `else:`. The whole block becomes:

```python
    if getattr(args, "publish", False):
        if not _may_publish(state.review_status, getattr(args, "publish_anyway", False)):
            print(f"  Not publishing — gate verdict is "
                  f"'{state.review_status}'. Review and re-run, or pass "
                  f"--publish-anyway to override.")
        else:
            try:
                from src.publish import publish_meeting

                result = publish_meeting(meeting, state.body_slug)
                print(f"  Published to Supabase: {result.segments} segments, "
                      f"{result.speakers} speakers")
            except Exception as e:
                print(f"  WARNING: Supabase publish failed: {e}")
                print(f"  Retry later with: python run_local.py --publish-meeting {meeting.meeting_id}")
```

- [ ] **Step 6: Guard the standalone publish**

In `_publish_meeting_standalone` (~line 1676), the function builds `meeting` and reads `state`. Add a `publish_anyway` parameter and guard. Change the signature:

```python
def _publish_meeting_standalone(meeting_id: str, publish_anyway: bool = False) -> None:
```

After the `state = PipelineState(meeting_dir)` line (~line 1701) and before `print(f"Publishing {meeting_id} ...")` (~line 1706), add:

```python
    if not _may_publish(state.review_status, publish_anyway):
        print(f"Refusing to publish {meeting_id} — gate verdict is "
              f"'{state.review_status}'.")
        print("  Review it (python run_local.py --review "
              f"{meeting_id}) and re-run, or pass --publish-anyway to override.")
        sys.exit(2)
```

Update the call site in `main` (~line 2866):

```python
    if args.publish_meeting:
        _publish_meeting_standalone(args.publish_meeting, getattr(args, "publish_anyway", False))
        return
```

- [ ] **Step 7: Run tests + import check**

Run: `.venv/bin/python -m pytest tests/test_gate_pipeline.py -q`
Expected: PASS

Run: `.venv/bin/python -c "import run_local; run_local.build_parser().parse_args(['--publish-meeting','x','--publish-anyway'])"`
Expected: no error

- [ ] **Step 8: Commit**

```bash
git add run_local.py tests/test_gate_pipeline.py
git commit -m "feat(gate): guard all publish paths behind verdict with --publish-anyway override"
```

---

## Task 6: `--review-queue` lister

**Files:**
- Modify: `run_local.py` — add `_review_queue()` (near the other utility functions, after `_meeting_body_slug` ~line 1836) + `--review-queue` flag (~after line 2678) + dispatch in `main` (in the utility-commands block, ~after the `--show-roster` handler at line 2797)
- Test: `tests/test_review_queue.py`

### What it does

Scans `MEETINGS_DIR`, reads each `pipeline_state.json`, and prints meetings grouped by verdict (`review`, then `failed`/low-yield), each group ranked by `trusted_coverage` descending. `pass` and unscored meetings are summarized as counts only. Pure listing — no mutation.

- [ ] **Step 1: Write the failing test**

Create `tests/test_review_queue.py`:

```python
"""Tests for the --review-queue lister."""
from __future__ import annotations

import json

import run_local
from src import config
from src.checkpoint import PipelineState


def _make_meeting(root, mid, verdict, coverage):
    mdir = root / mid
    mdir.mkdir(parents=True)
    state = PipelineState(mdir)
    state.review_status = verdict
    state.trusted_coverage = coverage
    state.save()


def test_review_queue_groups_and_ranks(tmp_path, monkeypatch, capsys):
    meetings_dir = tmp_path / "meetings"
    meetings_dir.mkdir()
    monkeypatch.setattr(config, "MEETINGS_DIR", meetings_dir)
    _make_meeting(meetings_dir, "2026-01-01-a", "review", 0.62)
    _make_meeting(meetings_dir, "2026-01-02-b", "review", 0.81)
    _make_meeting(meetings_dir, "2026-01-03-c", "failed", 0.30)
    _make_meeting(meetings_dir, "2026-01-04-d", "pass", 0.97)

    run_local._review_queue()
    out = capsys.readouterr().out

    # review section present, ranked desc (b before a)
    assert out.index("2026-01-02-b") < out.index("2026-01-01-a")
    # failed/low-yield section present
    assert "2026-01-03-c" in out
    # passing meeting is summarized, not listed in a review section
    assert "1 passing" in out or "pass: 1" in out.lower()


def test_review_queue_empty(tmp_path, monkeypatch, capsys):
    meetings_dir = tmp_path / "meetings"
    meetings_dir.mkdir()
    monkeypatch.setattr(config, "MEETINGS_DIR", meetings_dir)
    run_local._review_queue()
    out = capsys.readouterr().out
    assert "No meetings" in out or "0" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_review_queue.py -q`
Expected: FAIL — `AttributeError: module 'run_local' has no attribute '_review_queue'`

- [ ] **Step 3: Implement `_review_queue`**

In `run_local.py`, after `_meeting_body_slug` (~line 1836), add:

```python
def _review_queue() -> None:
    """List meetings awaiting review, grouped by verdict and ranked by coverage."""
    from src import config
    from src.checkpoint import PipelineState

    meetings_dir = config.MEETINGS_DIR
    if not meetings_dir.exists():
        print("No meetings directory found.")
        return

    review, failed, passed, unscored = [], [], 0, 0
    for mdir in sorted(d for d in meetings_dir.iterdir()
                       if d.is_dir() and not d.name.startswith(".")):
        if not (mdir / "pipeline_state.json").exists():
            continue
        state = PipelineState(mdir)
        cov = state.trusted_coverage if state.trusted_coverage is not None else 0.0
        row = (mdir.name, cov)
        if state.review_status == "review":
            review.append(row)
        elif state.review_status == "failed":
            failed.append(row)
        elif state.review_status == "pass":
            passed += 1
        else:
            unscored += 1

    review.sort(key=lambda r: r[1], reverse=True)
    failed.sort(key=lambda r: r[1], reverse=True)

    if not review and not failed:
        print(f"Review queue empty. ({passed} passing, {unscored} unscored)")
        return

    if review:
        print(f"NEEDS REVIEW ({len(review)}):")
        for name, cov in review:
            print(f"  {cov:5.0%}  {name}")
    if failed:
        print(f"\nLOW YIELD / FAILED ({len(failed)}) "
              f"— may need a roster or voice profiles before review is productive:")
        for name, cov in failed:
            print(f"  {cov:5.0%}  {name}")
    print(f"\nSummary: {len(review)} review, {len(failed)} failed, "
          f"{passed} passing, {unscored} unscored.")
    print("Review one with: python run_local.py --review <MEETING_ID>")
```

- [ ] **Step 4: Add the flag + dispatch**

In `build_parser`, after the `--batch-resume` argument (~line 2678), add:

```python
    parser.add_argument("--review-queue", action="store_true",
                        help="List meetings awaiting review (grouped by gate verdict) and exit")
```

In `main`, in the utility-commands block (after the `--show-roster` handler ends at ~line 2797, before `if args.list_profiles:`), add:

```python
    if args.review_queue:
        _review_queue()
        return
```

- [ ] **Step 5: Run tests + import check**

Run: `.venv/bin/python -m pytest tests/test_review_queue.py -q`
Expected: PASS

Run: `.venv/bin/python -c "import run_local; run_local.build_parser().parse_args(['--review-queue'])"`
Expected: no error

- [ ] **Step 6: Commit**

```bash
git add run_local.py tests/test_review_queue.py
git commit -m "feat(gate): add --review-queue lister"
```

---

## Task 7: Non-destructive calibration harness

**Files:**
- Create: `bench/calibrate_gate.py`
- Test: `tests/test_calibrate_gate.py`

### What it does

For each already-reviewed meeting dir (the corrected `transcript_named.json` is ground truth), re-derive the *automated* Stage-4 attributions by calling `src.identify.identify_speakers` directly against the meeting's saved `diarization.json`/`transcript_raw.json` + `embeddings.json` + the current voice profiles + roster — **never** running the full pipeline, **never** enrolling, **never** writing to the meeting dir. Then compute, per meeting and aggregated per `event_kind`:

- `trusted_coverage` of the automated output (what the gate would score), and
- `trusted_precision`: of the speech-time the automated trusted+probable tiers *claimed*, how much matched the ground-truth identity (link-first via `quality.identity_key`).

Output a table so HIGH can be set where `trusted_precision` ≈ 100%.

Speaker labels are stable between the truth and the re-derived run because diarization is reused (not re-run), so comparison is per-label, weighted by that label's speech-time. The Layer-3 LLM is skipped by default (`llm_identify_fn=None`) — it only feeds the `unverified` tier and adds cost/time; pass `--with-llm` to include it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calibrate_gate.py`:

```python
"""Tests for the calibration harness precision math + non-destructiveness."""
from __future__ import annotations

import json

import numpy as np

import importlib.util
from pathlib import Path

# Load bench/calibrate_gate.py as a module.
_spec = importlib.util.spec_from_file_location(
    "calibrate_gate", Path(__file__).resolve().parent.parent / "bench" / "calibrate_gate.py")
calibrate_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calibrate_gate)

from src.models import Meeting, Segment, SpeakerMapping


def _truth():
    segs = [Segment(0, 0, 600, "S0", "x", speaker_name="A"),
            Segment(1, 600, 1200, "S1", "x", speaker_name="B")]
    speakers = {
        "S0": SpeakerMapping("S0", "A", 1.0, "human_confirmed", politician_slug="a"),
        "S1": SpeakerMapping("S1", "B", 1.0, "human_confirmed", politician_slug="b"),
    }
    return Meeting(meeting_id="m", city="C", date="2026-01-01",
                   event_kind="council", segments=segs, speakers=speakers)


def test_precision_perfect_when_auto_matches_truth():
    truth = _truth()
    auto = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile", politician_slug="a"),
        "S1": SpeakerMapping("S1", "B", 0.95, "voice_profile", politician_slug="b"),
    }
    res = calibrate_gate.compare(truth, auto)
    assert res["trusted_claimed_seconds"] == 1200.0
    assert res["trusted_correct_seconds"] == 1200.0
    assert res["trusted_precision"] == 1.0


def test_precision_drops_on_false_positive_voice_match():
    truth = _truth()
    # S1 is voice-matched to the WRONG person at the lowered returning threshold.
    auto = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile", politician_slug="a"),
        "S1": SpeakerMapping("S1", "C", 0.72, "voice_profile (returning_3+)", politician_slug="c"),
    }
    res = calibrate_gate.compare(truth, auto)
    # 600s of S0 correct out of 1200s claimed (S1 wrong) -> 0.5 precision.
    assert res["trusted_correct_seconds"] == 600.0
    assert res["trusted_precision"] == 0.5


def test_unidentified_auto_not_counted_as_claim():
    truth = _truth()
    auto = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile", politician_slug="a"),
        "S1": SpeakerMapping("S1", None, 0.0, None),
    }
    res = calibrate_gate.compare(truth, auto)
    # Only S0 was claimed; precision over claims is 100%, coverage is partial.
    assert res["trusted_claimed_seconds"] == 600.0
    assert res["trusted_precision"] == 1.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_calibrate_gate.py -q`
Expected: FAIL — `FileNotFoundError` / module load error (bench/calibrate_gate.py missing)

- [ ] **Step 3: Implement `bench/calibrate_gate.py`**

Create `bench/calibrate_gate.py`:

```python
#!/usr/bin/env python3
"""Non-destructive calibration of the meeting confidence gate.

For each reviewed meeting (corrected transcript_named.json = ground truth),
re-derive the AUTOMATED Stage-4 attributions by calling identify_speakers()
directly against the saved diarization/embeddings + current profiles + roster.
Never runs the full pipeline, never enrolls, never writes to the meeting dir.

Reports per-meeting and per-event-kind:
  trusted_coverage  — what the gate would score on the automated output
  trusted_precision — of the speech-time the trusted+probable tiers CLAIMED,
                      how much matched the ground-truth identity (link-first)

Usage:
  .venv/bin/python bench/calibrate_gate.py                 # all reviewed meetings
  .venv/bin/python bench/calibrate_gate.py 2026-02-10-regular-session ...
  .venv/bin/python bench/calibrate_gate.py --with-llm      # include Layer 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import config, quality
from src.models import Meeting, Segment, SpeakerMapping
from src.identify import identify_speakers


def _speech_by_label(segments) -> dict[str, float]:
    secs: dict[str, float] = {}
    for s in segments:
        dur = max(0.0, (s.end_time or 0.0) - (s.start_time or 0.0))
        secs[s.speaker_label] = secs.get(s.speaker_label, 0.0) + dur
    return secs


def compare(truth: Meeting, auto_mappings: dict[str, SpeakerMapping]) -> dict:
    """Per-label, speech-time-weighted precision of the automated TRUSTED+PROBABLE tiers.

    A label is 'claimed' when its automated tier is trusted or probable. It is
    'correct' when the automated identity_key equals the truth identity_key.
    """
    secs = _speech_by_label(truth.segments)
    claimed = 0.0
    correct = 0.0
    trusted_secs = 0.0
    total = sum(secs.values()) or 0.0

    for label, label_secs in secs.items():
        auto = auto_mappings.get(label)
        tier = quality.classify_method(auto.id_method) if (auto and auto.speaker_name) else quality.TIER_UNKNOWN
        if tier == quality.TIER_TRUSTED:
            trusted_secs += label_secs
        if tier in (quality.TIER_TRUSTED, quality.TIER_PROBABLE):
            claimed += label_secs
            if quality.identity_key(auto) == quality.identity_key(truth.speakers.get(label)):
                correct += label_secs

    return {
        "trusted_claimed_seconds": round(claimed, 1),
        "trusted_correct_seconds": round(correct, 1),
        "trusted_precision": round(correct / claimed, 4) if claimed else 1.0,
        "trusted_coverage": round(trusted_secs / total, 4) if total else 0.0,
    }


def _rederive_auto(meeting_dir: Path, truth: Meeting, with_llm: bool) -> dict[str, SpeakerMapping]:
    """Run identify_speakers() against saved artifacts. Read-only; never enrolls."""
    from src.enroll import get_stored_centroids, load_profiles

    emb_path = meeting_dir / "embeddings.json"
    if emb_path.exists():
        emb = json.loads(emb_path.read_text())
        embeddings = {k: np.array(v) for k, v in emb.items()}
    else:
        embeddings = {}

    profile_db = load_profiles()
    centroids = get_stored_centroids(profile_db)

    body_slug = None
    state_file = meeting_dir / "pipeline_state.json"
    if state_file.exists():
        body_slug = json.loads(state_file.read_text()).get("body_slug")

    roster = None
    try:
        from src.roster import load_roster
        roster = load_roster(body_slug=body_slug) if body_slug else load_roster()
    except Exception:
        roster = None

    llm_fn = None  # Layer 3 only feeds the unverified tier; skip by default.
    if with_llm:
        from src.llm_utils import llm_identify_speakers, load_llm
        _llm = load_llm()
        llm_fn = lambda segs, maps: llm_identify_speakers(_llm, segs, maps)

    return identify_speakers(
        truth.segments, embeddings,
        stored_profiles=centroids or None,
        llm_identify_fn=llm_fn,
        roster=roster,
        profile_db=profile_db,
    )


def calibrate_meeting(meeting_dir: Path, with_llm: bool) -> dict | None:
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    truth = Meeting.from_dict(json.loads(named.read_text()))
    auto = _rederive_auto(meeting_dir, truth, with_llm)
    result = compare(truth, auto)
    result["meeting_id"] = meeting_dir.name
    result["event_kind"] = truth.event_kind
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Calibrate the confidence gate against reviewed meetings.")
    ap.add_argument("meeting_ids", nargs="*", help="Specific meeting IDs (default: all)")
    ap.add_argument("--with-llm", action="store_true", help="Include Layer-3 LLM in re-derivation")
    args = ap.parse_args()

    if args.meeting_ids:
        dirs = [config.MEETINGS_DIR / m for m in args.meeting_ids]
    else:
        dirs = sorted(d for d in config.MEETINGS_DIR.iterdir()
                      if d.is_dir() and not d.name.startswith("."))

    rows = []
    for d in dirs:
        res = calibrate_meeting(d, args.with_llm)
        if res:
            rows.append(res)

    if not rows:
        print("No reviewed meetings (transcript_named.json) found to calibrate against.")
        return

    print(f"{'meeting':<34} {'kind':<16} {'cov':>6} {'prec':>6}")
    print("-" * 66)
    for r in rows:
        print(f"{r['meeting_id']:<34} {r['event_kind']:<16} "
              f"{r['trusted_coverage']:>6.0%} {r['trusted_precision']:>6.0%}")

    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r["event_kind"], []).append(r)

    print("\nPer-event-kind (set HIGH where precision ~= 100%):")
    for kind, krows in sorted(by_kind.items()):
        covs = [r["trusted_coverage"] for r in krows]
        precs = [r["trusted_precision"] for r in krows]
        print(f"  {kind:<16} n={len(krows)}  "
              f"min_cov={min(covs):.0%}  mean_prec={sum(precs)/len(precs):.0%}  "
              f"min_prec={min(precs):.0%}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_calibrate_gate.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bench/calibrate_gate.py tests/test_calibrate_gate.py
git commit -m "feat(gate): add non-destructive calibration harness"
```

---

## Task 8: Full-suite regression + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (no regressions in the existing ~35 test files; new gate tests green)

- [ ] **Step 2: Smoke-test the queue on real data (read-only)**

Run: `.venv/bin/python run_local.py --review-queue`
Expected: prints either "Review queue empty…" or a grouped list — no traceback. (Meetings processed before this change are `unscored` until re-run, which is fine.)

- [ ] **Step 3: Smoke-test calibration on a known-good meeting (read-only, non-destructive)**

Run: `.venv/bin/python bench/calibrate_gate.py` (or pass a specific reviewed meeting ID)
Expected: a coverage/precision table. Confirm afterward that the meeting's `transcript_named.json` mtime is unchanged (the harness must not have written to it):
Run: `git status` and verify no meeting-data files were modified.

- [ ] **Step 4: Commit any doc note (optional)**

If you adjusted seed thresholds based on calibration output, edit `src/config.py` `GATE_THRESHOLDS` and:

```bash
git add src/config.py
git commit -m "chore(gate): tune seed thresholds from calibration run"
```

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** Q1 speech-time signal → Task 2 `evaluate_meeting`. Q2 tiers + probable discount → Task 2 `classify_method` + `GATE_PROBABLE_DISCOUNT`. Q3 don't-publish + queue → Tasks 4-6. Q4 gate after Stage 4 + skip paid summary → Task 4 early-return. Q5 quality.json + state + module → Tasks 2-4. Q6/Q7 per-kind thresholds + floor + calibration → Tasks 1, 2, 7. Q8 publish guard + override → Task 5. Q9 failed surfaced + error separate → Task 6 (`failed` listed low-yield; pipeline errors are unchanged batch try/except). Q10 link-first identity + precision → Task 2 `identity_key` + Task 7 `compare`.
- **Cold-start behavior (intended):** a brand-new body with no voice profiles and no roster will score low and route to review/failed — correct; reviewing it once bootstraps profiles, after which similar meetings start passing.
- **Interactive runs are unchanged:** the early-return in Task 4 fires only when `not sys.stdin.isatty()`. A human at a terminal still reviews inline at Stage 4; the recomputed verdict (now containing `human_confirmed`) typically becomes `pass`, so summary/enroll/publish proceed as before.
- **Indentation caution:** Task 5 Step 5 re-indents the existing inline publish body under a new `else:`. Copy the final block verbatim rather than hand-editing indentation.
```
