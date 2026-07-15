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

# Each quote maps to one race (lowest race_id) for grouping; a politician in multiple races is
# audited once (not once per race) since essentials.race_candidates has no uniqueness on politician_id.
SCOPE_SQL = """
SELECT q.id, q.topic_key, q.readrank_selected, q.quote_text, q.deidentified_text,
       q.editor_note, q.source_name, q.source_url,
       q.politician_id::text AS politician_id,
       p.full_name AS candidate,
       (SELECT rc.race_id::text FROM essentials.race_candidates rc
        WHERE rc.politician_id = q.politician_id ORDER BY rc.race_id LIMIT 1) AS race_id
FROM essentials.quotes q
JOIN essentials.politicians p ON p.id = q.politician_id
WHERE (%(ids)s IS NULL OR q.id = ANY(%(ids)s::uuid[]))
  AND (%(candidate)s IS NULL OR lower(p.full_name) = lower(%(candidate)s))
  AND (%(topic)s IS NULL OR q.topic_key = %(topic)s)
  AND (%(race)s IS NULL OR EXISTS (
        SELECT 1 FROM essentials.race_candidates rc2
        WHERE rc2.politician_id = q.politician_id AND rc2.race_id::text = %(race)s))
  AND (%(drafts)s OR q.readrank_selected = true)
ORDER BY race_id, q.topic_key, q.readrank_selected DESC
"""

def fetch_rows(conn, ids=None, candidate=None, topic=None, race=None, include_drafts=False):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(SCOPE_SQL, dict(ids=ids, candidate=candidate, topic=topic, race=race, drafts=include_drafts))
        return [dict(r) for r in cur.fetchall()]

def fetch_stance(conn, politician_id, topic_key, race_id=None):
    """Returns the candidate+topic stance, or None.

    `question_text` is the RESOLVED ranking question (per-race override ?? Compass), which is what
    Read & Rank gates responsiveness against. `compass_question_text` is the canonical Compass
    question and `override_active` says whether an override applied — both let the audit check an
    override for axis-drift. Keyed on politician_id (names collide across the national race set)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT t.question_text AS compass_question_text,
                 COALESCE(rtq.question_text, t.question_text) AS question_text,
                 (rtq.question_text IS NOT NULL) AS override_active,
                 (SELECT a.value FROM inform.politician_answers a
                  WHERE a.topic_id=t.id AND a.politician_id=%s::uuid) AS value,
                 (SELECT json_agg(json_build_object('v', s.value, 'text', s.text) ORDER BY s.value)
                  FROM inform.compass_stances s WHERE s.topic_id=t.id) AS chairs
          FROM inform.compass_topics t
          LEFT JOIN essentials.readrank_race_topic_questions rtq
            ON rtq.race_id = %s::uuid AND rtq.topic_key = t.topic_key
          WHERE t.topic_key=%s
        """, (politician_id, race_id, topic_key))
        row = cur.fetchone()
        return dict(row) if row else None
