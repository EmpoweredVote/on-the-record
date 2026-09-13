"""Stage JSON-only copies of processed House-floor meeting folders for upload.

Excludes raw audio/video: the local GUI enroll path reads embeddings.json, and
review clips stream from the CDN HLS URL, so no audio is needed on the Mac.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

_JSON_FILES = ("pipeline_state.json", "transcript_named.json",
               "embeddings.json", "diarization.json")
_EXTRA = ("thumbnail.jpg",)


def _hls_url(state: dict) -> str:
    meta = state.get("processing_metadata") or {}
    return (meta.get("source_audio_url") or state.get("audio_source") or "")


def stage(meetings_dir: Path, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for d in sorted(meetings_dir.glob("*-house-floor")):
        state_path = d / "pipeline_state.json"
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text())
        except json.JSONDecodeError:
            continue
        dest = out_dir / d.name
        dest.mkdir(parents=True, exist_ok=True)
        for name in (*_JSON_FILES, *_EXTRA):
            src = d / name
            if src.exists():
                shutil.copy2(src, dest / name)
        has_hls = bool(_hls_url(state))
        if not has_hls:
            print(f"WARNING: {d.name} has no CDN HLS url; review playback will be unavailable")
        manifest.append({
            "slug": d.name,
            "date": state.get("date"),
            "gate_verdict": state.get("review_status"),
            "gate_coverage": state.get("trusted_coverage"),
            "has_hls": has_hls,
        })
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":  # pragma: no cover
    from src.config import MEETINGS_DIR

    stage(MEETINGS_DIR, Path("artifact_staging"))
