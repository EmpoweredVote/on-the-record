# Per-Embedding Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag every stored voice embedding with its source meeting so the calibration harness can do embedding-level leave-one-out (recompute a held-out centroid) instead of dropping whole profiles, restoring real held-out signal.

**Architecture:** Introduce an `EmbeddingRecord` dataclass (`vector`, `meeting_id`, `seg_count`) and change `StoredProfile.embeddings` from `list[np.ndarray]` to `list[EmbeddingRecord]`. Production centroid math stays a plain mean (now over `.vector`). A new `StoredProfile.centroid_excluding(meeting_id)` method powers a rewritten `_decontaminated_centroids` in the bench harness. Bump the profile schema version so the existing auto-discard path drops legacy DBs that lack provenance.

**Tech Stack:** Python 3, numpy, pytest. Tests run with `.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-16-embedding-provenance-design.md`

---

## File Structure

- `src/enroll.py` — add `EmbeddingRecord`; change `StoredProfile.embeddings` type; update `recompute_centroid`; add `centroid_excluding`; stamp records in `_enroll_one`. The `.extend()` calls in `merge_profiles`/`rename_profile`/`fix_profiles_with_roster` need no change (they extend lists of records).
- `src/config.py` — bump `PROFILE_SCHEMA_VERSION` 3 → 4; update the comment.
- `bench/calibrate_gate.py` — rewrite `_decontaminated_centroids` to recompute held-out centroids.
- `tests/test_embedding_provenance.py` — new: provenance stamping, `centroid_excluding`, merge provenance, pickle round-trip.
- `tests/test_profile_v3.py` — update two schema-version assertions (3 → 4).
- `tests/test_calibrate_gate.py` — rewrite the decontamination test for record-based profiles; add a held-out-centroid test.
- `tests/test_identification.py` — update one direct `StoredProfile(embeddings=[...])` construction to use `EmbeddingRecord` (type consistency; no behavior change).

---

## Task 1: EmbeddingRecord + provenance-aware enrollment + schema bump

This is one atomic change: the dataclass, the type change, `recompute_centroid`, `centroid_excluding`, and the `_enroll_one` stamping must land together or `recompute_centroid` would try to read `.vector` off bare arrays and the suite would go red.

**Files:**
- Modify: `src/enroll.py` (dataclass at 34-48, `recompute_centroid` at 45-47, `_enroll_one` at 148-180)
- Modify: `src/config.py:85` and comment at `src/config.py:32`
- Modify: `tests/test_profile_v3.py:23,53`
- Modify: `tests/test_identification.py:438-446`
- Test: `tests/test_embedding_provenance.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_embedding_provenance.py`:

