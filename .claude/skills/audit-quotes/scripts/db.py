"""Thin DB layer for the audit. Read-only except apply_fixes.py."""
import pathlib, sys
import psycopg2, psycopg2.extras

# The ev-accounts .env lookup is shared with publish-quotes; it has to cope with this skill
# running from a git worktree, where the repo root is not a fixed number of levels up.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "_shared"))
from ev_env import ev_accounts_database_url

def connect():
    return psycopg2.connect(ev_accounts_database_url(__file__), sslmode="require")

# Each quote maps to one race for grouping. When the caller scoped the run to a race, THAT race is
# authoritative: a politician can sit on several rosters (Bass and Raman are on both the LA Mayor
# June primary and the November general), and picking the lowest-sorting race id silently
# mislabelled every quote in the general — which sent fetch_stance to the wrong race and made a
# general-race ranking-question override invisible. Unscoped sweeps keep the lowest-id fallback so
# grouping stays deterministic and a politician is audited once, not once per race.
_RACE_EXPR_SCOPED = "%(race)s"
_RACE_EXPR_LOWEST = """(SELECT rc.race_id::text FROM essentials.race_candidates rc
        WHERE rc.politician_id = q.politician_id ORDER BY rc.race_id LIMIT 1)"""


def race_id_expr(race=None) -> str:
    """The SQL expression that labels a quote with its race."""
    return _RACE_EXPR_SCOPED if race else _RACE_EXPR_LOWEST


def build_scope_sql(race=None) -> str:
    return f"""
SELECT q.id, q.topic_key, q.readrank_selected, q.quote_text, q.deidentified_text,
       q.editor_note, q.source_name, q.source_url,
       q.politician_id::text AS politician_id,
       p.full_name AS candidate,
       q.question_id::text AS question_id,
       rq.question_text AS question_text,
       rq.origin AS question_origin,
       {race_id_expr(race)} AS race_id
FROM essentials.quotes q
JOIN essentials.politicians p ON p.id = q.politician_id
LEFT JOIN essentials.readrank_questions rq ON rq.id = q.question_id
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
        cur.execute(build_scope_sql(race),
                    dict(ids=ids, candidate=candidate, topic=topic, race=race, drafts=include_drafts))
        return [dict(r) for r in cur.fetchall()]

def fetch_stance(conn, politician_id, topic_key, race_id=None):
    """Returns the candidate+topic stance, or None.

    `question_text` is the RESOLVED ranking question (per-race override ?? Compass), which is what
    Read & Rank gates responsiveness against. `compass_question_text` is the canonical Compass
    question and `override_active` says whether an override applied — both let the audit check an
    override for axis-drift. Keyed on politician_id (names collide across the national race set).

    Answers are season-scoped (one row per politician, topic, season), so `value` is the OPEN
    season's answer — the chair Read & Rank and Compass show now — falling back to the most
    recently opened season. `season_name` says which season it came from.

    `chairs` is the wording voters see now, resolved the way ev-accounts `getCompassTopics` does
    (ADR 0006): the open season's pin (`compass_topics_promoted.season_revision_id`) → the latest
    published/superseded revision of that pin's version → its `compass_stance_revisions`, first
    letter capitalized. That can differ from `is_current`. A topic the open season does not ask
    falls back to `compass_stances_current`; `chairs_source` says which. Never the frozen legacy
    `compass_stances` table, whose text no longer matches published revisions."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT t.question_text AS compass_question_text,
                 COALESCE(rtq.question_text, t.question_text) AS question_text,
                 (rtq.question_text IS NOT NULL) AS override_active,
                 sa.value, sa.season_name,
                 COALESCE(oc.chairs, cc.chairs) AS chairs,
                 CASE WHEN oc.chairs IS NOT NULL THEN 'open_season'
                      WHEN cc.chairs IS NOT NULL THEN 'is_current' END AS chairs_source
          FROM inform.compass_topics t
          LEFT JOIN LATERAL (
            -- Mirrors seasonService.servedRevisionLateral + getCompassTopics' stance query.
            SELECT json_agg(json_build_object(
                     'v', sr.value, 'text', upper(left(sr.text, 1)) || substr(sr.text, 2))
                   ORDER BY sr.value) AS chairs
            FROM inform.compass_topics_promoted p
            JOIN LATERAL (
              SELECT e.id
              FROM inform.compass_topic_revisions pin
              JOIN inform.compass_topic_revisions e
                ON e.topic_id = pin.topic_id AND e.version = pin.version
               AND e.status IN ('published', 'superseded')
              WHERE pin.id = p.season_revision_id
              ORDER BY e.revision DESC
              LIMIT 1
            ) eff ON true
            JOIN inform.compass_stance_revisions sr ON sr.topic_revision_id = eff.id
            WHERE p.id = t.id
          ) oc ON true
          LEFT JOIN LATERAL (
            SELECT json_agg(json_build_object(
                     'v', s.value, 'text', upper(left(s.text, 1)) || substr(s.text, 2))
                   ORDER BY s.value) AS chairs
            FROM inform.compass_stances_current s WHERE s.topic_id = t.id
          ) cc ON true
          LEFT JOIN LATERAL (
            SELECT a.value, se.name AS season_name
            FROM inform.politician_answers a
            JOIN inform.seasons se ON se.id = a.season_id
            WHERE a.topic_id=t.id AND a.politician_id=%s::uuid
            ORDER BY (se.status = 'open') DESC, se.opened_at DESC NULLS LAST
            LIMIT 1
          ) sa ON true
          LEFT JOIN essentials.readrank_race_topic_questions rtq
            ON rtq.race_id = %s::uuid AND rtq.topic_key = t.topic_key
          WHERE t.topic_key=%s
        """, (politician_id, race_id, topic_key))
        row = cur.fetchone()
        return dict(row) if row else None
