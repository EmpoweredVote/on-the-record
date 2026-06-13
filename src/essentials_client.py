"""HTTP client for the ev-accounts essentials bodies endpoint (Phase 107).

CouncilScribe-side HTTP wrapper. Never reads, logs, or persists any
affiliation fields — the upstream endpoint already strips them
(Phase 107 D-15), and the Phase 108 test suite enforces this with a
grep assertion.
"""
from __future__ import annotations

import os
import re

import requests

DEFAULT_BASE_URL = "https://accounts.empowered.vote"
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB cap — T-108-01 mitigation
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 15


class EssentialsClientError(Exception):
    """Raised for any failure in fetch_body_roster.

    Attributes:
        code: Upstream error envelope code (e.g. "BODY_NOT_FOUND").
        status: HTTP status code, or None for transport failures.
    """

    def __init__(
        self,
        message: str,
        code: str | None = None,
        status: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status


def _resolve_base_url(base_url: str | None) -> str:
    base = base_url or os.environ.get("EV_ACCOUNTS_URL") or DEFAULT_BASE_URL
    return base.rstrip("/")


def _validate_slug(body_slug: str) -> None:
    if not body_slug or not _SLUG_RE.match(body_slug):
        raise EssentialsClientError(
            f"Invalid body slug format: {body_slug!r} (must match ^[a-z0-9-]+$)",
            code="INVALID_SLUG",
            status=None,
        )


def _request_json(url: str, *, params: dict | None = None) -> object:
    """GET `url` and return parsed JSON, applying the shared hardening:
    timeouts, 5 MB cap, and EssentialsClientError on any non-200/transport/
    non-JSON failure. Used by fetch_body_roster and search_politicians.
    """
    try:
        resp = requests.get(url, params=params, timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT))
    except requests.exceptions.RequestException as exc:
        raise EssentialsClientError(f"Network error: {exc}") from exc

    content_length = resp.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_RESPONSE_BYTES:
                raise EssentialsClientError(
                    f"Response too large: {content_length} bytes "
                    f"(cap {_MAX_RESPONSE_BYTES})",
                    status=resp.status_code,
                )
        except ValueError:
            pass

    if resp.status_code == 404:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        raise EssentialsClientError(
            body.get("message", "Body not found"),
            code=body.get("code", "BODY_NOT_FOUND"),
            status=404,
        )
    if resp.status_code == 422:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        raise EssentialsClientError(
            body.get("message", "Validation error"),
            code=body.get("code", "VALIDATION_ERROR"),
            status=422,
        )
    if resp.status_code >= 500:
        raise EssentialsClientError(
            f"Server error ({resp.status_code})",
            status=resp.status_code,
        )
    if resp.status_code != 200:
        raise EssentialsClientError(
            f"Unexpected status {resp.status_code}",
            status=resp.status_code,
        )

    try:
        return resp.json()
    except ValueError as exc:  # includes requests.exceptions.JSONDecodeError
        snippet = (resp.text or "")[:200]
        raise EssentialsClientError(
            f"Non-JSON response from {url}: {snippet}",
            status=resp.status_code,
        ) from exc


def fetch_body_roster(body_slug: str, base_url: str | None = None) -> dict:
    """Fetch the roster JSON for a body slug from ev-accounts.

    Raises EssentialsClientError on any non-200 status, transport failure,
    invalid slug, or oversized response.
    """
    _validate_slug(body_slug)
    base = _resolve_base_url(base_url)
    url = f"{base}/api/essentials/bodies/{body_slug}/roster"
    return _request_json(url)


def _normalize_politician(rec: dict) -> dict:
    """Whitelist the fields the pipeline needs from a PoliticianFlatRecord."""
    return {
        "politician_id": rec.get("id"),
        "politician_slug": rec.get("slug"),
        "full_name": rec.get("full_name", ""),
        "office_title": rec.get("office_title", ""),
        "district_label": rec.get("district_label", ""),
        "is_incumbent": bool(rec.get("is_incumbent", False)),
        "government_name": rec.get("government_name", ""),
    }


def search_politicians(
    q: str, *, limit: int = 10, base_url: str | None = None
) -> list[dict]:
    """Name-search essentials politicians AND candidates (one ID space).

    Calls GET /api/essentials/candidates/search-by-name (incumbents +
    challengers). Returns up to `limit` normalized dicts, each with
    politician_id, politician_slug, full_name, office_title,
    district_label, is_incumbent, government_name.

    Note: affiliation fields are intentionally excluded — the pipeline never
    reads or persists them (enforced by tests/test_antipartisan.py).

    Raises EssentialsClientError on a <2-char query (INVALID_QUERY) or any
    transport/HTTP/parse failure. Callers in review treat this as best-effort.
    """
    query = (q or "").strip()
    if len(query) < 2:
        raise EssentialsClientError(
            "Search query must be at least 2 characters",
            code="INVALID_QUERY",
            status=None,
        )
    base = _resolve_base_url(base_url)
    url = f"{base}/api/essentials/candidates/search-by-name"
    data = _request_json(url, params={"q": query})
    if not isinstance(data, list):
        raise EssentialsClientError(
            f"Expected a list from {url}, got {type(data).__name__}",
            status=None,
        )
    return [_normalize_politician(r) for r in data[:limit]]
