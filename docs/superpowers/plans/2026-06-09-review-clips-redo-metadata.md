# Review Clips, Stage Re-run & Metadata Prompts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make speaker clips identify the right person (longest-turn + cycling + type-while-playing), let operators re-run a chosen pipeline stage for past meetings (`--redo`), and prompt for meeting metadata when not passed (`--default` to opt out).

**Architecture:** Three independent units in the CLI layer. (A) `src/review.py` gains ordered `clip_candidates`; `run_local.py`'s `play_speaker_clip` becomes non-blocking + looping and the review loop cycles clips and always cleans up the player. (B) `src/checkpoint.py` gains `rewind_to(stage)`, wired to a new `--redo` flag. (C) `run_local.py` gains `_resolve_metadata` + `--default`, with sentinel argparse defaults.

**Tech Stack:** Python 3, pytest, ffplay (ffmpeg) via subprocess. Use `.venv/bin/python` for all commands.

Spec: `docs/superpowers/specs/2026-06-09-review-clips-redo-metadata-design.md`

---

## File Structure

- `src/review.py` — add `SpeakerView.clip_candidates`; clip selection in `build_review_state`; remove now-unused `_representative_segment`.
- `run_local.py` — `play_speaker_clip` (non-blocking/looping) + `_stop_player`; `_interactive_speaker_review` clip cycling + cleanup; `--redo` flag/validation/wiring; `--default` + `_resolve_metadata`; sentinel metadata defaults; drop the duplicate date-default block.
- `src/checkpoint.py` — `rewind_to(stage)`.
- Tests: `tests/test_review.py` (clip_candidates), `tests/test_play_clip.py` (rewrite for Popen), `tests/test_rewind_to.py` (new), `tests/test_metadata_prompt.py` (new), `tests/test_redo_arg.py` (new).
- `README.md` — document the three features.

**Stage map (Task 4/5), referenced across tasks:**
`diarize→DIARIZED(2)`, `transcribe→TRANSCRIBED(3)`, `identify→IDENTIFIED(4)`, `summary→SUMMARIZED(5)`, `all→DIARIZED(2)` (re-run full analysis; ingested audio.wav is preserved). `rewind_to(stage)` never deletes INGESTED outputs (audio.wav / captions.vtt) or global voice profiles.

---

## Task 1: `clip_candidates` — longest-turn clip selection

**Files:**
- Modify: `src/review.py` (`SpeakerView`, `build_review_state`; remove `_representative_segment`)
- Test: `tests/test_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review.py`:

```python
def test_build_review_state_clip_candidates_longest_first():
    segs = [
        _seg("S0", 0, 5, "short a"),
        _seg("S0", 5, 35, "the long identifying turn"),
        _seg("S0", 40, 42, "short b"),
    ]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    views = review.build_review_state(segs, mappings, {}, _FakeProfileDB({}), show_text=True)
    v = views[0]
    # candidates ordered by segment duration desc: 30s@5, 5s@0, 2s@40
    assert v.clip_candidates == [5.0, 0.0, 40.0]
    # default clip is the longest turn
    assert v.clip_start == 5.0
    # sample text comes from the longest turn
    assert v.sample_text == "the long identifying turn"


def test_build_review_state_clip_candidates_capped_at_8():
    segs = [_seg("S0", i * 10, i * 10 + (i + 1), "x") for i in range(12)]  # 12 segments, increasing length
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    views = review.build_review_state(segs, mappings, {}, _FakeProfileDB({}), show_text=False)
    assert len(views[0].clip_candidates) == 8  # top 8 only


def test_build_review_state_single_segment_clip_start():
    segs = [_seg("S0", 3.0, 9.0, "hello")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    views = review.build_review_state(segs, mappings, {}, _FakeProfileDB({}), show_text=True)
    assert views[0].clip_candidates == [3.0]
    assert views[0].clip_start == 3.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review.py -k "clip_candidates or single_segment_clip" -v`
Expected: FAIL — `AttributeError: 'SpeakerView' object has no attribute 'clip_candidates'`

- [ ] **Step 3: Implement**

In `src/review.py`, add the field to `SpeakerView` (after `clip_start`):

```python
    clip_start: Optional[float]
    clip_candidates: list[float] = field(default_factory=list)
    sample_text: Optional[str] = None
```

(Keep the other fields; `clip_candidates` and the already-defaulted `sample_text`/`soft_hints`/`needs_review` all have defaults, so field ordering stays valid — place `clip_candidates` immediately after `clip_start` and before `sample_text`.)

