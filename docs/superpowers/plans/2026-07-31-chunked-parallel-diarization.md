# Chunked Parallel Diarization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diarize long meetings by fanning out fixed-length windows across Modal containers and unifying speaker labels globally, cutting a 5-hour meeting's diarization from ~109 min to ~6 min without changing what we publish.

**Architecture:** Per the spec `docs/superpowers/specs/2026-07-31-chunked-parallel-diarization-design.md`: a stateless Modal **chunk worker** (reads only its window from the volume WAV, diarizes with overlap for context, returns absolute-time turns + per-local-speaker centroids and speech durations), a pure local **stitcher** (`src/diarize_stitch.py`, one-to-one Hungarian assignment against a running global speaker table), and a chunked branch in the local **orchestrator** (`src/modal_compute.run_diarization`) that fans out with `.starmap`, stitches, then reuses the existing `merge_similar_speakers`. Same `(segments_data, embeddings)` return contract as the single-pass path.

**Tech Stack:** Python (`/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python` ONLY — the venv lives in the MAIN repo; from a worktree use that absolute path), modal, pyannote.audio 4.0.4, numpy, scipy, soundfile. Branch `perf/chunked-diarization` (already created off main, spec + L4 revert already committed as a809804).

**House conventions:** flat `tests/test_*.py` run with `.venv/bin/pytest`; pure logic is unit-tested, Modal/cursor-bound code is thin and untested; `conftest.py` strips DATABASE_URL; config knobs live in `src/config.py`; fixtures committed verbatim.

**Measured facts this plan depends on** (do not re-derive): diarization cost ∝ duration^1.8–1.9; the `embeddings` hook step is ~99% of wall-clock and buckets CPU-bound single-threaded clustering; GPU tier, cpu count, embedding batch size, and volume-vs-local audio I/O were each measured to make no difference. A 60-min window of the June 10 audio diarizes in ~306s total (embeddings 298.8s).

---

### Task 1: Pure stitcher — `src/diarize_stitch.py`

**Files:**
- Create: `src/diarize_stitch.py`
- Create: `tests/test_diarize_stitch.py`
- Modify: `src/config.py`

- [ ] **Step 1: Add config knobs.** In `src/config.py`, directly below the existing `SPEAKER_MERGE_THRESHOLD` line (currently `SPEAKER_MERGE_THRESHOLD = 0.80  # merge diarized speakers ...`), add:

```python
# Chunked diarization (see docs/superpowers/specs/2026-07-31-chunked-parallel-diarization-design.md).
# 0 disables chunking (single-pass diarization, the pre-2026-08 behaviour).
DIARIZE_CHUNK_MINUTES = 0
DIARIZE_CHUNK_OVERLAP_SECONDS = 60
# Cosine similarity required to call a chunk-local speaker the same person as
# an already-seen global speaker. Same scale as SPEAKER_MERGE_THRESHOLD.
CHUNK_STITCH_THRESHOLD = 0.80
```

- [ ] **Step 2: Write the failing tests** — `tests/test_diarize_stitch.py`, exactly this content:

