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

2. **Reconciler** — `src/speaker_reconcile.py`, pure, **extracted from `src/vibevoice.py`
   rather than newly written** (see the plan's REVISION section): `reconcile_chunks(chunks,
   embedding_threshold, min_embedding_speech_seconds, label_prefix)`. It matches
   **temporally first** — speakers whose turns physically overlap inside the shared overlap
   region — and only then by centroid similarity, greedy highest-first against a
   `used_globals` set. Global centroids update as duration-weighted running means, and
   `_ownership_bounds` assigns each second of audio to exactly one window at the overlap
   midpoint.

   Matching is **one-to-one per chunk**: two speakers a chunk's own clustering called
   distinct are distinct people and must never collapse into one global speaker (the
   identity-collision lesson from the `interview-chris-swanson` conflation). Greedy
   highest-first with a used-set satisfies that, and — unlike the optimal-assignment
   version this spec originally proposed — it cannot displace a perfect match onto a worse
   global in pursuit of a higher total similarity. Reusing the existing reconciler also
   brought guards a new module lacked: non-finite embedding rejection and a
   `MIN_EMBEDDING_SPEECH_SECONDS = 3.0` floor so thin evidence never sets a voiceprint.
   Temporal matching turned out to supply 7–19 matches per meeting that centroid
   similarity alone would have missed, so this was the build's biggest quality decision.

3. **Orchestrator** — chunked branch in `src/modal_compute.run_diarization`: compute
   windows from the local audio duration, fan out with Modal `.starmap` (concurrent),
   stitch, then run the **existing** `merge_similar_speakers` over the stitched result so
   within-meeting fragmentation is handled exactly as today. Returns the same
   `(segments_data, embeddings)` contract as the single-pass path — a drop-in swap.

Embeddings stay in the same space (same `pyannote/embedding` model, same whole-window
inference), so voice-profile enrollment and identification are unaffected.

## Contract and configuration

- `config.DIARIZE_CHUNK_MINUTES` (**60 as calibrated** — was 0/off until the gate passed),
  `config.DIARIZE_CHUNK_OVERLAP_SECONDS` (60), and
  `config.DIARIZE_CHUNK_STITCH_THRESHOLD` (**0.50 as calibrated**; deliberately separate
  from `speaker_reconcile.EMBEDDING_MATCH_THRESHOLD = 0.75`, which VibeVoice still uses).
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

## Calibration results (2026-08-01/03) — SHIPPING ENABLED at 60 min / 0.50

Method: for each meeting the **single-pass output is the reference** (the honest
question is "does chunking change what we ship", not "is pyannote correct"), scored with
`bench.score.calculate_der`. `scripts/sweep_chunk_thresholds.py` pays for the GPU chunk
work once per chunk size, caches the payloads, and then re-stitches locally across a
threshold grid — so the whole threshold search costs no GPU at all.

### The chosen configuration

`DIARIZE_CHUNK_MINUTES = 60`, `DIARIZE_CHUNK_STITCH_THRESHOLD = 0.50`.

| meeting | audio | ref speakers | DER | speakers | drift | slowest window | single-pass | speedup |
|---|---|---|---|---|---|---|---|---|
| May 6 | 244 min | 42 | **0.0589** | 43 | **+2.4%** | 110s | 3586s | **33×** |
| June 10 | 298 min | 41 | **0.0439** | 49 | +19.5% | 111s | 7100s | **64×** |
| July 29 | 82 min | 14 | n/a — **does not chunk** | — | — | — | 584s | 1× |

At 60-minute windows a meeting under ~90 minutes falls through to a single window, i.e.
literally today's single-pass path with byte-identical output. Chunking therefore engages
only above ~90 minutes, which is exactly where the single-pass cost is intolerable, and
short meetings carry zero risk. July 29's row is deliberately marked n/a rather than
"pass": no chunking happens there, so counting it as passing evidence would be vacuous.

### Why 0.50, and why not lower

Same-person centroids score as low as ~0.55 **across** windows, because a per-window
centroid averages over far fewer turns than a whole-meeting one. VibeVoice's inherited
0.75 therefore fragments badly (June 10: 56 speakers vs 41). Sweeping down:

| June 10, 60-min | 0.75 | 0.65 | 0.60 | 0.55 | **0.50** | 0.45 |
|---|---|---|---|---|---|---|
| DER | .0681 | .0542 | .0542 | .0469 | **.0439** | .0555 ↑ |
| speakers | 56 | 53 | 53 | 50 | **49** | 47 |

DER bottoms at 0.50 and climbs again at 0.45 while the speaker count keeps falling — that
is the **conflation cliff**: genuinely different people beginning to merge. 0.50 is the
bottom of that U on the hardest meeting, and July 29's independent optimum landed at
0.55, so the value is not fitted to one meeting.

The two error modes are deliberately **not** treated as equivalent. Fragmentation (one
person as two speakers) surfaces as an extra unnamed speaker that the GUI review gate
catches. Conflation (two people as one) silently misattributes quotes — the
identity-collision failure this repo has already been burned by. So every judgment call
here errs toward the higher threshold.

### What the sweep also settled

- **30-minute windows fail everywhere on long meetings** (June 10 best: DER 0.128 at any
  threshold). DER improves with window size; drift worsens with it. The two gates pull in
  opposite directions, and 60 min is where both clear.
- **`merge_similar_speakers` contributes nothing post-reconcile** — identical results at
  merge thresholds 0.80/0.75/0.70. Everything mergeable is already handled by
  reconciliation; the call is retained only for parity with the single-pass path.
- **Temporal matching earns its place**: 7–19 matches per meeting come from turn overlap
  in the seam region, a signal per-window centroids cannot provide. Reusing vibevoice's
  reconciler (which already had it) rather than the purpose-built Hungarian stitcher was
  the single biggest quality decision in this build.

### Residual risk, accepted knowingly

June 10's +19.5% drift is the weak point: 49 speakers where single-pass found 41, i.e. ~8
extra labels for a reviewer to name or merge on a 5-hour meeting. May 6 shows +2.4% on a
244-minute meeting, so this is the outlier rather than the norm, and the cost is reviewer
time rather than published error — the review gate stands between chunked output and
publication. Watch the speaker count on the next long meeting; if drift regresses toward
June 10's figure routinely, the next step is architectural, not another threshold: chunk
for segmentation, then re-cluster identity **globally over per-turn embeddings** (measured
at ~20s for 2811 segments), which replaces window-centroid matching with the global view
that single-pass gets for free.

## Follow-up SHIPPED 2026-08-03 — the architectural fix named above was built

The final paragraph's proposal ("chunk for segmentation, then re-cluster identity **globally
over per-turn embeddings**") is implemented in `src/global_identity.py`; see
`docs/superpowers/specs/2026-08-03-global-identity-clustering-design.md`. Per-window centroid
matching is replaced by one constrained agglomerative clustering over per-turn embeddings at
full-meeting scope, with same-window distinctness as a hard cannot-link constraint. The chunk
worker now returns the per-turn vectors it was already computing (no extra GPU) and filters
non-finite vectors per turn — 7 of June 10's 86 window-local centroids had been NaN-poisoned by
a single bad turn and so could never match at any threshold.

Result on the two meetings calibrated here: June 10 **49 → 41 speakers** (DER 0.0439 → 0.0060,
people fragmented against the human-reviewed transcript 6 → 1, which is exactly what
single-pass itself fragments), May 6 **43 → 41** (DER 0.0589 → 0.0210), both at the same
65× / 33× speedup. The +19.5 % drift recorded above as the residual risk is now 0.0 %.

`DIARIZE_CHUNK_MINUTES` is therefore **60 (enabled)** as of that change. The residual-risk
paragraph below is kept as the historical record of why it shipped OFF first.
