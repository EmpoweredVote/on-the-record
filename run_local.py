#!/usr/bin/env python3
"""CouncilScribe — Local CLI runner for macOS / Linux.

Replaces the Colab notebook with a command-line interface.
All data is stored under ~/CouncilScribe (override with CS_DATA_DIR env var).

Usage:
    python run_local.py --input meeting.mp4 --city Bloomington --date 2026-02-10
    python run_local.py --input "https://catstv.net/..." --city Bloomington --date 2026-02-10
    python run_local.py --browse-catstv
    python run_local.py --resume 2026-02-10-regular
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure src/ is importable when running from the repo root
_REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_DIR))

# Load .env.local if present (HF_TOKEN, CS_DATA_DIR, etc.)
_env_file = _REPO_DIR / ".env.local"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

from src import config  # lightweight; must follow .env.local load (CS_DATA_DIR)

# ---------------------------------------------------------------------------
# Phase 109: pre-Stage-1 fail-fast guard (CSMEETING-02, D-07/D-08/D-09/D-13)
# ---------------------------------------------------------------------------

_BODY_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def ensure_body_roster_cached(body_slug: Optional[str]) -> None:
    """Phase 109 fail-fast guard: verify {body_slug}.json exists in the roster cache.

    Implements CSMEETING-02:
      - D-05: if body_slug is None/empty, return silently (legacy path).
      - D-07: runs BEFORE Stage 1, after argparse + metadata resolve.
      - D-08: on missing cache, print 2-line stderr error + sys.exit(2).
      - D-09: stale cache (>30 days) is NOT a fail-fast — file must merely exist.
      - D-10: behaves identically on resume after cache delete.
      - T-109-03: validates slug shape before composing filesystem paths.
    """
    if not body_slug:
        return  # D-05 legacy path

    # T-109-03: reject path-traversal / shell metacharacters BEFORE filesystem join.
    if not _BODY_SLUG_RE.match(body_slug):
        print(
            f'ERROR: Invalid body slug "{body_slug}" — must match '
            f'[a-z0-9][a-z0-9_-]{{0,63}} (1-64 chars)',
            file=sys.stderr,
        )
        sys.exit(2)

    cache_path = config.CONFIG_DIR / "rosters" / f"{body_slug}.json"
    if not cache_path.exists():
        # D-08: exact 2-line error. D-13: literal ~-path string, do NOT expand CONFIG_DIR.
        print(
            f'ERROR: Body "{body_slug}" has no cached roster at '
            f'~/CouncilScribe/config/rosters/{body_slug}.json',
            file=sys.stderr,
        )
        print(
            f'Run: python refresh_roster.py --body {body_slug}',
            file=sys.stderr,
        )
        sys.exit(2)
    # D-09: staleness is checked later inside load_roster() — not our concern here.


def _list_cached_rosters() -> list[tuple[str, str]]:
    """Return [(body_slug, label), ...] for each cached per-body roster.

    Scans CONFIG_DIR/rosters/*.json, sorted by filename. label is
    "{body_key} ({N} members) [{slug}]", falling back to the slug if the
    file can't be parsed.
    """
    rosters_dir = config.CONFIG_DIR / "rosters"
    out: list[tuple[str, str]] = []
    if not rosters_dir.exists():
        return out
    for path in sorted(rosters_dir.glob("*.json")):
        slug = path.stem
        label = slug
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            body_key = data.get("body_key") or slug
            count = len(data.get("politicians", []))
            label = f"{body_key} ({count} members) [{slug}]"
        except Exception:
            pass
        out.append((slug, label))
    return out


def _should_prompt_roster(
    *,
    cli_body,
    persisted_body,
    roster_choice,
    identified: bool,
    isatty: bool,
) -> bool:
    """Decide whether to show the interactive roster chooser.

    Prompt only on a fresh interactive run where the operator hasn't already
    chosen a roster: TTY attached, no --body, no persisted body_slug, no prior
    roster_choice, and Stage 4 (identification) not already complete.
    """
    return (
        isatty
        and not cli_body
        and not persisted_body
        and roster_choice is None
        and not identified
    )


def _prompt_roster_choice() -> tuple[Optional[str], str]:
    """Interactive roster chooser. Returns (body_slug_or_None, marker).

    marker is the value to persist in state.roster_choice:
      - the slug itself for a cached roster (body_slug is also returned)
      - "__legacy__" for the legacy council_roster.json
      - "__none__" for no roster (also the bare-Enter default)

    Caller is responsible for only invoking this when interactive
    (see _should_prompt_roster).
    """
    cached = _list_cached_rosters()
    legacy_path = config.CONFIG_DIR / "council_roster.json"
    has_legacy = legacy_path.exists()

    print("=" * 60)
    print("ROSTER SELECTION")
    print("=" * 60)
    print("  Which council roster should guide speaker identification?")
    print()

    # options[i] = ("cached"|"legacy"|"none", slug_or_None)
    options: list[tuple[str, Optional[str]]] = []
    n = 0
    for slug, label in cached:
        n += 1
        print(f"  {n}. {label}")
        options.append(("cached", slug))

    if has_legacy:
        legacy_label = "legacy council_roster.json"
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            name = f"{data.get('city', '')} {data.get('body', '')}".strip()
            members = len(data.get("members", []))
            legacy_label = f"{name or 'council_roster.json'} (legacy, {members} members)"
        except Exception:
            pass
        n += 1
        print(f"  {n}. {legacy_label}")
        options.append(("legacy", None))

    n += 1
    print(f"  {n}. No roster (skip name correction)")
    options.append(("none", None))
    print()

    while True:
        choice = input(f"  Select [1-{n}] (default {n} = no roster): ").strip()
        if choice == "":
            kind, value = "none", None
            break
        try:
            idx = int(choice)
        except ValueError:
            print("  Please enter a number.")
            continue
        if 1 <= idx <= len(options):
            kind, value = options[idx - 1]
            break
        print(f"  Out of range. Enter 1-{n}.")

    if kind == "cached":
        return value, value
    if kind == "legacy":
        return None, "__legacy__"
    return None, "__none__"


def _resolve_roster(effective_body_slug: Optional[str], roster_choice: Optional[str]) -> Optional["Roster"]:
    """Resolve the Roster (or None) for Stage 4 given the meeting's state.

    - body_slug set      → load that body's cached roster.
    - roster_choice legacy → bare load_roster() (legacy council_roster.json).
    - "__none__" / unchosen → no roster (no name correction). This is the
      non-interactive default since the chooser only runs interactively.
    """
    # Local import so tests can patch src.roster.load_roster.
    from src.roster import load_roster

    if effective_body_slug:
        return load_roster(body_slug=effective_body_slug)
    if roster_choice == "__legacy__":
        return load_roster()
    return None


def get_hf_token() -> str:
    """Resolve HuggingFace token from env, cached login, or prompt."""
    # 1. Environment variable
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        return token

    # 2. Cached token from `huggingface-cli login`
    try:
        from huggingface_hub import get_token
        token = get_token()
        if token:
            return token
    except Exception:
        pass

    # 3. Prompt user
    print("\nHuggingFace token required (for pyannote models).")
    print("Get one at: https://huggingface.co/settings/tokens")
    print("Accept the pyannote model agreements:")
    print("  https://huggingface.co/pyannote/speaker-diarization-3.1")
    print("  https://huggingface.co/pyannote/embedding")
    token = input("\nHF Token: ").strip()
    if not token:
        print("No token provided. Exiting.")
        sys.exit(1)
    return token


def browse_catstv(search_url: str | None = None, limit: int = 25) -> dict | None:
    """Interactive CATS TV meeting browser. Returns selected meeting dict or None."""
    from src.download import fetch_catstv_meetings, display_catstv_meetings

    print("Fetching CATS TV meeting archive...")
    meetings = fetch_catstv_meetings(search_url)
    print(f"Found {len(meetings)} meetings.\n")
    display_catstv_meetings(meetings, limit=limit)

    print()
    choice = input("Enter meeting number (or 'q' to cancel): ").strip()
    if choice.lower() == "q":
        return None

    try:
        idx = int(choice)
        if 0 <= idx < len(meetings):
            return meetings[idx]
        print(f"Invalid: must be 0-{len(meetings) - 1}")
        return None
    except ValueError:
        print("Invalid input.")
        return None


def human_review(mappings: dict) -> dict:
    """Interactive prompt for correcting speaker identifications."""
    from src.models import SpeakerMapping

    review_needed = [m for m in mappings.values() if m.needs_review]
    if not review_needed:
        return mappings

    print(f"\n  {len(review_needed)} speaker(s) flagged for review:")
    for m in review_needed:
        name = m.speaker_name or "(unidentified)"
        print(f"    {m.speaker_label} -> {name} (conf={m.confidence:.2f})")

    # Skip interactive prompt if stdin is not a terminal (e.g. background task)
    if not sys.stdin.isatty():
        print("  (non-interactive mode — skipping review)")
        return mappings

    print("\n  Enter corrections as: SPEAKER_00=Mayor Johnson, SPEAKER_03=Clerk Smith")
    print("  Or press Enter to skip:")
    corrections = input("  > ").strip()

    if corrections:
        for pair in corrections.split(","):
            pair = pair.strip()
            if "=" in pair:
                label, name = pair.split("=", 1)
                label, name = label.strip(), name.strip()
                if label in mappings:
                    mappings[label].speaker_name = name
                    mappings[label].confidence = 1.0
                    mappings[label].id_method = "human_review"
                    mappings[label].needs_review = False
                    print(f"    Updated: {label} -> {name}")
        print("  Corrections applied.")
    else:
        print("  No corrections. Continuing.")

    return mappings


def find_video_file(meeting_dir: Path, original_input: str) -> str | None:
    """Find the video file for a meeting, checking the meeting directory first.

    Returns path to video file, or None if not found.
    """
    # Check for downloaded source video in meeting directory (source.m4v, source.mp4, etc.)
    for ext in (".m4v", ".mp4", ".mkv", ".webm", ".avi", ".mov"):
        candidate = meeting_dir / f"source{ext}"
        if candidate.exists():
            return str(candidate)

    # Check if original input is a local video file that still exists
    if original_input and not original_input.startswith(("http://", "https://")):
        p = Path(original_input)
        if p.exists() and p.suffix.lower() in (".m4v", ".mp4", ".mkv", ".webm", ".avi", ".mov"):
            return str(p)

    return None


def play_video_clip(video_path: str, start_time: float, duration: float = 15.0, title: str = "") -> None:
    """Play a video clip using ffplay starting at the given timestamp.

    Args:
        video_path: Path to the video file.
        start_time: Start time in seconds.
        duration: Duration to play in seconds.
        title: Window title.
    """
    # Start a few seconds early to give visual context
    seek = max(0, start_time - 3.0)

    cmd = [
        "ffplay",
        "-ss", str(seek),
        "-t", str(duration),
        "-autoexit",
        "-loglevel", "quiet",
    ]
    if title:
        cmd += ["-window_title", title]
    cmd.append(video_path)

    print(f"    Playing video clip ({duration:.0f}s from {int(seek // 60):02d}:{int(seek % 60):02d})...")
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print("    ffplay not found — install ffmpeg to enable video playback")


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
          f"— looping; press V for the next clip, R to replay, or type a name / Enter to move on...")
    try:
        # Detach from the terminal's stdin so the looping player can never
        # contend with the review prompt's input().
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
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


def free_gpu_memory():
    """Release GPU memory (CUDA or MPS)."""
    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        torch.mps.empty_cache()


def run_pipeline(args: argparse.Namespace) -> None:
    """Execute the full 6-stage pipeline."""
    import numpy as np
    import torch

    from src import config
    from src.checkpoint import PipelineStage, PipelineState, ensure_drive_structure
    from src.models import Meeting, ProcessingMetadata

    print(f"Data directory: {config.DRIVE_ROOT}")

    # --- Resolve audio source ---
    audio_path = args.input
    if not audio_path:
        print("Error: --input is required (file path or URL). Use --browse-catstv to pick a meeting.")
        sys.exit(1)

    # --- HuggingFace token ---
    hf_token = get_hf_token()
    print(f"HuggingFace token: ...{hf_token[-4:]}")

    # --- Device info ---
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        vram = torch.cuda.get_device_properties(0).total_mem / 1e9
        print(f"GPU: {gpu_name} ({vram:.1f} GB VRAM)")
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        print("GPU: Apple Silicon (MPS)")
    else:
        print("GPU: None (CPU mode — slower, smaller model)")

    # --- Initialize meeting ---
    meeting_id = args.meeting_id or f"{args.date}-{args.meeting_type.lower().replace(' ', '-')}"
    meeting_dir = ensure_drive_structure(meeting_id)
    state = PipelineState(meeting_dir)

    # Apply --redo rewind now that meeting_dir is known (works with both
    # --resume and --input; the --resume branch may have already done this,
    # but rewind_to is idempotent so the double-call is harmless).
    if getattr(args, "redo", None):
        _redo_map = {
            "ingest":     PipelineStage.INGESTED,
            "diarize":    PipelineStage.DIARIZED,
            "transcribe": PipelineStage.TRANSCRIBED,
            "identify":   PipelineStage.IDENTIFIED,
            "summary":    PipelineStage.SUMMARIZED,
            "all":        PipelineStage.INGESTED,
        }
        state.rewind_to(_redo_map[args.redo])
        print(f"Re-running from stage: {args.redo}")

    # ── Phase 109: resolve effective body_slug (D-01..D-06, D-11) ──
    cli_body = getattr(args, "body", None)
    persisted_body = state.body_slug
    force_retag = getattr(args, "force_retag", False)

    if cli_body and persisted_body and cli_body != persisted_body and not force_retag:
        # D-02: hard error on mismatch
        print(
            f"ERROR: Meeting already tagged as \"{persisted_body}\". "
            f"Pass --body {persisted_body} to continue, or add --force-retag "
            f"to change the body (this will re-run Stages 4-7).",
            file=sys.stderr,
        )
        sys.exit(2)

    if cli_body and persisted_body and cli_body != persisted_body and force_retag:
        # D-03 + D-04 + D-11: overwrite, rewind, clear stale pre_ids
        print(f"  Force-retag: {persisted_body} → {cli_body}", file=sys.stderr)
        state.body_slug = cli_body
        state.rewind_for_retag()
    elif cli_body and not persisted_body:
        # D-01: first run persists
        if force_retag:
            print(
                f"  --force-retag on untagged meeting: behaving as first-run "
                f"persist of {cli_body}",
                file=sys.stderr,
            )
        state.body_slug = cli_body
        state.save()
    elif not cli_body and persisted_body and force_retag:
        # Should be unreachable: D-12 enforced at argparse (line 1835).
        raise AssertionError("--force-retag without --body bypassed D-12 guard")
    # else: D-05 (no flag, no persisted — legacy) or D-06 (no flag, persisted — silent read)

    # Roster chooser: on a fresh interactive run with no --body, ask which
    # roster should guide Stage 4 instead of silently using the legacy file.
    # Non-interactive runs (no TTY) fall through to no roster (handled in
    # _resolve_roster) unless --body was passed.
    if _should_prompt_roster(
        cli_body=cli_body,
        persisted_body=persisted_body,
        roster_choice=state.roster_choice,
        identified=state.is_complete(PipelineStage.IDENTIFIED),
        isatty=sys.stdin.isatty() and not getattr(args, "batch_mode", False),
    ):
        chosen_slug, marker = _prompt_roster_choice()
        state.roster_choice = marker
        if chosen_slug:
            state.body_slug = chosen_slug
        state.save()

    effective_body_slug = state.body_slug  # used by Plan 02 guard + Plan 03 Stage 4

    if effective_body_slug:
        # D-01 / D-06: single info line for operator visibility
        print(f"Body: {effective_body_slug}", file=sys.stderr)

    # Phase 109 D-07: fail fast if tagged meeting has no cached roster.
    # Must run before Stage 1 ingestion so operators don't burn GPU on a bad run.
    ensure_body_roster_cached(effective_body_slug)

    meeting = Meeting(
        meeting_id=meeting_id,
        city=args.city,
        date=args.date,
        meeting_type=args.meeting_type,
        audio_source=str(audio_path),
    )

    print(f"\nMeeting: {args.city} {args.meeting_type} ({args.date})")
    print(f"Meeting ID: {meeting_id}")
    print(f"Directory: {meeting_dir}")
    if state.completed_stage > PipelineStage.NOT_STARTED:
        print(f"Resuming from checkpoint: stage {state.completed_stage.name} ({state.completed_stage.value}/6)")
    print()

    num_speakers = args.num_speakers if args.num_speakers > 0 else None
    wav_path = meeting_dir / "audio.wav"

    # ======================================================================
    # Stage 1: Ingest
    # ======================================================================
    print("=" * 60)
    print("STAGE 1: Audio Ingestion")
    print("=" * 60)

    vtt_path = meeting_dir / "captions.vtt"

    if state.is_complete(PipelineStage.INGESTED):
        print("  Already complete. Skipping.")
        from src.audio_utils import get_audio_duration
        meeting.duration_seconds = get_audio_duration(wav_path)
    else:
        from src.ingest import normalize_audio

        t0 = time.time()
        metadata = normalize_audio(
            audio_path, wav_path,
            noise_reduce=args.noise_reduce,
            cookies_file=getattr(args, "cookies", None),
        )
        elapsed = time.time() - t0
        meeting.duration_seconds = metadata["duration_seconds"]
        state.mark_complete(PipelineStage.INGESTED)
        print(f"  Done in {elapsed:.1f}s")

        # Try to download VTT if input is a CATS TV URL
        if not vtt_path.exists() and isinstance(audio_path, str) and "catstv" in audio_path:
            from src.download import download_vtt, extract_catstv_vtt_url
            vtt_url = extract_catstv_vtt_url(audio_path)
            if vtt_url:
                result = download_vtt(vtt_url, vtt_path)
                if result:
                    print(f"  Downloaded VTT captions: {vtt_path.name} (will use instead of Whisper)")
                else:
                    print("  No VTT captions available from CATS TV")
            else:
                print("  No VTT captions available from CATS TV")

        # Try to download captions for YouTube / Facebook / other yt-dlp URLs
        elif not vtt_path.exists() and isinstance(audio_path, str):
            from src.download import download_captions_via_ytdlp, is_ytdlp_url
            if is_ytdlp_url(str(audio_path)):
                print("  Checking for closed captions...")
                result = download_captions_via_ytdlp(str(audio_path), vtt_path)
                if result:
                    print(f"  Downloaded captions: {vtt_path.name} (will use instead of Whisper)")
                else:
                    print("  No captions available — will transcribe with Whisper")

    duration_min = meeting.duration_seconds / 60
    print(f"  Audio duration: {duration_min:.1f} minutes\n")

    # ======================================================================
    # Stage 2: Diarization
    # ======================================================================
    print("=" * 60)
    print("STAGE 2: Speaker Diarization")
    print("=" * 60)

    from src.models import Segment

    diarization_path = meeting_dir / "diarization.json"
    embeddings_path = meeting_dir / "embeddings.json"

    if state.is_complete(PipelineStage.DIARIZED):
        print("  Already complete. Loading from checkpoint...")
        with open(diarization_path, "r") as f:
            segments = [Segment.from_dict(d) for d in json.load(f)]
        print(f"  Loaded {len(segments)} segments")
    else:
        _compute = getattr(args, "compute", "local")
        if _compute == "modal" and getattr(args, "diarizer", "oss") != "api":
            if not diarization_path.exists() or not embeddings_path.exists():
                from src.modal_compute import run_diarization as _modal_diarize
                t0 = time.time()
                _segs_data, _emb_data = _modal_diarize(
                    wav_path, meeting_id, use_merge=args.merge
                )
                elapsed = time.time() - t0
                segments = [Segment.from_dict(d) for d in _segs_data]
                with open(diarization_path, "w") as f:
                    json.dump([s.to_dict() for s in segments], f, indent=2)
                with open(embeddings_path, "w") as f:
                    json.dump(_emb_data, f)
                print(f"  Done in {elapsed:.1f}s (Modal)")
            else:
                print("  Diarization + embeddings found. Loading from checkpoint...")
                with open(diarization_path, "r") as f:
                    segments = [Segment.from_dict(d) for d in json.load(f)]
                print(f"  Loaded {len(segments)} segments")
        else:
            # Sub-step A: Diarization
            if diarization_path.exists():
                print("  Diarization file found. Loading instead of re-running...")
                with open(diarization_path, "r") as f:
                    segments = [Segment.from_dict(d) for d in json.load(f)]
                print(f"  Loaded {len(segments)} segments from previous run")
            else:
                diarizer = getattr(args, "diarizer", "oss")
                if diarizer == "api":
                    from src.diarize_api import run_diarization_via_api

                    api_key = os.environ.get("PYANNOTE_AI_KEY")
                    if not api_key:
                        raise RuntimeError(
                            "--diarizer api requires PYANNOTE_AI_KEY in env "
                            "(add it to .env.local)."
                        )
                    print("  Running speaker diarization (pyannote.ai Precision-2)...")
                    if num_speakers is not None:
                        print(
                            f"  ! --num-speakers={num_speakers} is ignored by the API "
                            "backend (Precision-2 does not accept a speaker-count hint)."
                        )
                    t0 = time.time()
                    segments = run_diarization_via_api(wav_path, api_key)
                    elapsed = time.time() - t0

                    with open(diarization_path, "w") as f:
                        json.dump([s.to_dict() for s in segments], f, indent=2)

                    print(f"  Diarization done in {elapsed:.1f}s")
                else:
                    from src.diarize import load_diarization_pipeline, run_diarization

                    print("  Running speaker diarization (pyannote OSS 3.1)...")
                    t0 = time.time()
                    pipeline = load_diarization_pipeline(hf_token)
                    segments = run_diarization(pipeline, wav_path, num_speakers=num_speakers)
                    elapsed = time.time() - t0

                    with open(diarization_path, "w") as f:
                        json.dump([s.to_dict() for s in segments], f, indent=2)

                    del pipeline
                    free_gpu_memory()
                    print(f"  Diarization done in {elapsed:.1f}s")

            # Sub-step B: Speaker embeddings
            if embeddings_path.exists():
                print("  Embeddings file found. Skipping extraction.")
            else:
                from src.diarize import extract_speaker_embeddings

                print("  Extracting speaker embeddings...")
                t0 = time.time()
                speaker_embeddings = extract_speaker_embeddings(wav_path, segments, hf_token)

                emb_data = {k: v.tolist() for k, v in speaker_embeddings.items()}
                with open(embeddings_path, "w") as f:
                    json.dump(emb_data, f)

                elapsed = time.time() - t0
                print(f"  Embeddings done in {elapsed:.1f}s")

        free_gpu_memory()
        state.mark_complete(PipelineStage.DIARIZED)

    unique_speakers = set(s.speaker_label for s in segments)
    print(f"  {len(segments)} segments, {len(unique_speakers)} speakers detected")
    if getattr(args, "diarizer", "oss") == "api":
        meeting.processing_metadata.diarization_model = "pyannote/ai-precision-2"
    else:
        meeting.processing_metadata.diarization_model = config.DIARIZATION_MODEL
    print()

    # ======================================================================
    # Stage 2.5: Auto-merge fragmented speakers (opt-in via --merge)
    # ======================================================================
    if args.merge and getattr(args, "diarizer", "oss") == "api":
        print(
            "  ! --merge requested with --diarizer api: skipping merge stage. "
            "Precision-2 already produces continuous turns; merge was designed "
            "for OSS pyannote 3.1 fragmentation."
        )
    elif args.merge and getattr(args, "compute", "local") == "modal":
        print("  (--merge was applied inside Modal — skipping local merge step)")
    elif args.merge:
        if embeddings_path.exists():
            with open(embeddings_path, "r") as f:
                emb_data = json.load(f)
            speaker_embeddings = {k: np.array(v) for k, v in emb_data.items()}

            from src.merge import merge_similar_speakers

            before_count = len(set(s.speaker_label for s in segments))
            segments, speaker_embeddings, merge_log = merge_similar_speakers(
                segments, speaker_embeddings,
            )
            after_count = len(set(s.speaker_label for s in segments))

            if merge_log:
                print("Speaker merge:")
                for entry in merge_log:
                    print(f"  {entry}")
                print(f"  {before_count} speakers -> {after_count} speakers")

                # Update embeddings.json on disk
                emb_data = {k: v.tolist() for k, v in speaker_embeddings.items()}
                with open(embeddings_path, "w") as f:
                    json.dump(emb_data, f)

                # Update diarization.json with merged labels
                with open(diarization_path, "w") as f:
                    json.dump([s.to_dict() for s in segments], f, indent=2)

                print()

    # ======================================================================
    # Pre-identification (optional, between diarization and transcription)
    # ======================================================================
    pre_identifications = {}
    pre_id_path = meeting_dir / "pre_identifications.json"

    # Load existing pre-identifications if present (from --identify-speakers)
    if pre_id_path.exists():
        with open(pre_id_path, "r") as f:
            pre_data = json.load(f)
        from src.models import SpeakerMapping as SM
        for label, data in pre_data.items():
            pre_identifications[label] = SM(
                speaker_label=label,
                speaker_name=data["speaker_name"],
                confidence=data.get("confidence", 1.0),
                id_method=data.get("id_method", "human_review"),
            )
        print(f"  Loaded {len(pre_identifications)} pre-identification(s) from previous session")

    if args.pre_identify and sys.stdin.isatty():
        print("=" * 60)
        print("PRE-IDENTIFICATION: Identify speakers by video clip")
        print("=" * 60)

        video_path = find_video_file(meeting_dir, meeting.audio_source)

        import numpy as np
        from src.enroll import load_profiles as _load_profiles
        from src import review as _review
        if embeddings_path.exists():
            with open(embeddings_path, "r") as f:
                _emb = json.load(f)
            _pre_embeddings = {k: np.array(v) for k, v in _emb.items()}
        else:
            _pre_embeddings = {}
        _pre_profile_db = _load_profiles()

        from src.models import SpeakerMapping as SM
        temp_mappings = dict(pre_identifications)  # start with any existing
        _views = _review.build_review_state(segments, temp_mappings, _pre_embeddings, _pre_profile_db, show_text=False)
        for v in _views:
            if v.label not in temp_mappings:
                temp_mappings[v.label] = SM(speaker_label=v.label)

        if video_path:
            print(f"  Video: {Path(video_path).name}")
        else:
            print("  Video: not found")
        _hint_count = sum(1 for v in _views if v.soft_hints)
        if _hint_count:
            print(f"  Voice hints: {_hint_count} speaker(s) have possible profile matches")
        print(f"  Speakers: {len(_views)}")
        print()

        changes = _interactive_speaker_review(
            segments, temp_mappings, _pre_embeddings, _pre_profile_db,
            video_path, str(meeting_dir / "audio.wav"),
            body_slug=effective_body_slug, show_text=False,
        )
        _persist_after_review(meeting_dir, segments, _pre_embeddings, changes)

        if changes:
            for label, mapping in temp_mappings.items():
                if isinstance(mapping, SM) and mapping.speaker_name:
                    pre_identifications[label] = mapping

            # Save pre-identifications
            pre_data = {}
            for label, m in pre_identifications.items():
                pre_data[label] = {
                    "speaker_name": m.speaker_name,
                    "confidence": m.confidence,
                    "id_method": m.id_method,
                }
            with open(pre_id_path, "w") as f:
                json.dump(pre_data, f, indent=2)

            print(f"\n  {len(changes)} identification(s) saved. These will be used in Stage 4.")

            # Offer enrollment
            _enroll_after_review(
                changes, temp_mappings, meeting_dir,
                meeting_id, segments,
            )
        print()

    # ======================================================================
    # Stage 3: Transcription (Whisper or VTT alignment)
    # ======================================================================
    print("=" * 60)
    use_vtt = args.use_vtt or (vtt_path.exists() and not state.is_complete(PipelineStage.TRANSCRIBED))
    if use_vtt and vtt_path.exists():
        print("STAGE 3: VTT Alignment (skipping Whisper)")
    else:
        print("STAGE 3: Transcription")
        use_vtt = False  # force off if no VTT file
    print("=" * 60)

    from src.transcribe import (
        load_raw_transcript,
        load_whisper_model,
        remove_segment_overlaps,
        save_raw_transcript,
        transcribe_segments,
    )

    transcript_path = meeting_dir / "transcript_raw.json"

    if state.is_complete(PipelineStage.TRANSCRIBED):
        print("  Already complete. Loading from checkpoint...")
        segments = load_raw_transcript(transcript_path)
        print(f"  Loaded {len(segments)} transcribed segments")
    else:
        remove_segment_overlaps(segments)

    if not state.is_complete(PipelineStage.TRANSCRIBED) and use_vtt:
        from src.vtt_align import align_vtt_to_segments

        t0 = time.time()
        print(f"  Aligning VTT captions from {vtt_path.name}...")
        segments = align_vtt_to_segments(vtt_path, segments)
        elapsed = time.time() - t0

        meeting.processing_metadata.transcription_model = "vtt_alignment"
        save_raw_transcript(segments, transcript_path)
        state.mark_complete(PipelineStage.TRANSCRIBED)
        print(f"  Done in {elapsed:.1f}s")
    elif (
        not state.is_complete(PipelineStage.TRANSCRIBED)
        and getattr(args, "compute", "local") == "modal"
    ):
        from src.modal_compute import run_transcription as _modal_transcribe, upload_audio as _modal_upload

        # When --diarizer api is used, run_diarization() on Modal was never called,
        # so the audio was never uploaded to the volume. Always ensure it's present.
        _modal_upload(wav_path, meeting_id)

        print("  Dispatching Whisper transcription to Modal GPU (large-v3)...")
        t0 = time.time()
        _segs_data = [s.to_dict() for s in segments]
        _updated_data = _modal_transcribe(meeting_id, _segs_data)
        elapsed = time.time() - t0
        segments = [Segment.from_dict(d) for d in _updated_data]

        meeting.processing_metadata.transcription_model = config.WHISPER_MODEL_GPU
        meeting.processing_metadata.gpu_used = True
        save_raw_transcript(segments, transcript_path)
        state.mark_complete(PipelineStage.TRANSCRIBED)
        print(f"  Done in {elapsed:.1f}s (Modal)")
    elif not state.is_complete(PipelineStage.TRANSCRIBED):
        resume_from = state.transcription_progress
        if resume_from > 0:
            print(f"  Resuming from segment {resume_from}/{len(segments)}")
            if transcript_path.exists():
                segments = load_raw_transcript(transcript_path)

        t0 = time.time()
        whisper_model = load_whisper_model()

        model_name = config.WHISPER_MODEL_GPU if torch.cuda.is_available() else config.WHISPER_MODEL_CPU
        meeting.processing_metadata.transcription_model = model_name
        meeting.processing_metadata.gpu_used = torch.cuda.is_available()
        print(f"  Using model: {model_name}")

        def checkpoint_fn(current, total):
            save_raw_transcript(segments, transcript_path)
            state.update_transcription_progress(current, total)
            pct = (current / total) * 100
            print(f"  Checkpoint: {current}/{total} segments ({pct:.0f}%)")

        segments = transcribe_segments(
            whisper_model, wav_path, segments,
            checkpoint_callback=checkpoint_fn,
            resume_from=resume_from,
        )
        elapsed = time.time() - t0

        save_raw_transcript(segments, transcript_path)
        del whisper_model
        free_gpu_memory()
        state.mark_complete(PipelineStage.TRANSCRIBED)
        print(f"  Done in {elapsed:.1f}s")

    # Show sample
    print("\n  Sample transcript:")
    for seg in segments[:5]:
        if seg.text:
            text = seg.text[:80] + "..." if len(seg.text) > 80 else seg.text
            print(f"    [{seg.speaker_label}] {text}")
    print()

    # ======================================================================
    # Stage 4: Speaker Identification
    # ======================================================================
    print("=" * 60)
    print("STAGE 4: Speaker Identification")
    print("=" * 60)

    from src.enroll import get_stored_centroids, load_profiles
    from src.identify import (
        apply_mappings_to_segments,
        flag_for_review,
        identify_speakers,
    )
    from src.roster import roster_names_for_prompt

    named_transcript_path = meeting_dir / "transcript_named.json"
    llm_partial_path = meeting_dir / "llm_partial_results.json"

    # Phase 109 CSMEETING-03 + roster chooser: resolve the Stage 4 roster from the
    # meeting's tagging/choice state. effective_body_slug comes from the resolve
    # block above; if it's set, Plan 02's guard has already verified the cache file
    # exists. The actual load_roster() calls live inside _resolve_roster(); this is
    # the only meeting-context roster resolution. The 3 offline utility sites
    # (_fix_transcripts, --show-roster, --fix-profiles) still call bare load_roster()
    # because they have no meeting context. See 109-RESEARCH.md §1.
    roster = _resolve_roster(effective_body_slug, state.roster_choice)
    if roster:
        # Roster dataclass may not have .city/.body when loaded from a body-keyed cache;
        # print whichever label is available without crashing the legacy path.
        label = f"{getattr(roster, 'city', '') or ''} {getattr(roster, 'body', '') or ''}".strip()
        if not label and effective_body_slug:
            label = effective_body_slug
        print(f"  Loaded council roster: {len(roster.members)} members ({label})")
    else:
        print("  No roster loaded — speaker names won't be corrected against a council roster.")
    roster_hint = roster_names_for_prompt(roster) if roster else ""

    if state.is_complete(PipelineStage.IDENTIFIED):
        print("  Already complete. Loading from checkpoint...")
        with open(named_transcript_path, "r") as f:
            meeting_data = json.load(f)
        meeting = Meeting.from_dict(meeting_data)
        segments = meeting.segments
    else:
        # Load embeddings
        if embeddings_path.exists():
            with open(embeddings_path, "r") as f:
                emb_data = json.load(f)
            speaker_embeddings = {k: np.array(v) for k, v in emb_data.items()}
        else:
            speaker_embeddings = {}

        # Layer 1: Voice profiles
        profile_db = load_profiles()
        stored_centroids = get_stored_centroids(profile_db)
        if stored_centroids:
            print(f"  Loaded {len(stored_centroids)} voice profiles")

        # Layer 3: LLM (optional)
        llm_fn = None
        llm = None
        if not args.skip_llm:
            print("  Loading LLM for speaker identification...")
            from src.llm_utils import llm_identify_speakers, load_llm, unload_llm

            llm = load_llm()
            llm_fn = lambda segs, maps: llm_identify_speakers(
                llm, segs, maps, partial_results_path=llm_partial_path,
                roster_hint=roster_hint,
            )

        t0 = time.time()
        mappings = identify_speakers(
            segments, speaker_embeddings,
            stored_profiles=stored_centroids if stored_centroids else None,
            llm_identify_fn=llm_fn,
            roster=roster,
            profile_db=profile_db,
        )
        elapsed = time.time() - t0

        if llm is not None:
            unload_llm(llm)
            del llm
        if llm_partial_path.exists():
            llm_partial_path.unlink()

        # Apply pre-identifications (override automated results)
        if pre_identifications:
            overrides = 0
            for label, pre_map in pre_identifications.items():
                if label in mappings:
                    if pre_map.confidence > mappings[label].confidence:
                        mappings[label] = pre_map
                        overrides += 1
                else:
                    mappings[label] = pre_map
                    overrides += 1
            if overrides:
                print(f"  Applied {overrides} pre-identification(s) as ground truth")

        print(f"  Done in {elapsed:.1f}s")
        for label, m in mappings.items():
            status = "REVIEW" if m.needs_review else "OK"
            name = m.speaker_name or "(unidentified)"
            print(f"    {label} -> {name} (conf={m.confidence:.2f}, method={m.id_method}, {status})")

        # Human review — rich interactive review on a terminal (clips, hints,
        # merge); fall back to the text-only quick review otherwise or when
        # --no-review is set.
        if sys.stdin.isatty() and not getattr(args, "no_review", False):
            review_video = find_video_file(meeting_dir, meeting.audio_source)
            review_changes = _interactive_speaker_review(
                segments, mappings, speaker_embeddings, profile_db,
                review_video, str(wav_path),
                roster=roster, body_slug=effective_body_slug, show_text=True,
            )
            _persist_after_review(meeting_dir, segments, speaker_embeddings, review_changes)
        else:
            mappings = human_review(mappings)

        # Apply to segments
        segments = apply_mappings_to_segments(segments, mappings)
        meeting.segments = segments
        meeting.speakers = mappings

        with open(named_transcript_path, "w") as f:
            json.dump(meeting.to_dict(), f, indent=2)
        state.mark_complete(PipelineStage.IDENTIFIED)

    print()

    # ======================================================================
    # Stage 5: Summary Generation
    # ======================================================================
    print("=" * 60)
    print("STAGE 5: Summary Generation")
    print("=" * 60)

    summary_path = meeting_dir / "summary.json"

    if state.is_complete(PipelineStage.SUMMARIZED):
        print("  Already complete. Loading from checkpoint...")
        if summary_path.exists():
            from src.models import MeetingSummary
            with open(summary_path, "r") as f:
                meeting.summary = MeetingSummary.from_dict(json.load(f))
            print(f"  Loaded summary ({len(meeting.summary.sections)} sections)")
    elif args.skip_summary:
        print("  Skipped (--skip-summary).")
        state.mark_complete(PipelineStage.SUMMARIZED)
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            print("  No ANTHROPIC_API_KEY found. Skipping summary generation.")
            print("  Set the environment variable or use --skip-summary to silence this.")
            state.mark_complete(PipelineStage.SUMMARIZED)
        else:
            from src.summarize import generate_summary

            def summary_progress(step, current=0, total=0):
                if total > 0:
                    print(f"    [{current}/{total}] {step}")
                else:
                    print(f"    {step}...")

            t0 = time.time()
            print("  Generating meeting summary via Anthropic API...")
            try:
                meeting.summary = generate_summary(meeting, progress_callback=summary_progress)
            except Exception as e:
                # The summary stage is the only stage that needs the Anthropic API.
                # If it fails (e.g. out of credits, bad key, network), don't crash the
                # whole pipeline — report it clearly and continue. The stage is left
                # incomplete so it will be retried automatically on the next run.
                meeting.summary = None
                detail = str(e)
                if "credit balance is too low" in detail:
                    reason = "Anthropic API credit balance is too low."
                    hint = "Add credits at https://console.anthropic.com (Plans & Billing), then re-run to generate the summary."
                elif "authentication" in detail.lower() or "invalid x-api-key" in detail.lower():
                    reason = "Anthropic API key was rejected."
                    hint = "Check ANTHROPIC_API_KEY, then re-run to generate the summary."
                else:
                    reason = f"Summary generation failed: {detail}"
                    hint = "Re-run once the issue is resolved to generate the summary."
                print(f"\n  ⚠ Skipping summary — {reason}")
                print(f"    {hint}")
                print("    (All other stages are complete; only the summary needs the API.)")
            else:
                elapsed = time.time() - t0

                # Save summary checkpoint
                with open(summary_path, "w") as f:
                    json.dump(meeting.summary.to_dict(), f, indent=2)

                # Re-save named transcript with summary included
                with open(named_transcript_path, "w") as f:
                    json.dump(meeting.to_dict(), f, indent=2)

                state.mark_complete(PipelineStage.SUMMARIZED)

                print(f"\n  Summary generated in {elapsed:.1f}s")
                print(f"    Sections: {len(meeting.summary.sections)}")
                print(f"    Key decisions: {len(meeting.summary.key_decisions)}")
                if meeting.summary.executive_summary:
                    # Show first 200 chars of executive summary
                    preview = meeting.summary.executive_summary[:200]
                    if len(meeting.summary.executive_summary) > 200:
                        preview += "..."
                    print(f"    Preview: {preview}")

    print()

    # ======================================================================
    # Stage 6: Voice Enrollment
    # ======================================================================
    print("=" * 60)
    print("STAGE 6: Voice Enrollment")
    print("=" * 60)

    from src.enroll import enroll_confirmed, enroll_speakers, get_borderline_speakers, save_profiles

    if state.is_complete(PipelineStage.ENROLLED):
        print("  Already complete. Skipping.")
    else:
        if embeddings_path.exists():
            with open(embeddings_path, "r") as f:
                emb_data = json.load(f)
            speaker_embeddings = {k: np.array(v) for k, v in emb_data.items()}
        else:
            speaker_embeddings = {}

        profile_db = load_profiles()
        before_count = len(profile_db.profiles)

        # Auto-enroll high-confidence speakers (>= 0.85)
        profile_db = enroll_speakers(
            profile_db, speaker_embeddings, meeting.speakers,
            meeting_id=meeting_id, segments=segments, roster=roster,
        )

        auto_count = len(profile_db.profiles) - before_count
        if auto_count > 0:
            print(f"  Auto-enrolled {auto_count} high-confidence speaker(s)")

        # Interactive enrollment for borderline speakers (0.70-0.85)
        if args.confirm_enroll and sys.stdin.isatty():
            borderline = get_borderline_speakers(
                meeting.speakers, speaker_embeddings, segments,
            )
            if borderline:
                video_path = find_video_file(meeting_dir, meeting.audio_source)
                if video_path:
                    print(f"\n  Video found: {Path(video_path).name}")
                else:
                    print("\n  No video file found (audio-only fallback with afplay)")

                print(f"  {len(borderline)} speaker(s) eligible for enrollment confirmation:")
                confirmed = []
                for info in borderline:
                    m = info["mapping"]
                    mins = info["total_speech_seconds"] / 60
                    print(f"\n  {m.speaker_label} identified as \"{m.speaker_name}\"")
                    print(f"    Method: {m.id_method} (confidence: {m.confidence:.2f})")
                    print(f"    Segments: {info['seg_count']} ({mins:.1f}m total speech)")
                    if info["sample_segment"]:
                        sample = info["sample_segment"]
                        text = sample.text[:100] + "..." if len(sample.text) > 100 else sample.text
                        ts_min = int(sample.start_time // 60)
                        ts_sec = int(sample.start_time % 60)
                        print(f"    Sample [{ts_min:02d}:{ts_sec:02d}]: \"{text}\"")

                    while True:
                        if video_path:
                            choice = input("\n    [V]iew clip / [E]nroll / [S]kip? ").strip().lower()
                        else:
                            choice = input("\n    [E]nroll / [S]kip? ").strip().lower()

                        if choice in ("v", "view") and video_path and info["sample_segment"]:
                            play_video_clip(
                                video_path,
                                start_time=info["sample_segment"].start_time,
                                duration=20.0,
                                title=f"{m.speaker_label} → {m.speaker_name or 'Unknown'}",
                            )
                            continue  # re-prompt after viewing
                        elif choice in ("e", "enroll", "y", "yes"):
                            confirmed.append(info["label"])
                            print(f"    -> Will enroll {m.speaker_name}")
                            break
                        else:
                            print(f"    -> Skipped")
                            break

                if confirmed:
                    profile_db = enroll_confirmed(
                        profile_db, speaker_embeddings, confirmed,
                        meeting.speakers, meeting_id=meeting_id, segments=segments,
                        roster=roster,
                    )
                    print(f"\n  Enrolled {len(confirmed)} additional speaker(s) via confirmation")
            else:
                print("  No borderline speakers to confirm.")

        save_profiles(profile_db)

        after_count = len(profile_db.profiles)
        total_new = after_count - before_count
        state.mark_complete(PipelineStage.ENROLLED)

        print(f"\n  Total new profiles: {total_new}. Total stored: {after_count}")
        for pid, p in profile_db.profiles.items():
            print(f"    {pid}: {p.display_name} ({len(p.meetings_seen)} meetings, {p.total_segments_confirmed} segments)")

    print()

    # ======================================================================
    # Post-identification segment merging
    # ======================================================================
    from src.identify import merge_adjacent_segments

    before_count = len(meeting.segments)
    meeting.segments = merge_adjacent_segments(meeting.segments)
    after_count = len(meeting.segments)
    if before_count != after_count:
        print(f"  Segment merge: {before_count} -> {after_count} segments")

    # ======================================================================
    # Stage 7: Export
    # ======================================================================
    print("=" * 60)
    print("STAGE 7: Export")
    print("=" * 60)

    from src.export import export_all

    if state.is_complete(PipelineStage.EXPORTED):
        print("  Already complete.")
    else:
        export_dir = meeting_dir / "exports"
        results = export_all(meeting, export_dir)
        state.mark_complete(PipelineStage.EXPORTED)

        print("  Export complete:")
        for fmt, path in results.items():
            print(f"    {fmt}: {path}")

    if getattr(args, "publish", False):
        try:
            from src.publish import publish_meeting

            result = publish_meeting(meeting, state.body_slug)
            print(f"  Published to Supabase: {result.segments} segments, "
                  f"{result.speakers} speakers")
        except Exception as e:
            print(f"  WARNING: Supabase publish failed: {e}")
            print(f"  Retry later with: python run_local.py --publish-meeting {meeting.meeting_id}")

    print()
    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    export_dir = meeting_dir / "exports"
    print(f"\nOutputs:")
    print(f"  Transcript: {export_dir / 'transcript.md'}")
    print(f"  JSON:       {export_dir / 'transcript.json'}")
    print(f"  Subtitles:  {export_dir / 'subtitles.srt'}")
    if meeting.summary:
        print(f"  Summary:    {export_dir / 'summary.md'}")


def _parse_batch_inputs(batch_path: str) -> list[dict]:
    """Parse batch input: a text file or a directory of video files.

    Returns list of dicts with keys: input, date, city, meeting_type.
    """
    p = Path(batch_path)

    if p.is_dir():
        # Directory of video files
        video_exts = {".m4v", ".mp4", ".mkv", ".webm", ".avi", ".mov"}
        entries = []
        for f in sorted(p.iterdir()):
            if f.suffix.lower() in video_exts:
                # Try to extract date from filename (YYYY-MM-DD pattern)
                import re
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", f.stem)
                date = date_match.group(1) if date_match else ""
                entries.append({
                    "input": str(f),
                    "date": date,
                    "city": "Bloomington",
                    "meeting_type": "Regular Session",
                })
        return entries

    if p.is_file():
        # Text file with one entry per line
        # Format: PATH_OR_URL [DATE] [CITY] [TYPE]
        # or just: PATH_OR_URL
        entries = []
        with open(p, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(maxsplit=3)
                entry = {
                    "input": parts[0],
                    "date": parts[1] if len(parts) > 1 else "",
                    "city": parts[2] if len(parts) > 2 else "Bloomington",
                    "meeting_type": parts[3] if len(parts) > 3 else "Regular Session",
                }
                entries.append(entry)
        return entries

    print(f"Error: batch path '{batch_path}' is not a file or directory.")
    sys.exit(1)


def _run_batch(args: argparse.Namespace) -> None:
    """Run batch processing on multiple meetings.

    Runs Stages 1-3 (ingest, diarize, transcribe) + automated Stage 4
    (no interactive review) for each meeting. Skips pre-identify and
    human review in batch mode.
    """
    entries = _parse_batch_inputs(args.batch)
    if not entries:
        print("No inputs found for batch processing.")
        return

    print(f"Batch processing: {len(entries)} meeting(s)")
    print()

    results = []
    for i, entry in enumerate(entries):
        print(f"{'=' * 60}")
        print(f"BATCH [{i+1}/{len(entries)}]: {entry['input']}")
        print(f"{'=' * 60}")

        # Check if already processed (for --batch-resume)
        if args.batch_resume and entry["date"]:
            from src import config
            from src.checkpoint import PipelineStage, PipelineState
            mid = f"{entry['date']}-{entry['meeting_type'].lower().replace(' ', '-')}"
            mdir = config.MEETINGS_DIR / mid
            state_file = mdir / "pipeline_state.json"
            if state_file.exists():
                state = PipelineState(mdir)
                if state.is_complete(PipelineStage.IDENTIFIED):
                    print(f"  Already complete (stage {state.completed_stage.name}). Skipping.")
                    results.append({"input": entry["input"], "status": "skipped (complete)", "meeting_id": mid})
                    print()
                    continue

        # Build args for run_pipeline
        batch_args = argparse.Namespace(
            input=entry["input"],
            date=entry["date"],
            city=entry["city"],
            meeting_type=entry["meeting_type"],
            meeting_id="",
            num_speakers=0,
            noise_reduce=False,
            skip_llm=args.skip_llm if hasattr(args, "skip_llm") else False,
            skip_summary=True,  # skip summary in batch mode
            confirm_enroll=False,
            merge=args.merge if hasattr(args, "merge") else False,
            pre_identify=False,  # skip interactive pre-identify
            use_vtt=args.use_vtt if hasattr(args, "use_vtt") else False,
            diarizer=getattr(args, "diarizer", "oss"),
            compute=getattr(args, "compute", "local"),
            body=getattr(args, "body", None),
            force_retag=getattr(args, "force_retag", False),
            batch_mode=True,  # suppress the interactive roster chooser (D3: batch uses no roster unless --body)
        )

        # Auto-generate date if missing
        if not batch_args.date:
            from datetime import date
            batch_args.date = date.today().isoformat()
            print(f"  No date provided, using today: {batch_args.date}")

        mid = f"{batch_args.date}-{batch_args.meeting_type.lower().replace(' ', '-')}"

        try:
            run_pipeline(batch_args)
            results.append({"input": entry["input"], "status": "completed", "meeting_id": mid})
        except Exception as e:
            print(f"\n  ERROR: {e}")
            results.append({"input": entry["input"], "status": f"failed: {e}", "meeting_id": mid})

        print()

    # Print summary
    print("=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    completed = [r for r in results if r["status"] == "completed"]
    skipped = [r for r in results if r["status"].startswith("skipped")]
    failed = [r for r in results if r["status"].startswith("failed")]

    print(f"  Completed: {len(completed)}")
    print(f"  Skipped:   {len(skipped)}")
    print(f"  Failed:    {len(failed)}")

    if completed:
        print("\nCompleted:")
        for r in completed:
            print(f"  {r['meeting_id']}")

    if failed:
        print("\nFailed (need review):")
        for r in failed:
            print(f"  {r['meeting_id']}: {r['status']}")

    if completed or skipped:
        print(f"\nUse --review-meeting MEETING_ID to review speaker identifications.")


def _repair_transcript_standalone(meeting_id: str) -> None:
    """Repair transcript artifacts for one already-processed meeting."""
    from src import config
    from src.repair import RepairError, repair_transcript

    meeting_dir = config.MEETINGS_DIR / meeting_id
    try:
        result = repair_transcript(meeting_dir)
    except RepairError as exc:
        print(f"Transcript repair failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Transcript repair complete:")
    print(f"  Meeting: {result.meeting_id}")
    print(f"  Segments: {result.segment_count}")
    print(f"  Backup: {result.backup_dir}")
    print("  Exports:")
    for export_name, export_path in result.exports.items():
        print(f"    {export_name}: {export_path}")


def _option_supplied(argv: list[str], *options: str) -> bool:
    """Return whether argv explicitly contains any option or option=value."""
    return any(
        argument == option or argument.startswith(f"{option}=")
        for argument in argv
        for option in options
    )


def _publish_meeting_standalone(meeting_id: str) -> None:
    """Publish an already-processed meeting to Supabase (backfill workhorse)."""
    from src import config
    from src.checkpoint import PipelineState
    from src.models import Meeting
    from src.publish import publish_meeting

    meeting_dir = config.MEETINGS_DIR / meeting_id
    named_path = meeting_dir / "transcript_named.json"
    if not named_path.exists():
        print(f"No transcript_named.json found for meeting ID: {meeting_id}")
        print(f"  Expected at: {named_path}")
        sys.exit(1)

    with open(named_path, "r", encoding="utf-8") as f:
        meeting = Meeting.from_dict(json.load(f))

    body_slug = PipelineState(meeting_dir).body_slug

    print(f"Publishing {meeting_id} to Supabase...")
    result = publish_meeting(meeting, body_slug)
    print(f"  Meeting:  {result.meeting_id}")
    print(f"  Segments: {result.segments}")
    print(f"  Speakers: {result.speakers}")


def _fix_transcripts() -> None:
    """Re-correct speaker names in all existing transcripts using the roster.

    Walks through every meeting directory, loads transcript_named.json,
    applies roster corrections to speaker mappings and segments, saves
    the corrected transcript, and re-exports markdown/json/srt.
    """
    from src import config
    from src.export import export_all
    from src.models import Meeting
    from src.roster import add_alias, correct_speaker_name, load_roster

    roster = load_roster()
    if not roster:
        print("No council roster found. Cannot fix transcripts.")
        print(f"  Create one at: {config.CONFIG_DIR / 'council_roster.json'}")
        sys.exit(1)

    meetings_dir = config.MEETINGS_DIR
    if not meetings_dir.exists():
        print("No meetings directory found.")
        return

    meeting_dirs = sorted(
        d for d in meetings_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

    if not meeting_dirs:
        print("No meetings found.")
        return

    print(f"Fixing transcripts using roster ({len(roster.members)} members)...")
    print()

    total_corrections = 0
    total_aliases = 0

    for mdir in meeting_dirs:
        named_path = mdir / "transcript_named.json"
        if not named_path.exists():
            continue

        with open(named_path, "r") as f:
            meeting = Meeting.from_dict(json.load(f))

        corrections = []

        # Fix speaker mappings
        for label, mapping in meeting.speakers.items():
            if mapping.speaker_name:
                corrected = correct_speaker_name(mapping.speaker_name, roster)
                if corrected != mapping.speaker_name:
                    corrections.append({
                        "label": label,
                        "original": mapping.speaker_name,
                        "corrected": corrected,
                    })
                    mapping.speaker_name = corrected

        # Fix segment speaker names
        for seg in meeting.segments:
            if seg.speaker_name:
                corrected = correct_speaker_name(seg.speaker_name, roster)
                if corrected != seg.speaker_name:
                    seg.speaker_name = corrected

        if corrections:
            # Save corrected transcript
            with open(named_path, "w") as f:
                json.dump(meeting.to_dict(), f, indent=2)

            # Re-export
            export_dir = mdir / "exports"
            export_all(meeting, export_dir)

            print(f"  {mdir.name}: {len(corrections)} correction(s)")
            for c in corrections:
                print(f"    {c['label']}: {c['original']} -> {c['corrected']}")
                # Auto-learn: add original name as alias for the corrected name
                if add_alias(None, c["corrected"], c["original"]):
                    print(f"      -> Added '{c['original']}' as alias for '{c['corrected']}'")
                    total_aliases += 1

            total_corrections += len(corrections)
        else:
            print(f"  {mdir.name}: no corrections needed")

    print(f"\nDone. {total_corrections} total correction(s) across {len(meeting_dirs)} meeting(s).")
    if total_aliases:
        print(f"  {total_aliases} new alias(es) auto-added to roster.")


def _format_ts(seconds: float) -> str:
    """Format seconds as MM:SS."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def _persist_after_review(meeting_dir: Path, segments, embeddings, changes) -> None:
    """If the review performed any merges, rewrite diarization.json + embeddings.json."""
    if not any("merged_into" in c for c in changes):
        return
    diar_path = meeting_dir / "diarization.json"
    emb_path = meeting_dir / "embeddings.json"
    with open(diar_path, "w") as f:
        json.dump([s.to_dict() for s in segments], f, indent=2)
    emb_out = {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in embeddings.items()}
    with open(emb_path, "w") as f:
        json.dump(emb_out, f)


def _meeting_body_slug(meeting_dir: Path) -> str | None:
    """Read the persisted body_slug from a meeting's pipeline_state.json, if any."""
    state_file = meeting_dir / "pipeline_state.json"
    if not state_file.exists():
        return None
    try:
        with open(state_file, "r") as f:
            return json.load(f).get("body_slug")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Metadata defaults / interactive prompt helpers
# ---------------------------------------------------------------------------

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


def _prompt_link_politician(mappings: dict, label: str, query: str) -> None:
    """Offer to link a just-named speaker to an essentials politician/candidate.

    No-op when the speaker is already linked (e.g. roster auto-match) or when
    not attached to a TTY. Best-effort: any search failure degrades to a manual
    slug paste or a skip — never blocks or crashes review.
    """
    from src import review
    from src.essentials_client import EssentialsClientError, search_politicians

    mapping = mappings.get(label)
    if mapping is None or mapping.politician_slug:
        return
    if not sys.stdin.isatty():
        return

    def _do_search(q: str):
        try:
            return search_politicians(q)
        except EssentialsClientError as e:
            print(f"  (politician search unavailable: {e})")
            return None

    matches = _do_search(query)
    while True:
        if matches:
            print("  Link to a politician/candidate? (Enter = leave unlinked)")
            for i, mt in enumerate(matches):
                print(review.format_match_line(mt, i))
            prompt = "  [number] pick · [m] search again · [p] paste slug · [Enter/s] skip · [n] none/unlink: "
        else:
            prompt = "  Link politician? [m] search · [p] paste slug · [Enter/s] skip: "

        choice = input(prompt).strip()
        action, idx = review.parse_link_selection(choice, len(matches or []))

        if action == "skip":
            return
        if action == "none":
            review.link_speaker(mappings, label, None, None)
            print("  Left unlinked.")
            return
        if action == "search":
            q = input("    Search name: ").strip()
            if q:
                matches = _do_search(q)
            continue
        if action == "pick":
            mt = matches[idx]
            review.link_speaker(mappings, label, mt["politician_slug"], mt["politician_id"])
            print(f"  Linked → {mt['full_name']} ({mt['politician_slug']})")
            return
        # 'invalid' — allow a manual slug paste (handy when there are no matches).
        if choice.lower() == "p":
            slug = input("    politician_slug: ").strip()
            if slug:
                review.link_speaker(mappings, label, slug, None)
                print(f"  Linked → {slug} (id unknown)")
            return
        print("  Not understood.")


def _interactive_speaker_review(
    segments,
    mappings: dict,
    embeddings: dict,
    profile_db,
    video_path: str | None,
    audio_path: str | None,
    *,
    roster=None,
    body_slug: str | None = None,
    show_text: bool = True,
) -> list[dict]:
    """Interactive review loop built on the pure src/review.py core.

    Per speaker: play clips ([V] cycles through up to 8 candidates, [V3] jumps
    to clip 3, [R] replays the current one), accept the top voice hint ([Y]),
    merge this speaker into another ([M]), skip ([Enter]), quit ([Q]), or type
    a name.
    Mutates segments/mappings/embeddings in place via review.py; the CALLER
    persists (diarization.json / embeddings.json / transcript).

    Returns change dicts: {"label","old_name","new_name"} for renames and
    {"label","merged_into"} for merges.
    """
    from src import review

    if not sys.stdin.isatty():
        print("(non-interactive mode — cannot review)")
        return []

    changes: list[dict] = []
    views = review.build_review_state(segments, mappings, embeddings, profile_db, show_text=show_text)

    i = 0
    quit_requested = False
    while i < len(views):
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
        clip_idx = 0        # index of the clip [V] plays next
        last_clip = None    # index of the most recently played clip
        current_player = None
        has_clip = bool((video_path or audio_path) and view.clip_candidates)
        n_clips = len(view.clip_candidates)

        def _play_clip(n: int) -> None:
            nonlocal current_player, last_clip, clip_idx
            _stop_player(current_player)
            start = view.clip_candidates[n]
            print(f"  Clip {n + 1}/{n_clips} at [{_format_ts(start)}]")
            current_player = play_speaker_clip(
                video_path, audio_path, start,
                title=f"{label} → {name} (clip {n + 1}/{n_clips})",
            )
            last_clip = n
            clip_idx = n + 1

        try:
            while True:
                parts = ["  "]
                if has_clip:
                    if last_clip is None:
                        parts.append(f"[V]iew clip (1/{n_clips})")
                    else:
                        parts.append(f"[V]=next clip ({clip_idx % n_clips + 1}/{n_clips}) [R]eplay")
                if top_hint:
                    parts.append(f"[Y=accept {top_hint[0]}]")
                if len(views) > 1:
                    parts.append("[M]erge")
                parts.append("[Enter=skip] [Q=quit] or type name: ")
                choice = input(" ".join(parts)).strip()
                lower = choice.lower()

                if lower in ("v", "view") and has_clip:
                    _play_clip(clip_idx % n_clips)
                    continue
                elif lower.startswith("v") and lower[1:].isdigit() and has_clip:
                    n = int(lower[1:]) - 1
                    if not 0 <= n < n_clips:
                        print(f"  No clip {lower[1:]} — this speaker has {n_clips} clip(s).")
                        continue
                    _play_clip(n)
                    continue
                elif lower in ("r", "replay") and has_clip:
                    _play_clip(last_clip if last_clip is not None else 0)
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
                    _prompt_link_politician(mappings, label, res.new_name)
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
                    _prompt_link_politician(mappings, label, res.new_name)
                    break
        finally:
            _stop_player(current_player)

        if quit_requested:
            break
        if advance:
            i += 1

    return changes


def _enroll_after_review(
    changes: list[dict],
    current_mappings: dict,
    meeting_dir: Path,
    meeting_id: str,
    segments,
) -> None:
    """Offer to enroll speakers that were identified or corrected during review.

    Only runs if embeddings are available on disk.
    """
    import numpy as np

    from src.enroll import (
        _enroll_mapping,
        load_profiles,
        resolve_mapping_enrollment,
        save_profiles,
    )
    from src.models import SpeakerMapping

    embeddings_path = meeting_dir / "embeddings.json"
    if not embeddings_path.exists():
        return

    if not changes:
        return

    if not sys.stdin.isatty():
        return

    with open(embeddings_path, "r") as f:
        emb_data = json.load(f)
    speaker_embeddings = {k: np.array(v) for k, v in emb_data.items()}

    # Find which changed speakers have embeddings available
    enrollable = []
    profile_db = load_profiles()

    for change in changes:
        if "merged_into" in change:
            continue  # merges aren't renames — nothing to enroll here
        label = change["label"]
        new_name = change["new_name"]
        if not new_name or label not in speaker_embeddings:
            continue
        # Show the exact key the speaker will enroll under — same resolver
        # _enroll_mapping uses, so the NEW/UPDATE tag can't drift from reality.
        mapping = current_mappings.get(label) or SpeakerMapping(
            speaker_label=label, speaker_name=new_name)
        slug, _, _ = resolve_mapping_enrollment(mapping)
        is_new = slug not in profile_db.profiles
        enrollable.append({
            "label": label,
            "name": new_name,
            "slug": slug,
            "is_new": is_new,
        })

    if not enrollable:
        return

    print(f"\n{len(enrollable)} speaker(s) can be enrolled/updated in voice profiles:")
    for e in enrollable:
        tag = "NEW" if e["is_new"] else "UPDATE"
        print(f"  {e['label']}: {e['name']} [{tag}]")

    choice = input("\nEnroll these speakers? [Y/n] ").strip().lower()
    if choice in ("", "y", "yes"):
        for e in enrollable:
            mapping = current_mappings.get(e["label"]) or SpeakerMapping(
                speaker_label=e["label"], speaker_name=e["name"])
            seg_count = sum(1 for s in segments if s.speaker_label == e["label"])
            # roster=None on purpose: identity here comes from the mapping itself
            # (set when the speaker was linked during review), not a roster lookup.
            _enroll_mapping(
                profile_db, mapping,
                speaker_embeddings[e["label"]],
                meeting_id, seg_count, None,
            )
            tag = "NEW" if e["is_new"] else "UPDATE"
            print(f"  Enrolled: {e['name']} ({e['slug']}) [{tag}]")

        save_profiles(profile_db)
        print(f"  Voice profiles saved ({len(profile_db.profiles)} total)")
    else:
        print("  Skipped enrollment.")


def _review_meeting(meeting_id: str) -> None:
    """Interactively review and correct all speakers in an existing meeting."""
    from src import config
    from src.export import export_all
    from src.models import Meeting, SpeakerMapping

    meeting_dir = config.MEETINGS_DIR / meeting_id
    named_path = meeting_dir / "transcript_named.json"

    if not named_path.exists():
        print(f"No transcript found for meeting: {meeting_id}")
        print(f"  Expected at: {named_path}")
        available = sorted(
            d.name for d in config.MEETINGS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ) if config.MEETINGS_DIR.exists() else []
        if available:
            print(f"  Available meetings: {', '.join(available)}")
        sys.exit(1)

    with open(named_path, "r") as f:
        meeting = Meeting.from_dict(json.load(f))

    video_path = find_video_file(meeting_dir, meeting.audio_source)
    embeddings_path = meeting_dir / "embeddings.json"

    import numpy as np
    from src.enroll import load_profiles
    from src import review as _review

    if embeddings_path.exists():
        with open(embeddings_path, "r") as f:
            _emb = json.load(f)
        embeddings = {k: np.array(v) for k, v in _emb.items()}
    else:
        embeddings = {}
    profile_db = load_profiles()
    body_slug = _meeting_body_slug(meeting_dir)

    views = _review.build_review_state(meeting.segments, meeting.speakers, embeddings, profile_db, show_text=True)

    print(f"\nReviewing: {meeting.city} {meeting.meeting_type} ({meeting.date})")
    print(f"Meeting ID: {meeting_id}")
    if video_path:
        print(f"Video: {Path(video_path).name}")
    else:
        print("Video: not found (no clip playback available)")
    print(f"Speakers: {len(views)}")
    print()

    # Show overview table
    print("  #  Label         Current Name                  Segs  Speech  Conf   Method")
    print("  " + "-" * 90)
    for i, v in enumerate(views):
        name = v.current_name or "(unidentified)"
        method = v.current_method or ""
        mins = v.total_speech_seconds / 60
        hint = ""
        if v.soft_hints and not (v.current_name and v.current_confidence >= 0.85):
            top = v.soft_hints[0]
            hint = f"  ~ {top[0]} ({top[1]:.2f})"
        print(f"  {i+1:>2}  {v.label:<13} {name:<30} {v.seg_count:>4}  {mins:>5.1f}m  {v.current_confidence:.2f}  {method}{hint}")

    print()
    print("Commands for each speaker:")
    print("  [Enter]  Skip (keep current name)")
    print("  [V]      View video clip of this speaker")
    print("  [Y]      Accept suggested voice match (if shown)")
    print("  [M]      Merge this speaker into another")
    print("  [name]   Type a new name to assign")
    print("  [Q]      Quit review (save changes so far)")
    print()

    changes = _interactive_speaker_review(
        meeting.segments, meeting.speakers, embeddings, profile_db,
        video_path, str(meeting_dir / "audio.wav"),
        body_slug=body_slug, show_text=True,
    )
    _persist_after_review(meeting_dir, meeting.segments, embeddings, changes)

    # Apply corrections to segments and save
    if changes:
        for seg in meeting.segments:
            m = meeting.speakers.get(seg.speaker_label)
            if m and m.speaker_name:
                seg.speaker_name = m.speaker_name
                seg.confidence = m.confidence
                seg.id_method = m.id_method

        with open(named_path, "w") as f:
            json.dump(meeting.to_dict(), f, indent=2)

        export_dir = meeting_dir / "exports"
        export_all(meeting, export_dir)

        print(f"\n{len(changes)} correction(s) saved:")
        for c in changes:
            if "merged_into" in c:
                print(f"  {c['label']}: merged into {c['merged_into']}")
                continue
            old = c["old_name"] or "(unidentified)"
            print(f"  {c['label']}: {old} -> {c['new_name']}")
        print(f"Exports updated: {export_dir}")

        # Offer enrollment
        _enroll_after_review(
            changes, meeting.speakers, meeting_dir,
            meeting.meeting_id, meeting.segments,
        )
    else:
        print("\nNo changes made.")


def _identify_speakers_standalone(meeting_id: str) -> None:
    """Standalone pre-identification for an existing meeting.

    Works on any meeting that has diarization + embeddings.
    Does not require transcription to be complete.
    """
    from src import config
    from src.models import Meeting, SpeakerMapping

    meeting_dir = config.MEETINGS_DIR / meeting_id
    diarization_path = meeting_dir / "diarization.json"
    embeddings_path = meeting_dir / "embeddings.json"

    if not diarization_path.exists():
        print(f"No diarization found for meeting: {meeting_id}")
        print(f"  Expected at: {diarization_path}")
        available = sorted(
            d.name for d in config.MEETINGS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ) if config.MEETINGS_DIR.exists() else []
        if available:
            print(f"  Available meetings: {', '.join(available)}")
        sys.exit(1)

    # Load segments (prefer transcribed, fall back to diarization-only)
    from src.models import Segment

    transcript_path = meeting_dir / "transcript_raw.json"
    named_path = meeting_dir / "transcript_named.json"
    has_text = False

    if named_path.exists():
        with open(named_path, "r") as f:
            meeting = Meeting.from_dict(json.load(f))
        segments = meeting.segments
        current_mappings = meeting.speakers
        has_text = any(s.text for s in segments)
    elif transcript_path.exists():
        with open(transcript_path, "r") as f:
            segments = [Segment.from_dict(d) for d in json.load(f)]
        current_mappings = {}
        has_text = any(s.text for s in segments)
    else:
        with open(diarization_path, "r") as f:
            segments = [Segment.from_dict(d) for d in json.load(f)]
        current_mappings = {}

    video_path = find_video_file(meeting_dir, "")

    import numpy as np
    from src.enroll import load_profiles
    from src import review as _review

    if embeddings_path.exists():
        with open(embeddings_path, "r") as f:
            _emb = json.load(f)
        embeddings = {k: np.array(v) for k, v in _emb.items()}
    else:
        embeddings = {}
    profile_db = load_profiles()
    body_slug = _meeting_body_slug(meeting_dir)

    views = _review.build_review_state(segments, current_mappings, embeddings, profile_db, show_text=has_text)

    print(f"\nSpeaker Identification: {meeting_id}")
    if video_path:
        print(f"Video: {Path(video_path).name}")
    else:
        print("Video: not found")
    if has_text:
        print("Transcript: available (text samples shown)")
    else:
        print("Transcript: not yet available (video clips only)")
    print(f"Speakers: {len(views)}")
    _hint_count = sum(1 for v in views if v.soft_hints)
    if _hint_count:
        print(f"Voice hints: {_hint_count} speaker(s) have possible profile matches")
    print()

    # Show overview table
    print("  #  Label         Current Name                  Segs  Speech  Voice Hint")
    print("  " + "-" * 85)
    for i, v in enumerate(views):
        name = v.current_name or "(unidentified)"
        mins = v.total_speech_seconds / 60
        hint = ""
        if v.soft_hints:
            top = v.soft_hints[0]
            if top[1] >= 0.85:
                hint = f"* {top[0]} ({top[1]:.2f})"
            else:
                hint = f"? {top[0]} ({top[1]:.2f})"
        print(f"  {i+1:>2}  {v.label:<13} {name:<30} {v.seg_count:>4}  {mins:>5.1f}m  {hint}")

    print()
    print("Commands for each speaker:")
    print("  [Enter]  Skip")
    print("  [V]      View video clip of this speaker")
    print("  [Y]      Accept suggested voice match (if shown)")
    print("  [M]      Merge this speaker into another")
    print("  [name]   Type a name to assign")
    print("  [Q]      Quit (save changes so far)")
    print()

    changes = _interactive_speaker_review(
        segments, current_mappings, embeddings, profile_db,
        video_path, str(meeting_dir / "audio.wav"),
        body_slug=body_slug, show_text=has_text,
    )
    _persist_after_review(meeting_dir, segments, embeddings, changes)

    if changes:
        # Save identifications as pre_identifications.json
        pre_id_path = meeting_dir / "pre_identifications.json"
        pre_ids = {}
        for label, mapping in current_mappings.items():
            if isinstance(mapping, SpeakerMapping) and mapping.speaker_name:
                pre_ids[label] = {
                    "speaker_name": mapping.speaker_name,
                    "confidence": mapping.confidence,
                    "id_method": mapping.id_method,
                }
        with open(pre_id_path, "w") as f:
            json.dump(pre_ids, f, indent=2)

        print(f"\n{len(changes)} identification(s) saved to {pre_id_path.name}")
        for c in changes:
            if "merged_into" in c:
                print(f"  {c['label']}: merged into {c['merged_into']}")
                continue
            old = c["old_name"] or "(unidentified)"
            print(f"  {c['label']}: {old} -> {c['new_name']}")

        # If named transcript exists, update it too. Reuse the in-memory
        # `meeting` (loaded at the top of this function and mutated in place by
        # the review loop) — re-loading from disk here would discard merges.
        if named_path.exists():
            for seg in meeting.segments:
                m = meeting.speakers.get(seg.speaker_label)
                if m and m.speaker_name:
                    seg.speaker_name = m.speaker_name
                    seg.confidence = m.confidence
                    seg.id_method = m.id_method
            with open(named_path, "w") as f:
                json.dump(meeting.to_dict(), f, indent=2)
            from src.export import export_all
            export_dir = meeting_dir / "exports"
            export_all(meeting, export_dir)
            print(f"Transcript and exports updated.")

        # Offer enrollment
        _enroll_after_review(
            changes, current_mappings, meeting_dir,
            meeting_id, segments,
        )

        print("\nThese identifications will be used as ground truth in Stage 4")
        print("(overriding LLM/pattern matching for identified speakers).")
    else:
        print("\nNo identifications made.")


def main():
    parser = argparse.ArgumentParser(
        description="CouncilScribe — Automated City Council Meeting Transcription",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --input meeting.mp4 --city Bloomington --date 2026-02-10
  %(prog)s --input "https://catstv.net/..." --city Bloomington --date 2026-02-10
  %(prog)s --browse-catstv --city Bloomington
  %(prog)s --resume 2026-02-10-regular-session

Environment Variables:
  CS_DATA_DIR          Override data directory (default: ~/CouncilScribe)
  HF_TOKEN             HuggingFace API token (for pyannote model access)
""",
    )

    # Audio source
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--input", "-i",
        help="Path to audio/video file or URL (direct or CATS TV page)",
    )
    source.add_argument(
        "--browse-catstv",
        action="store_true",
        help="Browse CATS TV archive and select a meeting interactively",
    )
    source.add_argument(
        "--resume",
        metavar="MEETING_ID",
        help="Resume a previous meeting by its ID",
    )

    # Meeting metadata
    parser.add_argument("--city", default=None,
                        help=f"City name (default: {CITY_DEFAULT}; prompted if omitted)")
    parser.add_argument("--date", default="", help="Meeting date (YYYY-MM-DD; prompted if omitted)")
    parser.add_argument("--meeting-type", default=None,
                        help="Meeting type or name, free text "
                             "(e.g. \"Regular Session\", \"Plan Commission\"; prompted if omitted)")
    parser.add_argument("--meeting-id", default="", help="Custom meeting ID (auto-generated if omitted)")

    # Processing options
    parser.add_argument("--num-speakers", type=int, default=0,
                        help="Expected number of speakers (0 = auto-detect)")
    parser.add_argument("--noise-reduce", action="store_true",
                        help="Apply spectral noise reduction to audio")
    parser.add_argument("--cookies", metavar="FILE",
                        help="Netscape-format cookies file for authenticated downloads "
                             "(e.g. private Facebook videos). Export from browser with "
                             "a 'Get cookies.txt' extension.")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip LLM-based speaker identification (Layer 3)")
    parser.add_argument("--skip-summary", action="store_true",
                        help="Skip meeting summary generation (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--confirm-enroll", action="store_true",
                        help="Interactively confirm enrollment for borderline speakers (0.70-0.85 confidence)")
    parser.add_argument("--merge", action="store_true",
                        help="Opt-in: collapse speakers whose voice embeddings exceed "
                             "SPEAKER_MERGE_THRESHOLD. Disabled by default — current "
                             "pyannote 3.1 doesn't fragment Bloomington audio in practice, "
                             "and embeddings have known NaN issues. See bench/diagnose_merge.py.")
    parser.add_argument("--use-vtt", action="store_true",
                        help="Use VTT subtitles instead of Whisper (auto-detected if captions.vtt exists)")
    parser.add_argument("--diarizer", choices=["oss", "api"], default="oss",
                        help="Diarization backend. 'oss' uses local pyannote 3.1 "
                             "(default, free, ~50min/3hr meeting on L4). 'api' uses "
                             "pyannote.ai Precision-2 (cleaner segmentation, ~3min "
                             "for the same meeting, ~$0.45 per audio hour). "
                             "Requires PYANNOTE_AI_KEY in env. "
                             "Recommended per bench/FINDINGS.md.")
    parser.add_argument("--compute", choices=["local", "modal"], default="local",
                        help="Compute backend for GPU-intensive stages (diarization "
                             "with --diarizer oss, and Whisper transcription). "
                             "'local' runs on this machine (default). "
                             "'modal' offloads to Modal cloud GPUs — requires modal "
                             "installed and authenticated (modal token new). "
                             "Has no effect when --diarizer api is used (pyannote.ai "
                             "is always remote).")
    parser.add_argument("--default", action="store_true",
                        help="Skip metadata prompts and use defaults "
                             f"({CITY_DEFAULT} / {MEETING_TYPE_DEFAULT} / today)")

    # Utilities
    parser.add_argument("--list-profiles", action="store_true",
                        help="List stored voice profiles and exit")
    parser.add_argument("--fix-profiles", action="store_true",
                        help="Rename stored voice profiles using the council roster and exit")
    parser.add_argument("--fix-transcripts", action="store_true",
                        help="Re-correct speaker names in all existing transcripts using the roster and re-export")
    parser.add_argument(
        "--repair-transcript",
        metavar="MEETING_ID",
        help="Rebuild one processed caption-backed transcript/exports without "
             "rerunning diarization or speaker identification.",
    )
    parser.add_argument("--publish", action="store_true",
                        help="After the pipeline completes, publish the meeting to Supabase for the web site")
    parser.add_argument("--publish-meeting", metavar="MEETING_ID",
                        help="Publish an already-processed meeting to Supabase and exit")
    parser.add_argument("--merge-profiles", nargs=2, metavar=("SOURCE", "DEST"),
                        help="Merge SOURCE profile into DEST profile and exit (use slugs from --list-profiles)")
    parser.add_argument("--show-roster", action="store_true",
                        help="Display the current council roster and exit")
    parser.add_argument("--no-review", action="store_true",
                        help="Skip the interactive speaker review at the end of a run")
    parser.add_argument("--review", metavar="MEETING_ID",
                        help="Review/correct/merge speakers in an existing meeting "
                             "(canonical; --review-meeting and --identify-speakers are aliases)")
    parser.add_argument("--review-meeting", metavar="MEETING_ID",
                        help="Interactively review and correct all speakers in an existing meeting (alias of --review)")
    parser.add_argument("--identify-speakers", metavar="MEETING_ID",
                        help="Standalone speaker identification with video clips and voice hints (works pre-transcription) (alias of --review)")
    parser.add_argument("--pre-identify", action="store_true",
                        help="Interactive speaker identification after diarization, before transcription (pipeline mode)")
    parser.add_argument("--batch", metavar="FILE_OR_DIR",
                        help="Batch mode: text file with one input per line (path or 'URL DATE'), or directory of videos")
    parser.add_argument("--batch-resume", action="store_true",
                        help="Resume an interrupted batch run (skip already-completed meetings)")
    parser.add_argument(
        "--body",
        type=str,
        default=None,
        help="Governing body slug (e.g. bloomington-common-council). "
             "Persisted to pipeline_state.json on first run; omit on re-invocation.",
    )
    parser.add_argument(
        "--force-retag",
        action="store_true",
        default=False,
        help="Overwrite a meeting's persisted body_slug. Rewinds stages 4-7. "
             "Requires --body.",
    )
    parser.add_argument(
        "--redo",
        choices=["ingest", "diarize", "transcribe", "identify", "summary", "all"],
        default=None,
        help="Re-run from this stage onward. Use with --resume or --input. "
             "'ingest' re-downloads and re-checks for captions. "
             "'all' re-runs everything from ingest.",
    )

    args = parser.parse_args()

    if args.repair_transcript:
        cli_argv = sys.argv[1:]
        repair_conflict_map = {
            "--input": _option_supplied(cli_argv, "--input", "-i"),
            "--browse-catstv": _option_supplied(cli_argv, "--browse-catstv"),
            "--resume": _option_supplied(cli_argv, "--resume"),
            "--city": _option_supplied(cli_argv, "--city"),
            "--date": _option_supplied(cli_argv, "--date"),
            "--meeting-type": _option_supplied(cli_argv, "--meeting-type"),
            "--meeting-id": _option_supplied(cli_argv, "--meeting-id"),
            "--num-speakers": _option_supplied(cli_argv, "--num-speakers"),
            "--noise-reduce": _option_supplied(cli_argv, "--noise-reduce"),
            "--cookies": _option_supplied(cli_argv, "--cookies"),
            "--skip-llm": _option_supplied(cli_argv, "--skip-llm"),
            "--skip-summary": _option_supplied(cli_argv, "--skip-summary"),
            "--confirm-enroll": _option_supplied(cli_argv, "--confirm-enroll"),
            "--merge": _option_supplied(cli_argv, "--merge"),
            "--use-vtt": _option_supplied(cli_argv, "--use-vtt"),
            "--diarizer": _option_supplied(cli_argv, "--diarizer"),
            "--compute": _option_supplied(cli_argv, "--compute"),
            "--default": _option_supplied(cli_argv, "--default"),
            "--list-profiles": _option_supplied(cli_argv, "--list-profiles"),
            "--fix-profiles": _option_supplied(cli_argv, "--fix-profiles"),
            "--fix-transcripts": _option_supplied(cli_argv, "--fix-transcripts"),
            "--publish": _option_supplied(cli_argv, "--publish"),
            "--publish-meeting": _option_supplied(cli_argv, "--publish-meeting"),
            "--merge-profiles": _option_supplied(cli_argv, "--merge-profiles"),
            "--show-roster": _option_supplied(cli_argv, "--show-roster"),
            "--no-review": _option_supplied(cli_argv, "--no-review"),
            "--review": _option_supplied(cli_argv, "--review"),
            "--review-meeting": _option_supplied(cli_argv, "--review-meeting"),
            "--identify-speakers": _option_supplied(cli_argv, "--identify-speakers"),
            "--pre-identify": _option_supplied(cli_argv, "--pre-identify"),
            "--batch": _option_supplied(cli_argv, "--batch"),
            "--batch-resume": _option_supplied(cli_argv, "--batch-resume"),
            "--body": _option_supplied(cli_argv, "--body"),
            "--force-retag": _option_supplied(cli_argv, "--force-retag"),
            "--redo": _option_supplied(cli_argv, "--redo"),
        }
        repair_conflicts = [
            flag
            for flag, supplied in repair_conflict_map.items()
            if supplied
        ]
        if repair_conflicts:
            parser.error(
                "--repair-transcript cannot be combined with "
                + ", ".join(repair_conflicts)
            )
        _repair_transcript_standalone(args.repair_transcript)
        return

    # D-12: --force-retag requires --body
    if args.force_retag and not args.body:
        parser.error("--force-retag requires --body <slug>")

    if args.redo and not args.resume and not args.input:
        parser.error("--redo requires --resume <MEETING_ID> or --input <URL/FILE>")

    # --- Utility commands ---
    if args.show_roster:
        from src import config
        from src.roster import load_roster
        roster = load_roster()
        if not roster:
            print("No council roster found.")
            print(f"  Create one at: {config.CONFIG_DIR / 'council_roster.json'}")
        else:
            print(f"Council Roster: {roster.city} {roster.body}")
            print(f"  {len(roster.members)} member(s):\n")
            for m in roster.members:
                print(f"  {m.name}")
                if m.aliases:
                    print(f"    Aliases: {', '.join(m.aliases)}")
        return

    if args.list_profiles:
        from src.enroll import load_profiles
        db = load_profiles()
        if not db.profiles:
            print("No voice profiles stored yet.")
        else:
            print(f"Stored profiles ({len(db.profiles)}):")
            for pid, p in db.profiles.items():
                print(f"  {pid}: {p.display_name}")
                print(f"    Meetings: {', '.join(p.meetings_seen)}")
                print(f"    Confirmed segments: {p.total_segments_confirmed}")
                print(f"    Embeddings: {len(p.embeddings)}")
        return

    if args.fix_profiles:
        from src.enroll import fix_profiles_with_roster, load_profiles, save_profiles
        from src.roster import load_roster
        roster = load_roster()
        if not roster:
            print("No council roster found. Cannot fix profiles.")
            sys.exit(1)
        db = load_profiles()
        if not db.profiles:
            print("No voice profiles stored yet.")
            return
        print(f"Checking {len(db.profiles)} profile(s) against roster...")
        changes = fix_profiles_with_roster(db, roster)
        if changes:
            save_profiles(db)
            print(f"\nRenamed {len(changes)} profile(s):")
            for c in changes:
                print(f"  {c}")
            print(f"\nTotal profiles: {len(db.profiles)}")
            for pid, p in db.profiles.items():
                print(f"  {pid}: {p.display_name}")
        else:
            print("All profiles already match the roster. No changes needed.")
        return

    if args.merge_profiles:
        from src.enroll import load_profiles, merge_profiles, save_profiles
        source, dest = args.merge_profiles
        db = load_profiles()
        if source not in db.profiles:
            print(f"Source profile '{source}' not found.")
            print(f"Available: {', '.join(db.profiles.keys())}")
            sys.exit(1)
        if dest not in db.profiles:
            print(f"Destination profile '{dest}' not found.")
            print(f"Available: {', '.join(db.profiles.keys())}")
            sys.exit(1)
        src_p = db.profiles[source]
        dst_p = db.profiles[dest]
        print(f"Merging '{source}' ({src_p.display_name}) into '{dest}' ({dst_p.display_name})...")
        merge_profiles(db, source, dest)
        save_profiles(db)
        merged = db.profiles[dest]
        print(f"  Done. '{dest}' now has {len(merged.embeddings)} embeddings, "
              f"{merged.total_segments_confirmed} segments, "
              f"{len(merged.meetings_seen)} meetings")
        return

    if args.fix_transcripts:
        _fix_transcripts()
        return

    if args.publish_meeting:
        _publish_meeting_standalone(args.publish_meeting)
        return

    if args.review:
        # Canonical review: full post-transcription review when a named
        # transcript exists, else diarization-only identification.
        from src import config as _config
        if (_config.MEETINGS_DIR / args.review / "transcript_named.json").exists():
            _review_meeting(args.review)
        else:
            _identify_speakers_standalone(args.review)
        return

    if args.review_meeting:
        _review_meeting(args.review_meeting)
        return

    if args.identify_speakers:
        _identify_speakers_standalone(args.identify_speakers)
        return

    # --- Batch mode ---
    if args.batch:
        _run_batch(args)
        return

    # --- CATS TV browser ---
    if args.browse_catstv:
        selected = browse_catstv()
        if selected is None:
            print("No meeting selected. Exiting.")
            return
        args.input = selected["video_url"]
        if selected["date"] and not args.date:
            args.date = selected["date"]
        if selected["name"]:
            args.meeting_type = selected["name"]
        print(f"\nSelected: {selected['name']} ({selected['date']})")
        print(f"  URL: {args.input}\n")

    # --- Resume mode ---
    if args.resume:
        from src import config
        meeting_dir = config.MEETINGS_DIR / args.resume
        state_file = meeting_dir / "pipeline_state.json"
        if not state_file.exists():
            print(f"No checkpoint found for meeting ID: {args.resume}")
            print(f"  Expected at: {state_file}")
            sys.exit(1)

        # Load meeting metadata from named transcript or reconstruct
        named_path = meeting_dir / "transcript_named.json"
        if named_path.exists():
            with open(named_path, "r") as f:
                data = json.load(f)
            args.input = data.get("audio_source", "")
            args.city = data.get("city", args.city)
            args.date = data.get("date", args.date)
            args.meeting_type = data.get("meeting_type", args.meeting_type)
        else:
            # Use the WAV file as input since audio is already ingested
            wav = meeting_dir / "audio.wav"
            if wav.exists():
                args.input = str(wav)
            else:
                print(f"Cannot resume: no audio.wav found in {meeting_dir}")
                sys.exit(1)

        args.meeting_id = args.resume
        print(f"Resuming meeting: {args.resume}")


    # --- Validate ---
    if not args.input:
        parser.print_help()
        print("\nError: --input, --browse-catstv, or --resume is required.")
        sys.exit(1)

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

    # --- Run ---
    run_pipeline(args)


if __name__ == "__main__":
    main()
