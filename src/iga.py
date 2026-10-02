"""Indiana General Assembly (iga.in.gov) archived-video resolver.

The IGA site is a JS app over a public JSON API. A video page URL names a
*listing*, not one recording:

    https://iga.in.gov/session/2026/video/committee_judiciary_4200/   (committee)
    https://iga.in.gov/session/2026/video/senate                      (floor days)

so a specific recording is selected with a ``?video=<stem>`` query parameter
(the site ignores it, so the URL still opens the right page as a citation):

    https://iga.in.gov/session/2026/video/committee_judiciary_4200/?video=Judiciary_1_14_2026_1

A direct ``.m3u8`` stream URL under iga.in.gov/video/... is also accepted.

Every request needs a full browser User-Agent: without one the server answers
every path (API and media alike) with the SPA's index.html.

Parsing is pure; the only network primitive is ``fetch``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from .resolve import ResolvedSource, SourceSelectionRequired

IGA_HOST = "iga.in.gov"
API_BASE = "https://iga.in.gov/api"
OUTLET = "Indiana General Assembly"

_PAGE_RE = re.compile(r"^/session/(?P<year>\d{4}[a-z0-9]*)/video/(?P<key>[a-z0-9_]+)/?$", re.I)
# /video/124/2026/committees/senate/standing/committee_judiciary_4200/media/<uuid>/X.mp4/X.m3u8
# /video/124/2026/senate/media/video_senate_2026_day_32/X.mp4/X.m3u8
_STREAM_RE = re.compile(
    r"^/video/\d+/(?P<year>\d{4}[a-z0-9]*)/"
    r"(?:committees/(?P<chamber>[a-z]+)/[a-z]+/(?P<committee>[a-z0-9_]+)"
    r"|(?P<floor>senate|house))/media/(?P<media>[^/]+)/[^/]+/(?P<stem>[^/]+)\.m3u8$",
    re.I,
)
# EV-owned CloudFront in front of iga.in.gov/video/* that adds the CORS header
# IGA's own CDN drops on cached GETs (ev-cto decision 0026). The site's hls.js
# player cannot read IGA streams directly outside Safari.
MEDIA_PROXY_BASE = os.environ.get("IGA_MEDIA_PROXY_BASE", "https://media.empowered.vote").rstrip("/")


def proxied_playback_url(url: str) -> str:
    """The media proxy URL for an IGA /video/ stream; any other URL unchanged."""
    if not is_iga_url(url):
        return url
    parsed = urlparse(url)
    if not parsed.path.startswith("/video/") or not MEDIA_PROXY_BASE:
        return url
    return MEDIA_PROXY_BASE + parsed.path


_MONTHS = ("Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec.")


@dataclass
class IgaVideo:
    """One archived recording from a getVideos listing."""
    stream_url: str
    stem: str                 # e.g. Judiciary_1_14_2026_1 — unique per recording
    date: Optional[str]       # YYYY-MM-DD
    label: str                # the site's own text, e.g. "Wednesday, Jan. 14 - 1:00pm"
    media_id: Optional[str]   # committee recordings: == the meeting's lpid


def is_iga_url(url: str) -> bool:
    try:
        return urlparse(url).netloc.lower() in (IGA_HOST, "www." + IGA_HOST)
    except Exception:
        return False


def parse_page_url(url: str) -> Optional[tuple[str, str, Optional[str]]]:
    """(session_year, video_key, selected_stem) for an IGA video page URL, else None."""
    if not is_iga_url(url):
        return None
    parsed = urlparse(url)
    m = _PAGE_RE.match(parsed.path)
    if not m or m.group("key").lower() == "livestreams":
        return None
    stem = (parse_qs(parsed.query).get("video") or [None])[0]
    return m.group("year"), m.group("key"), stem


def parse_stream_url(url: str) -> Optional[dict]:
    """Path facts of a direct IGA .m3u8 URL: year, video_key, chamber, media, stem."""
    if not is_iga_url(url):
        return None
    m = _STREAM_RE.match(urlparse(url).path)
    if not m:
        return None
    floor = m.group("floor")
    return {
        "year": m.group("year"),
        "video_key": (m.group("committee") or floor).lower(),
        "chamber": (m.group("chamber") or floor).lower(),
        "media": m.group("media"),
        "stem": m.group("stem"),
    }


def page_url_for(year: str, video_key: str, stem: str) -> str:
    """Canonical per-recording page URL (the citation link)."""
    return f"https://{IGA_HOST}/session/{year}/video/{video_key}/?video={stem}"


def _session_lpid(year: str) -> str:
    return f"session_{year}"


def _iso_date(original_date: Optional[str]) -> Optional[str]:
    # "01/14/2026, 00:00:00" or "01/14/2026"
    if not original_date:
        return None
    try:
        return datetime.strptime(original_date.split(",")[0].strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _human_date(iso: Optional[str]) -> str:
    if not iso:
        return ""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def parse_videos(payload: dict) -> list[IgaVideo]:
    """getVideos JSON -> [IgaVideo], in the API's order (newest first)."""
    out: list[IgaVideo] = []
    for v in payload.get("videos") or []:
        stream = ((v.get("value") or {}).get("http") or "").strip()
        facts = parse_stream_url(stream)
        if not facts:
            continue
        out.append(IgaVideo(
            stream_url=stream,
            stem=facts["stem"],
            date=_iso_date(v.get("original_date")),
            label=(v.get("text") or "").strip(),
            media_id=facts["media"],
        ))
    return out


