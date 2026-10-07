; NetX Windows installer (Inno Setup 6+)
; Compile after: packaging\build_release.ps1
; ISCC.exe packaging\installer\netx.iss
; Encoding: UTF-8 with BOM (required for Chinese CustomMessages)

#define MyAppName "NetX"
#ifndef MyAppVersion
  #define MyAppVersion "0.4.8"
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
#ifndef OutputBaseFilename
  #define OutputBaseFilename "NetX-Setup-" + MyAppVersion
#endif
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\packaging\assets\netx.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; Data lives under {commonappdata}\NetX - not overwritten by upgrades.
; Do NOT use CloseApplications=yes — it can freeze/beep on the Tasks/Ready
; pages while scanning locked NetX tray/service processes with no visible dialog.
CloseApplications=no
ShowLanguageDialog=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "Languages\ChineseSimplified.isl"

[CustomMessages]
english.CreateDesktopIcon=Create a desktop shortcut
english.SetupOptions=After install:
english.StartTrayNow=Start NetX tray now
english.EnableAutostart=Start NetX tray at Windows logon
english.EnableAutoUpdate=Enable daily silent auto-update (needs network later)
english.InstallService=Install as Windows Service (uses bundled WinSW, offline)
english.UninstallDeleteData=Also delete NetX data (%ProgramData%\NetX)?%n%nYes = remove database, settings, and secrets%nNo = keep data for a later reinstall
english.DbPageCaption=Database
english.DbPageDescription=Choose built-in or external PostgreSQL. Optional credential key: paste from an existing install, or leave empty to auto-generate.
english.DbBundled=Built-in PostgreSQL (portable, offline)
english.DbExternal=External PostgreSQL (existing server)
english.DbHost=Host
english.DbPort=Port
english.DbUser=User
english.DbPassword=Password
english.DbName=Database
english.CredKey=Credential key
english.CredKeyHint=NETX_CREDENTIAL_SECRET_KEY (optional — empty = auto-generate)
english.DbFieldsRequired=Please fill in host, port, user, password, and database name.
english.DbTesting=Testing PostgreSQL connection, please wait…
english.DbTestFailed=Cannot connect to PostgreSQL with these settings.%n%n%1%n%nFix the settings and try again.
english.DbTestExtractFailed=Could not extract PostgreSQL client tools for connection test.
english.DbSilentExternalMissing=Silent install with external DB requires /DbHost /DbUser /DbPassword /DbName (optional /DbPort).
english.DbApplyFailed=Database configuration failed after file install (exit %1).%n%nLog: %2%n%nFix the problem, then use Start Menu → Reconfigure database — or reinstall.
english.DbApplyExecFailed=Could not start database configuration script.
english.DbBundledMissing=Built-in PostgreSQL files are missing from the install folder:%n%1%n%nThe Setup package is incomplete. Re-download NetX-Setup and install again.
english.DbEnvMissing=Database configuration did not write %ProgramData%\NetX\.env.
english.ReconfigureDb=Reconfigure database
chinesesimplified.CreateDesktopIcon=创建桌面快捷方式
chinesesimplified.SetupOptions=安装完成后:
chinesesimplified.StartTrayNow=立即启动 NetX 托盘
chinesesimplified.EnableAutostart=开机时自动启动 NetX 托盘
chinesesimplified.EnableAutoUpdate=启用每日静默自动更新（以后需要联网）
chinesesimplified.InstallService=安装为 Windows 服务（使用已捆绑的 WinSW，可离线）
chinesesimplified.UninstallDeleteData=是否同时删除 NetX 数据（%ProgramData%\NetX）？%n%n是 = 删除数据库、配置和密钥%n否 = 保留数据以便以后重装
chinesesimplified.DbPageCaption=数据库
chinesesimplified.DbPageDescription=选择内置或外置 PostgreSQL。可选粘贴已有 NETX_CREDENTIAL_SECRET_KEY；留空则自动生成。
chinesesimplified.DbBundled=内置 PostgreSQL（便携，可离线）
chinesesimplified.DbExternal=外置 PostgreSQL（已有服务器）
chinesesimplified.DbHost=主机
chinesesimplified.DbPort=端口
chinesesimplified.DbUser=用户
chinesesimplified.DbPassword=密码
chinesesimplified.DbName=数据库名
chinesesimplified.CredKey=凭据密钥
chinesesimplified.CredKeyHint=NETX_CREDENTIAL_SECRET_KEY（可选，留空=自动生成）
chinesesimplified.DbFieldsRequired=请填写主机、端口、用户、密码和数据库名。
chinesesimplified.DbTesting=正在测试 PostgreSQL 连接，请稍候…
chinesesimplified.DbTestFailed=无法用当前设置连接 PostgreSQL。%n%n%1%n%n请改正后重试。
chinesesimplified.DbTestExtractFailed=无法解压 PostgreSQL 客户端以测试连接。
chinesesimplified.DbSilentExternalMissing=静默安装外置库需要参数 /DbHost /DbUser /DbPassword /DbName（可选 /DbPort）。
chinesesimplified.DbApplyFailed=文件安装后数据库自动配置失败（退出码 %1）。%n%n日志: %2%n%n请处理后使用开始菜单 → 重新配置数据库，或重新安装。
chinesesimplified.DbApplyExecFailed=无法启动数据库配置脚本。
chinesesimplified.DbBundledMissing=安装目录中缺少内置 PostgreSQL 文件：%n%1%n%n安装包不完整，请重新下载 NetX-Setup 后再装。
chinesesimplified.DbEnvMissing=数据库配置未写入 %ProgramData%\NetX\.env。
chinesesimplified.ReconfigureDb=重新配置数据库

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; Entire release stage -> {app}. Exclude .portable so installed builds use ProgramData.
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".portable"
; Connection-test tools (wizard Next / silent PrepareToInstall only)
Source: "test_pg_conn.ps1"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\psql.exe"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\libpq.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\libssl-3-x64.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\libcrypto-3-x64.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\libintl-9.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\libiconv-2.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\libwinpthread-1.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\zlib1.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\liblz4.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\libzstd.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\libxml2.dll"; Flags: dontcopy
Source: "{#StageDir}\postgres\pgsql\bin\libxslt.dll"; Flags: dontcopy

