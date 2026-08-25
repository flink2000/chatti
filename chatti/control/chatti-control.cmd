@echo off
REM Chatti Control - target of the desktop shortcut.
REM
REM Starts the service only if nothing is listening on the port yet, then waits
REM for it to answer before opening the browser. Without that wait the browser
REM is faster than uvicorn and shows "connection refused".
REM
REM The minimised console window is the off switch: closing it stops the
REM service. That is why this uses python.exe and not pythonw.exe.

cd /d "%~dp0"
set PORT=8099
set PY=.venv\Scripts\python.exe

if not exist "%PY%" (
  echo The environment is missing. Please run setup.cmd first.
  pause
  exit /b 1
)

"%PY%" waitport.py %PORT%
if errorlevel 1 (
  echo Starting Chatti Control...
  start "Chatti Control" /min "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
  "%PY%" waitport.py %PORT% --wait
  if errorlevel 1 (
    echo The service did not come up. See the "Chatti Control" window.
    pause
    exit /b 1
  )
) else (
  echo Chatti Control is already running.
)

REM Nothing is started here on purpose: opening the page must not spin up
REM containers or load a 6 GB model. The stack comes up from the buttons on
REM the page - all of it at once, or one service at a time.
start "" "http://127.0.0.1:%PORT%/"
