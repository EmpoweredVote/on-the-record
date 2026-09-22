import json
from pathlib import Path

from bench.stage_floor_artifacts import stage


def _floor_meeting(root: Path, slug: str, *, hls: bool = True) -> Path:
    d = root / slug
    d.mkdir(parents=True)
    (d / "pipeline_state.json").write_text(json.dumps({
        "completed_stage": 4, "event_kind": "floor", "date": slug[:10],
        "review_status": "review", "trusted_coverage": 0.63,
        "processing_metadata": {"source_audio_url":
            "https://cdn.example/house.m3u8" if hls else ""},
    }))
    (d / "transcript_named.json").write_text(json.dumps({"title": "House Floor"}))
    (d / "embeddings.json").write_text(json.dumps({"SPEAKER_00": [0.1, 0.2]}))
    (d / "diarization.json").write_text(json.dumps([{"speaker_label": "SPEAKER_00"}]))
    (d / "audio.wav").write_bytes(b"RIFFxxxx")  # must NOT be staged
    return d


def test_stage_copies_json_only_and_excludes_audio(tmp_path):
    meetings = tmp_path / "meetings"; meetings.mkdir()
    _floor_meeting(meetings, "2026-09-03-house-floor")
    (meetings / "2026-09-03-council").mkdir()  # non-floor: ignored
    (meetings / "2026-09-03-council" / "pipeline_state.json").write_text("{}")

    out = tmp_path / "staging"
    result = stage(meetings, out)

    staged = out / "2026-09-03-house-floor"
    assert (staged / "pipeline_state.json").exists()
    assert (staged / "transcript_named.json").exists()
    assert (staged / "embeddings.json").exists()
    assert not (staged / "audio.wav").exists()          # audio excluded
    assert not (out / "2026-09-03-council").exists()     # non-floor excluded
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest == result
    assert result[0]["slug"] == "2026-09-03-house-floor"
    assert result[0]["has_hls"] is True


def test_stage_flags_sessions_without_hls(tmp_path):
    meetings = tmp_path / "meetings"; meetings.mkdir()
    _floor_meeting(meetings, "2026-09-04-house-floor", hls=False)
    result = stage(meetings, tmp_path / "staging")
    assert result[0]["has_hls"] is False
