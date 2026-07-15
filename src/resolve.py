"""Turn an episode page URL into a normalized ResolvedSource.

A ResolvedSource is what the ingestion pipeline consumes regardless of where the
audio came from (podcast RSS, public-radio CMS, ...). Resolvers are tried in
order; the first one that confidently applies wins. If none applies,
resolve_source returns None and the caller falls back to the existing yt-dlp /
direct-download path.

Parsing is pure and network-free; the only network primitive is `fetch`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class ResolvedSource:
    audio_url: str
    title: Optional[str] = None
    date: Optional[str] = None            # YYYY-MM-DD
    outlet: Optional[str] = None          # show / station name -> source_channel
    description: Optional[str] = None     # show notes / article summary
    image_url: Optional[str] = None       # episode / show / og artwork
    transcript: Optional[str] = None      # clean transcript text, when provided
    resolver: str = ""                    # 'podcast' | 'brightspot'


def _default_fetch(url: str) -> str:
    import requests

    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def resolve_source(
    url: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
) -> Optional[ResolvedSource]:
    """Try each resolver; return the first ResolvedSource, or None.

    Brightspot is tried before the generic podcast resolver because it is more
    specific (NPR-CDN MP3 + JSON-LD). Each resolver returns None when it does not
    apply, so the caller falls back to the existing yt-dlp / direct path.
    """
    if not (url or "").startswith(("http://", "https://")):
        return None

    from .brightspot import resolve_brightspot_episode
    from .podcast import resolve_podcast_episode

    for resolver in (resolve_brightspot_episode, resolve_podcast_episode):
        try:
            resolved = resolver(url, fetch=fetch)
        except Exception:
            resolved = None
        if resolved is not None:
            return resolved
    return None
