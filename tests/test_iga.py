"""IGA (iga.in.gov) resolver + roster builder, against trimmed real API responses."""
from __future__ import annotations

from pathlib import Path

import pytest

from src import iga
from src.iga_roster import Legislator, build_roster, collect_legislators, pick_politician
from src.resolve import SourceSelectionRequired, resolve_source

FIX = Path(__file__).parent / "fixtures" / "iga"
API = "https://iga.in.gov/api"
COMMITTEE_ID = "54e74b71-7d9a-4031-9a97-df093348e7c9"
MEETING_ID = "8562da70-8f8e-4345-bed8-c93dc135d0e9"
MEDIA = ("https://iga.in.gov/video/124/2026/committees/senate/standing/committee_judiciary_4200/"
         "media/e3d3d7a6-b420-40b5-896d-46771d09861f/Judiciary_1_14_2026_1.mp4/")
MASTER = MEDIA + "Judiciary_1_14_2026_1.m3u8"
PAGE = "https://iga.in.gov/session/2026/video/committee_judiciary_4200/"
JAN14 = PAGE + "?video=Judiciary_1_14_2026_1"

ROUTES = {
    f"{API}/getVideos?session_lpid=session_2026&video_key=committee_judiciary_4200": "videos_judiciary.json",
    f"{API}/getVideos?session_lpid=session_2026&video_key=senate": "videos_senate.json",
    f"{API}/getCommittees?session_lpid=session_2026": "committees.json",
    f"{API}/getMeetings?committee_id={COMMITTEE_ID}&session_lpid=session_2026": "meetings_judiciary.json",
    f"{API}/getMeetingDetails?meeting_id={MEETING_ID}&session_lpid=session_2026": "meeting_details_2026_01_14.json",
    f"{API}/getMembers?committee_id={COMMITTEE_ID}&session_lpid=session_2026": "members_judiciary.json",
    f"{API}/getLegislators?session_lpid=session_2026": "legislators.json",
    MASTER: "master.m3u8",
    MEDIA + "Judiciary_1_14_2026_1_captions.m3u8": "captions.m3u8",
    MEDIA + "Judiciary_1_14_2026_1_captions.vtt": "captions_head.vtt",
}


def fake_fetch(url: str) -> str:
    if url not in ROUTES:
        raise AssertionError(f"unexpected fetch: {url}")
    return (FIX / ROUTES[url]).read_text(encoding="utf-8")


# --- URL parsing -------------------------------------------------------------

def test_parse_page_url_with_and_without_selection():
    assert iga.parse_page_url(PAGE) == ("2026", "committee_judiciary_4200", None)
    assert iga.parse_page_url(JAN14) == ("2026", "committee_judiciary_4200", "Judiciary_1_14_2026_1")
    assert iga.parse_page_url("https://iga.in.gov/session/2026/video/senate") == ("2026", "senate", None)


def test_parse_page_url_rejects_other_pages():
    assert iga.parse_page_url("https://iga.in.gov/session/2026/video/livestreams") is None
    assert iga.parse_page_url("https://iga.in.gov/2026/committees/senate/judiciary") is None
    assert iga.parse_page_url("https://example.com/session/2026/video/senate") is None


def test_parse_stream_url_committee_and_floor():
    facts = iga.parse_stream_url(MASTER)
    assert facts == {"year": "2026", "video_key": "committee_judiciary_4200", "chamber": "senate",
                     "media": "e3d3d7a6-b420-40b5-896d-46771d09861f", "stem": "Judiciary_1_14_2026_1"}
    floor = iga.parse_stream_url("https://iga.in.gov/video/124/2026/senate/media/video_senate_2026_day_32/"
                                 "Session_2_27_2026_4.mp4/Session_2_27_2026_4.m3u8")
    assert floor["video_key"] == "senate" and floor["chamber"] == "senate"
    assert floor["stem"] == "Session_2_27_2026_4"


def test_captions_url_from_master_is_absolute():
    master = (FIX / "master.m3u8").read_text()
    assert iga.captions_url_from_master(master, MASTER) == MEDIA + "Judiciary_1_14_2026_1_captions.m3u8"


# --- resolver ----------------------------------------------------------------

def test_listing_page_requires_selection_with_choices():
    with pytest.raises(SourceSelectionRequired) as exc:
        resolve_source(PAGE, fetch=fake_fetch)
    urls = [c["url"] for c in exc.value.choices]
    assert JAN14 in urls
    assert any("(2026-01-14)" in c["label"] for c in exc.value.choices)


