from __future__ import annotations

import builtins

import run_local
from src.models import SpeakerMapping


class _FakeTTY:
    """Stand-in for sys.stdin whose isatty() is True, so the prompt functions
    get PAST their tty short-circuit and the politician_id guard is what
    actually decides — otherwise these tests would pass for the wrong reason."""

    def isatty(self):
        return True


def test_prompt_link_skips_when_politician_id_set_slug_null(monkeypatch):
    # id-linked (slug-null) speaker => _prompt_link_politician must treat it as
    # already linked and return BEFORE searching, even on a (faked) TTY.
    monkeypatch.setattr("sys.stdin", _FakeTTY())
    called = {"search": 0}
    monkeypatch.setattr("src.essentials_client.search_politicians",
                        lambda *a, **k: called.__setitem__("search", called["search"] + 1) or [])
    monkeypatch.setattr(builtins, "input",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted")))
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Steve Hilton",
                                     politician_id="uuid-h", politician_slug=None)}
    run_local._prompt_link_politician(mappings, "S0", "Steve Hilton")
    assert called["search"] == 0   # id-guard short-circuited before search/prompt


def test_prompt_create_local_person_skips_when_politician_id_set_slug_null(monkeypatch):
    # id-linked speaker => _prompt_create_local_person must NOT offer a local
    # person (essentials link wins), returning before any prompt, on a TTY.
    monkeypatch.setattr("sys.stdin", _FakeTTY())
    monkeypatch.setattr(builtins, "input",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted")))
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Steve Hilton",
                                     politician_id="uuid-h", politician_slug=None)}
    run_local._prompt_create_local_person(mappings, "S0", "Steve Hilton")
    # No AssertionError raised by input => it returned via the id-guard.
    assert mappings["S0"].local_slug is None
