from src.discovery.web_search import tavily_search


def test_tavily_search_unset_key_returns_empty(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert tavily_search("anything") == []


def test_tavily_search_http_error_returns_empty(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "dummy-key")

    class _BoomResponse:
        def raise_for_status(self):
            raise RuntimeError("500 server error")

    def _fake_post(*args, **kwargs):
        return _BoomResponse()

    monkeypatch.setattr("src.discovery.web_search.requests.post", _fake_post)

    assert tavily_search("anything") == []


def test_tavily_search_post_itself_raising_returns_empty(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "dummy-key")

    def _fake_post(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr("src.discovery.web_search.requests.post", _fake_post)

    assert tavily_search("anything") == []


def test_tavily_search_success_maps_and_clamps(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "dummy-key")
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": "T", "url": "U", "content": "x" * 999}]}

    def _fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("src.discovery.web_search.requests.post", _fake_post)

    long_query = "q " * 500  # far over 380 chars
    result = tavily_search(long_query, max_results=3)

    assert result == [{"title": "T", "url": "U", "content": "x" * 500}]
    assert captured["json"]["query"] == long_query.strip()[:380]
    assert len(captured["json"]["query"]) <= 380
    assert captured["json"]["max_results"] == 3
    assert captured["json"]["api_key"] == "dummy-key"
    assert captured["timeout"] == 20
