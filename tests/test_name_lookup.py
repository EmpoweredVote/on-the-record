from __future__ import annotations

from src.name_lookup import Lookup, name_on_page, norm_name, page_text, verify_on_page

HTML = """<html><head><title>Staff</title><script>var x = "Aaron Spiegel";</script></head>
<body><h1>Our Team</h1><p>Rabbi <b>Aaron&nbsp;Spiegel</b>, Executive Director</p></body></html>"""


def test_page_text_drops_scripts_and_joins_inline_tags():
    t = page_text(HTML)
    assert "var x" not in t and "Aaron Spiegel" in t.replace("\xa0", " ")


def test_name_on_page_is_case_space_and_punctuation_insensitive():
    assert name_on_page("aaron spiegel", page_text(HTML))
    assert name_on_page("Aaron  Spiegel", "Rabbi AARON SPIEGEL, director")
    assert not name_on_page("Aaron Spiegelman", "Rabbi Aaron Spiegel")
    assert not name_on_page("Ann Lee", "Joann Leeds")


def test_norm_name():
    assert norm_name("  O'Brien-Smith, Jr. ") == "o'brien smith jr"


def test_verify_on_page_ok_and_reasons():
    assert verify_on_page("Aaron Spiegel", "https://x.org/team", fetch=lambda u: HTML) == (True, None)
    ok, why = verify_on_page("Rachel Sample", "https://x.org/team", fetch=lambda u: HTML)
    assert not ok and why == "name not on page"

    def boom(u):
        raise RuntimeError("403")

    ok, why = verify_on_page("Aaron Spiegel", "https://x.org/team", fetch=boom)
    assert not ok and why.startswith("fetch failed")
    assert verify_on_page("Aaron Spiegel", "notaurl", fetch=lambda u: HTML) == (False, "bad url")


def test_lookup_to_dict_hides_url_when_unverified():
    d = Lookup(name="Ann Lee", source="web", verified=False, url="https://x", reason="name not on page").to_dict()
    assert d["url"] is None and d["reason"] == "name not on page"
    d2 = Lookup(name="Ann Lee", source="web", verified=True, url="https://x").to_dict()
    assert d2["url"] == "https://x"
