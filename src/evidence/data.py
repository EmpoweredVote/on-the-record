from __future__ import annotations
import os
import pathlib
import re
from dataclasses import dataclass

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


@dataclass
class TranscriptSource:
    meeting_id: str
    source_url: str
    video_url: str | None
    title: str | None
    event_kind: str | None
    full_text: str
    segments: list  # list[(start_time_seconds: float, text: str)] in order


def fetch_transcript_sources(conn, politician_id) -> list:
    """Assemble one TranscriptSource per meeting where this politician is a linked
    speaker: the full speaker-labeled transcript (so the own-words extractor can
    pull only their statements, with the eliciting question as context), plus the
    ordered (start_time, text) segments for timestamp lookup."""
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT m.id, sp.display_name, m.source_url, m.video_url, m.title, m.event_kind "
        "FROM meetings.speakers sp JOIN meetings.meetings m ON m.id = sp.meeting_id "
        "WHERE sp.politician_id = %s ORDER BY m.id", (politician_id,))
    meetings = cur.fetchall()
    out = []
    for mid, _display_name, source_url, video_url, title, event_kind in meetings:
        cur2 = conn.cursor()
        cur2.execute(
            "SELECT segment_index, start_time, speaker_name, text "
            "FROM meetings.segments WHERE meeting_id = %s ORDER BY segment_index", (mid,))
        rows = cur2.fetchall()
        lines, segs = [], []
        for _idx, start, speaker, text in rows:
            text = (text or "").strip()
            if not text:
                continue
            lines.append(f"{speaker or 'Speaker'}: {text}")
            segs.append((float(start) if start is not None else 0.0, text))
        out.append(TranscriptSource(
            meeting_id=str(mid), source_url=source_url or "", video_url=video_url,
            title=title, event_kind=event_kind, full_text="\n".join(lines), segments=segs))
    return out
