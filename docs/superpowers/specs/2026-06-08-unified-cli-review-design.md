# Unified CLI speaker review — design

**Date:** 2026-06-08
**Status:** Approved (pending implementation)
**Sub-project 1 of 2** (sub-project 2 = local web GUI, deferred to its own spec)

## Problem

CouncilScribe's speaker identification, correction, and merging are spread
across seven CLI flags plus a weak in-pipeline review. The operator will process
many meetings with new people and needs to: assign the right name to each
detected speaker, correct wrong names, and merge speakers that diarization split
into two. Today:

- A normal `run_local.py --input ...` run ends in the weak text-only
  `human_review` (no video clips, no voice-profile hints, no merge). The rich
  review (clips + hints) only runs if the operator separately invokes
  `--review-meeting` or `--identify-speakers`.
- There is **no way to mark two detected speakers as the same person** during
  review. The only consolidation paths are `--merge` (embedding-threshold, off
  by default) and `--merge-profiles` (manual slug bookkeeping, post-hoc).
- YouTube/Facebook downloads pull audio-only (`bestaudio/best`), so no video
  clip is available for those meetings — defeating the watch-clip-to-ID flow.
- Learned aliases (`add_alias`) write only to the legacy `council_roster.json`,
  not the per-body cache roster the pipeline actually resolves, so corrections
  don't improve future auto-correction for body-tagged meetings.
- When no video is found, the borderline-enroll loop prints a misleading
  "audio-only fallback with afplay" message, but no audio is ever played.

## Goal

Consolidate review/name/merge into one guided flow that runs automatically after
an interactive pipeline run, add a real in-review same-speaker merge, and fix the
three gaps above. Extract the review *operations* into a pure, reusable module so
the future GUI (sub-project 2) shares one tested implementation.

## Decisions (from brainstorming)

- **D1 — Architecture:** extract a pure review core (`src/review.py`); the CLI
  interactive loop and the future GUI both call it. (Chosen over extend-in-place
  and minimal.)
- **D2 — Merge semantics:** "full merge" — combine the two speakers for THIS
  meeting (segments collapse to one name in transcript/exports) AND treat them as
  one person for voice-profile enrollment.
- **D3 — Default review:** an interactive run auto-drops into the rich review at
  the end. Non-interactive/batch keeps the text-only fallback. `--no-review`
  opts out.
- **D4 — Sources:** the operator uses YouTube/Facebook too, so video clips must
  work for yt-dlp downloads (fix the audio-only download).
- **D5 — Environment:** local, single-user. No auth/hosting concerns.

## Architecture

### New module: `src/review.py` (pure core)

No prompts, no printing, no file writes — pure transforms over in-memory data,
so it is directly unit-testable and reusable by the GUI.

**`SpeakerView` (dataclass):**
- `label: str`
- `current_name: Optional[str]`
- `current_confidence: float`
- `current_method: Optional[str]`
- `seg_count: int`
- `total_speech_seconds: float`
- `clip_start: Optional[float]` — timestamp of a representative segment (≈1/3 in)
- `sample_text: Optional[str]` — present only when `show_text=True`
- `soft_hints: list[tuple[str, float]]` — top voice-profile matches (name, score)
- `needs_review: bool`

**Functions:**

- `build_review_state(segments, mappings, embeddings, profile_db, *, show_text: bool) -> list[SpeakerView]`
  Builds one `SpeakerView` per speaker label, sorted by `total_speech_seconds`
  descending. Reuses the per-speaker stats logic currently in
  `run_local._build_speaker_stats` (moved into `review.py`) and
  `src.identify.soft_match_voice_profiles` for hints. `sample_text`/`clip_start`
  come from a representative text segment (or any segment if no text).

- `rename_speaker(mappings, segments, label, new_name, *, roster=None) -> RenameResult`
  Sets `mappings[label]` name (confidence 1.0, `id_method="human_review"`,
  `needs_review=False`), applies the name to every segment with that label, and
  (if `roster`) runs `correct_speaker_name`. Returns `RenameResult(old_name,
  new_name, alias_suggestion)` where `alias_suggestion` is the old wrong name to
  offer as an alias (None if not applicable). Mutates `mappings`/`segments` in
  place; returns the summary for the caller to act on (persist, offer alias).

- `merge_speakers(segments, embeddings, mappings, source_label, target_label) -> MergeResult`
  **Full merge.** Relabels every `source_label` segment to `target_label`;
  combines `embeddings[source_label]` into `embeddings[target_label]` and
  recomputes the target centroid; removes `source_label` from `embeddings` and
  `mappings`. Returns `MergeResult(source_label, target_label, moved_segments,
  combined_name)`. Reuses centroid/relabel helpers from `src.merge` where they
  exist; if a helper isn't cleanly reusable, add a small shared helper rather
  than duplicating. Mutates the passed structures in place.

- `speakers_needing_review(mappings) -> list[str]` — labels with
  `needs_review=True` (used by the non-interactive fallback summary).

Persistence stays in `run_local.py` (or a thin persist helper there): after the
caller applies rename/merge results, it writes `diarization.json`,
`embeddings.json`, `transcript_named.json`, and re-exports as appropriate.

### `run_local.py` changes