def _get_json(fetch: Callable[[str], str], url: str) -> dict:
    text = fetch(url)
    try:
        return json.loads(text)
    except ValueError as exc:
        # The SPA shell instead of JSON means the request was not treated as a
        # browser (missing/short User-Agent).
        raise RuntimeError(f"IGA API returned non-JSON for {url}") from exc


def list_videos(year: str, video_key: str, *, fetch: Callable[[str], str]) -> list[IgaVideo]:
    return parse_videos(_get_json(
        fetch, f"{API_BASE}/getVideos?session_lpid={_session_lpid(year)}&video_key={video_key}"))


def _find_committee(year: str, video_key: str, fetch) -> Optional[dict]:
    data = _get_json(fetch, f"{API_BASE}/getCommittees?session_lpid={_session_lpid(year)}")
    for c in data.get("committees") or []:
        if (c.get("lpid") or "").lower() == video_key.lower():
            return c
    return None


def _agenda_description(meeting_details: dict) -> Optional[str]:
    """Readable agenda (bills, subjects, authors, outcome) for the description field."""
    lines = []
    for item in meeting_details.get("agenda") or []:
        authors = ", ".join(a.get("last_name", "") for a in item.get("authors") or [] if a.get("last_name"))
        line = f"- {item.get('label') or item.get('base_name')}: {item.get('description') or ''}".rstrip()
        if authors:
            line += f" (Authors: {authors})"
        if item.get("status"):
            line += f" — {item['status']}"
        lines.append(line)
    return ("Agenda:\n" + "\n".join(lines)) if lines else None


def find_committee_meeting(year: str, committee_id: str, media_id: str, fetch) -> Optional[dict]:
    """The getMeetings row whose lpid is this recording's media id."""
    data = _get_json(fetch, f"{API_BASE}/getMeetings?committee_id={committee_id}"
                            f"&session_lpid={_session_lpid(year)}")
    for m in data.get("meetings") or []:
        if m.get("lpid") == media_id:
            return m
    return None


def get_meeting_details(year: str, meeting_id: str, fetch) -> dict:
    return _get_json(fetch, f"{API_BASE}/getMeetingDetails?meeting_id={meeting_id}"
                            f"&session_lpid={_session_lpid(year)}")


