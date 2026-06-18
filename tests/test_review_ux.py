# tests/test_review_ux.py
from __future__ import annotations
from src.models import SpeakerMapping
from src.review import identity_label


def test_identity_label_for_each_status():
    assert identity_label(SpeakerMapping("S0", "Jane", politician_slug="jane-adams")) == "essentials:jane-adams"
    assert identity_label(SpeakerMapping("S0", "Bob", local_slug="bob-smith")) == "local:bob-smith"
    assert identity_label(SpeakerMapping("S0", "Unknown", local_slug="unidentified-m-s0",
                                         speaker_status="unidentified")) == "unidentified"
    assert identity_label(SpeakerMapping("S0", "Music", speaker_status="non_speaker")) == "non-speaker"
    # status wins over a (stale) slug — pins the precedence ordering
    assert identity_label(SpeakerMapping("S0", "X", politician_slug="p",
                                         speaker_status="unidentified")) == "unidentified"
    assert identity_label(SpeakerMapping("S0", "X", politician_slug="p",
                                         speaker_status="non_speaker")) == "non-speaker"
    assert identity_label(SpeakerMapping("S0", "Someone")) == "unlinked"
    assert identity_label(None) == "unlinked"
