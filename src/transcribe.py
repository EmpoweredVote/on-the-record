"""Stage 3: Segment-level transcription using faster-whisper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import torch

from . import config
from .audio_utils import load_wav, slice_audio
from .models import Segment, Word


def remove_segment_overlaps(segments: list[Segment]) -> list[Segment]:
    """Trim later segments so each instant of audio is transcribed once.

    Diarization may represent simultaneous speech with overlapping turns. A
    readable video transcript needs a single chronological stream, so the
    earlier turn owns the overlap and the later turn begins after it.
    """
    if not segments:
        return segments

    occupied_until = segments[0].end_time
    for seg in segments[1:]:
        if seg.start_time < occupied_until:
            seg.start_time = round(min(occupied_until, seg.end_time), 3)
        occupied_until = max(occupied_until, seg.end_time)

    return segments


def load_whisper_model():
    """Load faster-whisper model. GPU: large-v3 float16, CPU: medium int8."""
    from faster_whisper import WhisperModel

    if torch.cuda.is_available():
        model = WhisperModel(
            config.WHISPER_MODEL_GPU,
            device="cuda",
            compute_type=config.WHISPER_COMPUTE_GPU,
        )
    else:
        model = WhisperModel(
            config.WHISPER_MODEL_CPU,
            device="cpu",
            compute_type=config.WHISPER_COMPUTE_CPU,
        )
    return model


def transcribe_full_audio(model, wav_path: str | Path) -> list[Word]:
    """Transcribe the entire audio in one pass with word-level timestamps.

    Returns a flat, chronological list of Words whose start/end are already on
    the meeting's global timeline (the WAV is the meeting). No per-segment
    rebasing — that is the source of the drift this replaces.
    """
    samples, sr = load_wav(wav_path)
    result_segments, _ = model.transcribe(
        samples,
        word_timestamps=True,
        language="en",
    )
    words: list[Word] = []
    for rs in result_segments:
        for w in (rs.words or []):
            words.append(
                Word(word=w.word.strip(), start=round(w.start, 3), end=round(w.end, 3))
            )
    return words


def recover_orphan_turns(
    model,
    wav_path: str | Path,
    segments: list[Segment],
    continuous_words: list[Word],
    *,
    min_seconds: float = 0.1,
    overlap_skip_frac: float = 0.5,
) -> list[Segment]:
    """Recover faint short utterances dropped by whole-audio transcription.

    Whole-audio Whisper locks onto the dominant voice and drops faint short
    turns (a roll-call "Here.", a quiet "Second."), leaving them wordless. For
    each empty turn long enough to embed, transcribe just its slice and attach
    the result, rebasing word times to the turn's start.

    GUARD: only recover a turn the continuous pass left genuinely silent. If any
    continuous word overlaps the turn by >= overlap_skip_frac of the turn's
    duration, that turn sits over another speaker's speech (an overlap/bleed
    region, e.g. a listener's backchannel during the interviewee's sentence) and
    slice-transcribing it would steal the dominant speaker's word. Skip those.
    """
    from .word_assign import _overlap

    samples, sr = load_wav(wav_path)
    for seg in segments:
        if seg.words:
            continue
        turn_dur = seg.end_time - seg.start_time
        if turn_dur < min_seconds:
            continue
        # Skip turns that overlap continuous-pass speech (overlap/bleed regions).
        max_cov = 0.0
        for w in continuous_words:
            cov = _overlap(seg.start_time, seg.end_time, w.start, w.end)
            if cov > max_cov:
                max_cov = cov
        if turn_dur > 0 and max_cov / turn_dur >= overlap_skip_frac:
            continue

        audio_slice = slice_audio(samples, sr, seg.start_time, seg.end_time)
        result_segments, _ = model.transcribe(
            audio_slice, word_timestamps=True, language="en"
        )
        words: list[Word] = []
        for rs in result_segments:
            for w in (rs.words or []):
                words.append(
                    Word(word=w.word.strip(),
                         start=round(seg.start_time + w.start, 3),
                         end=round(seg.start_time + w.end, 3))
                )
        seg.words = words
        seg.text = " ".join(w.word for w in words)
    return segments


def transcribe_and_assign(
    model,
    wav_path: str | Path,
    segments: list[Segment],
) -> list[Segment]:
    """Whole-audio transcription, then assign each word to its diarized turn,
    then recover faint short turns the continuous pass left silent."""
    from .word_assign import assign_words_to_segments

    remove_segment_overlaps(segments)
    words = transcribe_full_audio(model, wav_path)
    assign_words_to_segments(words, segments)
    recover_orphan_turns(model, wav_path, segments, words)
    return segments


def transcribe_segments(
    model,
    wav_path: str | Path,
    segments: list[Segment],
    checkpoint_callback: Optional[Callable[[int, int], None]] = None,
    resume_from: int = 0,
) -> list[Segment]:
    """Transcribe each diarized segment with word-level timestamps.

    Args:
        model: faster-whisper WhisperModel instance.
        wav_path: Path to the normalized WAV file.
        segments: List of diarized segments (modified in-place).
        checkpoint_callback: Called every CHECKPOINT_EVERY_N_SEGMENTS with
            (current_index, total) to allow saving progress.
        resume_from: Segment index to resume from (for checkpoint recovery).

    Returns:
        The same segments list with text and words populated.
    """
    remove_segment_overlaps(segments)
    samples, sr = load_wav(wav_path)
    total = len(segments)

    for i in range(resume_from, total):
        seg = segments[i]
        audio_slice = slice_audio(samples, sr, seg.start_time, seg.end_time)

        if len(audio_slice) < sr * 0.1:  # skip segments shorter than 0.1s
            seg.text = ""
            seg.words = []
            if (i + 1) % 10 == 0:
                print(f"  [{i + 1}/{total}] (skipped short segment)", flush=True)
            continue

        result_segments, _ = model.transcribe(
            audio_slice,
            word_timestamps=True,
            language="en",
        )

        words = []
        text_parts = []
        for rs in result_segments:
            if rs.words:
                for w in rs.words:
                    words.append(
                        Word(
                            word=w.word.strip(),
                            start=round(seg.start_time + w.start, 3),
                            end=round(seg.start_time + w.end, 3),
                        )
                    )
            text_parts.append(rs.text.strip())

        seg.text = " ".join(text_parts).strip()
        seg.words = words

        # Per-segment progress (every 10 segments)
        if (i + 1) % 10 == 0:
            pct = ((i + 1) / total) * 100
            preview = seg.text[:60] + "..." if len(seg.text) > 60 else seg.text
            print(f"  [{i + 1}/{total}] ({pct:.0f}%) {preview}", flush=True)

        if (
            checkpoint_callback
            and (i + 1) % config.CHECKPOINT_EVERY_N_SEGMENTS == 0
        ):
            checkpoint_callback(i + 1, total)

    return segments


def save_raw_transcript(segments: list[Segment], output_path: str | Path) -> None:
    """Save transcript segments to JSON for checkpoint recovery."""
    data = [seg.to_dict() for seg in segments]
    with open(str(output_path), "w") as f:
        json.dump(data, f, indent=2)


def load_raw_transcript(input_path: str | Path) -> list[Segment]:
    """Load transcript segments from a checkpoint JSON file."""
    with open(str(input_path), "r") as f:
        data = json.load(f)
    return [Segment.from_dict(d) for d in data]
