"""Tests for the synthesis-stage BLIND A/B pairing/randomization/answer-key
logic (src.summary_ab_pairing)."""
from __future__ import annotations

import hashlib

from src.summary_ab_pairing import (
    assign_visible_ids,
    build_answer_key,
    build_pair,
    build_visible_id_index,
    pair_rng,
    pair_seed,
    render_pair_markdown,
)


# --- pair_seed / pair_rng -----------------------------------------

def test_pair_seed_deterministic():
    assert pair_seed("2026-02-25-council", "modelA") == pair_seed("2026-02-25-council", "modelA")


def test_pair_seed_differs_for_different_meeting_ids():
    assert pair_seed("meeting-a", "modelA") != pair_seed("meeting-b", "modelA")


def test_pair_seed_differs_for_different_models_same_meeting():
    # The whole point of the fix: two models compared against the SAME
    # meeting must not share a seed (that would put the identical reference
    # text in the same option slot for both, de-blinding a multi-model run).
    assert pair_seed("meeting-a", "modelA") != pair_seed("meeting-a", "modelB")


def test_pair_seed_matches_known_sha256_derivation():
    # Pins the derivation (sha256 of "meeting_id::model" hexdigest, first 16
    # hex chars as an int) so a future edit can't silently swap in Python's
    # randomized-per-process hash() and still pass the determinism test above.
    expected = int(hashlib.sha256(b"m1::modelA").hexdigest()[:16], 16)
    assert pair_seed("m1", "modelA") == expected


def test_pair_rng_reproducible_sequence():
    r1 = pair_rng("meeting-1", "modelA")
    r2 = pair_rng("meeting-1", "modelA")
    seq1 = [r1.random() for _ in range(5)]
    seq2 = [r2.random() for _ in range(5)]
    assert seq1 == seq2


def test_pair_rng_differs_across_meetings():
    r1 = pair_rng("meeting-1", "modelA")
    r2 = pair_rng("meeting-2", "modelA")
    assert [r1.random() for _ in range(3)] != [r2.random() for _ in range(3)]


def test_pair_rng_differs_across_models_same_meeting():
    r1 = pair_rng("meeting-1", "modelA")
    r2 = pair_rng("meeting-1", "modelB")
    assert [r1.random() for _ in range(3)] != [r2.random() for _ in range(3)]


# --- build_pair -----------------------------------------------------------

def test_build_pair_deterministic_for_fixed_seed():
    pair1 = build_pair("m1", "executive_summary", "deepseek/deepseek-chat-v3.1",
                        "CANDIDATE TEXT", "REFERENCE TEXT", pair_rng("m1", "deepseek/deepseek-chat-v3.1"))
    pair2 = build_pair("m1", "executive_summary", "deepseek/deepseek-chat-v3.1",
                        "CANDIDATE TEXT", "REFERENCE TEXT", pair_rng("m1", "deepseek/deepseek-chat-v3.1"))
    assert pair1 == pair2


def test_build_pair_places_texts_consistently_with_answer():
    rng = pair_rng("m1", "modelX")
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
    rng = pair_rng("m1", "super-secret-model-name")
    pair = build_pair("m1", "executive_summary", "super-secret-model-name", "CAND", "REF", rng)
    reviewer_blob = str(pair["reviewer"])
    assert "super-secret-model-name" not in reviewer_blob
    assert "candidate" not in reviewer_blob.lower()
    assert "reference" not in reviewer_blob.lower()


def test_build_pair_advances_rng_by_exactly_one_draw():
    rng_a = pair_rng("m1", "modelA")
    rng_b = pair_rng("m1", "modelA")
    build_pair("m1", "k", "modelA", "CAND", "REF", rng_a)
    rng_b.random()  # manually consume one draw
    # Both rngs should now be in the same state.
    assert rng_a.random() == rng_b.random()


def test_build_pair_both_orders_reachable_across_different_pair_keys():
    # Sequential draws from one (meeting, model)'s rng should produce both
    # orderings over enough pairs (sanity check it's not degenerate/constant).
    rng = pair_rng("some-meeting-id", "modelA")
    orders = set()
    for i in range(20):
        pair = build_pair("some-meeting-id", f"pair_{i}", "modelA", "CAND", "REF", rng)
        orders.add(pair["answer"]["option_1_is"])
    assert orders == {"candidate", "reference"}


# --- assign_visible_ids ----------------------------------------------------

def test_assign_visible_ids_sequential_default_start():
    rng = pair_rng("m1", "modelA")
    pairs = [build_pair("m1", f"k{i}", "modelA", "C", "R", rng) for i in range(3)]
    tagged = assign_visible_ids(pairs)
    assert [p["visible_id"] for p in tagged] == ["pair-1", "pair-2", "pair-3"]


