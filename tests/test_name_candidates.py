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
