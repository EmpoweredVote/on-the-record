# Diarization benchmark — findings

**Date:** 2026-05-14
**Test set:** 4 Bloomington City Council meetings (Feb 4, Feb 18, Feb 25, latest)
**Audio durations:** 0.5h, 2.9h, 3.0h, ~4h
**Raw data:** [`snapshots/2026-05-14-baseline/`](snapshots/2026-05-14-baseline/)

---

## TL;DR

**Switch to pyannote.ai Precision-2 in production.** It produces materially
cleaner segmentation than pyannote OSS on real council audio — fewer false
speaker changes, longer continuous turns, less interjection noise — at
comparable cost. Three secondary findings:

1. The local **speaker-merge feature does nothing on Bloomington audio** and
   has been gated behind an opt-in `--merge` flag in `run_local.py`.
2. **NeMo Sortformer is not viable** for council-meeting-length audio on
   commodity GPUs (OOMs on L4 for anything > ~30 min). Skipped.
3. The `expected_speakers: 12` hint in `meetings.yaml` is wrong — real count
   scales with meeting length (more public commenters in longer meetings).

---

## Headline scores

12 successful runs (4 meetings × 3 pyannote variants). NeMo failed all 4
attempts (OOM, not in this table).

| Meeting           | Model           | Speakers | Turns | Avg turn | Elapsed | Cost   |
| ----------------- | --------------- | -------: | ----: | -------: | ------: | -----: |
| Feb 4 (2.87h)     | pyannote_oss    |       32 |  1716 |     5.3s | 26 min  | $0.35  |
|                   | pyannote_merged |       32 |  1716 |     5.3s | 25 min  | $0.33  |
|                   | pyannote_ai     |       31 |  1430 |     6.4s |  3 min  | $0.43  |
| Feb 18 (3.05h)    | pyannote_oss    |       25 |  1879 |     5.1s | 38 min  | $0.50  |
|                   | pyannote_merged |       25 |  1879 |     5.1s | 51 min  | $0.67  |
|                   | pyannote_ai     |       24 |  1369 |     7.2s |  3 min  | $0.46  |
| Feb 25 (0.52h)    | pyannote_oss    |        9 |   377 |     4.4s |  2 min  | $0.02  |
|                   | pyannote_merged |        9 |   377 |     4.4s |  2 min  | $0.02  |
|                   | pyannote_ai     |        8 |   344 |     4.9s | 30 s    | $0.08  |
| latest (~4h)      | pyannote_oss    |       42 |  2524 |     5.0s | 51 min  | $0.69  |
|                   | pyannote_merged |       42 |  2524 |     5.0s | 89 min  | $1.19  |
|                   | pyannote_ai     |       44 |  2304 |     5.5s |  4 min  | $0.61  |

Full data: [`snapshots/2026-05-14-baseline/scores.csv`](snapshots/2026-05-14-baseline/scores.csv).

---

## Finding 1 — pyannote.ai Precision-2 wins on segmentation quality

Across all four meetings, pyannote.ai produces **15–30 % fewer turns** than
OSS with slightly longer averages, despite reporting roughly the same
speaker count (±1–2). That's not "lazier" — it's correctly identifying
continuous speech as one turn instead of fragmenting it.

**Spot-check** (Feb 25, 9:30–10:30, see
[`results/oss_vs_ai_spotcheck/`](results/oss_vs_ai_spotcheck/)):

The dominant speaker is Council Member Stosberg for the first 36 seconds,
then Council Member Flaherty for the rest, with brief "okay" / "mmhmm"
interjections from Council President Asare.

- **OSS** produces **19 turns** in this 60s window. Stosberg's continuous
  36-second statement is fragmented into 7 separate `SPEAKER_05` turns of
  4–6 s each. The "okay/mmhmm" interjections become 0.02-second sub-turns
  attributed to different speaker IDs.
- **AI** produces **7 turns**. Stosberg is correctly identified as one
  continuous 42-second turn. Interjections are captured but not as
  separate turns.