def test_assign_visible_ids_custom_start_keeps_numbering_contiguous():
    rng = pair_rng("m1", "modelA")
    pairs = [build_pair("m1", f"k{i}", "modelA", "C", "R", rng) for i in range(2)]
    tagged = assign_visible_ids(pairs, start=8)
    assert [p["visible_id"] for p in tagged] == ["pair-8", "pair-9"]


def test_assign_visible_ids_does_not_mutate_input():
    rng = pair_rng("m1", "modelA")
    pairs = [build_pair("m1", "k0", "modelA", "C", "R", rng)]
    assign_visible_ids(pairs)
    assert "visible_id" not in pairs[0]


def test_assign_visible_ids_carries_visible_id_into_markdown_leak_check():
    # (sanity link to render_pair_markdown tests below)
    rng = pair_rng("m1", "modelA")
    pairs = [build_pair("m1", "k0", "modelA", "C", "R", rng)]
    tagged = assign_visible_ids(pairs)
    assert tagged[0]["visible_id"] == "pair-1"


# --- build_answer_key / build_visible_id_index -----------------------------

def test_build_answer_key_groups_by_meeting_and_pair():
    p1 = build_pair("m1", "executive_summary", "modelA", "C", "R", pair_rng("m1", "modelA"))
    p2 = build_pair("m1", "section_0:Intro", "modelA", "C2", "R2", pair_rng("m1", "modelA"))
    p3 = build_pair("m2", "executive_summary", "modelB", "C3", "R3", pair_rng("m2", "modelB"))

    key = build_answer_key([p1, p2, p3])
    assert set(key) == {"m1", "m2"}
    assert set(key["m1"]) == {"executive_summary", "section_0:Intro"}
    assert key["m1"]["executive_summary"]["candidate_model"] == "modelA"
    assert key["m2"]["executive_summary"]["candidate_model"] == "modelB"


def test_build_answer_key_includes_visible_id_when_present():
    p1 = build_pair("m1", "executive_summary", "modelA", "C", "R", pair_rng("m1", "modelA"))
    tagged = assign_visible_ids([p1])
    key = build_answer_key(tagged)
    assert key["m1"]["executive_summary"]["visible_id"] == "pair-1"


def test_build_answer_key_empty_input():
    assert build_answer_key([]) == {}


def test_build_visible_id_index_maps_visible_id_to_model_identity():
    p1 = build_pair("m1", "executive_summary", "modelA", "C", "R", pair_rng("m1", "modelA"))
    p2 = build_pair("m2", "executive_summary", "modelB", "C3", "R3", pair_rng("m2", "modelB"))
    tagged = assign_visible_ids([p1, p2])
    index = build_visible_id_index(tagged)
    assert set(index) == {"pair-1", "pair-2"}
    assert index["pair-1"]["candidate_model"] == "modelA"
    assert index["pair-1"]["meeting_id"] == "m1"
    assert index["pair-2"]["candidate_model"] == "modelB"


def test_build_visible_id_index_skips_untagged_pairs():
    p1 = build_pair("m1", "executive_summary", "modelA", "C", "R", pair_rng("m1", "modelA"))
    assert build_visible_id_index([p1]) == {}


def test_build_visible_id_index_empty_input():
    assert build_visible_id_index([]) == {}


# --- render_pair_markdown --------------------------------------------------

def test_render_pair_markdown_contains_both_options_and_title():
    rng = pair_rng("m1", "modelA")
    pair = build_pair("m1", "executive_summary", "modelA", "CAND TEXT", "REF TEXT", rng)
    md = render_pair_markdown(pair, "Executive Summary")
    assert "Executive Summary" in md
    assert "CAND TEXT" in md
    assert "REF TEXT" in md
    assert "Option 1" in md and "Option 2" in md


def test_render_pair_markdown_never_leaks_model_or_role():
    rng = pair_rng("m1", "super-secret-model")
    pair = build_pair("m1", "executive_summary", "super-secret-model", "CAND TEXT", "REF TEXT", rng)
    md = render_pair_markdown(pair, "Executive Summary")
    assert "super-secret-model" not in md
    assert "candidate" not in md.lower()
    assert "reference" not in md.lower()


def test_render_pair_markdown_includes_visible_id_when_given():
    rng = pair_rng("m1", "modelA")
    pair = build_pair("m1", "executive_summary", "modelA", "CAND TEXT", "REF TEXT", rng)
    md = render_pair_markdown(pair, "Executive Summary", visible_id="pair-42")
    assert "pair-42" in md


def test_render_pair_markdown_omits_visible_id_when_absent():
    rng = pair_rng("m1", "modelA")
    pair = build_pair("m1", "executive_summary", "modelA", "CAND TEXT", "REF TEXT", rng)
    md = render_pair_markdown(pair, "Executive Summary")
    assert "pair-" not in md