[Dirs]
; users-modify so tray / non-admin reconfigure can update .env and runtime files.
Name: "{commonappdata}\NetX"; Permissions: users-modify
Name: "{commonappdata}\NetX\data"; Permissions: users-modify
Name: "{commonappdata}\NetX\data\auth"; Permissions: users-modify
Name: "{commonappdata}\NetX\data\runtime"; Permissions: users-modify
Name: "{commonappdata}\NetX\pgdata"; Permissions: users-modify
Name: "{commonappdata}\NetX\backups"; Permissions: users-modify

[InstallDelete]
; Remove legacy desktop shortcuts from older Setup builds (keep a single NetX icon).
Type: files; Name: "{commondesktop}\NetX Start.lnk"
Type: files; Name: "{commondesktop}\NetX First-time setup.lnk"
Type: files; Name: "{commondesktop}\首次配置（内置或外置数据库）.lnk"

[Icons]
; Prefer .cmd targets so Server 2012 / classic Start Menu shows normal program entries.
Name: "{group}\NetX Tray"; Filename: "{app}\NetX-Tray.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Start NetX"; Filename: "{app}\NetX-Start.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Stop NetX"; Filename: "{app}\NetX-Stop.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Check for updates"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\launch_shortcut.ps1"" -Action update -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Open NetX UI"; Filename: "http://127.0.0.1:8890/"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\{cm:ReconfigureDb}"; Filename: "{app}\NetX-FirstRun.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Install Windows Service (admin)"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\install_service.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"" -Start"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Enable silent auto-update (daily)"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\install_update_task.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
Name: "{group}\Enable start at logon"; Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\packaging\install_autostart.ps1"" -ProgramRoot ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"
; One desktop shortcut only (tray = start + tray UI). Start Menu keeps Start/Stop entries.
Name: "{commondesktop}\NetX"; Filename: "{app}\NetX-Tray.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\assets\netx.ico"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\packaging\netx_tray.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"" -StartOnLaunch"; WorkingDir: "{app}"; Description: "{cm:StartTrayNow}"; Flags: postinstall runhidden nowait skipifsilent; Check: NetxShouldStartTray
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\packaging\install_autostart.ps1"" -ProgramRoot ""{app}"""; WorkingDir: "{app}"; Description: "{cm:EnableAutostart}"; Flags: postinstall runhidden skipifsilent unchecked
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\packaging\install_update_task.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; Description: "{cm:EnableAutoUpdate}"; Flags: postinstall runhidden skipifsilent unchecked
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\packaging\install_service.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"" -Start"; WorkingDir: "{app}"; Description: "{cm:InstallService}"; Flags: postinstall runhidden skipifsilent unchecked

; Stop runtime BEFORE files are deleted. Do not also Exec prepare from
; InitializeUninstall (that previously raced/killed unins000 or hung the UI).
[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\packaging\uninstall_prepare.ps1"" -ProgramRoot ""{app}"" -DataRoot ""{commonappdata}\NetX"""; WorkingDir: "{app}"; RunOnceId: "StopNetX"; Flags: runhidden waituntilterminated

