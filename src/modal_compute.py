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
import threading
import time
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


#: A trailing window shorter than this fraction of a full chunk is folded
#: into its predecessor instead of standing alone — tiny windows produce
#: thin-evidence centroids that stitch badly.
_MIN_TRAILING_FRACTION = 0.5


def plan_chunk_windows(
    duration_s: float, chunk_minutes: int
) -> list[tuple[float, float]]:
    """Split [0, duration_s] into canonical (start, end) diarization windows.

    `chunk_minutes <= 0`, or audio shorter than one chunk, yields a single
    window — i.e. exactly the single-pass behaviour. The worker derives its
    actual READ window by expanding each canonical span by the configured
    overlap; these spans themselves abut exactly with no gap or overlap.
    """
    if chunk_minutes <= 0:
        return [(0.0, duration_s)]
    chunk_s = float(chunk_minutes * 60)
    if duration_s <= chunk_s:
        return [(0.0, duration_s)]

    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_s:
        end = min(start + chunk_s, duration_s)
        windows.append((start, end))
        start = end
    # Fold a stub trailing window into its predecessor.
    if len(windows) > 1:
        last_start, last_end = windows[-1]
        if (last_end - last_start) < chunk_s * _MIN_TRAILING_FRACTION:
            prev_start, _ = windows[-2]
            windows[-2:] = [(prev_start, last_end)]
    return windows


def fetch_chunk_payloads(
    app,
    wav_path: Path,
    meeting_id: str,
    chunk_minutes: int,
    embedders: tuple[str, ...] | None = None,
) -> list[str]:
    """Fan canonical windows out across Modal containers; return raw payloads.

    This is the expensive half of chunked diarization (all the GPU time).
    Split from `stitch_chunk_payloads` so calibration can pay for the GPU
    work once and then re-stitch the same payloads at many thresholds for
    free — stitching is pure local code.
    """
    from . import config
    from .audio_utils import get_audio_duration

    if embedders is None:
        embedders = (config.DIARIZE_CHUNK_EMBEDDER,)

    duration = get_audio_duration(wav_path)
    windows = plan_chunk_windows(duration, chunk_minutes)
    overlap = float(config.DIARIZE_CHUNK_OVERLAP_SECONDS)
    print(f"  Chunked diarization: {len(windows)} window(s) of "
          f"{chunk_minutes} min (+{overlap:.0f}s overlap) over "
          f"{duration / 60:.1f} min of audio")

    args = [
        (meeting_id, start, end, overlap, index, tuple(embedders))
        for index, (start, end) in enumerate(windows)
    ]
    with app.app.run():
        return list(app.diarize_chunk_window.starmap(args))


def _recompute_centroids(result, per_window_centroids, per_window_speech):
    """Duration-weighted global centroids for the sequential path.

    `reconcile_chunks` returns turns + diagnostics but not global voiceprints.
    Each StableTurn retains chunk_index/local_speaker, so the mapping back to
    the chunk-local centroid that produced it is exact. (The global-identity
    path builds centroids from its own per-turn vectors instead.)
    """
    import numpy as np

    weighted_sums: dict[str, np.ndarray] = {}
    weighted_totals: dict[str, float] = {}
    seen_pairs: set[tuple[int, str, str]] = set()
    for turn in result.turns:
        key = (turn.chunk_index, turn.local_speaker, turn.speaker)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        centroid = per_window_centroids.get(turn.chunk_index, {}).get(turn.local_speaker)
        if centroid is None:
            continue
        weight = per_window_speech.get(turn.chunk_index, {}).get(turn.local_speaker, 0.0)
        if weight <= 0:
            continue
        vec = np.asarray(centroid, dtype=float)
        if turn.speaker in weighted_sums:
            weighted_sums[turn.speaker] = weighted_sums[turn.speaker] + vec * weight
        else:
            weighted_sums[turn.speaker] = vec * weight
        weighted_totals[turn.speaker] = weighted_totals.get(turn.speaker, 0.0) + weight

    all_speakers = {t.speaker for t in result.turns}
    centroids = {
        speaker: (weighted_sums[speaker] / weighted_totals[speaker]).tolist()
        for speaker in all_speakers
        if speaker in weighted_totals and weighted_totals[speaker] > 0
    }
    for speaker in sorted(all_speakers - set(centroids)):
        print(f"  WARNING: global speaker {speaker} has no centroid "
              "(every contributing window had too little speech to embed); "
              "turns still publish but voice-profile matching will skip it.")
    return centroids


