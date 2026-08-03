"""Crash-safe writes for on-disk artifacts.

Every writer of a meeting artifact (transcript_named.json above all) goes
through here. A plain `open(path, "w")` truncates the file before the new bytes
land, so a process killed mid-dump leaves JSON cut off mid-token — invisible
until some later reader (GUI workspace/review, duplicate-speaker scans, publish,
the backfills) fails to parse it, which is exactly how a meeting sat broken on
disk for a month. Writing a sibling temp file and renaming it into place means a
reader always sees either the whole old file or the whole new one.

Lives in src/ (not gui/) so run_local.py and src/* can import it without
depending on the GUI; gui.review_api re-exports it for its existing callers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: temp file in the same dir, then
    os.replace (atomic on macOS/Linux; same dir guarantees same filesystem).

    On any failure the temp file is removed and `path` keeps its previous
    contents — a failed write never destroys what was already there.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        # Includes KeyboardInterrupt: a Ctrl-C mid-write shouldn't leave a turd.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    """Serialize `data` and write it to `path` atomically.

    Serialization happens before the file is touched, so an unserializable
    payload raises without disturbing the existing file.
    """
    atomic_write_text(path, json.dumps(data, indent=indent))
