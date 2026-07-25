from __future__ import annotations

from pathlib import Path

from backfill_thumbnails import meetings_needing_thumbnail


def _mk(mdir: Path, *, source=True, thumb=False):
    mdir.mkdir(parents=True)
    if source:
        (mdir / "source.webm").write_bytes(b"x")
    if thumb:
        (mdir / "thumbnail.jpg").write_bytes(b"x")


def test_lists_only_dirs_with_source_and_no_thumbnail(tmp_path: Path):
    _mk(tmp_path / "needs-it")                 # source, no thumb -> included
    _mk(tmp_path / "has-thumb", thumb=True)    # already has thumb -> skipped
    _mk(tmp_path / "no-source", source=False)  # no video -> skipped
    (tmp_path / "loose.txt").write_text("x")   # not a dir -> ignored

    out = meetings_needing_thumbnail(tmp_path)
    assert out == [tmp_path / "needs-it"]


def test_empty_when_dir_missing(tmp_path: Path):
    assert meetings_needing_thumbnail(tmp_path / "nope") == []


import json


def _mk_hls(mdir: Path, *, m3u8=True, thumb=False):
    mdir.mkdir(parents=True)
    if thumb:
        (mdir / "thumbnail.jpg").write_bytes(b"x")
    src = "https://cdn/east/x/manifest.m3u8" if m3u8 else "https://cdn/x/audio.mp3"
    (mdir / "transcript_named.json").write_text(
        json.dumps({"processing_metadata": {"source_audio_url": src}}),
        encoding="utf-8",
    )


def test_includes_hls_source_meetings(tmp_path: Path):
    _mk_hls(tmp_path / "house")                    # HLS, no video, no thumb -> included
    out = meetings_needing_thumbnail(tmp_path)
    assert out == [tmp_path / "house"]


def test_skips_hls_meeting_that_has_thumbnail(tmp_path: Path):
    _mk_hls(tmp_path / "house", thumb=True)        # already has thumb -> skipped
    assert meetings_needing_thumbnail(tmp_path) == []


def test_skips_non_hls_audio_only_meeting(tmp_path: Path):
    _mk_hls(tmp_path / "podcast", m3u8=False)      # audio.mp3 source, no video -> skipped
    assert meetings_needing_thumbnail(tmp_path) == []


def test_ignores_malformed_transcript(tmp_path: Path):
    mdir = tmp_path / "broken"
    mdir.mkdir(parents=True)
    (mdir / "transcript_named.json").write_text("[1, 2, 3]", encoding="utf-8")  # top-level list
    assert meetings_needing_thumbnail(tmp_path) == []  # no crash, just excluded


def test_backfill_persists_thumbnail_url(tmp_path, monkeypatch):
    import backfill_thumbnails as b

    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    mid = "2026-07-16-house-floor"
    mdir = tmp_path / mid
    mdir.mkdir(parents=True)
    meeting = {
        "meeting_id": mid, "city": None, "date": "2026-07-16",
        "meeting_type": "House Floor", "event_kind": "floor",
        "audio_source": "https://live.house.gov/?date=2026-07-16",
        "duration_seconds": 12240.0, "segments": [], "speakers": {},
        "processing_metadata": {"source_audio_url": "https://cdn/east/x/manifest.m3u8"},
        "event_orgs": [],
    }
    (mdir / "transcript_named.json").write_text(json.dumps(meeting), encoding="utf-8")

    # stub attach_thumbnail: set thumbnail_url + create the local jpg, no ffmpeg/upload
    def fake_attach(m, d):
        m.thumbnail_url = "https://bucket/thumb.jpg"
        (Path(d) / "thumbnail.jpg").write_bytes(b"x")
    monkeypatch.setattr(b, "attach_thumbnail", fake_attach)

    b.backfill()

    saved = json.loads((mdir / "transcript_named.json").read_text(encoding="utf-8"))
    assert saved["thumbnail_url"] == "https://bucket/thumb.jpg"
