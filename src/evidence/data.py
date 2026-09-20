from __future__ import annotations
import os
import pathlib
import re

_DEFAULT_ENV = pathlib.Path.home() / "Documents/GitHub/ev-accounts/backend/.env"


def _parse(text: str) -> "str | None":
    m = re.search(r'^DATABASE_URL\s*=\s*["\']?([^"\'\n]+)', text, re.M)
    return m.group(1) if m else None


def database_url(env_file: "str | None" = None) -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    path = pathlib.Path(env_file) if env_file else _DEFAULT_ENV
    if not path.is_file():
        raise FileNotFoundError(f"no env file at {path}")
    url = _parse(path.read_text())
    if not url:
        raise ValueError(f"no DATABASE_URL in {path}")
    return url


def connect(env_file=None):
    import psycopg2
    conn = psycopg2.connect(database_url(env_file))
    conn.set_session(readonly=True, autocommit=True)
    return conn


def fetch_roster(conn, race_id) -> list:
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT rc.politician_id, "
        "COALESCE(p.full_name, TRIM(COALESCE(p.preferred_name,p.first_name)||' '||p.last_name)) name "
        "FROM essentials.race_candidates rc JOIN essentials.politicians p "
        "ON p.id = rc.politician_id WHERE rc.race_id = %s ORDER BY name",
        (race_id,))
    return [dict(r) for r in cur.fetchall()]


def fetch_cited_sources(conn, politician_id) -> list:
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT unnest(sources) FROM inform.politician_context "
        "WHERE politician_id = %s", (politician_id,))
    return [(r[0], None) for r in cur.fetchall() if r[0]]


def load_topic_keys(conn) -> set:
    cur = conn.cursor()
    cur.execute("SELECT lower(topic_key) FROM inform.compass_topics")
    return {r[0] for r in cur.fetchall()}