def stitch_chunk_payloads(
    payloads: list[str],
    use_merge: bool,
    embedding_threshold: float | None = None,
    merge_threshold: float | None = None,
    identity: str | None = None,
    cluster_threshold: float | None = None,
    linkage: str | None = None,
    embedder: str | None = None,
) -> tuple[list[dict], dict[str, list[float]]]:
    """Reconcile window payloads into the standard (segments, centroids) pair.

    Pure (no Modal, no GPU): `src.speaker_reconcile.reconcile_chunks` matches
    window-local labels into stable meeting-wide labels (temporal overlap
    first, then embedding similarity), then the existing
    `merge_similar_speakers` handles residual fragmentation exactly as the
    single-pass path does. Both thresholds are overridable so calibration can
    sweep them against cached payloads.

    `identity` selects the cross-window identity strategy: "global"
    (src.global_identity, one constrained clustering over per-turn
    embeddings) or "sequential" (src.speaker_reconcile, per-window centroid
    matching). Global requires payloads carrying `turn_embeddings`; payloads
    cached before that field existed fall back to sequential automatically, so
    calibration can compare both on the same cached GPU work.
    """
    import numpy as np

    from . import config
    from .global_identity import cluster_global_identities, decode_turn_vectors
    from .merge import merge_similar_speakers
    from .models import Segment
    from .speaker_reconcile import (
        ChunkResult,
        ChunkWindow,
        LocalTurn,
        reconcile_chunks,
    )

    if embedding_threshold is None:
        # NOT speaker_reconcile's 0.75 default — that is tuned for VibeVoice's
        # windows and fragments pyannote per-window centroids. See the config
        # comment for the calibration behind this value.
        embedding_threshold = config.DIARIZE_CHUNK_STITCH_THRESHOLD
    if identity is None:
        identity = config.DIARIZE_CHUNK_IDENTITY
    if cluster_threshold is None:
        cluster_threshold = config.DIARIZE_CHUNK_CLUSTER_THRESHOLD
    if linkage is None:
        linkage = config.DIARIZE_CHUNK_LINKAGE
    if embedder is None:
        embedder = config.DIARIZE_CHUNK_EMBEDDER

    chunks: list[ChunkResult] = []
    # local_speaker -> {(chunk_index, local_speaker): (centroid, weight)} for
    # recomputing global centroids after reconciliation, since reconcile_chunks
    # returns turns/diagnostics but not the global voiceprints themselves.
    per_window_centroids: dict[int, dict[str, list[float]]] = {}
    per_window_speech: dict[int, dict[str, float]] = {}
    # {window_index: {turn_index: vector}}, for the global-identity path. Built
    # in the same pass as `chunks` rather than a second json.loads over every
    # payload — the payload is already parsed here.
    turn_vectors: dict[int, dict[int, "np.ndarray"]] = {}
    have_turn_embeddings = True
    slowest_elapsed = 0.0
    for payload in payloads:
        data = json.loads(payload)
        index = data["window_index"]
        per_window_centroids[index] = data["centroids"]
        per_window_speech[index] = data["speech_seconds"]
        slowest_elapsed = max(slowest_elapsed, data.get("elapsed_s", 0.0))
        chunks.append(ChunkResult(
            window=ChunkWindow(index, data["window_start_s"], data["window_end_s"]),
            turns=[
                LocalTurn(index, t[0], t[1], t[2]) for t in data["turns"]
            ],
            embeddings={
                label: np.asarray(vec, dtype=float)
                for label, vec in data["centroids"].items()
            },
            speech_seconds=data["speech_seconds"],
        ))

        blocks = data.get("turn_embeddings") or {}
        block = blocks.get(embedder)
        if block is None and len(blocks) == 1:
            block = next(iter(blocks.values()))  # single-embedder payload
        if block is None:
            have_turn_embeddings = False
        else:
            turn_vectors[index] = decode_turn_vectors(block)
    print(f"  Slowest window: {slowest_elapsed:.0f}s "
          f"(vs one single-pass call over the whole meeting)")

    if identity == "global" and have_turn_embeddings:
        global_result = cluster_global_identities(
            chunks, turn_vectors,
            threshold=cluster_threshold, linkage=linkage, label_prefix="SPEAKER_",
        )
        result = global_result
        centroids = global_result.centroids
        diag = global_result.diagnostics
        print(f"  Global identity ({linkage} linkage @ {cluster_threshold:.2f}, "
              f"{embedder}): {diag['nodes']} window-local speaker(s) -> "
              f"{diag['clusters']} global (bounds "
              f"{diag['window_speaker_bounds'][0]}-{diag['window_speaker_bounds'][1]}); "
              f"{len(diag['temporal_matches'])} seam match(es), "
              f"{len(diag['embedding_matches'])} embedding merge(s), "
              f"{len(diag['cannot_link_blocks'])} blocked by cannot-link, "
              f"margin {diag['margin']}")
        for speaker in diag["speakers_without_centroid"]:
            print(f"  WARNING: global speaker {speaker} has no centroid "
                  "(no turn of theirs could be embedded); turns still publish "
                  "but voice-profile matching will skip it.")
    else:
        if identity == "global":
            print("  Note: payloads carry no per-turn embeddings for "
                  f"{embedder} — falling back to sequential centroid matching.")
        result = reconcile_chunks(
            chunks, embedding_threshold=embedding_threshold, label_prefix="SPEAKER_"
        )
        diag = result.diagnostics
        print(f"  Reconciled to {len({t.speaker for t in result.turns})} global "
              f"speaker(s): {len(diag['temporal_matches'])} temporal match(es), "
              f"{len(diag['embedding_matches'])} embedding match(es), "
              f"{len(diag['new_speakers'])} new speaker(s)")
        centroids = _recompute_centroids(
            result, per_window_centroids, per_window_speech
        )

    segments_data = [
        {
            "segment_id": i,
            "start_time": turn.start,
            "end_time": turn.end,
            "speaker_label": turn.speaker,
            "text": "",
            "words": [],
        }
        for i, turn in enumerate(result.turns)
    ]

    if use_merge and centroids:
        segs = [
            Segment(
                segment_id=d["segment_id"],
                start_time=d["start_time"],
                end_time=d["end_time"],
                speaker_label=d["speaker_label"],
            )
            for d in segments_data
        ]
        merged_segs, merged_centroids, merge_log = merge_similar_speakers(
            segs, {k: np.array(v) for k, v in centroids.items()},
            threshold=merge_threshold,
        )
        if merge_log:
            print(f"  Post-reconcile merge: {merge_log}")
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

    return segments_data, centroids


