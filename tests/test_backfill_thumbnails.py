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
