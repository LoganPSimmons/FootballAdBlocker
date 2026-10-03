; Football Ad Blocker - Inno Setup script.
;
; Compiled by installer\build.ps1, which first stages the payload in build\stage: an embedded
; Python 3.11 with the app's packages installed, the app itself, and the INSTALLED marker that
; sends everything the app writes to %LOCALAPPDATA%\FootballAdBlocker. Per-user install under
; %LOCALAPPDATA%\Programs, no administrator rights, nothing in the registry but the uninstaller.
; The image model is an optional component: install_image_model.cmd pip-installs it into the
; copy's own Python at the end of the setup (a 2-3 GB download), and can be run again later.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\build\stage"
#endif

[Setup]
AppId={{B3F1D2A4-6C7E-4E52-9A0B-2F7C1D5E8A91}
AppName=Football Ad Blocker
AppVersion={#AppVersion}
AppVerName=Football Ad Blocker {#AppVersion}
AppPublisher=Logan Simmons
AppPublisherURL=https://github.com/LoganPSimmons/FootballAdBlocker
AppSupportURL=https://github.com/LoganPSimmons/FootballAdBlocker/issues
AppUpdatesURL=https://github.com/LoganPSimmons/FootballAdBlocker/releases
DefaultDirName={localappdata}\Programs\Football Ad Blocker
DefaultGroupName=Football Ad Blocker
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=FootballAdBlocker-Setup-{#AppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\assets\app.ico
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern

[Types]
Name: "full"; Description: "Football Ad Blocker with the image model (recommended)"
Name: "compact"; Description: "Football Ad Blocker only (the image model can be added later)"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "app"; Description: "Football Ad Blocker"; Types: full compact custom; Flags: fixed
Name: "model"; Description: "Image model: downloads 2-3 GB and needs about 5 GB of disk. NVIDIA GPU recommended, works on a CPU more slowly"; Types: full

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Dirs]
Name: "{localappdata}\FootballAdBlocker"; Flags: uninsneveruninstall

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Football Ad Blocker"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m adblock.app"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; AppUserModelID: "Logan.FootballAdBlocker"; Comment: "Hides ad breaks: mutes Chrome and covers the stream until the game is back"
Name: "{group}\Install the image model"; Filename: "{app}\install_image_model.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; Comment: "Adds the image model to this copy (a 2-3 GB download)"
Name: "{group}\Recordings and settings"; Filename: "{localappdata}\FootballAdBlocker"; Comment: "config.toml, recordings, learned graphics and the log"
Name: "{autodesktop}\Football Ad Blocker"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m adblock.app"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; AppUserModelID: "Logan.FootballAdBlocker"; Tasks: desktopicon

[Run]
Filename: "{cmd}"; Parameters: "/C ""{app}\install_image_model.cmd"" quiet"; WorkingDir: "{app}"; StatusMsg: "Installing the image model (downloads 2-3 GB; a console window shows the progress) ..."; Components: model; Flags: waituntilterminated
Filename: "{app}\python\pythonw.exe"; Parameters: "-m adblock.app"; WorkingDir: "{app}"; Description: "Start Football Ad Blocker now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: "tools\unmute_chrome.py"; WorkingDir: "{app}"; RunOnceId: "unmute"; Flags: runhidden
