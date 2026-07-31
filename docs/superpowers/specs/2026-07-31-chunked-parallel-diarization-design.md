# Chunked Parallel Diarization — Design

**Date:** 2026-07-31
**Status:** Approved (user-directed after the measurement campaign below)
**Supersedes:** the A100 approach in closed [PR #130](https://github.com/EmpoweredVote/on-the-record/pull/130)

## Problem, as measured (not assumed)

Diarization dominates pipeline wall-clock: 46 min (July 22, 3h), 109 min (June 10, 5h).
Instrumenting the Modal function (`[timing]` prints + `probe_diarize_tuning`, both on the
closed PR's branch) produced these findings, each of which **killed a candidate fix**:

| Hypothesis | Measurement | Verdict |
|---|---|---|
| Serial per-segment embedding loop | 20.4s of 4125s | dead |
| GPU too slow | L4 → A100: no change | dead |
| CPU starvation | default → `cpu=16`: minor/confounded | dead |
| Tiny embedding batch | already 32 by default | dead |
| Model on CPU | both models on `cuda` | dead |
| Network-volume audio reads | local copy 561.5s vs volume 619.8s (copy = 0.1s) | dead |

The pyannote `embeddings` hook step is **~99% of pipeline wall-clock** in every run, and
the truncation test showed why nothing helps — **cost is ~quadratic in duration**:

| slice of the same June 10 audio | embeddings | s per audio-min | speakers |
|---|---|---|---|
| first 60 min | 298.8s | 4.98 | 15 |
| first 120 min | 1357.6s | 11.31 | 22 |
| full 298 min | ~6500s | 21.8 | 41 |

2× duration → 4.5× cost, i.e. cost ∝ duration^1.8–1.9. pyannote's hook exposes only four
step names, so that bucket spans embedding extraction **plus agglomerative clustering** —
superlinear in the number of embeddings, CPU-bound, single-threaded. That single fact
explains every null result at once.

## Approach: split the clustering problem, don't buy bigger hardware

Because cost is quadratic in the number of embeddings per clustering pass, splitting a
meeting into N windows cuts total work by ~N (each pass is (1/N)² of the cost, N times
over) **and** the windows run concurrently. Projected for a 5-hour meeting: one 6500s pass
→ five ~299s passes ≈ 5–6 min wall-clock (~20×). Sequential chunking alone would still be
~4× — the win is shrinking n, not merely parallelism.

The cost of splitting is a new correctness problem: **speaker labels are only meaningful
within a chunk**, so they must be unified globally. That, not the speedup, is where the
engineering care goes.

## Architecture

Three units. Orchestration stays local (the existing division of labour in
`src/modal_compute.py`: "uploads audio, dispatches GPU work, returns results in the format
the local pipeline expects"), so the Modal side stays a pure stateless worker and the
stitching logic is locally testable without Modal.

1. **Chunk worker** — `bench/modal_app.diarize_chunk_window(meeting_id, start_s, end_s,
   overlap_s)`, `gpu="L4"` (measured: tier is irrelevant; take the cheap one). Reads only
   its window out of the volume WAV via `soundfile`'s `start`/`frames` (no re-upload, no
   full-file load), diarizes `[start-overlap, end+overlap]` so the segmentation model has
   context across the seam, then **clips returned turns to `[start, end)`** and converts
   to absolute meeting time. Also returns, per chunk-local speaker: centroid embedding and
   total speech seconds (needed for weighting and for the representative choice at merge).

2. **Stitcher** — `src/diarize_stitch.py`, pure: `stitch_chunks(chunks, threshold) ->
   (turns, centroids, log)`. Walks chunks in time order maintaining a global speaker table.
   For each chunk it builds the cosine-similarity matrix between chunk-local centroids and
   global centroids and solves a **one-to-one assignment** (`scipy.optimize.
   linear_sum_assignment`), accepting only pairs at or above `threshold`; unmatched locals
   become new global speakers. Global centroids update as duration-weighted running means.

   One-to-one is not an optimization — it is the correctness constraint. Two distinct
   speakers *within* a chunk are distinct people by that chunk's own clustering, so they
   must never collapse into one global speaker (the identity-collision lesson from the
   `interview-chris-swanson` conflation). Greedy nearest-match can violate this; the
   assignment solve cannot.

3. **Orchestrator** — chunked branch in `src/modal_compute.run_diarization`: compute
   windows from the local audio duration, fan out with Modal `.starmap` (concurrent),
   stitch, then run the **existing** `merge_similar_speakers` over the stitched result so
   within-meeting fragmentation is handled exactly as today. Returns the same
   `(segments_data, embeddings)` contract as the single-pass path — a drop-in swap.

Embeddings stay in the same space (same `pyannote/embedding` model, same whole-window
inference), so voice-profile enrollment and identification are unaffected.

## Contract and configuration

- `config.DIARIZE_CHUNK_MINUTES` (default **0 = off**, single-pass) and
  `config.DIARIZE_CHUNK_OVERLAP_SECONDS` (default 60), plus
  `config.CHUNK_STITCH_THRESHOLD` (default 0.80, matching `SPEAKER_MERGE_THRESHOLD`).
- CLI: `run_local.py --diarize-chunk-minutes N` overrides the config for one run.
- Meetings shorter than one chunk fall through to the existing single-pass path
  untouched — no behaviour change for short meetings.
- The default flips from 0 to the calibrated chunk size **only after the DER gate below
  passes**, in the same PR. Capability first, default second.

## Quality gate (this is the part that can fail)

There is no human-labelled reference RTTM for these meetings, so the honest question is
not "is chunked diarization good" but **"does chunking change what we already ship"**.
`bench/score.calculate_der` (pyannote.metrics) already computes DER between two RTTMs, and
`_write_result` already writes per-model RTTMs to the volume.

Gate: run single-pass and chunked over the same meeting, take the **single-pass output as
the reference**, and require:

- **DER(chunked, single-pass) ≤ 10%** on July 29 (82 min, 14 speakers, human-reviewed) and
  on June 10 (298 min, 41 speakers — the adversarial case: many speakers, many boundaries).
- **Speaker count within ±20%** of single-pass (catches stitch fragmentation, the expected
  failure mode: one person becoming two global speakers because their two appearances
  didn't match).
- Chunk-boundary spot check: for each seam, the turns immediately before and after are
  inspected in the report — a person speaking across a seam must not change label.

A run that fails the gate leaves `DIARIZE_CHUNK_MINUTES = 0` and reports why; the
capability still ships, disabled. Sweeping 30/45/60-minute chunks picks the default by DER
first, wall-clock second.

## Risks

- **Stitch fragmentation** (most likely): a speaker's two appearances don't match, so they
  publish as two speakers. Not wrong data — extra review work — and the existing
  `merge_similar_speakers` post-pass catches the highly-similar cases. The DER +
  speaker-count gate is the detector.
- **Thin-evidence centroids**: a speaker with a few seconds in one chunk gets a noisy
  centroid and matches badly. Mitigated by duration-weighted centroids; smaller chunks
  make it worse, which is a reason not to over-split.
- **Cross-chunk conflation** (worst case, quality-destroying): two different people
  merged. Guarded structurally by the one-to-one assignment plus the similarity threshold,
  and detected by DER.
- **Modal concurrency**: many simultaneous containers each pay model-load startup
  (~20–40s from the volume HF cache). Real but small next to the minutes saved; it does
  set a floor that makes very small chunks pointless.

## Out of scope

Whisper/transcription chunking (already free via CATS VTT); using the overlap region to
*verify* stitching rather than only to provide context; a different diarizer (NeMo, paid
pyannote API); GUI exposure of the chunk flag. The A100/`cpu=16` changes from PR #130 are
reverted — the probe and `[timing]` instrumentation are kept.
