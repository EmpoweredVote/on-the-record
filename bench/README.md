# Diarization model benchmark

A reproducible harness for picking the best diarization model for CouncilScribe.
Runs four candidates on a fixed set of Bloomington City Council meetings on
Modal, writes RTTM outputs back, and scores them locally.

## What it tests

| Model | Image | GPU | Cost signal |
|---|---|---|---|
| `pyannote_oss` | `pyannote.audio==3.3.2` | L4 | GPU time only |
| `pyannote_merged` | same as above + uses `src/merge.py` | L4 | GPU time only |
| `pyannote_ai` | requests (REST API) | none | ~$0.15 / audio hour |
| `nemo_sortformer` | NeMo 2.0 from `nvcr.io/nvidia/pytorch:24.07` | L4 | GPU time only |

## One-time setup

### 1. Install Modal locally

```bash
pip install modal pyyaml
modal token new        # browser-based auth
```

### 2. Create Modal secrets

You need a HuggingFace token (for pyannote OSS + NeMo) and a pyannote.ai key
(for the Precision-2 API). Create them in the Modal dashboard or via CLI:

```bash
modal secret create huggingface-token  HF_TOKEN=hf_xxx...
modal secret create pyannote-ai-key    PYANNOTE_AI_KEY=sk-xxx...
```

For the HF token, make sure you've accepted the model terms on both:
- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/segmentation-3.0
- https://huggingface.co/pyannote/embedding

### 3. (No volume setup needed)

`bench/modal_app.py` creates the `councilscribe-bench` volume on first run.

## Running

From the **repo root** (not from inside `bench/`):

```bash
# Smoke test: fetch one meeting + run pyannote OSS only
modal run bench/modal_app.py::smoke --meeting-id 2026-02-25-council

# Full sweep (4 meetings × 4 models = 16 runs)
python bench/run.py

# Subset
python bench/run.py --models pyannote_oss pyannote_ai \
                    --meetings 2026-02-25-council latest-council

# Re-score an existing run dir without re-running anything on Modal
python bench/score.py bench/results/2026-05-13_18-22-04
```

The first run for each meeting downloads the source video from CATS TV
(~500MB–1GB) and normalizes it to 16kHz mono WAV. Subsequent runs reuse the
cached audio.

## What you get

```
bench/results/2026-05-13_18-22-04/
  results/                          # mirror of the Modal volume
    <meeting_id>/
      pyannote_oss.rttm
      pyannote_oss.meta.json
      pyannote_merged.rttm
      ...
  spot_checks/
    <meeting_id>/<model>/
      clip_00_t01234.wav            # 60-sec audio sample
      clip_00_t01234.txt            # one-char-per-second speaker strip
      ...
  scores.csv                        # summary table
  sweep_log.json
```

`scores.csv` columns: `meeting_id, model, audio_duration_s, elapsed_s,
realtime_factor, cost_usd, num_turns, num_speakers, expected_speakers,
speaker_delta, avg_turn_s, silence_fraction`.

## How to pick a winner (no ground truth)

We don't have hand-labeled RTTMs, so the scorer doesn't compute DER. Use
this decision framework instead:

1. **Speaker count delta.** Compare `num_speakers` to `expected_speakers`.
   Big positive = fragmentation (the diarizer split one person into many);
   big negative = under-clustering (it merged different people).
   `pyannote_merged` should have a smaller delta than `pyannote_oss` if
   the merge feature is doing its job.
2. **Average turn duration.** Very short turns (< 2s) on otherwise calm
   council audio is a fragmentation signal.
3. **Silence fraction.** Should be roughly similar across models; large
   differences mean someone is missing speech or over-claiming silence.
4. **Spot-check.** Open 3–5 of the generated `clip_*.wav` clips per
   leading candidate and read the matching `.txt` strip while listening.
   Does the speaker change match what you hear? Are 2 people getting one
   label?
5. **Cost + speed.** Tie-breakers — favor cheaper / faster only if quality
   is comparable.

## Cost estimate

A full sweep on the 4 meetings:
- pyannote OSS + merged: ~30–60 min L4 GPU × 2 = ~$0.50–1.00
- pyannote.ai: ~8 hours of audio × $0.15 = ~$1.20
- NeMo Sortformer: ~30 min L4 GPU = ~$0.40

Total: **~$2–3 for a full sweep**. NeMo image is the biggest variable —
the first build pulls a ~7GB CUDA base image and may take 10–15 min.

## Known caveats

- **Sortformer's published model (`diar_sortformer_4spk-v1`) is a
  4-speaker model.** Council meetings routinely have 10+ distinct voices,
  so Sortformer will likely under-cluster on the full meeting. This is
  expected to surface as a large negative `speaker_delta`. If it otherwise
  looks fast/accurate, the follow-up is to switch to NeMo's MSDD
  clustering pipeline (arbitrary N speakers).
- **pyannote.ai API contract.** The wrapper assumes Precision-2's REST
  flow as of mid-2026 (upload → diarize → poll). If their API has shifted,
  the `diarize_pyannote_ai` function is the only place to update.
- **Cost numbers in `meta.json` are estimates.** L4 list price is ~$0.80/hr
  on Modal; verify against your dashboard. pyannote.ai pricing depends on
  your plan.
- **The audio cache is intentionally permanent.** If you want to force a
  fresh CATS TV pull (e.g., the "latest" meeting has changed), delete
  `meetings/<id>/` from the volume:
  ```bash
  modal volume rm councilscribe-bench /meetings/latest-council --recursive
  ```

## Rerunning when a new model drops

The harness is the regression harness — add a new function in
`bench/modal_app.py`, add its name to `models:` in `meetings.yaml`,
re-run `python bench/run.py`, compare against the prior `scores.csv`.
