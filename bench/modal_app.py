"""Modal App: diarization benchmark for CouncilScribe.

Runs 4 diarization models against a shared meeting set:
    - pyannote_oss        — pyannote/speaker-diarization-3.1 (current baseline)
    - pyannote_merged     — pyannote OSS + speaker-merge (src/merge.py)
    - pyannote_ai         — pyannote.ai Precision-2 (paid API)
    - nemo_sortformer     — NVIDIA NeMo Sortformer

Each function consumes a pre-normalized 16kHz mono WAV from the shared
Modal Volume and writes a `.rttm` file back to the volume. The local
orchestrator (run.py) launches every (model, meeting) pair and pulls the
RTTMs down for scoring.

Run from the repo root (NOT from inside bench/):

    modal run bench/modal_app.py::fetch_meeting --meeting-id 2026-02-25-council
    modal run bench/modal_app.py::diarize_pyannote_oss --meeting-id 2026-02-25-council

Or via the orchestrator: `python bench/run.py`
"""

from __future__ import annotations

import time
from pathlib import Path

import modal

# ---------------------------------------------------------------------------
# Shared infrastructure
# ---------------------------------------------------------------------------

APP_NAME = "councilscribe-bench"
VOLUME_NAME = "councilscribe-bench"

# Volume layout:
#   /vol/meetings/<meeting_id>/audio.wav   (16kHz mono, normalized)
#   /vol/meetings/<meeting_id>/source.m4v  (raw download, kept for re-runs)
#   /vol/results/<meeting_id>/<model>.rttm
#   /vol/results/<meeting_id>/<model>.meta.json
#   /vol/cache/                            (HF, NeMo model weights)
VOLUME_PATH = "/vol"

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

# Secrets pull values from your Modal dashboard:
#   modal secret create huggingface-token HF_TOKEN=hf_xxx
#   modal secret create pyannote-ai-key   PYANNOTE_AI_KEY=sk-xxx
hf_secret = modal.Secret.from_name("huggingface-token")
pyannote_ai_secret = modal.Secret.from_name("pyannote-ai-key")

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

# Image used by fetch_meeting (download + ffmpeg). No GPU deps.
fetch_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("requests==2.32.3", "beautifulsoup4==4.12.3", "pyyaml==6.0.2")
)

# Pyannote OSS pipeline. Used by both `pyannote_oss` and `pyannote_merged`.
# omegaconf is a transitive dep used by pyannote.audio.Inference (the embedding
# extractor); not auto-installed, so we add it explicitly.
pyannote_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1", "git")
    .pip_install(
        # pyannote.audio 4.x pulls its own torch/torchcodec/huggingface_hub
        # versions. Pinning too tightly causes resolver conflicts.
        "pyannote.audio==4.0.4",
        "omegaconf>=2.3",
        "soundfile==0.12.1",
        "numpy>=1.26,<3",
        "scipy>=1.13,<2",
    )
    .env({"HF_HOME": "/vol/cache/huggingface"})
)

# Variant of pyannote image with src/ bundled in so merge.py + models.py are
# importable inside the function. Lives at /root/cs_src in the container.
pyannote_merged_image = pyannote_image.add_local_dir("./src", remote_path="/root/cs_src")

# Image for Whisper transcription (production pipeline functions).
whisper_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "faster-whisper>=1.0.0",
        "soundfile==0.12.1",
        "numpy>=1.26,<3",
    )
)

# pyannote.ai Precision-2 (REST API; no GPU needed).
# ffmpeg/ffprobe needed because the function uses _ffprobe_duration().
api_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("requests==2.32.3")
)