[UninstallDelete]
; Program files always removed. Data dir only when user chose Yes in InitializeUninstall.
Type: filesandordirs; Name: "{app}"

[Code]
var
  GDeleteNetxData: Boolean;
  GTempDeleteDataScript: String;
  DbPage: TWizardPage;
  RbBundled: TNewRadioButton;
  RbExternal: TNewRadioButton;
  LblHost: TNewStaticText;
  LblPort: TNewStaticText;
  LblUser: TNewStaticText;
  LblPassword: TNewStaticText;
  LblDbName: TNewStaticText;
  EdHost: TNewEdit;
  EdPort: TNewEdit;
  EdUser: TNewEdit;
  EdPassword: TNewEdit;
  EdDbName: TNewEdit;
  LblCredKey: TNewStaticText;
  LblCredHint: TNewStaticText;
  EdCredKey: TNewEdit;
  GDbMode: String;
  GDbUrl: String;
  GDbHost: String;
  GDbPort: String;
  GDbUser: String;
  GDbPassword: String;
  GDbName: String;
  GCredKey: String;
  GSkipDbPage: Boolean;
  GDbConfigured: Boolean;
  GPgToolsReady: Boolean;
  GDbConnOk: Boolean;
  GNextBtnCaption: String;

function EnvHasDbMode(): Boolean;
var
  Lines: TArrayOfString;
  i: Integer;
  Line: String;
begin
  Result := False;
  if not LoadStringsFromFile(ExpandConstant('{commonappdata}\NetX\.env'), Lines) then
    Exit;
  for i := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Trim(Lines[i]);
    if Pos('NETX_DB_MODE=', Line) = 1 then
    begin
      if Length(Line) > Length('NETX_DB_MODE=') then
      begin
        Result := True;
        Exit;
      end;
    end;
  end;
end;

function EnvModeIsExternal(): Boolean;
var
  Lines: TArrayOfString;
  i: Integer;
  Line: String;
begin
  Result := False;
  if not LoadStringsFromFile(ExpandConstant('{commonappdata}\NetX\.env'), Lines) then
    Exit;
  for i := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Trim(Lines[i]);
    if CompareText(Line, 'NETX_DB_MODE=external') = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function NetxShouldStartTray(): Boolean;
begin
  Result := GDbConfigured or
    FileExists(ExpandConstant('{commonappdata}\NetX\.env'));
end;

procedure UpdateDbFieldState();
var
  Ext: Boolean;
begin
  Ext := RbExternal.Checked;
  LblHost.Enabled := Ext;
  LblPort.Enabled := Ext;
  LblUser.Enabled := Ext;
  LblPassword.Enabled := Ext;
  LblDbName.Enabled := Ext;
  EdHost.Enabled := Ext;
  EdPort.Enabled := Ext;
  EdUser.Enabled := Ext;
  EdPassword.Enabled := Ext;
  EdDbName.Enabled := Ext;
end;

procedure DbModeClick(Sender: TObject);
begin
  UpdateDbFieldState();
end;

