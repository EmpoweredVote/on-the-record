# Chunk for Segmentation, Cluster Identity Globally — Design

**Date:** 2026-08-03
**Status:** Approved (user-directed, forks resolved 2026-08-03)
**Branch:** `perf/global-identity-clustering`, stacked on `perf/chunked-diarization` ([PR #141](https://github.com/EmpoweredVote/on-the-record/pull/141))
**Builds on:** `docs/superpowers/specs/2026-07-31-chunked-parallel-diarization-design.md` — its
"Residual risk, accepted knowingly" section names this change as the next step.

## The problem this fixes

Chunked diarization works and is 33–64× faster, but it ships **disabled by default** for one
reason: it produces more speaker labels than there are people (June 10: 49 vs 41; May 6: 43
vs 42). That is not cosmetic. `identify._dedupe_identities` treats two labels resolving to
one person as a mis-identification and demotes all but the highest-confidence one to
unnamed + `needs_review`, so an unmerged fragment publishes a real person's remarks
attributed to nobody if the reviewer misses it.

Cross-window identity currently rests on **per-window centroids**: one vector per
(window, local speaker), averaged over that window's turns, matched sequentially against a
running global mean, greedy highest-first, one-to-one per window
(`src/speaker_reconcile.reconcile_chunks`). Segmentation is not the problem — the
calibration measured missed speech 0.0007 and false alarm 0.0003. Identity is the problem.

## What was measured before designing (no GPU spent)

June 10's `transcript_named.json` is **human-reviewed**: 871 `voice_profile` +
184 `human_review` segments, 40 distinct people, no name mapping to two labels. Mapping
each cached window-local speaker to the reviewed person that owns most of its canonical
speech gives a real reference, not a similarity-to-single-pass proxy.

| | June 10 @60m | July 29 @45m | July 29 @30m |
|---|---|---|---|
| window-local nodes | 86 | 20 | 29 |
| **distinct reference people they map to = ceiling** | **40** | **13** | **13** |
| same-window collisions (would break cannot-link) | **0** | **0** | 1 (a 5.7 s fragment) |
| impure nodes (<75 % one person) | 1/86 | 0/20 | 1/29 |
| nodes with <3 s canonical speech | **0** | **0** | **0** |

Three consequences, each of which shaped a decision below:

1. **The ceiling is 40 on June 10** — at or below single-pass's 41. Grouping window-local
   nodes is sufficient to eliminate fragmentation entirely; the 49 is *purely* cross-window
   matching failure. This design cannot be blamed on within-window over-splitting.
2. **"Two locals in one window are two different people" is measured-clean at 60-minute
   windows** (0 violations on both meetings that have a named reference). That makes it
   usable as a hard *cannot-link* constraint, which is what structurally preserves the
   anti-conflation guarantee the current one-to-one greedy walk provides. At 30-minute
   windows it is violated once, which is an additional argument for 60.
3. **There are no thin nodes at 60 minutes.** The `MIN_EMBEDDING_SPEECH_SECONDS = 3.0`
   floor and the "do short turns get clustered or assigned by adjacency" question are both
   **moot at this window size** — every node has ≥3 s of canonical speech. Short *turns*
   still exist; they inherit their node's label and need no separate rule.

Scoring all 2435 cross-window centroid pairs against the reviewed names shows why centroid
matching fails, and that the failure is not a threshold choice:

- **7 of 86 node centroids are non-finite.** `pyannote/embedding` returns NaN on some turns
  and `diarize_chunk_window` averages turn vectors **without filtering** — one bad turn
  poisons the whole node. (`pipeline_extract_embeddings` does filter; the chunk worker was
  not given the same guard.) Those 7 nodes cannot match by embedding at *any* threshold, so
  they fragment by construction. This is a bug, not a tuning problem.
- Same-person pairs: median 0.773, p05 0.321. Different-person pairs: p95 0.274, **max
  0.495**. At the shipped 0.50 threshold: **83.3 % same-person recall with 0 false
  positives**.

So ~17 % of same-person seams are missed at a threshold that has *zero* measured false
positives, and the discriminative boundary sits near 0.32–0.45 — yet the sweep found DER
climbing at 0.45. Pair-level signal has headroom that **greedy one-to-one matching against
a running mean cannot safely exploit**: a single bad assignment early cascades, and the
running mean drifts. Global constrained clustering is what makes that headroom safe.

## Verified cost premise

Clustering per-turn vectors at full-meeting scope is not a new bottleneck. Measured on the
real June 10 shape (2745 turns, 86 nodes, 512-dim) and extrapolated shapes:

| turns | nodes | similarity matrix | node-pair average-linkage |
|---|---|---|---|
| 2745 (June 10, 5 h) | 86 | 12 ms (30 MB) | 3 ms |
| 6000 (~10 h) | 180 | 54 ms (144 MB) | 10 ms |
| 12000 (~20 h) | 360 | 203 ms (576 MB) | 77 ms |

A deliberately naive Python constrained-merge loop costs 0.28 s at 86 nodes and 2.8 s at
180. The quadratic blowup in pyannote is over its **dense sliding-window** embeddings
(hundreds of thousands of frame vectors), not per-turn ones. Memory is the only scale
caveat: the turn×turn matrix is O(n²), so above ~20 k turns the implementation must
aggregate per node pair in blocks instead of materialising the full matrix. That is a guard,
not a present concern.

## Architecture

Chunking stays exactly as it is **for segmentation** — the part where the quadratic cost
lives and where accuracy is already near-perfect. Only cross-window identity changes.

### 1. Chunk worker — stop discarding what it already computes

`bench/modal_app.diarize_chunk_window` already loops per turn through an embedding model
and then averages. The loop is the per-turn extraction; only the averaging throws the
information away. Changes (thin, Modal-bound, untested per house convention):

- Return the **per-turn vectors**, aligned to the returned `turns` list by index, as
  `turn_embeddings: {model_id: {"dim": int, "dtype": "float32", "b64": str, "turn_indices": [int]}}`.
  base64 float32 keeps the payload JSON-cacheable by the existing sweep harness with no
  precision loss (June 10's largest window, 657 turns: ≈1.8 MB at 512-dim, ≈0.9 MB at
  256-dim, on top of ~0.2 MB of turns).
- **Filter non-finite per turn** before including a vector, and keep filtering before the
  centroid average. This alone removes the 7 poisoned nodes.
- Compute vectors under **both** embedders in one call (`embedders` parameter, default the
  shipping one): `pyannote/embedding` (512-dim, today's chunk/single-pass space) and
  `pyannote/wespeaker-voxceleb-resnet34-LM` (256-dim, `config.EMBEDDING_MODEL`, what
  pyannote 3.1 clusters on internally and what voice profiles are built on). Segmentation
  GPU is paid once; the second embedder costs ~20–40 s per window. The sweep decides.
- `centroids` / `speech_seconds` / `turns` / `window_*` keep their current meaning, so the
  existing sequential path and every cached payload stay valid.

Turn vectors are computed over the **canonical span only**, exactly as centroids are today:
overlap audio informs matching but never shapes a voiceprint a neighbouring window owns.

### 2. `src/global_identity.py` — one global identity pass (pure, unit-tested)

Reuses `src/speaker_reconcile`'s `ChunkWindow` / `LocalTurn` / `ChunkResult` /
`StableTurn` / `ReconciliationResult` / `_overlap_seconds` / `_ownership_bounds` and returns
the same `ReconciliationResult`, so it is a one-call swap in the orchestrator. No second
copy of ownership or overlap logic — the repo has already been burned by duplicated
stitchers.

```
cluster_global_identities(
    chunks: list[ChunkResult],
    turn_vectors: dict[tuple[int, int], np.ndarray],   # (chunk_index, turn_index) -> vector
    *, threshold: float, linkage: str = "average",
    label_prefix: str = "SPEAKER_",
) -> ReconciliationResult
```

1. **Nodes.** One per (window index, local speaker). Each carries its canonical-span turns
   and their vectors (non-finite already dropped upstream; dropped again defensively).
   `turn_indices` maps each vector row to its index in the payload's `turns` list; turns
   lying wholly in the overlap region have no vector by design. A node with **no** usable
   vector can still be joined by a seam must-link; if nothing links it, it becomes its own
   global speaker — the same conservative outcome as today, and a fragmentation (reviewable)
   rather than a conflation (silent).
2. **Must-link from the seam.** For adjacent windows, summed temporal overlap between
   locals inside the shared overlap region, greedy highest-first, **skipping any union that
   would violate a cannot-link**. This is the 7–19 matches per meeting that centroid
   similarity cannot see, and it is retained deliberately.
3. **Cannot-link.** Two nodes from the same window may never land in one cluster,
   propagated through the union-find so the constraint survives transitive merges.
4. **Constrained agglomerative merge.** Cluster distance = 1 − mean pairwise cosine
   similarity over the two clusters' turn vectors (**average linkage**; `complete` and
   `centroid` are sweepable alternatives). Repeatedly merge the closest admissible pair
   while distance < 1 − `threshold`. Cost is milliseconds at real scale.
5. **Labels.** `f"{label_prefix}{n:02d}"` assigned by **descending total speech time**, so
   numbering is deterministic and the chair lands at `SPEAKER_00`.
6. **Turns.** Clipped through the existing `_ownership_bounds`, so each second of audio
   belongs to exactly one window — unchanged from today.
7. **Centroids.** Duration-weighted mean of the cluster's own **turn** vectors — a strictly
   better voiceprint than today's average-of-per-window-averages, and it replaces the
   recomputation currently done in `stitch_chunk_payloads`.
8. **Diagnostics.** `temporal_matches` / `embedding_matches` / `new_speakers` — the same
   three keys the operator print in `stitch_chunk_payloads` already consumes, so that print
   needs no change — plus `cannot_link_blocks`, `clusters`,
   `nodes`, per-cluster window span, and **`margin`** — the distance of the first *rejected*
   merge. Margin is the operator's readout of how close a run came to the conflation cliff;
   a run that merged right up to the threshold is worth a look even when the count is right.

### Why a distance threshold rather than a cluster count

`threshold`, not silhouette or eigengap or pyannote's own estimate:

- Pyannote itself is threshold-based agglomerative with unknown speaker count; a
  K-selection criterion would be a *new* mechanism to calibrate, not a reused one.
- Council meetings are severely unbalanced — the chair holds ~40 % of turns while a public
  commenter holds 30 s. Silhouette and eigengap both degrade badly on that shape.
- A threshold composes with cannot-link; a target K does not (a constrained solution at
  exactly K may not exist).
- Pyannote's per-window counts do bound the answer, and are reported as diagnostics:
  `max(per-window count) ≤ K ≤ sum(per-window counts)` (June 10 @60 min, per-window counts
  15/16/26/13/17: **26 ≤ K ≤ 87**, truth 40). Bounds, not a criterion.

### 3. Orchestrator — a branch, with the old path intact

`src/modal_compute.stitch_chunk_payloads` gains one branch: when every payload carries
turn embeddings **and** `config.DIARIZE_CHUNK_IDENTITY == "global"`, build the turn-vector
map and call `cluster_global_identities`; otherwise run today's `reconcile_chunks` path
unchanged. Two things fall out of that shape:

- Payloads cached before this change still stitch, so old-vs-new A/B in the sweep costs no
  GPU.
- `DIARIZE_CHUNK_IDENTITY` (`"global"` | `"sequential"`) is a real escape hatch if global
  clustering ever misbehaves on a meeting, and it is what the sweep switches between.

`merge_similar_speakers` stays wired exactly as now (it was measured to contribute nothing
post-reconcile). The sweep measures it again rather than assuming; if it *changes* anything
post-clustering it is a conflation risk, because it has no cannot-link constraint.

### Config

- `DIARIZE_CHUNK_IDENTITY = "global"` — new.
- `DIARIZE_CHUNK_CLUSTER_THRESHOLD` — new, calibrated. Kept separate from
  `DIARIZE_CHUNK_STITCH_THRESHOLD` (0.50, sequential path) and from
  `speaker_reconcile.EMBEDDING_MATCH_THRESHOLD` (0.75, VibeVoice): three different
  matchers over three different signals, and collapsing them is how a tuned value gets
  silently applied where it was never measured.
- `DIARIZE_CHUNK_LINKAGE` — new, calibrated (`average` | `complete` | `centroid`).
- `DIARIZE_CHUNK_EMBEDDER` — new, calibrated; whichever embedder wins.
- `DIARIZE_CHUNK_MINUTES` flips 0 → 60 **only if the gate passes and fragmentation is
  actually gone**. `DIARIZE_CHUNK_OVERLAP_SECONDS` stays 60.

If the winning embedder is wespeaker, the chunked path returns **256-dim
profile-compatible** centroids. That is a bonus, not a requirement: `run_local`'s
"stale embeddings … re-extracting" guard (`run_local.py:1409`) currently fires on every
chunked run that has stored profiles, re-embedding every segment locally on CPU.
`PROFILE_SCHEMA_VERSION` is untouched either way — the guard exists precisely to make this
safe, and single-pass behaviour does not change.

## Verification

Reuses `scripts/sweep_chunk_thresholds.py` — same pay-GPU-once-and-sweep-locally method,
same cached artefacts. **Cached single-pass references are not re-derived** (June 10 7100 s,
May 6 3586 s, July 29 584 s already on disk); per-turn embeddings are absent from the
cached chunk payloads, so chunk payloads *are* re-fetched: June 10 + May 6 at 60 min
(~10 min GPU) and July 29 at 45 min (~2 min) as an independent threshold check, cached under
new filenames so the existing caches survive for A/B.

Script gains `--identity global|sequential|both`, `--linkage`, `--embedder`, `--cluster`
(threshold grid). Everything after the one fetch is free.

### Gate — unchanged, and not to be relaxed

- **DER ≤ 0.10** against the cached single-pass reference, **and**
- **speaker count within ±20 %** of single-pass,
- on **both** long meetings (June 10, May 6).

### Target — stronger than the gate

- Speaker count **at or below** single-pass (41 / 42), because eliminating fragmentation is
  the entire point. The measured ceiling is 40 on June 10.
- **Zero conflation** against the named reference on June 10.

### Real accuracy check, not a similarity check

DER against single-pass measures *change*, not correctness — single-pass is not verified
ground truth. New pure module `bench/identity_score.py` scores against June 10's and July
29's **human-reviewed named** transcripts:

- map each hypothesis label to the reviewed person owning most of its speech;
- **fragmentation** = reviewed people split across ≥2 hypothesis labels (seconds-weighted,
  with a small floor so boundary noise is not counted);
- **conflation** = hypothesis labels spanning ≥2 reviewed people;
- **named DER**: reviewed names as the reference annotation.

Single-pass is scored the same way, so the report reads "chunked+global vs single-pass vs
the ceiling" on the same axis. This is reporting *added alongside* the gate, never a
substitute for it.

### Seam spot-check

Per seam, list the turns within ±10 s and whether a speaker's label changes across it, plus
the reconciler's own per-seam temporal-match count. Reported for the winning configuration.

## Risks

- **Cannot-link is wrong on some meeting.** Measured clean at 60 min on two meetings; one
  violation appeared at 30 min. Consequence is bounded — a violated cannot-link causes
  *fragmentation* (the reviewable error), never conflation. `cannot_link_blocks` in the
  diagnostics makes it visible.
- **A lower threshold conflates.** The reason the sequential path could not go below 0.50.
  Different-person cross-window centroid similarity maxes at 0.495 on June 10, cannot-link
  bounds it structurally, and the `margin` diagnostic exposes near-misses. The threshold is
  still calibrated on two meetings and cross-checked on a third, and the error asymmetry
  (fragmentation reviewable, conflation silent) breaks every tie toward the higher value.
- **The winning embedder differs from single-pass's.** Handled by the existing dimension
  guard; single-pass untouched.
- **Payload size / Modal return limits.** Up to ~1.8 MB (512-dim) + ~0.9 MB (256-dim) per
  window as base64 float32. Verified on the first real fetch; if Modal objects,
  the fallback is writing an `.npy` to the volume and returning its path.
- **O(n²) memory above ~20 k turns.** Guarded by a documented block-aggregation fallback;
  no current meeting is close.

## Out of scope

Routing VibeVoice's 50-minute windows through the global clusterer (it has its own tuned
0.75 and its own regression tests — a separate change with its own calibration); changing
single-pass diarization in any way; re-sweeping chunk size below 60 min; GUI exposure of the
new knobs; whisper/transcription chunking.