Replace the per-label body of `build_review_state` (the `for label, segs in by_label.items():` loop) with:

```python
    views: list[SpeakerView] = []
    for label, segs in by_label.items():
        total = sum(s.end_time - s.start_time for s in segs)
        # Candidates: this speaker's segments by duration desc (longest turn is
        # the most identifying), top 8 start times. The default clip + sample
        # come from the longest turn.
        ordered = sorted(segs, key=lambda s: s.end_time - s.start_time, reverse=True)
        clip_candidates = [s.start_time for s in ordered[:8]]
        longest = ordered[0] if ordered else None
        mapping = mappings.get(label)
        sample_text = None
        if show_text and longest is not None and getattr(longest, "text", None) and longest.text.strip():
            sample_text = longest.text
        views.append(SpeakerView(
            label=label,
            current_name=getattr(mapping, "speaker_name", None) if mapping else None,
            current_confidence=getattr(mapping, "confidence", 0.0) if mapping else 0.0,
            current_method=getattr(mapping, "id_method", None) if mapping else None,
            seg_count=len(segs),
            total_speech_seconds=total,
            clip_start=longest.start_time if longest is not None else None,
            clip_candidates=clip_candidates,
            sample_text=sample_text,
            soft_hints=hints.get(label, []),
            needs_review=getattr(mapping, "needs_review", False) if mapping else False,
        ))
```

Then delete the now-unused `_representative_segment` function. Confirm with `grep -n "_representative_segment" src/review.py` → no matches.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_review.py -v`
Expected: PASS (all review tests, incl. the prior ones — the single-segment `clip_start==0.0` cases still hold since the lone segment is the longest).

- [ ] **Step 5: Commit**

```bash
git add src/review.py tests/test_review.py
git commit -m "feat(review): clip_candidates (longest-turn first) on SpeakerView"
```

---

## Task 2: Non-blocking, looping `play_speaker_clip` + `_stop_player`

**Files:**
- Modify: `run_local.py` (`play_speaker_clip`; add `_stop_player`)
- Test: `tests/test_play_clip.py` (rewrite — the old tests patch `subprocess.run`, which is no longer used)

- [ ] **Step 1: Rewrite the tests**

Replace the ENTIRE contents of `tests/test_play_clip.py` with:

```python
"""play_speaker_clip launches a non-blocking, looping player and returns the handle."""
from __future__ import annotations

import run_local


class _FakeProc:
    def __init__(self):
        self.terminated = False
    def poll(self):
        return None  # still running
    def terminate(self):
        self.terminated = True
    def wait(self, timeout=None):
        return 0
    def kill(self):
        self.terminated = True


def test_play_speaker_clip_uses_video_nonblocking(monkeypatch):
    captured = {}
    fake = _FakeProc()
    monkeypatch.setattr(run_local.subprocess, "Popen", lambda cmd, **kw: captured.setdefault("cmd", cmd) or fake)
    handle = run_local.play_speaker_clip("/m/source.mp4", "/m/audio.wav", 30.0, duration=10.0, title="x")
    assert handle is fake                       # returns the Popen handle (non-blocking)
    assert captured["cmd"][-1] == "/m/source.mp4"
    assert "-loop" in captured["cmd"] and "0" in captured["cmd"]
    assert "-nodisp" not in captured["cmd"]     # video → display on


def test_play_speaker_clip_audio_fallback_nodisp(monkeypatch):
    captured = {}
    fake = _FakeProc()
    monkeypatch.setattr(run_local.subprocess, "Popen", lambda cmd, **kw: captured.setdefault("cmd", cmd) or fake)
    handle = run_local.play_speaker_clip(None, "/m/audio.wav", 30.0, duration=10.0)
    assert handle is fake
    assert captured["cmd"][-1] == "/m/audio.wav"
    assert "-nodisp" in captured["cmd"]


def test_play_speaker_clip_no_media_returns_none(monkeypatch, capsys):
    spawned = {"n": 0}
    monkeypatch.setattr(run_local.subprocess, "Popen", lambda *a, **k: spawned.__setitem__("n", spawned["n"] + 1))
    handle = run_local.play_speaker_clip(None, None, 30.0)
    assert handle is None
    assert spawned["n"] == 0
    assert "no media" in capsys.readouterr().out.lower()


def test_play_speaker_clip_ffplay_missing_returns_none(monkeypatch, capsys):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(run_local.subprocess, "Popen", boom)
    handle = run_local.play_speaker_clip("/m/source.mp4", None, 30.0)
    assert handle is None
    assert "ffplay not found" in capsys.readouterr().out.lower()


