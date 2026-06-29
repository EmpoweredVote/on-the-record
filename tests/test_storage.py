from pathlib import Path

from src.storage import public_url, upload_thumbnail, THUMBNAIL_BUCKET


def test_public_url_joins_path():
    assert public_url("https://x.supabase.co", "meeting-thumbnails", "abc.jpg") == (
        "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/abc.jpg"
    )


def test_public_url_strips_trailing_slash():
    assert public_url("https://x.supabase.co/", THUMBNAIL_BUCKET, "a.jpg") == (
        "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/a.jpg"
    )


def test_upload_noops_without_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    jpg = tmp_path / "thumbnail.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")  # minimal JPEG-ish bytes
    assert upload_thumbnail(jpg, "some-slug") is None


def test_upload_returns_none_when_file_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "key")
    assert upload_thumbnail(tmp_path / "missing.jpg", "slug") is None


def test_upload_happy_path_posts_and_returns_public_url(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "key")
    jpg = tmp_path / "thumbnail.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")

    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["headers"] = kwargs.get("headers")
        return FakeResp()

    monkeypatch.setattr("src.storage.requests.post", fake_post)
    result = upload_thumbnail(jpg, "my-slug")
    assert result == (
        "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/my-slug.jpg"
    )
    assert calls["url"] == (
        "https://x.supabase.co/storage/v1/object/meeting-thumbnails/my-slug.jpg"
    )
    assert calls["headers"]["x-upsert"] == "true"
    assert calls["headers"]["Content-Type"] == "image/jpeg"
    assert calls["headers"]["Authorization"] == "Bearer key"
