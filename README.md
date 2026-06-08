# CouncilScribe

Automated city council meeting transcription with speaker diarization and identification. Runs entirely on Google Colab's free tier.

## What it does

CouncilScribe processes a meeting recording through a 6-stage pipeline:

1. **Ingest** — Normalize audio to 16kHz mono WAV via ffmpeg
2. **Diarize** — Speaker segmentation with pyannote.audio 3.x
3. **Transcribe** — Speech-to-text with faster-whisper (large-v3 on GPU, medium on CPU)
4. **Identify** — Map speaker labels to real names using voice profiles, rule-based patterns, and an optional local LLM
5. **Enroll** — Save confirmed voice profiles for future meetings
6. **Export** — Output Markdown, JSON, and SRT subtitle files

Every stage checkpoints to Google Drive, so Colab session timeouts don't lose progress.

## Prerequisites

- Google account (for Colab and Drive)
- [Hugging Face account](https://huggingface.co/join) with an access token
- Accept the pyannote model terms:
  - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
  - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
- A meeting recording (MP4, WAV, MP3, or other common formats)

## First-run checklist

1. Open `council_scribe.ipynb` in [Google Colab](https://colab.research.google.com/)
2. Run the **Setup** cells to install dependencies and mount Drive (~3 min)
3. Enter your Hugging Face token when prompted
4. Update the **Configuration** cell with your meeting details and audio path
5. Run the pipeline cells in order (or use Runtime > Run all)
6. Review flagged speakers in the Human Review cell if prompted
7. Find outputs in `Google Drive/CouncilScribe/meetings/<meeting_id>/exports/`

## Output formats

| Format | File | Use case |
|--------|------|----------|
| Markdown | `transcript.md` | Human-readable, shareable |
| JSON | `transcript.json` | Programmatic access, full metadata |
| SRT | `subtitles.srt` | Video subtitle overlay |

## Processing time estimates

| Meeting length | GPU (T4) | CPU only |
|---------------|----------|----------|
| 1 hour | ~10 min | ~45 min |
| 3 hours | ~30 min | ~2.5 hrs |

## Project structure

```
CouncilScribe/
  council_scribe.ipynb    # Main Colab notebook (start here)
  run_local.py            # Local CLI entry point (alternative to Colab)
  requirements.txt
  src/
    config.py             # Settings, paths, thresholds
    models.py             # Data classes (Meeting, Segment, etc.)
    checkpoint.py         # Pipeline state machine
    audio_utils.py        # Audio helpers
    download.py           # CATS TV scraping + URL download
    ingest.py             # Stage 1: ffmpeg normalization
    diarize.py            # Stage 2: pyannote diarization
    merge.py              # Post-diarization: collapse fragmented speakers
    transcribe.py         # Stage 3: faster-whisper transcription
    vtt_align.py          # Alt Stage 3: align CATS TV VTT to diarization
    identify.py           # Stage 4: speaker identification (Layers 1-2)
    llm_utils.py          # Stage 4: LLM identification (Layer 3)
    roster.py             # Council roster + fuzzy name correction
    enroll.py             # Stage 5: voice profile enrollment
    summarize.py          # Stage 6: meeting summary generation
    export.py             # Stage 7: JSON/Markdown/SRT export
  bench/                  # Diarization model benchmark harness (Modal)
    README.md             # See for benchmarking setup + how to pick a winner
    modal_app.py          # 4 diarization models, runs on Modal
    meetings.yaml         # Test set definition
    run.py                # Local orchestrator
    score.py              # Scoring + spot-check sampler
```

## Diarization benchmarking

`bench/` contains a Modal-based harness for comparing diarization models
(`pyannote_oss`, `pyannote_merged`, `pyannote_ai` Precision-2, `nemo_sortformer`)
against a fixed test set. See [`bench/README.md`](bench/README.md) for
setup and how to use the output to pick a model for production.

## Speaker identification strategy

CouncilScribe uses three layers to identify speakers, applied in order of confidence:

1. **Voice profiles** — Cosine similarity against stored embeddings from previous meetings (threshold: 0.85)
2. **Pattern matching** — Regex patterns for roll call, chair recognition, self-identification, name addressing, and title context
3. **LLM-assisted** — A small local model (Phi-3.5-mini) infers identities from conversational context

Speakers below 0.70 confidence are flagged for human review via a Colab form widget.

### Choosing a roster (local CLI)

Speaker identification can be guided by a council roster (it corrects
transcription errors against known member names). When you run `run_local.py`
**interactively without `--body`**, CouncilScribe now asks which roster to use:

- any cached per-body roster under `~/CouncilScribe/config/rosters/` (added
  with `python refresh_roster.py --body <slug>`),
- the legacy `~/CouncilScribe/config/council_roster.json`, or
- **No roster** (the default — just press Enter) to skip name correction.

Picking a cached roster tags the meeting (like passing `--body <slug>`), so
resuming it reuses that roster automatically. Pass `--body <slug>` explicitly
to skip the prompt. In non-interactive runs (batch mode, piped, cron) with no
`--body`, no roster is used.

## Google Drive structure

After processing, your Drive will contain:

```
CouncilScribe/
  meetings/
    <meeting_id>/
      audio.wav
      diarization.json
      embeddings.json
      transcript_raw.json
      transcript_named.json
      pipeline_state.json
      exports/
        transcript.md
        transcript.json
        subtitles.srt
  profiles/
    speaker_profiles.pkl
```
