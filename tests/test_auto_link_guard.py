from __future__ import annotations

import run_local
from src.models import SpeakerMapping


def test_prompt_link_skips_when_politician_id_set_slug_null(monkeypatch):
    # An id-linked (slug-null) speaker must be treated as already linked: the
    # prompt returns immediately without searching or prompting.
    called = {"search": 0}
    monkeypatch.setattr("src.essentials_client.search_politicians",
                        lambda *a, **k: called.__setitem__("search", called["search"] + 1) or [])
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Steve Hilton",
                                     politician_id="uuid-h", politician_slug=None)}
    run_local._prompt_link_politician(mappings, "S0", "Steve Hilton")
    assert called["search"] == 0   # short-circuited; never searched/prompted
