@echo off
rem Football Ad Blocker - control window + tray icon, no console. Double-click me.
rem The log is in logs\app.log; recordings land in sessions\ (an installed copy: %LOCALAPPDATA%\FootballAdBlocker).
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0python\pythonw.exe"
start "" /D "%~dp0" "%PYW%" -m adblock.app %*