# NVIDIA NeMo Sortformer.
# matplotlib is a transitive dep of nemo_toolkit[asr] for some plotting paths;
# not always pulled, so install explicitly. huggingface_hub left unpinned so
# nemo can pull its own compatible version.
nemo_image = (
    modal.Image.from_registry("nvcr.io/nvidia/pytorch:24.07-py3", add_python="3.10")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "Cython",
        # Sortformer (SortformerEncLabelModel) was added in NeMo 2.4+.
        # 2.0.0 lacks the class entirely, hence the import error in earlier
        # sweep runs.
        "nemo_toolkit[asr]==2.7.3",
        "matplotlib",
        "soundfile==0.12.1",
    )
    .env({"HF_HOME": "/vol/cache/huggingface", "NEMO_CACHE_DIR": "/vol/cache/nemo"})
)


# ---------------------------------------------------------------------------
# RTTM helper (shared by all models)
# ---------------------------------------------------------------------------

def _annotation_to_turns(diarization) -> list[tuple[float, float, str]]:
    """Extract (start, end, speaker) from pyannote 3.x Annotation OR 4.x DiarizeOutput.

    Mirrors the dual-API handling in src/diarize.py so the benchmark works
    against either pinned version.
    """
    turns: list[tuple[float, float, str]] = []
    if hasattr(diarization, "itertracks"):
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            turns.append((turn.start, turn.end, str(speaker)))
    elif hasattr(diarization, "speaker_diarization"):
        for turn, speaker in diarization.speaker_diarization:
            label = (
                f"SPEAKER_{int(speaker):02d}"
                if str(speaker).isdigit()
                else str(speaker)
            )
            turns.append((turn.start, turn.end, label))
    else:
        raise RuntimeError(
            f"Unexpected diarization output type: {type(diarization)}"
        )
    return turns


def turns_to_rttm(meeting_id: str, turns: list[tuple[float, float, str]]) -> str:
    """Convert (start, end, speaker_label) turns to NIST RTTM string.

    RTTM line format:
        SPEAKER <file> 1 <onset> <duration> <NA> <NA> <speaker_label> <NA> <NA>
    """
    lines = []
    for start, end, speaker in turns:
        duration = end - start
        if duration <= 0:
            continue
        lines.append(
            f"SPEAKER {meeting_id} 1 {start:.3f} {duration:.3f} "
            f"<NA> <NA> {speaker} <NA> <NA>"
        )
    return "\n".join(lines) + "\n"


def _cached_result(meeting_id: str, model_name: str) -> dict | None:
    """Return cached meta dict if both .rttm and .meta.json already exist.

    Lets the orchestrator be safely restarted — any (model, meeting) pair
    that completed in a previous run is skipped on re-entry.
    """
    import json

    results_dir = Path(VOLUME_PATH) / "results" / meeting_id
    rttm_path = results_dir / f"{model_name}.rttm"
    meta_path = results_dir / f"{model_name}.meta.json"
    if rttm_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        meta["cached"] = True
        print(f"  Cached — skipping. ({rttm_path})")
        return meta
    return None


def _write_result(
    meeting_id: str,
    model_name: str,
    turns: list[tuple[float, float, str]],
    elapsed_seconds: float,
    audio_duration_seconds: float,
    cost_usd: float,
    extra: dict | None = None,
) -> dict:
    """Write RTTM + metadata to the Modal volume and return a summary dict."""
    import json

    results_dir = Path(VOLUME_PATH) / "results" / meeting_id
    results_dir.mkdir(parents=True, exist_ok=True)

    rttm_path = results_dir / f"{model_name}.rttm"
    meta_path = results_dir / f"{model_name}.meta.json"

    rttm_path.write_text(turns_to_rttm(meeting_id, turns))

    distinct_speakers = sorted({s for _, _, s in turns})
    summary = {
        "meeting_id": meeting_id,
        "model": model_name,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "audio_duration_seconds": round(audio_duration_seconds, 2),
        "realtime_factor": round(audio_duration_seconds / elapsed_seconds, 2)
            if elapsed_seconds > 0 else None,
        "num_turns": len(turns),
        "num_distinct_speakers": len(distinct_speakers),
        "speaker_labels": distinct_speakers,
        "cost_usd": round(cost_usd, 4),
        "rttm_path": str(rttm_path),
    }
    if extra:
        summary.update(extra)
    meta_path.write_text(json.dumps(summary, indent=2))
    return summary