```python
"""Tests for per-embedding provenance (EmbeddingRecord) — schema v4."""
from __future__ import annotations

import numpy as np

from src import config
from src.enroll import (
    EmbeddingRecord,
    ProfileDB,
    StoredProfile,
    enroll_speakers,
    merge_profiles,
    load_profiles,
    save_profiles,
    _db_path,
)
from src.models import Segment, SpeakerMapping


def _emb():
    return np.random.randn(8).astype(np.float32)


def _mapping(name, label="SPEAKER_01"):
    return {label: SpeakerMapping(
        speaker_label=label, speaker_name=name, confidence=0.95,
        id_method="human_review")}


def _segments(label="SPEAKER_01", n=3):
    return [Segment(segment_id=i, start_time=float(i), end_time=float(i) + 1.0,
                    speaker_label=label, text="hi") for i in range(n)]


def test_schema_version_is_4():
    assert config.PROFILE_SCHEMA_VERSION == 4
    assert ProfileDB().schema_version == 4


def test_enroll_stamps_meeting_id_and_seg_count():
    db = enroll_speakers(ProfileDB(), {"SPEAKER_01": _emb()},
                         _mapping("John Public"), "m1", _segments(n=3))
    profile = db.profiles["public_john"]
    assert len(profile.embeddings) == 1
    rec = profile.embeddings[0]
    assert isinstance(rec, EmbeddingRecord)
    assert rec.meeting_id == "m1"
    assert rec.seg_count == 3


def test_centroid_excluding_drops_only_matching_meeting():
    profile = StoredProfile(
        speaker_id="x", display_name="X",
        embeddings=[
            EmbeddingRecord(np.array([1.0, 0.0]), "m1"),
            EmbeddingRecord(np.array([0.0, 1.0]), "m2"),
        ],
    )
    held = profile.centroid_excluding("m1")
    np.testing.assert_allclose(held, np.array([0.0, 1.0]))


def test_centroid_excluding_singleton_returns_none():
    profile = StoredProfile(
        speaker_id="x", display_name="X",
        embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "m1")],
    )
    assert profile.centroid_excluding("m1") is None


def test_centroid_excluding_unrelated_meeting_returns_full_centroid():
    profile = StoredProfile(
        speaker_id="x", display_name="X",
        embeddings=[
            EmbeddingRecord(np.array([1.0, 0.0]), "m1"),
            EmbeddingRecord(np.array([3.0, 0.0]), "m2"),
        ],
    )
    held = profile.centroid_excluding("m99")
    np.testing.assert_allclose(held, np.array([2.0, 0.0]))


def test_merge_preserves_both_sources_provenance():
    db = ProfileDB(profiles={
        "src": StoredProfile(speaker_id="src", display_name="S",
                             embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "m1")],
                             meetings_seen=["m1"]),
        "dst": StoredProfile(speaker_id="dst", display_name="D",
                             embeddings=[EmbeddingRecord(np.array([0.0, 1.0]), "m2")],
                             meetings_seen=["m2"]),
    })
    assert merge_profiles(db, "src", "dst") is True
    dst = db.profiles["dst"]
    seen = sorted(r.meeting_id for r in dst.embeddings)
    assert seen == ["m1", "m2"]


def test_v4_db_round_trips_through_pickle(monkeypatch, tmp_path):
    path = tmp_path / "speaker_profiles.pkl"
    monkeypatch.setattr("src.enroll._db_path", lambda: path)
    db = ProfileDB(profiles={
        "x": StoredProfile(speaker_id="x", display_name="X",
                           embeddings=[EmbeddingRecord(np.array([1.0, 2.0]), "m1", 5)],
                           meetings_seen=["m1"]),
    })
    save_profiles(db)
    loaded = load_profiles()
    assert loaded.schema_version == 4
    rec = loaded.profiles["x"].embeddings[0]
    assert isinstance(rec, EmbeddingRecord)
    assert rec.meeting_id == "m1"
    assert rec.seg_count == 5
    np.testing.assert_allclose(rec.vector, np.array([1.0, 2.0]))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_embedding_provenance.py -v`
Expected: FAIL — `ImportError: cannot import name 'EmbeddingRecord'`.

- [ ] **Step 3: Bump the schema version in `src/config.py`**

Replace line 85:

```python
PROFILE_SCHEMA_VERSION = 4
```

Replace the comment at lines 32-33:

```python
# PROFILE_SCHEMA_VERSION is bumped when the embedding model OR the stored profile
# structure changes, so load_profiles() can detect and discard stale profiles
# instead of silently mis-matching or unpickling an incompatible shape.
```

- [ ] **Step 4: Add `EmbeddingRecord` and update `StoredProfile` in `src/enroll.py`**

Replace the `StoredProfile` dataclass (lines 34-47) with:

```python
@dataclass
class EmbeddingRecord:
    """One stored voice embedding plus where it came from.

    meeting_id enables embedding-level leave-one-out in calibration; seg_count is
    banked for later centroid weighting / profile pruning (roadmap item L).
    """
    vector: np.ndarray
    meeting_id: str
    seg_count: int = 0


@dataclass
class StoredProfile:
    speaker_id: str  # slug, e.g. "adams_jane"
    display_name: str
    embeddings: list[EmbeddingRecord] = field(default_factory=list)
    centroid: Optional[np.ndarray] = None
    meetings_seen: list[str] = field(default_factory=list)
    total_segments_confirmed: int = 0
    politician_slug: Optional[str] = None   # essentials identifier
    politician_id: Optional[str] = None     # essentials UUID

    def recompute_centroid(self) -> None:
        if self.embeddings:
            self.centroid = np.mean([r.vector for r in self.embeddings], axis=0)

    def centroid_excluding(self, meeting_id: str) -> Optional[np.ndarray]:
        """Centroid from embeddings NOT sourced from meeting_id.

        None when every embedding came from that meeting (no held-out signal),
        which is the honest answer for a speaker enrolled only from that meeting.
        """
        held_out = [r.vector for r in self.embeddings if r.meeting_id != meeting_id]
        return np.mean(held_out, axis=0) if held_out else None
```

