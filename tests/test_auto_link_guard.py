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


def test_prompt_create_local_person_seeds_the_slug_from_the_name_it_is_given(monkeypatch):
    """Pins WHY the terminal review call sites must hand this the name the curator
    TYPED, not rename_speaker's roster-corrected `res.new_name`.

    The `name` argument feeds default_local_slug only — the public display name
    comes from mapping.speaker_name, which rename_speaker leaves verbatim now that
    it normalises with allow_fuzzy=False. But local_slug is itself a persistent
    public identifier (publish._upsert_local_people writes it), and a local person
    is by definition NOT on the roster, so a default slug naming a councilmember
    must never be the one offered for a member of the public.
    """
    monkeypatch.setattr("sys.stdin", _FakeTTY())
    prompts: list[str] = []

    def _fake_input(prompt=""):
        prompts.append(prompt)
        if "ocal person" in prompt:
            return "l"          # accept the offer
        if "Slug" in prompt:
            return ""           # accept the OFFERED default, the risky keypress
        return ""               # role -> first default

    monkeypatch.setattr(builtins, "input", _fake_input)
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Jane Smith")}
    run_local._prompt_create_local_person(mappings, "S0", "Jane Smith",
                                          event_kind="council")
    assert mappings["S0"].local_slug == "jane-smith"
    # And the default really did come from the name argument, not the label.
    assert any("jane-smith" in p for p in prompts)


def test_prompt_create_local_person_would_offer_a_roster_slug_if_given_one(monkeypatch):
    """The negative half: hand it a roster canonical name and the offered default
    slug names the councilmember. This is the outcome the call-site change avoids."""
    monkeypatch.setattr("sys.stdin", _FakeTTY())

    def _fake_input(prompt=""):
        if "ocal person" in prompt:
            return "l"
        if "Slug" in prompt:
            return ""
        return ""

    monkeypatch.setattr(builtins, "input", _fake_input)
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Jane Smith")}
    run_local._prompt_create_local_person(mappings, "S0",
                                          "Councilmember Piedmont-Smith",
                                          event_kind="council")
    assert mappings["S0"].local_slug == "councilmember-piedmont-smith"