def test_stop_player_terminates_running(monkeypatch):
    fake = _FakeProc()
    run_local._stop_player(fake)
    assert fake.terminated is True
    # tolerant of None
    run_local._stop_player(None)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_play_clip.py -v`
Expected: FAIL — `play_speaker_clip` still uses `subprocess.run`/returns None unconditionally; `_stop_player` undefined.

- [ ] **Step 3: Implement**

In `run_local.py`, replace the entire `play_speaker_clip` function with:

```python
def play_speaker_clip(
    video_path: str | None,
    audio_path: str | None,
    start_time: float,
    duration: float = 40.0,
    title: str = "",
):
    """Play a looping clip of a speaker WITHOUT blocking, returning the player handle.

    Video if available, else the audio segment (ffplay -nodisp). Loops (`-loop 0`)
    so the clip stays up while the operator types the name; the caller stops it via
    _stop_player. Returns the subprocess.Popen handle, or None if there's no media
    or ffplay isn't installed.
    """
    media = video_path or audio_path
    if not media:
        print("    No media to play (no video or audio found).")
        return None

    seek = max(0, start_time - 3.0)
    cmd = ["ffplay", "-ss", str(seek), "-t", str(duration), "-loop", "0", "-loglevel", "quiet"]
    if not video_path:
        cmd.append("-nodisp")
    if title:
        cmd += ["-window_title", title]
    cmd.append(media)

    kind = "video" if video_path else "audio"
    print(f"    Playing {kind} clip ({duration:.0f}s from {int(seek // 60):02d}:{int(seek % 60):02d}) "
          f"— looping; type a name or skip to stop it...")
    try:
        return subprocess.Popen(cmd)
    except FileNotFoundError:
        print("    ffplay not found — install ffmpeg to enable clip playback")
        return None


def _stop_player(proc) -> None:
    """Terminate a clip player started by play_speaker_clip (tolerant of None/exited)."""
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
    except Exception:
        pass
```

(`subprocess` is already imported at module top from prior work.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_play_clip.py -v`
Expected: PASS (5 passed). Also `.venv/bin/python -c "import run_local"` → clean.

- [ ] **Step 5: Commit**

```bash
git add run_local.py tests/test_play_clip.py
git commit -m "feat(run_local): non-blocking looping play_speaker_clip + _stop_player"
```

---

## Task 3: Review loop — cycle clips, type while playing, always clean up

**Files:**
- Modify: `run_local.py` (`_interactive_speaker_review` per-speaker loop body)

This refactors the per-speaker section of the loop. The function signature and the rest (build_review_state call, `changes`, merge logic, rename logic) stay the same.

- [ ] **Step 1: Replace the per-speaker loop body**

In `_interactive_speaker_review`, the structure is `i = 0 ... while i < len(views):` with a per-speaker block ending in `if quit_requested: break` / `if advance: i += 1`. Replace the per-speaker block (from `view = views[i]` through the `if advance: i += 1` line) with:

