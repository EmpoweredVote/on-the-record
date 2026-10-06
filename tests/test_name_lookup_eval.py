from __future__ import annotations

from src.name_suggestion_eval import score_lookup_rows


def test_score_lookup_rows():
    rows = [
        {"gold": "Bob Costello", "spoken": "Bob Gasillo", "looked_up": "Bob Costello", "verified": True, "status": "verified"},
        {"gold": "Rachael Sample", "spoken": "Rachel Sample", "looked_up": "Rachel Sample", "verified": True, "status": "verified"},
        {"gold": "Ann Lee", "spoken": "Ann Lee", "looked_up": None, "verified": False, "status": "not_found"},
        {"gold": "Jo Fox", "spoken": "Joe Fox", "looked_up": "Joe Fox", "verified": False, "status": "not_verified"},
        {"gold": "Kim Wu", "spoken": "Kim Woo", "looked_up": None, "verified": False, "status": "unavailable"},
        {"gold": "Al Ray", "spoken": "Al Ray", "looked_up": None, "verified": False, "status": "failed"},
    ]
    s = score_lookup_rows(rows)
    assert s["n"] == 6 and s["spoken_exact"] == 2 and s["final_exact"] == 3
    assert s["verified"] == 2 and s["verified_exact"] == 1 and s["verified_precision"] == 0.5
    assert s["not_found"] == 1 and s["unavailable"] == 1 and s["failed"] == 1
