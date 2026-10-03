<#
.SYNOPSIS
Builds the Football Ad Blocker installer: stages an embedded Python with the app's packages and the app,
runs the unit tests inside that Python, then compiles the Inno Setup script into
dist\FootballAdBlocker-Setup-<version>.exe. The same script runs on a PC and on GitHub Actions.

.PARAMETER SkipInno
Stage and test only. build\stage is then a runnable copy: build\stage\python\pythonw.exe -m adblock.app

.EXAMPLE
.\installer\build.ps1
.\installer\build.ps1 -SkipInno
#>
[CmdletBinding()]
param([switch]$SkipInno)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is many times faster without the progress bar

$Root = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $Root "build\stage"
$Downloads = Join-Path $Root "build\downloads"
$PythonVersion = "3.11.9"  # the last 3.11 with binaries; the app is developed and pinned on 3.11
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$PythonZipSha256 = "009D6BF7E3B2DDCA3D784FA09F90FE54336D5B60F0E0F305C37F400BF83CFD3B"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

$init = Get-Content (Join-Path $Root "adblock\__init__.py") -Raw
if ($init -notmatch '__version__\s*=\s*"([^"]+)"') { throw "no __version__ in adblock\__init__.py" }
$Version = $Matches[1]
Step "Football Ad Blocker $Version"

# 1. An embedded Python with the app's packages in it.
Step "Python $PythonVersion embeddable package"
New-Item -ItemType Directory -Force $Downloads | Out-Null
$zip = Join-Path $Downloads (Split-Path -Leaf $PythonUrl)
if (-not (Test-Path $zip)) { Invoke-WebRequest -Uri $PythonUrl -OutFile $zip }
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash
if ($hash -ne $PythonZipSha256) { Remove-Item $zip; throw "python zip SHA-256 mismatch: $hash" }
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
$PyDir = Join-Path $Stage "python"
New-Item -ItemType Directory -Force $PyDir | Out-Null
Expand-Archive -Path $zip -DestinationPath $PyDir
# The ._pth file *is* the embedded interpreter's sys.path: its own zip and folder, the packages pip
# installs, and the install root (..) so that "-m adblock.app" works from anywhere; "import site" is
# what lets pip run.
$pth = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
@("python311.zip", ".", "Lib\site-packages", "..", "import site") | Set-Content $pth.FullName -Encoding ascii
$Python = Join-Path $PyDir "python.exe"

Step "pip and the app's packages"
$getpip = Join-Path $Downloads "get-pip.py"
if (-not (Test-Path $getpip)) { Invoke-WebRequest -Uri $GetPipUrl -OutFile $getpip }
& $Python $getpip --no-warn-script-location
if ($LASTEXITCODE) { throw "get-pip failed" }
& $Python -m pip install --no-warn-script-location -r (Join-Path $PSScriptRoot "requirements-installer.txt")
if ($LASTEXITCODE) { throw "pip install failed" }

# 2. The app itself, plus the payload that marks the copy as installed.
Step "the app"
foreach ($dir in @("adblock", "assets", "tools", "tests", "docs")) {
    robocopy (Join-Path $Root $dir) (Join-Path $Stage $dir) /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $dir failed ($LASTEXITCODE)" }
}
foreach ($file in @("README.md", "LICENSE", "CONTRIBUTING.md", "PLAN.md", "config.example.toml",
                    "requirements.txt", "requirements-ml.txt", "run.cmd", "start_ui.cmd")) {
    Copy-Item (Join-Path $Root $file) $Stage
}
Copy-Item (Join-Path $PSScriptRoot "payload\*") $Stage
$staged = Get-ChildItem $Stage -Recurse -File | Measure-Object Length -Sum
Write-Host ("staged {0} files, {1:N0} MB" -f $staged.Count, ($staged.Sum / 1MB))

# 3. The unit tests, with the staged Python: what ships is what passed.
Step "unit tests in the staged Python"
Push-Location $Stage
try {
    $env:FOOTBALL_AD_BLOCKER_DATA = Join-Path $Root "build\test-data"  # the INSTALLED marker must not send test files to %LOCALAPPDATA%
    & $Python -m unittest discover -s tests -t .
    if ($LASTEXITCODE) { throw "unit tests failed in the staged Python" }
} finally {
    Remove-Item Env:FOOTBALL_AD_BLOCKER_DATA -ErrorAction SilentlyContinue
    Pop-Location
}

# 4. The setup program.
if ($SkipInno) { Step "stage ready, Inno Setup skipped: $Stage"; exit 0 }
Step "Inno Setup"
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup), or run with -SkipInno" }
& $iscc "/DAppVersion=$Version" "/DStageDir=$Stage" (Join-Path $PSScriptRoot "FootballAdBlocker.iss")
if ($LASTEXITCODE) { throw "ISCC failed" }
$setup = Get-ChildItem (Join-Path $Root "dist") -Filter "FootballAdBlocker-Setup-$Version.exe"
Step ("built {0} ({1:N0} MB)" -f $setup.FullName, ($setup.Length / 1MB))
