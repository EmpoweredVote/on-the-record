# tests/test_govinfo.py
from __future__ import annotations

from pathlib import Path

from src.govinfo import CrecTurn, _package_id, _resolve_api_key, parse_granule_list, _next_offset_mark
from src.govinfo import html_to_text
from src.govinfo import parse_granule_turns

_FIX = Path(__file__).parent / "fixtures" / "govinfo"


def _read(name: str) -> str:
    return (_FIX / name).read_text(encoding="utf-8")


def test_crec_turn_fields():
    t = CrecTurn(speaker_raw="Mr. Cotton", text="The majority leader is recognized.",
                 granule_id="CREC-2018-10-10-pt1-PgS6735-6", order=0)
    assert t.speaker_raw == "Mr. Cotton"
    assert t.text == "The majority leader is recognized."
    assert t.granule_id == "CREC-2018-10-10-pt1-PgS6735-6"
    assert t.order == 0


def test_package_id():
    assert _package_id("2018-10-10") == "CREC-2018-10-10"


def test_resolve_api_key_prefers_arg(monkeypatch):
    monkeypatch.setenv("GOVINFO_API_KEY", "envkey")
    assert _resolve_api_key("argkey") == "argkey"


def test_resolve_api_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GOVINFO_API_KEY", "envkey")
    assert _resolve_api_key(None) == "envkey"


def test_resolve_api_key_falls_back_to_demo(monkeypatch):
    monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
    assert _resolve_api_key(None) == "DEMO_KEY"


def test_parse_granule_list_filters_house():
    ids = parse_granule_list(_read("granules_page1.json"), "house")
    assert ids == ["CREC-2018-10-10-pt1-PgH1-1"]


def test_parse_granule_list_filters_senate():
    ids = parse_granule_list(_read("granules_page1.json"), "senate")
    assert ids == ["CREC-2018-10-10-pt1-PgS6735-6"]


def test_parse_granule_list_is_case_insensitive():
    assert parse_granule_list(_read("granules_page1.json"), "HOUSE") == \
        ["CREC-2018-10-10-pt1-PgH1-1"]


def test_parse_granule_list_excludes_digest_and_extensions():
    ids = parse_granule_list(_read("granules_page1.json"), "house") + \
        parse_granule_list(_read("granules_page1.json"), "senate")
    assert "CREC-2018-10-10-pt1-PgD1124" not in ids
    assert "CREC-2018-10-10-pt1-PgE1-1" not in ids


def test_next_offset_mark_returns_url_then_none():
    assert _next_offset_mark(_read("granules_page1.json")) == \
        "https://api.govinfo.gov/packages/CREC-2018-10-10/granules?offsetMark=PAGE2&pageSize=100"
    assert _next_offset_mark(_read("granules_page2.json")) is None


def test_html_to_text_extracts_pre_and_unescapes():
    text = html_to_text(_read("granule_presiding.htm"))
    assert "RECOGNITION OF THE MAJORITY LEADER" in text
    assert "The PRESIDING OFFICER (Mr. Cotton)." in text
    # the <a> tag around the gpo.gov link is stripped, its text kept
    assert "<a" not in text
    assert "www.gpo.gov" in text
    # header bracket lines survive as text (stripped later by the turn parser)
    assert "[Senate]" in text


def test_parse_granule_turns_single_procedural():
    text = html_to_text(_read("granule_presiding.htm"))
    turns = parse_granule_turns(text, "CREC-2018-10-10-pt1-PgS6735-6", start_order=0)
    assert len(turns) == 1
    assert turns[0].speaker_raw == "The PRESIDING OFFICER (Mr. Cotton)"
    assert turns[0].text == "The majority leader is recognized."
    assert turns[0].granule_id == "CREC-2018-10-10-pt1-PgS6735-6"
    assert turns[0].order == 0


def test_parse_granule_turns_multi_speaker_reflows_continuations():
    text = html_to_text(_read("granule_debate.htm"))
    turns = parse_granule_turns(text, "CREC-2018-10-10-pt1-PgH1-1", start_order=5)
    assert [t.speaker_raw for t in turns] == ["Mr. SMITH of Michigan", "Mr. JONES"]
    # wrapped continuation line is joined into one space-separated string
    assert turns[0].text == (
        "Madam Speaker, I rise today in strong support of this measure, "
        "which will help my constituents."
    )
    assert turns[1].text.startswith("Madam Speaker, I thank the gentleman")
    # start_order offsets the sequence
    assert [t.order for t in turns] == [5, 6]


def test_parse_granule_turns_empty_when_no_designations():
    turns = parse_granule_turns("just some floor boilerplate\nwith no speakers",
                                "CREC-x", start_order=0)
    assert turns == []