function ExtractPgClientTools(): Boolean;
begin
  if GPgToolsReady and FileExists(ExpandConstant('{tmp}\psql.exe')) and
     FileExists(ExpandConstant('{tmp}\test_pg_conn.ps1')) then
  begin
    Result := True;
    Exit;
  end;
  Result := False;
  try
    ExtractTemporaryFile('test_pg_conn.ps1');
    ExtractTemporaryFile('psql.exe');
    ExtractTemporaryFile('libpq.dll');
    ExtractTemporaryFile('libssl-3-x64.dll');
    ExtractTemporaryFile('libcrypto-3-x64.dll');
    ExtractTemporaryFile('libintl-9.dll');
    ExtractTemporaryFile('libiconv-2.dll');
    ExtractTemporaryFile('libwinpthread-1.dll');
    ExtractTemporaryFile('zlib1.dll');
    ExtractTemporaryFile('liblz4.dll');
    ExtractTemporaryFile('libzstd.dll');
    ExtractTemporaryFile('libxml2.dll');
    ExtractTemporaryFile('libxslt.dll');
    Result := FileExists(ExpandConstant('{tmp}\psql.exe')) and
      FileExists(ExpandConstant('{tmp}\test_pg_conn.ps1'));
    GPgToolsReady := Result;
  except
    Result := False;
    GPgToolsReady := False;
  end;
end;

procedure SetWizardBusy(const Busy: Boolean; const StatusText: String);
begin
  if Busy then
  begin
    if GNextBtnCaption = '' then
      GNextBtnCaption := WizardForm.NextButton.Caption;
    WizardForm.NextButton.Caption := StatusText;
    WizardForm.NextButton.Enabled := False;
    WizardForm.BackButton.Enabled := False;
    WizardForm.CancelButton.Enabled := False;
  end
  else
  begin
    if GNextBtnCaption <> '' then
      WizardForm.NextButton.Caption := GNextBtnCaption;
    WizardForm.NextButton.Enabled := True;
    WizardForm.BackButton.Enabled := True;
    WizardForm.CancelButton.Enabled := True;
  end;
  WizardForm.Refresh;
end;

function TestExternalDb(const Host, Port, User, Password, DbName: String; var ErrMsg: String): Boolean;
var
  ResultCode: Integer;
  UrlFile: String;
  ErrFile: String;
  PwFile: String;
  Params: String;
  Lines: TArrayOfString;
  i: Integer;
