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
from src import ingest
from src import resolve
from src.download import is_ytdlp_url

from gui import publish_api
from gui import review_api
from gui import runner
from gui import workspace
from gui.library import scan_meetings
from gui.paths import is_safe_meeting_id
from gui.review_api import find_meeting_media
from gui.runner import RunParams

_GUI_DIR = Path(__file__).resolve().parent
_templates = Jinja2Templates(directory=str(_GUI_DIR / "templates"))
_REPO_DIR = _GUI_DIR.parent
_RUN_LOCAL = str(_REPO_DIR / "run_local.py")


class _NoCacheStaticFiles(StaticFiles):
    """Serve static assets with Cache-Control: no-cache so the browser always
    revalidates (via the ETag StaticFiles already sends) instead of silently
    reusing a stale JS/CSS file after an edit. Revalidation stays cheap — an
    unchanged file still returns a 304."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="CouncilScribe GUI")
    app.mount("/static", _NoCacheStaticFiles(directory=str(_GUI_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def library(request: Request) -> HTMLResponse:
        # Read MEETINGS_DIR via the module at request time so tests that
        # monkeypatch src.config.MEETINGS_DIR are honored.
        # One batch query for live-site status; None (no DB) => no live badge.
        live_slugs = publish_api.live_published_slugs()
        meetings = scan_meetings(config.MEETINGS_DIR, live_slugs=live_slugs)
        from gui import races
        race_ids = {m.race_id for m in meetings if m.race_id}
        labels = races.race_labels(race_ids) if race_ids else {}
        for m in meetings:
            if m.race_id:
                m.race_label = labels.get(m.race_id)
        from src.event_kinds import EVENT_KINDS
        return _templates.TemplateResponse(
            request, "library.html", {"meetings": meetings, "event_kinds": list(EVENT_KINDS)},
        )

    @app.get("/meetings/{meeting_id}/thumbnail")
    def thumbnail(meeting_id: str) -> FileResponse:
        if not is_safe_meeting_id(meeting_id):
            raise HTTPException(status_code=404)
        path = config.MEETINGS_DIR / meeting_id / "thumbnail.jpg"
        if not path.exists():
            raise HTTPException(status_code=404)
        return FileResponse(str(path), media_type="image/jpeg")

    @app.get("/meetings/{meeting_id}/review")
    def review_page(meeting_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=review", status_code=301)

    @app.get("/meetings/{meeting_id}", response_class=HTMLResponse)
    def workspace_shell(request: Request, meeting_id: str, tab: str = "") -> HTMLResponse:
        header = workspace.header_context(
            meeting_id, is_live=(publish_api.meeting_published_id(meeting_id) is not None),
        )
        if header is None:
            raise HTTPException(status_code=404)
        active = tab.strip() or workspace.default_tab_for_stage(header["completed_stage"])
        ctx = workspace.panel_context(active, meeting_id)
        if ctx is None:  # bad ?tab value -> fall back to the default tab
            active = workspace.default_tab_for_stage(header["completed_stage"])
            ctx = workspace.panel_context(active, meeting_id)
        return _templates.TemplateResponse(
            request, "workspace.html", {**ctx, "header": header, "active_tab": active},
        )

    @app.get("/meetings/{meeting_id}/panel/{name}", response_class=HTMLResponse)
    def workspace_panel(request: Request, meeting_id: str, name: str) -> HTMLResponse:
        ctx = workspace.panel_context(name, meeting_id)
        if ctx is None:
            raise HTTPException(status_code=404)
        return _templates.TemplateResponse(request, f"panels/{name}.html", ctx)

    @app.get("/meetings/{meeting_id}/status")
    def workspace_status(meeting_id: str) -> JSONResponse:
        st = runner.run_status(meeting_id)
        if st is None:
            raise HTTPException(status_code=404)
        # is_live changes only on an explicit publish (never mid-run), so skip the
        # remote DB round-trip while the pipeline is running to keep the poll cheap.
        is_live = None if st.get("running") else (
            publish_api.meeting_published_id(meeting_id) is not None
        )
        header = workspace.header_context(meeting_id, is_live=is_live)
        st["review_status"] = header["review_status"] if header else None
        st["is_live"] = header["is_live"] if header else None
        st["attention_count"] = header["attention_count"] if header else 0
        return JSONResponse(st)

    @app.get("/meetings/{meeting_id}/media")
    def media(meeting_id: str):
        if not is_safe_meeting_id(meeting_id):
            raise HTTPException(status_code=404)
        meeting_dir = config.MEETINGS_DIR / meeting_id
        found = find_meeting_media(meeting_dir)
        if found is None:
            raise HTTPException(status_code=404)
        _kind, filename = found
        suffix = Path(filename).suffix.lower()
        if suffix in (".opus", ".ogg"):
            media_type = "audio/ogg"
        elif suffix == ".wav":
            media_type = "audio/wav"
        else:
            media_type = "video/mp4"
        return FileResponse(str(meeting_dir / filename), media_type=media_type)

    @app.post("/meetings/{meeting_id}/cleanup")
    def cleanup_media_route(meeting_id: str):
        if not is_safe_meeting_id(meeting_id):
            raise HTTPException(status_code=404)
        from src.cleanup import cleanup_meeting

        result = cleanup_meeting(meeting_id)
        if result["status"] == "not_found":
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.post("/cleanup-all")
    def cleanup_all_route():
        from src.cleanup import backfill_all

        backfill_all()
        return RedirectResponse(url="/", status_code=303)

    @app.post("/meetings/{meeting_id}/delete")
    def delete_meeting_route(meeting_id: str, confirm_slug: str = Form("")):
        if not is_safe_meeting_id(meeting_id):
            raise HTTPException(status_code=404)
        if confirm_slug != meeting_id:
            # Typed confirmation didn't match — no-op, back to the review page.
            return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
        from src.purge import purge_meeting

        purge_meeting(meeting_id)
        return RedirectResponse(url="/", status_code=303)

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

    @app.get("/api/races/search")
    def race_search(q: str = "") -> JSONResponse:
        from gui import races
        return JSONResponse(races.search_races_safe(q))

    @app.get("/api/source-meta")
    def source_meta(url: str = "") -> JSONResponse:
        # Podcast / public-radio CMS episode pages: resolve for real metadata.
        # Looked up at call time so tests can monkeypatch resolve.resolve_source.
        try:
            resolved = resolve.resolve_source(url) if url else None
        except Exception:
            resolved = None
        if resolved is not None:
            return JSONResponse({
                "date": resolved.date,
                "title": resolved.title,
                "event_org": resolved.outlet,
            })
        # yt-dlp URLs (YouTube/Facebook): fetchable video metadata.
        if not is_ytdlp_url(url):
            return JSONResponse({"date": None, "title": None, "event_org": None})
        # Look up ingest.fetch_source_metadata at call time so tests can
        # monkeypatch it on the module.
        meta = ingest.fetch_source_metadata(url)
        return JSONResponse({
            "date": meta["upload_date"],
            "title": meta["title"],
            "event_org": meta["channel"],
        })

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
                                   CITY_REQUIRED_KINDS, MEETING_TYPE_DEFAULTS,
                                   FIELDS_BY_KIND, DEFAULT_COMPUTE, DEFAULT_DIARIZER)
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
                "fields_by_kind": FIELDS_BY_KIND,
                "default_compute": DEFAULT_COMPUTE,
                "default_diarizer": DEFAULT_DIARIZER,
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
        crec_chamber: str = Form(""),
        guest: str = Form(""),
        race_id: str = Form(""),
        race_slug: str = Form(""),
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
                            "crec_chamber": crec_chamber,
                            "guest": guest, "race_id": race_id, "race_slug": race_slug,
                        },
                    },
                )
        from gui.formmeta import FIELDS_BY_KIND
        _allowed = set(FIELDS_BY_KIND.get(event_kind, ()))
        if "city" not in _allowed:
            city = ""
        if "body" not in _allowed:
            body_slug = ""
        if "guest" not in _allowed:
            guest = ""
        if "race" not in _allowed:
            race_id = race_slug = ""
        if "crec_chamber" not in _allowed:
            crec_chamber = ""
        p = RunParams(
            input=input.strip(), date=date.strip(), meeting_type=meeting_type.strip(),
            event_kind=event_kind, city=city.strip() or None, title=title.strip() or None,
            compute=compute, diarizer=diarizer,
            clip_start=clip_start.strip() or None, clip_end=clip_end.strip() or None,
            event_orgs=[o.strip() for o in event_orgs.split(",") if o.strip()],
            body_slug=body_slug.strip() or None,
            crec_chamber=crec_chamber.strip() or None,
            guest=guest.strip() or None,
            race_id=race_id.strip() or None,
            race_slug=race_slug.strip() or None,
        )
        try:
            meeting_id = runner.launch_run(p, python_exe=sys.executable, script=_RUN_LOCAL)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=progress", status_code=303)

    @app.get("/meetings/{meeting_id}/run")
    def run_page(meeting_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=progress", status_code=301)

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

    @app.get("/meetings/{meeting_id}/edit")
    def edit_meeting_form(meeting_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=details", status_code=301)

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

    @app.get("/meetings/{meeting_id}/publish")
    def publish_confirm(meeting_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=publish", status_code=301)

    @app.post("/meetings/{meeting_id}/publish", response_class=HTMLResponse)
    def publish_apply(request: Request, meeting_id: str, force: str = Form("")):
        result = publish_api.apply_publish(meeting_id, force=bool(force.strip()))
        if result.get("reason") == "unknown":
            raise HTTPException(status_code=404)
        if result.get("ok"):
            msg = (f"✓ Published · {result.get('segments', 0)} segments · "
                   f"{result.get('speakers', 0)} speakers")
            body = f'<div class="publish-ok">{msg}</div>'
        else:
            body = (f'<div class="error-banner">Publish failed '
                    f'({result.get("reason")}): {result.get("error", "")}</div>')
        return HTMLResponse(body)

    return app
