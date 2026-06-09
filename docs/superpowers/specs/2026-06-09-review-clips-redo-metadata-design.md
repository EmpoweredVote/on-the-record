# Review clips, stage re-run, and metadata prompts — design

**Date:** 2026-06-09
**Status:** Approved (pending implementation)

Three independent CLI-ergonomics improvements to `run_local.py` (+ `src/review.py`,
`src/checkpoint.py`). They do not depend on each other.

## Background / motivation

While reviewing a real meeting, the operator hit a speaker auto-labeled
"Treasurer" (conf 0.75, `title_context`). The all-speaker review worked — the
speaker WAS presented — but the single ~20s clip it played (the segment near the
speaker's ⅓ point: *"Thank you. Justin."*) didn't reveal which on-screen person
was speaking, so they skipped it. The speaker had 17.2 min over 78 segments — a
long identifying turn almost certainly exists, but the heuristic didn't surface
it. Separately, the operator wants to re-run pipeline stages for past meetings,
and to be prompted for meeting metadata instead of silently getting defaults.

## Decisions (from brainstorming)

- **A — Clip review:** default the clip to the speaker's **longest turn**; `[V]`
  cycles through next-longest turns. Playback is **non-blocking and looping** so
  the operator can type the name (often shown on a lower-third graphic) while the
  clip plays; the player closes when they enter a name / accept / merge / skip /
  quit / or view another clip.
- **B — Re-run:** `--redo {diarize,transcribe,identify,summary,all}` paired with
  `--resume <MEETING_ID>`; rewinds the checkpoint to before that stage and re-runs
  from there.
- **C — Metadata prompts:** prompt for `city`/`date`/`meeting-type` when not
  passed (pre-filled with the default; Enter accepts). `--default` opts out;
  non-interactive runs use defaults. `meeting-id` stays auto-derived.
- **#1 coverage** (review every speaker, incl. above-threshold) already shipped —
  the rich review walks every speaker. No new work beyond A.

---

## A. Clip review: longest-turn, cycling, non-blocking playback

### A1. `src/review.py` — clip candidates
Add a field to `SpeakerView`:
- `clip_candidates: list[float]` — start times of this speaker's segments sorted
  by segment **duration descending**, capped at the top 8.

In `build_review_state`:
- Build `clip_candidates` from the speaker's segments (sorted by `end-start` desc,
  top 8 start times).
- Set `clip_start` to the **first** candidate (the longest turn) instead of the
  ⅓-point representative. If a speaker has no segments, `clip_candidates == []`
  and `clip_start is None`.
- `sample_text`: when `show_text`, use the text of the longest-turn segment (more
  identifying context) rather than the ⅓-point one. (Existing `_representative_segment`
  is no longer used for clip selection; keep it only if still referenced — else remove.)

Existing tests that assert `clip_start == 0.0` for a single-segment speaker stay
valid (the lone segment is also the longest).

### A2. `run_local.py` — non-blocking looping playback
Change `play_speaker_clip` to launch the player **without blocking** and **looping**:
- Use `subprocess.Popen` (not `subprocess.run`).
- ffplay args: `-loop 0` (loop forever) plus the existing `-ss`/`-t`/`-loglevel quiet`,
  `-nodisp` for audio-only, optional `-window_title`. Drop `-autoexit` (we kill it
  ourselves; looping keeps it visible until then).
- Return the `Popen` handle (or `None` if no media / ffplay missing).
- Bump default duration to 40s (longer look at the person).
- Signature: `play_speaker_clip(video_path, audio_path, start_time, duration=40.0, title="") -> Optional[subprocess.Popen]`.

Add a small helper `_stop_player(proc)` that terminates a `Popen` if running
(guards `None`, already-exited, and `FileNotFoundError`).

### A3. `run_local.py` — review loop cycling + cleanup
In `_interactive_speaker_review`, per speaker:
- Track a `clip_idx` (starts at 0) and a `current_player` handle.
- `[V]` → `_stop_player(current_player)`, play `view.clip_candidates[clip_idx]`
  (non-blocking, looping), advance `clip_idx = (clip_idx + 1) % len(clip_candidates)`,
  then re-prompt immediately (do NOT block).
- On ANY terminating input for the speaker (typed name, `[Y]`, `[M]`, `[Enter]`,
  `[Q]`), `_stop_player(current_player)` before handling it.
- Wrap the per-speaker inner loop in `try/finally` so the player is always stopped
  when leaving the speaker (covers exceptions too).
- The `[V]` option is offered when `clip_candidates` is non-empty and a player is
  available (video or audio path present).

**Edge cases:** ffplay not installed → `play_speaker_clip` prints the existing
"ffplay not found" note and returns `None`; the loop tolerates a `None` handle.
A speaker with one segment → `[V]` always replays the same clip (cycle of length 1).

---

## B. Re-run from a chosen stage (`--redo`)

### B1. `src/checkpoint.py` — `rewind_to(stage)`
Add `PipelineState.rewind_to(stage: PipelineStage)`:
- Sets `completed_stage` to the stage immediately before `stage` (or
  `NOT_STARTED` if `stage` is the first).
