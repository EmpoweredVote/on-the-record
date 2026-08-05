"""Cursor- and provider-injected tests for the one-shot pending re-classify."""
from src.discovery import reclassify


class _FakeProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.prompts.append(prompt)
        return self.replies.pop(0)


class _FakeCursor:
    def __init__(self, rows_by_query=None):
        self.executed = []
        self._rows = rows_by_query or {}

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._rows.get("fetchall", [])


_ROW = ("11111111-1111-1111-1111-111111111111",  # id
        "https://www.youtube.com/watch?v=abc12345678", "STARS Town Hall",
        "Candidates take citizen questions", "Civic Media", 5400,
        "2026-07-30", "22222222-2222-2222-2222-222222222222",
        "WI Governor (primary)", 3)  # race_label, old_tier


def test_reclassify_row_updates_tier_kind_confidence_why_only():
    provider = _FakeProvider(['{"relevant": true, "confidence": 0.9,'
                              ' "event_kind": "forum", "source_tier": 1,'
                              ' "original_vs_clip": "original",'
                              ' "route": "ingest", "why": "citizen questions"}'])
    cur = _FakeCursor({"fetchall": [("Alice Example",), ("Bob Sample",)]})
    old_tier, new_tier = reclassify.reclassify_row(cur, provider, _ROW)
    assert (old_tier, new_tier) == (3, 1)
    update_sql = cur.executed[-1][0]
    assert "set source_tier_guess" in update_sql
    for untouched in ("status", "route", "discovered_via"):
        assert untouched not in update_sql
    assert "Alice Example" in provider.prompts[0]


def test_reclassify_row_skips_on_parse_failure():
    provider = _FakeProvider(["not json"])
    cur = _FakeCursor({"fetchall": []})
    old_tier, new_tier = reclassify.reclassify_row(cur, provider, _ROW)
    assert (old_tier, new_tier) == (3, None)
    assert all("update" not in sql for sql, _ in cur.executed)
