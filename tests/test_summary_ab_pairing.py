"""Tests for the synthesis-stage BLIND A/B pairing/randomization/answer-key
logic (src.summary_ab_pairing)."""
from __future__ import annotations

from src.summary_ab_pairing import (
    build_answer_key,
    build_pair,
    meeting_rng,
    meeting_seed,
    render_pair_markdown,
)


# --- meeting_seed / meeting_rng -----------------------------------------

def test_meeting_seed_deterministic():
    assert meeting_seed("2026-02-25-council") == meeting_seed("2026-02-25-council")


def test_meeting_seed_differs_for_different_ids():
    assert meeting_seed("meeting-a") != meeting_seed("meeting-b")


def test_meeting_seed_matches_known_sha256_derivation():
    # Pins the derivation (sha256 hexdigest, first 16 hex chars as an int) so
    # a future edit can't silently swap in Python's randomized-per-process
    # hash() and still pass the determinism test above.
    import hashlib
    expected = int(hashlib.sha256(b"x").hexdigest()[:16], 16)
    assert meeting_seed("x") == expected


def test_meeting_rng_reproducible_sequence():
    r1 = meeting_rng("meeting-1")
    r2 = meeting_rng("meeting-1")
    seq1 = [r1.random() for _ in range(5)]
    seq2 = [r2.random() for _ in range(5)]
    assert seq1 == seq2


def test_meeting_rng_differs_across_meetings():
    r1 = meeting_rng("meeting-1")
    r2 = meeting_rng("meeting-2")
    assert [r1.random() for _ in range(3)] != [r2.random() for _ in range(3)]


# --- build_pair -----------------------------------------------------------

def test_build_pair_deterministic_for_fixed_seed():
    pair1 = build_pair("m1", "executive_summary", "deepseek/deepseek-chat-v3.1",
                        "CANDIDATE TEXT", "REFERENCE TEXT", meeting_rng("m1"))
    pair2 = build_pair("m1", "executive_summary", "deepseek/deepseek-chat-v3.1",
                        "CANDIDATE TEXT", "REFERENCE TEXT", meeting_rng("m1"))
    assert pair1 == pair2


def test_build_pair_places_texts_consistently_with_answer():
    rng = meeting_rng("m1")
    pair = build_pair("m1", "executive_summary", "modelX", "CAND", "REF", rng)
    if pair["answer"]["option_1_is"] == "candidate":
        assert pair["reviewer"]["option_1_text"] == "CAND"
        assert pair["reviewer"]["option_2_text"] == "REF"
        assert pair["answer"]["option_2_is"] == "reference"
    else:
        assert pair["reviewer"]["option_1_text"] == "REF"
        assert pair["reviewer"]["option_2_text"] == "CAND"
        assert pair["answer"]["option_2_is"] == "candidate"


def test_build_pair_reviewer_never_reveals_model_or_role():
    rng = meeting_rng("m1")
    pair = build_pair("m1", "executive_summary", "super-secret-model-name", "CAND", "REF", rng)
    reviewer_blob = str(pair["reviewer"])
    assert "super-secret-model-name" not in reviewer_blob
    assert "candidate" not in reviewer_blob.lower()
    assert "reference" not in reviewer_blob.lower()


def test_build_pair_advances_rng_by_exactly_one_draw():
    rng_a = meeting_rng("m1")
    rng_b = meeting_rng("m1")
    build_pair("m1", "k", "model", "CAND", "REF", rng_a)
    rng_b.random()  # manually consume one draw
    # Both rngs should now be in the same state.
    assert rng_a.random() == rng_b.random()


def test_build_pair_both_orders_reachable_across_different_pair_keys():
    # Sequential draws from one meeting's rng should produce both orderings
    # over enough pairs (sanity check it's not degenerate/constant).
    rng = meeting_rng("some-meeting-id")
    orders = set()
    for i in range(20):
        pair = build_pair("some-meeting-id", f"pair_{i}", "model", "CAND", "REF", rng)
        orders.add(pair["answer"]["option_1_is"])
    assert orders == {"candidate", "reference"}


# --- build_answer_key ------------------------------------------------------

def test_build_answer_key_groups_by_meeting_and_pair():
    rng = meeting_rng("m1")
    p1 = build_pair("m1", "executive_summary", "modelA", "C", "R", rng)
    p2 = build_pair("m1", "section_0:Intro", "modelA", "C2", "R2", rng)
    rng2 = meeting_rng("m2")
    p3 = build_pair("m2", "executive_summary", "modelB", "C3", "R3", rng2)

    key = build_answer_key([p1, p2, p3])
    assert set(key) == {"m1", "m2"}
    assert set(key["m1"]) == {"executive_summary", "section_0:Intro"}
    assert key["m1"]["executive_summary"]["candidate_model"] == "modelA"
    assert key["m2"]["executive_summary"]["candidate_model"] == "modelB"


def test_build_answer_key_empty_input():
    assert build_answer_key([]) == {}


# --- render_pair_markdown --------------------------------------------------

def test_render_pair_markdown_contains_both_options_and_title():
    rng = meeting_rng("m1")
    pair = build_pair("m1", "executive_summary", "modelA", "CAND TEXT", "REF TEXT", rng)
    md = render_pair_markdown(pair, "Executive Summary")
    assert "Executive Summary" in md
    assert "CAND TEXT" in md
    assert "REF TEXT" in md
    assert "Option 1" in md and "Option 2" in md


def test_render_pair_markdown_never_leaks_model_or_role():
    rng = meeting_rng("m1")
    pair = build_pair("m1", "executive_summary", "super-secret-model", "CAND TEXT", "REF TEXT", rng)
    md = render_pair_markdown(pair, "Executive Summary")
    assert "super-secret-model" not in md
    assert "candidate" not in md.lower()
    assert "reference" not in md.lower()
