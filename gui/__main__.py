"""Run the GUI: `python -m gui` → http://127.0.0.1:8000"""
from __future__ import annotations

import uvicorn

from gui.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