```python
"""Cross-chunk speaker unification tests.

The stitcher's job: chunk-local SPEAKER_xx labels are meaningless across
chunks, so match them by centroid similarity into a global label space.
The load-bearing rule is one-to-one per chunk — two speakers the chunk's own
clustering called distinct must never collapse into one global speaker.
"""
import numpy as np

from src.diarize_stitch import ChunkResult, stitch_chunks


def _unit(*values) -> list[float]:
    vec = np.array(values, dtype=float)
    return list(vec / np.linalg.norm(vec))


# Three mutually dissimilar voices (orthogonal → cosine similarity 0).
ALICE = _unit(1, 0, 0)
BOB = _unit(0, 1, 0)
CAROL = _unit(0, 0, 1)
# Alice with a little noise: still clearly Alice (similarity ~0.997).
ALICE_NOISY = _unit(1, 0.08, 0)


def test_same_voice_across_chunks_gets_one_global_label():
    chunks = [
        ChunkResult(
            start_s=0.0, end_s=60.0,
            turns=[(0.0, 30.0, "SPEAKER_00")],
            centroids={"SPEAKER_00": ALICE},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
        ChunkResult(
            start_s=60.0, end_s=120.0,
            turns=[(60.0, 90.0, "SPEAKER_00")],  # local label reused, same person
            centroids={"SPEAKER_00": ALICE_NOISY},
            speech_seconds={"SPEAKER_00": 30.0},
        ),
    ]
    turns, centroids, log = stitch_chunks(chunks, threshold=0.8)
    assert len({label for _, _, label in turns}) == 1
    assert len(centroids) == 1
    assert [t[0] for t in turns] == [0.0, 60.0]  # absolute times preserved, ordered


def test_different_voices_reusing_the_same_local_label_stay_separate():
    chunks = [
        ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 30.0}),
        ChunkResult(60.0, 120.0, [(60.0, 90.0, "SPEAKER_00")], {"SPEAKER_00": BOB},
                    {"SPEAKER_00": 30.0}),
    ]
    turns, centroids, log = stitch_chunks(chunks, threshold=0.8)
    assert len(centroids) == 2
    assert len({label for _, _, label in turns}) == 2


def test_one_to_one_two_locals_never_collapse_into_one_global():
    """The correctness constraint: chunk 2 has two speakers who both look like
    Alice; only the better match may take Alice's global label."""
    chunks = [
        ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 30.0}),
        ChunkResult(
            60.0, 120.0,
            turns=[(60.0, 70.0, "SPEAKER_00"), (70.0, 80.0, "SPEAKER_01")],
            centroids={"SPEAKER_00": ALICE, "SPEAKER_01": ALICE_NOISY},
            speech_seconds={"SPEAKER_00": 10.0, "SPEAKER_01": 10.0},
        ),
    ]
    turns, centroids, log = stitch_chunks(chunks, threshold=0.8)
    # Two distinct labels within chunk 2 must remain two distinct globals.
    chunk2 = [label for start, _, label in turns if start >= 60.0]
    assert len(set(chunk2)) == 2
    assert len(centroids) == 2


def test_below_threshold_creates_a_new_global_speaker():
    chunks = [
        ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 30.0}),
        ChunkResult(60.0, 120.0, [(60.0, 90.0, "SPEAKER_00")], {"SPEAKER_00": CAROL},
                    {"SPEAKER_00": 30.0}),
    ]
    turns, centroids, _ = stitch_chunks(chunks, threshold=0.8)
    assert len(centroids) == 2


def test_global_labels_are_canonical_and_time_ordered():
    chunks = [
        ChunkResult(60.0, 120.0, [(60.0, 90.0, "SPEAKER_00")], {"SPEAKER_00": BOB},
                    {"SPEAKER_00": 30.0}),
        ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 30.0}),
    ]
    turns, centroids, _ = stitch_chunks(chunks, threshold=0.8)
    # Chunks may arrive out of order; output is sorted by time and the first
    # speaker seen chronologically is SPEAKER_00.
    assert [t[0] for t in turns] == [0.0, 60.0]
    assert turns[0][2] == "SPEAKER_00"
    assert sorted(centroids) == ["SPEAKER_00", "SPEAKER_01"]


def test_centroids_are_duration_weighted():
    """A 10s appearance must not drag a global centroid as much as a 300s one."""
    chunks = [
        ChunkResult(0.0, 60.0, [(0.0, 300.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                    {"SPEAKER_00": 300.0}),
        ChunkResult(60.0, 120.0, [(60.0, 70.0, "SPEAKER_00")],
                    {"SPEAKER_00": ALICE_NOISY}, {"SPEAKER_00": 10.0}),
    ]
    _, centroids, _ = stitch_chunks(chunks, threshold=0.8)
    merged = np.array(centroids["SPEAKER_00"])
    # Much closer to pure Alice than to the noisy sample.
    assert float(merged @ np.array(ALICE)) > float(merged @ np.array(ALICE_NOISY))


def test_empty_and_single_chunk_inputs():
    assert stitch_chunks([], threshold=0.8) == ([], {}, [])
    one = ChunkResult(0.0, 60.0, [(0.0, 30.0, "SPEAKER_00")], {"SPEAKER_00": ALICE},
                      {"SPEAKER_00": 30.0})
    turns, centroids, _ = stitch_chunks([one], threshold=0.8)
    assert turns == [(0.0, 30.0, "SPEAKER_00")]
    assert list(centroids) == ["SPEAKER_00"]


def test_turn_without_a_centroid_is_kept_under_a_fresh_label():
    """A chunk can emit a turn for a speaker too short to embed; the turn is
    real audio and must not be silently dropped."""
    chunk = ChunkResult(
        0.0, 60.0,
        turns=[(0.0, 30.0, "SPEAKER_00"), (30.0, 30.2, "SPEAKER_09")],
        centroids={"SPEAKER_00": ALICE},
        speech_seconds={"SPEAKER_00": 30.0, "SPEAKER_09": 0.2},
    )
    turns, centroids, log = stitch_chunks([chunk], threshold=0.8)
    assert len(turns) == 2
    assert len({label for _, _, label in turns}) == 2
    assert any("no centroid" in line for line in log)
```

- [ ] **Step 3: Run to verify failure.** `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/pytest tests/test_diarize_stitch.py -v` → FAIL (`ModuleNotFoundError: src.diarize_stitch`).

- [ ] **Step 4: Implement `src/diarize_stitch.py`:**

