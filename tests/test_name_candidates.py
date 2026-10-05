from __future__ import annotations

from src.name_candidates import CONFLICT_TWO_LABELS, CONFLICT_TWO_NAMES, build_candidates
from src.name_evidence import Evidence


def ev(kind, label, name, title=None, affiliation=None, quote="q"):
    return Evidence(kind=kind, label=label, name=name, title=title, affiliation=affiliation,
                    quote=quote, segment_id=0)


def test_tiers():
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E3", "A", "Sample", "Ms.")])
    assert c["A"].tier == "strong" and c["A"].prefill_name == "Rachel Sample"
    assert build_candidates([ev("E1", "A", "Rachel Sample")])["A"].tier == "medium"
    assert build_candidates([ev("E2", "A", "Sample"), ev("E3", "A", "Sample")])["A"].tier == "medium"
    assert build_candidates([ev("E2", "A", "Rachel Sample")])["A"].tier == "weak"
    assert build_candidates([ev("E3", "A", "Rachel Sample")])["A"].tier == "weak"


def test_e4_alone_makes_no_candidate():
    assert build_candidates([ev("E4", "A", "Rachel Sample")]) == {}


def test_full_name_preferred_over_surname_only():
    c = build_candidates([ev("E2", "A", "Spiegel", "Rabbi"), ev("E1", "A", "Aaron Spiegel", "Rabbi")])
    assert c["A"].name == "Aaron Spiegel" and not c["A"].partial


def test_partial_name_is_not_prefilled():
    c = build_candidates([ev("E1", "A", "Nitya")])
    assert c["A"].partial and c["A"].prefill_name is None and c["A"].name == "Nitya"


def test_x4_same_name_on_two_labels_is_conflict():
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E1", "B", "Rachel Sample")])
    assert c["A"].conflict == c["B"].conflict == CONFLICT_TWO_LABELS
    assert c["A"].prefill_name is None


def test_x5_two_strong_names_on_one_label_is_conflict():
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E2", "A", "Paul Webster"),
                          ev("E3", "A", "Webster", "Mr.")])
    assert c["A"].conflict == CONFLICT_TWO_NAMES and c["A"].prefill_name is None


def test_x5_weak_second_name_does_not_conflict():
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E3", "A", "Paul Webster")])
    assert c["A"].conflict is None and c["A"].name == "Rachel Sample"


def test_roles():
    assert build_candidates([ev("E2", "A", "Brown", "Senator")])["A"].role == "official"
    assert build_candidates([ev("E2", "A", "Brown", "Senator")])["A"].titled is True
    assert build_candidates([ev("E1", "A", "Ann Lee", affiliation="the Legislative Services Agency")])["A"].role == "staff"
    assert build_candidates([ev("E1", "A", "Ann Lee", affiliation="Hoosier Families")])["A"].role == "presenter"
    assert build_candidates([ev("E1", "A", "Ann Lee")])["A"].role == "public_comment"
    assert build_candidates([ev("E1", "A", "Ann Lee")], event_kind="debate")["A"].role is None


def test_courtesy_title_is_not_office():
    c = build_candidates([ev("E1", "A", "Aaron Spiegel", "Rabbi")])
    assert c["A"].titled is False


# Fix: Group by full name (significant tokens), not surname
def test_two_different_full_names_same_surname_is_conflict():
    """Rachel Sample E1 + Tom Sample E1 → both conflict (two different full names)."""
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E1", "A", "Tom Sample")])
    assert c["A"].conflict == CONFLICT_TWO_NAMES and c["A"].prefill_name is None


def test_partial_name_with_two_full_names_same_surname_stays_ambiguous():
    """Rachel Sample E1 + Tom Sample E1 + "Sample" E3 → conflict persists, E3 partial doesn't join."""
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E1", "A", "Tom Sample"),
                          ev("E3", "A", "Sample")])
    assert c["A"].conflict == CONFLICT_TWO_NAMES
    assert "Sample" in [e.name for e in c["A"].evidence]  # Evidence kept but doesn't affect tier


