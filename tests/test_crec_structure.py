from pathlib import Path
from src.crec_structure import CrecGranule, parse_granule_records, fetch_granules

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def test_parse_granule_records_keeps_class_and_title():
    page = (FIX / "granules_page1.json").read_text()
    recs = parse_granule_records(page, "house")
    assert recs == [("CREC-2018-10-10-pt1-PgH1-1", "HOUSE", "MORNING-HOUR DEBATE")]


def test_fetch_granules_builds_units_with_text():
    # Inline single-page list (NO nextPage) so pagination terminates.
    list_json = (
        '{"granules": [{"granuleClass": "HOUSE", '
        '"granuleId": "CREC-2018-10-10-pt1-PgH1-1", "title": "MORNING-HOUR DEBATE"}]}'
    )
    body = "<html><body><pre>Mr. SMITH. I yield.</pre></body></html>"

    def fake_fetch(url: str) -> str:
        if "/granules?" in url:
            return list_json
        return body

    gs = fetch_granules("2018-10-10", "house", fetch=fake_fetch, api_key="k")
    assert len(gs) == 1
    g = gs[0]
    assert isinstance(g, CrecGranule)
    assert g.granule_id == "CREC-2018-10-10-pt1-PgH1-1"
    assert g.granule_class == "HOUSE"
    assert g.title == "MORNING-HOUR DEBATE"
    assert g.text.strip() == "Mr. SMITH. I yield."
