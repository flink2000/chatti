@echo off
REM One-off: create the virtual environment for Chatti Control.
REM Run again after changing requirements.txt.
cd /d "%~dp0"
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

REM Put the shortcut on the desktop, so the panel has a front door.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create-shortcut.ps1"

echo.
echo Done. Chatti Control starts from the desktop shortcut, or from
echo chatti-control.cmd in this folder.
pause
