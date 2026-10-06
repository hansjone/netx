; NetX Windows installer (Inno Setup 6+)
; Compile after: packaging\build_release.ps1
; ISCC.exe packaging\installer\netx.iss

#define MyAppName "NetX"
#ifndef MyAppVersion
  #define MyAppVersion "0.3.0"
#endif
#define MyAppPublisher "NetX"
#define MyAppURL "https://github.com/hansjone/netx"
#define MyAppExeName "packaging\start_netx_app.ps1"

; Stage directory produced by build_release.ps1 (relative to this .iss)
#define StageDir "..\release\netx-win64"

[Setup]
AppId={{A7E3C2D1-9F40-4B8E-9C1A-NETXWIN64001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\NetX
DefaultGroupName=NetX
DisableProgramGroupPage=yes
LicenseFile={#StageDir}\LICENSE
OutputDir=..\release
OutputBaseFilename=NetX-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; Data lives under {commonappdata}\NetX — not overwritten by upgrades
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "firstrun"; Description: "Run first-time setup (database mode) after install"; GroupDescription: "Setup:"; Flags: checkedonce

[Files]
; Entire release stage → {app}. Exclude .portable so installed builds use ProgramData.
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".portable"

[Dirs]
Name: "{commonappdata}\NetX"
Name: "{commonappdata}\NetX\data"
Name: "{commonappdata}\NetX\data\auth"
Name: "{commonappdata}\NetX\data\runtime"
Name: "{commonappdata}\NetX\pgdata"
Name: "{commonappdata}\NetX\backups"

[Icons]
Name: "{group}\Start NetX"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\start_netx_app.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"
Name: "{group}\Stop NetX"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\stop_netx_app.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"
Name: "{group}\Open NetX UI"; Filename: "http://127.0.0.1:8890/"
Name: "{group}\First-time setup"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\setup_first_run.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"
Name: "{autodesktop}\Start NetX"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\start_netx_app.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\setup_first_run.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; Flags: postinstall skipifsilent; Tasks: firstrun
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\start_netx_app.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; Description: "Start NetX now"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
; Do NOT delete {commonappdata}\NetX — preserves DB and secrets across reinstall
Type: filesandordirs; Name: "{app}"
