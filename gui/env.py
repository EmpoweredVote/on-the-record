"""Load .env.local into os.environ (mirrors run_local's loader). Called from the
server entrypoint only — NOT from create_app — so tests never pick up real
secrets like DATABASE_URL."""
from __future__ import annotations

import os
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent


def load_env_local(path: Path | None = None) -> None:
    """setdefault each KEY=VALUE from .env.local. Missing file is a no-op."""
    env_file = path if path is not None else _REPO_DIR / ".env.local"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())
