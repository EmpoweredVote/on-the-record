import sys
import types

import pytest

from src.discovery import search
from src.discovery.models import RawItem
from src.discovery.search import with_backoff


class _FakeYDL:
    captured_opts = None
    captured_query = None
    result = {"entries": []}

    def __init__(self, opts):
        _FakeYDL.captured_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, query, download=False):
        _FakeYDL.captured_query = query
        return dict(_FakeYDL.result)


def _install(monkeypatch, result):
    _FakeYDL.result = result
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = _FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)


def test_ytsearch_builds_flat_query_with_node_runtime(monkeypatch):
    _install(monkeypatch, {"entries": []})
    search.ytsearch('"Maria Delgado" debate', limit=10)
    assert _FakeYDL.captured_query == 'ytsearch10:"Maria Delgado" debate'
    assert _FakeYDL.captured_opts.get("extract_flat") == "in_playlist"
    assert _FakeYDL.captured_opts.get("js_runtimes") == {"node": {}}


def test_ytsearch_maps_entries_and_skips_blank(monkeypatch):
    _install(monkeypatch, {"entries": [
        {"id": "abc12345678", "url": "https://www.youtube.com/watch?v=abc12345678",
         "title": "Full debate", "channel": "KXAN", "duration": 3300},
        {},  # malformed entry
    ]})
    items = search.ytsearch("q", limit=5)
    assert len(items) == 1
    assert items[0].url == "https://www.youtube.com/watch?v=abc12345678"
    assert items[0].channel_name == "KXAN"
    assert items[0].duration_seconds == 3300
    assert items[0].via == "search"


def test_ytsearch_propagates_extractor_errors(monkeypatch):
    class _Boom(_FakeYDL):
        def extract_info(self, query, download=False):
            raise RuntimeError("bot check")

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = _Boom
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    with pytest.raises(RuntimeError):
        search.ytsearch("q", limit=5)


def test_hydrate_fills_only_missing_fields(monkeypatch):
    monkeypatch.setattr(search, "fetch_source_metadata", lambda url: {
        "title": "Hydrated title", "channel": "KXAN", "upload_date": "2026-08-01",
        "duration": 3300, "chapters": [], "description": "All four candidates.",
        "channel_id": "UCk", "channel_url": "https://www.youtube.com/channel/UCk",
    })
    item = RawItem(url="https://www.youtube.com/watch?v=abc12345678",
                   title="Full debate", via="search")
    out = search.hydrate_item(item)
    assert out.title == "Full debate"            # existing value kept
    assert out.description == "All four candidates."
    assert out.duration_seconds == 3300
    assert out.published_at == "2026-08-01"
    assert out.channel_id == "UCk"


def test_queries_for_candidate():
    qs = search.queries_for_candidate("Maria Delgado")
    assert '"Maria Delgado" debate' in qs
    assert '"Maria Delgado" town hall' in qs
    assert len(qs) == 4


def test_hydrate_casts_float_duration_to_int(monkeypatch):
    monkeypatch.setattr(search, "fetch_source_metadata", lambda url: {
        "title": None, "channel": None, "upload_date": None, "duration": 3300.0,
        "chapters": [], "description": None, "channel_id": None, "channel_url": None,
    })
    item = RawItem(url="https://www.youtube.com/watch?v=abc12345678", via="search")
    out = search.hydrate_item(item)
    assert out.duration_seconds == 3300 and isinstance(out.duration_seconds, int)


def test_ytsearch_skips_channel_and_playlist_entries(monkeypatch):
    _install(monkeypatch, {"entries": [
        {"id": "UCkxan0000000000000000", "url": "https://www.youtube.com/channel/UCkxan0000000000000000",
         "title": "KXAN", "channel": "KXAN"},
        {"id": "PLxyz", "url": "https://www.youtube.com/playlist?list=PLxyz", "title": "Debates"},
        {"id": "abc12345678", "url": "https://www.youtube.com/watch?v=abc12345678",
         "title": "Full debate", "channel": "KXAN", "duration": 3300},
    ]})
    items = search.ytsearch("q", limit=5)
    assert [i.url for i in items] == ["https://www.youtube.com/watch?v=abc12345678"]


def _flaky(fail_times, exc):
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise exc
        return "ok"
    return fn, calls


def test_backoff_retries_bot_check_and_succeeds():
    fn, calls = _flaky(2, RuntimeError("Sign in to confirm you're not a bot"))
    sleeps = []
    assert with_backoff(fn, sleep_fn=sleeps.append) == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2
    # invariant: attempt0 jitter ceiling base*3*0.5 == base*1.5 == attempt1 floor
    # -- exclusive, so sleeps[1] > sleeps[0] can't flake; widening jitter past
    # [0.5, 1.5) or dropping the 3x multiplier breaks this.
    assert sleeps[1] > sleeps[0]          # exponential: later waits are longer


def test_backoff_exhaustion_reraises():
    fn, calls = _flaky(99, RuntimeError("HTTP Error 429: Too Many Requests"))
    with pytest.raises(RuntimeError):
        with_backoff(fn, retries=2, sleep_fn=lambda s: None)
    assert calls["n"] == 3                # 1 try + 2 retries


def test_backoff_ignores_non_retryable_errors():
    fn, calls = _flaky(99, ValueError("Unsupported URL: https://x"))
    with pytest.raises(ValueError):
        with_backoff(fn, sleep_fn=lambda s: None)
    assert calls["n"] == 1