def test_full_name_groups_with_title_prefix():
    """'Dr. Ann Lee' E1 + 'Ann Lee' E1 → same group, medium tier."""
    c = build_candidates([ev("E1", "A", "Dr. Ann Lee"), ev("E1", "A", "Ann Lee")])
    assert c["A"].tier == "medium" and c["A"].name in ("Dr. Ann Lee", "Ann Lee")


def test_x4_compares_significant_tokens_not_normalize():
    """'Dr. Ann Lee' on A and 'Ann Lee' on B → conflict (same significant tokens)."""
    c = build_candidates([ev("E1", "A", "Dr. Ann Lee"), ev("E1", "B", "Ann Lee")])
    assert c["A"].conflict == c["B"].conflict == CONFLICT_TWO_LABELS


def test_partial_joins_only_one_full_group_by_surname():
    """'Aaron Spiegel' E1 + 'Spiegel' E2 → 'Spiegel' joins 'Aaron Spiegel' group, strong tier."""
    c = build_candidates([ev("E1", "A", "Aaron Spiegel"), ev("E2", "A", "Spiegel")])
    assert c["A"].tier == "strong" and c["A"].name == "Aaron Spiegel"


def test_partial_alone_forms_own_group():
    """'Senator Brown' E2 alone (single significant token) forms its own weak group."""
    c = build_candidates([ev("E2", "A", "Senator Brown")])
    assert c["A"].name == "Senator Brown" and c["A"].tier == "weak"
    # one significant token ("Senator" is an honorific) => partial, never pre-filled
    assert c["A"].partial and c["A"].prefill_name is None


def test_staff_cues_from_e1_and_affiliation_only():
    """E1 with affiliation cue → staff. E2/E3 with staff cue in quote → not considered."""
    c = build_candidates([
        ev("E1", "A", "Ann Lee", affiliation="Legislative Services Agency"),
        ev("E2", "A", "Ann Lee", quote="City staff meeting"),  # E2 quote ignored
    ])
    assert c["A"].role == "staff"


def test_public_commenter_with_e3_staff_cue_stays_public():
    """E1 public commenter + E3 quote with 'city staff' → role still public_comment."""
    c = build_candidates([
        ev("E1", "A", "John Doe"),
        ev("E3", "A", "John Doe", quote="I work with city staff"),
    ])
    assert c["A"].role == "public_comment"


def test_clerk_cues_added():
    """'city clerk' and 'county clerk' in affiliation → staff role."""
    assert build_candidates([ev("E1", "A", "Ann Lee", affiliation="city clerk")])["A"].role == "staff"
    assert build_candidates([ev("E1", "A", "Ann Lee", affiliation="county clerk")])["A"].role == "staff"


def test_office_title_in_debate_is_none():
    """Titled 'Senator' in debate event_kind → role None."""
    c = build_candidates([ev("E1", "A", "Brown", "Senator")], event_kind="debate")
    assert c["A"].titled is True and c["A"].role is None


def test_tied_groups_do_not_crash_sorting():
    """The same unmatched first name twice makes two own-groups tie on every sort key (distinct quotes, so Evidence would be compared)."""
    c = build_candidates([
        ev("E1", "A", "Carolyn Johnson"), ev("E2", "A", "Lolita", quote="one"), ev("E2", "A", "Lolita", quote="two"),
    ])
    assert c["A"].name == "Carolyn Johnson"


# ---- final-review fixes ----
def test_title_without_period_and_partial_by_significant_tokens():
    c = build_candidates([ev("E2", "A", "Sen. Brown", "Sen.")])["A"]
    assert c.partial is True and c.titled is True and c.prefill_name is None
    c = build_candidates([ev("E2", "A", "Brown", "Gov")])["A"]
    assert c.titled is True


def test_honorific_only_evidence_dropped_no_false_conflict():
    c = build_candidates([ev("E1", "A", "Ann Lee"), ev("E1", "A", "Chair"), ev("E2", "A", "Chair")])["A"]
    assert c.conflict is None and c.prefill_name == "Ann Lee"


def test_tied_top_tier_names_block_prefill():
    from src.name_candidates import CONFLICT_TIED_NAMES
    c = build_candidates([ev("E2", "A", "Ann Lee"), ev("E3", "A", "Bob Fox")])["A"]
    assert c.conflict == CONFLICT_TIED_NAMES and c.prefill_name is None
