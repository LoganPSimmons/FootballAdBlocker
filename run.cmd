@echo off
rem Football Ad Blocker launcher. From PowerShell:  .\run.cmd --game utah
rem All options:  .\run.cmd --help
pushd "%~dp0"
".venv\Scripts\python.exe" -m adblock.app %*
popd
