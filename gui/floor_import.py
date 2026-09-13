"""Import cloud-processed House-floor sessions onto the Mac for local review."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FloorSession:
    slug: str
    date: str | None
    gate_verdict: str | None
    gate_coverage: float | None
    is_local: bool


def _query_draft_floor_rows() -> list[dict]:
    """Draft floor meetings from the DB, newest first. [] if DB not configured."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return []
    import psycopg2  # local import: keeps import-time hermetic for tests
    from psycopg2.extras import RealDictCursor
    sql = (
        "SELECT slug, date::text AS date, "
        "  processing_metadata->>'gate_verdict' AS gate_verdict, "
        "  (processing_metadata->>'gate_coverage')::float AS gate_coverage "
        "FROM meetings.meetings "
        "WHERE status = 'draft' AND slug LIKE %s "
        "ORDER BY date DESC"
    )
    with psycopg2.connect(dsn) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, ("%-house-floor",))
            return [dict(r) for r in cur.fetchall()]


def list_floor_sessions(meetings_dir: Path) -> list[FloorSession]:
    """Draft House-floor sessions from the DB, marking which are already local
    (a meetings_dir/<slug> directory exists). [] if the DB is not configured."""
    out: list[FloorSession] = []
    for r in _query_draft_floor_rows():
        slug = r["slug"]
        out.append(FloorSession(
            slug=slug, date=r.get("date"),
            gate_verdict=r.get("gate_verdict"),
            gate_coverage=r.get("gate_coverage"),
            is_local=(meetings_dir / slug).is_dir(),
        ))
    return out


class FloorImportError(RuntimeError):
    pass


class FloorAlreadyLocalError(FloorImportError):
    pass


_WORKFLOW = "house-floor-weekly.yml"
_ARTIFACT = "house-floor-sessions"


def _download_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="floor-import-"))


def import_session(slug: str, meetings_dir: Path, *, runner=subprocess.run,
                   max_runs: int = 6, force: bool = False) -> Path:
    """Download the `house-floor-sessions` artifact from a recent
    `house-floor-weekly.yml` run that contains `<slug>/pipeline_state.json`,
    and copy that folder to `meetings_dir/<slug>`.

    Writes directly to `meetings_dir/<slug>` — never via `gui.runner.launch_run`,
    which would create a `-2` fork instead of landing in the reviewed slug dir.
    """
    dest = meetings_dir / slug
    if dest.exists() and not force:
        raise FloorAlreadyLocalError(f"{slug} already exists locally; delete it or pass force")

    listed = runner(["gh", "run", "list", "--workflow", _WORKFLOW,
                     "--json", "databaseId", "-L", str(max_runs)],
                    capture_output=True, text=True)
    if listed.returncode != 0:
        raise FloorImportError(f"gh run list failed: {getattr(listed, 'stderr', '')}")
    run_ids = [r["databaseId"] for r in json.loads(listed.stdout or "[]")]

    root = _download_root()
    for rid in run_ids:
        sub = root / str(rid)
        got = runner(["gh", "run", "download", str(rid), "-n", _ARTIFACT, "-D", str(rid)],
                     capture_output=True, text=True, cwd=str(root))
        candidate = sub / slug
        if got.returncode == 0 and (candidate / "pipeline_state.json").exists():
            meetings_dir.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(candidate, dest)
            return dest
    raise FloorImportError(f"{slug} not found in the last {max_runs} {_WORKFLOW} runs")
