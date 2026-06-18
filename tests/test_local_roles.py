"""Event-kind-aware local-person role taxonomy for the review prompt."""
from __future__ import annotations

from src.event_kinds import (
    DEFAULT_LOCAL_ROLES,
    local_roles_for,
    resolve_local_role,
)


def test_council_offers_civic_roles_public_comment_first():
    roles = local_roles_for("council")
    assert roles[0] == "public_comment"  # empty-input default
    assert set(roles) == {"public_comment", "staff", "official", "presenter"}


def test_school_board_and_community_meeting_match_council():
    assert local_roles_for("school_board") == local_roles_for("council")
    assert local_roles_for("community_meeting") == local_roles_for("council")


def test_forum_and_debate_keep_campaign_roles():
    assert local_roles_for("forum") == ("candidate", "moderator", "panelist")
    assert local_roles_for("debate")[0] == "candidate"


def test_unknown_or_none_kind_falls_back_to_all_roles():
    assert local_roles_for(None) == DEFAULT_LOCAL_ROLES
    assert local_roles_for("news_clip") == DEFAULT_LOCAL_ROLES
    # the campaign vocab is never silently lost in the fallback
    assert "candidate" in DEFAULT_LOCAL_ROLES and "staff" in DEFAULT_LOCAL_ROLES


def test_resolve_empty_is_default_first_role():
    assert resolve_local_role("", "council") == "public_comment"
    assert resolve_local_role("   ", "forum") == "candidate"


def test_resolve_number_picks_listed_option():
    assert resolve_local_role("2", "council") == "staff"
    assert resolve_local_role("3", "forum") == "panelist"


def test_resolve_out_of_range_number_defaults_not_custom():
    assert resolve_local_role("9", "council") == "public_comment"
    assert resolve_local_role("0", "council") == "public_comment"


def test_resolve_freetext_custom_role_is_normalized():
    assert resolve_local_role("City Attorney", "council") == "city_attorney"
    assert resolve_local_role("Dept. Head!", "council") == "dept_head"


def test_resolve_never_silently_coerces_unknown_to_candidate():
    # The old prompt forced any unrecognized input to "candidate"; for a council
    # meeting that was always wrong. A typed role is honored instead.
    assert resolve_local_role("clerk", "council") == "clerk"
