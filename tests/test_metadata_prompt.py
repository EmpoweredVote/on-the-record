"""_resolve_metadata: prompt for unset fields interactively; --default / non-tty use defaults."""
from __future__ import annotations

import argparse
import run_local


def _args(**kw):
    base = dict(city=None, date="", meeting_type=None, default=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_default_flag_fills_without_prompting(monkeypatch):
    def no_input(*a, **k):
        raise AssertionError("should not prompt with --default")
    monkeypatch.setattr("builtins.input", no_input)
    monkeypatch.setattr(run_local, "_today_iso", lambda: "2026-06-09")
    args = _args(default=True)
    run_local._resolve_metadata(args)
    assert args.city == "Bloomington"
    assert args.meeting_type == "Regular Session"
    assert args.date == "2026-06-09"


def test_non_tty_fills_without_prompting(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt")))
    monkeypatch.setattr(run_local, "_today_iso", lambda: "2026-06-09")
    args = _args()
    run_local._resolve_metadata(args)
    assert args.city == "Bloomington"
    assert args.meeting_type == "Regular Session"
    assert args.date == "2026-06-09"


def test_interactive_prompts_only_unset(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(run_local, "_today_iso", lambda: "2026-06-09")
    answers = iter(["Carmel", "", ""])   # city typed; date Enter→today; type Enter→default
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    args = _args(meeting_type="Plan Commission")  # already provided → not prompted
    run_local._resolve_metadata(args)
    assert args.city == "Carmel"
    assert args.date == "2026-06-09"
    assert args.meeting_type == "Plan Commission"


def test_interactive_keeps_cli_values(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing to prompt")))
    monkeypatch.setattr(run_local, "_today_iso", lambda: "2026-06-09")
    args = _args(city="Bloomington", date="2026-01-01", meeting_type="Special")
    run_local._resolve_metadata(args)
    assert (args.city, args.date, args.meeting_type) == ("Bloomington", "2026-01-01", "Special")
