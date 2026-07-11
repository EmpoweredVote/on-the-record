from scripts.apply_fixes import build_statement

def test_set_field():
    sql, params = build_statement({"kind": "set_field", "id": "q1", "field": "editor_note", "value": "hi"})
    assert "UPDATE essentials.quotes SET editor_note = %s WHERE id = %s::uuid" == sql
    assert params == ["hi", "q1"]

def test_regex_sub_uses_regexp_replace():
    sql, params = build_statement({"kind": "regex_sub", "id": "q1", "field": "quote_text",
                                   "pattern": r"\s*…\s*$", "repl": ""})
    assert "regexp_replace(quote_text" in sql and params[-1] == "q1"

def test_set_live():
    sql, params = build_statement({"kind": "set_live", "id": "q1", "value": False})
    assert "readrank_selected = %s" in sql and params == [False, "q1"]

def test_rejects_unknown_field():
    import pytest
    with pytest.raises(ValueError):
        build_statement({"kind": "set_field", "id": "q1", "field": "politician_id", "value": "x"})

def test_set_field_allows_source_url():
    sql, params = build_statement({"kind":"set_field","id":"q1","field":"source_url","value":"https://x"})
    assert "source_url = %s" in sql and params == ["https://x","q1"]