# ---------------------------------------------------------------------------
# Fetch: download from CATS TV, normalize to 16kHz mono WAV
# ---------------------------------------------------------------------------

@app.function(
    image=fetch_image,
    volumes={VOLUME_PATH: volume},
    timeout=60 * 60,
)
def fetch_meeting(
    meeting_id: str,
    permalink: str | None = None,
    latest_filter: str | None = None,
) -> dict:
    """Resolve a meeting to a normalized WAV in the volume.

    Exactly one of `permalink` or `latest_filter` must be provided.
    `latest_filter` is currently understood as 'bloomington_city_council',
    which matches the `B_CC_\\d{6}.m4v` pattern (excludes committees,
    fiscal sessions, deliberations, etc.).
    """
    import re
    import subprocess
    import requests
    from bs4 import BeautifulSoup

    if (permalink is None) == (latest_filter is None):
        raise ValueError("Provide exactly one of permalink or latest_filter")

    audio_dir = Path(VOLUME_PATH) / "meetings" / meeting_id
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav_path = audio_dir / "audio.wav"
    source_path = audio_dir / "source.m4v"

    if wav_path.exists() and source_path.exists():
        # Cached. Skip redownload + reencode.
        duration = _ffprobe_duration(wav_path)
        return {
            "meeting_id": meeting_id,
            "wav_path": str(wav_path),
            "source_url": "(cached)",
            "duration_seconds": duration,
        }

    # Resolve source URL
    if latest_filter == "bloomington_city_council":
        resp = requests.get("https://catstv.net/government.php?issearch=govt", timeout=60)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        pat = re.compile(r"^B_CC_\d{6}\.m4v$")
        chosen = None
        for link in soup.find_all("a", attrs={"data-m4v": True}):
            m4v = link.get("data-m4v", "")
            if pat.match(m4v):
                chosen = {
                    "m4v": m4v,
                    "date": link.get("data-date", ""),
                    "permalink": link.get("data-permalink", ""),
                }
                break
        if not chosen:
            raise RuntimeError("No Bloomington City Council meeting found on CATS TV")
        video_url = f"https://catstv.blob.core.windows.net/videoarchive/{chosen['m4v']}"
        print(f"  Latest BCC: {chosen['date']} ({chosen['m4v']})")
        source_url = video_url
    else:
        # Permalink: scrape page for the m4v URL.
        source_url = _resolve_catstv_permalink(permalink)
        print(f"  Resolved permalink to: {source_url}")

    # Download
    print(f"  Downloading {source_url} ...")
    with requests.get(source_url, stream=True, timeout=(30, 600)) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(source_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total and downloaded % (32 * 1024 * 1024) < 8192:
                    pct = downloaded / total * 100
                    print(f"    {downloaded / 1e6:.0f} / {total / 1e6:.0f} MB ({pct:.0f}%)")

    # Normalize: 16kHz mono WAV
    print(f"  Normalizing to 16kHz mono WAV ...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(source_path),
            "-ac", "1", "-ar", "16000", "-vn",
            str(wav_path),
        ],
        check=True, capture_output=True,
    )
    volume.commit()

    duration = _ffprobe_duration(wav_path)
    return {
        "meeting_id": meeting_id,
        "wav_path": str(wav_path),
        "source_url": source_url,
        "duration_seconds": duration,
    }


