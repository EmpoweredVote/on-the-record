"""Run the GUI: `python -m gui` → http://127.0.0.1:8000

Uses uvicorn's auto-reload so code changes under gui/ and src/ are picked up
without a manual restart. The app is served from the `gui.asgi:app` import string
(required for reload) — that module loads .env.local in the worker subprocess.
"""
from __future__ import annotations

from pathlib import Path

import uvicorn

_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    uvicorn.run(
        "gui.asgi:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[str(_ROOT / "gui"), str(_ROOT / "src")],
    )


if __name__ == "__main__":
    main()
