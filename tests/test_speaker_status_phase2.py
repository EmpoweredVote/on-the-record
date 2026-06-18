from __future__ import annotations
from src.models import Segment, SpeakerMapping
from src.review import link_to_unidentified_handle


def test_link_to_unidentified_handle_reuses_existing_slug():
    segs = [Segment(0, 0, 5, "S0", "hi")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0", local_role="staff")}
    link_to_unidentified_handle(mappings, segs, "S0",
                                handle_key="local:unidentified-mA-s3",
                                display_name="Unidentified Speaker")
    m = mappings["S0"]
    assert m.local_slug == "unidentified-mA-s3"   # strips the 'local:' prefix
    assert m.speaker_status == "unidentified"
    assert m.politician_slug is None
    assert m.local_role is None
    assert m.id_method == "human_confirmed"
    assert segs[0].speaker_name == "Unidentified Speaker"


def test_promote_merges_handle_into_target_identity():
    import numpy as np
    from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord, promote_unidentified_handle
    db = ProfileDB(profiles={
        "local:unidentified-mA-s3": StoredProfile(
            speaker_id="local:unidentified-mA-s3", display_name="Unidentified Speaker",
            embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "mA")], meetings_seen=["mA"]),
        "essentials:jane-adams": StoredProfile(
            speaker_id="essentials:jane-adams", display_name="Jane Adams",
            embeddings=[EmbeddingRecord(np.array([0.0, 1.0]), "mB")], meetings_seen=["mB"],
            politician_slug="jane-adams", politician_id="uuid-ja"),
    })
    ok = promote_unidentified_handle(db, "local:unidentified-mA-s3", "essentials:jane-adams")
    assert ok is True
    assert "local:unidentified-mA-s3" not in db.profiles          # handle removed
    target = db.profiles["essentials:jane-adams"]
    assert {r.meeting_id for r in target.embeddings} == {"mA", "mB"}  # embeddings carried over


def test_promote_creates_target_when_absent():
    import numpy as np
    from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord, promote_unidentified_handle
    db = ProfileDB(profiles={
        "local:unidentified-mA-s3": StoredProfile(
            speaker_id="local:unidentified-mA-s3", display_name="Unidentified Speaker",
            embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "mA")], meetings_seen=["mA"]),
    })
    ok = promote_unidentified_handle(db, "local:unidentified-mA-s3", "essentials:new-person",
                                     display_name="New Person", politician_id="uuid-np")
    assert ok is True
    assert "local:unidentified-mA-s3" not in db.profiles
    prof = db.profiles["essentials:new-person"]
    assert {r.meeting_id for r in prof.embeddings} == {"mA"}
    assert prof.politician_slug == "new-person"   # derived from essentials:<slug> key
    assert prof.politician_id == "uuid-np"
    assert prof.display_name == "New Person"       # real name, not the placeholder


def test_promote_explicit_slug_overrides_key_derivation():
    import numpy as np
    from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord, promote_unidentified_handle
    db = ProfileDB(profiles={
        "local:unidentified-mA-s3": StoredProfile(
            speaker_id="local:unidentified-mA-s3", display_name="Unidentified Speaker",
            embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "mA")], meetings_seen=["mA"]),
    })
    ok = promote_unidentified_handle(db, "local:unidentified-mA-s3", "local:custom",
                                     display_name="Jane Q", politician_slug="jane-q", politician_id="u1")
    assert ok is True
    prof = db.profiles["local:custom"]
    assert prof.politician_slug == "jane-q" and prof.politician_id == "u1"
    assert prof.display_name == "Jane Q"


def test_promote_returns_false_for_missing_handle():
    from src.enroll import ProfileDB, promote_unidentified_handle
    db = ProfileDB()
    assert promote_unidentified_handle(db, "local:nope", "essentials:x") is False
