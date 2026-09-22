from src.discovery import feeds


def test_extract_body_text_prefers_longest_article(monkeypatch):
    html = "<html><body><nav>menu menu menu</nav><article>" + ("Real body. " * 40) + "</article></body></html>"
    out = feeds._extract_body_text(html, max_chars=6000)
    assert "Real body." in out and "menu" not in out


def test_fetch_page_text_default_path_unchanged(monkeypatch):
    html = "<html><body><article>" + ("Hello world. " * 40) + "</article></body></html>"
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_polite_pause", lambda *a, **k: None)
    monkeypatch.setattr(feeds, "_fetch_page_bytes",
                        lambda url, **k: ("text/html", html.encode()))
    out = feeds.fetch_page_text("https://example.com/x", max_chars=6000)
    assert "Hello world." in out


def _stub_common(monkeypatch):
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_polite_pause", lambda *a, **k: None)

_RENDERED = "<html><body><article>" + ("Rendered body sentence. " * 40) + "</article></body></html>"

def test_render_used_when_plain_raises(monkeypatch):
    _stub_common(monkeypatch)
    def boom(url, **k): raise ConnectionError("dropped")
    monkeypatch.setattr(feeds, "_fetch_page_bytes", boom)
    out = feeds.fetch_page_text("https://x/y", max_chars=6000,
                                render_fallback=True, renderer=lambda u: _RENDERED)
    assert "Rendered body sentence." in out

def test_render_used_when_plain_short(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(feeds, "_fetch_page_bytes",
                        lambda url, **k: ("text/html", b"<html><body>hi</body></html>"))
    out = feeds.fetch_page_text("https://x/y", max_chars=6000,
                                render_fallback=True, renderer=lambda u: _RENDERED)
    assert "Rendered body sentence." in out

def test_render_skipped_when_plain_good(monkeypatch):
    _stub_common(monkeypatch)
    good = "<html><body><article>" + ("Plain good body. " * 40) + "</article></body></html>"
    monkeypatch.setattr(feeds, "_fetch_page_bytes",
                        lambda url, **k: ("text/html", good.encode()))
    called = {"n": 0}
    def r(u): called["n"] += 1; return _RENDERED
    out = feeds.fetch_page_text("https://x/y", max_chars=6000,
                                render_fallback=True, renderer=r)
    assert "Plain good body." in out and called["n"] == 0

def test_render_off_preserves_raise(monkeypatch):
    _stub_common(monkeypatch)
    def boom(url, **k): raise ConnectionError("dropped")
    monkeypatch.setattr(feeds, "_fetch_page_bytes", boom)
    import pytest
    with pytest.raises(ConnectionError):
        feeds.fetch_page_text("https://x/y", max_chars=6000)  # render_fallback defaults False

def test_render_robots_denial_still_blocks(monkeypatch):
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: False)
    called = {"n": 0}
    def r(u): called["n"] += 1; return _RENDERED
    out = feeds.fetch_page_text("https://x/y", render_fallback=True, renderer=r)
    assert out == "" and called["n"] == 0