def captions_url_from_master(master_m3u8: str, master_url: str) -> Optional[str]:
    """URI of the SUBTITLES rendition in an HLS master playlist, absolutized."""
    for line in master_m3u8.splitlines():
        if line.startswith("#EXT-X-MEDIA:") and "TYPE=SUBTITLES" in line:
            m = re.search(r'URI="([^"]+)"', line)
            if m:
                return urljoin(master_url, m.group(1))
    return None


def fetch_captions_vtt(master_url: str, *, fetch: Callable[[str], str]) -> Optional[str]:
    """Concatenated WebVTT caption text for a recording, or None if it has none."""
    cap_playlist_url = captions_url_from_master(fetch(master_url), master_url)
    if not cap_playlist_url:
        return None
    parts = []
    for line in fetch(cap_playlist_url).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            parts.append(fetch(urljoin(cap_playlist_url, line)))
    text = "\n".join(p for p in parts if p.lstrip().startswith("WEBVTT"))
    return text or None


def _title(chamber: str, committee: Optional[dict], video: IgaVideo) -> str:
    when = _human_date(video.date)
    if committee:
        name = committee.get("name") or "Committee"
        kind = (committee.get("type") or "").lower()
        if chamber in ("senate", "house"):
            body = f"Indiana {chamber.title()} {name} Committee"
        else:
            body = f"Indiana {name} Committee"
        if kind == "interim":
            body = f"Indiana Interim {name} Committee"
        return f"{body} — {when}" if when else body
    # Floor session: keep the site's "part N" so multi-part days stay distinct.
    part = re.search(r"\bpart \d+\b", video.label, re.I)
    title = f"Indiana {chamber.title()} Floor Session — {when}" if when else f"Indiana {chamber.title()} Floor Session"
    return f"{title} ({part.group(0)})" if part else title


def resolve_iga_video(url: str, *, fetch: Callable[[str], str]) -> Optional[ResolvedSource]:
    """ResolvedSource for one IGA recording; None when the URL is not IGA video.

    Raises SourceSelectionRequired for a listing page with several recordings
    and no ``?video=`` selection, carrying the choices.
    """
    page = parse_page_url(url)
    stream = parse_stream_url(url) if page is None else None
    if page is None and stream is None:
        return None

    if page is not None:
        year, video_key, stem = page
    else:
        year, video_key, stem = stream["year"], stream["video_key"], stream["stem"]

    videos = list_videos(year, video_key, fetch=fetch)
    if stem:
        chosen = next((v for v in videos if v.stem.lower() == stem.lower()), None)
        if chosen is None:
            raise ValueError(f"IGA recording {stem!r} is not in the {video_key} {year} listing")
    elif len(videos) == 1:
        chosen = videos[0]
    elif not videos:
        raise ValueError(f"IGA listing {video_key} {year} has no archived recordings")
    else:
        raise SourceSelectionRequired(
            "This IGA page lists several recordings; pick one.",
            choices=[{"label": f"{v.label} ({v.date})" if v.date else v.label,
                      "url": page_url_for(year, video_key, v.stem)} for v in videos],
        )

    facts = parse_stream_url(chosen.stream_url) or {}
    chamber = facts.get("chamber") or ""
    committee = None
    description = None
    if facts.get("video_key") not in ("senate", "house"):
        committee = _find_committee(year, video_key, fetch)
        if committee and chosen.media_id:
            try:
                meeting = find_committee_meeting(year, committee["id"], chosen.media_id, fetch)
                if meeting:
                    description = _agenda_description(get_meeting_details(year, meeting["id"], fetch))
            except Exception:
                description = None  # agenda is a nicety; never block the audio

    try:
        captions = fetch_captions_vtt(chosen.stream_url, fetch=fetch)
    except Exception:
        captions = None

    return ResolvedSource(
        audio_url=chosen.stream_url,
        title=_title(chamber, committee, chosen),
        date=chosen.date,
        outlet=OUTLET,
        description=description,
        captions_vtt=captions,
        page_url=page_url_for(year, video_key, chosen.stem),
        resolver="iga",
    )