```python
        view = views[i]
        label = view.label
        name = view.current_name or "(unidentified)"
        mins = view.total_speech_seconds / 60

        print(f"\n[{i+1}/{len(views)}] {label}: {name}")
        print(f"  Segments: {view.seg_count}, Speech: {mins:.1f}m", end="")
        if view.current_confidence > 0:
            print(f", Confidence: {view.current_confidence:.2f}, Method: {view.current_method or 'none'}", end="")
        print()

        top_hint = None
        if view.soft_hints and not (view.current_name and view.current_confidence >= 0.85):
            for hint_name, hint_score in view.soft_hints[:3]:
                marker = "*" if hint_score >= 0.85 else "?"
                print(f"  {marker} Voice match: {hint_name} ({hint_score:.2f})")
            top_hint = view.soft_hints[0]

        if view.sample_text:
            preview = view.sample_text[:120] + "..." if len(view.sample_text) > 120 else view.sample_text
            print(f"  Sample [{_format_ts(view.clip_start or 0)}]: \"{preview}\"")
        elif view.clip_start is not None:
            print(f"  Clip at [{_format_ts(view.clip_start)}]")

        advance = True
        clip_idx = 0
        current_player = None
        has_clip = bool((video_path or audio_path) and view.clip_candidates)
        try:
            while True:
                parts = ["  "]
                if has_clip:
                    parts.append("[V]iew" + (" next" if clip_idx > 0 else ""))
                if top_hint:
                    parts.append(f"[Y=accept {top_hint[0]}]")
                if len(views) > 1:
                    parts.append("[M]erge")
                parts.append("[Enter=skip] [Q=quit] or type name: ")
                choice = input(" ".join(parts)).strip()

                if choice.lower() in ("v", "view") and has_clip:
                    _stop_player(current_player)
                    n = clip_idx % len(view.clip_candidates)
                    start = view.clip_candidates[n]
                    print(f"  Clip {n + 1}/{len(view.clip_candidates)} at [{_format_ts(start)}]")
                    current_player = play_speaker_clip(video_path, audio_path, start, title=f"{label} → {name}")
                    clip_idx += 1
                    continue
                elif choice.lower() == "q":
                    print("  Quitting review.")
                    quit_requested = True
                    break
                elif choice == "":
                    break  # skip
                elif choice.lower() == "m" and len(views) > 1:
                    others = [v for v in views if v.label != label]
                    print("  Merge THIS speaker into which?")
                    for k, ov in enumerate(others):
                        print(f"    {k+1}. {ov.label}: {ov.current_name or '(unidentified)'}")
                    sel = input("    Number (or Enter to cancel): ").strip()
                    if not sel:
                        continue
                    try:
                        target = others[int(sel) - 1]
                    except (ValueError, IndexError):
                        print("    Invalid selection.")
                        continue
                    try:
                        res = review.merge_speakers(segments, embeddings, mappings, label, target.label)
                    except ValueError as e:
                        print(f"    {e}")
                        continue
                    changes.append({"label": label, "merged_into": target.label})
                    print(f"  Merged {label} → {target.label} ({res.combined_name or 'unidentified'})")
                    views = review.build_review_state(segments, mappings, embeddings, profile_db, show_text=show_text)
                    advance = False
                    break
                elif choice.lower() in ("y", "yes") and top_hint:
                    res = review.rename_speaker(mappings, segments, label, top_hint[0], roster=roster)
                    mappings[label].id_method = "human_confirmed"
                    changes.append({"label": label, "old_name": res.old_name, "new_name": res.new_name})
                    print(f"  Confirmed: {label} -> {res.new_name}")
                    break
                else:
                    res = review.rename_speaker(mappings, segments, label, choice, roster=roster)
                    changes.append({"label": label, "old_name": res.old_name, "new_name": res.new_name})
                    print(f"  Updated: {label} -> {res.new_name}")
                    if res.alias_suggestion:
                        from src.roster import add_alias
                        if add_alias(None, res.new_name, res.alias_suggestion, body_slug=body_slug):
                            target_label = body_slug or "council_roster.json"
                            print(f"  Auto-added alias: '{res.alias_suggestion}' -> '{res.new_name}' ({target_label})")
                    break
        finally:
            _stop_player(current_player)

        if quit_requested:
            break
        if advance:
            i += 1
```

Key changes vs the old body: `[V]` now stops the current player, plays `clip_candidates[clip_idx]` (cycling, non-blocking), prints which clip N/total, and re-prompts immediately; the per-speaker input loop is wrapped in `try/finally: _stop_player(...)` so the player is always stopped when leaving the speaker; the `[V]iew` prompt is gated on `has_clip` (non-empty `clip_candidates`).

- [ ] **Step 2: Verify import + suite**

Run: `.venv/bin/python -c "import run_local"` → clean.
Run: `.venv/bin/python -m pytest -q` → full suite stays green (the loop has no direct unit test; its building blocks — build_review_state, play_speaker_clip, _stop_player, rename/merge — are covered).

- [ ] **Step 3: Manual smoke (optional, recommended)**

On a processed meeting: `.venv/bin/python run_local.py --review <ID>` → for a multi-segment speaker, `[V]` plays the longest turn (clip 1/N) and keeps looping while the prompt waits; pressing `[V]` again plays clip 2/N; typing a name stops the clip and records it.

- [ ] **Step 4: Commit**

```bash
git add run_local.py
git commit -m "feat(run_local): cycle review clips (longest-first) with non-blocking playback"
```

---

## Task 4: `PipelineState.rewind_to(stage)`

**Files:**
- Modify: `src/checkpoint.py` (add `rewind_to`; leave `rewind_for_retag` unchanged)
- Test: `tests/test_rewind_to.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rewind_to.py`:

