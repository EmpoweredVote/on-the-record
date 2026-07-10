from scripts.models import Finding
from scripts.report import render

def f(**kw):
    d = dict(check_id="note-missing", level="quote", principle="editor_note required",
             severity="high", fix_class="mechanical", what="empty", suggested_fix="write it",
             quote_id="q1", topic_key="housing", race_id="r1", candidate="A")
    d.update(kw); return Finding(**d)

def test_render_groups_by_race_and_counts():
    md = render([f(), f(severity="low", fix_class="guided", check_id="note-too-long")], scope_label="all races")
    assert "# Quote Audit — all races" in md
    assert "2 findings" in md
    assert "race r1" in md
    assert "high" in md and "mechanical" in md

def test_render_empty():
    md = render([], scope_label="CA Governor")
    assert "No findings" in md
