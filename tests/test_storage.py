from pathlib import Path

from src.storage import public_url, upload_thumbnail, THUMBNAIL_BUCKET


def test_public_url_joins_path():
    assert public_url("https://x.supabase.co", "meeting-thumbnails", "abc.jpg") == (
        "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/abc.jpg"
    )


def test_public_url_strips_trailing_slash():
    assert public_url("https://x.supabase.co/", THUMBNAIL_BUCKET, "a.jpg").startswith(
        "https://x.supabase.co/storage/v1/object/public/"
    )


def test_upload_noops_without_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    jpg = tmp_path / "thumbnail.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")  # minimal JPEG-ish bytes
    assert upload_thumbnail(jpg, "some-slug") is None