```python
"""Unify chunk-local speaker labels into one global label space.

Chunked diarization runs pyannote on fixed windows because its cost is
~quadratic in duration (measured July 2026: 2x duration = 4.5x cost). Each
window's SPEAKER_xx labels are only meaningful inside that window, so the
windows' speakers must be matched to each other by voice embedding.

The load-bearing rule is ONE-TO-ONE per chunk: two labels a chunk's own
clustering called distinct are distinct people, so they must never collapse
into a single global speaker (the identity-collision failure that once
conflated two people in an interview). A greedy nearest-centroid walk can
violate that; an optimal assignment cannot, so matching is solved with
scipy's linear_sum_assignment over the similarity matrix, keeping only pairs
at or above the threshold.

Global centroids are duration-weighted running means: a 10-second appearance
must not move a speaker's voiceprint as much as a 5-minute one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment


@dataclass
class ChunkResult:
    """One window's diarization, in ABSOLUTE meeting time.

    `centroids` and `speech_seconds` are keyed by the chunk-local speaker
    label. A label may appear in `turns`/`speech_seconds` without a centroid
    when it had too little audio to embed.
    """

    start_s: float
    end_s: float
    turns: list[tuple[float, float, str]]
    centroids: dict[str, list[float]]
    speech_seconds: dict[str, float] = field(default_factory=dict)


def _cosine_matrix(locals_: list[np.ndarray], globals_: list[np.ndarray]) -> np.ndarray:
    """Rows = chunk-local centroids, cols = global centroids."""
    if not locals_ or not globals_:
        return np.zeros((len(locals_), len(globals_)))
    a = np.vstack(locals_)
    b = np.vstack(globals_)
    a = a / np.clip(np.linalg.norm(a, axis=1, keepdims=True), 1e-12, None)
    b = b / np.clip(np.linalg.norm(b, axis=1, keepdims=True), 1e-12, None)
    return a @ b.T


def stitch_chunks(
    chunks: list[ChunkResult], threshold: float
) -> tuple[list[tuple[float, float, str]], dict[str, list[float]], list[str]]:
    """Merge per-chunk diarizations into one globally-labelled turn list.

    Returns (turns sorted by start time, global centroids, human-readable log).
    Global labels are assigned in chronological order of first appearance:
    SPEAKER_00, SPEAKER_01, ...
    """
    log: list[str] = []
    # Global speaker table, parallel lists so assignment indices map directly.
    g_vectors: list[np.ndarray] = []
    g_weights: list[float] = []
    g_turns: list[list[tuple[float, float]]] = []

    for chunk in sorted(chunks, key=lambda c: c.start_s):
        local_labels = sorted(chunk.centroids)
        local_vecs = [np.asarray(chunk.centroids[label], dtype=float) for label in local_labels]
        assigned: dict[str, int] = {}

        if local_vecs and g_vectors:
            sims = _cosine_matrix(local_vecs, g_vectors)
            # Maximize total similarity => minimize its negation.
            rows, cols = linear_sum_assignment(-sims)
            for row, col in zip(rows, cols):
                if sims[row, col] >= threshold:
                    assigned[local_labels[row]] = col
                    log.append(
                        f"chunk@{chunk.start_s:.0f}s {local_labels[row]} -> "
                        f"global {col} (sim {sims[row, col]:.3f})"
                    )

        # Unmatched locals (and every local in the first chunk) open new globals.
        for label, vec in zip(local_labels, local_vecs):
            if label in assigned:
                continue
            g_vectors.append(vec)
            g_weights.append(0.0)
            g_turns.append([])
            assigned[label] = len(g_vectors) - 1
            log.append(f"chunk@{chunk.start_s:.0f}s {label} -> new global {assigned[label]}")

        # A turn whose label never got a centroid still happened; give it its
        # own global speaker rather than dropping or guessing it.
        for label in {lbl for _, _, lbl in chunk.turns} - set(assigned):
            g_vectors.append(np.zeros_like(g_vectors[0]) if g_vectors else np.zeros(1))
            g_weights.append(0.0)
            g_turns.append([])
            assigned[label] = len(g_vectors) - 1
            log.append(
                f"chunk@{chunk.start_s:.0f}s {label} -> new global {assigned[label]} "
                "(no centroid: too little audio to embed)"
            )

        # Fold this chunk's turns and voice evidence into the global table.
        for start, end, label in chunk.turns:
            g_turns[assigned[label]].append((start, end))
        for label, index in assigned.items():
            weight = max(chunk.speech_seconds.get(label, 0.0), 0.0)
            if weight <= 0 or label not in chunk.centroids:
                continue
            vec = np.asarray(chunk.centroids[label], dtype=float)
            total = g_weights[index] + weight
            g_vectors[index] = (
                (g_vectors[index] * g_weights[index] + vec * weight) / total
                if g_weights[index] > 0
                else vec
            )
            g_weights[index] = total

    # Relabel by chronological first appearance so output is stable and reads
    # like single-pass output.
    first_seen = {
        index: min((start for start, _ in spans), default=float("inf"))
        for index, spans in enumerate(g_turns)
    }
    order = sorted(first_seen, key=lambda i: (first_seen[i], i))
    names = {index: f"SPEAKER_{rank:02d}" for rank, index in enumerate(order)}

    turns = sorted(
        (
            (start, end, names[index])
            for index, spans in enumerate(g_turns)
            for start, end in spans
        ),
        key=lambda t: (t[0], t[1]),
    )
    centroids = {
        names[index]: list(g_vectors[index])
        for index in order
        if g_weights[index] > 0
    }
    return turns, centroids, log
```

