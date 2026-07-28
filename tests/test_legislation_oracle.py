"""Tests for the Bloomington legislation-page oracle.

Fixture provenance (captured live 2026-07-28):
- ordinance_2026-14.html: /council/legislation/Ordinance/2026/2026-14 -> 200,
  carries the real final-action row
  ``<tr><th>Final</th><td>2026-06-10</td><td>pass</td><td>7-2 (Asare, Rosenbarger)</td></tr>``.
- ordinance_2026-12.html / resolution_2026-13.html: both URLs 404'd (the
  site publishes a legislation page only after final disposition), so these
  are the real "Page not found" bodies — the pending case.
"""
from pathlib import Path

import pytest

from src.legislation_oracle import FinalAction, build_legislation_url, fetch_final_action

FIX = Path(__file__).parent / "fixtures" / "legislation"


def _fixture_fetch(name):
    def fetch(url: str) -> str:
        return (FIX / name).read_text()

    return fetch


# ---------------------------------------------------------------------------
# Real-fixture parse
# ---------------------------------------------------------------------------


def test_real_ordinance_page_parses_final_action():
    action = fetch_final_action("Ordinance 2026-14", fetch=_fixture_fetch("ordinance_2026-14.html"))
    assert action == FinalAction(
        action_date="2026-06-10",
        outcome="passed",
        tally="7-2 (Asare, Rosenbarger)",
    )


def test_pending_page_returns_none():
    # The captured 404 bodies have no Final row -> pending -> None.
    assert fetch_final_action("Ordinance 2026-12", fetch=_fixture_fetch("ordinance_2026-12.html")) is None
    assert fetch_final_action("Resolution 2026-13", fetch=_fixture_fetch("resolution_2026-13.html")) is None


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


def test_url_construction_ordinance():
    assert (
        build_legislation_url("Ordinance 2026-14")
        == "https://bloomington.in.gov/council/legislation/Ordinance/2026/2026-14"
    )


def test_url_construction_resolution():
    assert (
        build_legislation_url("Resolution 2026-13")
        == "https://bloomington.in.gov/council/legislation/Resolution/2026/2026-13"
    )


def test_url_construction_appropriation_ordinance_and_zero_padding():
    # The landing page lists Appropriation%20Ordinance as its own type path,
    # and item pages zero-pad the number (2026-01 is 200, 2026-1 is 404).
    assert (
        build_legislation_url("Appropriation Ordinance 2026-3")
        == "https://bloomington.in.gov/council/legislation/Appropriation%20Ordinance/2026/2026-03"
    )


def test_unknown_type_returns_none_without_fetching():
    def explode(url):  # pragma: no cover - must not be called
        raise AssertionError("fetch must not be called for an unknown type")

    assert build_legislation_url("Memorandum 2026-1") is None
    assert fetch_final_action("Memorandum 2026-1", fetch=explode) is None
    assert build_legislation_url("garbage") is None
    assert fetch_final_action("garbage", fetch=explode) is None


def test_fetch_receives_constructed_url():
    seen = {}

    def capture(url):
        seen["url"] = url
        return (FIX / "ordinance_2026-14.html").read_text()

    fetch_final_action("Ordinance 2026-14", fetch=capture)
    assert seen["url"] == "https://bloomington.in.gov/council/legislation/Ordinance/2026/2026-14"


# ---------------------------------------------------------------------------
# Outcome vocabulary + robustness
# ---------------------------------------------------------------------------


def _page(date="2026-06-10", outcome="pass", tally="7-2"):
    return (
        "<html><body><section>"
        "<table><tr><th>Amends Code</th><td>Yes</td></tr></table>"
        f"<table><tr><th>Final</th><td>{date}</td><td>{outcome}</td><td>{tally}</td></tr></table>"
        "</section></body></html>"
    )


@pytest.mark.parametrize(
    "word,expected",
    [
        ("pass", "passed"),
        ("passed", "passed"),
        ("adopted", "passed"),
        ("fail", "failed"),
        ("failed", "failed"),
        ("rejected", "failed"),
        ("defeated", "failed"),
        ("postponed", "continued"),
        ("continued", "continued"),
        ("withdrawn", "pulled"),
    ],
)
def test_outcome_vocabulary_mapping(word, expected):
    action = fetch_final_action("Ordinance 2026-14", fetch=lambda u: _page(outcome=word))
    assert action is not None
    assert action.outcome == expected


def test_unrecognized_outcome_word_returns_none():
    assert fetch_final_action("Ordinance 2026-14", fetch=lambda u: _page(outcome="tabled??")) is None


def test_final_row_without_tally_cell():
    html = (
        "<html><body><table><tr><th>Final</th>"
        "<td>2026-06-10</td><td>pass</td></tr></table></body></html>"
    )
    action = fetch_final_action("Ordinance 2026-14", fetch=lambda u: html)
    assert action == FinalAction(action_date="2026-06-10", outcome="passed", tally=None)


def test_fetch_error_returns_none():
    def boom(url):
        raise OSError("HTTP 404")

    assert fetch_final_action("Ordinance 2026-12", fetch=boom) is None


def test_malformed_html_returns_none():
    assert fetch_final_action("Ordinance 2026-14", fetch=lambda u: "<tr><th>Final</th>") is None
    assert fetch_final_action("Ordinance 2026-14", fetch=lambda u: "not html at all") is None
    assert fetch_final_action("Ordinance 2026-14", fetch=lambda u: "") is None