def _resolve_catstv_permalink(permalink: str) -> str:
    """Scrape a CATS TV page for its m4v blob URL."""
    import re
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(permalink, timeout=60)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # jPlayer config in inline script
    for script in soup.find_all("script"):
        text = script.string or ""
        match = re.search(r'm4v:\s*["\']([^"\']+\.m4v)["\']', text)
        if match:
            m4v = match.group(1)
            return m4v if m4v.startswith("http") else \
                f"https://catstv.blob.core.windows.net/videoarchive/{m4v}"

    # data-m4v attribute fallback
    link = soup.find("a", attrs={"data-m4v": True})
    if link:
        m4v = link["data-m4v"]
        return m4v if m4v.startswith("http") else \
            f"https://catstv.blob.core.windows.net/videoarchive/{m4v}"

    raise ValueError(f"Could not find video URL on CATS TV page: {permalink}")


def _ffprobe_duration(wav_path: Path) -> float:
    """Get audio duration in seconds via ffprobe."""
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(wav_path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# ---------------------------------------------------------------------------
# Model 1: pyannote 3.1 OSS (baseline)
# ---------------------------------------------------------------------------

@app.function(
    image=pyannote_image,
    volumes={VOLUME_PATH: volume},
    secrets=[hf_secret],
    gpu="L4",
    timeout=60 * 60 * 2,
)
def diarize_pyannote_oss(meeting_id: str) -> dict:
    """Run pyannote/speaker-diarization-3.1 and write RTTM to volume."""
    if (cached := _cached_result(meeting_id, "pyannote_oss")) is not None:
        return cached

    import os
    import torch
    from pyannote.audio import Pipeline

    wav_path = Path(VOLUME_PATH) / "meetings" / meeting_id / "audio.wav"
    if not wav_path.exists():
        raise FileNotFoundError(f"{wav_path} not found. Run fetch_meeting first.")

    audio_duration = _ffprobe_duration(wav_path)
    print(f"  Audio: {audio_duration:.0f}s")

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=os.environ["HF_TOKEN"],
    )
    pipeline.to(torch.device("cuda"))

    t0 = time.time()
    diarization = pipeline(str(wav_path))
    elapsed = time.time() - t0

    turns = _annotation_to_turns(diarization)

    # Cost: L4 GPU at ~$0.80/hr (verify in Modal dashboard)
    cost = (elapsed / 3600) * 0.80

    summary = _write_result(
        meeting_id, "pyannote_oss", turns, elapsed, audio_duration, cost,
    )
    volume.commit()
    return summary


# ---------------------------------------------------------------------------
# Model 2: pyannote 3.1 OSS + speaker-merge (src/merge.py)
# ---------------------------------------------------------------------------