begin
  Result := False;
  ErrMsg := '';
  if not ExtractPgClientTools() then
  begin
    ErrMsg := ExpandConstant('{cm:DbTestExtractFailed}');
    Exit;
  end;

  UrlFile := ExpandConstant('{tmp}\netx_db_url.txt');
  ErrFile := ExpandConstant('{tmp}\netx_db_test_err.txt');
  PwFile := ExpandConstant('{tmp}\netx_db_pw.txt');
  DeleteFile(UrlFile);
  DeleteFile(ErrFile);
  SaveStringToFile(PwFile, Password, False);

  Params :=
    '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\test_pg_conn.ps1') + '"' +
    ' -PgHost "' + Host + '"' +
    ' -Port ' + Port +
    ' -User "' + User + '"' +
    ' -PasswordFile "' + PwFile + '"' +
    ' -Database "' + DbName + '"' +
    ' -PsqlPath "' + ExpandConstant('{tmp}\psql.exe') + '"' +
    ' -OutUrlFile "' + UrlFile + '"' +
    ' -OutErrFile "' + ErrFile + '"';

  if not Exec('powershell.exe', Params, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    ErrMsg := 'powershell_exec_failed';
    DeleteFile(PwFile);
    Exit;
  end;
  DeleteFile(PwFile);
  if ResultCode <> 0 then
  begin
    ErrMsg := 'psql/test exit ' + IntToStr(ResultCode);
    if LoadStringsFromFile(ErrFile, Lines) then
    begin
      ErrMsg := '';
      for i := 0 to GetArrayLength(Lines) - 1 do
      begin
        if ErrMsg <> '' then
          ErrMsg := ErrMsg + #13#10;
        ErrMsg := ErrMsg + Lines[i];
      end;
      if ErrMsg = '' then
        ErrMsg := 'psql/test exit ' + IntToStr(ResultCode);
    end;
    Exit;
  end;
  if not LoadStringsFromFile(UrlFile, Lines) or (GetArrayLength(Lines) < 1) or (Trim(Lines[0]) = '') then
  begin
    ErrMsg := 'url_file_missing';
    Exit;
  end;
  GDbUrl := Trim(Lines[0]);
  Result := True;
end;

function SaveExternalFieldsFromControls(var ErrMsg: String): Boolean;
begin
  Result := False;
  GDbHost := Trim(EdHost.Text);
  GDbPort := Trim(EdPort.Text);
  GDbUser := Trim(EdUser.Text);
  GDbPassword := EdPassword.Text;
  GDbName := Trim(EdDbName.Text);
  GDbConnOk := False;
  GDbUrl := '';
  if (GDbHost = '') or (GDbPort = '') or (GDbUser = '') or
     (GDbPassword = '') or (GDbName = '') then
  begin
    ErrMsg := ExpandConstant('{cm:DbFieldsRequired}');
    Exit;
  end;
  GDbMode := 'external';
  Result := True;
end;

function ValidateExternalDbSaved(var ErrMsg: String): Boolean;
begin
  Result := False;
  if GDbConnOk and (GDbUrl <> '') then
  begin
    Result := True;
    Exit;
  end;
  if not TestExternalDb(GDbHost, GDbPort, GDbUser, GDbPassword, GDbName, ErrMsg) then
  begin
    if ErrMsg = '' then
      ErrMsg := 'connection_failed';
    ErrMsg := FmtMessage(ExpandConstant('{cm:DbTestFailed}'), [ErrMsg]);
    GDbConnOk := False;
    Exit;
  end;
  GDbConnOk := True;
  Result := True;
end;

function ApplySilentDbParams(var ErrMsg: String): Boolean;
var
  Mode, Host, Port, User, Password, DbName: String;
begin
  Result := False;
  GCredKey := Trim(ExpandConstant('{param:CredentialSecretKey|}'));
  Mode := LowerCase(ExpandConstant('{param:DbMode|bundled}'));
  if Mode = '' then
    Mode := 'bundled';
  if Mode = 'bundled' then
  begin
    GDbMode := 'bundled';
    GDbUrl := '';
    Result := True;
    Exit;
  end;
  if Mode <> 'external' then
  begin
    ErrMsg := 'invalid DbMode (use bundled or external)';
    Exit;
  end;
  Host := ExpandConstant('{param:DbHost|}');
  Port := ExpandConstant('{param:DbPort|5432}');
  User := ExpandConstant('{param:DbUser|}');
  Password := ExpandConstant('{param:DbPassword|}');
  DbName := ExpandConstant('{param:DbName|}');
  if (Host = '') or (User = '') or (Password = '') or (DbName = '') then
  begin
    ErrMsg := ExpandConstant('{cm:DbSilentExternalMissing}');
    Exit;
  end;
  if not TestExternalDb(Host, Port, User, Password, DbName, ErrMsg) then
  begin
    if ErrMsg = '' then
      ErrMsg := 'connection_failed';
    ErrMsg := FmtMessage(ExpandConstant('{cm:DbTestFailed}'), [ErrMsg]);
    Exit;
  end;
  GDbMode := 'external';
  Result := True;
end;

procedure CaptureCredKeyFromControls();
begin
  if EdCredKey <> nil then
    GCredKey := Trim(EdCredKey.Text)
  else
    GCredKey := '';
end;

procedure InitializeWizard();
var
  TopPos: Integer;
begin
  GDbMode := 'bundled';
  GDbUrl := '';
  GDbHost := '';
  GDbPort := '5432';
  GDbUser := '';
  GDbPassword := '';
  GDbName := '';
  GCredKey := '';
  GDbConfigured := False;
  GPgToolsReady := False;
  GDbConnOk := False;
  { Always show DB page so choosing built-in always re-runs auto-configure.
    Skipping when .env exists caused upgrades/reinstalls to keep a broken or
    external config and still launch tray → confusing "first-time" prompts. }
  GSkipDbPage := False;

  DbPage := CreateCustomPage(wpSelectDir,
    ExpandConstant('{cm:DbPageCaption}'),
    ExpandConstant('{cm:DbPageDescription}'));

  RbBundled := TNewRadioButton.Create(DbPage);
  RbBundled.Parent := DbPage.Surface;
  RbBundled.Caption := ExpandConstant('{cm:DbBundled}');
  RbBundled.Checked := True;
  RbBundled.Top := ScaleY(8);
  RbBundled.Left := ScaleX(0);
  RbBundled.Width := DbPage.SurfaceWidth;
  RbBundled.OnClick := @DbModeClick;

  RbExternal := TNewRadioButton.Create(DbPage);
  RbExternal.Parent := DbPage.Surface;
  RbExternal.Caption := ExpandConstant('{cm:DbExternal}');
  RbExternal.Top := RbBundled.Top + ScaleY(28);
  RbExternal.Left := ScaleX(0);
  RbExternal.Width := DbPage.SurfaceWidth;
  RbExternal.OnClick := @DbModeClick;

  TopPos := RbExternal.Top + ScaleY(36);

  LblHost := TNewStaticText.Create(DbPage);
  LblHost.Parent := DbPage.Surface;
  LblHost.Caption := ExpandConstant('{cm:DbHost}');
  LblHost.Top := TopPos;
  LblHost.Left := ScaleX(16);

  EdHost := TNewEdit.Create(DbPage);
  EdHost.Parent := DbPage.Surface;
  EdHost.Top := TopPos;
  EdHost.Left := ScaleX(120);
  EdHost.Width := ScaleX(220);
  EdHost.Text := '127.0.0.1';

  TopPos := TopPos + ScaleY(28);
  LblPort := TNewStaticText.Create(DbPage);
  LblPort.Parent := DbPage.Surface;
  LblPort.Caption := ExpandConstant('{cm:DbPort}');
  LblPort.Top := TopPos;
  LblPort.Left := ScaleX(16);

  EdPort := TNewEdit.Create(DbPage);
  EdPort.Parent := DbPage.Surface;
  EdPort.Top := TopPos;
  EdPort.Left := ScaleX(120);
  EdPort.Width := ScaleX(80);
  EdPort.Text := '5432';

  TopPos := TopPos + ScaleY(28);
  LblUser := TNewStaticText.Create(DbPage);
  LblUser.Parent := DbPage.Surface;
  LblUser.Caption := ExpandConstant('{cm:DbUser}');
  LblUser.Top := TopPos;
  LblUser.Left := ScaleX(16);

  EdUser := TNewEdit.Create(DbPage);
  EdUser.Parent := DbPage.Surface;
  EdUser.Top := TopPos;
  EdUser.Left := ScaleX(120);
  EdUser.Width := ScaleX(220);
  EdUser.Text := 'netx';

  TopPos := TopPos + ScaleY(28);
  LblPassword := TNewStaticText.Create(DbPage);
  LblPassword.Parent := DbPage.Surface;
  LblPassword.Caption := ExpandConstant('{cm:DbPassword}');
  LblPassword.Top := TopPos;
  LblPassword.Left := ScaleX(16);

  EdPassword := TNewEdit.Create(DbPage);
  EdPassword.Parent := DbPage.Surface;
  EdPassword.Top := TopPos;
  EdPassword.Left := ScaleX(120);
  EdPassword.Width := ScaleX(220);
  EdPassword.PasswordChar := '*';

  TopPos := TopPos + ScaleY(28);
  LblDbName := TNewStaticText.Create(DbPage);
  LblDbName.Parent := DbPage.Surface;
  LblDbName.Caption := ExpandConstant('{cm:DbName}');
  LblDbName.Top := TopPos;
  LblDbName.Left := ScaleX(16);

  EdDbName := TNewEdit.Create(DbPage);
  EdDbName.Parent := DbPage.Surface;
  EdDbName.Top := TopPos;
  EdDbName.Left := ScaleX(120);
  EdDbName.Width := ScaleX(220);
  EdDbName.Text := 'netx';

  TopPos := TopPos + ScaleY(36);
  LblCredKey := TNewStaticText.Create(DbPage);
  LblCredKey.Parent := DbPage.Surface;
  LblCredKey.Caption := ExpandConstant('{cm:CredKey}');
  LblCredKey.Top := TopPos;
  LblCredKey.Left := ScaleX(0);

  EdCredKey := TNewEdit.Create(DbPage);
  EdCredKey.Parent := DbPage.Surface;
  EdCredKey.Top := TopPos;
  EdCredKey.Left := ScaleX(120);
  EdCredKey.Width := DbPage.SurfaceWidth - ScaleX(120);
  EdCredKey.Text := '';

  TopPos := TopPos + ScaleY(24);
  LblCredHint := TNewStaticText.Create(DbPage);
  LblCredHint.Parent := DbPage.Surface;
  LblCredHint.Caption := ExpandConstant('{cm:CredKeyHint}');
  LblCredHint.Top := TopPos;
  LblCredHint.Left := ScaleX(120);
  LblCredHint.Width := DbPage.SurfaceWidth - ScaleX(120);
  LblCredHint.AutoSize := False;

  UpdateDbFieldState();

  { Prefill from existing ProgramData\.env when present. }
  if EnvModeIsExternal() then
  begin
    RbExternal.Checked := True;
    UpdateDbFieldState();
  end
  else if EnvHasDbMode() then
  begin
    RbBundled.Checked := True;
    UpdateDbFieldState();
  end;

  if WizardSilent then
  begin
    { Params applied in PrepareToInstall }
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (DbPage <> nil) and (PageID = DbPage.ID) then
    Result := GSkipDbPage;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ErrMsg: String;
begin
  Result := True;
  { Only the DB page runs validation. Other pages must always proceed
    (Tasks/Ready used to appear "dead" when CloseApplications hung). }
  if (DbPage = nil) or (CurPageID <> DbPage.ID) or GSkipDbPage then
    Exit;

  CaptureCredKeyFromControls();
  if RbBundled.Checked then
  begin
    GDbMode := 'bundled';
    GDbUrl := '';
    GDbConnOk := True;
    Result := True;
    Exit;
  end;

  if not SaveExternalFieldsFromControls(ErrMsg) then
  begin
    MsgBox(ErrMsg, mbError, MB_OK);
    Result := False;
    Exit;
  end;

  { Connection test here so bad settings never reach Tasks/Ready.
    Show busy state — ExtractTemporaryFile + psql can take a few seconds. }
  SetWizardBusy(True, ExpandConstant('{cm:DbTesting}'));
  try
    Result := ValidateExternalDbSaved(ErrMsg);
  finally
    SetWizardBusy(False, '');
  end;
  if not Result then
    MsgBox(ErrMsg, mbError, MB_OK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ErrMsg: String;
begin
  Result := '';
  NeedsRestart := False;
  if GSkipDbPage then
    Exit;

  if WizardSilent then
  begin
    if not ApplySilentDbParams(ErrMsg) then
      Result := ErrMsg;
    Exit;
  end;

  if GDbMode = '' then
    GDbMode := 'bundled';

  { Re-check only if user never got a successful test (should be rare). }
  if (GDbMode = 'external') and (not GDbConnOk) then
  begin
    if not ValidateExternalDbSaved(ErrMsg) then
      Result := ErrMsg;
  end;
end;

function ApplyDatabaseConfig(): Boolean;
var
  ResultCode: Integer;
  Params: String;
  UrlFile: String;
  KeyFile: String;
  LogFile: String;
  InitDb: String;
  EnvFile: String;
  Wrapper: String;
  WrapBody: String;
begin
  Result := False;
  GDbConfigured := False;
  if GDbMode = '' then
    GDbMode := 'bundled';

  InitDb := ExpandConstant('{app}\postgres\pgsql\bin\initdb.exe');
  if (GDbMode = 'bundled') and (not FileExists(InitDb)) then
  begin
    MsgBox(FmtMessage(ExpandConstant('{cm:DbBundledMissing}'), [InitDb]), mbError, MB_OK);
    Exit;
  end;

  ForceDirectories(ExpandConstant('{commonappdata}\NetX\data\runtime'));
  LogFile := ExpandConstant('{commonappdata}\NetX\data\runtime\setup_first_run.log');
  Wrapper := ExpandConstant('{tmp}\netx_apply_db.ps1');

  WrapBody :=
    '$ErrorActionPreference = ''Stop''' + #13#10 +
    '$log = ''' + LogFile + '''' + #13#10 +
    'Start-Transcript -Path $log -Force | Out-Null' + #13#10 +
    'try {' + #13#10 +
    '  & ''' + ExpandConstant('{app}\packaging\setup_first_run.ps1') + '''' +
    ' -ProgramRoot ''' + ExpandConstant('{app}') + '''' +
    ' -DataRoot ''' + ExpandConstant('{commonappdata}\NetX') + '''' +
    ' -NonInteractive -DbMode ' + GDbMode;

  if GDbMode = 'external' then
  begin
    UrlFile := ExpandConstant('{tmp}\netx_db_url.txt');
    if GDbUrl <> '' then
      SaveStringToFile(UrlFile, GDbUrl, False);
    WrapBody := WrapBody +
      ' -ExternalDatabaseUrlFile ''' + UrlFile + ''' -SkipExternalProbe';
  end;

  if GCredKey <> '' then
  begin
    KeyFile := ExpandConstant('{tmp}\netx_cred_key.txt');
    SaveStringToFile(KeyFile, GCredKey, False);
    WrapBody := WrapBody + ' -CredentialSecretKeyFile ''' + KeyFile + '''';
  end;

  WrapBody := WrapBody + #13#10 +
    '  if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }' + #13#10 +
    '} catch {' + #13#10 +
    '  Write-Error $_' + #13#10 +
    '  exit 1' + #13#10 +
    '} finally {' + #13#10 +
    '  Stop-Transcript | Out-Null' + #13#10 +
    '}' + #13#10;

  SaveStringToFile(Wrapper, WrapBody, False);
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + Wrapper + '"';

  if not Exec(
    'powershell.exe', Params, ExpandConstant('{app}'),
    SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox(ExpandConstant('{cm:DbApplyExecFailed}'), mbError, MB_OK);
    Exit;
  end;
  if ResultCode <> 0 then
  begin
    MsgBox(FmtMessage(ExpandConstant('{cm:DbApplyFailed}'), [IntToStr(ResultCode), LogFile]), mbError, MB_OK);
    Exit;
  end;

  EnvFile := ExpandConstant('{commonappdata}\NetX\.env');
  if not FileExists(EnvFile) then
  begin
    MsgBox(ExpandConstant('{cm:DbEnvMissing}'), mbError, MB_OK);
    Exit;
  end;

  GDbConfigured := True;
  Result := True;
end;

function InitializeUninstall(): Boolean;
var
  DeleteSrc: String;
begin
  Result := True;
  GDeleteNetxData := False;
  GTempDeleteDataScript := ExpandConstant('{tmp}\netx_uninstall_delete_data.ps1');

  // Copy data-wipe helper to TEMP before program files are removed.
  DeleteSrc := ExpandConstant('{app}\packaging\uninstall_delete_data.ps1');
  if FileExists(DeleteSrc) then
    CopyFile(DeleteSrc, GTempDeleteDataScript, False);

  if UninstallSilent then
  begin
    // Settings often uses QuietUninstallString; keep data by default.
    GDeleteNetxData := False;
  end
  else
  begin
    GDeleteNetxData :=
      MsgBox(ExpandConstant('{cm:UninstallDeleteData}'), mbConfirmation, MB_YESNO) = IDYES;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  DataRoot: String;
begin
  if (CurUninstallStep = usPostUninstall) and GDeleteNetxData then
  begin
    DataRoot := ExpandConstant('{commonappdata}\NetX');
    if FileExists(GTempDeleteDataScript) then
    begin
      Exec(
        'powershell.exe',
        '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
          GTempDeleteDataScript + '" -DataRoot "' + DataRoot + '"',
        ExpandConstant('{tmp}'),
        SW_HIDE,
        ewWaitUntilTerminated,
        ResultCode
      );
    end;
    if DirExists(DataRoot) then
      DelTree(DataRoot, True, True, True);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  UninstallKey: String;
begin
  if CurStep = ssPostInstall then
  begin
    ApplyDatabaseConfig();

    UninstallKey :=
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A7E3C2D1-9F40-4B8E-9C1A-NETXWIN64001}_is1';
    RegDeleteValue(HKLM, UninstallKey, 'QuietUninstallString');
  end;
end;
