"""FastAPI app factory for the processing GUI.

Slice 1: a single library route. Later slices mount review/launch/publish
routers onto the same app."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src import config

from gui.library import scan_meetings

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

    return app
