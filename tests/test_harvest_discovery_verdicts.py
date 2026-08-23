import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from harvest_discovery_verdicts import merge_examples, to_example

ROW = ("youtube:abc12345678", "Full debate", "All four candidates", "KXAN",
       3480, "2026-07-01", "ingested", None, "ingest",
       "TX · U.S. Senate · General · 2026", ["Ana Ruiz", "Maria Delgado"])


def test_to_example_approved_is_gold_true():
    ex = to_example(ROW)
    assert ex == {
        "title": "Full debate", "description": "All four candidates",
        "channel": "KXAN", "duration_seconds": 3480,
        "race_label": "TX · U.S. Senate · General · 2026",
        "roster": ["Ana Ruiz", "Maria Delgado"],
        "gold_relevant": True, "source_key": "youtube:abc12345678",
        "published_at": "2026-07-01", "status": "ingested",
        "status_reason": None, "route": "ingest",
    }


def test_to_example_relevance_rejects_are_gold_false():
    for reason in ("clip-not-original", "wrong-person", "tier-5"):
        row = ROW[:6] + ("rejected", reason) + ROW[8:]
        assert to_example(row)["gold_relevant"] is False


def test_to_example_non_relevance_rejects_are_skipped():
    for reason in ("stale", "duplicate", "other"):
        row = ROW[:6] + ("rejected", reason) + ROW[8:]
        assert to_example(row) is None


def test_merge_examples_dedupes_on_source_key_existing_wins():
    existing = [{"source_key": "youtube:abc12345678", "gold_relevant": False,
                 "title": "hand-corrected"}]
    merged = merge_examples(existing, [to_example(ROW), to_example(ROW)])
    assert len(merged) == 1
    assert merged[0]["title"] == "hand-corrected"
