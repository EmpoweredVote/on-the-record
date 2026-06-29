"""Upload meeting thumbnails to a public Supabase Storage bucket.

Best-effort: returns None (with a warning) when env is missing or the upload
fails, so publishing never breaks over a thumbnail.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

THUMBNAIL_BUCKET = "meeting-thumbnails"


def public_url(supabase_url: str, bucket: str, object_path: str) -> str:
    """Public read URL for an object in a public Storage bucket."""
    base = supabase_url.rstrip("/")
    return f"{base}/storage/v1/object/public/{bucket}/{object_path}"


def upload_thumbnail(jpg_path: Path, meeting_id: str) -> Optional[str]:
    """Upload ``jpg_path`` as ``{meeting_id}.jpg``; return its public URL or None."""
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        logger.warning(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set; skipping thumbnail upload"
        )
        return None

    object_path = f"{meeting_id}.jpg"
    url = f"{base}/storage/v1/object/{THUMBNAIL_BUCKET}/{object_path}"
    try:
        with open(jpg_path, "rb") as fh:
            resp = requests.post(
                url,
                data=fh,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "image/jpeg",
                    "x-upsert": "true",
                },
                timeout=30,
            )
        resp.raise_for_status()
    except Exception as exc:  # network, auth, file IO — all non-fatal
        logger.warning("thumbnail upload failed: %s", exc)
        return None

    return public_url(base, THUMBNAIL_BUCKET, object_path)