- [ ] **Step 5: Run tests.** `.venv/bin/pytest tests/test_diarize_stitch.py -v` → all 8 PASS. Then the full suite: `.venv/bin/pytest tests/ -q` (was 1532 passed; expect 1540). If a pin fails, fix the CODE, never the test — and if you believe a test expectation is genuinely wrong, STOP and report rather than editing it.

- [ ] **Step 6: Commit.**

```bash
git add src/diarize_stitch.py tests/test_diarize_stitch.py src/config.py
git commit -m "feat: cross-chunk speaker stitcher (one-to-one assignment, weighted centroids)"
```

### Task 2: Modal chunk worker — `bench/modal_app.diarize_chunk_window`

**Files:** Modify `bench/modal_app.py` (add one function; no tests — Modal-bound per house policy)

- [ ] **Step 1: Add the worker** directly ABOVE `pipeline_diarize_and_embed` (search for `def pipeline_diarize_and_embed`; put the new function and its decorator before that decorator block):

```python
@app.function(
    image=pyannote_image,
    volumes={VOLUME_PATH: volume},
    secrets=[hf_secret],
    # Measured: GPU tier does not matter for this workload (the dominant cost
    # is single-threaded CPU clustering), so take the cheap card. Chunking is
    # what buys the speedup — see the chunked-diarization design doc.
    gpu="L4",
    timeout=60 * 60,
)
def diarize_chunk_window(
    meeting_id: str, start_s: float, end_s: float, overlap_s: float = 60.0
) -> str:
    """Diarize ONE window of a meeting; return absolute-time turns + centroids.

    Reads only `[start_s - overlap_s, end_s + overlap_s]` out of the volume
    WAV (soundfile start/frames — no full-file load, no re-upload). The
    overlap gives the segmentation model context across the seam; returned
    turns are clipped to `[start_s, end_s)` so windows never double-count.

    Returns JSON: {"start_s", "end_s", "turns": [[start, end, label], ...],
    "centroids": {label: [float, ...]}, "speech_seconds": {label: float},
    "elapsed_s": float} with all times in ABSOLUTE meeting seconds.
    """
    import json as _json
    import os
    import time as _time

    import numpy as np
    import soundfile as sf
    import torch
    from pyannote.audio import Inference, Model, Pipeline

    wav_path = Path(VOLUME_PATH) / "meetings" / meeting_id / "audio.wav"
    if not wav_path.exists():
        raise FileNotFoundError(
            f"Audio not found in Modal volume: {wav_path}. "
            "Run src.modal_compute.upload_audio() first."
        )

    info = sf.info(str(wav_path))
    sr = info.samplerate
    read_start = max(0.0, start_s - overlap_s)
    read_end = min(info.frames / sr, end_s + overlap_s)
    frames = int((read_end - read_start) * sr)
    samples, _ = sf.read(
        str(wav_path), start=int(read_start * sr), frames=frames, dtype="float32"
    )
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    device = torch.device("cuda")
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1", token=os.environ["HF_TOKEN"]
    )
    pipeline.to(device)

    t0 = _time.time()
    waveform = torch.tensor(samples, dtype=torch.float32).unsqueeze(0)
    diarization = pipeline({"waveform": waveform, "sample_rate": sr})
    elapsed = _time.time() - t0

    # Window-relative -> absolute, then clip to this chunk's canonical span so
    # the overlap is context only and turns are never counted twice.
    turns: list[tuple[float, float, str]] = []
    for rel_start, rel_end, speaker in _annotation_to_turns(diarization):
        abs_start = read_start + rel_start
        abs_end = read_start + rel_end
        clipped_start = max(abs_start, start_s)
        clipped_end = min(abs_end, end_s)
        if clipped_end - clipped_start > 0.05:  # drop slivers created by clipping
            turns.append((round(clipped_start, 3), round(clipped_end, 3), str(speaker)))

    speech_seconds: dict[str, float] = {}
    for start, end, label in turns:
        speech_seconds[label] = speech_seconds.get(label, 0.0) + (end - start)

    # Per-speaker centroids over this window's KEPT turns, same model and
    # whole-window inference as the single-pass path so the embedding space
    # (and therefore voice-profile matching) is unchanged.
    emb_model = Model.from_pretrained("pyannote/embedding", token=os.environ["HF_TOKEN"])
    inference = Inference(emb_model, window="whole", device=device)
    per_speaker: dict[str, list] = {}
    for start, end, label in turns:
        i0 = int((start - read_start) * sr)
        i1 = int((end - read_start) * sr)
        clip = samples[i0:i1]
        if len(clip) < int(sr * 0.3):
            continue
        wf = torch.tensor(clip, dtype=torch.float32).unsqueeze(0).to(device)
        per_speaker.setdefault(label, []).append(inference({"waveform": wf, "sample_rate": sr}))
    centroids = {
        label: np.mean(vectors, axis=0).tolist()
        for label, vectors in per_speaker.items()
    }

    print(f"  [chunk {start_s:.0f}-{end_s:.0f}s] {len(turns)} turns, "
          f"{len(centroids)} speakers, {elapsed:.1f}s")
    return _json.dumps({
        "start_s": start_s,
        "end_s": end_s,
        "turns": turns,
        "centroids": centroids,
        "speech_seconds": speech_seconds,
        "elapsed_s": round(elapsed, 1),
    })
```

