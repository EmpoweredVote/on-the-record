"""FastAPI app factory for the processing GUI.

Slice 1: a single library route. Later slices mount review/launch/publish
routers onto the same app."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src import config

from gui import publish_api
from gui import review_api
from gui import runner
from gui.library import scan_meetings
from gui.models import stage_label as stage_label_for
from gui.paths import is_safe_meeting_id
from gui.review_api import find_meeting_media, load_review_page
from gui.runner import RunParams

_GUI_DIR = Path(__file__).resolve().parent
_templates = Jinja2Templates(directory=str(_GUI_DIR / "templates"))
_REPO_DIR = _GUI_DIR.parent
_RUN_LOCAL = str(_REPO_DIR / "run_local.py")


def create_app() -> FastAPI:
    app = FastAPI(title="CouncilScribe GUI")
    app.mount("/static", StaticFiles(directory=str(_GUI_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def library(request: Request) -> HTMLResponse:
        # Read MEETINGS_DIR via the module at request time so tests that
        # monkeypatch src.config.MEETINGS_DIR are honored.
        # One batch query for live-site status; None (no DB) => no live badge.
        live_slugs = publish_api.live_published_slugs()
        meetings = scan_meetings(config.MEETINGS_DIR, live_slugs=live_slugs)
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

    @app.get("/api/politicians/search")
    def politician_search(q: str = "") -> JSONResponse:
        return JSONResponse(review_api.search_politicians_safe(q))

    @app.post("/meetings/{meeting_id}/speakers/{label}/link")
    def link_speaker_route(meeting_id: str, label: str,
                           politician_slug: str = Form(""), politician_id: str = Form("")):
        redirect = RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
        if not politician_slug.strip() and not politician_id.strip():
            return redirect  # nothing to link
        if not review_api.apply_link(meeting_id, label, politician_slug, politician_id):
            raise HTTPException(status_code=404)
        return redirect

    @app.post("/meetings/{meeting_id}/speakers/{label}/unlink")
    def unlink_speaker_route(meeting_id: str, label: str):
        if not review_api.apply_unlink(meeting_id, label):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.post("/meetings/{meeting_id}/speakers/{label}/merge")
    def merge_speaker_route(meeting_id: str, label: str, target: str = Form("")):
        if not review_api.apply_merge(meeting_id, label, target.strip()):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.post("/meetings/{meeting_id}/speakers/{label}/unidentified")
    def unidentified_route(meeting_id: str, label: str, display_label: str = Form("")):
        if not review_api.apply_mark_unidentified(meeting_id, label, display_label):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.post("/meetings/{meeting_id}/speakers/{label}/not-speaker")
    def not_speaker_route(meeting_id: str, label: str, display_label: str = Form("")):
        if not review_api.apply_mark_non_speaker(meeting_id, label, display_label):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.post("/meetings/{meeting_id}/speakers/{label}/enroll")
    def enroll_route(meeting_id: str, label: str):
        if not review_api.apply_enroll(meeting_id, label):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.get("/new", response_class=HTMLResponse)
    def new_meeting_form(request: Request) -> HTMLResponse:
        from src.event_kinds import EVENT_KINDS
        from gui.formmeta import (EVENT_KIND_HELP, COMPUTE_HELP, DIARIZER_HELP,
                                   CITY_REQUIRED_KINDS, MEETING_TYPE_DEFAULTS)
        from gui.rosters import list_cached_rosters
        return _templates.TemplateResponse(
            request, "new_meeting.html",
            {
                "event_kinds": list(EVENT_KINDS),
                "event_kind_help": EVENT_KIND_HELP,
                "compute_help": COMPUTE_HELP,
                "diarizer_help": DIARIZER_HELP,
                "city_required_kinds": sorted(CITY_REQUIRED_KINDS),
                "meeting_type_defaults": MEETING_TYPE_DEFAULTS,
                "cached_rosters": list_cached_rosters(),
            },
        )

    @app.post("/new")
    def new_meeting_launch(
        request: Request,
        input: str = Form(""),
        date: str = Form(""),
        meeting_type: str = Form(""),
        event_kind: str = Form("council"),
        city: str = Form(""),
        title: str = Form(""),
        compute: str = Form("local"),
        diarizer: str = Form("oss"),
        clip_start: str = Form(""),
        clip_end: str = Form(""),
        event_orgs: str = Form(""),
        body_slug: str = Form(""),
        confirm: str = Form(""),
    ):
        if not input.strip() or not date.strip() or not meeting_type.strip():
            raise HTTPException(status_code=400, detail="input, date, and meeting_type are required")
        from gui.formmeta import CITY_REQUIRED_KINDS
        if event_kind in CITY_REQUIRED_KINDS and not city.strip():
            raise HTTPException(
                status_code=400,
                detail=f"A city is required for event kind '{event_kind}'.",
            )
        if not confirm.strip():
            existing = runner.find_meeting_by_source(input)
            if existing:
                from src.checkpoint import PipelineState
                st = PipelineState(config.MEETINGS_DIR / existing)
                return _templates.TemplateResponse(
                    request, "dedup_confirm.html",
                    {
                        "existing_id": existing,
                        "completed_stage": int(st.completed_stage),
                        "review_status": st.review_status,
                        # echo the form so "Process anyway" can resubmit with confirm=1
                        "form": {
                            "input": input, "date": date, "meeting_type": meeting_type,
                            "event_kind": event_kind, "city": city, "title": title,
                            "compute": compute, "diarizer": diarizer,
                            "clip_start": clip_start, "clip_end": clip_end,
                            "event_orgs": event_orgs, "body_slug": body_slug,
                        },
                    },
                )
        p = RunParams(
            input=input.strip(), date=date.strip(), meeting_type=meeting_type.strip(),
            event_kind=event_kind, city=city.strip() or None, title=title.strip() or None,
            compute=compute, diarizer=diarizer,
            clip_start=clip_start.strip() or None, clip_end=clip_end.strip() or None,
            event_orgs=[o.strip() for o in event_orgs.split(",") if o.strip()],
            body_slug=body_slug.strip() or None,
        )
        try:
            meeting_id = runner.launch_run(p, python_exe=sys.executable, script=_RUN_LOCAL)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return RedirectResponse(url=f"/meetings/{meeting_id}/run", status_code=303)

    @app.get("/meetings/{meeting_id}/run", response_class=HTMLResponse)
    def run_page(request: Request, meeting_id: str) -> HTMLResponse:
        from src.checkpoint import PipelineStage
        stages = [(s.value, stage_label_for(s.value)) for s in PipelineStage if s.value >= 1]
        return _templates.TemplateResponse(
            request, "run.html",
            {"meeting_id": meeting_id, "stages": stages, "redo_stages": list(runner.REDO_STAGES)},
        )

    @app.get("/meetings/{meeting_id}/run/status")
    def run_status_json(meeting_id: str) -> JSONResponse:
        st = runner.run_status(meeting_id)
        if st is None:
            raise HTTPException(status_code=404)
        return JSONResponse(st)

    @app.post("/meetings/{meeting_id}/redo")
    def redo_route(meeting_id: str, stage: str = Form("")):
        stage = stage.strip()
        if stage not in runner.REDO_STAGES:
            raise HTTPException(status_code=400, detail="invalid redo stage")
        if runner.launch_redo(meeting_id, stage, python_exe=sys.executable, script=_RUN_LOCAL) is None:
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/run", status_code=303)

    @app.post("/meetings/{meeting_id}/continue")
    def continue_route(meeting_id: str, override: str = Form("")):
        if runner.launch_resume(meeting_id, override_gate=bool(override.strip()),
                                python_exe=sys.executable, script=_RUN_LOCAL) is None:
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/run", status_code=303)

    @app.get("/meetings/{meeting_id}/edit", response_class=HTMLResponse)
    def edit_meeting_form(request: Request, meeting_id: str) -> HTMLResponse:
        from gui.review_api import _load_meeting_ctx
        from src.event_kinds import EVENT_KINDS
        ctx = _load_meeting_ctx(meeting_id)
        if ctx is None:
            raise HTTPException(status_code=404)
        meeting, _dir, _roster = ctx
        return _templates.TemplateResponse(
            request, "edit_meeting.html",
            {"meeting_id": meeting_id, "m": meeting, "event_kinds": list(EVENT_KINDS)},
        )

    @app.post("/meetings/{meeting_id}/edit")
    def edit_meeting_apply(
        meeting_id: str,
        title: str = Form(""), city: str = Form(""), date: str = Form(""),
        meeting_type: str = Form(""), event_kind: str = Form(""),
    ):
        fields = {"title": title, "city": city, "date": date,
                  "meeting_type": meeting_type, "event_kind": event_kind}
        if publish_api.apply_metadata_edit(meeting_id, fields) is None:
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.get("/meetings/{meeting_id}/publish", response_class=HTMLResponse)
    def publish_confirm(request: Request, meeting_id: str) -> HTMLResponse:
        from gui.review_api import _load_meeting_ctx
        from src.checkpoint import PipelineState
        ctx = _load_meeting_ctx(meeting_id)
        if ctx is None:
            raise HTTPException(status_code=404)
        _meeting, meeting_dir, _roster = ctx
        state = PipelineState(meeting_dir)
        return _templates.TemplateResponse(
            request, "publish_confirm.html",
            {
                "meeting_id": meeting_id,
                "review_status": state.review_status,
                "gate_pass": state.review_status == "pass",
                "already_published": publish_api.meeting_published_id(meeting_id) is not None,
            },
        )

    @app.post("/meetings/{meeting_id}/publish", response_class=HTMLResponse)
    def publish_apply(request: Request, meeting_id: str, force: str = Form("")):
        result = publish_api.apply_publish(meeting_id, force=bool(force.strip()))
        if result.get("reason") == "unknown":
            raise HTTPException(status_code=404)
        return _templates.TemplateResponse(
            request, "publish_result.html",
            {"meeting_id": meeting_id, "result": result},
        )

    return app
