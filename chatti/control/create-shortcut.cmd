@echo off
REM Creates the desktop shortcut to Chatti Control. Run once, after setup.cmd.
REM
REM -ExecutionPolicy Bypass because a fresh Windows refuses to run an unsigned
REM .ps1, and this one is generated per clone rather than signed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create-shortcut.ps1"
if errorlevel 1 echo The shortcut could not be created.
pause
