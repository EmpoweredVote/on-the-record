"""FastAPI app factory for the processing GUI.

Slice 1: a single library route. Later slices mount review/launch/publish
routers onto the same app."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src import config

from gui import review_api
from gui.library import scan_meetings
from gui.paths import is_safe_meeting_id
from gui.review_api import find_meeting_media, load_review_page

_GUI_DIR = Path(__file__).resolve().parent
_templates = Jinja2Templates(directory=str(_GUI_DIR / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="CouncilScribe GUI")
    app.mount("/static", StaticFiles(directory=str(_GUI_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def library(request: Request) -> HTMLResponse:
        # Read MEETINGS_DIR via the module at request time so tests that
        # monkeypatch src.config.MEETINGS_DIR are honored.
        meetings = scan_meetings(config.MEETINGS_DIR)
        return _templates.TemplateResponse(
            request, "library.html", {"meetings": meetings}
        )

    @app.get("/meetings/{meeting_id}/thumbnail")
    def thumbnail(meeting_id: str) -> FileResponse:
        if not is_safe_meeting_id(meeting_id):
            raise HTTPException(status_code=404)
        path = config.MEETINGS_DIR / meeting_id / "thumbnail.jpg"
        if not path.exists():
            raise HTTPException(status_code=404)
        return FileResponse(str(path), media_type="image/jpeg")

    @app.get("/meetings/{meeting_id}/review", response_class=HTMLResponse)
    def review_page(request: Request, meeting_id: str) -> HTMLResponse:
        page = load_review_page(meeting_id)
        if page is None:
            raise HTTPException(status_code=404)
        return _templates.TemplateResponse(request, "review.html", {"page": page})

    @app.get("/meetings/{meeting_id}/media")
    def media(meeting_id: str):
        if not is_safe_meeting_id(meeting_id):
            raise HTTPException(status_code=404)
        meeting_dir = config.MEETINGS_DIR / meeting_id
        found = find_meeting_media(meeting_dir)
        if found is None:
            raise HTTPException(status_code=404)
        kind, filename = found
        media_type = "video/mp4" if kind == "video" else "audio/wav"
        return FileResponse(str(meeting_dir / filename), media_type=media_type)

    @app.post("/meetings/{meeting_id}/speakers/{label}/name")
    def set_speaker_name(meeting_id: str, label: str, name: str = Form("")):
        redirect = RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
        if not name.strip():
            return redirect  # empty submission: no-op, back to the page
        if not review_api.apply_rename(meeting_id, label, name):
            raise HTTPException(status_code=404)  # unknown meeting / unsafe id / unknown label
        return redirect

    return app
