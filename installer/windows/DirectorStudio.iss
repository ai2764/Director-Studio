; Director Studio - Inno Setup script (Inno Setup 6.3+ / 7).
; Built by installer/build_windows.py, which passes the defines below via ISCC /D...:
;   ISCC /DAppVersion=0.2.0 /DAppVersionNumeric=0.2.0.0 /DVariant=cu128 /DSourceDir=<staging root>
;        /DOutputDir=<dir> /DAppId={GUID} installer\windows\DirectorStudio.iss
; Per-user install (no admin): program in %LOCALAPPDATA%\Programs\DirectorStudio,
; user data in %LOCALAPPDATA%\DirectorStudio - never written or removed by this script.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.0.0.0"
#endif
#ifndef Variant
  #define Variant "cu128"
#endif
#ifndef SourceDir
  #error SourceDir (staging root from build_windows.py stage) is required
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#ifndef AppId
  #define AppId "{0A59C4F5-9743-5BEF-817E-57B843D93A3C}"
#endif
#define AppName "Director Studio"
#define AppExeName "director-studio.pyw"

[Setup]
; the AppId GUID must never change (upgrades + uninstall entry); "{{" escapes the brace
AppId={{#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} ({#Variant})
AppPublisher=Director Studio
VersionInfoVersion={#AppVersionNumeric}
DefaultDirName={localappdata}\Programs\DirectorStudio
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=DirectorStudio-{#AppVersion}-windows-x64-{#Variant}-setup
SetupIconFile=director-studio.ico
UninstallDisplayIcon={app}\director-studio.ico
UninstallDisplayName={#AppName} {#AppVersion}
WizardStyle=modern
Compression=lzma2/normal
SolidCompression=no
LZMANumBlockThreads=4
; payload is > 2 GB: span into setup.exe + setup-1.bin, setup-2.bin ... (keep them together).
; Slices stay below 2 GiB (GitHub release asset limit, FAT32); "max" means unlimited in Inno 6.5+.
DiskSpanning=yes
DiskSliceSize=2000000000
SlicesPerDisk=1
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
ChangesEnvironment=no
AllowNoIcons=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; the whole staged tree incl. precompiled (checked-hash) bytecode for a fast first start
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".build-steps"

[InstallDelete]
; upgrades: drop the previous program tree's runtimes so removed files do not linger
; (user data lives in %LOCALAPPDATA%\DirectorStudio and is not touched)
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\harness"
Type: filesandordirs; Name: "{app}\runtime\python"
Type: filesandordirs; Name: "{app}\runtime\comfy-python"
Type: filesandordirs; Name: "{app}\runtime\comfy-tools"
Type: filesandordirs; Name: "{app}\runtime\ollama"
Type: filesandordirs; Name: "{app}\runtime\llama-swap"
Type: filesandordirs; Name: "{app}\runtime\llama.cpp"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-s ""{app}\{#AppExeName}"" run"; WorkingDir: "{app}"; IconFilename: "{app}\director-studio.ico"; Comment: "Start Director Studio and open it in the browser"
Name: "{autoprograms}\Stop {#AppName}"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-s ""{app}\{#AppExeName}"" stop"; WorkingDir: "{app}"; IconFilename: "{app}\director-studio.ico"
Name: "{autoprograms}\{#AppName} (console)"; Filename: "{app}\DirectorStudio.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\director-studio.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-s ""{app}\{#AppExeName}"" run"; WorkingDir: "{app}"; IconFilename: "{app}\director-studio.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-s ""{app}\{#AppExeName}"" run"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; stop a running instance first (graceful stop request, job object as backstop)
Filename: "{app}\runtime\python\python.exe"; Parameters: "-s ""{app}\{#AppExeName}"" stop"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopDirectorStudio"

[UninstallDelete]
; only the program folder (bytecode caches written at runtime); %LOCALAPPDATA%\DirectorStudio is kept
Type: filesandordirs; Name: "{app}"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then
  begin
    MsgBox('Director Studio requires 64-bit Windows 10 1809 or newer.', mbCriticalError, MB_OK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Python, Entry: String;
  Code: Integer;
begin
  Result := '';
  { upgrade: stop a running Director Studio so its files can be replaced }
  Python := ExpandConstant('{app}\runtime\python\python.exe');
  Entry := ExpandConstant('{app}\{#AppExeName}');
  if FileExists(Python) and FileExists(Entry) then
    Exec(Python, '-s "' + Entry + '" stop', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    Log('User data kept in ' + ExpandConstant('{localappdata}') + '\DirectorStudio');
end;
