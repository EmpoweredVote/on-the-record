from pathlib import Path
from src.crec_votes import RollCallVote
from src.crec_members import build_bioguide_index, MemberVote, enrich_vote

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def test_build_bioguide_index_from_mods():
    mods = (FIX / "granule_vote_mods.xml").read_text()
    idx = build_bioguide_index(mods)
    assert idx == {"Adams": "A000370", "Aguilar": "A000371"}


def test_ambiguous_surname_is_dropped():
    mods = (
        '<congMember bioGuideId="X1"><name type="parsed">Smith</name></congMember>'
        '<congMember bioGuideId="X2"><name type="parsed">Smith</name></congMember>'
        '<congMember bioGuideId="Y1"><name type="parsed">Jones</name></congMember>'
    )
    idx = build_bioguide_index(mods)
    assert "Smith" not in idx           # ambiguous -> unresolved, never mislinked
    assert idx["Jones"] == "Y1"


def test_enrich_vote_resolves_known_and_leaves_unknown():
    idx = {"Adams": "A000370", "Aguilar": "A000371"}
    v = RollCallVote(438, "q", {"YEA": ["Adams", "Aguilar"], "NAY": ["Abraham"]})
    members = enrich_vote(v, idx)
    assert MemberVote("Adams", "YEA", "A000370") in members
    assert MemberVote("Aguilar", "YEA", "A000371") in members
    assert MemberVote("Abraham", "NAY", None) in members
