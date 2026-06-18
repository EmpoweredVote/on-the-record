"""Clip-control input for interactive speaker review.

Root cause of "shortcut keys don't work while a clip plays" was two-fold:
  1. the ffplay VIDEO window steals macOS keyboard focus on launch, so keys
     reach the player, not the terminal; and
  2. the prompt used line-based input(), so single keys (V/R/Y/...) did nothing
     until Enter.

These tests cover the two fixes:
  * _read_review_command — single-key (cbreak) reader that fires command keys
    instantly, with a graceful input() fallback when no TTY/termios is available
    (so scripted callers and non-interactive mode are unaffected); and
  * _focus_terminal — best-effort, never-raising macOS focus return that no-ops
    off-darwin or when the terminal app is unknown.
"""
from __future__ import annotations

import run_local


# --------------------------------------------------------------------------
# _read_review_command — single-key command reader
# --------------------------------------------------------------------------

def _key(monkeypatch, ch):
    """Force the raw-mode reader to yield a single key `ch`."""
    monkeypatch.setattr(run_local, "_read_one_key", lambda: ch)


def test_enter_returns_skip(monkeypatch):
    for nl in ("\r", "\n", ""):
        _key(monkeypatch, nl)
        assert run_local._read_review_command("p ") == ""


def test_command_letters_map_to_tokens(monkeypatch):
    for ch, token in [("v", "v"), ("V", "v"), ("r", "r"), ("R", "r"),
                      ("y", "y"), ("m", "m"), ("q", "q"), ("Q", "q")]:
        _key(monkeypatch, ch)
        assert run_local._read_review_command("p ") == token


def test_digit_jumps_to_clip(monkeypatch):
    _key(monkeypatch, "3")
    # Reuses the existing "v<n>" jump parser in the review loop.
    assert run_local._read_review_command("p ") == "v3"


def test_printable_char_starts_a_typed_name(monkeypatch):
    _key(monkeypatch, "J")
    monkeypatch.setattr("builtins.input", lambda prompt="": "ane Doe")
    assert run_local._read_review_command("p ") == "Jane Doe"


def test_slash_starts_name_for_command_letter_names(monkeypatch):
    # Names beginning with a reserved command letter (Maria, Victor, ...) are
    # entered via the '/' escape, which drops straight into a line read.
    _key(monkeypatch, "/")
    monkeypatch.setattr("builtins.input", lambda prompt="": "Maria")
    assert run_local._read_review_command("p ") == "Maria"


def test_falls_back_to_line_input_without_raw_mode(monkeypatch):
    # No TTY/termios → _read_one_key returns None → behave exactly like input().
    monkeypatch.setattr(run_local, "_read_one_key", lambda: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "v3")
    assert run_local._read_review_command("p ") == "v3"


def test_refocus_returns_terminal_focus_before_reading(monkeypatch):
    calls = []
    monkeypatch.setattr(run_local, "_focus_terminal", lambda: calls.append(1))
    monkeypatch.setattr(run_local, "_read_one_key", lambda: "v")
    run_local._read_review_command("p ", refocus=True)
    assert calls == [1]
    # Not called when no player is up.
    calls.clear()
    run_local._read_review_command("p ", refocus=False)
    assert calls == []


# --------------------------------------------------------------------------
# _focus_terminal — best-effort macOS focus return
# --------------------------------------------------------------------------

def _spy_popen(monkeypatch):
    spawned = []
    monkeypatch.setattr(run_local.subprocess, "Popen",
                        lambda cmd, **kw: spawned.append(cmd))
    return spawned


def test_focus_terminal_noop_off_darwin(monkeypatch):
    monkeypatch.setattr(run_local.sys, "platform", "linux")
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    spawned = _spy_popen(monkeypatch)
    run_local._focus_terminal()
    assert spawned == []


def test_focus_terminal_activates_apple_terminal(monkeypatch):
    monkeypatch.setattr(run_local.sys, "platform", "darwin")
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    spawned = _spy_popen(monkeypatch)
    run_local._focus_terminal()
    assert len(spawned) == 1
    cmd = spawned[0]
    assert cmd[0] == "osascript"
    assert 'tell application "Terminal" to activate' in " ".join(cmd)


def test_focus_terminal_activates_iterm(monkeypatch):
    monkeypatch.setattr(run_local.sys, "platform", "darwin")
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    spawned = _spy_popen(monkeypatch)
    run_local._focus_terminal()
    assert len(spawned) == 1
    assert 'iTerm2' in " ".join(spawned[0])


def test_focus_terminal_noop_for_unknown_terminal(monkeypatch):
    monkeypatch.setattr(run_local.sys, "platform", "darwin")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    spawned = _spy_popen(monkeypatch)
    run_local._focus_terminal()
    assert spawned == []


def test_focus_terminal_never_raises(monkeypatch):
    monkeypatch.setattr(run_local.sys, "platform", "darwin")
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")

    def boom(*a, **k):
        raise OSError("osascript missing")

    monkeypatch.setattr(run_local.subprocess, "Popen", boom)
    run_local._focus_terminal()  # must not raise
