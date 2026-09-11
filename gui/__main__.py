"""Run the GUI: `python -m gui` → http://127.0.0.1:8000

Uses uvicorn's auto-reload so code changes under gui/ and src/ are picked up
without a manual restart. The app is served from the `gui.asgi:app` import string
(required for reload) — that module loads .env.local in the worker subprocess.

`--open` waits for the server to accept connections, then opens a browser. The
double-click launchers at the repo root (start-gui.command on macOS,
start-gui.bat on Windows) pass it. The waiting and the browser call live here,
in Python, because they are the only cross-platform way to do it — the launchers
themselves can only be per-platform, since Finder needs a shell script and
Explorer needs a batch file.
"""
from __future__ import annotations

import argparse
import socket
import threading
import webbrowser
from pathlib import Path

import uvicorn

_ROOT = Path(__file__).resolve().parent.parent

HOST = "127.0.0.1"
PORT = 8000


def port_is_serving(host: str, port: int, timeout: float = 0.5) -> bool:
    """True if something already accepts TCP connections on host:port.

    Used both to detect an already-running GUI before uvicorn fails with a bare
    'address already in use', and to decide when the browser may be opened.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def open_when_ready(
    url: str,
    host: str,
    port: int,
    attempts: int = 60,
    delay: float = 0.5,
    opener=webbrowser.open,
    sleep=None,
) -> bool:
    """Open `url` once the server answers. Returns whether it was opened.

    Polls rather than opening immediately, so a dead tab never appears before
    the server is up. Gives up quietly — a browser that did not open is a far
    smaller problem than a server that refuses to start because of it.
    """
    if sleep is None:
        import time

        sleep = time.sleep
    for _ in range(attempts):
        sleep(delay)
        if port_is_serving(host, port):
            opener(url)
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gui", description=__doc__)
    parser.add_argument("--open", action="store_true",
                        help="Open the GUI in a browser once it is serving.")
    parser.add_argument("--port", type=int, default=PORT,
                        help=f"Port to serve on (default {PORT}).")
    args = parser.parse_args(argv)

    url = f"http://{HOST}:{args.port}"

    if port_is_serving(HOST, args.port):
        print(f"The GUI is already running on {url}")
        if args.open:
            webbrowser.open(url)
        return 0

    if args.open:
        threading.Thread(
            target=open_when_ready, args=(url, HOST, args.port), daemon=True
        ).start()

    print(f"Serving the processing GUI on {url}  (Ctrl-C to stop)")
    uvicorn.run(
        "gui.asgi:app",
        host=HOST,
        port=args.port,
        reload=True,
        reload_dirs=[str(_ROOT / "gui"), str(_ROOT / "src")],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