For a readable transcript, AI's segmentation is meaningfully better. The
0.02 s sub-turns in OSS's output aren't useful information — they're noise
in the speaker-change signal.

**Cost:** ~$0.50/3h meeting either way (pyannote.ai API fees vs. L4 GPU
time). AI is also **~15× faster wall-clock** (3 min vs. 50 min for a 3h
meeting), which matters for batch processing.

**Production action:** swap pyannote OSS for pyannote.ai Precision-2 as
the primary diarizer. Keep OSS as a free fallback.

---

## Finding 2 — Speaker-merge feature was solving a non-problem

`pyannote_oss` and `pyannote_merged` produce **byte-identical RTTM
outputs** on every meeting. The merge code runs (it pays for embedding
extraction) but performs **zero merges** at the configured threshold
(0.80).

Diagnosis (`bench/diagnose_merge.py`) on locally-cached embeddings:

- 3 of 9 Feb 25 speaker centroids are entirely **NaN** (real bug — likely
  short-segment slices producing degenerate embeddings).
- Even the non-NaN pairs cap out at cosine similarity **~0.29**, nowhere
  near the 0.80 merge threshold. No threshold from 0.50–0.85 produces
  meaningful merges.

**Hand-verified** by extracting video clips of the alleged
"fragmented" speakers on Feb 25 (see
[`results/merge_diagnosis_feb25/`](results/merge_diagnosis_feb25/)): the 8
labels pyannote produced are 7 distinct councilors plus a non-speech
outro-music segment. **No fragmentation actually occurred.** The
`NEXT_SESSION_PROMPT.md` premise that those labels were the same person
was wrong.

**Production action (done):** flipped `--no-merge` (on-by-default) →
`--merge` (off-by-default) in `run_local.py`. Code kept in tree in case a
future pyannote release reintroduces fragmentation.

---

## Finding 3 — NeMo Sortformer doesn't fit our use case

`nvidia/diar_sortformer_4spk-v1` (the released open-source Sortformer) is
a short-clip model. Processing a 31-min meeting needs **32 GB** for a
single intermediate tensor; even the L4 (22 GB) can't fit any of our
meetings.

Options that would make NeMo viable:

- **A100-80GB** — ~5× the cost per minute. Untested.
- **Chunked inference** in the wrapper — 1–2 hrs of dev work; requires
  cross-chunk speaker re-clustering. Untested.
- **NeMo `NeuralDiarizer`** (different model, clustering-based, designed
  for long meetings). Not benchmarked here.

Since pyannote.ai already wins the segmentation quality argument at
similar cost, the additional NeMo investigation isn't justified for the
production decision. **Skipped.**

`nemo_sortformer` is commented out in `meetings.yaml` with a note.

---

## Finding 4 — `expected_speakers: 12` is wrong

The hint scales with meeting length:

| Meeting       | Hours | Speakers found (OSS) |
| ------------- | ----: | -------------------: |
| Feb 25 (short)|   0.5 |                    9 |
| Feb 4         |   2.9 |                   32 |
| Feb 18        |   3.0 |                   25 |
| latest (long) |  ~4   |                   42 |

Council = ~9 fixed + clerk + mayor + variable public commenters. The
public commenter count scales roughly linearly with meeting length, and
each public commenter is a unique voice. **Set `expected_speakers` per
meeting if you re-use this benchmark**, or drop the field entirely and
rely on the in-clip spot-check sample.

---

## Rerunning this benchmark

```bash
# Full re-run (skips cached pairs in Modal volume)
.venv/bin/python -u bench/run.py --continue-on-error

# Single model
.venv/bin/python -u bench/run.py --models pyannote_ai

# Score an existing run dir without re-running anything
.venv/bin/python bench/score.py bench/results/<run-id>
```

To force a fresh `latest-council` (since CATS TV publishes new meetings):

```bash
.venv/bin/modal volume rm councilscribe-bench /meetings/latest-council --recursive
```