- Deletes the on-disk artifacts produced by `stage` and every later stage, so they
  are regenerated. Artifact-per-stage map (deletes are best-effort, ignore missing):
  - DIARIZED → `diarization.json`, `embeddings.json`
  - TRANSCRIBED → `transcript_raw.json` (and reset `transcription_progress`/`total_segments` to 0)
  - IDENTIFIED → `transcript_named.json`, `pre_identifications.json`, `llm_partial_results.json`
  - SUMMARIZED → `summary.json`
  - EXPORTED → `exports/` contents
  When rewinding to stage N, delete artifacts for N..EXPORTED.
- `save()` after.
- Generalizes the existing `rewind_for_retag` (which is rewind_to(IDENTIFIED) minus
  embeddings/diarization). Keep `rewind_for_retag` as-is or reimplement it on top
  of `rewind_to` — implementer's call, but do not change its current behavior.

### B2. `run_local.py` — `--redo` flag + wiring
- argparse: `--redo`, `choices=["diarize","transcribe","identify","summary","all"]`,
  default `None`. Help notes it requires `--resume <MEETING_ID>`.
- Validation in `main()`: `--redo` requires `--resume` (parser.error otherwise).
  `all` maps to re-running from ingest (rewind to NOT_STARTED).
- In the resume path (after the meeting dir/state are known, before `run_pipeline`),
  if `args.redo`: resolve the stage name → `PipelineStage`, call
  `state.rewind_to(<stage>)`, print a one-line notice (e.g.
  `Re-running from stage: identify`).
- Stage-name → PipelineStage map: diarize→DIARIZED, transcribe→TRANSCRIBED,
  identify→IDENTIFIED, summary→SUMMARIZED, all→(rewind to NOT_STARTED / ingest).

---

## C. Interactive metadata prompts (`--default` opt-out)

### C1. argparse sentinel
Change defaults so "not passed" is detectable:
- `--city` default `None` (was `"Bloomington"`).
- `--meeting-type` default `None` (was `"Regular Session"`).
- `--date` already defaults to `""` → treat empty as missing.
- Add `--default` (`store_true`): skip all metadata prompts, use defaults.
- Hardcoded fallback defaults live in one place: `CITY_DEFAULT="Bloomington"`,
  `MEETING_TYPE_DEFAULT="Regular Session"`, date default = today.

### C2. `run_local.py` — `_resolve_metadata(args)` helper
Called in `main()` for new runs (`--input`/`--browse-catstv`), AFTER the
`--browse-catstv` block (which already fills `date`/`meeting_type` from the
selection) and BEFORE the date-default/`run_pipeline`:
- If `args.default` OR `not sys.stdin.isatty()`: fill any unset field with its
  hardcoded default (city→"Bloomington", meeting_type→"Regular Session",
  date→today). No prompts.
- Else (interactive, no `--default`): for each still-unset field, prompt with the
  default pre-shown, e.g. `City [Bloomington]: ` — Enter accepts the default, typed
  value overrides. For date, validate `YYYY-MM-DD`; empty → today.
- Fields already provided on the CLI are left untouched (no prompt).
- `meeting-id` is never prompted; it stays auto-derived unless `--meeting-id` given.

`--resume` does NOT prompt for metadata (it loads metadata from the existing
meeting). `--batch` keeps its own per-entry metadata (no change).

The existing `main()` block that sets `args.date = today` when empty is subsumed
by `_resolve_metadata` (which always leaves `date` populated for new runs) — remove
it to avoid a duplicate "using today" message. `--resume`/`--batch` set their own
dates before reaching that point, so removing it is safe.

---

## Files touched

- `src/review.py` — `SpeakerView.clip_candidates`; clip selection in
  `build_review_state`.
- `src/checkpoint.py` — `rewind_to`.
- `run_local.py` — `play_speaker_clip` (non-blocking/looping) + `_stop_player`;
  `_interactive_speaker_review` cycling + cleanup; `--redo` flag/validation/wiring;
  `--default` flag + `_resolve_metadata`; argparse sentinel defaults.
- `README.md` — document clip cycling / type-while-playing, `--redo`, metadata
  prompts + `--default`.
- Tests — see below.

## Testing

- `tests/test_review.py`: `clip_candidates` ordered by duration desc (top-N);
  `clip_start` == longest-turn start; single-segment speaker still works.
- `tests/test_play_clip.py`: `play_speaker_clip` uses `Popen` with `-loop 0` and
  does NOT block (mock `subprocess.Popen`, assert args incl. loop flag, video vs
  `-nodisp` audio, returns a handle); no-media returns None without spawning.
- `tests/` (new) for `rewind_to`: rewinding to each stage sets `completed_stage`
  correctly and deletes the right artifacts (use tmp meeting dir with dummy files),
  and `save()` persists.
- `tests/` (new) for `_resolve_metadata`: `--default`/non-tty fills defaults
  without prompting; interactive prompts only for unset fields (mock `input`);
  CLI-provided fields untouched; empty date → today (inject/monkeypatch date).
- `--redo` argparse validation: errors without `--resume`.

## Out of scope

- The web GUI (separate future sub-project — it will reuse the same `src/review.py`).
- Changing diarization/identification algorithms.
- Scrubbing/seeking within a clip beyond cycling between turns.
