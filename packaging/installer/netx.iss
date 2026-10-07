; NetX Windows installer (Inno Setup 6+)
; Compile after: packaging\build_release.ps1
; ISCC.exe packaging\installer\netx.iss

#define MyAppName "NetX"
#ifndef MyAppVersion
  #define MyAppVersion "0.4.5"
#endif
#define MyAppPublisher "NetX"
#define MyAppURL "https://github.com/hansjone/netx"
#define MyAppExeName "packaging\start_netx_app.ps1"

; Stage directory produced by build_release.ps1 (relative to this .iss)
#define StageDir "..\release\netx-win64"
#define IconFile "..\assets\netx.ico"

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
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\packaging\assets\netx.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; Data lives under {commonappdata}\NetX 鈥?not overwritten by upgrades
CloseApplications=yes
ShowLanguageDialog=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "Languages\ChineseSimplified.isl"

[CustomMessages]
english.CreateDesktopIcon=Create a desktop shortcut
english.SetupOptions=Setup options:
english.RunFirstSetup=Run first-time setup after install (bundled database, offline)
english.StartTrayNow=Start NetX tray now
english.EnableAutostart=Start NetX tray at Windows logon
english.EnableAutoUpdate=Enable daily silent auto-update (needs network later)
english.InstallService=Install as Windows Service (uses bundled WinSW, offline)
chinesesimplified.CreateDesktopIcon=鍒涘缓妗岄潰蹇嵎鏂瑰紡
chinesesimplified.SetupOptions=瀹夎閫夐」:
chinesesimplified.RunFirstSetup=瀹夎鍚庤繍琛岄娆￠厤缃紙鍐呯疆鏁版嵁搴擄紝鍙绾匡級
chinesesimplified.StartTrayNow=绔嬪嵆鍚姩 NetX 鎵樼洏
chinesesimplified.EnableAutostart=寮€鏈烘椂鑷姩鍚姩 NetX 鎵樼洏
chinesesimplified.EnableAutoUpdate=鍚敤姣忔棩闈欓粯鑷姩鏇存柊锛堜互鍚庨渶瑕佽仈缃戯級
chinesesimplified.InstallService=瀹夎涓?Windows 鏈嶅姟锛堜娇鐢ㄥ凡鎹嗙粦鐨?WinSW锛屽彲绂荤嚎锛?
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "firstrun"; Description: "{cm:RunFirstSetup}"; GroupDescription: "{cm:SetupOptions}"; Flags: checkedonce

[Files]
; Entire release stage 鈫?{app}. Exclude .portable so installed builds use ProgramData.
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".portable"

[Dirs]
Name: "{commonappdata}\NetX"
Name: "{commonappdata}\NetX\data"
Name: "{commonappdata}\NetX\data\auth"
Name: "{commonappdata}\NetX\data\runtime"
Name: "{commonappdata}\NetX\pgdata"
Name: "{commonappdata}\NetX\backups"

[Icons]
; Prefer .cmd targets so Server 2012 / classic Start Menu shows normal program entries.
; Also pin First-time setup + Start to common desktop (always), not only optional tray icon.
Name: "{group}\NetX Tray"; Filename: "{app}\NetX-Tray.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Start NetX"; Filename: "{app}\NetX-Start.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Stop NetX"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\launch_shortcut.ps1"" -Action stop -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Check for updates"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\launch_shortcut.ps1"" -Action update -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Open NetX UI"; Filename: "http://127.0.0.1:8890/"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\First-time setup"; Filename: "{app}\NetX-FirstRun.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Install Windows Service (admin)"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\install_service.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"" -Start"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Enable silent auto-update (daily)"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\install_update_task.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Enable start at logon"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\install_autostart.ps1"" -ProgramRoot ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{commondesktop}\NetX First-time setup"; Filename: "{app}\NetX-FirstRun.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{commondesktop}\NetX Start"; Filename: "{app}\NetX-Start.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{commondesktop}\NetX"; Filename: "{app}\NetX-Tray.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"; Tasks: desktopicon

[Run]
; First-run is NonInteractive + bundled DB so offline machines succeed without prompts or downloads.
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\setup_first_run.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"" -NonInteractive -DbMode bundled"; WorkingDir: "{app}"; Flags: postinstall skipifsilent; Tasks: firstrun
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\packaging\netx_tray.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"" -StartOnLaunch"; WorkingDir: "{app}"; Description: "{cm:StartTrayNow}"; Flags: postinstall nowait skipifsilent unchecked
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\install_autostart.ps1"" -ProgramRoot ""{app}"""; WorkingDir: "{app}"; Description: "{cm:EnableAutostart}"; Flags: postinstall skipifsilent unchecked
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\install_update_task.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; Description: "{cm:EnableAutoUpdate}"; Flags: postinstall skipifsilent unchecked
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\install_service.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"" -Start"; WorkingDir: "{app}"; Description: "{cm:InstallService}"; Flags: postinstall skipifsilent unchecked

[UninstallDelete]
; Do NOT delete {commonappdata}\NetX 鈥?preserves DB and secrets across reinstall
Type: filesandordirs; Name: "{app}"