def test_selected_committee_recording_resolves_full_metadata():
    r = resolve_source(JAN14, fetch=fake_fetch)
    assert r.resolver == "iga"
    assert r.audio_url == MASTER
    assert r.title == "Indiana Senate Judiciary Committee — Jan. 14, 2026"
    assert r.date == "2026-01-14"
    assert r.outlet == "Indiana General Assembly"
    assert r.page_url == JAN14
    assert "SB 285: Housing matters. (Authors: Carrasco, Koch)" in r.description
    assert r.captions_vtt.startswith("WEBVTT")
    assert r.transcript is None  # CART captions are never a reconcile reference


def test_direct_stream_url_resolves_same_recording():
    r = resolve_source(MASTER, fetch=fake_fetch)
    assert r.page_url == JAN14 and r.date == "2026-01-14"


def test_unknown_selection_does_not_fall_through_silently():
    # A bad ?video= is a resolver error; resolve_source swallows it to None.
    assert resolve_source(PAGE + "?video=Nope_1", fetch=fake_fetch) is None


def test_non_iga_url_never_fetches():
    def no_fetch(url):
        raise AssertionError("fetched")

    assert iga.resolve_iga_video("https://example.com/x.m3u8", fetch=no_fetch) is None


def test_floor_listing_titles_keep_part_number():
    with pytest.raises(SourceSelectionRequired) as exc:
        resolve_source("https://iga.in.gov/session/2026/video/senate", fetch=fake_fetch)
    assert all("/video/senate/?video=Session_" in c["url"] for c in exc.value.choices)
    video = iga.IgaVideo(stream_url="", stem="Session_2_27_2026_4", date="2026-02-27",
                         label="Friday, Feb. 27 part 4", media_id=None)
    assert iga._title("senate", None, video) == "Indiana Senate Floor Session — Feb. 27, 2026 (part 4)"


def test_ingest_surfaces_selection_as_a_clear_error(monkeypatch):
    from src import ingest, resolve

    def raise_selection(url):
        raise SourceSelectionRequired("pick one", choices=[{"label": "Jan 14", "url": JAN14}])

    monkeypatch.setattr(resolve, "resolve_source", raise_selection)
    with pytest.raises(ValueError, match="Jan 14: " + JAN14.replace("?", r"\?")):
        ingest._resolve_source_safe(PAGE)


# --- roster ------------------------------------------------------------------

def _load(name):
    import json

    return json.loads((FIX / name).read_text())


def test_collect_legislators_members_then_agenda_authors():
    legs = collect_legislators(_load("members_judiciary.json"),
                               _load("meeting_details_2026_01_14.json"),
                               _load("legislators.json"))
    names = [f"{x.first_name} {x.last_name}" for x in legs]
    assert names[0] == "Cyndi Carrasco" and "Chair" in legs[0].roles
    assert "Bill author (SB 285)" in legs[0].roles  # member who also authors: merged, not duplicated
    assert names.count("Eric Koch") == 1
    walker = next(x for x in legs if x.last_name == "Walker")
    assert walker.roles == ["Bill author (SB 52)"] and walker.district == "31" and walker.chamber == "senate"
    assert len(legs) == 14


def test_pick_politician_requires_state_chamber_and_district():
    leg = Legislator("l", "Greg", "Taylor", "senate", "33")
    rows = [
        {"politician_id": "tx", "full_name": "Greg Taylor", "last_name": "Taylor",
         "representing_state": "TX", "district_label": "State Senate District 33"},
        {"politician_id": "house", "full_name": "Greg Taylor", "last_name": "Taylor",
         "representing_state": "IN", "district_label": "State House District 33"},
        {"politician_id": "in", "full_name": "Greg Taylor", "last_name": "Taylor",
         "representing_state": "IN", "district_label": "Indiana State Senate - District 33"},
    ]
    assert pick_politician(leg, rows)["politician_id"] == "in"
    assert pick_politician(Legislator("l", "Greg", "Taylor", "senate", "3"), rows) is None


def test_build_roster_reports_unmatched():
    def lookup(last):
        if last == "Walker":
            return []
        return [{"politician_id": f"id-{last}", "full_name": last, "last_name": last,
                 "representing_state": "IN", "district_label": "State Senate District {}"}]

    # Districts come from IGA; give every IN row the right one.
    legs = {x.last_name: x.district for x in collect_legislators(
        _load("members_judiciary.json"), _load("meeting_details_2026_01_14.json"), _load("legislators.json"))}

    def lookup_with_district(last):
        return [dict(r, district_label=r["district_label"].format(legs[last])) for r in lookup(last)]

    response, matches = build_roster(JAN14, fetch=fake_fetch, lookup=lookup_with_district)
    assert len(response["members"]) == 13
    assert [m.legislator.last_name for m in matches if not m.politician_id] == ["Walker"]
    assert all(m["title"] == "Senator" for m in response["members"])