- Refactor `_interactive_speaker_review` to:
  - consume `build_review_state(...)` for display, and
  - call `review.rename_speaker(...)` for typed names / `[Y]` hint-accept, and
  - add a **`[M]erge`** command: prompt "merge SPEAKER_xx into which #?", call
    `review.merge_speakers(...)`, then persist updated `diarization.json` +
    `embeddings.json` and refresh the in-loop state.
  - On a rename that produced an `alias_suggestion`, offer to add it as an alias
    (now body-aware — see roster fix).
- **Auto-drop into rich review (D3):** in `run_pipeline` Stage 4, when
  `sys.stdin.isatty()` and not `--no-review`, run the rich review (build state →
  interactive loop → apply results → persist) in place of the current
  `human_review(mappings)` call. Non-interactive or `--no-review` keeps the
  existing text-only `human_review`. Merges performed here update
  `embeddings.json` so Stage 6 enrollment sees the combined speaker.
- **Consolidate entry points:** add `--review <MEETING_ID>` as the canonical
  re-review command (works pre- or post-transcription, includes merge).
  `--review-meeting` and `--identify-speakers` remain as thin aliases that call
  the same unified path (no behavior loss; help text marks them as aliases).
- Add `--no-review` flag (suppresses the auto post-run review).

### Bug/gap fixes (existing modules)

1. **yt-dlp video (`src/download.py::download_via_ytdlp`):** change the format
   from `bestaudio/best` to a capped-resolution video format, e.g.
   `bestvideo[height<=480]+bestaudio/best[height<=480]/best`, so the saved
   `source.*` is a playable video. Keep the downstream `-vn` audio extraction to
   `audio.wav` unchanged. Larger downloads are acceptable (capped ~480p — clips
   only need to show a face). `find_video_file` already matches mp4/mkv/webm.

2. **Roster-alias targeting (`src/roster.py::add_alias`):** make it body-aware.
   When the meeting is tagged with a `body_slug`, learned aliases write to that
   per-body cache roster (`config/rosters/{body_slug}.json`) — the roster the
   pipeline resolves via `_resolve_roster` — instead of only the legacy
   `council_roster.json`. The review flow passes the effective `body_slug`
   (read from `pipeline_state.json` for `--review <ID>`; already known in
   `run_pipeline`). Legacy behavior (no body_slug) unchanged.

3. **Audio fallback (`run_local.py::play_video_clip` + callers):** remove the
   misleading "afplay" message. When no video file is found, play the audio
   segment from `audio.wav` at the clip timestamp via `ffplay` (audio-only).
   So `[V]iew` always plays something — video if available, else audio.

## Data flow (auto review after an interactive run)

1. Stages 1–4 run as today; Stage 4 produces `mappings` + `segments`, with
   `embeddings.json` on disk.
2. If interactive and not `--no-review`: `build_review_state(...)` → operator
   loops over speakers (view clip/audio, accept hint, type name, or merge).
3. Rename/merge mutate `mappings`/`segments`/`embeddings` via `review.py`;
   `run_local` persists `diarization.json`/`embeddings.json` after a merge and
   offers body-aware alias learning after a rename.
4. `apply_mappings_to_segments` + write `transcript_named.json`; mark IDENTIFIED.
5. Stage 6 enrollment reads the updated `embeddings.json`, so a merged speaker
   enrolls as one profile.

## Error handling

- Merge into a non-existent / same-as-source label: `merge_speakers` raises a
  `ValueError`; the interactive loop catches it and re-prompts.
- Missing `embeddings.json`: merge still relabels segments; centroid combine is
  skipped with a printed note (name-only effect on enrollment).
- `ffplay` not installed: the existing "ffplay not found" message stays (now the
  single failure path for both video and audio playback).
- yt-dlp capped-format unavailable for a source: the `/best` fallback in the
  format string yields whatever is available (may be audio-only for that one
  source — then audio fallback applies).

## Testing

- `tests/test_review.py` (new):
  - `build_review_state`: ordering by speech time, hint inclusion, needs_review,
    `show_text` toggling `sample_text`.
  - `rename_speaker`: mapping + segment names updated, confidence/method set,
    alias_suggestion returned for a corrected wrong name.
  - `merge_speakers`: source segments relabeled to target, embeddings combined +
    centroid recomputed, source removed from mappings/embeddings, MergeResult
    fields correct; error on bad/same labels; missing-embeddings path.
  - `speakers_needing_review`.
- Targeted tests:
  - yt-dlp format string now requests video (assert the format string the code
    passes to yt-dlp includes a video selector, via the existing download tests'
    seam / a small unit on the format-builder).
  - `add_alias` writes to the per-body cache when given a `body_slug`, and to the
    legacy file when not (tmp config dir).
  - audio-fallback selection: when no video file, the playback path targets
    `audio.wav` (assert the chosen command/path, mocking subprocess).
- The interactive loop itself stays thin (I/O only); its logic is covered
  through the `review.py` functions.

## Out of scope (sub-project 2 / later)

- The local web GUI (paste-URL → metadata → run → browser review). It will reuse
  `src/review.py`.
- Any hosting, auth, or multi-user concerns.
- Diarization model changes.

## Documentation

Update `README.md` "Speaker identification strategy" / the roster section to
describe: the auto post-run review, the `[M]erge` command, `--review <ID>` and
`--no-review`, and that YouTube/Facebook now download video for clips.
