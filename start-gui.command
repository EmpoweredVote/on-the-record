#!/usr/bin/env bash
# Double-click in Finder to start the processing GUI (macOS).
# On Windows, use start-gui.bat instead.
#
# This is only a launcher. The logic it calls lives in gui/__main__.py, which is
# the same on every platform; only the way a desktop starts a script differs.

# Deliberately no `-e`: each failure is reported with its own message and a
# pause, because a double-clicked window can close and take a bare trace with it.
set -uo pipefail

# $0 is the full path when Finder launches this, so the repo root comes from the
# script's own location, not from the caller's working directory.
cd "$(cd "$(dirname "$0")" && pwd)" || exit 1

pause_then_exit() {
    printf '\n%s\n\n' "$1" >&2
    printf 'Press Return to close this window. '
    read -r _
    exit 1
}

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    pause_then_exit "No virtualenv at $(pwd)/.venv

Create one first:
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt"
fi

"$PY" -m gui --open
status=$?

# 130 is Ctrl-C, the documented way to stop the server — not a failure.
if [ "$status" -ne 0 ] && [ "$status" -ne 130 ]; then
    pause_then_exit "The GUI exited with status ${status}. See the messages above."
fi