def _run_chunked_diarization(
    app, wav_path: Path, meeting_id: str, chunk_minutes: int, use_merge: bool
) -> tuple[list[dict], dict[str, list[float]]]:
    """Chunked diarization end to end: fan out to Modal, then stitch locally."""
    payloads = fetch_chunk_payloads(app, wav_path, meeting_id, chunk_minutes)
    return stitch_chunk_payloads(payloads, use_merge)


def upload_audio(wav_path: Path, meeting_id: str) -> None:
    """Upload *wav_path* to the Modal volume at meetings/{meeting_id}/audio.wav."""
    modal = _ensure_modal()

    vol = modal.Volume.from_name(_VOLUME_NAME, create_if_missing=True)
    remote = f"meetings/{meeting_id}/audio.wav"
    size_mb = wav_path.stat().st_size / (1024 * 1024)
    print(f"  Uploading audio to Modal volume ({size_mb:.1f} MB)...")
    with vol.batch_upload(force=True) as batch:
        batch.put_file(str(wav_path), remote)
    print("  Upload complete.")


def run_diarization(
    wav_path: Path,
    meeting_id: str,
    use_merge: bool = False,
    diarizer: str = "oss",
    chunk_minutes: int = 0,
) -> tuple[list[dict], dict[str, list[float]]]:
    """Run the selected open-source diarizer + embeddings on Modal GPUs.

    Pyannote runs as one L4 function. VibeVoice runs on A100-80GB/H100, saves
    diagnostics locally, then uses a separate L4 function for normal
    WeSpeaker embeddings. When `diarizer != "vibevoice"` and `chunk_minutes`
    is positive, pyannote instead runs as N overlapping windows fanned out
    across Modal containers (`_run_chunked_diarization`) — diarization cost
    is ~quadratic in window length, so this is the main speedup for long
    meetings; `chunk_minutes <= 0` keeps the single-pass path below.

    Returns:
        segments_data — list of Segment.to_dict() dicts (text/words empty).
        embeddings    — {speaker_label: centroid_vector_as_list}
    """
    app = _modal_app()

    upload_audio(wav_path, meeting_id)

    if diarizer != "vibevoice" and chunk_minutes > 0:
        return _run_chunked_diarization(
            app, wav_path, meeting_id, chunk_minutes, use_merge
        )

    merge_label = " (with merge)" if use_merge else ""
    backend_label = "VibeVoice" if diarizer == "vibevoice" else "pyannote OSS"
    print(f"  Dispatching {backend_label} diarization{merge_label} to Modal GPU...")
    with app.app.run():
        if diarizer == "vibevoice":
            inference_path = app.vibevoice_infer_chunks.remote(meeting_id)
            result_json = app.pipeline_vibevoice_diarize.remote(
                meeting_id, inference_path
            )
            result = json.loads(result_json)
            diagnostics_path = wav_path.parent / "vibevoice_diagnostics.json"
            diagnostics_path.write_text(
                json.dumps(result.get("diagnostics", {}), indent=2)
            )
            embeddings_json = app.pipeline_extract_embeddings.remote(
                meeting_id, json.dumps(result["segments"])
            )
            return result["segments"], json.loads(embeddings_json)
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

    n_segs = len(segments)
    print(f"  Dispatching Whisper transcription to Modal GPU (large-v3, {n_segs} segments)...")

    stop_heartbeat = threading.Event()

    def _heartbeat():
        t0 = time.time()
        interval = 30
        while not stop_heartbeat.wait(interval):
            elapsed = time.time() - t0
            mins, secs = divmod(int(elapsed), 60)
            print(f"  Still transcribing... ({mins}m{secs:02d}s elapsed — run 'modal logs' for segment progress)", flush=True)

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()
    try:
        with app.app.run():
            result_json = app.pipeline_transcribe.remote(
                meeting_id, json.dumps(segments)
            )
    finally:
        stop_heartbeat.set()
        hb.join()

    return json.loads(result_json)
