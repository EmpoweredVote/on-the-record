from src.models import Meeting


def test_thumbnail_url_round_trips():
    m = Meeting(meeting_id="2026-02-18-regular", city="Asheville", date="2026-02-18")
    m.thumbnail_url = "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/2026-02-18-regular.jpg"
    restored = Meeting.from_dict(m.to_dict())
    assert restored.thumbnail_url == m.thumbnail_url


def test_thumbnail_url_defaults_none():
    m = Meeting(meeting_id="x", city=None, date="2026-01-01")
    assert m.thumbnail_url is None
    assert Meeting.from_dict(m.to_dict()).thumbnail_url is None
