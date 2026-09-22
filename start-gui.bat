@echo off
REM Double-click in Explorer to start the processing GUI (Windows).
REM On macOS, use start-gui.command instead.
REM
REM This is only a launcher. The logic it calls lives in gui/__main__.py, which
REM is the same on every platform; only the way a desktop starts a script differs.

REM %~dp0 is this file's own folder, so the repo root does not depend on
REM where Explorer happened to start us.
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo.
    echo No virtualenv at %CD%\.venv
    echo.
    echo Create one first:
    echo     py -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

"%PY%" -m gui --open

REM Pause unconditionally. Ctrl-C and a real crash are hard to tell apart from a
REM batch errorlevel, and losing a crash message is worse than one extra keypress.
echo.
pause
