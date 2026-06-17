# Per-Embedding Provenance Design

## Goal

Tag every stored voice embedding with the meeting it came from, so the
calibration harness can do **embedding-level** leave-one-out instead of dropping
whole profiles. This turns "decontaminated coverage ≈ 0" into real held-out
signal, which is the prerequisite for measuring whether later profile-hygiene
changes (roadmap item L: voice-collision handling, threshold tuning, junk
pruning) actually help.

This is the first of three entangled blockers (E → L → P). E only makes L and P
*measurable*; it deliberately changes no identification behavior.

## Problem

Each `StoredProfile` holds `embeddings: list[np.ndarray]` — one centroid per
speaker per enrolled meeting — averaged into `centroid` for Layer-1 matching.
Nothing records which embedding came from which meeting.

`bench/calibrate_gate.py::_decontaminated_centroids` prevents a meeting from
grading itself (memorization, not generalization). Today it excludes a profile
entirely when the scored meeting appears in `meetings_seen`:

```python
if p.centroid is not None and meeting_id not in p.meetings_seen
```

Because most speakers are singletons (enrolled only from their own meeting),
this drops their whole profile during their own scoring → the robot has nothing
to recognize them with → coverage collapses to ~0. Even multi-meeting speakers
lose *all* their meetings' signal, not just the contaminating one. The result is
no usable held-out signal.

## Approach

1. Attach `{meeting_id, seg_count}` to each stored embedding.
2. Change decontamination to recompute a centroid from only the embeddings *not*
   sourced from the scored meeting, instead of dropping the profile.

A multi-meeting speaker then keeps a real, uncontaminated centroid when one of
their meetings is scored. A singleton correctly contributes nothing for its own
meeting — an honest absence of held-out signal, not a bug.

## Data Model (`src/enroll.py`)

New pickle-safe dataclass in `src.enroll` (already inside
`RestrictedUnpickler._SAFE_MODULES`, so the unpickler needs no change):

```python
@dataclass
class EmbeddingRecord:
    vector: np.ndarray
    meeting_id: str
    seg_count: int = 0
```

`StoredProfile.embeddings` changes from `list[np.ndarray]` to
`list[EmbeddingRecord]`. All other fields (`centroid`, `meetings_seen`,
`total_segments_confirmed`, `politician_slug`, `politician_id`) are unchanged.

`recompute_centroid` averages over the vectors — production centroid behavior is
identical (still a plain mean):

```python
def recompute_centroid(self) -> None:
    if self.embeddings:
        self.centroid = np.mean([r.vector for r in self.embeddings], axis=0)
```

New method, used by decontamination and reusable by L:

```python
def centroid_excluding(self, meeting_id: str) -> Optional[np.ndarray]:
    """Centroid from embeddings NOT sourced from meeting_id.
    None when every embedding came from that meeting (no held-out signal)."""
    held_out = [r.vector for r in self.embeddings if r.meeting_id != meeting_id]
    return np.mean(held_out, axis=0) if held_out else None
```

`seg_count` is stored for later use (L: centroid weighting / pruning) and is
otherwise unused by E.

## Enrollment Changes (`src/enroll.py`)

- `_enroll_one`: append `EmbeddingRecord(embedding, meeting_id, seg_count)` in
  both the existing-profile and new-profile branches, instead of a bare array.
- `merge_profiles`, `rename_profile`, `fix_profiles_with_roster`: the existing
  `target.embeddings.extend(source.embeddings)` calls work unchanged — they now
  extend lists of records, each carrying its own `meeting_id`. Provenance
  survives merges with no collisions. This is the reason `list[EmbeddingRecord]`
  was chosen over a `dict[meeting_id -> embedding]`, which would silently drop an
  embedding when two profiles that both saw the same meeting are merged.
- `get_stored_centroids`, `get_borderline_speakers`, `enroll_speakers`,
  `enroll_confirmed`: signatures and behavior unchanged (they read `.centroid`
  and mappings, never raw embeddings).

## Calibration Harness Change (`bench/calibrate_gate.py`)

`_decontaminated_centroids` stops dropping whole profiles and recomputes a
held-out centroid per profile:

```python
def _decontaminated_centroids(profile_db, meeting_id):
    out = {}
    for pid, p in profile_db.profiles.items():
        c = p.centroid_excluding(meeting_id)  # None ⇒ no held-out signal
        if c is not None:
            out[pid] = c
    return out
```

Effect: a multi-meeting speaker keeps a real, uncontaminated centroid when one
of their meetings is scored (was: dropped entirely). Singletons contribute
nothing for their own meeting — honest, not a bug.

## Schema and Migration (`src/config.py`)

- Bump `PROFILE_SCHEMA_VERSION` from `3` to `4`.
- Update the comment at `src/config.py:32` to note the version also bumps on
  stored-structure shape changes, not only on embedding-model changes.
- No code change to `load_profiles()`: its existing version-mismatch path
  auto-backs-up the old DB (`speaker_profiles.v3.pkl.bak`) and returns a fresh
  empty `ProfileDB`. Profiles are re-enrolled from meetings on the next runs.

Legacy v3 embeddings have no recoverable per-meeting provenance, so a
migration could only stamp them `meeting_id=None`, making them un-decontaminable
(they would silently contaminate every calibration). Discard + re-enroll is the
honest choice and is low-cost now: auto-publish is OFF and profiles are not yet
load-bearing, so this is the moment to change the schema before they accumulate.

## Testing

- `EmbeddingRecord` is stamped with the correct `meeting_id` and `seg_count` on
  enrollment, in both the new-profile and existing-profile branches.
- `centroid_excluding`: returns the mean of held-out records for an unrelated
  meeting_id; returns `None` for a singleton's own meeting; returns a partial
  centroid for a multi-meeting profile when one of its meetings is excluded.
- `merge_profiles` preserves both sources' provenance — records from both
  meetings are present and correctly tagged afterward.
- Decontamination integration: a two-meeting speaker yields a centroid when
  scoring meeting A; a singleton yields none for its own meeting.
- Round-trip pickle save/load of a v4 DB containing `EmbeddingRecord`s.

## Out of Scope (deferred to L and P)

- No change to `VOICE_MATCH_THRESHOLD`, `RETURNING_SPEAKER_THRESHOLD_*`,
  centroid weighting, voice-collision handling, or junk-profile pruning.
- No change to pattern attribution (`roll_call`, `self_identification`, etc.).

E only makes those measurable. `seg_count` is stored but unused until L.
