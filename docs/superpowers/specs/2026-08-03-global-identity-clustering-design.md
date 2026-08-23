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
  poisons the whole node — `pipeline_extract_embeddings` does filter, but the chunk worker was
  never given the same guard. Those 7 nodes cannot match by embedding at *any* threshold, so
  they fragment by construction. This is a bug, not a tuning problem. (Independently confirmed
  during review: replaying the cached June 10 payloads through the new consumer dropped 161 of
  2541 vectors as non-finite, traceable to exactly those 7 nodes.)
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
`StableTurn` / `_overlap_seconds` / `_ownership_bounds`, and returns a
`GlobalIdentityResult` — `ReconciliationResult`'s `turns` + `diagnostics` **plus**
`centroids`, since it can build voiceprints from its own per-turn vectors instead of making
the orchestrator re-derive them from per-window averages. No second copy of ownership or
overlap logic — the repo has already been burned by duplicated stitchers.

```
cluster_global_identities(
    chunks: list[ChunkResult],
    turn_vectors: dict[int, dict[int, np.ndarray]],   # chunk_index -> turn_index -> vector
    *, threshold: float, linkage: str = "average",
    label_prefix: str = "SPEAKER_",
) -> GlobalIdentityResult
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
4. **Constrained agglomerative merge.** Cluster similarity = mean pairwise cosine over the
   two clusters' turn vectors (**average linkage**; `complete`, the worst pair, and
   `centroid` are sweepable alternatives). Repeatedly merge the single most similar
   admissible pair while its similarity ≥ `threshold` — same similarity convention as every
   other threshold in `src/config.py`. Node-pair aggregates (sum, count, min, gram) are
   precomputed once from the turn matrix, which makes each linkage exact (verified to 1e-9
   against direct turn-vector computation) and the merge loop nodes-sized rather than
   turns-sized. Measured at June 10's scale (87 nodes, 2745 turns): 23 ms to precompute,
   ~500 ms for the merge loop. The loop is O(nodes³) because it rescans every cluster pair
   per merge; negligible against GPU diarization at this size, but it would want
   per-row caching above ~300 nodes.
5. **Labels.** `f"{label_prefix}{n:02d}"` assigned by **descending total speech time**, so
   numbering is deterministic and the chair lands at `SPEAKER_00`.
6. **Turns.** Clipped through the existing `_ownership_bounds`, so each second of audio
   belongs to exactly one window — unchanged from today.
7. **Centroids.** Mean of the cluster's own **turn** vectors (unit-normalised at node build,
   so this is turn-count-weighted rather than duration-weighted) — still a better voiceprint
   than today's average-of-per-window-averages, and it replaces the recomputation currently
   done in `stitch_chunk_payloads`. Duration weighting is a deliberate deferral: it needs
   turn durations threaded alongside the vector rows, and centroid quality feeds
   voice-profile matching downstream rather than anything this change's gate measures. Worth
   revisiting if profile match rates move.
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
- **fragmentation** = reviewed people split across ≥2 hypothesis labels;
- **conflation** = hypothesis labels spanning ≥2 reviewed people;
- **named DER**: reviewed names as the reference annotation.

Both error modes need a floor, because diarization routinely bleeds a word across a turn
edge. A fixed seconds floor is not enough: at 3.0 s, single-pass on June 10 scores 5
"conflated" labels of which 4 are 3–14 s bleeds against a person holding 500–1700 s of the
same label. So a side counts only if it holds **≥ 3.0 s and ≥ 2 % of that label's (or that
person's) attributed speech**, applied symmetrically to both axes, and the largest minority
share is reported alongside each count so a 4 s bleed can never be mistaken for a real
merge. The floor was set from the *reference's own* behaviour before any new-path result
existed, and single-pass is scored under the identical floor.

**The measured bar (June 10, this floor):** single-pass produces **41 labels for 40 real
people, 1 person fragmented** (Zulich, split 265.9 s / 15.9 s — a 5.6 % minority share) and
**0 conflated**. That is what chunked+global has to match or beat. This is reporting *added
alongside* the gate, never a substitute for it.

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

## Calibration results (2026-08-03) — SHIPPING ENABLED at 60 min / average / 0.32 / wespeaker

Method: `scripts/sweep_chunk_thresholds.py`. Chunk payloads were re-fetched once per meeting
with per-turn embeddings under **both** candidate embedders (segmentation GPU paid once, both
vector sets cached, ~11 MB for June 10) and cached under `calibration_chunks_60min_turnemb.json`
so the pre-existing caches survived. The single-pass references were **reused from cache** —
no reference arm was re-derived. Every threshold, linkage and embedder after the one fetch
cost nothing.

### The chosen configuration

`DIARIZE_CHUNK_MINUTES = 60`, `DIARIZE_CHUNK_IDENTITY = "global"`,
`DIARIZE_CHUNK_LINKAGE = "average"`, `DIARIZE_CHUNK_CLUSTER_THRESHOLD = 0.32`,
`DIARIZE_CHUNK_EMBEDDER = pyannote/wespeaker-voxceleb-resnet34-LM`.

| meeting | audio | DER | speakers | base | drift | fragmented | conflated | slowest window | single-pass | speedup |
|---|---|---|---|---|---|---|---|---|---|---|
| June 10 | 298 min | **0.0060** | **41** | 41 | **0.0%** | **1** (= single-pass's own) | **0** | 109s | 7100s | **65×** |
| May 6 | 244 min | **0.0210** | **41** | 42 | **−2.4%** | reference not trustworthy | — | 109s | 3586s | **33×** |
| July 29 | 82 min | n/a — **does not chunk** at 60 min | — | — | — | — | — | — | 584s | 1× |

Both gates clear on both long meetings, and the stronger target clears too: the speaker count
is **at or below** single-pass on both, not above. What blocked the default is gone.

| June 10 | shipped sequential @0.50 | this change | single-pass |
|---|---|---|---|
| labels for 40 real people | 49 | **41** | 41 |
| people fragmented | **6** | **1** | 1 |
| people conflated | 0 | **0** | 0 |
| DER vs single-pass | 0.0439 | **0.0060** | — |

The one remaining fragmented person on June 10 is Zulich, split 265.9 s / 15.9 s — **the same
person single-pass itself splits**, at a 3.2 % minority share. This path is therefore at
parity with whole-meeting clustering on real identity accuracy, at 65× the speed.

### Why 0.32, and why the scale is not 0.50

Per-turn average linkage lives on a lower scale than per-window centroid similarity, because
it averages over pairs of *individual* turn embeddings rather than over two window means. On
June 10's reviewed reference, same-person cross-window node pairs score p05 0.273 / median
0.427; different-person pairs top out at **0.322**. The grid inherited from the centroid path
(0.45–0.75) was therefore entirely above the operating range and produced 58–79 speakers —
the first sweep was aborted and re-run on a measured grid rather than tuned blindly.

| June 10, 60 min, average | 0.40 | 0.36 | **0.32** | 0.30 | 0.28 | 0.26 | 0.24 | 0.22 |
|---|---|---|---|---|---|---|---|---|
| DER | .0085 | .0085 | **.0060** | .0060 | .0150 | .0150 | .0163 | .0318 |
| speakers | 44 | 44 | **41** | 41 | 40 | 40 | 39 | 37 |
| people conflated | 0 | 0 | **0** | 0 | **1** | 1 | 2 | 4 |

**0.28 is a conflation cliff that two meetings agree on independently**: June 10 merges Paul
Gillard (140.4 s, 44.6 % of the label) into another speaker, May 6 merges Steve Volin
(168.5 s, 47.3 %). 0.32 is the top of the flat basin above it — chosen high because
fragmentation is reviewable and conflation is not. July 29 at 45 minutes is **flat across the
entire 0.24–0.40 grid**, so 0.32 is not fitted to the two gate meetings.

### What the sweep also settled, by measurement rather than argument

- **wespeaker beats pyannote/embedding decisively.** Separating same- from different-person
  cross-window pairs: wespeaker J=0.953 (90.3 % recall at 4 false pairs in 2810);
  pyannote/embedding J=0.919 at a 10× higher false rate and only 44 % recall at the same
  threshold. wespeaker is also profile-compatible, so chunked runs now return 256-dim
  centroids and no longer trip `run_local`'s re-extraction guard.
- **Complete linkage is unusable here.** A real person's *worst* turn pair is often
  anti-correlated (same-person median −0.125), so it merges almost nothing (recall 2–5 %).
- **Centroid linkage is worse than average.** At the most conservative threshold tested it
  already conflated 2 real people on June 10 (Hilary Martel, 30.3 %) and on May 6 (City Clerk
  Bolden, 42.4 %). Pooling turns into one mean discards the distribution that per-turn
  embeddings exist to provide.
- **The DER gate alone could not have defended this.** Every June 10 configuration passes DER
  ≤ 0.10 and ±20 % drift, *including* 0.22 with four real people conflated (DER 0.0318). The
  reviewed-names check is what discriminates. The gate was kept exactly as specified; the
  named check was added alongside it.
- **Seams are clean.** At the winning configuration every seam (60/120/180/240 min on June 10,
  45 min on July 29) has a speaker whose label is continuous across it. The seam report had to
  be fixed first: it was inspecting `window_start_s`, a full minute from where ownership
  actually transfers.
- **Cost is a non-issue.** Global clustering over 2745 turns / 87 nodes: 23 ms to precompute
  node-pair aggregates, ~500 ms for the merge loop.

### Residual risk, accepted knowingly

- **May 6's named reference is not trustworthy** and its fragmentation/conflation numbers are
  excluded from the accuracy claim: only 21 human-reviewed segments, **150 segments with no
  name at all** (each unnamed label becomes its own pseudo-person, so merging two of them
  scores as conflation by construction), and one name already split across two labels in the
  reference itself. May 6 is judged on the DER + speaker-count gate, which is what the gate
  specifies. June 10 (871 voice-profile + 184 human-review segments, 40 names, nothing
  unnamed) is the one real accuracy reference; July 29 (fully named, 13 people) supports it.
- **July 29's single 2.6 % conflation** (Stosberg, 3.4 s) sits just above the 2 % floor and is
  a boundary bleed, not a merge — single-pass scores one conflation there too.
- **The sequential path is now outside its calibrated regime by default.** Its 0.50 was
  measured on `pyannote/embedding` centroids, but new payloads carry wespeaker centroids. It
  remains only as an escape hatch and for stitching pre-existing caches; a true sequential
  baseline must come from the old cache (`--payload-suffix ""`), which is how the comparison
  table above was produced.
- **Centroids are turn-count-weighted, not duration-weighted** (see the architecture note).
  Untested effect on voice-profile match rates; revisit if they move.

## Post-calibration correction (2026-08-03, later the same day)

May 6's human review was **completed** after the calibration above ran, which promoted its
`transcript_named.json` from untrustworthy (21 human-review segments, 150 unnamed) to a full
reference: 852 `voice_profile` + 142 `human_review`, 40 labels, 40 names, **nothing unnamed**,
no name spanning two labels. Re-scoring May 6 on the real accuracy axis then exposed a defect
the DER gate could not see.

**Finding: the seam temporal must-link had no minimum-overlap floor.** `seed_clusters` accepted
any overlap `> 0`. Of May 6's 12 seam joins, the 10 correct ones overlapped **1.1–71.0 s**; the
2 that joined *different people* overlapped **0.6 s and 0.3 s**, and together they chained three
real people (Kerr → Toothman → Sturbaum) into one cluster. Because a must-link is applied
before, and independently of, the embedding threshold, no threshold could undo it — which is
exactly why May 6's conflation count sat at 4 across the entire 0.30–0.40 grid, and why the
shipped sequential path exhibits the same merge (`src/speaker_reconcile` also takes any
`score > 0`).

`MIN_SEAM_OVERLAP_SECONDS = 1.0`. A sub-second overlap is two windows disagreeing about a
boundary by a few hundred milliseconds, not evidence of one speaker; and dropping a weak join
is safe, because the pair can still merge on voice similarity — the signal that *should* decide
when temporal evidence is thin.

### Final measured state, all three meetings scored against completed human review

| meeting | reviewed people | single-pass | this change @0.32 | DER | drift |
|---|---|---|---|---|---|
| June 10 (298 min) | 40 | 41 labels, 1 fragmented, 0 conflated | **41 labels, 1 fragmented, 0 conflated** | 0.0060 | 0.0% |
| May 6 (244 min) | 40 | 42 labels, 2 fragmented, 1 conflated | **43 labels, 2 fragmented, 3 conflated** | 0.0087 | +2.4% |
| July 29 (82 min, 45-min windows) | 13 | 14 labels, 2 fragmented, 1 conflated | **13 labels, 0 fragmented, 1 conflated** | 0.0062 | −7.1% |

- **People fragmented now equals or beats single-pass on all three** (1 vs 1, 2 vs 2, 0 vs 2).
- **All three of May 6's residual conflations are INHERITED from pyannote's own within-window
  labels** — `Duffy 143 s + Piedmont-Smith 5 s`, `Asare 35 s + Daily 4 s`,
  `Richardson 22 s + Rollo 4 s`, each a 4–5 s bleed inside one window. No identity clustering
  can fix a window whose own segmentation already merged two voices, and chunking exposes
  slightly more of this because each window clusters independently. After the floor, the
  identity pass introduces **zero cross-window conflation on all three meetings**.
- Single-pass is not clean either: it conflates 1 label on May 6 and 1 on July 29. "Zero
  conflation" was never achievable on these meetings — parity with whole-meeting clustering is.

**Honest note on the earlier "at or below single-pass" target.** The floor moved May 6 from 41
labels to 43, one *above* single-pass's 42, because two nodes that a 0.3–0.6 s overlap had been
forcing together now stand apart. The trade is right and taken deliberately: it removes a
110.5 s two-person merge in exchange for slivers that do not register as a fragmented person at
all (the fragmentation count stays 2, equal to single-pass). Conflation misattributes quotes
silently; fragmentation surfaces at the review gate. Both gates still pass with wide margin.

**The floor was then applied to `src/speaker_reconcile.reconcile_chunks` too**, since the defect
was in the shared reconciler rather than in this change's copy of it. `MIN_SEAM_OVERLAP_SECONDS`
now lives in `speaker_reconcile` as the single source of truth and `global_identity` imports it;
`vibevoice.reconcile_chunks` threads it through, and VibeVoice's regression suite passes
untouched. Measured effect on the legacy sequential path (old cached payloads, threshold 0.50):

- **June 10 unchanged** — 49 labels, 6 people fragmented, 0 conflated, exactly as PR #141 shipped.
- **May 6: 43 → 45 labels, conflation 4 → 3.** So the floor improves the legacy path as well,
  but it does change one figure #141 recorded (43 speakers), which is the honest caveat: cached
  payloads still stitch, and June 10 reproduces exactly, but May 6's sequential speaker count no
  longer matches that PR's table.

## Venue validation (2026-08-23) — threshold raised 0.32 → 0.50

The original calibration used three Bloomington council meetings. That is one venue and one
recording chain, and the spec flagged it as the open risk. Re-validating on two unlike venues,
scored against their existing human-reviewed transcripts (chunk-diarization GPU only — no
single-pass arm, since the reviewed names are the better truth):

**The 0.32 default was too low.** On the July 16 House floor it merged **Rep. Lauren Underwood
(82 s) with Rep. Emilia Sykes (60 s)** into one speaker — a 42.2 % minority share, two real
people rather than a boundary bleed. The reviewed transcript lists both as distinct people with
one label each, so single-pass kept them apart: a genuine regression, and exactly the silent
misattribution this design exists to prevent.

Attribution (same within-window vs cross-window test used on May 6) located it precisely: the
floor had only **2** window-local labels already spanning two people, but **3** conflations in
the output — so the identity pass created exactly one, and it was that merge.

| threshold | House floor (38 people) |
|---|---|
| ≤ 0.46 | 37 labels, **3 conflated incl. Underwood+Sykes 42.2 %** |
| **≥ 0.48** | **38 labels, 2 conflated — both 4 s within-window bleeds** |

0.48–0.60 are identical on the floor and 0.44–0.60 identical on every council meeting, so
**0.50** was chosen: two steps above the cliff, inside the wide plateau, below where 0.55+
starts costing fragmentation.

### Final state at 0.50, all five meetings

| meeting | labels | reviewed people | people fragmented | labels conflated | worst conflation |
|---|---|---|---|---|---|
| House floor, 200 min | **38** | 38 | 3 | 2 | 3.2 % / 4 s |
| Council, 298 min | 44 | 39 | 2 | **0** | — |
| Council, 244 min | 44 | 40 | 3 | 3 | 13.9 % / 4 s |
| Council, 82 min (45-min windows) | 13 | 12 | **0** | **0** | — |
| LA mayoral debate, 106 min | 29 | 33 | 8 | 8 | 43.2 % / 26 s |

Every remaining conflation is a small within-window bleed inherited from pyannote, except on
the debate. **Cost of the raise:** roughly +3 labels and +1 fragmented person on a 5-hour
council meeting (June 10 went 41 → 44 labels, 1 → 2 fragmented). That is the intended trade —
fragmentation surfaces as an extra unnamed speaker at the review gate; conflation misattributes
quotes silently.

### Limitation found: dense broadcast debate

The LA mayoral debate (~33 people in 106 minutes) under-separates badly and no threshold helps —
conflation is 8 at every value from 0.32 to 0.60. **14 of its window-local labels already span
two people before any cross-window step**, so this is pyannote clustering inside a 60-minute
window, not the identity pass. Prefer single-pass for dense multi-speaker debate/forum audio;
most of it is under 90 minutes and therefore never chunks. Chunking is validated for **long
civic meetings — council and legislative floor**.

Two caveats on that meeting's evidence: its reference carries generic pseudo-names
(`Interviewee5`, `(middle)_moderator`) that behave like placeholders, so several of its
"conflations" are 3–4 s bleeds against non-people; and `bench/identity_score` had to be fixed
first — it crashed on a hypothesis label overlapping no reference turn, which happens whenever
placeholder segments are excluded and leave gaps. Such labels are now reported as
`unmapped_labels` rather than crashing.
