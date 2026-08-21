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


from src.event_kinds import LOCAL_ROLE_RE, resolve_local_role


def test_resolve_local_role_always_matches_the_db_shape():
    """The DB CHECK added by CA_0003 requires a leading letter, so every value
    this function can return must satisfy LOCAL_ROLE_RE — otherwise the prompt
    accepts roles that publish cannot store."""
    for raw in ["City Attorney", "123 Main St", "_leading", "!!!", "3rd party",
                "  ", "x" * 200, "Dept. Head!", "ZONING board"]:
        role = resolve_local_role(raw, "council")
        assert LOCAL_ROLE_RE.match(role), f"{raw!r} produced {role!r}"


def test_resolve_local_role_strips_leading_non_letters():
    assert resolve_local_role("123 Main St", "council") == "main_st"


def test_resolve_local_role_falls_back_when_nothing_survives():
    # all digits normalise away entirely -> the kind's default, not an empty role
    assert resolve_local_role("123", "council") == "public_comment"


def test_resolve_local_role_truncates_to_forty_chars():
    role = resolve_local_role("a" * 80, "council")
    assert len(role) == 40
