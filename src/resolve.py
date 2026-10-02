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
    captions_vtt: Optional[str] = None    # source WebVTT; kept for reference / --use-vtt
    page_url: Optional[str] = None        # canonical citation page, when it differs from the input
    resolver: str = ""                    # 'podcast' | 'brightspot' | 'iga'


class SourceSelectionRequired(ValueError):
    """The URL names a listing of several recordings; the caller must pick one.

    ``choices`` is [{"label": str, "url": str}], each url resolving to exactly
    one recording.
    """

    def __init__(self, message: str, choices: list[dict]):
        super().__init__(message)
        self.choices = choices


def _default_fetch(url: str) -> str:
    import requests

    from .download import BROWSER_USER_AGENT

    # A full browser UA: some sources (iga.in.gov) serve their SPA shell, not
    # the requested JSON/playlist, to a bare "Mozilla/5.0".
    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": BROWSER_USER_AGENT})
    resp.raise_for_status()
    return resp.text


def resolve_source(
    url: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
) -> Optional[ResolvedSource]:
    """Try each resolver; return the first ResolvedSource, or None.

    Raises SourceSelectionRequired when the URL is a listing page that needs
    the caller to pick one recording (IGA committee / floor video pages).

    IGA is tried first (host-gated, so free for every other URL). Brightspot is
    tried before the generic podcast resolver because it is more specific
    (NPR-CDN MP3 + JSON-LD). Each resolver returns None when it does not
    apply, so the caller falls back to the existing yt-dlp / direct path.
    """
    if not (url or "").startswith(("http://", "https://")):
        return None

    # YouTube/Facebook are handled by yt-dlp; skip the resolvers entirely so we
    # never do a wasted (and potentially slow) page fetch on the common path.
    try:
        from .download import is_ytdlp_url

        if is_ytdlp_url(url):
            return None
    except Exception:
        pass

    from .brightspot import resolve_brightspot_episode
    from .iga import resolve_iga_video
    from .podcast import resolve_podcast_episode

    for resolver in (resolve_iga_video, resolve_brightspot_episode, resolve_podcast_episode):
        try:
            resolved = resolver(url, fetch=fetch)
        except SourceSelectionRequired:
            raise
        except Exception:
            resolved = None
        if resolved is not None:
            return resolved
    return None
