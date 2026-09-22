import re
from src.event_kinds import local_roles_for, LOCAL_ROLE_PATTERN


def test_news_clip_offers_interview_roles():
    roles = local_roles_for("news_clip")
    assert "anchor" in roles and "host" in roles and "guest" in roles


def test_podcast_offers_interview_roles():
    assert "host" in local_roles_for("podcast")


def test_interview_roles_match_the_stored_pattern():
    pat = re.compile(LOCAL_ROLE_PATTERN)
    for r in local_roles_for("news_clip"):
        assert pat.match(r), f"{r} must match the storable role pattern"


def test_civic_and_campaign_kinds_unchanged():
    assert local_roles_for("council")[0] == "public_comment"
    assert local_roles_for("debate") == ("candidate", "moderator", "panelist")
