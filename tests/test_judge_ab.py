import json

from scripts.judge_ab import (
    mechanism_separation,
    human_agreement,
    parse_error_rate,
    inter_judge_agreement,
    divergent_ids,
    load_gold,
    run_ab,
    render_report,
)
from src.evidence.disposition import decide
from src.evidence.models import JudgeScores


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


# --- load_gold (fake DB cursor — no real DB) ---

class _FakeCursor:
    """Mimics the slice of the DB-API cursor protocol load_gold uses:
    .execute(sql, params), .description (column-name tuples), .fetchall()."""

    def __init__(self, columns, rows):
        self.description = [(c,) for c in columns]
        self._rows = rows
        self.executed_sql = None
        self.executed_params = None

    def execute(self, sql, params=None):
        self.executed_sql = sql
        self.executed_params = params

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, columns, rows):
        self._cursor = _FakeCursor(columns, rows)

    def cursor(self):
        return self._cursor


def test_load_gold_maps_rows_to_dicts_by_column():
    columns = ["id", "verbatim_text", "context", "issue", "review_status", "review_reason"]
    rows = [
        ("e1", "We will cut permit review to 30 days.", "housing forum", "housing",
         "accepted", None),
        ("e2", "We will reduce homelessness.", "housing forum", "housing",
         "rejected", "goal-only"),
    ]
    conn = _FakeConn(columns, rows)
    gold = load_gold(conn)
    assert gold == [
        {"id": "e1", "verbatim_text": "We will cut permit review to 30 days.",
         "context": "housing forum", "issue": "housing",
         "review_status": "accepted", "review_reason": None},
        {"id": "e2", "verbatim_text": "We will reduce homelessness.",
         "context": "housing forum", "issue": "housing",
         "review_status": "rejected", "review_reason": "goal-only"},
    ]


def test_load_gold_is_select_only_and_filters_review_status():
    conn = _FakeConn(
        ["id", "verbatim_text", "context", "issue", "review_status", "review_reason"], [])
    load_gold(conn)
    sql = conn._cursor.executed_sql.upper()
    assert sql.strip().startswith("SELECT")
    assert "INSERT" not in sql and "UPDATE" not in sql and "DELETE" not in sql
    assert "INFORM.EVIDENCE_ITEMS" in sql
    assert "REVIEW_STATUS" in sql and "ACCEPTED" in sql and "REJECTED" in sql


# --- run_ab / render_report (fake arm callables, REAL decide, no DB/LLM) ---

def _fake_arm(*, tag_ok, mechanism, dispute_risk, context_sufficient=0.9, parse_ok=True):
    """A canned arm callable: ignores its candidate, always returns the same
    JudgeScores + parse_ok. No timing/network — just plain Python."""
    scores = JudgeScores(tag_ok=tag_ok, context_sufficient=context_sufficient,
                          dispute_risk=dispute_risk, mechanism=mechanism, notes="")

    def arm(cand):
        return scores, parse_ok

    return arm


def _gold_item(id_, human_status, review_reason=None):
    return {"id": id_, "verbatim_text": f"quote text for {id_}", "context": "some context",
            "issue": "housing", "review_status": human_status, "review_reason": review_reason}


def _tiny_gold():
    return [
        _gold_item("a1", "accepted"),
        _gold_item("r1", "rejected", review_reason="goal-only"),
    ]


def test_run_ab_one_row_per_item_per_arm_with_real_decide():
    gold = _tiny_gold()
    # "good" scores every item as clean evidence -> green regardless of the
    # gold label (this harness isolates the JUDGE from the human verdict).
    # "bad" scores every item as mechanism-free -> flagged (judge:no-mechanism).
    arms = {
        "good": _fake_arm(tag_ok=0.9, mechanism=0.9, dispute_risk=0.1),
        "bad": _fake_arm(tag_ok=0.9, mechanism=0.1, dispute_risk=0.1),
    }

    rows_by_arm = run_ab(gold, arms, decide_fn=decide)

    assert set(rows_by_arm.keys()) == {"good", "bad"}
    for arm_name, rows in rows_by_arm.items():
        assert len(rows) == 2
        assert {r["id"] for r in rows} == {"a1", "r1"}
        for row in rows:
            assert row["arm"] == arm_name
            assert row["parse_ok"] is True
            assert isinstance(row["latency"], float) and row["latency"] >= 0

    good_by_id = {r["id"]: r for r in rows_by_arm["good"]}
    assert good_by_id["a1"]["disposition"] == "green"
    assert good_by_id["a1"]["human_status"] == "accepted"
    assert good_by_id["a1"]["review_reason"] is None
    assert good_by_id["r1"]["disposition"] == "green"
    assert good_by_id["r1"]["human_status"] == "rejected"
    assert good_by_id["r1"]["review_reason"] == "goal-only"

    bad_by_id = {r["id"]: r for r in rows_by_arm["bad"]}
    assert bad_by_id["a1"]["disposition"] == "flagged"
    assert bad_by_id["r1"]["disposition"] == "flagged"


def test_run_ab_carries_parse_ok_false_through_to_the_row():
    gold = [_gold_item("a1", "accepted")]
    arms = {"broken": _fake_arm(tag_ok=0.0, mechanism=0.0, dispute_risk=1.0, parse_ok=False)}

    rows_by_arm = run_ab(gold, arms, decide_fn=decide)

    row = rows_by_arm["broken"][0]
    assert row["parse_ok"] is False
    # worst-default scores also fail the judge gates on their own merits
    assert row["disposition"] == "flagged"


def test_render_report_contains_each_arm_name():
    gold = _tiny_gold()
    arms = {
        "deepseek": _fake_arm(tag_ok=0.9, mechanism=0.9, dispute_risk=0.1),
        "gemini-flash": _fake_arm(tag_ok=0.9, mechanism=0.2, dispute_risk=0.1),
    }
    rows_by_arm = run_ab(gold, arms, decide_fn=decide)

    report = render_report(rows_by_arm, gold)

    assert isinstance(report, str)
    assert "deepseek" in report
    assert "gemini-flash" in report
    assert "Inter-judge agreement" in report
    assert "Divergent ids" in report


def test_render_report_surfaces_jev_confidence_when_notes_carry_it():
    gold = [_gold_item("a1", "accepted")]
    scores = JudgeScores(tag_ok=1.0, context_sufficient=1.0, dispute_risk=0.0, mechanism=1.0,
                          notes=json.dumps({"confidence": {"mechanism": 0.9, "tag_ok": 0.3}}))

    def jev_arm(cand):
        return scores, True

    rows_by_arm = run_ab(gold, {"jev": jev_arm}, decide_fn=decide)
    report = render_report(rows_by_arm, gold)

    assert "jev confidence" in report
    assert "low-confidence items" in report


def test_render_report_omits_jev_confidence_when_no_arm_carries_notes():
    gold = _tiny_gold()
    arms = {"deepseek": _fake_arm(tag_ok=0.9, mechanism=0.9, dispute_risk=0.1)}
    rows_by_arm = run_ab(gold, arms, decide_fn=decide)

    report = render_report(rows_by_arm, gold)

    assert "jev confidence" not in report
