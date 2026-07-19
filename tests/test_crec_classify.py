from pathlib import Path
from src.crec_structure import CrecGranule
from src.crec_classify import GranuleKind, classify

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def _g(title, text):
    return CrecGranule("id", "HOUSE", title, text)


def test_vote_bearing_granule_is_legislative():
    text = (FIX / "granule_vote_block.txt").read_text()
    assert classify(_g("NATIONAL DEFENSE AUTHORIZATION ACT", text)) is GranuleKind.LEGISLATIVE


def test_consideration_title_is_legislative_without_vote():
    assert classify(_g("PROVIDING FOR CONSIDERATION OF H.R. 962",
                       "Mr. Speaker, I yield.")) is GranuleKind.LEGISLATIVE


def test_constitutional_authority_statement_is_back_matter():
    text = (FIX / "granule_backmatter.txt").read_text()
    assert classify(_g("Constitutional Authority Statement for H.R. 3694",
                       text)) is GranuleKind.BACK_MATTER


def test_the_journal_is_procedural():
    assert classify(_g("THE JOURNAL", "The SPEAKER pro tempore. The Journal stands approved.")) is GranuleKind.PROCEDURAL


def test_one_minute_speech_is_attention():
    text = "The SPEAKER pro tempore. The gentleman is recognized.\n  Mr. SMITH. Mr. Speaker, I rise today to honor..."
    assert classify(_g("HONORING WILLIAM HENRY WARD", text)) is GranuleKind.ONE_MINUTE