- [ ] **Step 5: Stamp provenance in `_enroll_one`**

In `src/enroll.py`, replace the append on line 161:

```python
        profile.embeddings.append(EmbeddingRecord(embedding, meeting_id, seg_count))
```

And the new-profile branch (lines 170-178) `embeddings=[embedding]` with:

```python
        profile = StoredProfile(
            speaker_id=slug,
            display_name=display_name,
            embeddings=[EmbeddingRecord(embedding, meeting_id, seg_count)],
            meetings_seen=[meeting_id],
            total_segments_confirmed=seg_count,
            politician_slug=politician_slug,
            politician_id=politician_id,
        )
```

(The `.extend()` calls in `merge_profiles`, `rename_profile`, `fix_profiles_with_roster` are left unchanged — they extend lists of records and provenance survives.)

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_embedding_provenance.py -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Update the two stale schema-version assertions**

In `tests/test_profile_v3.py`, line 23:

```python
    assert db.schema_version == 4
```

Line 53:

```python
    assert result.schema_version == 4
```

- [ ] **Step 8: Update the direct StoredProfile construction in `test_identification.py`**

In `tests/test_identification.py`, add `EmbeddingRecord` to the import on line 433:

```python
from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord
```

Replace line 441 (`embeddings=[centroid],`) with:

```python
        embeddings=[EmbeddingRecord(centroid, "m0")],
```

- [ ] **Step 9: Run the affected existing suites to verify green**

Run: `.venv/bin/python -m pytest tests/test_profile_v3.py tests/test_identification.py -v`
Expected: PASS (no failures; the schema and construction updates keep them green).

- [ ] **Step 10: Commit**

```bash
git add src/enroll.py src/config.py tests/test_embedding_provenance.py tests/test_profile_v3.py tests/test_identification.py
git commit -m "feat(enroll): per-embedding provenance via EmbeddingRecord (schema v4)"
```

---

## Task 2: Embedding-level decontamination in the calibration harness

**Files:**
- Modify: `bench/calibrate_gate.py:96-108` (`_decontaminated_centroids`)
- Modify: `tests/test_calibrate_gate.py:85-97` (rewrite decontam test); add one test

- [ ] **Step 1: Rewrite the decontamination test and add a held-out test**

In `tests/test_calibrate_gate.py`, replace `test_decontaminated_centroids_excludes_self_enrolled` (lines 85-97) with:

```python
def test_decontaminated_centroids_excludes_self_enrolled():
    from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord

    db = ProfileDB(profiles={
        "a": StoredProfile(speaker_id="a", display_name="A",
                           embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "m1")],
                           centroid=np.array([1.0, 0.0]), meetings_seen=["m1"]),
        "b": StoredProfile(speaker_id="b", display_name="B",
                           embeddings=[EmbeddingRecord(np.array([0.0, 1.0]), "m2")],
                           centroid=np.array([0.0, 1.0]), meetings_seen=["m2"]),
    })
    cents = calibrate_gate._decontaminated_centroids(db, "m1")
    assert "a" not in cents     # singleton from m1 -> no held-out signal
    assert "b" in cents


def test_decontaminated_centroid_recomputed_from_held_out_meetings():
    from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord

    db = ProfileDB(profiles={
        "a": StoredProfile(
            speaker_id="a", display_name="A",
            embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "m1"),
                        EmbeddingRecord(np.array([0.0, 1.0]), "m2")],
            centroid=np.array([0.5, 0.5]), meetings_seen=["m1", "m2"]),
    })
    cents = calibrate_gate._decontaminated_centroids(db, "m1")
    # m1 excluded; only m2's embedding remains -> held-out centroid is m2's.
    assert "a" in cents
    np.testing.assert_allclose(cents["a"], np.array([0.0, 1.0]))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_calibrate_gate.py -k decontam -v`
