"""A meeting id's date prefix and --date are entered separately; a mismatch
published the wrong date (LWV forum: id 2026-03-23, --date 2026-06-09)."""
import run_local


def test_mismatch_returns_warning_naming_both_dates():
    msg = run_local._meeting_id_date_mismatch(
        "2026-03-23-lwv-candidate-forum", "2026-06-09")
    assert "2026-03-23" in msg and "2026-06-09" in msg


def test_matching_date_returns_none():
    assert run_local._meeting_id_date_mismatch(
        "2026-03-23-lwv-candidate-forum", "2026-03-23") is None


def test_id_without_date_prefix_or_missing_date_returns_none():
    assert run_local._meeting_id_date_mismatch("lwv-forum", "2026-03-23") is None
    assert run_local._meeting_id_date_mismatch("2026-03-23-x", None) is None
    assert run_local._meeting_id_date_mismatch("2026-03-23-x", "") is None