def test_parse_granule_turns_appends_multi_paragraph_speech():
    # CREC tags only the FIRST paragraph of a speech; later paragraphs of the
    # SAME speaker are flush-indented with no designation and must append, not drop.
    text = (
        "                          A BILL TO DO THINGS\n\n"
        "  Mr. SMITH of Michigan. Madam Speaker, I rise in support of this measure.\n"
        "  It will help my constituents in countless ways across the district.\n"
        "  Mr. JONES. Madam Speaker, I yield myself such time as I may consume.\n"
    )
    turns = parse_granule_turns(text, "g", start_order=0)
    assert [t.speaker_raw for t in turns] == ["Mr. SMITH of Michigan", "Mr. JONES"]
    assert turns[0].text == (
        "Madam Speaker, I rise in support of this measure. "
        "It will help my constituents in countless ways across the district."
    )
    assert turns[1].text == "Madam Speaker, I yield myself such time as I may consume."


def test_parse_granule_turns_does_not_append_allcaps_heading():
    # An ALL-CAPS section heading (e.g. after a section break) must NOT append to
    # the prior speaker's turn — only lowercase-bearing prose continuations do.
    text = (
        "  Mr. SMITH. I yield back the balance of my time.\n"
        "                          ANOTHER SECTION HEADING\n"
        "  Mr. JONES. I claim the time in opposition.\n"
    )
    turns = parse_granule_turns(text, "g", start_order=0)
    assert [t.speaker_raw for t in turns] == ["Mr. SMITH", "Mr. JONES"]
    assert turns[0].text == "I yield back the balance of my time."


import pytest

from src.govinfo import (
    fetch_congressional_record_turns,
    _granules_url,
    _granule_text_url,
)


def test_granules_url_shape():
    url = _granules_url("CREC-2018-10-10", "*", "KEY")
    assert url == (
        "https://api.govinfo.gov/packages/CREC-2018-10-10/granules"
        "?offsetMark=*&pageSize=100&api_key=KEY"
    )


def test_granule_text_url_shape():
    url = _granule_text_url("CREC-2018-10-10", "CREC-2018-10-10-pt1-PgH1-1", "KEY")
    assert url == (
        "https://api.govinfo.gov/packages/CREC-2018-10-10/granules/"
        "CREC-2018-10-10-pt1-PgH1-1/htm?api_key=KEY"
    )


def _fake_fetch_factory():
    """URL -> fixture text, following the real two-page + htm topology."""
    page1 = _read("granules_page1.json")
    page2 = _read("granules_page2.json")
    debate = _read("granule_debate.htm")

    def fetch(url: str) -> str:
        if "/granules/" in url and url.endswith("/htm?api_key=KEY"):
            return debate  # every granule returns the multi-speaker fixture
        if "offsetMark=PAGE2" in url:
            return page2
        if "/granules?" in url:
            return page1
        raise AssertionError(f"unexpected url {url}")
    return fetch


def test_fetch_house_turns_paginates_and_orders():
    turns = fetch_congressional_record_turns(
        "2018-10-10", "house", fetch=_fake_fetch_factory(), api_key="KEY"
    )
    # page1 has one HOUSE granule, page2 has one -> both fetched (pagination works).
    # Each granule fixture yields 2 turns, so the granule ids repeat per turn;
    # dedup-in-order shows both granules (from both pages) were fetched, in order.
    assert turns is not None
    seen_granule_ids = list(dict.fromkeys(t.granule_id for t in turns))
    assert seen_granule_ids == [
        "CREC-2018-10-10-pt1-PgH1-1", "CREC-2018-10-10-pt1-PgH2-1",
    ]
    # each granule fixture yields 2 turns; order is continuous across granules
    assert [t.order for t in turns] == [0, 1, 2, 3]
    assert turns[0].speaker_raw == "Mr. SMITH of Michigan"


def test_fetch_excludes_other_chamber():
    turns = fetch_congressional_record_turns(
        "2018-10-10", "senate", fetch=_fake_fetch_factory(), api_key="KEY"
    )
    # the SENATE granule also returns the debate fixture (2 turns), House excluded
    assert turns is not None
    assert all(t.granule_id == "CREC-2018-10-10-pt1-PgS6735-6" for t in turns)


def test_fetch_returns_none_on_missing_package():
    def fetch(url: str) -> str:
        raise RuntimeError("404")
    assert fetch_congressional_record_turns(
        "1900-01-01", "house", fetch=fetch, api_key="KEY") is None


def test_fetch_returns_none_when_all_granule_texts_fail():
    page1 = _read("granules_page1.json")
    page2 = _read("granules_page2.json")

    def fetch(url: str) -> str:
        if "/granules/" in url and url.endswith("/htm?api_key=KEY"):
            raise RuntimeError("granule text 500")
        if "offsetMark=PAGE2" in url:
            return page2
        return page1
    assert fetch_congressional_record_turns(
        "2018-10-10", "house", fetch=fetch, api_key="KEY") is None


