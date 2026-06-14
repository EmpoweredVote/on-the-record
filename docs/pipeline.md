# Pipeline guide

Automated meeting transcription with speaker diarization and identification. Runs locally (`run_local.py`) or on Google Colab's free tier (`council_scribe.ipynb`).

> Operator documentation for the transcription pipeline. For the project overview, see the [main README](../README.md).

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

### Reviewing & naming speakers

After an interactive run finishes, CouncilScribe drops into a guided speaker
review (skip with `--no-review`). For each detected speaker it shows stats and
any voice-profile match hints, and lets you:

- **`[V]iew`** — play a clip of that speaker, starting with their **longest turn**
  (most likely to show them clearly). It loops and plays in the background, so you
  can **type the name while it plays** (handy when the name is shown on screen).
  Press `[V]` again to jump to the next-longest turn. The clip stops when you enter
  a name or skip.
- **`[Y]`** — accept the suggested voice-profile match,
- **`[M]erge`** — merge this speaker into another (when diarization split one
  person into two: their segments and voice data combine),
- type a **name**, or **`[Enter]`** to skip / **`[Q]`** to quit.

Naming a speaker enrolls their voice so future meetings auto-match them. To
re-review a finished meeting later: `python run_local.py --review <MEETING_ID>`
(the old `--review-meeting` / `--identify-speakers` still work as aliases).

YouTube/Facebook meetings now download a capped-resolution video so clips are
available during review (CATS TV, direct URLs, and local files always had them).

### Re-running a past meeting

To re-run a finished meeting from a particular stage (e.g. after improving a
roster or fixing audio):

```
python run_local.py --resume <MEETING_ID> --redo identify
```

`--redo` accepts `diarize`, `transcribe`, `identify`, `summary`, or `all` (the
full analysis from diarization; the already-ingested audio is kept). It rewinds
the checkpoint and re-runs from that stage onward — `--redo identify` re-runs
speaker identification and drops you back into the all-speaker review.

Note: if the meeting has CATS TV captions (`captions.vtt`), `--redo transcribe`
re-aligns the transcript from those captions rather than re-running Whisper
(same as a first run with captions present).

### Repairing one caption-backed transcript

For a processed meeting with an existing `captions.vtt`, rebuild only its
transcript text and exports:

```bash
python run_local.py --repair-transcript <MEETING_ID>
```

The repair preserves reviewed speaker identities, politician links, metadata,
summary, and checkpoint state. It creates a timestamped backup at
`<meeting>/backups/transcript-repair-YYYYMMDD-HHMMSS/`.

Meetings without saved captions must use
`--resume <MEETING_ID> --redo transcribe`, which reruns transcription and
downstream stages.

### Meeting metadata prompts

For a new run, if you don't pass `--city`, `--date`, or `--meeting-type`,
CouncilScribe prompts for each (press Enter to accept the shown default). Pass
`--default` to skip the prompts and use the defaults (Bloomington / Regular
Session / today). Non-interactive runs use the defaults automatically.

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