Expected: FAIL — `test_decontaminated_centroid_recomputed_from_held_out_meetings` fails because the current implementation drops the whole multi-meeting profile (the meeting being scored is in `meetings_seen`), so `"a"` is absent.

- [ ] **Step 3: Rewrite `_decontaminated_centroids`**

In `bench/calibrate_gate.py`, replace the function body (lines 96-108) with:

```python
def _decontaminated_centroids(profile_db, meeting_id: str) -> dict[str, np.ndarray]:
    """Held-out centroids: each profile's centroid recomputed from only the
    embeddings NOT sourced from `meeting_id`.

    Embeddings carry their source meeting (EmbeddingRecord.meeting_id), so a
    speaker seen in several meetings keeps a real, uncontaminated centroid when
    one of their meetings is scored. A speaker enrolled only from `meeting_id`
    yields no centroid — honest, since there is no held-out signal for them.
    """
    out: dict[str, np.ndarray] = {}
    for pid, p in profile_db.profiles.items():
        c = p.centroid_excluding(meeting_id)
        if c is not None:
            out[pid] = c
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_calibrate_gate.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add bench/calibrate_gate.py tests/test_calibrate_gate.py
git commit -m "feat(calibrate): embedding-level leave-one-out via held-out centroids"
```

---

## Task 3: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. If anything fails, it is almost certainly a remaining `StoredProfile(embeddings=[<bare array>])` construction or a `np.mean(profile.embeddings)` call — fix it to use `EmbeddingRecord` / `.vector` and re-run. No new failures should remain.

- [ ] **Step 2: Confirm no bare-array embedding constructions remain**

Run: `grep -rn "embeddings=\[" src/ bench/ tests/ reenroll_profiles.py | grep -v EmbeddingRecord`
Expected: no output (every embedding construction now wraps an `EmbeddingRecord`).

---

## Post-implementation (operational, not code)

The running profile DB (`~/CouncilScribe/profiles/speaker_profiles.pkl`) is v3 and will be auto-discarded on the next load (backed up to `speaker_profiles.v3.pkl.bak`). Re-populate it by re-running enrollment over reviewed meetings:

```bash
.venv/bin/python reenroll_profiles.py
```

`reenroll_profiles.py` calls `enroll_speakers`, which now stamps provenance, so every re-enrolled embedding carries its source meeting. Re-run `bench/calibrate_gate.py` afterward to confirm held-out coverage is no longer collapsing to ~0 for multi-meeting speakers.

---

## Self-Review

**Spec coverage:**
- EmbeddingRecord {vector, meeting_id, seg_count} → Task 1 Step 4. ✓
- `embeddings` type change → Task 1 Step 4. ✓
- `recompute_centroid` over `.vector` (unchanged behavior) → Task 1 Step 4. ✓
- `centroid_excluding` → Task 1 Step 4, tested Step 1. ✓
- `_enroll_one` stamping → Task 1 Step 5, tested Step 1. ✓
- merges preserve provenance → Task 1 Step 1 test (no code change needed). ✓
- `_decontaminated_centroids` rewrite → Task 2. ✓
- schema bump 3→4 + comment → Task 1 Steps 3. ✓
- discard legacy DB (no code change, existing path) + re-enroll → Post-implementation note + Task 1 round-trip test. ✓
- Testing list (stamping, centroid_excluding, merge, decontam integration, pickle round-trip) → Task 1 + Task 2 tests. ✓
- Out-of-scope (no threshold/weighting/pruning/pattern changes) → respected; seg_count stored but unused. ✓

**Placeholder scan:** none — every code step shows full code; every run step shows command + expected outcome.

**Type consistency:** `EmbeddingRecord(vector, meeting_id, seg_count)` used identically in enroll code and all tests; `centroid_excluding(meeting_id)` signature matches its call site in `_decontaminated_centroids`; schema version `4` consistent across config, defaults, and assertions.