def test_fetch_max_granules_truncates(capsys):
    turns = fetch_congressional_record_turns(
        "2018-10-10", "house", fetch=_fake_fetch_factory(), api_key="KEY",
        max_granules=1,
    )
    # only the first HOUSE granule is fetched
    assert turns is not None
    assert {t.granule_id for t in turns} == {"CREC-2018-10-10-pt1-PgH1-1"}
    # truncation is logged, never silent
    assert "truncat" in capsys.readouterr().out.lower()


from src.govinfo import format_turns_text


def test_format_turns_text():
    turns = [
        CrecTurn("Mr. SMITH of Michigan", "I rise in support.", "g1", 0),
        CrecTurn("Mr. JONES", "I yield myself time.", "g1", 1),
    ]
    out = format_turns_text(turns)
    assert out == (
        "Mr. SMITH of Michigan: I rise in support.\n\n"
        "Mr. JONES: I yield myself time."
    )


def test_fetch_partial_when_later_page_fails_is_logged(capsys):
    page1 = _read("granules_page1.json")  # has PgH1-1 (HOUSE) + nextPage -> PAGE2
    debate = _read("granule_debate.htm")

    def fetch(url: str) -> str:
        if "/granules/" in url and url.endswith("/htm?api_key=KEY"):
            return debate
        if "offsetMark=PAGE2" in url:
            raise RuntimeError("page 2 fetch failed")
        if "/granules?" in url:
            return page1
        raise AssertionError(f"unexpected url {url}")

    turns = fetch_congressional_record_turns(
        "2018-10-10", "house", fetch=fetch, api_key="KEY")
    # page-1 HOUSE granule still processed (partial, not None)
    assert turns is not None
    assert {t.granule_id for t in turns} == {"CREC-2018-10-10-pt1-PgH1-1"}
    # the partial-pagination warning was logged, not silent
    out = capsys.readouterr().out.lower()
    assert "partial" in out


def test_parse_granule_list_falls_back_to_docclass():
    payload = (
        '{"granules": ['
        '{"granuleId": "CREC-x-PgH9-1", "docClass": "HOUSE", "title": "OLD PAYLOAD"}'
        ']}'
    )
    assert parse_granule_list(payload, "house") == ["CREC-x-PgH9-1"]


def test_html_to_text_falls_back_without_pre():
    assert html_to_text("<html><body>Plain body text</body></html>") == "Plain body text"


def test_parse_granule_turns_rejects_non_role_the_sentences():
    # Real false positives observed against live CREC data (2018-10-10 Senate):
    # prose paragraphs beginning "The <Capitalized clause>." are NOT presiding-role
    # designations and must not be parsed as speaker turns.
    text = (
        "  The House was not in session today. Its next meeting will be Friday.\n"
        "  The Trump administration has expanded junk insurance plans. It did so.\n"
        "  The Brandon Road project is integral. It protects the Great Lakes.\n"
        "  The Army Corps was also able to use the program. It restored habitat.\n"
    )
    assert parse_granule_turns(text, "g", start_order=0) == []


def test_parse_granule_turns_matches_presiding_roles():
    # Genuine presiding-role designations (incl. parenthetical and pro-tempore /
    # acting variants) must still parse.
    text = (
        "  The PRESIDING OFFICER. The clerk will report.\n"
        "  The PRESIDING OFFICER (Mr. Sullivan). Without objection.\n"
        "  The ACTING PRESIDENT pro tempore. The Senator is recognized.\n"
    )
    turns = parse_granule_turns(text, "g", start_order=0)
    assert [t.speaker_raw for t in turns] == [
        "The PRESIDING OFFICER",
        "The PRESIDING OFFICER (Mr. Sullivan)",
        "The ACTING PRESIDENT pro tempore",
    ]


def test_fetch_appends_api_key_to_nextpage_url():
    # GovInfo's `nextPage` URL omits the api_key; following it verbatim returns
    # 401 and truncates every CREC day to its first page. The fetcher must append
    # the key so pagination continues. This fetch rejects any URL missing it.
    page1 = _read("granules_page1.json")   # nextPage -> ...offsetMark=PAGE2... (no api_key)
    page2 = _read("granules_page2.json")   # contains HOUSE granule PgH2-1

    def fetch(url: str) -> str:
        if "api_key=KEY" not in url:
            raise RuntimeError("401 Unauthorized (missing api_key)")
        if "/granules/" in url and url.endswith("/htm?api_key=KEY"):
            return _read("granule_debate.htm")
        if "offsetMark=PAGE2" in url:
            return page2
        if "/granules?" in url:
            return page1
        raise AssertionError(f"unexpected url {url}")

    turns = fetch_congressional_record_turns(
        "2018-10-10", "house", fetch=fetch, api_key="KEY")
    # page 2's HOUSE granule is only reached if the nextPage fetch carried the key
    assert turns is not None
    assert any(t.granule_id == "CREC-2018-10-10-pt1-PgH2-1" for t in turns)
