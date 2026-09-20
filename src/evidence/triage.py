from __future__ import annotations
from urllib.parse import urlsplit
from .models import SourceType

SCORECARD_QUIZ_DOMAINS = {"lcv.org", "isidewith.com", "votesmart.org",
                          "justfacts.votesmart.org"}
VIDEO_DOMAINS = {"youtube.com", "youtu.be", "m.youtube.com", "vimeo.com"}
POINTER_DOMAINS = {"ontheissues.org", "en.wikipedia.org", "wikipedia.org",
                   "web.archive.org"}
# Primary-for-VOTES (out of this slice; logged, not extracted).
VOTE_RECORD_DOMAINS = {
    "congress.gov", "govinfo.gov", "cityclerk.lacity.org",
    "leginfo.legislature.ca.gov", "mgaleg.maryland.gov",
    "legislature.maine.gov", "malegislature.gov", "capitol.texas.gov",
    "senate.gov", "senate.texas.gov", "ncleg.gov", "azleg.gov",
    "docs.legis.wisconsin.gov", "olis.oregonlegislature.gov",
    "lawfilesext.leg.wa.gov", "legacylis.virginia.gov",
}


def registrable_domain(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def classify_domain(url: str) -> "SourceType | None":
    d = registrable_domain(url)
    if d in SCORECARD_QUIZ_DOMAINS:
        return SourceType.SCORECARD_QUIZ
    if d in VIDEO_DOMAINS:
        return SourceType.VIDEO_UNFETCHED
    if d in VOTE_RECORD_DOMAINS:
        return SourceType.VOTE_RECORD
    if d in POINTER_DOMAINS:
        return SourceType.POINTER
    return None
