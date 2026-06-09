"""Modal compute backend for the CouncilScribe pipeline.

Called by run_local.py when --compute modal is passed. Handles uploading
audio to the shared Modal Volume, dispatching GPU work to Modal functions
defined in bench/modal_app.py, and returning results in the same format
that the local pipeline expects.

Prerequisites:
    pip install modal
    modal token new          # authenticate once
    modal secret create huggingface-token HF_TOKEN=hf_xxx
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent
_VOLUME_NAME = "councilscribe-bench"


def _ensure_modal():
    try:
        import modal
        return modal
    except ImportError:
        raise RuntimeError(
            "modal is not installed — run: pip install modal\n"
            "Then authenticate: modal token new"
        )


def _modal_app():
    """Import the bench Modal app (lazy, so modal isn't required at import time)."""
    if str(_REPO_DIR) not in sys.path:
        sys.path.insert(0, str(_REPO_DIR))
    from bench import modal_app as _app
    return _app


def upload_audio(wav_path: Path, meeting_id: str) -> None:
    """Upload *wav_path* to the Modal volume at meetings/{meeting_id}/audio.wav."""
    modal = _ensure_modal()

    vol = modal.Volume.from_name(_VOLUME_NAME, create_if_missing=True)
    remote = f"meetings/{meeting_id}/audio.wav"
    size_mb = wav_path.stat().st_size / (1024 * 1024)
    print(f"  Uploading audio to Modal volume ({size_mb:.1f} MB)...")
    with vol.batch_upload() as batch:
        batch.put_file(str(wav_path), remote)
    print("  Upload complete.")


def run_diarization(
    wav_path: Path,
    meeting_id: str,
    use_merge: bool = False,
) -> tuple[list[dict], dict[str, list[float]]]:
    """Run pyannote OSS diarization + embedding extraction on Modal GPU.

    Uploads the WAV to the Modal volume if not already present, then calls
    pipeline_diarize_and_embed on an L4 GPU.

    Returns:
        segments_data — list of Segment.to_dict() dicts (text/words empty).
        embeddings    — {speaker_label: centroid_vector_as_list}
    """
    app = _modal_app()

    upload_audio(wav_path, meeting_id)

    merge_label = " (with merge)" if use_merge else ""
    print(f"  Dispatching diarization{merge_label} to Modal GPU...")
    with app.app.run():
        result_json = app.pipeline_diarize_and_embed.remote(
            meeting_id, use_merge=use_merge
        )

    result = json.loads(result_json)
    return result["segments"], result["embeddings"]


def run_transcription(meeting_id: str, segments: list[dict]) -> list[dict]:
    """Transcribe diarized segments with Whisper large-v3 on a Modal GPU.

    The audio must already be in the Modal volume (upload_audio is called by
    run_diarization; if you're only transcribing, call upload_audio first).

    Returns the same segments list with ``text`` and ``words`` populated.
    """
    app = _modal_app()

    print("  Dispatching Whisper transcription to Modal GPU (large-v3)...")
    with app.app.run():
        result_json = app.pipeline_transcribe.remote(
            meeting_id, json.dumps(segments)
        )

    return json.loads(result_json)