@app.function(
    image=pyannote_merged_image,
    volumes={VOLUME_PATH: volume},
    secrets=[hf_secret],
    gpu="L4",
    timeout=60 * 60 * 2,
)
def diarize_pyannote_merged(meeting_id: str) -> dict:
    """Run pyannote OSS, then collapse fragmented speakers via embedding similarity.

    Mirrors the production pipeline (Stage 2 + speaker merge) so the score
    reflects what CouncilScribe actually produces today, post-merge.
    """
    if (cached := _cached_result(meeting_id, "pyannote_merged")) is not None:
        return cached

    import os
    import sys
    import numpy as np
    import torch
    from pyannote.audio import Pipeline, Model, Inference
    import soundfile as sf

    # Make `cs_src` importable as `cs_src.*` so we can reuse merge_similar_speakers
    sys.path.insert(0, "/root")
    from cs_src.merge import merge_similar_speakers
    from cs_src.models import Segment

    wav_path = Path(VOLUME_PATH) / "meetings" / meeting_id / "audio.wav"
    if not wav_path.exists():
        raise FileNotFoundError(f"{wav_path} not found. Run fetch_meeting first.")

    audio_duration = _ffprobe_duration(wav_path)
    print(f"  Audio: {audio_duration:.0f}s")

    device = torch.device("cuda")

    # --- Step 1: diarize ---
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=os.environ["HF_TOKEN"],
    )
    pipeline.to(device)

    t0 = time.time()
    diarization = pipeline(str(wav_path))

    raw_turns = _annotation_to_turns(diarization)
    segments: list[Segment] = [
        Segment(
            segment_id=i,
            start_time=round(start, 3),
            end_time=round(end, 3),
            speaker_label=str(speaker),
        )
        for i, (start, end, speaker) in enumerate(raw_turns)
    ]

    # --- Step 2: extract per-speaker embeddings ---
    emb_model = Model.from_pretrained("pyannote/embedding", token=os.environ["HF_TOKEN"])
    inference = Inference(emb_model, window="whole", device=device)

    samples, sr = sf.read(str(wav_path))
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    embeddings_per_speaker: dict[str, list[np.ndarray]] = {}
    for seg in segments:
        i0 = int(seg.start_time * sr)
        i1 = int(seg.end_time * sr)
        slice_ = samples[i0:i1]
        if len(slice_) < int(sr * 0.3):  # skip very short segments
            continue
        wf = torch.tensor(slice_, dtype=torch.float32).unsqueeze(0).to(device)
        emb = inference({"waveform": wf, "sample_rate": sr})
        embeddings_per_speaker.setdefault(seg.speaker_label, []).append(emb)

    centroids = {
        label: np.mean(embs, axis=0)
        for label, embs in embeddings_per_speaker.items()
    }

    # --- Step 3: merge ---
    merged_segments, merged_embeddings, merge_log = merge_similar_speakers(
        segments, centroids,  # uses config.SPEAKER_MERGE_THRESHOLD = 0.80
    )

    elapsed = time.time() - t0

    turns = [(s.start_time, s.end_time, s.speaker_label) for s in merged_segments]
    cost = (elapsed / 3600) * 0.80

    summary = _write_result(
        meeting_id, "pyannote_merged", turns, elapsed, audio_duration, cost,
        extra={
            "merge_log": merge_log,
            "speakers_before_merge": len(centroids),
            "speakers_after_merge": len(merged_embeddings),
        },
    )
    volume.commit()
    return summary


# ---------------------------------------------------------------------------
# Model 3: pyannote.ai Precision-2 (paid API)
# ---------------------------------------------------------------------------

