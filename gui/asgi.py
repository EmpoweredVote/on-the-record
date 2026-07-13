"""ASGI entrypoint for the local GUI dev server (`python -m gui`).

Loads .env.local and builds the app at import time so that under uvicorn's
`reload=True` — where the serving worker is a subprocess that imports this module
rather than running `__main__.main()` — the worker still gets DATABASE_URL et al.

Tests import `gui.app.create_app` directly and never this module, so they still
never pick up real secrets (see gui/env.py).
"""
from __future__ import annotations

from gui.app import create_app
from gui.env import load_env_local

load_env_local()  # server-only; must run in the reload worker, not in create_app
app = create_app()
