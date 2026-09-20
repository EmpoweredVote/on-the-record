from src.evidence.models import SourceType
from src.evidence.triage import registrable_domain, classify_domain


def test_domain_rules():
    assert classify_domain("https://www.lcv.org/scorecard") is SourceType.SCORECARD_QUIZ
    assert classify_domain("https://isidewith.com/x") is SourceType.SCORECARD_QUIZ
    assert classify_domain("https://youtu.be/abc") is SourceType.VIDEO_UNFETCHED
    assert classify_domain("https://www.congress.gov/bill/1") is SourceType.VOTE_RECORD
    assert classify_domain("https://cityclerk.lacity.org/x") is SourceType.VOTE_RECORD
    assert classify_domain("https://en.wikipedia.org/wiki/Karen_Bass") is SourceType.POINTER
    assert classify_domain("https://www.ontheissues.org/x") is SourceType.POINTER


def test_ordinary_web_page_returns_none_for_llm():
    assert classify_domain("https://nithyaforthecity.com/housing") is None
    assert classify_domain("https://laist.com/news/story") is None


def test_registrable_domain_strips_www():
    assert registrable_domain("https://www.LAist.com/a") == "laist.com"