- [ ] **Step 2: Verify it imports.** `.venv/bin/python -c "import ast; ast.parse(open('bench/modal_app.py').read())"` then `.venv/bin/python -c "import sys; sys.path.insert(0,'.'); import bench.modal_app as m; print(m.diarize_chunk_window)"`.

- [ ] **Step 3: Commit.**

```bash
git add bench/modal_app.py
git commit -m "feat: Modal chunk worker (window read, overlap context, absolute times)"
```

### Task 3: Orchestrator — chunked branch in `src/modal_compute.py`

**Files:** Modify `src/modal_compute.py`; create `tests/test_modal_compute.py`

- [ ] **Step 1: Write failing tests for the pure window planner** — `tests/test_modal_compute.py`:

```python
"""Window planning for chunked diarization (pure; no Modal, no network)."""
from src.modal_compute import plan_chunk_windows


def test_short_audio_is_a_single_window():
    assert plan_chunk_windows(1800.0, chunk_minutes=30) == [(0.0, 1800.0)]


def test_windows_tile_the_audio_without_gaps_or_overlap():
    windows = plan_chunk_windows(9000.0, chunk_minutes=30)  # 2.5 hours
    assert windows[0][0] == 0.0
    assert windows[-1][1] == 9000.0
    assert len(windows) == 5
    for (prev_start, prev_end), (next_start, _) in zip(windows, windows[1:]):
        assert prev_end == next_start  # canonical spans abut exactly


def test_short_trailing_remainder_folds_into_the_previous_window():
    """A 61-minute meeting must not produce a 1-minute window whose speakers
    can barely be embedded."""
    windows = plan_chunk_windows(3660.0, chunk_minutes=30)
    assert windows == [(0.0, 1800.0), (1800.0, 3660.0)]


def test_chunking_disabled_returns_one_window():
    assert plan_chunk_windows(9000.0, chunk_minutes=0) == [(0.0, 9000.0)]
```

- [ ] **Step 2: Run to verify failure.** `.venv/bin/pytest tests/test_modal_compute.py -v` → FAIL (`ImportError: cannot import name 'plan_chunk_windows'`).

- [ ] **Step 3: Implement.** In `src/modal_compute.py`, add the planner at module level (after `_modal_app`) :

```python
#: A trailing window shorter than this fraction of a full chunk is folded
#: into its predecessor instead of standing alone — tiny windows produce
#: thin-evidence centroids that stitch badly.
_MIN_TRAILING_FRACTION = 0.5


def plan_chunk_windows(
    duration_s: float, chunk_minutes: int
) -> list[tuple[float, float]]:
    """Split [0, duration_s] into canonical (start, end) diarization windows.

    `chunk_minutes <= 0`, or audio shorter than one chunk, yields a single
    window — i.e. exactly the single-pass behaviour.
    """
    if chunk_minutes <= 0:
        return [(0.0, duration_s)]
    chunk_s = float(chunk_minutes * 60)
    if duration_s <= chunk_s:
        return [(0.0, duration_s)]

    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_s:
        end = min(start + chunk_s, duration_s)
        windows.append((start, end))
        start = end
    # Fold a stub trailing window into its predecessor.
    if len(windows) > 1:
        last_start, last_end = windows[-1]
        if (last_end - last_start) < chunk_s * _MIN_TRAILING_FRACTION:
            prev_start, _ = windows[-2]
            windows[-2:] = [(prev_start, last_end)]
    return windows
```