@app.function(
    image=api_image,
    volumes={VOLUME_PATH: volume},
    secrets=[pyannote_ai_secret],
    timeout=60 * 60 * 2,
)
def diarize_pyannote_ai(meeting_id: str) -> dict:
    """Submit audio to pyannote.ai Precision-2 API, poll for results.

    API reference: https://docs.pyannote.ai/  (Precision-2 endpoint)
    Flow:
        1. Request a one-time upload URL (POST /v1/media/input).
        2. PUT the WAV file to that URL.
        3. POST /v1/diarize with media={uri} → returns job_id.
        4. Poll GET /v1/jobs/{job_id} until status is succeeded/failed.
    """
    if (cached := _cached_result(meeting_id, "pyannote_ai")) is not None:
        return cached

    import os
    import json
    import requests

    wav_path = Path(VOLUME_PATH) / "meetings" / meeting_id / "audio.wav"
    if not wav_path.exists():
        raise FileNotFoundError(f"{wav_path} not found. Run fetch_meeting first.")

    audio_duration = _ffprobe_duration(wav_path)
    print(f"  Audio: {audio_duration:.0f}s")

    api_key = os.environ["PYANNOTE_AI_KEY"]
    base = "https://api.pyannote.ai"
    headers = {"Authorization": f"Bearer {api_key}"}

    t0 = time.time()

    # 1. Get presigned upload URL
    media_uri = f"media://councilscribe-bench/{meeting_id}.wav"
    create = requests.post(
        f"{base}/v1/media/input",
        headers={**headers, "Content-Type": "application/json"},
        data=json.dumps({"url": media_uri}),
        timeout=60,
    )
    create.raise_for_status()
    upload_url = create.json()["url"]

    # 2. Upload audio
    with open(wav_path, "rb") as f:
        up = requests.put(
            upload_url,
            data=f,
            headers={"Content-Type": "audio/wav"},
            timeout=(30, 1800),
        )
    up.raise_for_status()

    # 3. Submit diarization job
    submit = requests.post(
        f"{base}/v1/diarize",
        headers={**headers, "Content-Type": "application/json"},
        data=json.dumps({"url": media_uri}),
        timeout=60,
    )
    submit.raise_for_status()
    job_id = submit.json()["jobId"]
    print(f"  Submitted job {job_id}")

    # 4. Poll
    output = None
    while True:
        time.sleep(5)
        status_resp = requests.get(f"{base}/v1/jobs/{job_id}", headers=headers, timeout=60)
        status_resp.raise_for_status()
        body = status_resp.json()
        status = body.get("status")
        if status == "succeeded":
            output = body["output"]
            break
        if status == "failed":
            raise RuntimeError(f"pyannote.ai job failed: {body}")
        if status not in ("created", "running"):
            print(f"  ! unexpected status: {status} — continuing to poll")

    elapsed = time.time() - t0

    # Output format: {"diarization": [{"start": float, "end": float, "speaker": str}, ...]}
    turns: list[tuple[float, float, str]] = []
    for item in output.get("diarization", []):
        turns.append((float(item["start"]), float(item["end"]), str(item["speaker"])))

    # Approx cost: ~$0.10–0.20 per audio hour, depending on plan. Use 0.15 as midpoint.
    cost = (audio_duration / 3600) * 0.15

    summary = _write_result(
        meeting_id, "pyannote_ai", turns, elapsed, audio_duration, cost,
    )
    volume.commit()
    return summary


# ---------------------------------------------------------------------------
# Model 4: NVIDIA NeMo Sortformer
# ---------------------------------------------------------------------------

@app.function(
    image=nemo_image,
    volumes={VOLUME_PATH: volume},
    secrets=[hf_secret],
    gpu="L4",
    timeout=60 * 60 * 2,
)
def diarize_nemo_sortformer(meeting_id: str) -> dict:
    """Run NVIDIA NeMo Sortformer diarization.

    Uses `nvidia/diar_sortformer_4spk-v1` from HF (released Sortformer model).
    This is technically a 4-speaker model — for council meetings with >4
    speakers it will under-cluster. Expected: this is a finding of the
    benchmark, not a code issue. If Sortformer otherwise looks promising,
    follow up with NeMo's MSDD/clustering pipeline which handles arbitrary N.
    """
    if (cached := _cached_result(meeting_id, "nemo_sortformer")) is not None:
        return cached

    import os
    import torch
    from nemo.collections.asr.models import SortformerEncLabelModel

    wav_path = Path(VOLUME_PATH) / "meetings" / meeting_id / "audio.wav"
    if not wav_path.exists():
        raise FileNotFoundError(f"{wav_path} not found. Run fetch_meeting first.")

    audio_duration = _ffprobe_duration(wav_path)
    print(f"  Audio: {audio_duration:.0f}s")

    # HF_TOKEN needed because NeMo pulls model card metadata from HF Hub
    os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", os.environ.get("HF_TOKEN", ""))

    model = SortformerEncLabelModel.from_pretrained("nvidia/diar_sortformer_4spk-v1")
    model = model.to(torch.device("cuda")).eval()

    t0 = time.time()
    # Sortformer's diarize() returns a list (one entry per file) of speaker
    # activity predictions. We pass a single file → single result.
    predictions = model.diarize(audio=[str(wav_path)], batch_size=1)
    elapsed = time.time() - t0

    # `predictions[0]` is a list of strings in the format
    #   "start_sec end_sec speaker_idx"
    turns: list[tuple[float, float, str]] = []
    for line in predictions[0]:
        parts = line.strip().split()
        if len(parts) >= 3:
            start = float(parts[0])
            end = float(parts[1])
            spk = f"SORT_{int(parts[2]):02d}"
            turns.append((start, end, spk))

    cost = (elapsed / 3600) * 0.80

    summary = _write_result(
        meeting_id, "nemo_sortformer", turns, elapsed, audio_duration, cost,
        extra={"model_card": "nvidia/diar_sortformer_4spk-v1"},
    )
    volume.commit()
    return summary


