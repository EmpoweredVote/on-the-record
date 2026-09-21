from scripts.judge_ab import (
    mechanism_separation,
    human_agreement,
    parse_error_rate,
    inter_judge_agreement,
    divergent_ids,
)


def _row(**kw):
    base = dict(
        id="q1",
        human_status="accepted",
        review_reason=None,
        arm="deepseek",
        mechanism=0.8,
        disposition="green",
        parse_ok=True,
        latency=1.2,
    )
    base.update(kw)
    return base


# --- mechanism_separation ---

def test_mechanism_separation_positive_gap_for_accept_vs_goalonly():
    rows = [
        _row(id="a1", human_status="accepted", mechanism=0.9),
        _row(id="a2", human_status="accepted", mechanism=0.7),
        _row(id="r1", human_status="rejected", review_reason="goal-only", mechanism=0.2),
        _row(id="r2", human_status="rejected", review_reason="goal-only", mechanism=0.4),
    ]
    result = mechanism_separation(rows)
    assert round(result["accept_mean"], 10) == 0.8
    assert round(result["goalonly_mean"], 10) == 0.3
    assert result["gap"] > 0
    assert round(result["gap"], 10) == round(0.8 - 0.3, 10)


def test_mechanism_separation_ignores_other_reject_reasons():
    rows = [
        _row(id="a1", human_status="accepted", mechanism=1.0),
        _row(id="r1", human_status="rejected", review_reason="not-verbatim", mechanism=0.9),
        _row(id="r2", human_status="rejected", review_reason="goal-only", mechanism=0.0),
    ]
    result = mechanism_separation(rows)
    assert result["accept_mean"] == 1.0
    assert result["goalonly_mean"] == 0.0
    assert result["gap"] == 1.0


def test_mechanism_separation_empty_groups_are_none():
    result = mechanism_separation([])
    assert result["accept_mean"] is None
    assert result["goalonly_mean"] is None
    assert result["gap"] is None


def test_mechanism_separation_one_empty_group_is_none():
    rows = [_row(id="a1", human_status="accepted", mechanism=0.5)]
    result = mechanism_separation(rows)
    assert result["accept_mean"] == 0.5
    assert result["goalonly_mean"] is None
    assert result["gap"] is None


# --- human_agreement ---

def test_human_agreement_counts_green_accept_and_flag_reject():
    rows = [
        _row(id="a1", human_status="accepted", disposition="green"),
        _row(id="a2", human_status="accepted", disposition="flagged"),  # disagree
        _row(id="r1", human_status="rejected", review_reason="goal-only", disposition="flagged"),
        _row(id="r2", human_status="rejected", review_reason="goal-only", disposition="green"),  # disagree
    ]
    result = human_agreement(rows)
    assert result["n"] == 4
    assert result["agree"] == 2
    assert result["rate"] == 0.5


def test_human_agreement_ignores_non_judge_relevant_rows():
    rows = [
        _row(id="a1", human_status="accepted", disposition="green"),
        _row(id="x1", human_status="rejected", review_reason="not-verbatim", disposition="flagged"),
    ]
    result = human_agreement(rows)
    assert result["n"] == 1
    assert result["agree"] == 1
    assert result["rate"] == 1.0


def test_human_agreement_empty_rate_zero():
    result = human_agreement([])
    assert result["n"] == 0
    assert result["agree"] == 0
    assert result["rate"] == 0.0


# --- parse_error_rate ---

def test_parse_error_rate_counts_false():
    rows = [
        _row(id="1", parse_ok=True),
        _row(id="2", parse_ok=False),
        _row(id="3", parse_ok=False),
        _row(id="4", parse_ok=True),
    ]
    assert parse_error_rate(rows) == 0.5


def test_parse_error_rate_empty_is_zero():
    assert parse_error_rate([]) == 0.0


# --- inter_judge_agreement / divergent_ids ---

def test_inter_judge_agreement_perfect_match():
    rows_by_arm = {
        "deepseek": [
            _row(id="q1", arm="deepseek", disposition="green"),
            _row(id="q2", arm="deepseek", disposition="flagged"),
        ],
        "gemini": [
            _row(id="q1", arm="gemini", disposition="green"),
            _row(id="q2", arm="gemini", disposition="flagged"),
        ],
    }
    result = inter_judge_agreement(rows_by_arm)
    assert result["deepseek|gemini"] == 1.0
    assert divergent_ids(rows_by_arm) == []


def test_inter_judge_agreement_one_divergence():
    rows_by_arm = {
        "deepseek": [
            _row(id="q1", arm="deepseek", disposition="green"),
            _row(id="q2", arm="deepseek", disposition="flagged"),
        ],
        "gemini": [
            _row(id="q1", arm="gemini", disposition="green"),
            _row(id="q2", arm="gemini", disposition="green"),  # divergent
        ],
    }
    result = inter_judge_agreement(rows_by_arm)
    assert result["deepseek|gemini"] == 0.5
    assert divergent_ids(rows_by_arm) == ["q2"]


def test_inter_judge_agreement_three_arms_pairwise():
    rows_by_arm = {
        "a": [_row(id="q1", arm="a", disposition="green")],
        "b": [_row(id="q1", arm="b", disposition="green")],
        "c": [_row(id="q1", arm="c", disposition="flagged")],
    }
    result = inter_judge_agreement(rows_by_arm)
    assert set(result.keys()) == {"a|b", "a|c", "b|c"}
    assert result["a|b"] == 1.0
    assert result["a|c"] == 0.0
    assert result["b|c"] == 0.0
    assert divergent_ids(rows_by_arm) == ["q1"]
