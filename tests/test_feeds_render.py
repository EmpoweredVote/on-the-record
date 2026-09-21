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
