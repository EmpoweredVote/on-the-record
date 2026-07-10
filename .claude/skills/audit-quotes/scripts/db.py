"""Thin DB layer for the audit. Read-only except apply_fixes.py."""
import os, re, pathlib
import psycopg2, psycopg2.extras

def _database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    here = pathlib.Path(__file__).resolve()
    repo = here.parents[4]  # .../on-the-record
    env = repo.parent / "ev-accounts" / "backend" / ".env"
    for line in env.read_text().splitlines():
        m = re.match(r'\s*DATABASE_URL\s*=\s*"?([^"\n]+)"?', line)
        if m:
            return m.group(1)
    raise RuntimeError("DATABASE_URL not found in env or ev-accounts/backend/.env")

def connect():
    return psycopg2.connect(_database_url(), sslmode="require")

SCOPE_SQL = """
SELECT q.id, q.topic_key, q.readrank_selected, q.quote_text, q.deidentified_text,
       q.editor_note, q.source_name, q.source_url,
       p.full_name AS candidate, rc.race_id::text AS race_id
FROM essentials.quotes q
JOIN essentials.politicians p ON p.id = q.politician_id
LEFT JOIN essentials.race_candidates rc ON rc.politician_id = q.politician_id
WHERE (%(ids)s IS NULL OR q.id = ANY(%(ids)s))
  AND (%(candidate)s IS NULL OR lower(p.full_name) = lower(%(candidate)s))
  AND (%(topic)s IS NULL OR q.topic_key = %(topic)s)
  AND (%(drafts)s OR q.readrank_selected = true)
ORDER BY race_id, q.topic_key, q.readrank_selected DESC
"""

def fetch_rows(conn, ids=None, candidate=None, topic=None, include_drafts=False):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(SCOPE_SQL, dict(ids=ids, candidate=candidate, topic=topic, drafts=include_drafts))
        return [dict(r) for r in cur.fetchall()]

def fetch_stance(conn, candidate, topic_key):
    """Returns {'question_text':..., 'value': float|None, 'chairs': [{'v','text'}...]} or None."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT t.question_text,
                 (SELECT a.value FROM inform.politician_answers a
                  JOIN essentials.politicians p ON p.id=a.politician_id
                  WHERE a.topic_id=t.id AND lower(p.full_name)=lower(%s)) AS value,
                 (SELECT json_agg(json_build_object('v', s.value, 'text', s.text) ORDER BY s.value)
                  FROM inform.compass_stances s WHERE s.topic_id=t.id) AS chairs
          FROM inform.compass_topics t WHERE t.topic_key=%s
        """, (candidate, topic_key))
        row = cur.fetchone()
        return dict(row) if row else None