- [ ] **Step 4: Add the chunked dispatch path.** Still in `src/modal_compute.py`, add this function after `plan_chunk_windows`:

```python
def _run_chunked_diarization(
    app, wav_path: Path, meeting_id: str, chunk_minutes: int, use_merge: bool
) -> tuple[list[dict], dict[str, list[float]]]:
    """Fan windows out across Modal containers, then unify labels locally.

    Diarization cost is ~quadratic in window length (measured), so N windows
    cost ~1/N of one pass AND run concurrently. Speaker labels are only
    meaningful within a window, so `stitch_chunks` matches them by voice
    embedding, and the existing `merge_similar_speakers` then handles
    within-meeting fragmentation exactly as the single-pass path does.
    """
    import numpy as np

    from . import config
    from .audio_utils import get_audio_duration
    from .diarize_stitch import ChunkResult, stitch_chunks
    from .merge import merge_similar_speakers
    from .models import Segment

    duration = get_audio_duration(wav_path)
    windows = plan_chunk_windows(duration, chunk_minutes)
    overlap = float(config.DIARIZE_CHUNK_OVERLAP_SECONDS)
    print(f"  Chunked diarization: {len(windows)} window(s) of "
          f"{chunk_minutes} min (+{overlap:.0f}s overlap) over "
          f"{duration / 60:.1f} min of audio")

    args = [(meeting_id, start, end, overlap) for start, end in windows]
    with app.app.run():
        payloads = list(app.diarize_chunk_window.starmap(args))

    chunks = []
    for payload in payloads:
        data = json.loads(payload)
        chunks.append(ChunkResult(
            start_s=data["start_s"],
            end_s=data["end_s"],
            turns=[(t[0], t[1], t[2]) for t in data["turns"]],
            centroids=data["centroids"],
            speech_seconds=data["speech_seconds"],
        ))
    slowest = max((json.loads(p).get("elapsed_s", 0.0) for p in payloads), default=0.0)
    print(f"  Slowest window: {slowest:.0f}s "
          f"(vs one single-pass call over the whole meeting)")

    turns, centroids, stitch_log = stitch_chunks(
        chunks, threshold=config.CHUNK_STITCH_THRESHOLD
    )
    print(f"  Stitched to {len(centroids)} global speaker(s) from "
          f"{sum(len(c.centroids) for c in chunks)} chunk-local label(s)")
    for line in stitch_log:
        print(f"    {line}")

    segments_data = [
        {
            "segment_id": i,
            "start_time": start,
            "end_time": end,
            "speaker_label": label,
            "text": "",
            "words": [],
        }
        for i, (start, end, label) in enumerate(turns)
    ]

    if use_merge and centroids:
        segs = [
            Segment(
                segment_id=d["segment_id"],
                start_time=d["start_time"],
                end_time=d["end_time"],
                speaker_label=d["speaker_label"],
            )
            for d in segments_data
        ]
        merged_segs, merged_centroids, merge_log = merge_similar_speakers(
            segs, {k: np.array(v) for k, v in centroids.items()}
        )
        if merge_log:
            print(f"  Post-stitch merge: {merge_log}")
        segments_data = [
            {
                "segment_id": s.segment_id,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "speaker_label": s.speaker_label,
                "text": "",
                "words": [],
            }
            for s in merged_segs
        ]
        centroids = {k: v.tolist() for k, v in merged_centroids.items()}

    return segments_data, centroids
```

- [ ] **Step 5: Wire it into `run_diarization`.** In `src/modal_compute.py`, `run_diarization` currently has the signature `(wav_path, meeting_id, use_merge=False, diarizer="oss")`. Add a `chunk_minutes: int = 0` keyword parameter, and immediately after the `upload_audio(wav_path, meeting_id)` call insert:

```python
    if diarizer != "vibevoice" and chunk_minutes > 0:
        return _run_chunked_diarization(
            app, wav_path, meeting_id, chunk_minutes, use_merge
        )
```

Leave every existing line below it unchanged (the single-pass path stays the default and the fallback). Update the function's docstring to mention the chunked branch.

