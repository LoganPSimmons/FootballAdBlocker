@echo off
rem Football Ad Blocker - control window + tray icon, no console. Double-click me.
rem The log is in logs\app.log; recordings land in sessions\.
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" -m adblock.app %*
