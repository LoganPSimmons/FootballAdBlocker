@echo off
rem Football Ad Blocker launcher. From PowerShell:  .\run.cmd --game utah
rem All options:  .\run.cmd --help
rem Uses the project's .venv from a clone, or the embedded python\ of an installed copy.
pushd "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python\python.exe"
"%PY%" -m adblock.app %*
set "ADBLOCK_EXIT=%ERRORLEVEL%"
popd
exit /b %ADBLOCK_EXIT%