```python
"""PipelineState.rewind_to deletes stage>=N artifacts and rewinds completed_stage."""
from __future__ import annotations

import json
from src.checkpoint import PipelineState, PipelineStage


def _touch(p, name):
    (p / name).write_text("x", encoding="utf-8")


def test_rewind_to_identify_deletes_stage4_plus_keeps_diar_audio(tmp_path):
    for n in ("audio.wav", "diarization.json", "embeddings.json", "transcript_raw.json",
              "transcript_named.json", "pre_identifications.json", "summary.json"):
        _touch(tmp_path, n)
    (tmp_path / "exports").mkdir()
    _touch(tmp_path / "exports", "transcript.md")

    state = PipelineState(tmp_path)
    state.completed_stage = PipelineStage.EXPORTED
    state.save()

    state.rewind_to(PipelineStage.IDENTIFIED)

    assert state.completed_stage == PipelineStage.TRANSCRIBED  # one before IDENTIFIED
    # stage 4+ artifacts gone
    assert not (tmp_path / "transcript_named.json").exists()
    assert not (tmp_path / "pre_identifications.json").exists()
    assert not (tmp_path / "summary.json").exists()
    assert not (tmp_path / "exports" / "transcript.md").exists()
    # earlier stages preserved
    assert (tmp_path / "audio.wav").exists()
    assert (tmp_path / "diarization.json").exists()
    assert (tmp_path / "transcript_raw.json").exists()
    # persisted
    data = json.loads((tmp_path / "pipeline_state.json").read_text())
    assert data["completed_stage"] == int(PipelineStage.TRANSCRIBED)


def test_rewind_to_diarize_keeps_audio_resets_progress(tmp_path):
    for n in ("audio.wav", "diarization.json", "embeddings.json", "transcript_raw.json"):
        _touch(tmp_path, n)
    state = PipelineState(tmp_path)
    state.completed_stage = PipelineStage.TRANSCRIBED
    state.transcription_progress = 50
    state.total_segments = 100
    state.save()

    state.rewind_to(PipelineStage.DIARIZED)

    assert state.completed_stage == PipelineStage.INGESTED
    assert not (tmp_path / "diarization.json").exists()
    assert not (tmp_path / "embeddings.json").exists()
    assert not (tmp_path / "transcript_raw.json").exists()
    assert (tmp_path / "audio.wav").exists()           # ingest output preserved
    assert state.transcription_progress == 0           # reset
    assert state.total_segments == 0


def test_rewind_to_summary_only_clears_summary_and_exports(tmp_path):
    for n in ("transcript_named.json", "summary.json"):
        _touch(tmp_path, n)
    (tmp_path / "exports").mkdir(); _touch(tmp_path / "exports", "x.md")
    state = PipelineState(tmp_path)
    state.completed_stage = PipelineStage.EXPORTED
    state.save()

    state.rewind_to(PipelineStage.SUMMARIZED)

    assert state.completed_stage == PipelineStage.IDENTIFIED
    assert (tmp_path / "transcript_named.json").exists()  # stage 4 kept
    assert not (tmp_path / "summary.json").exists()
    assert not (tmp_path / "exports" / "x.md").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_rewind_to.py -v`
Expected: FAIL — `AttributeError: 'PipelineState' object has no attribute 'rewind_to'`

- [ ] **Step 3: Implement**

In `src/checkpoint.py`, add this method to `PipelineState` (e.g. right after `rewind_for_retag`):

```python
    # Artifacts produced by each stage (INGESTED outputs and global voice
    # profiles are intentionally never deleted by rewind_to).
    _STAGE_ARTIFACTS = {
        PipelineStage.DIARIZED: ("diarization.json", "embeddings.json"),
        PipelineStage.TRANSCRIBED: ("transcript_raw.json",),
        PipelineStage.IDENTIFIED: ("transcript_named.json", "pre_identifications.json", "llm_partial_results.json"),
        PipelineStage.SUMMARIZED: ("summary.json",),
    }

    def rewind_to(self, stage: "PipelineStage") -> None:
        """Rewind so `stage` and everything after it re-run.

        Sets completed_stage to the stage immediately before `stage`, deletes the
        on-disk artifacts of `stage`..EXPORTED (and the exports/ directory), and
        resets transcription progress if TRANSCRIBED or earlier is being redone.
        Never deletes audio.wav/captions.vtt (ingest) or the global voice profiles.
        Files are deleted BEFORE save() (crash-safe, mirrors rewind_for_retag).
        """
        if stage < PipelineStage.DIARIZED:
            stage = PipelineStage.DIARIZED  # never re-ingest via rewind_to

        # Delete artifacts for this stage and all later file-producing stages.
        for s, names in self._STAGE_ARTIFACTS.items():
            if s >= stage:
                for name in names:
                    p = self.meeting_dir / name
                    if p.exists():
                        p.unlink()
        # exports/ (EXPORTED) is always downstream of any rewind_to target.
        exports = self.meeting_dir / "exports"
        if exports.exists():
            for child in exports.iterdir():
                if child.is_file():
                    child.unlink()

        if stage <= PipelineStage.TRANSCRIBED:
            self.transcription_progress = 0
            self.total_segments = 0

        self.completed_stage = PipelineStage(int(stage) - 1)
        self.save()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_rewind_to.py -v`
