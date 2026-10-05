from __future__ import annotations

import json
import subprocess

import pytest

from src.name_lookup import (
    Lookup, RESEARCH_SCHEMA, ResearchCache, ResearcherUnavailable, build_prompt, name_on_page,
    norm_name, page_text, research, research_command, should_research, verify_on_page,
)

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


def test_verify_never_raises():
    assert verify_on_page("Aaron Spiegel", "http://[bad", fetch=lambda u: HTML) == (False, "bad url")
    ok, why = verify_on_page("Aaron Spiegel", "https://x.org", fetch=lambda u: None)
    assert not ok and why.startswith("verify failed")
    ok, why = verify_on_page(None, "https://x.org", fetch=lambda u: HTML)
    assert not ok and why.startswith("verify failed")


def test_bytes_html_decodes_utf8_via_meta():
    raw = b'<meta charset="utf-8"><p>Jos\xc3\xa9 Ram\xc3\xadrez</p>'
    t = page_text(raw)
    assert "Jos\u00e9 Ram\u00edrez" in t
    assert name_on_page("Jos\u00e9 Ram\u00edrez", t)
    assert verify_on_page("Jos\u00e9 Ram\u00edrez", "https://x.org", fetch=lambda u: raw) == (True, None)


def test_unicode_nfc_both_directions():
    decomposed = "Jose\u0301"
    composed = "Jos\u00e9"
    assert name_on_page(composed, "Dr. " + decomposed + " Lee")
    assert name_on_page(decomposed, "Dr. " + composed + " Lee")


def test_possessive_and_quoted_names():
    assert name_on_page("Aaron Spiegel", "Aaron Spiegel\u2019s remarks")
    assert name_on_page("Aaron Spiegel", "Aaron Spiegel's remarks")
    assert name_on_page("Aaron Spiegel", "said \u2018Aaron Spiegel\u2019 today")
    assert not name_on_page("Aaron Spiegel", "Aaron Spiegelman's remarks")


def runner_returning(payload, rc=0):
    calls = []

    def run(cmd, timeout):
        calls.append((cmd, timeout))
        return rc, json.dumps(payload), ""

    run.calls = calls
    return run


def test_prompt_contains_only_name_title_affiliation_place():
    p = build_prompt("Aaron Spiegel", "Rabbi", "Indy Multi-Faith", "Indianapolis, IN")
    assert "Aaron Spiegel" in p and "Rabbi" in p and "Indy Multi-Faith" in p and "Indianapolis, IN" in p
    assert "found=false" in p.lower() or "found false" in p.lower()


def test_command_flags():
    cmd = research_command("PROMPT")
    assert cmd[:3] == ["claude", "-p", "PROMPT"]
    for flag in ("--output-format", "--json-schema", "--allowedTools", "--max-turns",
                 "--no-session-persistence", "--strict-mcp-config", "--model"):
        assert flag in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == RESEARCH_SCHEMA


def test_research_returns_structured_output():
    run = runner_returning({"type": "result", "is_error": False, "structured_output":
                            {"found": True, "name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance",
                             "url": "https://example.org/team"}})
    out = research("Aaron Spiegel", "Rabbi", "Indy multi-faith", "Indianapolis, IN", runner=run)
    assert out == {"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith Alliance", "url": "https://example.org/team"}
    assert run.calls[0][1] == 120


def test_research_not_found_and_bad_output_return_none():
    assert research("A B", None, "X", None, runner=runner_returning(
        {"is_error": False, "structured_output": {"found": False}})) is None
    assert research("A B", None, "X", None, runner=lambda c, t: (0, "not json", "")) is None
    assert research("A B", None, "X", None, runner=runner_returning(
        {"is_error": False, "structured_output": {"found": True, "name": "", "url": "x"}})) is None


def test_research_auth_and_limit_errors_raise_unavailable():
    for msg in ("Failed to authenticate. API Error: 401 OAuth access token has expired.",
                "Claude usage limit reached. Your limit will reset at 5pm."):
        with pytest.raises(ResearcherUnavailable):
            research("A B", None, "X", None, runner=runner_returning({"is_error": True, "result": msg}, rc=1))


def test_research_missing_cli_raises_unavailable_and_timeout_returns_none():
    def missing(cmd, timeout):
        raise FileNotFoundError("claude")

    with pytest.raises(ResearcherUnavailable):
        research("A B", None, "X", None, runner=missing)

    def slow(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    assert research("A B", None, "X", None, runner=slow) is None


def test_should_research():
    assert should_research(titled=False, partial=False, affiliation=None)
    assert should_research(titled=False, partial=True, affiliation="Hoosier Families")
    assert not should_research(titled=False, partial=True, affiliation=None)
    assert not should_research(titled=True, partial=False, affiliation="Senate")


def test_cache_roundtrip_and_stored_miss(tmp_path):
    c = ResearchCache(tmp_path / "cache.json")
    k = c.key("Aaron  Spiegel", "Indy Multi-Faith", "Indianapolis, IN")
    assert c.get(k) is None
    c.put(k, {"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith", "url": "https://x", "extra": "dropped"})
    k2 = c.key("Nobody Here", None, None)
    c.put(k2, None)
    c.save()
    c2 = ResearchCache(tmp_path / "cache.json")
    assert c2.get(k) == {"name": "Aaron Spiegel", "affiliation": "Indy Multi-Faith", "url": "https://x"}
    assert c2.get(k2) == {"found": False}