# ---------------------------------------------------------------------------
# Production pipeline functions (called by src/modal_compute.py)
# ---------------------------------------------------------------------------
# These mirror the benchmark functions above but return JSON-serialisable
# dicts directly instead of writing RTTM files to the volume. run_local.py
# uses these when --compute modal is passed.

@app.function(
    image=pyannote_merged_image,  # has cs_src for merge.py
    volumes={VOLUME_PATH: volume},
    secrets=[hf_secret],
    gpu="L4",
    timeout=60 * 60 * 2,
)
def pipeline_diarize_and_embed(meeting_id: str, use_merge: bool = False) -> str:
    """Diarize + extract speaker embeddings; return JSON for run_local.py.

    Return format (JSON string):
        {"segments": [Segment.to_dict(), ...], "embeddings": {label: [float, ...]}}

    The audio must already be in the volume at
        /vol/meetings/{meeting_id}/audio.wav
    Upload it first via src.modal_compute.upload_audio().
    """
    import json as _json
    import os
    import sys

    import numpy as np
    import soundfile as sf
    import torch
    from pyannote.audio import Inference, Model, Pipeline

    wav_path = Path(VOLUME_PATH) / "meetings" / meeting_id / "audio.wav"
    if not wav_path.exists():
        raise FileNotFoundError(
            f"Audio not found in Modal volume: {wav_path}. "
            "Run src.modal_compute.upload_audio() first."
        )

    device = torch.device("cuda")

    # --- Diarize ---
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=os.environ["HF_TOKEN"],
    )
    pipeline.to(device)

    t0 = time.time()
    diarization = pipeline(str(wav_path))
    turns = _annotation_to_turns(diarization)

    # Convert to plain dicts (no Segment dataclass dependency here).
    segments_data = [
        {
            "segment_id": i,
            "start_time": round(start, 3),
            "end_time": round(end, 3),
            "speaker_label": spk,
            "text": "",
            "words": [],
        }
        for i, (start, end, spk) in enumerate(turns)
    ]

    # --- Extract per-speaker centroid embeddings ---
    emb_model = Model.from_pretrained("pyannote/embedding", token=os.environ["HF_TOKEN"])
    inference = Inference(emb_model, window="whole", device=device)

    samples, sr = sf.read(str(wav_path))
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    embs_per_speaker: dict[str, list] = {}
    for seg in segments_data:
        i0 = int(seg["start_time"] * sr)
        i1 = int(seg["end_time"] * sr)
        clip = samples[i0:i1]
        if len(clip) < int(sr * 0.3):
            continue
        wf = torch.tensor(clip, dtype=torch.float32).unsqueeze(0).to(device)
        emb = inference({"waveform": wf, "sample_rate": sr})
        embs_per_speaker.setdefault(seg["speaker_label"], []).append(emb)

    centroids = {
        label: np.mean(vecs, axis=0).tolist()
        for label, vecs in embs_per_speaker.items()
    }

    # --- Optional speaker merge (mirrors src/merge.py logic) ---
    if use_merge:
        sys.path.insert(0, "/root")
        from cs_src.merge import merge_similar_speakers
        from cs_src.models import Segment as _Seg

        _segs = [
            _Seg(
                segment_id=d["segment_id"],
                start_time=d["start_time"],
                end_time=d["end_time"],
                speaker_label=d["speaker_label"],
            )
            for d in segments_data
        ]
        _centroids_np = {k: np.array(v) for k, v in centroids.items()}
        merged_segs, merged_centroids, merge_log = merge_similar_speakers(_segs, _centroids_np)
        if merge_log:
            print(f"  Merged {len(centroids) - len(merged_centroids)} speaker(s): {merge_log}")
        segments_data = [
            {
                "segment_id": s.segment_id,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "speaker_label": s.speaker_label,
                "text": "",
                "words": [],
            }
            for s in merged_segs
        ]
        centroids = {k: v.tolist() for k, v in merged_centroids.items()}

    elapsed = time.time() - t0
    print(f"  Diarization + embeddings done in {elapsed:.1f}s "
          f"({len(segments_data)} segments, {len(centroids)} speakers)")

    return _json.dumps({"segments": segments_data, "embeddings": centroids})


