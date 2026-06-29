"""_resolve_metadata: explicit metadata required; prompts interactively; --default
opts into civic defaults (never date); non-interactive + unset hard-fails."""
from __future__ import annotations

import argparse
import pytest
import run_local


def _args(**kw):
    base = dict(city=None, date="", meeting_type=None, title=None,
                event_kind=None, default=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_default_flag_fills_civic_but_requires_date(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not prompt with --default")))
    args = _args(default=True, date="2026-06-09")
    run_local._resolve_metadata(args)
    assert args.city == "Bloomington"
    assert args.meeting_type == "Regular Session"
    assert args.event_kind == "council"
    assert args.date == "2026-06-09"


def test_default_flag_without_date_raises(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no prompt")))
    args = _args(default=True)  # date unset
    with pytest.raises(ValueError, match="--date"):
        run_local._resolve_metadata(args)


def test_non_tty_unset_raises_naming_fields(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no prompt")))
    args = _args()
    with pytest.raises(ValueError) as exc:
        run_local._resolve_metadata(args)
    msg = str(exc.value)
    assert "--event-kind" in msg and "--meeting-type" in msg and "--date" in msg


def test_non_tty_explicit_flags_ok(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no prompt")))
    args = _args(city="Monroe County", date="2026-05-01",
                 meeting_type="Candidate Forum", event_kind="forum")
    run_local._resolve_metadata(args)
    assert (args.city, args.date, args.meeting_type, args.event_kind) == (
        "Monroe County", "2026-05-01", "Candidate Forum", "forum")


def test_non_tty_forum_needs_no_city(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no prompt")))
    # forum: city not required; meeting_type + date + kind given explicitly.
    args = _args(date="2026-05-01", meeting_type="Forum", event_kind="forum")
    run_local._resolve_metadata(args)
    assert args.city is None
    assert args.event_kind == "forum"


def test_interactive_prompts_event_kind_then_city_then_date(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # prompt order: event_kind (Enter->council), city (typed), date (typed).
    answers = iter(["", "Carmel", "2026-06-09"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    args = _args(meeting_type="Plan Commission")  # provided -> not prompted
    run_local._resolve_metadata(args)
    assert args.event_kind == "council"
    assert args.city == "Carmel"
    assert args.date == "2026-06-09"
    assert args.meeting_type == "Plan Commission"


def test_interactive_date_reprompts_until_given(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # event_kind Enter->council, city Enter->Bloomington, date: "" then real.
    answers = iter(["", "", "", "2026-03-03"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    args = _args(meeting_type="Regular Session")
    run_local._resolve_metadata(args)
    assert args.date == "2026-03-03"


def test_interactive_keeps_cli_values(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("nothing to prompt")))
    args = _args(city="Bloomington", date="2026-01-01",
                 meeting_type="Special", event_kind="council")
    run_local._resolve_metadata(args)
    assert (args.city, args.date, args.meeting_type, args.event_kind) == (
        "Bloomington", "2026-01-01", "Special", "council")


def test_invalid_event_kind_flag_raises(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    args = _args(city="X", date="2026-01-01", meeting_type="Y", event_kind="bogus")
    with pytest.raises(ValueError):
        run_local._resolve_metadata(args)


def test_batch_mode_does_not_prompt_even_on_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)  # batch on a terminal
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("batch must not prompt")))
    args = _args(batch_mode=True)  # unset + non-interactive-by-batch
    with pytest.raises(ValueError):
        run_local._resolve_metadata(args)
