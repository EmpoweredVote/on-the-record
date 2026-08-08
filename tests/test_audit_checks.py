import importlib.util
from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parents[1] / ".claude/skills/audit-quotes/scripts/db.py"
_spec = importlib.util.spec_from_file_location("audit_db2", _SPEC_PATH)
audit_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_db)


def test_scope_sql_selects_question_columns():
    """Per-set checks need the question a quote answers, not just its topic."""
    sql = audit_db.build_scope_sql(race=None)
    assert "q.question_id" in sql
    assert "rq.question_text" in sql
    assert "rq.origin" in sql


def test_scope_sql_left_joins_questions_so_unattached_quotes_survive():
    """question_id is nullable; an unattached quote must still be audited."""
    sql = audit_db.build_scope_sql(race=None)
    assert "LEFT JOIN essentials.readrank_questions rq" in sql