Expected: PASS (3 passed). Also `.venv/bin/python -m pytest tests/test_body_tagging.py -q` → confirm `rewind_for_retag` behavior is untouched.

- [ ] **Step 5: Commit**

```bash
git add src/checkpoint.py tests/test_rewind_to.py
git commit -m "feat(checkpoint): add rewind_to(stage) for stage re-runs"
```

---

## Task 5: `--redo <stage>` flag + wiring

**Files:**
- Modify: `run_local.py` (argparse, validation, resume-path wiring)
- Test: `tests/test_redo_arg.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_redo_arg.py`:

```python
"""--redo requires --resume; maps stage names to rewind_to."""
from __future__ import annotations

import sys
import pytest
import run_local


def test_redo_requires_resume(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_local.py", "--input", "x.mp4", "--redo", "identify"])
    with pytest.raises(SystemExit):   # argparse parser.error → SystemExit(2)
        run_local.main()


def test_redo_calls_rewind_to_identify(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    mid = "2026-02-10-regular-session"
    mdir = tmp_path / mid
    mdir.mkdir(parents=True)
    (mdir / "pipeline_state.json").write_text('{"completed_stage": 7}', encoding="utf-8")
    (mdir / "transcript_named.json").write_text('{"audio_source": "src.mp4", "city": "B", "date": "2026-02-10", "meeting_type": "Regular", "segments": [], "speakers": {}}', encoding="utf-8")

    calls = {}
    from src.checkpoint import PipelineStage
    def fake_rewind(self, stage):
        calls["stage"] = stage
    monkeypatch.setattr("src.checkpoint.PipelineState.rewind_to", fake_rewind)
    # stop before running the heavy pipeline
    monkeypatch.setattr(run_local, "run_pipeline", lambda args: calls.setdefault("ran", True))

    monkeypatch.setattr(sys, "argv", ["run_local.py", "--resume", mid, "--redo", "identify"])
    run_local.main()

    assert calls["stage"] == PipelineStage.IDENTIFIED
    assert calls.get("ran") is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_redo_arg.py -v`
Expected: FAIL — `--redo` unknown arg / no rewind call.

- [ ] **Step 3: Add the argparse flag + validation**

In `main()`'s argparse block (near `--force-retag`), add:

```python
    parser.add_argument(
        "--redo",
        choices=["diarize", "transcribe", "identify", "summary", "all"],
        default=None,
        help="Re-run a past meeting from this stage onward (requires --resume MEETING_ID). "
             "'all' re-runs the full analysis from diarization (ingested audio is kept).",
    )
```

After `args = parser.parse_args()` (near the existing `if args.force_retag and not args.body:` check), add:

```python
    if args.redo and not args.resume:
        parser.error("--redo requires --resume <MEETING_ID>")
```

- [ ] **Step 4: Wire the rewind into the resume path**

In the `if args.resume:` block, at the very END (after `args.meeting_id = args.resume` and the `print(f"Resuming meeting: ...")`), add:

```python
        if args.redo:
            from src.checkpoint import PipelineState, PipelineStage
            _redo_map = {
                "diarize": PipelineStage.DIARIZED,
                "transcribe": PipelineStage.TRANSCRIBED,
                "identify": PipelineStage.IDENTIFIED,
                "summary": PipelineStage.SUMMARIZED,
                "all": PipelineStage.DIARIZED,
            }
            _redo_state = PipelineState(meeting_dir)
            _redo_state.rewind_to(_redo_map[args.redo])
            print(f"Re-running from stage: {args.redo}")
```

(`meeting_dir` is already defined at the top of the resume block.)

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_redo_arg.py -v`
Expected: PASS (2 passed). Also `.venv/bin/python -c "import run_local"` and `.venv/bin/python -m pytest -q` → green.

- [ ] **Step 6: Commit**

```bash
git add run_local.py tests/test_redo_arg.py
git commit -m "feat(run_local): --redo <stage> to re-run past meetings"
```

---

## Task 6: Metadata prompts + `--default`

**Files:**
- Modify: `run_local.py` (argparse sentinel defaults, `--default` flag, `_resolve_metadata`, remove duplicate date-default block)
- Test: `tests/test_metadata_prompt.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metadata_prompt.py`:

```python
"""_resolve_metadata: prompt for unset fields interactively; --default / non-tty use defaults."""
from __future__ import annotations

