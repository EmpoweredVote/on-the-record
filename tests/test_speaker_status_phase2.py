from __future__ import annotations
from src.models import Segment, SpeakerMapping
from src.review import link_to_unidentified_handle


def test_link_to_unidentified_handle_reuses_existing_slug():
    segs = [Segment(0, 0, 5, "S0", "hi")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    link_to_unidentified_handle(mappings, segs, "S0",
                                handle_key="local:unidentified-mA-s3",
                                display_name="Unidentified Speaker")
    m = mappings["S0"]
    assert m.local_slug == "unidentified-mA-s3"   # strips the 'local:' prefix
    assert m.speaker_status == "unidentified"
    assert m.politician_slug is None
    assert m.id_method == "human_confirmed"
    assert segs[0].speaker_name == "Unidentified Speaker"