- [ ] **Step 6: Pass the flag from `run_local.py`.** Find the `_modal_diarize(` call in `run_local.py` (around line 995, inside `run_pipeline`, the site that raised in the June 10 traceback) and add the argument `chunk_minutes=getattr(args, "diarize_chunk_minutes", None) or config.DIARIZE_CHUNK_MINUTES` (check how `config` is imported in that scope; if it isn't, import it locally as the surrounding code does). Then add the CLI flag next to the other diarizer flags in the argparse section:

```python
    parser.add_argument("--diarize-chunk-minutes", type=int, metavar="N",
                        help="Diarize in N-minute windows fanned out across Modal "
                             "containers and stitch speakers globally (0 = single "
                             "pass; default from config.DIARIZE_CHUNK_MINUTES). "
                             "Diarization cost is ~quadratic in window length, so "
                             "this is the main speedup for long meetings.")
```

- [ ] **Step 7: Full suite.** `.venv/bin/pytest tests/ -q` → green (expect 1544). Confirm the single-pass path is untouched by running `.venv/bin/pytest tests/ -q -k "modal or diariz"` and reading the output.

- [ ] **Step 8: Commit.**

```bash
git add src/modal_compute.py tests/test_modal_compute.py run_local.py
git commit -m "feat: chunked diarization orchestration (fan out, stitch, reuse merge)"
```

### Task 4: Live calibration + DER gate

**Files:** Create `scripts/calibrate_chunked_diarization.py`

This is the task that decides whether chunking ships enabled. It writes no DB rows and needs no Anthropic API (diarization only) — but it does spend Modal GPU time.

- [ ] **Step 1: Write the calibration script** `scripts/calibrate_chunked_diarization.py`:

```python
#!/usr/bin/env python
"""Compare chunked vs single-pass diarization on already-processed meetings.

For each meeting: run the chunked path at each requested chunk size, score it
against the SINGLE-PASS output as reference (DER via pyannote.metrics, the
same scorer bench/score.py uses), and print a table. No DB writes, no LLM.

Gate (from the design doc): DER <= 10% and speaker count within +-20% of the
single-pass result. A chunk size that fails on ANY meeting is not eligible to
become the default.

Usage:
  .venv/bin/python scripts/calibrate_chunked_diarization.py \
      bloomington-city-council-2026-07-29 --chunks 30 45 60
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local

load_env_local()

from bench.score import calculate_der  # noqa: E402
from src import config  # noqa: E402
from src.modal_compute import _run_chunked_diarization, _modal_app, upload_audio  # noqa: E402


def _rttm(turns, meeting_id: str, path: Path) -> Path:
    lines = [
        f"SPEAKER {meeting_id} 1 {start:.3f} {end - start:.3f} "
        f"<NA> <NA> {label} <NA> <NA>"
        for start, end, label in turns
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def _turns_from_segments(segments) -> list[tuple[float, float, str]]:
    return [(s["start_time"], s["end_time"], s["speaker_label"]) for s in segments]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("meeting_ids", nargs="+")
    ap.add_argument("--chunks", type=int, nargs="+", default=[30, 45, 60])
    args = ap.parse_args()

    app = _modal_app()
    rows = []
    for meeting_id in args.meeting_ids:
        wav = config.MEETINGS_DIR / meeting_id / "audio.wav"
        if not wav.exists():
            print(f"SKIP {meeting_id}: no local audio at {wav}", file=sys.stderr)
            continue
        upload_audio(wav, meeting_id)

        # Reference = the single-pass output this repo ships today.
        print(f"\n=== {meeting_id}: single-pass reference ===")
        t0 = time.time()
        with app.app.run():
            payload = app.pipeline_diarize_and_embed.remote(meeting_id, use_merge=True)
        base_elapsed = time.time() - t0
        base = json.loads(payload)
        base_turns = _turns_from_segments(base["segments"])
        base_speakers = len({t[2] for t in base_turns})
        print(f"  {len(base_turns)} turns, {base_speakers} speakers, "
              f"{base_elapsed:.0f}s")

        with tempfile.TemporaryDirectory() as tmp:
            ref = _rttm(base_turns, meeting_id, Path(tmp) / "single_pass.rttm")
            for chunk_minutes in args.chunks:
                print(f"\n=== {meeting_id}: chunked @ {chunk_minutes} min ===")
                t0 = time.time()
                segments, centroids = _run_chunked_diarization(
                    app, wav, meeting_id, chunk_minutes, use_merge=True
                )
                elapsed = time.time() - t0
                turns = _turns_from_segments(segments)
                speakers = len({t[2] for t in turns})
                hyp = _rttm(turns, meeting_id, Path(tmp) / f"chunk{chunk_minutes}.rttm")
                metrics = calculate_der(ref, hyp)
                der = metrics["der"] if metrics else None
                drift = (speakers - base_speakers) / base_speakers if base_speakers else 0
                rows.append({
                    "meeting": meeting_id,
                    "chunk_minutes": chunk_minutes,
                    "der": der,
                    "speakers": speakers,
                    "base_speakers": base_speakers,
                    "speaker_drift": round(drift, 3),
                    "elapsed_s": round(elapsed),
                    "base_elapsed_s": round(base_elapsed),
                    "speedup": round(base_elapsed / elapsed, 1) if elapsed else None,
                    "passes_gate": bool(
                        der is not None and der <= 0.10 and abs(drift) <= 0.20
                    ),
                })
                print(f"  DER vs single-pass: {der:.4f}" if der is not None
                      else "  DER: unavailable")
                print(f"  {speakers} speakers (single-pass {base_speakers}, "
                      f"drift {drift:+.1%}), {elapsed:.0f}s "
                      f"(speedup {rows[-1]['speedup']}x), "
                      f"gate {'PASS' if rows[-1]['passes_gate'] else 'FAIL'}")

    print("\n================ SUMMARY ================")
    print(f"{'meeting':<42} {'chunk':>6} {'DER':>8} {'spk':>5} {'drift':>7} "
          f"{'secs':>6} {'speedup':>8} gate")
    for r in rows:
        der = f"{r['der']:.4f}" if r["der"] is not None else "n/a"
        print(f"{r['meeting']:<42} {r['chunk_minutes']:>6} {der:>8} "
              f"{r['speakers']:>5} {r['speaker_drift']:>+7.1%} "
              f"{r['elapsed_s']:>6} {str(r['speedup']) + 'x':>8} "
              f"{'PASS' if r['passes_gate'] else 'FAIL'}")
    print("=========================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Check `calculate_der`'s signature before running.** Read `bench/score.py`'s `calculate_der` (around line 85) and confirm the call `calculate_der(ref, hyp)` matches — it takes `(reference_rttm, hypothesis_rttm, ...)` with an optional UEM. If a UEM argument is required, pass `None`. Also confirm `config.MEETINGS_DIR / meeting_id / "audio.wav"` is where processed audio lands (check another script, e.g. `scripts/make_segment_fixture.py`, for the local layout) and fix the path if it differs.

- [ ] **Step 3: Run the short meeting first** (cheap, ~15 min of GPU across arms):

```bash
.venv/bin/python scripts/calibrate_chunked_diarization.py \
    bloomington-city-council-2026-07-29 --chunks 30 45
```

Read the DER and speaker-drift numbers. Expect single-pass ~630s vs chunked ~2 windows; the speedup on an 82-minute meeting is modest — the point here is the **quality** signal on a meeting whose 14 speakers were human-reviewed.

- [ ] **Step 4: Run the adversarial meeting** (the real test: 5 hours, 41 speakers, many seams):

```bash
.venv/bin/python scripts/calibrate_chunked_diarization.py \
    bloomington-city-council-2026-06-10 --chunks 30 45 60
```

Record wall-clock and DER per arm. If Modal concurrency limits throttle the fan-out, note the observed concurrency in your report.

- [ ] **Step 5: Report, then set the default.** In your task report include the full summary table. Then:
  - If at least one chunk size passes the gate on **both** meetings → set `config.DIARIZE_CHUNK_MINUTES` to the passing size with the best speedup (prefer the LARGER window when DERs are within 2 points of each other — fewer seams is the safer failure mode) and add a one-line comment citing the measured DER.
  - If nothing passes → leave `DIARIZE_CHUNK_MINUTES = 0`, and report exactly which criterion failed and by how much. The capability still ships, disabled behind the flag. Do NOT relax the gate to make it pass.

- [ ] **Step 6: Commit.**

```bash
git add scripts/calibrate_chunked_diarization.py src/config.py
git commit -m "test: chunked-vs-single-pass DER calibration + calibrated default"
```

### Task 5: Docs + PR

- [ ] **Step 1: Update the spec** `docs/superpowers/specs/2026-07-31-chunked-parallel-diarization-design.md`: append a "Calibration results (2026-07-31)" section with the summary table from Task 4 and the chosen default (or the reason chunking stayed disabled).

- [ ] **Step 2: Note it in the meeting-day runbook** `docs/runbooks/bloomington-meeting-day.md`, in step 2 after the flag list — one short paragraph: long meetings diarize in parallel windows when `DIARIZE_CHUNK_MINUTES` is set (or with `--diarize-chunk-minutes N`), speaker labels are unified by voice embedding, and the single-pass path remains available with `--diarize-chunk-minutes 0`.

- [ ] **Step 3: Full suite green, then push and open the PR.**

```bash
.venv/bin/pytest tests/ -q
git push -u origin perf/chunked-diarization
gh pr create --title "perf: chunked parallel diarization (quadratic cost, split the clustering)" --body "..."
```

PR body must include: the measurement table that killed the six earlier hypotheses (from the spec), the quadratic-scaling evidence, the architecture in three bullets, the one-to-one stitching constraint and *why* it exists, the calibration table with DER + speedup per arm, the chosen default, and the explicit statement that the single-pass path is unchanged and remains the fallback. End with the standard Claude Code attribution line.

**Deferred (explicitly):** using the overlap region to verify stitching rather than only for context; GUI exposure of the chunk flag; chunking anything other than diarization; evaluating a different diarizer (NeMo / paid pyannote API).
