"""Stage 2 alternative: speaker diarization via pyannote.ai Precision-2 REST API.

Production-shaped adaptation of ``bench/modal_app.py::diarize_pyannote_ai``.
Same input/output contract as ``src.diarize.run_diarization`` so the rest of the
pipeline doesn't care which backend produced the segments.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import requests

from . import config
from .diarize import _merge_segments
from .models import Segment


API_BASE = "https://api.pyannote.ai"
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 60 * 60  # 1 hour ceiling for a single job


def run_diarization_via_api(
    wav_path: str | Path,
    api_key: str | None = None,
) -> list[Segment]:
    """Submit audio to pyannote.ai Precision-2, return merged Segments.

    Flow:
        1. POST /v1/media/input with a media:// URI -> presigned PUT URL
        2. PUT the WAV file
        3. POST /v1/diarize -> jobId
        4. Poll GET /v1/jobs/{jobId} until succeeded/failed

    Output matches src.diarize.run_diarization: adjacent same-speaker segments
    with gaps < config.MERGE_GAP_SECONDS are collapsed.
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise FileNotFoundError(f"Audio file not found: {wav_path}")

    api_key = api_key or os.environ.get("PYANNOTE_AI_KEY")
    if not api_key:
        raise RuntimeError(
            "pyannote.ai API key not provided. Set PYANNOTE_AI_KEY in env or pass api_key=..."
        )

    headers = {"Authorization": f"Bearer {api_key}"}
    json_headers = {**headers, "Content-Type": "application/json"}

    # Unique per-run object key so retries don't clobber each other.
    media_uri = f"media://councilscribe/{wav_path.stem}-{uuid.uuid4().hex[:8]}.wav"

    print(f"  Requesting upload URL for {media_uri}")
    create = requests.post(
        f"{API_BASE}/v1/media/input",
        headers=json_headers,
        data=json.dumps({"url": media_uri}),
        timeout=60,
    )
    create.raise_for_status()
    upload_url = create.json()["url"]

    size_mb = wav_path.stat().st_size / (1024 * 1024)
    print(f"  Uploading audio ({size_mb:.1f} MB)...")
    with open(wav_path, "rb") as f:
        up = requests.put(
            upload_url,
            data=f,
            headers={"Content-Type": "audio/wav"},
            timeout=(30, 1800),
        )
    up.raise_for_status()

    print("  Submitting diarization job...")
    submit = requests.post(
        f"{API_BASE}/v1/diarize",
        headers=json_headers,
        data=json.dumps({"url": media_uri}),
        timeout=60,
    )
    submit.raise_for_status()
    job_id = submit.json()["jobId"]
    print(f"  Job submitted: {job_id}")

    t_poll_start = time.time()
    output = None
    last_status = None
    while True:
        if time.time() - t_poll_start > POLL_TIMEOUT_SECONDS:
            raise TimeoutError(
                f"pyannote.ai job {job_id} did not complete within {POLL_TIMEOUT_SECONDS}s"
            )

        time.sleep(POLL_INTERVAL_SECONDS)
        resp = requests.get(
            f"{API_BASE}/v1/jobs/{job_id}", headers=headers, timeout=60
        )
        resp.raise_for_status()
        body = resp.json()
        status = body.get("status")

        if status != last_status:
            print(f"  Job status: {status}")
            last_status = status

        if status == "succeeded":
            output = body["output"]
            break
        if status in ("failed", "canceled"):
            raise RuntimeError(f"pyannote.ai job {job_id} {status}: {body}")
        # "created" / "running" / anything unexpected -> keep polling.

    raw_segments: list[tuple[float, float, str]] = []
    for item in output.get("diarization", []):
        raw_segments.append(
            (float(item["start"]), float(item["end"]), str(item["speaker"]))
        )

    if not raw_segments:
        print("  ! pyannote.ai returned 0 diarization segments")

    merged = _merge_segments(raw_segments)

    segments: list[Segment] = []
    for i, (start, end, label) in enumerate(merged):
        segments.append(
            Segment(
                segment_id=i,
                start_time=round(start, 3),
                end_time=round(end, 3),
                speaker_label=label,
            )
        )
    return segments
