# Repair Existing Transcript Design

## Goal

Add a focused command for repairing one previously processed meeting after
transcript alignment logic changes:

```bash
.venv/bin/python run_local.py --repair-transcript <MEETING_ID>
```

The command rebuilds transcript text and exports without repeating download,
diarization, speaker identification, enrollment, or summarization.

## Preconditions

The meeting directory must contain:

- `pipeline_state.json`
- `diarization.json`
- `captions.vtt`
- `transcript_named.json`

The repair command is limited to caption-backed meetings. If `captions.vtt` is
missing, it exits with a clear message explaining that Whisper transcription
cannot be repaired without rerunning transcription.

## Data Flow

1. Load the original diarization segments from `diarization.json`.
2. Remove overlapping segment boundaries using the same normalization applied
   to future Stage 3 runs.
3. Re-align `captions.vtt` using the current rolling-caption deduplication logic.
4. Prepare the repaired, unnamed, unmerged segment list for
   `transcript_raw.json`.
5. Load `transcript_named.json` and preserve its meeting metadata, speaker
   mappings, politician links, summary, and processing metadata.
6. Create a separate named segment list and copy reviewed speaker names and
   identification fields onto it by `speaker_label`.
7. Apply the normal adjacent same-speaker merge to the named segment list.
8. Back up files that will be replaced before performing any write.
9. Rewrite `transcript_raw.json`, then rewrite `transcript_named.json` with
   repaired named segments and all preserved meeting-level data.
10. Regenerate Markdown, JSON, SRT, and summary exports through `export_all`.

The command does not alter `pipeline_state.json`, because the meeting remains
fully processed and no pipeline stage needs to resume.

## Backups

Before overwriting, copy these existing artifacts when present:

- `transcript_raw.json`
- `transcript_named.json`
- `exports/transcript.md`
- `exports/transcript.json`
- `exports/subtitles.srt`
- `exports/summary.md`

Backups live under:

```text
<meeting>/backups/transcript-repair-YYYYMMDD-HHMMSS/
```

If backup creation fails, abort before changing any transcript artifact.

## Error Handling

The command exits without modifying files when:

- the meeting does not exist;
- a required input is missing or invalid;
- no captions can be parsed;
- backup creation fails; or
- repaired output cannot be serialized.

Writes use temporary files followed by atomic replacement so an interrupted
repair does not leave partial JSON or export files.

## CLI Behavior

`--repair-transcript` is a standalone utility command and cannot be combined
with `--input`, `--resume`, `--redo`, batch options, or review options.

On success, output reports:

- meeting ID;
- repaired segment count;
- backup directory;
- regenerated export paths.

## Testing

Tests cover:

- CLI dispatch to the repair handler;
- rejection of missing meetings or required files;
- preservation of reviewed names, mappings, links, metadata, and summary;
- removal of cross-speaker overlap and rolling-caption duplication;
- creation of backups before writes;
- regenerated transcript and subtitle exports;
- unchanged pipeline state;
- atomic failure behavior when repair cannot complete.