@app.function(
    image=whisper_image,
    volumes={VOLUME_PATH: volume},
    gpu="L4",
    timeout=60 * 60 * 4,
)
def pipeline_transcribe(meeting_id: str, segments_json: str) -> str:
    """Transcribe diarized segments with Whisper large-v3; return updated JSON.

    Accepts the segments list serialised as a JSON string (same format as
    Segment.to_dict()) and returns the same structure with ``text`` and
    ``words`` populated.
    """
    import json as _json

    import numpy as np
    import soundfile as sf
    from faster_whisper import WhisperModel

    wav_path = Path(VOLUME_PATH) / "meetings" / meeting_id / "audio.wav"
    if not wav_path.exists():
        raise FileNotFoundError(f"Audio not found in Modal volume: {wav_path}")

    segments_data: list[dict] = _json.loads(segments_json)

    model = WhisperModel("large-v3", device="cuda", compute_type="float16")

    samples, sr = sf.read(str(wav_path))
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    total = len(segments_data)
    t0 = time.time()

    for i, seg in enumerate(segments_data):
        i0 = int(seg["start_time"] * sr)
        i1 = int(seg["end_time"] * sr)
        clip = samples[i0:i1].astype(np.float32)

        if len(clip) < sr * 0.1:
            seg["text"] = ""
            seg["words"] = []
            continue

        result_segs, _ = model.transcribe(clip, word_timestamps=True, language="en")

        words = []
        text_parts = []
        for rs in result_segs:
            if rs.words:
                for w in rs.words:
                    words.append({
                        "word": w.word.strip(),
                        "start": round(seg["start_time"] + w.start, 3),
                        "end": round(seg["start_time"] + w.end, 3),
                    })
            text_parts.append(rs.text.strip())

        seg["text"] = " ".join(text_parts).strip()
        seg["words"] = words

        if (i + 1) % 50 == 0:
            pct = (i + 1) / total * 100
            elapsed = time.time() - t0
            print(f"  [{i + 1}/{total}] ({pct:.0f}%) {elapsed:.0f}s elapsed", flush=True)

    return _json.dumps(segments_data)


# ---------------------------------------------------------------------------
# Convenience entrypoint for `modal run`
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def smoke(meeting_id: str = "2026-02-25-council"):
    """Quick smoke test: fetch + pyannote OSS only.

    Run with:  modal run bench/modal_app.py::smoke --meeting-id 2026-02-25-council
    """
    # Resolve permalink from meetings.yaml
    import yaml
    config_path = Path(__file__).parent / "meetings.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    entry = next((m for m in cfg["meetings"] if m["id"] == meeting_id), None)
    if entry is None:
        raise SystemExit(f"meeting_id {meeting_id!r} not in meetings.yaml")

    if "permalink" in entry:
        info = fetch_meeting.remote(meeting_id, permalink=entry["permalink"])
    else:
        info = fetch_meeting.remote(meeting_id, latest_filter=entry["latest"])
    print(f"\nFetched: {info}\n")

    result = diarize_pyannote_oss.remote(meeting_id)
    print(f"\nResult: {result}\n")
