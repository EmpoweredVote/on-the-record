from __future__ import annotations

from src.source_key import source_key


def test_youtube_shapes_converge():
    k = "youtube:dQw4w9WgXcQ"
    assert source_key("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == k
    assert source_key("https://youtu.be/dQw4w9WgXcQ") == k
    assert source_key("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=90s") == k
    assert source_key("https://youtube.com/watch?v=dQw4w9WgXcQ&feature=share") == k
    assert source_key("https://www.youtube.com/shorts/dQw4w9WgXcQ") == k


def test_different_youtube_ids_differ():
    assert source_key("https://youtu.be/aaaaaaaaaaa") != source_key("https://youtu.be/bbbbbbbbbbb")


def test_generic_url_normalized():
    # host lowercased, trailing slash + fragment dropped, tracking params removed
    a = source_key("https://CATSTV.blob.core.windows.net/videoarchive/2026/foo.mp4")
    assert a == "url:catstv.blob.core.windows.net/videoarchive/2026/foo.mp4"
    assert source_key("https://ex.com/v/?utm_source=x#frag") == "url:ex.com/v"


def test_local_file_absolute():
    assert source_key("/tmp/meeting.mp4") == "file:/tmp/meeting.mp4"
    assert source_key("file:///tmp/meeting.mp4") == "file:/tmp/meeting.mp4"


def test_empty_is_empty():
    assert source_key("") == ""
    assert source_key("   ") == ""


def test_source_key_is_total_on_malformed_url():
    # urlparse raises ValueError on malformed IPv6 brackets; must not propagate.
    assert source_key("https://[bad") == "file:" + __import__("os").path.abspath("https://[bad")
    # a few more junk inputs must not raise:
    for junk in ("http://[::1", "https://exa mple.com/x", "://nope", "ht!tp://x"):
        assert isinstance(source_key(junk), str)


def test_source_key_stable_for_episode_page_with_tracking_params():
    a = source_key("https://show.buzzsprout.com/1414123/ep-1-housing")
    b = source_key("https://show.buzzsprout.com/1414123/ep-1-housing/?utm_source=x&si=y")
    assert a == b
    assert a.startswith("url:show.buzzsprout.com/1414123/ep-1-housing")


def test_source_key_distinct_for_different_episodes():
    a = source_key("https://www.ipm.org/show/askthemayor/2026-07-15/a")
    b = source_key("https://www.ipm.org/show/askthemayor/2026-07-15/b")
    assert a != b