import argparse
import run_local


def _args(**kw):
    base = dict(city=None, date="", meeting_type=None, default=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_default_flag_fills_without_prompting(monkeypatch):
    def no_input(*a, **k):
        raise AssertionError("should not prompt with --default")
    monkeypatch.setattr("builtins.input", no_input)
    monkeypatch.setattr(run_local, "_today_iso", lambda: "2026-06-09")
    args = _args(default=True)
    run_local._resolve_metadata(args)
    assert args.city == "Bloomington"
    assert args.meeting_type == "Regular Session"
    assert args.date == "2026-06-09"


def test_non_tty_fills_without_prompting(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt")))
    monkeypatch.setattr(run_local, "_today_iso", lambda: "2026-06-09")
    args = _args()
    run_local._resolve_metadata(args)
    assert args.city == "Bloomington"
    assert args.meeting_type == "Regular Session"
    assert args.date == "2026-06-09"


def test_interactive_prompts_only_unset(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(run_local, "_today_iso", lambda: "2026-06-09")
    answers = iter(["Carmel", "", ""])   # city typed; date Enter→today; type Enter→default
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    args = _args(meeting_type="Plan Commission")  # already provided → not prompted
    run_local._resolve_metadata(args)
    assert args.city == "Carmel"
    assert args.date == "2026-06-09"
    assert args.meeting_type == "Plan Commission"


def test_interactive_keeps_cli_values(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing to prompt")))
    monkeypatch.setattr(run_local, "_today_iso", lambda: "2026-06-09")
    args = _args(city="Bloomington", date="2026-01-01", meeting_type="Special")
    run_local._resolve_metadata(args)
    assert (args.city, args.date, args.meeting_type) == ("Bloomington", "2026-01-01", "Special")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_metadata_prompt.py -v`
Expected: FAIL — `_resolve_metadata`/`_today_iso` undefined.

- [ ] **Step 3: Implement helpers**

In `run_local.py`, add near the top-level helpers (e.g. after `_meeting_body_slug`):

```python
CITY_DEFAULT = "Bloomington"
MEETING_TYPE_DEFAULT = "Regular Session"


def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()


def _resolve_metadata(args) -> None:
    """Fill args.city/date/meeting_type for a new run.

    Interactive + no --default → prompt for each UNSET field (Enter accepts the
    shown default). --default or non-interactive → use defaults silently. Fields
    already provided on the CLI are left as-is. meeting_id is not touched.
    """
    interactive = sys.stdin.isatty() and not getattr(args, "default", False)
    today = _today_iso()

    if not interactive:
        if args.city is None:
            args.city = CITY_DEFAULT
        if args.meeting_type is None:
            args.meeting_type = MEETING_TYPE_DEFAULT
        if not args.date:
            args.date = today
        return

    if args.city is None:
        ans = input(f"  City [{CITY_DEFAULT}]: ").strip()
        args.city = ans or CITY_DEFAULT
    if not args.date:
        ans = input(f"  Date YYYY-MM-DD [{today}]: ").strip()
        args.date = ans or today
    if args.meeting_type is None:
        ans = input(f"  Meeting type [{MEETING_TYPE_DEFAULT}]: ").strip()
        args.meeting_type = ans or MEETING_TYPE_DEFAULT
```

- [ ] **Step 4: Run the helper tests**

Run: `.venv/bin/python -m pytest tests/test_metadata_prompt.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Update argparse defaults + add --default, and wire the call**

In `main()` argparse, change the metadata defaults to sentinels and add `--default`:

```python
    parser.add_argument("--city", default=None, help=f"City name (default: {CITY_DEFAULT}; prompted if omitted)")
    parser.add_argument("--date", default="", help="Meeting date (YYYY-MM-DD; prompted if omitted)")
    parser.add_argument("--meeting-type", default=None,
                        help="Meeting type or name, free text "
                             "(e.g. \"Regular Session\", \"Plan Commission\"; prompted if omitted)")
```
(Leave `--meeting-id` as-is.)

Add the `--default` flag near the other processing flags:

```python
    parser.add_argument("--default", action="store_true",
                        help="Skip metadata prompts and use defaults (Bloomington / Regular Session / today)")
```

In the `--browse-catstv` block, that code sets `args.meeting_type = selected["name"]` and `args.date` from the selection — unchanged. (Those fields become non-None there, so `_resolve_metadata` won't prompt for them.)

Replace the existing date-default block at the end of `main()`:
```python
    if not args.date:
        from datetime import date
        args.date = date.today().isoformat()
        print(f"No --date provided, using today: {args.date}")
```
with a call to the resolver (which now handles date too):
```python
    # Resolve city/date/meeting-type (prompt unless --default / non-interactive).
    _resolve_metadata(args)
```

This call sits AFTER the `if not args.input:` validation (so we don't prompt when erroring out) and BEFORE `run_pipeline(args)`. `--resume` returns? No — resume continues to run_pipeline; but resume sets city/date/meeting_type from the meeting and `args.input`, so `_resolve_metadata` would see them as non-None (set in the resume block) and not prompt. Confirm the resume block assigns `args.city`/`args.date`/`args.meeting_type` (it does, from the named transcript) — when resuming a meeting with no named transcript, those stay None; in that case `_resolve_metadata` fills defaults (acceptable — metadata isn't re-asked on resume). To be safe and match "resume doesn't prompt", guard the call:

```python
    # Resolve city/date/meeting-type (prompt unless --default / non-interactive).
    # Skip prompting on resume (metadata comes from the existing meeting).
    if args.resume:
        if args.city is None:
            args.city = CITY_DEFAULT
        if args.meeting_type is None:
            args.meeting_type = MEETING_TYPE_DEFAULT
        if not args.date:
            args.date = _today_iso()
    else:
        _resolve_metadata(args)
```

- [ ] **Step 6: Verify**

Run: `.venv/bin/python -c "import run_local"` → clean.
Run: `.venv/bin/python -m pytest -q` → full suite green.
Run: `.venv/bin/python run_local.py --help | grep -E "default|city|meeting-type"` → confirm `--default` present and the metadata help text mentions prompting.

- [ ] **Step 7: Commit**

```bash
git add run_local.py tests/test_metadata_prompt.py
git commit -m "feat(run_local): prompt for city/date/meeting-type; --default opt-out"
```

---

## Task 7: README + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the three features**

In `README.md`, under the `### Reviewing & naming speakers` subsection, update/extend the clip bullet and add notes. Replace the existing `[V]iew` bullet line:
```markdown
- **`[V]iew`** — play a ~20s clip of that speaker (video if available, otherwise
  audio),
```
with:
```markdown
- **`[V]iew`** — play a clip of that speaker, starting with their **longest turn**
  (most likely to show them clearly). It loops and plays in the background, so you
  can **type the name while it plays** (handy when the name is shown on screen).
  Press `[V]` again to jump to the next-longest turn. The clip stops when you enter
  a name or skip.
```

Then, after that subsection's closing paragraph, add:

```markdown
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

### Meeting metadata prompts

For a new run, if you don't pass `--city`, `--date`, or `--meeting-type`,
CouncilScribe prompts for each (press Enter to accept the shown default). Pass
`--default` to skip the prompts and use the defaults (Bloomington / Regular
Session / today). Non-interactive runs use the defaults automatically.
```

- [ ] **Step 2: Final verification**

Run: `.venv/bin/python -m pytest -q` → all pass.
Run: `.venv/bin/python -c "import run_local"` → clean.
Run: `grep -n "Re-running a past meeting\|Meeting metadata prompts\|longest turn" README.md` → confirm the additions.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: clip cycling, --redo, and metadata prompts"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** A1 clip_candidates = Task 1; A2 non-blocking/looping = Task 2; A3 cycling + cleanup = Task 3; B rewind_to = Task 4; B `--redo` wiring = Task 5; C metadata prompts + `--default` = Task 6; README = Task 7. The "#1 already shipped" item needs no task.
- **Type consistency:** `play_speaker_clip(video_path, audio_path, start_time, duration=40.0, title="") -> Optional[Popen]` and `_stop_player(proc)` are used identically in Tasks 2 and 3. `SpeakerView.clip_candidates: list[float]` (Task 1) is read in Task 3. `rewind_to(stage)` (Task 4) is called in Task 5. `_resolve_metadata(args)`/`_today_iso()`/`CITY_DEFAULT`/`MEETING_TYPE_DEFAULT` (Task 6) are consistent within the task.
- **Pre-flight checks the implementer MUST do:** confirm `subprocess` is imported at `run_local.py` module top (it is, from earlier work); confirm the resume block defines `meeting_dir` before the Task-5 insertion; confirm `Segment`/`SpeakerMapping`/`Meeting` shapes for any test fixtures; confirm `field` is imported in `src/review.py` (it is).
- **Out of scope:** web GUI, diarization/identification algorithm changes, in-clip scrubbing.
```
