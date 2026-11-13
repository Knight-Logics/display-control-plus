; Display Control Inno Setup Script
; This script creates an installer with options for desktop shortcut and Task Scheduler background auto-start

[Setup]
AppName=Display Control
AppVersion=1.1.2
DefaultDirName={commonpf64}\Display Control
DefaultGroupName=Display Control
UninstallDisplayIcon={app}\DisplayControl.exe
OutputDir=..
OutputBaseFilename=DisplayControlSetup_v1.1.2
SetupIconFile=..\Display Control+ Logo.ico
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Files]
Source: "..\dist\DisplayControl.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\tray.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\overlay_bg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\checkout_window.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Display Control+ Logo.png"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\Display Control+ Logo.ico"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\KnightLogicsLogo.png"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{commondesktop}\Display Control+"; Filename: "{app}\DisplayControl.exe"; IconFilename: "{app}\Display Control+ Logo.ico"
Name: "{group}\Display Control+"; Filename: "{app}\DisplayControl.exe"; IconFilename: "{app}\Display Control+ Logo.ico"

[InstallDelete]
Type: files; Name: "{commondesktop}\Display Control.lnk"
Type: files; Name: "{commondesktop}\Display Control+.lnk"
Type: files; Name: "{group}\Display Control.lnk"
Type: files; Name: "{group}\Display Control+.lnk"

[Tasks]
Name: "trayicon"; Description: "Start in &Task Tray on login"; GroupDescription: "Startup options:"; Flags: checkedonce

[Registry]
; HKCU Run works for the installing user even when setup is elevated, and it does not require admin at login.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DisplayControlPlusTray"; ValueData: """{app}\tray.exe"""; Flags: uninsdeletevalue; Tasks: trayicon
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DisplayControlPlusTray"; ValueData: """{app}\tray.exe"""; Flags: uninsdeletevalue; Check: WizardSilent

[Run]
; Start tray immediately after install (manual/wizard installs)
Filename: "{app}\tray.exe"; Description: "Start Display Control+ tray"; Flags: nowait postinstall skipifsilent

; Optional: open dashboard after install (manual/wizard installs)
Filename: "{app}\DisplayControl.exe"; Description: "Open Display Control+ dashboard"; Flags: nowait postinstall skipifsilent unchecked

; Refresh Windows icon cache so updated shortcut/icon branding is reflected quickly.
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden

; Silent update: relaunch tray, then reopen dashboard. Login startup is the HKCU Run value above.
Filename: "{app}\tray.exe"; Flags: nowait; Check: WizardSilent()
Filename: "{app}\DisplayControl.exe"; Flags: nowait; Check: WizardSilent()

[Code]
function KillRunningProcess(const ImageName: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(
    ExpandConstant('{cmd}'),
    '/C taskkill /F /T /IM "' + ImageName + '"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
end;

procedure ShutdownDisplayControlProcesses();
begin
  KillRunningProcess('DisplayControl.exe');
  KillRunningProcess('tray.exe');
  KillRunningProcess('overlay_bg.exe');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ShutdownDisplayControlProcesses();
  Result := '';
end;
